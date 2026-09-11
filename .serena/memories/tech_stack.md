# tech stack

- Python standard library only. `pyproject.toml` declares `dependencies = []`
  and `requires-python = ">=3.9"`; CI proves 3.11, 3.12, 3.13, 3.14.
- Package root is `claude_obsidian/` at the repo top level. There is no `src/`
  layout. Hatchling wheel target is `packages = ["claude_obsidian"]`.
- One console script: `claude-obsidian = claude_obsidian.cli:main`. The v1 `co-*`
  entrypoints are gone; do not reference them.
- No dual-copy drift any more: every `scripts/*.py` imports `claude_obsidian`
  rather than mirroring its logic. Fix behavior in the package, not in a script.
- Test runner is hand-rolled, not pytest: `tests/test_*.py` are executed as
  scripts and `tests/test_*.sh` as bash. Adding pytest collection is not the
  convention here.
- No ruff/mypy config exists. A bare `ruff check` reports ~209 pre-existing
  findings on upstream code; it is not a gate. Do not add a config or mass-fix.
- CI is `.github/workflows/test.yml`, three jobs:
  - `test`: `make test` on ubuntu and macos across the 4 Python versions, then
    proves the suite mutated nothing (`git diff --exit-code` plus a clean
    `git status --porcelain -uall`).
  - `windows-smoke`: sets `core.autocrlf false` before checkout, then runs an
    explicit allowlist (`test_package_validation.py`, `test_knowledge_contracts.py`,
    `test_contracts.py`, `test_benchmark_tools.py`, `test_windows_compat.py`) plus
    `package validate` and `contracts --check-only`. The full suite is
    deliberately unsupported on native Windows (dirfd confinement, symlinks,
    fcntl, bash).
  - `release-safety`: builds the artifact twice, `cmp`s them for byte identity,
    then `release audit`.
- `.gitattributes` sets `* -text`. Line endings are never translated, because
  frontmatter parsing and content hashing need byte-exact LF. Never commit CRLF;
  a CRLF blob turns any later merge into a whole-file conflict.
