# Claude plugin namespace migration

The Claude plugin identity is `agricidaniel-obsidian`. The repository, portable
Python core, vault configuration and transaction schemas retain their existing
claude-obsidian identities. Existing user vaults need no rename or content
migration.

`claude plugin validate` reports a plugin name starting with `claude-` as an
error, and `claude plugin init` and `claude plugin tag` refuse it, because the
name reads as one of Anthropic's own plugins. Claude Code still installs and
loads such a plugin; the rename keeps the strict validator and release tagging
usable, not installation. The earlier `obsidian-wiki` proposal is withdrawn
because another plugin uses that name. The owner-qualified name avoids that
known collision. It is not a claim that a name is globally exclusive. See the
[current manifest reference](https://code.claude.com/docs/en/plugins-reference#name).

## Try it from a checkout

Load a local checkout without changing the installed plugin:

```bash
claude --plugin-dir /absolute/path/to/claude-obsidian
```

Invoke `/agricidaniel-obsidian:wiki`, `/agricidaniel-obsidian:save`, or the
other skills under the same prefix. Existing `/claude-obsidian:*` invocations
belong to the old adapter. An alias is not provided. Portable skill names and
discovery paths remain unchanged.

## Published upgrade

The marketplace channel remains `agricidaniel-claude-obsidian`. Its plugin
entry changes to `agricidaniel-obsidian`, matching the manifest, and the
marketplace carries a top-level `renames` map from `claude-obsidian` to
`agricidaniel-obsidian`. Following the
[documented rename mechanism](https://code.claude.com/docs/en/plugins/host-marketplace#rename-or-remove-a-plugin),
Claude Code then migrates users instead of reporting
`Plugin "claude-obsidian" not found in marketplace`:

1. Update the user's copy of the marketplace:

   ```bash
   claude plugin marketplace update agricidaniel-claude-obsidian
   ```

   On the next `claude` start, Claude Code rewrites the
   `claude-obsidian@agricidaniel-claude-obsidian` key in `enabledPlugins` and
   `pluginConfigs` to the new name in the user, project and local scopes.
   The old plugin is then uninstalled, and the renamed one is not installed
   yet.
2. Each user installs it once:

   ```bash
   claude plugin install agricidaniel-obsidian@agricidaniel-claude-obsidian
   ```

3. Restart Claude Code and use `/agricidaniel-obsidian:*` commands.

Managed settings cannot be rewritten by Claude Code. An administrator who
enables the plugin there must change the key to
`agricidaniel-obsidian@agricidaniel-claude-obsidian`. Treat `renames` as
append-only history: a later rename adds an entry and keeps this one.
`package validate` rejects a renames chain that does not end in a listed plugin
or `null`, and the test suite requires the former identity to keep migrating to
the current one.

### Verify

After the install in step 2, check that the expected version and source are
installed and enabled, and that no entry reports an error. Run before the
install, the listing can briefly still show the old name as enabled.

```bash
claude plugin list --json
```

In a disposable vault, confirm the new namespace exposes `wiki`, `save`,
`wiki-ingest`, and `wiki-query` once; confirm explicit vault selection, a
reviewed Save/Ingest operation and a Query that leaves the vault unchanged. An
installed-plugin listing or metadata validator alone does not establish skill
loading or successful operation.

### Roll back

Once the marketplace serves the `renames` map, the old name is no longer listed
there, so re-enabling `claude-obsidian` from that channel is not possible. To
return to the previous adapter, replace the channel with one pinned to the last
release before the rename:

```bash
claude plugin disable agricidaniel-obsidian@agricidaniel-claude-obsidian
claude plugin marketplace remove agricidaniel-claude-obsidian
claude plugin marketplace add AgriciDaniel/claude-obsidian#v2.2.0
claude plugin install claude-obsidian@agricidaniel-claude-obsidian
claude plugin list --json
```

Removing a marketplace also removes the plugins installed from it, so
reinstall from the pinned channel as shown. To move forward again, remove the
pinned channel, add `AgriciDaniel/claude-obsidian` without a ref, and install
`agricidaniel-obsidian@agricidaniel-claude-obsidian`. Vault files require no
rollback for an adapter-only change.

## Evidence and limits

With Claude Code 2.1.302 on 2026-10-18, an isolated `CLAUDE_CONFIG_DIR` and a
local git copy of the marketplace:

- Publishing this tree without the `renames` map left the existing install
  reporting `Plugin claude-obsidian not found in marketplace`.
- With the map, `claude plugin marketplace update` rewrote the enabled key to
  `agricidaniel-obsidian@agricidaniel-claude-obsidian`, and one
  `claude plugin install` completed the migration.
- The rollback above, using a local path in place of the GitHub ref,
  reinstalled `claude-obsidian` from the pre-rename commit.

With Claude Code 2.1.304 on 2026-10-20, repeated with both a local directory
and a git marketplace served over local smart HTTP:

- `claude plugin validate --strict` rejected the `claude-obsidian` name as
  reserved and passed the renamed tree.
- `marketplace update` itself left settings unchanged. The next `claude` start
  rewrote the enabled key and uninstalled the old plugin, `plugin list` then
  reported no plugins, and one `claude plugin install` completed the
  migration.
- In a live session loaded with `--plugin-dir`, all 15 skills and 3 agents were
  listed under `agricidaniel-obsidian:`.
- The rollback, using the `v2.2.0` tag of the local git channel, reinstalled
  `claude-obsidian` 2.2.0 with files identical to the tag.

The GitHub-hosted channel, project and local scopes, managed settings and
Windows were not exercised. Check the target
host's help before use. Local source changes do not authorize installation,
commits, publishing, or account changes. See the
[installation and uninstall guide](install-guide.md).
