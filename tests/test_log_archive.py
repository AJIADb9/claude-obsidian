#!/usr/bin/env python3
"""test_log_archive.py - hermetic tests for scripts/log-archive.py.

Covers what could lose or reorder history: the byte-exact split of the log,
which entries move, month pages kept newest first across batches, the
cross-check against a fold's children, and the refusals.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "log-archive.py"
FOLD_EXTRACT = REPO / "scripts" / "fold-extract.py"

spec = importlib.util.spec_from_file_location("log_archive", SCRIPT)
la = importlib.util.module_from_spec(spec)
spec.loader.exec_module(la)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


LOG = """\
---
type: meta
title: "Wiki Log"
updated: 2026-08-02
tags:
  - meta
---

# Wiki Log

Chronological record of wiki operations.

## 2026-08-02

- **save** | Newest save
  - Created [[Newest]].

- **ingest** | Same-day ingest
  - Pages updated: [[Source A]]

## 2026-08-01

- **query** | August first query
  - Answered from [[Concept B]].

- **save** | August first save
  - Created [[Concept B]].

## 2026-07-31

- **fold** | Older fold entry
  - Range: 2026-07-01 to 2026-07-31

- **lint** | July lint
  - 3 dead links.
"""

FOLD_ENTRY = """\
- **fold** | batch-exponent-k1 rollup of 2 entries
  - Location: wiki/folds/example.md
