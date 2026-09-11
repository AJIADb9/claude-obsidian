# claude-obsidian - core

Product source for a local-first Agent Skills package plus a Claude Code plugin
adapter. v2.2.0. This checkout is the AJIADb9 fork on branch `a/main`, rebased
onto upstream AgriciDaniel/claude-obsidian; fork-only files and the rules for
keeping them rebasable are in `mem:fork_local`.

`AGENTS.md` is the authoritative agent contract and outranks these memories on
any conflict. Root `CLAUDE.md` is host-only and absent from this checkout.

## Product is not a vault

- This repo is product source. It is never the default user vault.
- A user vault is the directory holding `.claude-obsidian.json`, `wiki/`, `.raw/`.
  All mutable knowledge state lives there, never here.
- `templates/vault/` is the distributable seed; `examples/sample-vault/` is a fixture.
- Never derive a vault from the plugin cache or `${CLAUDE_PLUGIN_ROOT}`.
- Resolution order: `--vault`, `CLAUDE_OBSIDIAN_VAULT`, nearest
  `.claude-obsidian.json`, then an unambiguous vault at or above cwd. Fail closed.
- Enforced by `tests/test_vault_root_separation.py` and
  `tests/test_installed_tree_boundary.py`.

## Top-level map

- `skills/` - 16 skills at `skills/<name>/SKILL.md` (see `mem:conventions` for the list)
- `claude_obsidian/` - the stdlib-only core package, at repo top level, no `src/` layout
- `scripts/` - thin entry points that import the core, plus `setup-*.sh` installers.
  There is no top-level `bin/`; v2.2.0 moved it to `scripts/` because claude.ai
  rejects a plugin shipping `bin/` (`_validate_no_legacy_bin_directory`).
- `config/` - `capabilities.json`, `product-contract.json`, `adapters.json`,
  `release-allowlist.json`, `public-marketplace.json`
- `agents/` - `verifier.md`, `wiki-ingest.md`, `wiki-lint.md`
- `hooks/hooks.json` - host lifecycle adapter only, never knowledge behavior
- `tests/` - hermetic python + shell suites, driven by `Makefile` (`mem:task_completion`)
- `RELEASE_MANIFEST.json`, `SHA256SUMS` - builder-owned, regenerated only by a
  release build; do not hand-edit

## Single core entry point

`scripts/claude-obsidian.py` (a 17-line shim over `claude_obsidian.cli:main`).
Subcommands: `doctor init adopt transaction checkpoint capture lint mode migrate
extension contracts package release hook`.

## Mutation protocol

One logical knowledge operation is one recoverable
`claude-obsidian.transaction.v1` bundle: read targets and record expected
SHA-256, let parallel workers return drafts only, merge into one bundle, inspect
it, apply once, report the operation id and changed paths. Approval hashes bind
to the environment that produced the plan, so a dry run and its apply must share
one environment. `scripts/wiki-lock.sh` is deprecated; do not reintroduce direct
shared writes or lifecycle auto-commits.

See also: `mem:tech_stack`, `mem:suggested_commands`, `mem:conventions`,
`mem:task_completion`, `mem:fork_local`.
