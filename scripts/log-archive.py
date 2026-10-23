#!/usr/bin/env python3
"""log-archive.py - move the oldest wiki/log.md entries into month archive pages.

Fork helper behind wiki-fold's archive mode. It never writes to the vault: it
writes the proposed new file contents into --out and prints a JSON manifest
that the wiki-fold skill turns into one reviewed `fold` transaction, together
with the fold page and its index entry.

Entries are moved verbatim, with the entry boundaries fold-extract.py parses,
so the moved set is exactly the children of a `fold-extract.py --oldest` fold.
Each month page keeps its entries newest first, like the log. Folds always take
the oldest remaining entries, so a new batch is newer than anything already
archived and goes on top.

Usage:
  log-archive.py --vault PATH --count 64 --prepend fold-entry.md --out DIR \
      [--date YYYY-MM-DD] [--expect-fold fold.json]

Exit codes:
  0 - manifest printed, proposed files written to --out
  2 - usage, vault selection, or input validation failure
  3 - log missing, or fewer entries than --count
  4 - --expect-fold does not match the entries that would move
"""

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from claude_obsidian import logfmt
from claude_obsidian.paths import VaultSelectionError, resolve_vault_root
from claude_obsidian.transaction import TransactionError, read_vault_regular

_spec = importlib.util.spec_from_file_location("fold_extract", PLUGIN_ROOT / "scripts" / "fold-extract.py")
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)

LOG_RELATIVE = "wiki/log.md"
ARCHIVE_DIR = "wiki/meta/log-archive"
ARCHIVE_NAME = "Log Archive {month}"
POINTER_PREFIX = "Older entries are archived by month after each fold:"
UPDATED_RE = re.compile(r"^updated:\s*\S+\s*$")
DAY_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_LOG = 3
EXIT_MISMATCH = 4


def log(msg):
    print(msg, file=sys.stderr)


def split_log(text):
    """Split a log into (preamble, sections) without losing a byte.

    A section is one `## YYYY-MM-DD` heading with the lines before its first
    entry. An entry is one `- **op** | title` line plus every following line up
    to the next entry or heading, so text that fold-extract.py would not parse
    as body still moves with the entry it follows.
    """
    preamble, sections = [], []
    section = entry = None
    for line in text.split("\n"):
        day = logfmt.match_date_heading(line, lenient=True)
        if day:
            section = {"date": day, "head": [line], "entries": []}
            sections.append(section)
            entry = None
            continue
        if section is None:
            preamble.append(line)
            continue
        match = logfmt.match_entry(line, lenient=True)
        if match:
            entry = {"date": section["date"], "op": match[0],
                     "title": match[1], "lines": [line]}
            section["entries"].append(entry)
        elif entry is None:
            section["head"].append(line)
        else:
            entry["lines"].append(line)
    return preamble, sections


def render(preamble, sections):
    """Join exactly what was split. Separators for new boundaries are added
    where a move creates them, never here, so untouched text stays byte-exact."""
    lines = list(preamble)
    for section in sections:
        lines.extend(section["head"])
        for entry in section["entries"]:
            lines.extend(entry["lines"])
    return "\n".join(lines)


def ensure_trailing_blank(lines):
    return lines if lines and lines[-1] == "" else lines + [""]


def _end_with_blank(sections):
    """Give the last entry of a block a trailing blank line, because something
    new will follow it that did not follow it in the log."""
    if not sections or not sections[-1]["entries"]:
        return sections
    last = sections[-1]
    entries = last["entries"][:-1] + [{**last["entries"][-1],
                                       "lines": ensure_trailing_blank(last["entries"][-1]["lines"])}]
    return sections[:-1] + [{**last, "entries": entries}]


def take_oldest(sections, count):
    flat = [(si, ei) for si, section in enumerate(sections) for ei in range(len(section["entries"]))]
    chosen = set(flat[len(flat) - count:]) if count else set()
    kept, moved = [], []
    for si, section in enumerate(sections):
        stay = [e for ei, e in enumerate(section["entries"]) if (si, ei) not in chosen]
        go = [e for ei, e in enumerate(section["entries"]) if (si, ei) in chosen]
        if stay or not go:
            kept.append({**section, "entries": stay})
        if go:
            moved.append({"date": section["date"], "head": list(section["head"]), "entries": go})
    return kept, moved