"""


def make_vault(parent):
    vault = parent / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "wiki").mkdir()
    (vault / ".raw").mkdir()
    (vault / "wiki/log.md").write_text(LOG, encoding="utf-8", newline="\n")
    return vault


def run(vault, out, count, *extra, prepend=None):
    entry = out.parent / f"entry-{out.name}.md"
    entry.write_text(prepend or FOLD_ENTRY, encoding="utf-8", newline="\n")
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--vault", str(vault), "--count", str(count),
         "--prepend", str(entry), "--out", str(out), "--date", "2026-10-23", *extra],
        capture_output=True, text=True, encoding="utf-8", timeout=60)


def apply(vault, manifest):
    for write in manifest["writes"]:
        target = vault / write["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(write["content_file"], target)


def entry_blocks(text):
    _, sections = la.split_log(text)
    return ["\n".join(e["lines"]).rstrip("\n") for s in sections for e in s["entries"]]


def test_split_round_trips_exactly():
    print("test_split_round_trips_exactly")
    preamble, sections = la.split_log(LOG)
    check("render(split(log)) is byte-identical", la.render(preamble, sections) == LOG)
    check("six entries", sum(len(s["entries"]) for s in sections) == 6)
    tight = LOG.replace("[[Source A]]\n\n## 2026-08-01", "[[Source A]]\n## 2026-08-01")
    check("a heading right after text round-trips too",
          la.render(*la.split_log(tight)) == tight)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        vault = make_vault(root)
        (vault / "wiki/log.md").write_text(tight, encoding="utf-8", newline="\n")
        result = run(vault, root / "tight", 4)
        check("a tight log can be archived", result.returncode == 0, result.stderr)
        if result.returncode == 0:
            apply(vault, json.loads(result.stdout))
            august = (vault / "wiki/meta/log-archive/Log Archive 2026-08.md").read_text(encoding="utf-8")
            check("moved block ends before the next heading with a blank line",
                  "- **save** | August first save\n  - Created [[Concept B]].\n" in august)


def test_move_is_verbatim_and_newest_first():
    print("test_move_is_verbatim_and_newest_first")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        vault = make_vault(root)
        original = entry_blocks(LOG)

        first = run(vault, root / "out1", 3)
        check("first move succeeds", first.returncode == 0, first.stderr)
        manifest = json.loads(first.stdout)
        check("moves the three oldest, newest first",
              [m["title"] for m in manifest["moved"]] == ["August first save", "Older fold entry", "July lint"],
              manifest["moved"])
        check("two month pages", manifest["months"] == ["2026-08", "2026-07"], manifest["months"])
        modes = {w["path"]: w["mode"] for w in manifest["writes"]}
        check("archive pages are created", modes.get("wiki/meta/log-archive/Log Archive 2026-07.md") == "create")
        apply(vault, manifest)

        log_text = (vault / "wiki/log.md").read_text(encoding="utf-8")
        july = (vault / "wiki/meta/log-archive/Log Archive 2026-07.md").read_text(encoding="utf-8")
        august = (vault / "wiki/meta/log-archive/Log Archive 2026-08.md").read_text(encoding="utf-8")
        check("fold entry prepended under the new date",
              log_text.split("## ", 2)[1].startswith("2026-10-23\n\n- **fold** | batch-exponent-k1"))
        check("pointer names both months",
              "[[Log Archive 2026-08|2026-08]], [[Log Archive 2026-07|2026-07]]." in log_text)
        check("log frontmatter updated", "updated: 2026-10-23" in log_text)
        check("emptied July heading leaves the log", "## 2026-07-31" not in log_text)
        check("split August day keeps its heading in both",
              "## 2026-08-01" in log_text and "## 2026-08-01" in august)
        check("archive page has required frontmatter",
              all(f"{key}:" in july for key in ("title", "type", "status", "created", "updated", "tags")))
        moved_back = entry_blocks(log_text)[1:] + entry_blocks(august) + entry_blocks(july)
        check("every original entry survives verbatim, in order", moved_back == original,
              f"{moved_back!r}")

        second = run(vault, root / "out2", 2)
        check("second move succeeds", second.returncode == 0, second.stderr)
        manifest = json.loads(second.stdout)
        check("second move takes the next oldest",
              [m["title"] for m in manifest["moved"]] == ["Same-day ingest", "August first query"],
              manifest["moved"])
        check("existing month page is replaced, bound to its hash",
              any(w["mode"] == "replace" and w["expected_sha256"] for w in manifest["writes"]
                  if w["path"].endswith("2026-08.md")))
        apply(vault, manifest)
        august = (vault / "wiki/meta/log-archive/Log Archive 2026-08.md").read_text(encoding="utf-8")
        check("one heading per day in the archive", august.count("## 2026-08-01") == 1, august)
        check("August archive stays newest first",
              [b.splitlines()[0] for b in entry_blocks(august)] == [
                  "- **ingest** | Same-day ingest", "- **query** | August first query",
                  "- **save** | August first save"], entry_blocks(august))
        log_text = (vault / "wiki/log.md").read_text(encoding="utf-8")
        check("both fold entries stay on top of the log",
              [b.splitlines()[0][:10] for b in entry_blocks(log_text)[:2]] == ["- **fold**"] * 2)
        check("newest original entry still in the log", "- **save** | Newest save" in log_text)


def test_expect_fold_cross_check():
    print("test_expect_fold_cross_check")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        vault = make_vault(root)
        fold = subprocess.run([sys.executable, "-B", str(FOLD_EXTRACT), "--vault", str(vault), "--k", "1",
                               "--oldest", "--no-model"], capture_output=True, text=True, encoding="utf-8",
                              timeout=60)
        check("fold-extract --oldest succeeds", fold.returncode == 0, fold.stderr)
        fold_json = root / "fold.json"
        fold_json.write_text(fold.stdout, encoding="utf-8")
        children = [c["title"] for c in json.loads(fold.stdout)["children"]]
        check("--oldest picks the tail of the log", children == ["Older fold entry", "July lint"], children)
        matched = run(vault, root / "ok", 2, "--expect-fold", str(fold_json))
        check("matching fold accepted", matched.returncode == 0, matched.stderr)
        mismatched = run(vault, root / "bad", 3, "--expect-fold", str(fold_json))
        check("mismatching fold refused", mismatched.returncode == 4, mismatched.stderr)


def test_refusals_write_nothing_to_the_vault():
    print("test_refusals_write_nothing_to_the_vault")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        vault = make_vault(root)
        too_many = run(vault, root / "many", 7)
        check("more than the log holds", too_many.returncode == 3, too_many.stderr)
        two_entries = run(vault, root / "two", 1, prepend=FOLD_ENTRY + "- **save** | second\n")
        check("prepend must be one entry", two_entries.returncode == 2, two_entries.stderr)
        (vault / "wiki/log.md").write_bytes(LOG.replace("\n", "\r\n").encode("utf-8"))
        crlf = run(vault, root / "crlf", 1)
        check("CRLF log refused", crlf.returncode == 2, crlf.stderr)
        check("vault log untouched", (vault / "wiki/log.md").read_bytes() == LOG.replace("\n", "\r\n").encode())
        check("no archive written into the vault", not (vault / "wiki/meta").exists())


def main():
    for fn in (
        test_split_round_trips_exactly,
        test_move_is_verbatim_and_newest_first,
        test_expect_fold_cross_check,
        test_refusals_write_nothing_to_the_vault,
    ):
        fn()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("test_log_archive.py: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
