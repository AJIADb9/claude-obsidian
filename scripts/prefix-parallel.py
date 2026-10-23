#!/usr/bin/env python3
"""prefix-parallel.py - rebuild every contextual prefix in parallel shards, then install.

Fork helper. contextual-prefix.py takes the vault-wide writer lock for a whole
run, so two runs on one vault queue behind each other, and one Claude Code CLI
call per chunk makes a full rebuild serial and slow (about 3 s a chunk). This
runs it in N workers at once, each on its own copy of the vault, and touches
the real vault only at the end, under one lock:

  1. mirror   copy the vault's pages and retrieval config to a work directory
              (keep it on a local Linux filesystem: a synced or 9P drive is
              far slower and the per-page runs re-read every page)
  2. run      split the pages into N shards balanced by size; one copy and one
              worker per shard, calling contextual-prefix.py page by page
  3. retry    pages with any chunk not from the provider run once more
  4. collect  merge the shards' chunk files into one store, refusing overlaps
  5. install  take the vault's writer lock once; re-prefix pages edited during
              the run, build BM25, and swap .vault-meta/chunks and
              .vault-meta/bm25 in. The previous ones are kept, never deleted:
              as .vault-meta/<name>.prev-<stamp>, or under --backup-dir/<date>/

Page text goes to Anthropic through the local Claude Code login, so a provider
run requires --allow-egress. --no-llm builds synthetic prefixes only.

Usage:
  prefix-parallel.py --vault PATH --allow-egress            # 20 shards, Haiku
  prefix-parallel.py --vault PATH --allow-egress --shards 10 --backup-dir DIR
  prefix-parallel.py --vault PATH --install-only WORKDIR    # step 5 again

Exit codes:
  0 - installed, or built with --no-install / --limit
  1 - a step failed; the vault is unchanged
  2 - usage or vault selection failure
"""

import argparse
import collections
import concurrent.futures
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from claude_obsidian.paths import VaultSelectionError, resolve_vault_root
from claude_obsidian.transaction import MutationLock, TransactionError

PREFIX = PLUGIN_ROOT / "scripts" / "contextual-prefix.py"
BM25 = PLUGIN_ROOT / "scripts" / "bm25-index.py"
DEFAULT_SHARDS = 20
PROVIDER_SOURCE = "claude-code"
# `--all --peek` prints one `[i/N] -> wiki/page.md  address=...` line per page.
PEEK_RE = re.compile(r"^\[\d+/\d+\] -> (.*)  address=")
# A re-sync under the lock should be a handful of edited pages, never a rebuild.
MAX_RESYNC_CHUNKS = 300
_PRINT = threading.Lock()


class StepFailed(Exception):
    pass


def say(msg):
    with _PRINT:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def prefix_command(args, vault, *target):
    command = [sys.executable, "-B", str(PREFIX), "--vault", str(vault), *target]
    if args.no_llm:
        return command + ["--no-llm"]
    return command + ["--allow-egress", "--claude-cli", "--claude-cli-model", args.claude_cli_model,
                      "--claude-cli-max-requests", "512"]


def run(command):
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")


def page_hashes(vault):
    hashes = {}
    for path in sorted((vault / "wiki").rglob("*.md")):
        relative = path.relative_to(vault)
        if not any(part.startswith(".") for part in relative.parts):
            hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def make_mirror(source, target):
    target.mkdir(parents=True)
    shutil.copytree(source / "wiki", target / "wiki")
    for name in (".claude-obsidian.json", ".gitignore"):
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)
    (target / ".obsidian").mkdir(exist_ok=True)
    (target / ".vault-meta").mkdir()
    if (source / ".vault-meta/retrieval.json").is_file():
        shutil.copy2(source / ".vault-meta/retrieval.json", target / ".vault-meta/retrieval.json")


def page_list(args, vault):
    result = run([sys.executable, "-B", str(PREFIX), "--vault", str(vault), "--all", "--peek", "--no-llm"])
    output = result.stdout + result.stderr
    pages = [match.group(1) for line in output.splitlines() if (match := PEEK_RE.match(line))]
    if result.returncode or not pages:
        raise StepFailed(f"cannot list pages: {output[-400:]}")
    return pages


