# Claude Code Interactive Session Migration

`claude/migrate.py` collapses the three-step CSV flow into a single interactive command.
Use it when you want to select and transfer sessions without leaving the terminal.

## Usage

```bash
# Bundle locally (saved to ~/Downloads/claude-session-migration-bundles/)
~/Projects/session-handoff/claude/migrate.py

# Bundle and transfer directly to a remote machine
~/Projects/session-handoff/claude/migrate.py user@host
```

Options:

```
user@host             SSH target. If omitted, bundle is saved locally.
--claude-home PATH    Claude home directory (default: ~/.claude)
--target-dir PATH     Directory on target host to receive the bundle (default: ~/Downloads)
--output-dir PATH     Local output directory when no host is given
--replace             Pass --replace to the remote installer (user@host only)
--interactive         Pass --interactive to the remote installer (user@host only)
```

## Flow

### 1. Scan

The script scans `~/.claude/projects/` and builds an inventory in memory. No CSV is written.

### 2. Pick sessions

An `fzf` multi-select picker opens:

```
Sessions > ▌                                   < 20 (0)
  Tab·select  Enter·confirm  Esc·cancel
  PATH                                  ?  DATE        SIZE     TITLE
  …/mark/Projects/BSS                      2026-06-30   0.45MB  Organize Google Meet notes
  …/mark/Projects/caestudy-site-11ty       2026-06-28   3.21MB  Update homepage layout
  …/mark/Projects/session-handoff       ?  2026-07-01   1.77MB  Create session bundling scripts
```

- **Tab** to select or deselect a session
- **Type** to fuzzy-filter by path, date, size, or title
- **Enter** to confirm the selection
- **Esc** to cancel

The `?` column flags sessions whose project path could not be verified on this machine. This
happens when Claude's encoded directory name contains hyphens that decode ambiguously (e.g.
`/Users/mark/Projects/my-project` and `/Users/mark/Projects/my/project` encode identically).
These sessions can still be selected and migrated.

### 3. Confirm target paths (? sessions only)

If any selected sessions are flagged `?`, the script prompts once per affected project with the
decoded path pre-filled for editing:

```
1 project path(s) could not be verified on this machine.
Edit each target path — this determines where the session lands on the destination.

  -Users-mark-Projects-session-handoff
  target: /Users/mark/Projects/session-handoff▌
```

Edit the path if the project will live at a different location on the target machine. Press
Enter to accept the pre-filled value as-is.

If the project will live at the same path on the target machine, the pre-filled value is
already correct — just press Enter.

### 4. Bundle

Sessions are bundled into a `.tar.gz` archive with a sibling `.install.sh` installer.
Memory files (`memory/`) are included automatically for any project that has them.
Global config (`settings.json`, `CLAUDE.md`) is bundled under `config/` for review only
and is not installed automatically.

### 5. Transfer and install (with user@host)

The archive and installer are copied to the target machine via `scp`, then the installer
runs over SSH:

```
Transferring to mark@mini-m4:~/Downloads ...
Installing on mark@mini-m4 ...
Installing Claude session migration...
  archive:     ~/Downloads/claude-session-migration-20260701T120000Z.tar.gz
  claude_home: /Users/mark/.claude
  mode:        skip

…/Projects/BSS:
  installed:      /Users/mark/.claude/projects/-Users-mark-Projects-BSS/abc123.jsonl
  installed:      /Users/mark/.claude/projects/-Users-mark-Projects-BSS/memory/...

Bundle kept at mark@mini-m4:~/Downloads/
```

Default behavior skips existing files. Pass `--replace` or `--interactive` to `migrate.py`
to forward the flag to the remote installer automatically:

```bash
~/Projects/session-migrate/claude/migrate.py user@host --replace
~/Projects/session-migrate/claude/migrate.py user@host --interactive
```

When saving locally, run the installer directly with the desired flag:

```bash
bash ~/Downloads/claude-session-migration-bundles/claude-session-migration-<timestamp>.install.sh --replace
```

### 5. Save locally (no user@host)

Without a host, the archive and installer are saved to the output directory:

```
Archive:   ~/Downloads/claude-session-migration-bundles/claude-session-migration-<timestamp>.tar.gz
Installer: ~/Downloads/claude-session-migration-bundles/claude-session-migration-<timestamp>.install.sh
```

Transfer both files to the target machine and run:

```bash
./claude-session-migration-<timestamp>.install.sh
```

## After install

Repos are not included. Clone, pull, or rsync the related repos separately, then open
Claude Code in each target project and use `/resume`.

The session appears in the list as soon as the transcript is installed, but `/resume` is
only meaningful after the repo exists at the expected path.

## Requirements

- `fzf` — install with `brew install fzf`
- SSH key access to the target host (when using `user@host`)
