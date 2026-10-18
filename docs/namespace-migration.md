# Claude plugin namespace migration

The Claude plugin identity in this local candidate is `agricidaniel-obsidian`.
The repository, portable Python core, vault configuration and transaction
schemas retain their existing claude-obsidian identities. Existing user vaults
need no rename or content migration.

Current Claude Code reserves third-party plugin names starting with `claude-`.
The earlier `obsidian-wiki` proposal is withdrawn because another plugin uses
that name. The owner-qualified candidate avoids that known collision. This is
not a claim that a name is globally exclusive or that every installation has
been tested. See the [current manifest reference](https://code.claude.com/docs/en/plugins-reference#name).

## Local candidate

Load the candidate without changing the installed plugin:

```bash
claude --plugin-dir /absolute/path/to/claude-obsidian
```

Invoke `/agricidaniel-obsidian:wiki`, `/agricidaniel-obsidian:save`, or the
other skills under the same prefix. Existing `/claude-obsidian:*` invocations
belong to the old installed adapter. An alias is not provided. Portable skill
names and discovery paths remain unchanged.

## Published upgrade

The marketplace channel remains `agricidaniel-claude-obsidian`. Its plugin
entry changes to `agricidaniel-obsidian`, matching the manifest. Installation
commands in the guide describe the new package only after that candidate has
been approved and published. They do not assert that the live marketplace
already serves this uncommitted work.

### Record and preserve the installed adapter

Before an owner-approved installation change, save these read-only outputs
locally and retain a known-good package outside the disposable plugin cache:

```bash
claude --version
claude plugin list --json
claude plugin disable --help
claude plugin enable --help
claude plugin install --help
claude plugin uninstall --help
```

Record the version, exact plugin ID, scope, source and enabled state for every
matching entry. The examples below use the usual legacy ID and `user` scope.
Set `PLUGIN_SCOPE` to the recorded `user`, `project`, or `local` scope; run
project/local commands from the original project. If the old adapter occurs in
several scopes, handle each recorded entry deliberately. Do not infer that a
user-scope change removed a project-scope copy.

### Disable, install, then verify

After publication and explicit owner approval, disable the old adapter while
retaining its installed files for rollback, then install the new identity:

```bash
PLUGIN_SCOPE=user
OLD_PLUGIN=claude-obsidian@agricidaniel-claude-obsidian
NEW_PLUGIN=agricidaniel-obsidian@agricidaniel-claude-obsidian
claude plugin disable "$OLD_PLUGIN" --scope "$PLUGIN_SCOPE"
claude plugin install "$NEW_PLUGIN" --scope "$PLUGIN_SCOPE"
claude plugin list --json
```

Check that the expected new version and source are installed and enabled in
that scope, and that every old entry is disabled. Restart Claude Code so the
active session no longer holds the old adapter. In a disposable vault, confirm
the new namespace exposes `wiki`, `save`, `wiki-ingest`, and `wiki-query` once;
confirm explicit vault selection, a reviewed Save/Ingest operation and a Query
that leaves the vault unchanged. An installed-plugin listing or metadata
validator alone does not establish skill loading or successful operation.

If installation or verification fails, use the rollback below. Keep the old
adapter installed until the replacement has passed acceptance.

### Roll back before cleanup

Use the same recorded scope and IDs. If the new entry exists, disable it;
then re-enable the preserved old entry and start a fresh session:

```bash
claude plugin disable "$NEW_PLUGIN" --scope "$PLUGIN_SCOPE"
claude plugin enable "$OLD_PLUGIN" --scope "$PLUGIN_SCOPE"
claude plugin list --json
```

If installation failed before the new entry existed, skip its disable command.
Verify the old version, source and enabled state against the saved inventory,
then use its original `/claude-obsidian:*` commands. Vault files require no
rollback for an adapter-only change.

### Optional cleanup after acceptance

Only after acceptance and an explicitly approved cleanup, remove the disabled
old entry from its recorded scope while preserving plugin-local saved data:

```bash
claude plugin uninstall "$OLD_PLUGIN" --scope "$PLUGIN_SCOPE" --keep-data
claude plugin list --json
```

Do not remove the marketplace channel: the new adapter uses that same channel.
After uninstalling the old entry, `enable` cannot restore it. Restoring a saved
legacy package needs a compatible host; current reserved-name validation can
reject reinstalling that old identity. Retain the disabled installation when
this rollback limit is unacceptable, or use the reviewed portable-host setup
from the saved package as a separate installation decision.

These command shapes were checked against Claude Code 2.1.302 help on
2026-10-17. Check the target host's help before use, especially `--keep-data`.
No installation, coexistence or rollback was executed for this local candidate.
Local source changes do not authorize installation, commits, publishing, or
account changes. See the [installation and uninstall guide](install-guide.md).