def set_updated(preamble, today):
    """Rewrite the frontmatter `updated:` line; leave everything else alone."""
    if not preamble or preamble[0] != "---":
        return preamble
    out = list(preamble)
    for index in range(1, len(out)):
        if out[index] == "---":
            break
        if UPDATED_RE.match(out[index]):
            out[index] = f"updated: {today}"
    return out


def archive_preamble(month, today):
    title = ARCHIVE_NAME.format(month=month)
    return [
        "---",
        "type: meta",
        f'title: "{title}"',
        f"created: {today}",
        f"updated: {today}",
        "tags:",
        "  - meta",
        "  - log",
        "  - archive",
        "status: evergreen",
        "related:",
        '  - "[[log]]"',
        "---",
        "",
        f"# {title}",
        "",
        f"Log entries from {month}, moved verbatim out of [[log]] when they were folded, "
        "newest first. The fold pages listed in [[index]] under Folds hold their summaries.",
        "",
    ]


def merge_archive(existing, moved, month, today):
    """Put this month's moved sections on top of an existing or new page."""
    month_moved = _end_with_blank([s for s in moved if s["date"].startswith(month)])
    if existing is None:
        preamble, old = archive_preamble(month, today), []
    else:
        preamble, old = split_log(existing)
        preamble = set_updated(preamble, today)
    if old and month_moved and month_moved[-1]["date"] < old[0]["date"]:
        raise ValueError(f"archive {month} already holds entries newer than the batch")
    if old and month_moved and old[0]["date"] == month_moved[-1]["date"]:
        top = old[0]
        merged = {**top, "entries": [
            {**e, "lines": ensure_trailing_blank(e["lines"])} for e in month_moved[-1]["entries"]
        ] + top["entries"]}
        sections = month_moved[:-1] + [merged] + old[1:]
    else:
        sections = month_moved + old
    text = render(preamble, sections)
    return text if text.endswith("\n") else text + "\n"


def update_pointer(preamble, months):
    links = ", ".join(f"[[{ARCHIVE_NAME.format(month=m)}|{m}]]" for m in sorted(months, reverse=True))
    line = f"{POINTER_PREFIX} {links}."
    out = [existing for existing in preamble if not existing.startswith(POINTER_PREFIX)]
    while out and out[-1] == "":
        out.pop()
    return out + ["", line, ""]


