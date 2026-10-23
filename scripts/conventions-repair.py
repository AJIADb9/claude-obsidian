#!/usr/bin/env python3
"""conventions-repair.py - draft one-time repairs toward the vault's conventions.

Fork helper. Once a vault declares `.vault-meta/conventions.json`, the
transaction validator stops new drift, but the drift already written stays
until something repairs it. This drafts that repair as one reviewed `lint-fix`
transaction per kind. It never writes to the vault: it writes `bundle.json`
into --out and prints a summary, and the bundle is applied through
`transaction inspect` / `transaction apply` like any other operation.

Kinds:
  frontmatter  rename alias properties to their canonical name, map alias
               values (`status: stable` to `evergreen`), rewrite inline lists
               as block lists. A page that already has the canonical property
               keeps both and is reported instead.
  text         replace forbidden characters in body prose with a hyphen.
               Headings, code, links, URLs and quoted spans keep theirs, and so do the exempt paths and wiki/log.md, whose old
               lines are history.
  catalogs     list every page its folder catalog misses under an
               `## Uncatalogued` section at the end of that catalog, as a
               bare link: no summary is invented for it.

Usage:
  conventions-repair.py --vault PATH --fix frontmatter --out DIR
      [--operation-id ID] [--conventions FILE]

Exit codes:
  0 - bundle written, or nothing to repair (no bundle)
  2 - usage, vault selection or conventions failure
"""

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from claude_obsidian import conventions as cv
from claude_obsidian.json_utils import strict_json_loads
from claude_obsidian.lint_engine import _yaml_property_tokens, _yaml_scalar
from claude_obsidian.paths import VaultSelectionError, resolve_vault_root
from claude_obsidian.transaction import (
    BUNDLE_SCHEMA, TransactionError, read_vault_regular, sha256_bytes,
)

EXIT_OK = 0
EXIT_USAGE = 2
UNCATALOGUED = "## Uncatalogued"
_KEY_LINE_RE = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
_PLAIN_ITEM_RE = re.compile(r"^[^\s\"'\[\]{}#&*!|>%@`,:][^\[\]{}#,]*$")


def log(msg):
    print(msg, file=sys.stderr)


def wiki_pages(vault):
    """Yield (vault_relative_path, text) for every visible wiki Markdown page."""
    root = vault / "wiki"
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or not name.lower().endswith(".md"):
                continue
            relative = (Path(directory) / name).relative_to(vault).as_posix()
            data = read_vault_regular(vault, relative, limit=64 * 1024 * 1024, existing=True)
            if data is not None:
                yield relative, data.decode("utf-8")


def yaml_item(value):
    text = str(value)
    if _PLAIN_ITEM_RE.match(text) and text == text.strip():
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def repair_frontmatter(text, conventions):
    """Return (new_text, changes, conflicts) for one page."""
    block = cv.frontmatter_block(text)
    if block is None:
        return text, [], []
    start, end, lines, newline = block
    keys = {m.group(1).casefold() for m in map(_KEY_LINE_RE.match, lines) if m}
    out, changes, conflicts = [], [], []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _KEY_LINE_RE.match(line)
        if not match:
            out.append(line)
            index += 1
            continue
        key, raw = match.group(1), match.group(2)
        folded = key.casefold()
        name = key
        canonical = conventions.property_aliases.get(folded)
        if canonical is not None:
            if canonical in keys:
                conflicts.append(f"{folded} beside {canonical}")
            else:
                name = canonical
                keys.add(canonical)
                changes.append(f"{folded} -> {canonical}")
        value = raw.strip()
        alias = conventions.value_aliases.get(folded, {})
        scalar = cv._scalar(value) if value and not value.startswith("[") else None
        if scalar in alias:
            out.append(f"{name}: {alias[scalar]}")
            changes.append(f"{folded}: {scalar} -> {alias[scalar]}")
        elif folded in conventions.list_properties and value and value != "[]":
            if value.startswith("["):
                tokens = _yaml_property_tokens(raw)
                items = [_yaml_scalar(token) for token, _, _ in tokens] if tokens is not None else None
            else:
                items = [cv._scalar(value)]
            if isinstance(items, list) and all(isinstance(item, (str, int, float)) for item in items):
                out.append(f"{name}:")
                out.extend(f"  - {yaml_item(item)}" for item in items)
                changes.append(f"{folded}: inline list -> block list")
            else:
                conflicts.append(f"{folded}: list value outside the flat subset")
                out.append(f"{name}:{raw}")
        else:
            out.append(f"{name}:{raw}")
        index += 1
    if not changes:
        return text, [], conflicts
    body = newline.join(out) + newline if out else ""
    return text[:start] + body + text[end:], changes, conflicts


