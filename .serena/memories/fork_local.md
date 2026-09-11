# fork-local delta

`origin` is AJIADb9/claude-obsidian, `upstream` is AgriciDaniel/claude-obsidian.
Work branch is `a/main`, kept as a rebase onto `upstream/main`, never a merge.
Everything below exists only on the fork, so keep each item in its own commit and
expect to replay it on the next upstream release.

## Fork-only content

- `skills/youtube/SKILL.md` - transcript capture via `uvx youtube-transcript-api`,
  filed through `wiki-ingest`. It is the 16th skill and the reason every
  upstream count assertion of 15 has to become 16.
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
- `.serena/` (this config and these memories), `assets/diagrams/*.svg`,
  `assets/social-preview.png`, `docs/audits/*`, `docs/releases/v1.6.0.md` -
  fork-restored material. The audit and release docs are dated v1.6 to v1.9
  records; their `bin/` references are historically correct, leave them.

## Rebase hazards

- `config/capabilities.json` was once committed with CRLF, which made every merge
  a whole-file conflict. Resolve by taking the upstream side, then re-applying
  only the fork hunks (the `youtube` object and the `wiki-fold`
  implementation/network/verifier fields) in LF.
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
