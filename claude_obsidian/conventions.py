"""Fork: vault-declared writing conventions, checked on every write.

Model-written text drifts when nothing owns its shape. A survey of one real
vault found 16 `status` values, `confidence: medium-high` beside a three-step
scale, seven names for the date a source was fetched, 50 pages with inline
`tags`, folder catalogs listing half their folder, and 13,000 long dashes the
owner never wanted. Each had been written by an agent following prose
instructions that nobody checked.

A vault opts in by writing `.vault-meta/conventions.json`. Every section is
optional, and an absent file changes nothing, so an adopted vault keeps its
own vocabulary until it declares one:

    {
      "schema": "claude-obsidian.conventions.v1",
      "log": {"style": "dated-bullets", "ops": ["save", "ingest"]},
      "frontmatter": {
        "values": {"status": ["seed", "developing", "evergreen"]},
        "value_aliases": {"status": {"stable": "evergreen"}},
        "list_properties": ["tags", "related"],
        "property_aliases": {"retrieved": "date_fetched"}
      },
      "text": {"forbidden_characters": ["\\u2014"], "exempt": ["wiki/folds/*"]},
      "catalogs": {"folder_index": "_index.md"}
    }

The transaction validator rejects a write that introduces a violation. It
judges only what the write adds: a property value the page did not already
carry, a line that existed in no file the operation read, a page created
without its folder catalog listing it. Old drift never blocks an unrelated
edit, and lines moved verbatim between files (a hot-cache entry going to its
archive, log entries going to a month page) are history, not new text. Lint
reports the whole vault, and `scripts/conventions-repair.py` drafts the
one-time repairs as reviewed transactions.

The text rule covers page bodies, not frontmatter. It ignores code, comments,
math, wikilinks, link destinations, URLs and spans in quotation marks, because
those carry other people's text or names that must match a file. Lint and the
repair leave existing headings alone, since a heading is a link anchor; a new
heading is judged like any other new line.
"""

from __future__ import annotations

import fnmatch
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from . import logfmt

CONVENTIONS_PATH = ".vault-meta/conventions.json"
CONVENTIONS_SCHEMA = "claude-obsidian.conventions.v1"
MAX_CONVENTIONS_BYTES = 256 * 1024
LOG_PATH = "wiki/log.md"
LOG_STYLES = ("dated-bullets",)

_SECTION_KEYS = {
    "log": {"style", "ops"},
    "frontmatter": {"values", "value_aliases", "list_properties", "property_aliases"},
    "text": {"forbidden_characters", "exempt"},
    "catalogs": {"folder_index"},
}
_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
_URL_RE = re.compile(r"https?://[^\s<>()\[\]]+")
_QUOTED_RE = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”|«[^»\n]*»")
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}(?:[ \t]|$)")


@dataclass(frozen=True)
class Conventions:
    log_style: str | None = None
    log_ops: frozenset[str] | None = None
    values: Mapping[str, frozenset[str]] = field(default_factory=dict)
    value_aliases: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    list_properties: frozenset[str] = frozenset()
    property_aliases: Mapping[str, str] = field(default_factory=dict)
    forbidden_characters: tuple[str, ...] = ()
    text_exempt: tuple[str, ...] = ()
    folder_index: str | None = None


@dataclass(frozen=True)
class Violation:
    path: str
    rule: str
    message: str
    line: int | None = None

    def as_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {"path": self.path, "rule": self.rule, "message": self.message}
        if self.line is not None:
            item["line"] = self.line
        return item


