# fork-local delta

`origin` is AJIADb9/claude-obsidian, `upstream` is AgriciDaniel/claude-obsidian.
Work branch is `a/main`, kept as a rebase onto `upstream/main`, never a merge.
Everything below exists only on the fork, so keep each item in its own commit and
expect to replay it on the next upstream release.

## Fork-only content

- `skills/youtube/SKILL.md` + `scripts/youtube-ingest.py` + `tests/test_youtube_ingest.py`
  - the script fetches a transcript, has Haiku summarize it (isolated `claude -p`,
  needs `--allow-egress`; `--ollama-model` for local), and applies one `ingest`
  transaction (raw transcript, note flagged `enrichment: pending`, catalog link,
  log entry, manifest record, address); the session model then enriches through
  `wiki-ingest`. It is the 16th skill and the reason every upstream count
  assertion of 15 has to become 16; its verifier is added to the behavioral set
  in `tests/test_contracts.py`.
- `scripts/fold-extract.py` + `tests/test_fold_extract.py` - deterministic parser
  behind `wiki-fold`, with an optional local-Ollama synthesis pass that refuses a
  non-localhost `OLLAMA_URL` without `--allow-remote-ollama`. It makes `wiki-fold`
  a network-declaring capability with a real behavioral verifier.
- `scripts/co.sh` + `tests/test_co_wrapper.sh` + `docs/windows-wsl-fork.md` -
  platform-routed wrapper. On POSIX it calls the core directly; on native Windows
  it re-runs the same command through WSL, translating drive letters to `/mnt`
  and disabling MSYS argument conversion. Both a dry run and its apply must go
  through it, because approval hashes bind to the producing environment. A
  mounted-drive vault needs DrvFs metadata or applies fail with `RESULT_DRIFT`.
- `pyproject.toml` + `uv.lock` - uv/hatchling packaging; upstream ships neither.
  Bump its version by hand to match `.claude-plugin/plugin.json`.
- Hot-cache budget: 2048 estimated tokens instead of upstream's 500 words.
  `estimate_tokens` and `HOT_CACHE_TOKEN_BUDGET` live in `claude_obsidian/limits.py`;
  lint reports `oversized_hot_cache` (non-blocking) and `workflow.py` refuses
  `HOT_CONTEXT_TOO_LARGE` from the same estimate. Upstream's 32 KiB byte bound
  stays. Save, Ingest and `docs/workflow-acceptance.md` state the token budget.
- Prefix fallback tier in `scripts/contextual-prefix.py`: `--claude-cli-fallback`
  (needs `--allow-egress`) retries failed chunks through an isolated `claude -p`
  (tier `claude-code`, Haiku by default); `--claude-cli` makes that call the
  primary provider. Upstream's removed `claude-cli` tier and
  its tests stay removed; do not rename the fork tier to `claude-cli`. The prompt
  states a 30-word hard limit (`MODEL_PREFIX_PROMPT_WORDS`), which bumped
  `PREFIX_PROMPT_REVISION` to 4; an upstream prompt change must keep both.
  Documented in `docs/prefix-providers.md`.
- Log archive mode: `fold-extract.py --oldest`, `scripts/log-archive.py` (moves a
  fold's children verbatim into `wiki/meta/log-archive/Log Archive YYYY-MM.md`),
  and `transaction.py` letting a `fold` also write those month pages
  (`_LOG_ARCHIVE_PAGE_RE`). Documented in `skills/wiki-fold/SKILL.md`.
- `scripts/prefix-parallel.py` + `tests/test_prefix_parallel.py`: parallel shard
  rebuild of all prefixes (`--claude-cli`, 20 shards by default), installed into
  the vault under one MutationLock. Documented in `docs/prefix-providers.md`.
- Retrieval exclusions: `.vault-meta/retrieval.json` `{"exclude": [globs]}` read by
  `contextual-prefix.py`; separate from lint's `exclude`.
- Log format owner `claude_obsidian/logfmt.py` (strict write, lenient read of the
  legacy shapes) and the `log-entry` CLI command; `fold-extract.py` and
  `log-archive.py` import it. `workflow.py` also accepts a dated-bullets log
  (`logfmt.first_entry` / `is_prepend_of`) beside upstream's `## [date] op`.
- Vault conventions: `claude_obsidian/conventions.py`, opt-in through
  `.vault-meta/conventions.json`. `transaction._prepare_writes` collects the
  originals and calls `_validate_vault_conventions` (`CONVENTION_VIOLATION`,
  `INVALID_CONVENTIONS`); lint adds the `convention_violations` category;
  `scripts/conventions-repair.py` drafts `lint-fix` bundles. Upstream tests
  never write the file, so they are unaffected. Documented in
  `docs/vault-conventions.md`.
- `.serena/` (this config and these memories), `assets/diagrams/*.svg`,
  `assets/social-preview.png`, `docs/audits/*`, `docs/releases/v1.6.0.md` -
  fork-restored material. The audit and release docs are dated v1.6 to v1.9
  records; their `bin/` references are historically correct, leave them.

## Rebase hazards

- `config/capabilities.json` was once committed with CRLF, which made every merge
  a whole-file conflict. Resolve by taking the upstream side, then re-applying
  only the fork hunks (the `youtube` object and the `wiki-fold`
  implementation/network/verifier fields) in LF.
- Upstream tests that pin the 500-word hot limit (`tests/test_workflow.py`)
  must be moved past 2048 tokens; new ones may appear in any release.
- Serena regenerates `.serena/project.yml` with CRLF on Windows. Convert to LF
  before committing.
- Upstream renames land here too. v2.2.0 moved `bin/` to `scripts/`, which meant
  `bin/co.sh` became `scripts/co.sh` plus reference updates in the youtube skill,
  the wrapper test, and `docs/windows-wsl-fork.md`.
- When both sides rewrite the same skill paragraph, prefer keeping both: upstream
  usually adds a structural rule, the fork usually adds an egress note.

## Host traps

- WSL2 ext4-in-VHD quantizes file timestamps to 4ms. Any test that relies on two
  rapid writes producing distinct `st_mtime_ns` will fail here and pass on
  upstream CI. Construct the timestamp with `os.utime(..., ns=...)` instead of
  trusting the clock. `tests/test_transaction.py` needed exactly that fix.
- `release build` cannot run natively on Windows: its preflight audit of the
  staged archive reports `invalid_archive / ZIP end record is missing`. Pristine
  `upstream/main` fails the same way, and upstream runs release-safety on ubuntu
  only. Build in WSL.
- Before blaming a fork change for any failure, reproduce it against a pristine
  `upstream/main` clone in the same environment.
