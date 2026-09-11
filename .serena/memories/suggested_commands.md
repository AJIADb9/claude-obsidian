# suggested commands

Host is Windows. Repo tests and scripts are bash + python3 and assume POSIX, so
run them from Git Bash (the `Bash` tool) or WSL, never PowerShell. Per the user's
global CLAUDE.md, invoke Python as `uv run` (or `uv run --no-project`), never
bare `python`/`python3`; translate the repo's own `python3 X` examples yourself.

The full suite needs a real POSIX filesystem. Run it in WSL on ext4, not over
`/mnt/...` DrvFs: DrvFs makes `contracts --verify` report wiki-lint as
`degraded` and the suite fails for environment reasons alone.

## Tests (`Makefile`)

- `make test` - everything: `test-python`, `test-shell`, `test-contracts`, `test-package`
- `make test-python` / `make test-shell` - each `tests/test_*.py` / `tests/test_*.sh` in isolation
- `make test-contracts` - `contracts --check-only` then `contracts --verify`
- `make test-package` - `package validate`
- `make validate` - contracts + package without the suite
- `make clean-test-state` - wipe `.vault-meta/` locks, caches, journals

Windows portable surface (mirrors the CI allowlist, safe natively):
`uv run --no-project python tests/test_package_validation.py` and likewise
`test_knowledge_contracts.py`, `test_contracts.py`, `test_benchmark_tools.py`,
`test_windows_compat.py`, then `scripts/claude-obsidian.py package validate`
and `scripts/claude-obsidian.py contracts --check-only`.

## Release (local only, never publishes)

- `scripts/claude-obsidian.py release build --output PATH`
- `scripts/claude-obsidian.py release audit PATH`
- `scripts/claude-obsidian.py release gates [--execute]`

## Opt-in extension setup

`bash scripts/setup-vault.sh`, `setup-multi-agent.sh`, `setup-dragonscale.sh`,
`setup-retrieve.sh`, `setup-mode.sh`. Also `make setup-dragonscale|setup-retrieve|setup-mode`.

## Smoke checks

- `scripts/claude-obsidian.py doctor --vault PATH`
- `scripts/claude-obsidian.py mode get --vault PATH`
- `bash scripts/detect-transport.sh --peek`