def sources_by_page(vault):
    by_page = collections.defaultdict(collections.Counter)
    for path in (vault / ".vault-meta/chunks").rglob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        by_page[record["page_path"]][record.get("prefix_source")] += 1
    return by_page


def run_shards(args, work, plan, label, *extra):
    total = sum(map(len, plan))
    done = [0]

    def worker(index):
        with open(work / f"shard{index}.{label}.log", "a", encoding="utf-8") as log:
            for page in plan[index]:
                result = run(prefix_command(args, work / f"shard{index}", page, *extra))
                log.write(f"=== rc={result.returncode} {page}\n{result.stdout}{result.stderr}\n")
                log.flush()
                with _PRINT:
                    done[0] += 1
                    count = done[0]
                if count % 50 == 0 or count == total:
                    say(f"{label} {count}/{total}")

    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(plan)) as pool:
        for job in [pool.submit(worker, i) for i in range(len(plan)) if plan[i]]:
            job.result()
    say(f"{label}: {total} pages in {round((time.time() - started) / 60, 1)} min")


def incomplete_pages(work, plan):
    """Pages with a missing record, or a chunk the provider did not write."""
    found = []
    for index, pages in enumerate(plan):
        by_page = sources_by_page(work / f"shard{index}")
        found.extend((index, page) for page in pages
                     if set(by_page.get(page, {})) != {PROVIDER_SOURCE})
    return found


def merge(work, shards):
    target = work / "merged/.vault-meta/chunks"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for index in range(shards):
        source = work / f"shard{index}/.vault-meta/chunks"
        for path in sorted(source.rglob("*")):
            destination = target / path.relative_to(source)
            if path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif destination.exists():
                raise StepFailed(f"two shards wrote {path.relative_to(source)}")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)


def retrying_move(source, destination, tries=10):
    """Synced folders (OneDrive) briefly deny renames while they upload."""
    for attempt in range(tries):
        try:
            shutil.move(str(source), str(destination))
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(3)


