"""Fork: the one owner of the `wiki/log.md` entry format.

Every skill used to have the model type its own log line, and the vault ended
up with 124 entries the fold parser could not read: `query→web` as an op,
`**save**` followed by a long dash instead of the pipe, `**query** (standard) | title`
with a qualifier before it. Nothing rejected them, because no code owned the
format. This module does. `scripts/fold-extract.py`, `scripts/log-archive.py`,
the `log-entry` command and the transaction validator all read it from here.

The format, newest first:

    ## 2026-10-23

    - **save** | ElevenLabs voice generation
      - [[ElevenLabs Voice Generation]]: new question note.

    - **fold** | batch-exponent-k5 rollup of 32 entries

A date heading per day, one `- **op** | title` bullet per operation, its
details indented under it. An op is one lowercase token, optionally joined to
more with ` + ` when one logical piece of work was applied as two operations.
A qualifier such as `standard` or `autoresearch` belongs in the title or a
detail line, never in the op.

Reading stays lenient so the legacy shapes still parse as entries;
writing is strict.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date

DATE_HEADING_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$")
# `## 2026-09-06 (later)` occurs in the legacy log; it still names its day.
LEGACY_DATE_HEADING_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\b.*$")

OP_TOKEN = r"[a-z][a-z-]*"
# What fold-extract has always read. Spelling the op as tokens rather than a
# wide character class keeps bolded prose that contains a pipe from parsing as
# an entry.
ENTRY_RE = re.compile(
    rf"^-\s+\*\*({OP_TOKEN}(?:\s*\+\s*{OP_TOKEN})*)\*\*\s*\|\s*(.+?)\s*$",
    re.IGNORECASE,
)
# What a new entry must look like: lowercase op, single spaces, a title.
CANONICAL_ENTRY_RE = re.compile(
    rf"^- \*\*({OP_TOKEN}(?: \+ {OP_TOKEN})*)\*\* \| (\S(?:.*\S)?)$"
)
# Legacy shapes: any bold label, an optional `(qualifier)`, then a pipe or a
# long dash. A bold label with no separator stays prose.
LEGACY_ENTRY_RE = re.compile(
    r"^-\s+\*\*([^*\n]+?)\*\*\s*(?:\(([^()\n]*)\)\s*)?(?:\||\u2014|\u2013)\s*(.*?)\s*$"
)
CANONICAL_OP_RE = re.compile(rf"^{OP_TOKEN}(?: \+ {OP_TOKEN})*$")


class LogFormatError(ValueError):
    """A log entry or a log edit does not follow the format."""


def match_date_heading(line: str, *, lenient: bool = False) -> str | None:
    match = (LEGACY_DATE_HEADING_RE if lenient else DATE_HEADING_RE).match(line)
    return match.group(1) if match else None


def match_entry(line: str, *, lenient: bool = False) -> tuple[str, str] | None:
    """Return `(op, title)` for an entry line, or None.

    A legacy entry's op is its whole label as written, with any qualifier kept
    in parentheses, so a fold table shows what the log said.
    """
    match = ENTRY_RE.match(line)
    if match:
        return match.group(1).lower(), match.group(2).strip()
    if not lenient:
        return None
    match = LEGACY_ENTRY_RE.match(line)
    if not match:
        return None
    label = " ".join(match.group(1).split()).lower()
    if match.group(2) is not None:
        label = f"{label} ({' '.join(match.group(2).split())})"
    return label, match.group(3).strip()


def format_entry(op: str, title: str, details: Iterable[str] = ()) -> str:
    """Render one entry block, ending in a newline, or raise LogFormatError."""
    op = " ".join(op.split())
    if not CANONICAL_OP_RE.fullmatch(op):
        raise LogFormatError(
            f"op {op!r} must be lowercase tokens like 'save' or 'capture + save'; "
            "put a qualifier in the title or a detail line"
        )
    title = title.strip()
    if not title or "\n" in title:
        raise LogFormatError("title must be one nonempty line")
    lines = [f"- **{op}** | {title}"]
    for detail in details:
        detail = detail.strip()
        if not detail or "\n" in detail:
            raise LogFormatError("each detail must be one nonempty line")
        lines.append(f"  - {detail}")
    block = "\n".join(lines)
    if not CANONICAL_ENTRY_RE.match(lines[0]):
        raise LogFormatError(f"entry {lines[0]!r} does not follow the log format")
    return block + "\n"


def _first_heading(lines: Sequence[str]) -> int | None:
    return next((i for i, line in enumerate(lines) if line.startswith("## ")), None)


def insert_entry(text: str, day: str, block: str) -> str:
    """Put one entry block on top of the log, under the heading for `day`.

    Every existing byte keeps its place; only the block, and a new date
    heading when `day` has none yet, are added.
    """
    date.fromisoformat(day)
    block_lines = block.rstrip("\n").split("\n")
    if not block_lines or not CANONICAL_ENTRY_RE.match(block_lines[0]) or any(
        match_entry(line, lenient=True) or line.startswith("## ") for line in block_lines[1:]
    ):
        raise LogFormatError("the block must hold exactly one entry")
    lines = text.split("\n")
    first = _first_heading(lines)
    if first is None:
        # Only add: end the last line, add one blank separator, then the day.
        if not text or text == "\n" or text.endswith("\n\n"):
            lead = ""
        else:
            lead = "\n" if text.endswith("\n") else "\n\n"
        return text + lead + "\n".join([f"## {day}", "", *block_lines]) + "\n"
    newest = match_date_heading(lines[first], lenient=True)
    if newest is None:
        raise LogFormatError(f"the log's first heading is not a date: {lines[first]!r}")
    if newest > day:
        raise LogFormatError(f"the log already has a newer day ({newest}) than {day}")
    if newest == day:
        at = first + 1
        while at < len(lines) and lines[at] == "":
            at += 1
        lead = [""] if at == first + 1 else []
        new_lines = lines[:at] + lead + block_lines + [""] + lines[at:]
    else:
        new_lines = lines[:first] + [f"## {day}", ""] + block_lines + [""] + lines[first:]
    return "\n".join(new_lines)


def first_entry(text: str) -> tuple[str, str, str, list[str]] | None:
    """Return `(day, op, title, block_lines)` for the newest entry, or None."""
    lines = text.split("\n")
    first = _first_heading(lines)
    if first is None:
        return None
    day = match_date_heading(lines[first])
    if day is None:
        return None
    at = first + 1
    while at < len(lines) and lines[at] == "":
        at += 1
    if at >= len(lines) or not CANONICAL_ENTRY_RE.match(lines[at]):
        return None
    end = at + 1
    while end < len(lines) and lines[end].startswith((" ", "\t")):
        end += 1
    op, title = CANONICAL_ENTRY_RE.match(lines[at]).groups()
    return day, op, title, lines[at:end]


def is_prepend_of(previous: str, prospective: str) -> bool:
    """True when `prospective` is `previous` with exactly its newest entry added."""
    newest = first_entry(prospective)
    if newest is None:
        return False
    day, _, _, block = newest
    try:
        return insert_entry(previous, day, "\n".join(block) + "\n") == prospective
    except (LogFormatError, ValueError):
        return False


def added_line_violations(
    previous: str | None,
    prospective: str,
    *,
    moved: Iterable[str] = (),
    ops: frozenset[str] | None = None,
) -> list[tuple[int, str]]:
    """Return `(line_number, message)` for each new log line off the format.

    Lines already in the log, or moved from another file in the same
    operation, are history and are not judged. The preamble above the first
    heading is free text.
    """
    pool = Counter((previous or "").split("\n"))
    pool.update(moved)
    violations: list[tuple[int, str]] = []
    seen_heading = False
    for number, line in enumerate(prospective.split("\n"), 1):
        is_heading = line.startswith("## ")
        if pool[line] > 0:
            pool[line] -= 1
            seen_heading = seen_heading or is_heading
            continue
        if is_heading:
            seen_heading = True
            day = match_date_heading(line)
            try:
                date.fromisoformat(day or "")
            except ValueError:
                violations.append((number, "a log heading must be exactly '## YYYY-MM-DD', a real date"))
            continue
        if not seen_heading or not line.strip() or line.startswith((" ", "\t")):
            continue
        match = CANONICAL_ENTRY_RE.match(line)
        if not match:
            violations.append((number, "a log entry must be '- **op** | title' with a lowercase op; "
                                       "build it with `claude-obsidian.py log-entry`"))
        elif ops is not None:
            unknown = [token for token in match.group(1).split(" + ") if token not in ops]
            if unknown:
                violations.append((number, f"op {' + '.join(unknown)!r} is not in the vault's log ops"))
    return violations
