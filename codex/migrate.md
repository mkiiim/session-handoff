# Codex Interactive Session Migration

`codex/migrate.py` collapses the three-step CSV flow into a single interactive command.
Use it when you want to select and transfer sessions without leaving the terminal.

## Usage

```bash
# Bundle locally (saved to ~/Downloads/codex-session-migration-bundles/)
~/Projects/session-handoff/codex/migrate.py

# Bundle and transfer directly to a remote machine
~/Projects/session-handoff/codex/migrate.py user@host
```

Options:

```
user@host             SSH target. If omitted, bundle is saved locally.
--codex-home PATH     Codex home directory (default: $CODEX_HOME or ~/.codex)
--target-dir PATH     Directory on target host to receive the bundle (default: ~/Downloads)
--output-dir PATH     Local output directory when no host is given
```

## Flow

### 1. Scan

The script scans `~/.codex/sessions/` and `~/.codex/archived_sessions/` and builds an
inventory in memory. No CSV is written.

### 2. Pick sessions

An `fzf` multi-select picker opens:

```
Sessions > ▌                                    < 17 (0)
  Tab·select  Enter·confirm  Esc·cancel
  PATH                              !  AREA      DATE        SIZE     TITLE
  /Users/mark/Projects/BSS             sessions  2026-06-30   0.45MB  Organize Google Meet notes
  /Users/mark/Projects/codex           sessions  2026-06-28   3.21MB  Review line branching brief
  /Volumes/Orico/Projects/archive   !  archived  2026-05-10   0.82MB  Port legacy auth module
```

- **Tab** to select or deselect a session
- **Type** to fuzzy-filter by path, area, date, size, or title
- **Enter** to confirm the selection
- **Esc** to cancel

The `!` column flags sessions whose recorded working directory does not exist on this machine
(e.g. a repo that lived on an external drive). These sessions can still be selected and
migrated — the install path is always relative to `CODEX_HOME` and does not depend on the
original working directory.

The **AREA** column shows `sessions` for active rollouts and `archived` for archived ones,
matching the subdirectory structure under `CODEX_HOME`.

### 3. Bundle

Selected sessions are bundled into a `.tar.gz` archive with a sibling `.install.sh` installer
and a `manifest.csv` audit log. Shell snapshots are included automatically. Matching lines
from `history.jsonl` and `session_index.jsonl` are included under `codex/` for review only
and are not installed automatically.

### 4. Transfer and install (with user@host)

The archive and installer are copied to the target machine via `scp`, then the installer
runs over SSH:

```
Transferring to mark@mini-m4:~/Downloads ...
Installing on mark@mini-m4 ...
Installing Codex session migration...
  archive:    ~/Downloads/codex-session-migration-20260701T120000Z.tar.gz
  codex_home: /Users/mark/.codex
  mode:       skip

Session rollouts:
  installed:      /Users/mark/.codex/sessions/2026/06/30/rollout-2026-06-30T...jsonl
Archived sessions:
  installed:      /Users/mark/.codex/archived_sessions/rollout-...jsonl

Bundle kept at mark@mini-m4:~/Downloads/
```

Default behavior skips existing files. Pass `--replace` or `--interactive` to the installer
directly if you need different conflict handling:

```bash
bash ~/Downloads/codex-session-migration-<timestamp>.install.sh --replace
```

### 4. Save locally (no user@host)

Without a host, the archive, installer, and manifest are saved to the output directory:

```
Archive:   ~/Downloads/codex-session-migration-bundles/codex-session-migration-<timestamp>.tar.gz
Installer: ~/Downloads/codex-session-migration-bundles/codex-session-migration-<timestamp>.install.sh
Manifest:  ~/Downloads/codex-session-migration-bundles/codex-session-migration-<timestamp>.manifest.csv
```

Transfer the `.tar.gz` and `.install.sh` to the target machine and run:

```bash
./codex-session-migration-<timestamp>.install.sh
```

## After install

Repos are not included. Clone, pull, or rsync the related repos separately, then run Codex
from the target repo and use `/resume`.

## Requirements

- `fzf` — install with `brew install fzf`
- SSH key access to the target host (when using `user@host`)