def prepend_entry(kept, entry_text, day):
    lines = entry_text.rstrip("\n").split("\n")
    match = logfmt.CANONICAL_ENTRY_RE.match(lines[0]) if lines else None
    if not match or any(logfmt.match_entry(line, lenient=True) or line.startswith("## ")
                        for line in lines[1:]):
        raise ValueError("--prepend must hold exactly one '- **op** | title' entry")
    entry = {"date": day, "op": match.group(1), "title": match.group(2),
             "lines": lines + [""]}
    if kept and kept[0]["date"] > day:
        raise ValueError(f"the log already has a newer day ({kept[0]['date']}) than {day}")
    if kept and kept[0]["date"] == day:
        return [{**kept[0], "entries": [entry] + kept[0]["entries"]}] + kept[1:]
    return [{"date": day, "head": [f"## {day}", ""], "entries": [entry]}] + kept


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Move the oldest log entries into month archive pages")
    parser.add_argument("--vault", help="explicit user vault root")
    parser.add_argument("--count", type=int, required=True, help="number of oldest entries to move")
    parser.add_argument("--prepend", required=True, help="file holding the fold's own new log entry")
    parser.add_argument("--date", default=date.today().isoformat(), help="date heading for that entry")
    parser.add_argument("--out", required=True, help="directory for the proposed file contents")
    parser.add_argument("--expect-fold", help="fold-extract.py --oldest JSON the move must match")
    args = parser.parse_args()
    if args.count < 1 or not DAY_RE.fullmatch(args.date):
        log("ERR: --count must be positive and --date a real YYYY-MM-DD")
        return EXIT_USAGE

    try:
        vault = resolve_vault_root(args.vault, start=Path.cwd(), plugin_root=PLUGIN_ROOT).root
    except VaultSelectionError as exc:
        log(f"ERR {exc.code}: {exc}")
        return EXIT_USAGE
    try:
        raw = read_vault_regular(vault, LOG_RELATIVE)
    except TransactionError as exc:
        log(f"ERR {exc.code}: {exc}")
        return EXIT_USAGE
    if raw is None:
        log(f"ERR: no {LOG_RELATIVE} in {vault}")
        return EXIT_NO_LOG
    text = raw.decode("utf-8")
    if "\r" in text:
        log("ERR: log.md has CR line endings; this tool only moves LF text")
        return EXIT_USAGE

    preamble, sections = split_log(text)
    if render(preamble, sections) != text:
        log("ERR: log did not round-trip through the splitter; nothing proposed")
        return EXIT_USAGE
    total = sum(len(s["entries"]) for s in sections)
    if total != len(fx.parse_entries(text)):
        log("ERR: entry count disagrees with fold-extract.py; nothing proposed")
        return EXIT_USAGE
    if args.count > total:
        log(f"ERR: log holds {total} entries, cannot move {args.count}")
        return EXIT_NO_LOG

    kept, moved = take_oldest(sections, args.count)
    moved_keys = [(e["date"], e["op"], e["title"]) for s in moved for e in s["entries"]]
    if args.expect_fold:
        fold = json.loads(Path(args.expect_fold).read_text(encoding="utf-8"))
        expected = [(c["date"], c["op"], c["title"]) for c in fold.get("children", [])]
        if fold.get("selection") != "oldest" or expected != moved_keys:
            log("ERR: the entries that would move are not the fold's children")
            return EXIT_MISMATCH

    try:
        kept = prepend_entry(kept, Path(args.prepend).read_text(encoding="utf-8"), args.date)
    except ValueError as exc:
        log(f"ERR: {exc}")
        return EXIT_USAGE

    months = sorted({s["date"][:7] for s in moved}, reverse=True)
    existing_months = {
        p.stem.rsplit(" ", 1)[-1]
        for p in (vault / ARCHIVE_DIR).glob("Log Archive ????-??.md")
    } if (vault / ARCHIVE_DIR).is_dir() else set()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    writes = []
    for month in months:
        relative = f"{ARCHIVE_DIR}/{ARCHIVE_NAME.format(month=month)}.md"
        try:
            current = read_vault_regular(vault, relative)
        except TransactionError as exc:
            log(f"ERR {exc.code}: {exc}")
            return EXIT_USAGE
        existing = current.decode("utf-8") if current is not None else None
        try:
            content = merge_archive(existing, moved, month, args.date)
        except ValueError as exc:
            log(f"ERR: {exc}")
            return EXIT_USAGE
        target = out / f"{ARCHIVE_NAME.format(month=month)}.md"
        target.write_text(content, encoding="utf-8", newline="\n")
        writes.append({"path": relative, "mode": "replace" if existing is not None else "create",
                       "expected_sha256": sha256_text(existing) if existing is not None else None,
                       "content_file": str(target.resolve()), "sha256": sha256_text(content)})

    new_preamble = update_pointer(set_updated(preamble, args.date), existing_months | set(months))
    new_log = render(new_preamble, kept)
    new_log = new_log if new_log.endswith("\n") else new_log + "\n"
    target = out / "log.md"
    target.write_text(new_log, encoding="utf-8", newline="\n")
    writes.insert(0, {"path": LOG_RELATIVE, "mode": "replace", "expected_sha256": sha256_text(text),
                      "content_file": str(target.resolve()), "sha256": sha256_text(new_log)})

    print(json.dumps({"count": args.count, "moved": [
        {"date": d, "op": o, "title": t} for d, o, t in moved_keys
    ], "months": months, "writes": writes}, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
