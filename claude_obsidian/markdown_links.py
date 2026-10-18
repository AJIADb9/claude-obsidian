"""Shared, offset-preserving navigation parsing for the supported Markdown subset.

This is a navigation scanner, not a complete CommonMark or Obsidian renderer.
It handles comments, code, math, escapes, and inline path links. Reference-style
links, HTML anchors, and extension/plugin syntax are outside this subset.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterator

_FENCE = re.compile(r'(`{3,}|~{3,})(.*)$')
_LIST = re.compile(r'(?:[-+*]|\d{1,9}[.)])[ \t]+')
_PUNCTUATION_ESCAPE = re.compile(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])')
_HTML_RAW_OPEN_RE = re.compile(r'^ {0,3}<(script|pre|style|textarea)(?:[ \t>]|$)', re.I)
_HTML_BLOCK_TAG_RE = re.compile(
    r'^ {0,3}</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|'
    r'dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|'
    r'h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|'
    r'optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)'
    r'(?:[ \t\r\n]|/?>|$)', re.I,
)


def _html_block_end(text: str, start: int, line_end: int) -> int | None:
    """Bounded raw HTML support for heading analysis, not HTML rendering."""
    line = text[start:line_end]
    if (opening := _HTML_RAW_OPEN_RE.match(line)) is not None:
        closer = re.compile(r'</' + opening.group(1) + r'[ \t]*>', re.I).search(text, start)
        if closer is None:
            return len(text)
        end = text.find('\n', closer.end())
        return len(text) if end < 0 else end + 1
    if _HTML_BLOCK_TAG_RE.match(line):
        end = line_end
        while end < len(text):
            following = text.find('\n', end)
            following = len(text) if following < 0 else following + 1
            if not text[end:following].strip():
                break
            end = following
        return end
    return None


def mask_protected_regions(
    text: str,
    frontmatter_end: int = 0,
    *,
    preserve_inline_code: bool = False,
    preserve_block_code: bool = False,
    preserve_math: bool = False,
    mask_html_blocks: bool = False,
) -> str:
    """Blank protected content without moving a source offset or newline.

    List continuation indentation is relative to the current list item's content
    column. A four-space nested list continuation can therefore contain real
    navigation while a code block indented four more spaces cannot.

    Heading labels may preserve inline code, including its delimiters. Their
    block structure must still come from the default mask, since multiline
    inline code can contain text that resembles a heading or underline.

    Heading-only HTML masking consumes recognized raw blocks before their
    literal Markdown can affect following blocks. Both options default off for
    existing navigation callers. Generic custom tags and full HTML rendering
    remain outside this scanner.
    """
    chars = list(text)
    # Destinations are literal URL/path text, not prose math or comments. The
    # outer scanner still masks a whole link when enclosed in code or comments.
    destinations = {link.destination_start: link.destination_end
                    for link in iter_markdown_links(text)}
    destinations.update({match.start(): match.end()
                         for match in re.finditer(r'!?\[\[[^\]\r\n]+\]\]', text)})

    def blank(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in '\r\n':
                chars[index] = ' '

    blank(0, min(frontmatter_end, len(text)))
    index = min(frontmatter_end, len(text))
    lists: list[tuple[int, int]] = []
    while index < len(text):
        if index == 0 or text[index - 1] == '\n':
            end = text.find('\n', index)
            end = len(text) if end < 0 else end + 1
            line = text[index:end].rstrip('\r\n')
            expanded = line.expandtabs(4)
            indent = len(expanded) - len(expanded.lstrip(' '))
            if expanded.strip():
                while lists and indent < lists[-1][1]:
                    lists.pop()
                baseline = lists[-1][1] if lists else 0
                marker = _LIST.match(expanded, indent) if indent < baseline + 4 else None
                if marker:
                    lists.append((indent, marker.end()))
                    baseline = marker.end()
                fence_start = marker.end() if marker else indent
                fence = _FENCE.fullmatch(expanded[fence_start:]) if indent < baseline + 4 else None
                if fence and (fence[1][0] != '`' or '`' not in fence[2]):
                    marker_char, minimum = fence[1][0], len(fence[1])
                    close = re.compile(rf' {{0,{baseline + 3}}}{re.escape(marker_char)}{{{minimum},}}[ \t]*$')
                    fence_end = end
                    while fence_end < len(text):
                        next_end = text.find('\n', fence_end)
                        next_end = len(text) if next_end < 0 else next_end + 1
                        closing_line = text[fence_end:next_end].rstrip('\r\n').expandtabs(4)
                        closing_indent = len(closing_line) - len(closing_line.lstrip(' '))
                        if closing_line.strip() and closing_indent < baseline:
                            # A list-contained fence ends with its container.
                            # Revisit this line as ordinary outside Markdown.
                            break
                        fence_end = next_end
                        if close.fullmatch(closing_line):
                            break
                    if not preserve_block_code:
                        blank(index, fence_end)
                    index = fence_end
                    continue
                if not marker and indent >= baseline + 4:
                    if not preserve_block_code:
                        blank(index, end)
                    index = end
                    continue
                if mask_html_blocks and (html_end := _html_block_end(text, index, end)) is not None:
                    blank(index, html_end)
                    index = html_end
                    continue
        if index in destinations:
            index = destinations[index]
            continue
        if text[index] == '\\' and index + 1 < len(text):
            if text[index + 1] in '[!':
                blank(index + 1, index + 2)
            index += 2
            continue
        if text.startswith('<!--', index) or text.startswith('%%', index):
            delimiter, opening = ('-->', 4) if text.startswith('<!--', index) else ('%%', 2)
            closing = text.find(delimiter, index + opening)
            end = len(text) if closing < 0 else closing + len(delimiter)
            blank(index, end)
            index = end
            continue
        if text[index] == '`':
            opening = re.match(r'`+', text[index:])
            assert opening is not None
            end = index + len(opening[0])
            closer = next((match for match in re.finditer(r'`+', text[end:])
                           if len(match[0]) == len(opening[0])), None)
            if closer is not None:
                end += closer.end()
                if not preserve_inline_code:
                    blank(index, end)
            index = end
            continue
        if text[index] == '$':
            delimiter = '$$' if text.startswith('$$', index) else '$'
            search = index + len(delimiter)
            limit = len(text) if delimiter == '$$' else text.find('\n', search)
            if limit < 0:
                limit = len(text)
            closing = text.find(delimiter, search, limit)
            while closing >= 0 and (text[closing - 1] == '\\' or (delimiter == '$' and text[closing - 1].isspace())):
                closing = text.find(delimiter, closing + len(delimiter), limit)
            if closing > search and (delimiter == '$$' or not text[search].isspace()):
                end = closing + len(delimiter)
                if not preserve_math:
                    blank(index, end)
                index = end
                continue
        index += 1
    return ''.join(chars)


@dataclass(frozen=True)
class MarkdownLink:
    start: int
    end: int
    embed: bool
    destination: str
    destination_start: int
    destination_end: int
    label_start: int
    label_end: int


def markdown_destination(value: str) -> str:
    """Extract a destination, permitting angle delimiters and optional titles."""
    value = value.strip()
    if value.startswith('<'):
        closing = re.search(r'(?<!\\)>', value)
        if closing is None:
            return ''
        destination, suffix = value[1:closing.start()], value[closing.end():]
    else:
        match = re.match(r'(?:\\.|[^\s])+', value)
        if match is None:
            return ''
        destination, suffix = match[0], value[match.end():]
    if suffix and not re.fullmatch(r'\s+(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\((?:\\.|[^)\\])*\))\s*', suffix):
        return ''
    return html.unescape(_PUNCTUATION_ESCAPE.sub(r'\1', destination))


def _balanced_label_ends(text: str) -> dict[int, int]:
    """Pair label brackets in one pass while ignoring escapes and code spans."""
    ends: dict[int, int] = {}
    offset = 0
    while offset < len(text):
        line_end = len(text)
        for delimiter in ('\r', '\n'):
            candidate = text.find(delimiter, offset)
            if candidate >= 0:
                line_end = min(line_end, candidate)
        line = text[offset:line_end]
        runs = []
        for run in re.finditer(r'`+', line):
            backslashes = 0
            before = run.start() - 1
            while before >= 0 and line[before] == '\\':
                backslashes += 1
                before -= 1
            if backslashes % 2 == 0:
                runs.append(run)
        nearest_by_length: dict[int, tuple[int, int]] = {}
        code_ends: dict[int, int] = {}
        for run in reversed(runs):
            length = len(run[0])
            if length in nearest_by_length:
                code_ends[run.start()] = nearest_by_length[length][1]
            nearest_by_length[length] = run.span()

        run_ends = {run.start(): run.end() for run in runs}
        stack: list[int] = []
        index = 0
        while index < len(line):
            if index in code_ends:
                index = code_ends[index]
                continue
            if index in run_ends:
                index = run_ends[index]
                continue
            char = line[index]
            if char == '\\':
                index += 2
                continue
            if char == '[':
                stack.append(offset + index)
            elif char == ']' and stack:
                ends[stack.pop()] = offset + index
            index += 1

        offset = line_end
        if offset < len(text) and text[offset] == '\r':
            offset += 1
        if offset < len(text) and text[offset] == '\n':
            offset += 1
    return ends


def iter_markdown_links(masked: str) -> Iterator[MarkdownLink]:
    """Yield inline Markdown links with balanced labels and destinations."""
    opening = re.compile(r'(!?)\[')
    label_ends = _balanced_label_ends(masked)
    cursor = 0
    while (match := opening.search(masked, cursor)) is not None:
        label_start = match.end()
        label_end = label_ends.get(label_start - 1)
        if label_end is None or not masked.startswith('(', label_end + 1):
            cursor = match.end()
            continue
        start = label_end + 2
        index = start
        depth = 1
        angled = masked.startswith('<', start)
        quote = ''
        while index < len(masked) and masked[index] not in '\r\n':
            char = masked[index]
            if char == '\\':
                index += 2
                continue
            if angled:
                if char == '>':
                    angled = False
            elif quote:
                if char == quote:
                    quote = ''
            elif char in '\"\'' and index > start and masked[index - 1].isspace():
                quote = char
            elif char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    destination = markdown_destination(masked[start:index])
                    if destination:
                        yield MarkdownLink(
                            match.start(),
                            index + 1,
                            bool(match[1]),
                            destination,
                            start,
                            index,
                            label_start,
                            label_end,
                        )
                    break
            index += 1
        cursor = index + 1 if depth == 0 else match.end()


def markdown_visible_text(text: str) -> str:
    """Replace supported inline links with their visible source labels."""
    masked = mask_protected_regions(text)
    pieces: list[str] = []
    cursor = 0
    for link in iter_markdown_links(masked):
        pieces.append(text[cursor:link.start])
        pieces.append(text[link.label_start:link.label_end])
        cursor = link.end
    pieces.append(text[cursor:])
    return ''.join(pieces)


def mask_comments(text: str) -> str:
    """Blank HTML and Obsidian comments while preserving code and math."""
    return mask_protected_regions(
        text,
        preserve_inline_code=True,
        preserve_block_code=True,
        preserve_math=True,
    )