def install(args, vault, work):
    merged = work / "merged"
    if not (merged / ".vault-meta/chunks").is_dir():
        raise StepFailed(f"{work} holds no merged chunk store")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    say("waiting for the vault's writer lock")
    with MutationLock(vault, timeout=args.lock_timeout):
        say("lock held")
        live, built = page_hashes(vault), page_hashes(merged)
        changed = sorted(page for page in live if live[page] != built.get(page))
        gone = sorted(set(built) - set(live))
        say(f"pages edited during the run: {len(changed)} changed or new, {len(gone)} removed")
        for page in changed:
            (merged / page).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(vault / page, merged / page)
        for page in gone:
            (merged / page).unlink()
        if changed or gone:
            peek = run(prefix_command(args, merged, "--all", "--peek"))
            planned = (peek.stdout + peek.stderr).count("would write")
            say(f"re-sync regenerates {planned} chunks")
            if planned > MAX_RESYNC_CHUNKS:
                raise StepFailed("too many edited chunks to re-sync under the lock; run the whole build again")
            result = run(prefix_command(args, merged, "--all"))
            (work / "resync.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            if result.returncode:
                raise StepFailed("re-sync failed; see resync.log")
        result = run([sys.executable, "-B", str(BM25), "--vault", str(merged), "build"])
        (work / "bm25.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise StepFailed("BM25 build failed; see bm25.log")
        meta = vault / ".vault-meta"
        for name in ("chunks", "bm25"):
            shutil.copytree(merged / ".vault-meta" / name, meta / f"{name}.new-{stamp}")
        if args.backup_dir:
            backup = args.backup_dir / time.strftime("%Y-%m-%d")
            backup.mkdir(parents=True, exist_ok=True)
        for name in ("chunks", "bm25"):
            if (meta / name).exists():
                kept = (backup / f"{name}.{stamp}") if args.backup_dir else meta / f"{name}.prev-{stamp}"
                retrying_move(meta / name, kept)
                say(f"previous {name} kept at {kept}")
            retrying_move(meta / f"{name}.new-{stamp}", meta / name)
            say(f"installed {name}")
    say("lock released")


def build(args, vault):
    work_root = args.work_dir or Path(tempfile.gettempdir())
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="prefix-parallel.", dir=work_root))
    say(f"work {work}")
    make_mirror(vault, work / "merged")
    pages = page_list(args, work / "merged")
    if args.limit:
        pages = pages[:args.limit]
    sizes = {page: (work / "merged" / page).stat().st_size for page in pages}
    plan = [[] for _ in range(min(args.shards, len(pages)))]
    load = [0] * len(plan)
    for page in sorted(pages, key=lambda page: (-sizes[page], page)):
        index = load.index(min(load))
        plan[index].append(page)
        load[index] += sizes[page]
    for index in range(len(plan)):
        make_mirror(work / "merged", work / f"shard{index}")
    (work / "shards.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"{len(pages)} pages in {len(plan)} shards")

    run_shards(args, work, plan, "run")
    if not args.no_llm:
        retry = [[] for _ in plan]
        for index, page in incomplete_pages(work, plan):
            retry[index].append(page)
        say(f"pages with a chunk not from the provider: {sum(map(len, retry))}")
        if any(retry):
            run_shards(args, work, retry, "retry", "--rebuild")
            still = incomplete_pages(work, retry)
            (work / "incomplete.json").write_text(json.dumps(still, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
            say(f"still not all from the provider: {len(still)} pages (kept with synthetic prefixes)")

    merge(work, len(plan))
    for index in range(len(plan)):
        shutil.rmtree(work / f"shard{index}")
    totals = collections.Counter()
    for counts in sources_by_page(work / "merged").values():
        totals.update(counts)
    say(f"merged: {dict(totals)}")
    return work


def main():
    parser = argparse.ArgumentParser(description="Rebuild contextual prefixes in parallel shards")
    parser.add_argument("--vault", help="Explicit vault root")
    parser.add_argument("--shards", type=int, default=DEFAULT_SHARDS,
                        help=f"parallel workers, one vault copy each (default {DEFAULT_SHARDS})")
    parser.add_argument("--allow-egress", action="store_true",
                        help="consent to sending page text to Anthropic through the Claude Code CLI")
    parser.add_argument("--claude-cli-model", default="claude-haiku-5-5")
    parser.add_argument("--no-llm", action="store_true", help="synthetic prefixes only; no egress")
    parser.add_argument("--work-dir", type=Path, help="parent of the work directory (default: system temp)")
    parser.add_argument("--backup-dir", type=Path,
                        help="move the previous chunks and bm25 to BACKUP_DIR/<date>/ instead of .vault-meta")
    parser.add_argument("--lock-timeout", type=float, default=600.0)
    parser.add_argument("--no-install", action="store_true", help="build and merge only")
    parser.add_argument("--install-only", type=Path, metavar="WORKDIR", help="install an earlier build")
    parser.add_argument("--limit", type=int, help="first N pages only, for a trial (implies --no-install)")
    args = parser.parse_args()
    if args.shards < 1:
        parser.error("--shards must be at least 1")
    if not args.no_llm and not args.allow_egress:
        parser.error("a provider run sends page text to Anthropic and requires --allow-egress (or --no-llm)")
    try:
        vault = resolve_vault_root(args.vault, start=Path.cwd(), plugin_root=PLUGIN_ROOT).root
    except VaultSelectionError as exc:
        print(f"ERR {exc.code}: {exc}", file=sys.stderr)
        return 2
    try:
        work = args.install_only or build(args, vault)
        if args.install_only or not (args.no_install or args.limit):
            install(args, vault, work)
            say("done")
        else:
            say(f"not installed; install with --install-only {work}")
    except (StepFailed, TransactionError, OSError) as exc:
        say(f"FAILED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