def repair_catalogs(pages, conventions):
    """Return {catalog_path: new_text} adding the pages each catalog misses."""
    texts = dict(pages)
    stems = {}
    for path in texts:
        stems.setdefault(PurePosixPath(path).stem.casefold(), []).append(path)
    missing = {}
    for path in sorted(texts):
        catalog = cv.catalog_path_for(path, conventions)
        if catalog is None or catalog not in texts or cv.links_to(texts[catalog], path):
            continue
        missing.setdefault(catalog, []).append(path)
    updated = {}
    for catalog, paths in missing.items():
        text = texts[catalog]
        if re.search(rf"^{re.escape(UNCATALOGUED)}\s*$", text, re.M):
            raise ValueError(f"{catalog} already has an {UNCATALOGUED} section; finish sorting it first")
        links = []
        for path in sorted(paths, key=lambda p: PurePosixPath(p).stem.casefold()):
            stem = PurePosixPath(path).stem
            if len(stems[stem.casefold()]) > 1:
                links.append(f"- [[{PurePosixPath(path).with_suffix('').as_posix()}|{stem}]]")
            else:
                links.append(f"- [[{stem}]]")
        section = [
            "",
            UNCATALOGUED,
            "",
            "Pages filed before catalogs were checked. Move each one under its topic heading.",
            "",
            *links,
            "",
        ]
        updated[catalog] = text.rstrip("\n") + "\n" + "\n".join(section)
    return updated


def main():
    parser = argparse.ArgumentParser(description="Draft one-time convention repairs")
    parser.add_argument("--vault")
    parser.add_argument("--fix", required=True, choices=("frontmatter", "text", "catalogs"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--operation-id")
    parser.add_argument("--conventions", type=Path,
                        help="read conventions from this file instead of the vault's")
    args = parser.parse_args()

    try:
        vault = resolve_vault_root(args.vault, start=Path.cwd(), plugin_root=PLUGIN_ROOT).root
    except VaultSelectionError as exc:
        log(f"ERR {exc.code}: {exc}")
        return EXIT_USAGE
    try:
        if args.conventions:
            conventions = cv.parse_conventions(strict_json_loads(args.conventions.read_text(encoding="utf-8")))
        else:
            conventions = cv.load_conventions(
                lambda relative: read_vault_regular(vault, relative, limit=cv.MAX_CONVENTIONS_BYTES))
    except (OSError, ValueError, TransactionError) as exc:
        log(f"ERR: conventions: {exc}")
        return EXIT_USAGE
    if conventions is None:
        log(f"ERR: the vault has no {cv.CONVENTIONS_PATH}")
        return EXIT_USAGE

    pages = list(wiki_pages(vault))
    updated, summary, conflicts = {}, {}, {}
    if args.fix == "frontmatter":
        for path, text in pages:
            new, changes, problems = repair_frontmatter(text, conventions)
            if problems:
                conflicts[path] = problems
            if new != text:
                updated[path] = new
                for change in changes:
                    summary[change] = summary.get(change, 0) + 1
    elif args.fix == "text":
        for path, text in pages:
            if path == cv.LOG_PATH or cv.is_text_exempt(path, conventions):
                continue
            new, count = cv.replace_forbidden(text, conventions)
            if count:
                updated[path] = new
                summary[path] = count
    else:
        try:
            updated = repair_catalogs(pages, conventions)
        except ValueError as exc:
            log(f"ERR: {exc}")
            return EXIT_USAGE
        summary = {path: text.split(UNCATALOGUED, 1)[1].count("\n- [[") for path, text in updated.items()}

    texts = dict(pages)
    operation_id = args.operation_id or f"lint-fix-{date.today():%Y%m%d}-conventions-{args.fix}"
    report = {"operation_id": operation_id, "fix": args.fix, "writes": len(updated),
              "summary": summary if args.fix != "text" else
              {"pages": len(updated), "replacements": sum(summary.values())},
              "conflicts": conflicts}
    if updated:
        args.out.mkdir(parents=True, exist_ok=True)
        bundle = {
            "schema": BUNDLE_SCHEMA,
            "operation_id": operation_id,
            "operation_type": "lint-fix",
            "expected_hashes": {path: sha256_bytes(texts[path].encode("utf-8")) for path in sorted(updated)},
            "writes": [{"path": path, "mode": "replace", "content": updated[path]}
                       for path in sorted(updated)],
        }
        target = args.out / "bundle.json"
        target.write_text(json.dumps(bundle, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        report["bundle"] = str(target)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
