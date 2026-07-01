# Codex Session Migration

This flow is separate from `codex/bundle.sh`. It is for inventorying many local
Codex sessions, selecting rows in one CSV, and creating one migration archive
plus a sibling installer.

## Source machine

Run the inventory from anywhere:

```bash
~/Projects/session-handoff/codex/inventory.py \
  -o ~/codex-session-inventory/codex-sessions-inventory.csv
```

Open the CSV in Excel and mark sessions to migrate by setting `migrate` to one
of:

```text
yes
true
1
x
checked
migrate
```

Then build the selected-session migration bundle:

```bash
~/Projects/session-handoff/codex/bundle-selected-sessions.py \
  -i ~/codex-session-inventory/codex-sessions-inventory.csv \
  -o ~/codex-session-migration-bundles
```

Output:

```text
~/codex-session-migration-bundles/
  codex-session-migration-<timestamp>.tar.gz
  codex-session-migration-<timestamp>.install.sh
  codex-session-migration-<timestamp>.manifest.csv
```

Transfer the `.tar.gz` and matching `.install.sh` to the target machine.

## Target machine

Place the archive and installer in the same directory, then run:

```bash
chmod +x codex-session-migration-<timestamp>.install.sh
./codex-session-migration-<timestamp>.install.sh
```

The installer infers the sibling `.tar.gz` by filename. You can also pass the
archive explicitly:

```bash
./codex-session-migration-<timestamp>.install.sh ./codex-session-migration-<timestamp>.tar.gz
```

Default behavior skips existing files. Use `--replace` to overwrite or
`--interactive` to prompt per file.

The installer restores:

```text
$CODEX_HOME/sessions/...
$CODEX_HOME/archived_sessions/...
$CODEX_HOME/shell_snapshots/...
```

It does not modify `history.jsonl` or `session_index.jsonl`. Matching lines are
inside the archive for review only.

Repos are not included. Clone, pull, or rsync the related repos separately, then
run Codex from the target repo and use `/resume`.