def _strings(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{where} must be a list of nonempty strings")
    return value


def _string_map(value: Any, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and key and isinstance(item, str) and item
        for key, item in value.items()
    ):
        raise ValueError(f"{where} must map nonempty strings to nonempty strings")
    return value


def parse_conventions(payload: Any) -> Conventions:
    """Validate a decoded conventions document; raise ValueError when wrong."""
    if not isinstance(payload, dict):
        raise ValueError("conventions must be a JSON object")
    if payload.get("schema") != CONVENTIONS_SCHEMA:
        raise ValueError(f"schema must be {CONVENTIONS_SCHEMA!r}")
    unknown = set(payload) - {"schema", *_SECTION_KEYS}
    if unknown:
        raise ValueError(f"unknown sections: {', '.join(sorted(unknown))}")
    sections: dict[str, dict[str, Any]] = {}
    for name, allowed in _SECTION_KEYS.items():
        section = payload.get(name, {})
        if not isinstance(section, dict):
            raise ValueError(f"{name} must be an object")
        extra = set(section) - allowed
        if extra:
            raise ValueError(f"unknown {name} keys: {', '.join(sorted(extra))}")
        sections[name] = section

    log = sections["log"]
    style = log.get("style")
    if style is not None and style not in LOG_STYLES:
        raise ValueError(f"log.style must be one of: {', '.join(LOG_STYLES)}")
    ops = None
    if "ops" in log:
        ops = frozenset(_strings(log["ops"], "log.ops"))
        bad = sorted(op for op in ops if not re.fullmatch(logfmt.OP_TOKEN, op))
        if bad:
            raise ValueError(f"log.ops must be lowercase tokens: {', '.join(bad)}")

    front = sections["frontmatter"]
    raw_values = front.get("values", {})
    if not isinstance(raw_values, dict):
        raise ValueError("frontmatter.values must be an object")
    values = {key.casefold(): frozenset(_strings(items, f"frontmatter.values.{key}"))
              for key, items in raw_values.items()}
    raw_aliases = front.get("value_aliases", {})
    if not isinstance(raw_aliases, dict):
        raise ValueError("frontmatter.value_aliases must be an object")
    value_aliases: dict[str, dict[str, str]] = {}
    for key, mapping in raw_aliases.items():
        mapping = _string_map(mapping, f"frontmatter.value_aliases.{key}")
        allowed_values = values.get(key.casefold())
        if allowed_values is None:
            raise ValueError(f"frontmatter.value_aliases.{key} needs frontmatter.values.{key}")
        wrong = sorted(target for target in mapping.values() if target not in allowed_values)
        if wrong:
            raise ValueError(f"frontmatter.value_aliases.{key} points outside its values: {', '.join(wrong)}")
        value_aliases[key.casefold()] = dict(mapping)
    list_properties = frozenset(
        key.casefold() for key in _strings(front.get("list_properties", []), "frontmatter.list_properties")
    ) if "list_properties" in front else frozenset()
    property_aliases = {
        key.casefold(): target.casefold()
        for key, target in _string_map(front.get("property_aliases", {}),
                                       "frontmatter.property_aliases").items()
    }
    chained = sorted(target for target in property_aliases.values() if target in property_aliases)
    if chained:
        raise ValueError(f"frontmatter.property_aliases target is itself an alias: {', '.join(chained)}")

    text = sections["text"]
    forbidden = tuple(_strings(text.get("forbidden_characters", []), "text.forbidden_characters")) \
        if "forbidden_characters" in text else ()
    if any(len(item) != 1 for item in forbidden):
        raise ValueError("text.forbidden_characters must hold single characters")
    exempt = tuple(_strings(text.get("exempt", []), "text.exempt")) if "exempt" in text else ()

    catalogs = sections["catalogs"]
    folder_index = catalogs.get("folder_index")
    if folder_index is not None and (
        not isinstance(folder_index, str) or "/" in folder_index or not folder_index.endswith(".md")
    ):
        raise ValueError("catalogs.folder_index must be a Markdown file name such as '_index.md'")

    return Conventions(
        log_style=style,
        log_ops=ops,
        values=values,
        value_aliases=value_aliases,
        list_properties=list_properties,
        property_aliases=property_aliases,
        forbidden_characters=forbidden,
        text_exempt=exempt,
        folder_index=folder_index,
    )


def load_conventions(read: Callable[[str], bytes | None]) -> Conventions | None:
    """Read the vault's conventions through `read(relative) -> bytes | None`."""
    from .json_utils import strict_json_loads

    raw = read(CONVENTIONS_PATH)
    if raw is None:
        return None
    try:
        payload = strict_json_loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{CONVENTIONS_PATH} is not valid JSON: {exc}") from exc
    return parse_conventions(payload)


# ------------------------------------------------------------------ frontmatter


def frontmatter_block(text: str) -> tuple[int, int, list[str], str] | None:
    """Return `(start, end, lines, newline)` of the property lines, or None.

    `start` and `end` are offsets of the first property line and of the
    closing delimiter, so a repair can splice new lines in without touching
    anything around them. `newline` is the page's own line ending, which a
    repair must keep.
    """
    newline = "\r\n" if text.startswith("---\r\n") else "\n"
    if not text.startswith("---" + newline):
        return None
    start = cursor = 3 + len(newline)
    while cursor <= len(text):
        end = text.find(newline, cursor)
        line = text[cursor:] if end < 0 else text[cursor:end]
        if line in {"---", "..."}:
            return start, cursor, text[start:cursor].split(newline)[:-1], newline
        if end < 0:
            return None
        cursor = end + len(newline)
    return None


def _scalar(raw: str) -> str:
    from .lint_engine import _yaml_scalar, _yaml_without_comment

    value = _yaml_scalar(_yaml_without_comment(raw).strip())
    return value if isinstance(value, str) else str(value)


def frontmatter_violations(text: str, conventions: Conventions) -> set[tuple[str, str, str]]:
    """Return `(property, rule, detail)` for each frontmatter rule the page breaks."""
    block = frontmatter_block(text)
    if block is None:
        return set()
    found: set[tuple[str, str, str]] = set()
    for line in block[2]:
        match = _KEY_RE.match(line)
        if not match:
            continue
        key, raw = match.group(1).casefold(), match.group(2).strip()
        if key in conventions.property_aliases:
            found.add((key, "property-alias", conventions.property_aliases[key]))
        if key in conventions.list_properties and raw and raw != "[]":
            found.add((key, "inline-list", raw[:60]))
        allowed = conventions.values.get(key)
        if allowed is not None and raw and not raw.startswith("["):
            value = _scalar(raw)
            if value not in allowed:
                found.add((key, "value", value))
    return found


def _frontmatter_message(item: tuple[str, str, str], conventions: Conventions) -> str:
    key, rule, detail = item
    if rule == "property-alias":
        return f"property {key!r} is an alias; write it as {detail!r}"
    if rule == "inline-list":
        return f"property {key!r} must be a block list, one '  - item' per line"
    hint = conventions.value_aliases.get(key, {}).get(detail)
    allowed = ", ".join(sorted(conventions.values.get(key, ())))
    return (f"{key} {detail!r} is not one of the vault's values ({allowed})"
            + (f"; use {hint!r}" if hint else ""))


# ------------------------------------------------------------------ body text


def protected_mask(text: str) -> str:
    """Blank everything the text rule ignores, keeping every offset and newline."""
    from .markdown_links import iter_markdown_links, iter_wikilinks, mask_protected_regions

    block = frontmatter_block(text)
    body_start = min(len(text), block[1] + 3 + len(block[3])) if block else 0
    chars = list(mask_protected_regions(text, body_start))

    def blank(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "

    for match in iter_wikilinks(text):
        blank(match.start(), match.end())
    for link in iter_markdown_links(text):
        blank(link.destination_start, link.destination_end)
    for match in _URL_RE.finditer(text):
        blank(match.start(), match.end())
    masked = "".join(chars)
    for match in _QUOTED_RE.finditer(masked):
        blank(match.start(), match.end())
    return "".join(chars)


def is_text_exempt(path: str, conventions: Conventions) -> bool:
    folded = path.casefold()
    return any(fnmatch.fnmatchcase(folded, pattern.casefold()) for pattern in conventions.text_exempt)


def forbidden_lines(
    text: str,
    conventions: Conventions,
    pool: Counter[str] | None = None,
    *,
    moved: Counter[str] | None = None,
    headings: bool = True,
) -> list[tuple[int, str]]:
    """Return `(line_number, character)` for new body lines holding a forbidden character.

    A line found in `pool` (the file's own old lines) or else in `moved`
    (lines other files gave up) is history and is consumed from it. With
    `headings=False` heading lines are skipped: an existing heading is a link
    anchor that a repair must not change, so only a new one is judged.
    """
    if not conventions.forbidden_characters:
        return []
    if not any(character in text for character in conventions.forbidden_characters):
        return []
    found = []
    for number, (line, masked) in enumerate(zip(text.split("\n"), protected_mask(text).split("\n")), 1):
        if pool is not None and pool[line] > 0:
            pool[line] -= 1
            continue
        if moved is not None and moved[line] > 0:
            moved[line] -= 1
            continue
        if not headings and _HEADING_RE.match(line):
            continue
        hit = next((character for character in conventions.forbidden_characters if character in masked), None)
        if hit is not None:
            found.append((number, hit))
    return found


def _replacement(line: str, index: int) -> str:
    before = line[index - 1] if index > 0 else " "
    after = line[index + 1] if index + 1 < len(line) else " "
    if line[index] == "\u2013" and not before.isspace() and not after.isspace():
        return "-"
    if before.isspace() or after.isspace():
        return "-"
    return " - "


def replace_forbidden(text: str, conventions: Conventions) -> tuple[str, int]:
    """Replace forbidden characters in unprotected body prose with a hyphen.

    Headings keep theirs: a heading is a link anchor, and changing it would
    break every `[[Page#Heading]]` that points at it. A word-joining long
    dash becomes a spaced hyphen; a range dash between two values stays tight.
    """
    if not conventions.forbidden_characters:
        return text, 0
    masked_lines = protected_mask(text).split("\n")
    out = []
    count = 0
    for line, masked in zip(text.split("\n"), masked_lines):
        if _HEADING_RE.match(line):
            out.append(line)
            continue
        positions = [index for index, character in enumerate(masked)
                     if character in conventions.forbidden_characters and line[index] == character]
        for index in reversed(positions):
            line = line[:index] + _replacement(line, index) + line[index + 1:]
            count += 1
        out.append(line)
    return "\n".join(out), count


# ------------------------------------------------------------------ catalogs


def catalog_path_for(page: str, conventions: Conventions) -> str | None:
    """The folder catalog that should list `page`, or None."""
    if conventions.folder_index is None:
        return None
    path = PurePosixPath(page)
    if path.suffix.casefold() != ".md" or path.name.casefold() == conventions.folder_index.casefold():
        return None
    if len(path.parts) < 3 or path.parts[0] != "wiki":
        return None
    return str(path.parent / conventions.folder_index)


def links_to(catalog_text: str, page: str) -> bool:
    """True when `catalog_text` holds a wikilink that names `page`."""
    from .markdown_links import iter_wikilinks

    path = PurePosixPath(page)
    stem = path.stem.casefold()
    full = str(path.with_suffix("")).casefold()
    in_wiki = full.removeprefix("wiki/")
    for match in iter_wikilinks(catalog_text):
        target = match.group("body").split("|", 1)[0].split("#", 1)[0].strip().casefold()
        target = target.removesuffix(".md")
        if target in {stem, full, in_wiki} or full.endswith("/" + target):
            return True
    return False


# ------------------------------------------------------------------ checks


def check_writes(
    writes: Sequence[tuple[str, bytes, bytes | None]],
    conventions: Conventions,
    read_existing: Callable[[str], bytes | None],
) -> list[Violation]:
    """Judge what one operation adds. `writes` holds `(path, new, old)` triples."""
    decoded: dict[str, tuple[str, str | None]] = {}
    for path, new, old in writes:
        if not path.startswith("wiki/") or not path.casefold().endswith(".md"):
            continue
        try:
            decoded[path] = (new.decode("utf-8"),
                             old.decode("utf-8") if old is not None else None)
        except UnicodeDecodeError:
            continue  # The markdown validator reports non-UTF-8 content.
    # A line another file in this operation gave up is a move, not new text.
    # Only lines actually removed from their file count, so copying a line
    # into a new page does not borrow its history.
    moved: Counter[str] = Counter()
    for new, old in decoded.values():
        if old is not None:
            moved.update(Counter(old.split("\n")) - Counter(new.split("\n")))

    violations: list[Violation] = []
    for path, (new, old) in decoded.items():
        before = frontmatter_violations(old, conventions) if old is not None else set()
        for item in sorted(frontmatter_violations(new, conventions) - before):
            violations.append(Violation(path, "frontmatter", _frontmatter_message(item, conventions)))
        if not is_text_exempt(path, conventions):
            own = Counter(old.split("\n")) if old is not None else Counter()
            for number, character in forbidden_lines(new, conventions, own, moved=moved):
                violations.append(Violation(
                    path, "forbidden-character",
                    f"new text uses U+{ord(character):04X}; write a plain hyphen", number))
        if path == LOG_PATH and conventions.log_style == "dated-bullets":
            for number, message in logfmt.added_line_violations(old, new, ops=conventions.log_ops):
                violations.append(Violation(path, "log-entry", message, number))

    for path, (new, old) in decoded.items():
        if old is not None:
            continue
        catalog = catalog_path_for(path, conventions)
        if catalog is None:
            continue
        planned = decoded.get(catalog)
        catalog_bytes = planned[0].encode("utf-8") if planned else read_existing(catalog)
        if catalog_bytes is None:
            continue
        if not links_to(catalog_bytes.decode("utf-8", "replace"), path):
            violations.append(Violation(
                path, "catalog", f"a new page must be listed in {catalog} in the same operation"))
    return violations


def vault_findings(
    pages: Iterable[tuple[str, str]], conventions: Conventions
) -> list[dict[str, Any]]:
    """Whole-vault report for lint. `pages` holds `(vault_relative_path, text)`."""
    pages = list(pages)
    texts = dict(pages)
    findings: list[dict[str, Any]] = []
    for path, text in pages:
        if not path.startswith("wiki/"):
            continue
        for item in sorted(frontmatter_violations(text, conventions)):
            findings.append({"path": path, "rule": "frontmatter",
                             "message": _frontmatter_message(item, conventions)})
        # The log's old lines are history; the write check judges its new ones.
        if path != LOG_PATH and not is_text_exempt(path, conventions):
            hits = forbidden_lines(text, conventions, headings=False)
            if hits:
                findings.append({"path": path, "rule": "forbidden-character", "line": hits[0][0],
                                 "count": len(hits),
                                 "message": f"{len(hits)} body lines use a forbidden character"})
        if path == LOG_PATH and conventions.log_style == "dated-bullets":
            for number, message in logfmt.added_line_violations(None, text, ops=conventions.log_ops):
                findings.append({"path": path, "rule": "log-entry", "line": number, "message": message})
        catalog = catalog_path_for(path, conventions)
        if catalog is not None and catalog in texts and not links_to(texts[catalog], path):
            findings.append({"path": path, "rule": "catalog", "message": f"not listed in {catalog}"})
    return findings
