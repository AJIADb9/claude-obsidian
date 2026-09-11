# task completion checklist

1. Behavioral change: run `make test` in WSL on ext4 (`mem:suggested_commands`
   explains why not over `/mnt`). On Windows, at minimum run the windows-smoke
   allowlist natively.
2. Touched `skills/` or `config/capabilities.json`: run
   `scripts/claude-obsidian.py contracts --check-only`, `contracts --verify`, and
   `package validate`. Adding or removing a skill also means updating the count
   assertions in `tests/test_contracts.py` and `tests/test_setup_multi_agent.py`
   (`mem:conventions`).
3. Touched `.claude-plugin/plugin.json`, `config/public-marketplace.json`, or
   `hooks/hooks.json`: `package validate` covers JSON validity and version drift.
4. Touched anything that ships: `scripts/claude-obsidian.py release build
   --output /tmp/x.zip` must stay green. It fails closed on unreviewed binaries,
   absolute user-home paths, emails, and secret-shaped strings, and it requires a
   clean worktree whose bytes match the index.
5. The suite must not mutate or create product files. CI proves this with
   `git diff --exit-code` and an empty `git status --porcelain -uall`; check the
   same locally before committing.
6. Never commit CRLF. `.gitattributes` is `* -text`, so a CRLF blob is stored
   verbatim and turns the next upstream merge into a whole-file conflict.
7. Non-trivial change: dispatch the `verifier` agent over `git diff --cached`
   before committing.
8. Do not add a linter or formatter config. None is configured and none is a gate.
9. Never push, tag, publish a release, or mutate issues without explicit owner
   approval. Pushes go to `origin` (the fork) only, never to `upstream`.
