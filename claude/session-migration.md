# Claude Code Session Migration

This flow is separate from `claude/bundle.sh`. It is for inventorying many
local Claude Code sessions, selecting rows in one CSV, and creating one
migration archive plus a sibling installer.

## Source machine

### 1. Run the inventory

```bash
~/Projects/session-handoff/claude/inventory.py \
  -o ~/claude-session-inventory/claude-sessions-inventory.csv
```

The inventory scans `~/.claude/projects/` and writes one row per session
transcript. Project paths are decoded from Claude's directory name encoding —
a lossy process, since Claude replaces every non-alphanumeric character with
`-`. The `project_exists` column flags rows where the decoded path does not
exist on disk; correct `target_project_root` for those rows before bundling.

### 2. Edit the CSV in Excel

Open `claude-sessions-inventory.csv` and mark sessions to migrate by setting
the `migrate` column to one of:

```
yes  true  1  x  checked  migrate
```

If the project will live at a different path on the target machine, update
`target_project_root` for those rows. Leave it as-is if the path is the same
on both machines.

### 3. Bundle the selected sessions

```bash
~/Projects/session-handoff/claude/bundle-selected-sessions.py \
  -i ~/claude-session-inventory/claude-sessions-inventory.csv \
  -o ~/claude-session-migration-bundles
```

Output:

```
~/claude-session-migration-bundles/
  claude-session-migration-<timestamp>.tar.gz
  claude-session-migration-<timestamp>.install.sh
  claude-session-migration-<timestamp>.manifest.csv
```

Transfer the `.tar.gz` and matching `.install.sh` to the target machine.

## Target machine

Place the archive and installer in the same directory, then run:

```bash
chmod +x claude-session-migration-<timestamp>.install.sh
./claude-session-migration-<timestamp>.install.sh
```

The installer infers the sibling `.tar.gz` by filename. You can also pass the
archive explicitly:

```bash
./claude-session-migration-<timestamp>.install.sh ./claude-session-migration-<timestamp>.tar.gz
```

Default behavior skips existing files. Use `--replace` to overwrite or
`--interactive` to prompt per file.

The installer restores:

```
$CLAUDE_HOME/projects/<encoded-target-path>/
  <session-id>.jsonl     ← session transcript
  memory/                ← project memory files (if any)
```

Global config (`settings.json`, `CLAUDE.md`) is included in the archive under
`config/` for review but is not installed automatically. Copy manually only if
the target machine lacks its own.

Repos are not included. Clone, pull, or rsync the related repos separately.
Once the repos exist at the expected paths, open Claude Code in each project
and use `/resume` to continue the migrated sessions. The session appears in the
list as soon as the transcript is installed, but `/resume` is only meaningful
after the repo is present and the working tree matches what the prior session
expected.

## What's in the archive

```
claude-session-migration-<timestamp>/
  claude/
    projects/
      <encoded-target-path>/
        <session-id>.jsonl     ← session transcript
        memory/                ← project memory files (if any)
  config/
    settings.json              ← review-only, not auto-installed
    CLAUDE.md                  ← review-only, not auto-installed
  manifest.csv
  README.md
```
