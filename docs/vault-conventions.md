# Vault conventions (fork)

Agents write most of a vault's text, and prose instructions do not keep that
text consistent. A survey of one real vault after three months found:

- 16 different `status` values, and `confidence: medium-high` beside a
  three-step scale;
- seven property names for the date a source was fetched;
- 50 pages with inline `tags: [a, b]` beside block lists;
- folder catalogs (`_index.md`) listing about half of their folder;
- 124 log entries in shapes the fold parser could not read;
- 13,000 long dashes the vault owner never wanted.

Each value was written by an agent following instructions that nothing
checked. The fix is to make code own each shape, check every write against it,
and repair the old drift once.

## Declaring conventions

A vault opts in with `.vault-meta/conventions.json`. Without the file nothing
changes, so an adopted vault keeps its own vocabulary until it declares one.
Every section is optional.

```json
{
  "schema": "claude-obsidian.conventions.v1",
  "log": {"style": "dated-bullets", "ops": ["save", "ingest", "fold"]},
  "frontmatter": {
    "values": {
      "status": ["seed", "developing", "mature", "evergreen"],
      "confidence": ["low", "medium", "high"]
    },
    "value_aliases": {"status": {"stable": "evergreen"}},
    "list_properties": ["tags", "related", "sources", "aliases"],
    "property_aliases": {"retrieved": "date_fetched", "source_url": "url"}
  },
  "text": {
    "forbidden_characters": ["\u2014", "\u2013"],
    "exempt": ["wiki/folds/*", "wiki/meta/log-archive/*"]
  },
  "catalogs": {"folder_index": "_index.md"}
}
```

| Section | Rule |
|---|---|
| `log.style` | `dated-bullets`: new lines in `wiki/log.md` are `## YYYY-MM-DD` headings, `- **op** \| title` bullets, or indented details. `log.ops`, when present, lists the allowed ops. |
| `frontmatter.values` | A property's value must be one of the listed values. |
| `frontmatter.value_aliases` | Old values and the value they become. They appear in error messages and drive the repair. |
| `frontmatter.list_properties` | These properties must be block lists, one `  - item` per line. |
| `frontmatter.property_aliases` | Property names that must be written under their canonical name. |
| `text.forbidden_characters` | Characters that new body prose must not use. `exempt` globs (vault-relative, case-insensitive) skip the rule. |
| `catalogs.folder_index` | A page created in `wiki/<folder>/` must be linked from `wiki/<folder>/<folder_index>` in the same operation, when that catalog exists. |

The text rule covers page bodies, not frontmatter. It ignores code, comments,
math, wikilinks, link destinations, URLs and text inside quotation marks,
because those hold other people's words or names that must match a file.

## What is checked, and when

**Every transaction.** `transaction inspect` and `apply` (and the named
workflows built on them) refuse an operation with `CONVENTION_VIOLATION` when
it adds a violation. Only what the operation adds is judged:

- a frontmatter violation the page did not already have;
- a body line that existed in no file the operation read, so a line moved
  verbatim (a hot-cache entry going to its archive, log entries going to a
  month page) is history, not new text;
- a page created without its folder catalog linking it.

Old drift never blocks an unrelated edit. A malformed conventions file fails
closed with `INVALID_CONVENTIONS`.

**Lint.** `claude-obsidian.py lint` reports the whole vault under
`convention_violations`. Existing headings are not reported, because a heading
is a link anchor and renaming it breaks `[[Page#Heading]]` links; a new heading
is judged like any other new line.

## Writing log entries

`claude_obsidian/logfmt.py` owns the log format, and the `log-entry` command
drafts the log with one new entry:

```bash
python3 scripts/claude-obsidian.py log-entry --vault VAULT --op save \
  --title "ElevenLabs voice generation" \
  --detail "[[ElevenLabs Voice Generation]]: new question note." \
  --output /tmp/log.md
```

It writes nothing to the vault. It places the entry under today's heading
(adding the heading when the day has none), refuses an op that is not a
lowercase token, and prints `expected_sha256` and `sha256` for the bundle's
`wiki/log.md` write, whose `content_file` is the output. `--base FILE` adds the
entry to a draft log instead, such as a `log-archive.py` output.

Reading stays lenient: `fold-extract.py` and `log-archive.py` still parse the
legacy shapes (`**query→web** | ...`, a long dash instead of the pipe, a
`(qualifier)` before it, `## YYYY-MM-DD (later)`), so old entries fold like
new ones.

## Repairing old drift

`scripts/conventions-repair.py` drafts one `lint-fix` bundle per kind and
writes nothing to the vault:

```bash
python3 scripts/conventions-repair.py --vault VAULT --fix frontmatter --out DRAFT
python3 scripts/claude-obsidian.py transaction inspect DRAFT/bundle.json --vault VAULT
python3 scripts/claude-obsidian.py transaction apply DRAFT/bundle.json --vault VAULT \
  --approved-plan-sha256 SHA
```

| Kind | Repair |
|---|---|
| `frontmatter` | Renames alias properties, maps alias values, rewrites inline lists as block lists, keeping each page's line endings. A page that already has the canonical property keeps both and is reported as a conflict. |
| `text` | Replaces forbidden characters in body prose with a hyphen: a word-joining dash becomes ` - `, a range dash between two values stays tight. Headings, protected text, exempt paths and `wiki/log.md` (history) are left alone. |
| `catalogs` | Lists every page its folder catalog misses under an `## Uncatalogued` section at the end of the catalog, as a bare link. No summary is invented; sorting them under topic headings is left to a later edit. |

Run the kinds one at a time and apply each before drafting the next, since
each bundle pins the current hashes. Afterwards, lint reports no
`convention_violations` for the repaired kinds.
