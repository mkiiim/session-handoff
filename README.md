# session-handoff

Bundle an AI coding session for transfer to another machine.

Captures the session transcript, project memory, and repo state, and generates a ready-to-paste continuation prompt for the receiving machine.

## Tools

| Directory | Tool | Session store |
|---|---|---|
| `claude/` | [Claude Code](https://claude.ai/code) | `~/.claude/projects/` |
| `codex/` | [Codex CLI](https://github.com/openai/codex) | `~/.codex/sessions/` |

---

## Claude Code — full workflow

### 1. On the source machine: bundle the session

Run from anywhere inside the project:

```bash
# Bundle the most recent session
claude/bundle.sh

# Bundle a specific session
claude/bundle.sh -s <session-id>

# Bundle for a different project path on the target machine
claude/bundle.sh -t /Users/mark/Projects/myproject
```

Output:

```
Bundle:  ~/claude-session-bundles/claude-session-<id>-<timestamp>/
Archive: ~/claude-session-bundles/claude-session-<id>-<timestamp>.tar.gz
Prompt:  ~/claude-session-bundles/claude-session-<id>-<timestamp>/continuation-prompt.md
```

Transfer the `.tar.gz` to the target machine (AirDrop, scp, shared drive, etc.).

### 2. On the target machine: install and resume

```bash
# Extract
tar -xzf ~/Downloads/claude-session-<id>-<timestamp>.tar.gz -C /tmp/

# Install (existing files are skipped by default)
/tmp/claude-session-<id>-<timestamp>/install.sh
```

`install.sh` creates `~/.claude/projects/<encoded-target-path>/` if needed, then installs memory files and the session transcript one file at a time.

#### Duplicate handling flags

| Flag | Behavior |
|------|----------|
| *(none)* | skip files that already exist at the destination; report which were skipped |
| `--replace` | overwrite existing files without prompting |
| `--interactive` | prompt before overwriting each existing file |
| `--print-prompt` | print the continuation prompt after installing |

Flags can be combined:

```bash
/tmp/claude-session-<id>-<timestamp>/install.sh --replace --print-prompt
```

Then open Claude Code in the target project directory, start a new session, and paste `continuation-prompt.md`.

### Global config (optional)

`settings.json` and `CLAUDE.md` are included in the bundle but not installed automatically — they are machine-specific. Copy them manually only if the target machine lacks its own:

```bash
# cp /tmp/claude-session-<id>-<timestamp>/claude/settings.json ~/.claude/
# cp /tmp/claude-session-<id>-<timestamp>/claude/CLAUDE.md ~/.claude/
```

---

## Codex CLI — full workflow

### 1. On the source machine: bundle the session

Run from anywhere inside the project:

```bash
# Bundle a specific session
codex/bundle.sh <session-id>

# Bundle for a different project path on the target machine
codex/bundle.sh -t /Users/mark/Projects/myproject <session-id>
```

Output:

```
Bundle directory: <repo>/target/session-bundles/codex-session-<id>-<timestamp>/
Archive: <repo>/target/session-bundles/codex-session-<id>-<timestamp>.tar.gz
Prompt: <repo>/target/session-bundles/codex-session-<id>-<timestamp>/continuation-prompt.md
```

The Codex bundle preserves paths relative to `$CODEX_HOME`, so session files keep their native shape:

```
codex/
  sessions/YYYY/MM/DD/rollout-<timestamp>-<session-id>.jsonl
  shell_snapshots/<session-id>.<timestamp>.sh
  history-lines.jsonl
  session-index-lines.jsonl
```

Transfer the `.tar.gz` to the target machine.

### 2. On the target machine: install and resume

```bash
# Extract
tar -xzf ~/Downloads/codex-session-<id>-<timestamp>.tar.gz -C /tmp/

# Install (existing files are skipped by default)
/tmp/codex-session-<id>-<timestamp>/install.sh
```

`install.sh` restores session rollouts and shell snapshots into `$CODEX_HOME` one file at a time. It does not modify `history.jsonl` or `session_index.jsonl`; matching lines are bundled for manual review.

#### Duplicate handling flags

| Flag | Behavior |
|------|----------|
| *(none)* | skip files that already exist at the destination; report which were skipped |
| `--replace` | overwrite existing files without prompting |
| `--interactive` | prompt before overwriting each existing file |
| `--print-prompt` | print the continuation prompt after installing |

Flags can be combined:

```bash
/tmp/codex-session-<id>-<timestamp>/install.sh --replace --print-prompt
```

Then open Codex in the target project directory, start a new session, and paste `continuation-prompt.md`.

---

## What's in a bundle

### Claude Code

```
claude-session-<id>-<timestamp>/
  install.sh               # run this on the target machine to restore the session
  continuation-prompt.md   # paste this into a new Claude Code session to restore context
  MANIFEST.md              # full install notes and path details
  claude/
    <session-id>.jsonl     # full session transcript
    memory/                # project memory files
    settings.json          # global Claude settings (if present)
    CLAUDE.md              # global Claude instructions (if present)
  repo/
    git-state.txt          # branch, HEAD, log, status, worktrees at bundle time
```

### Codex CLI

```
codex-session-<id>-<timestamp>/
  install.sh               # run this on the target machine to restore the session
  continuation-prompt.md   # paste this into a new Codex session to restore context
  MANIFEST.md              # full install notes and path details
  codex/
    sessions/              # Codex-relative session rollout paths
    shell_snapshots/       # Codex shell snapshot files
    history-lines.jsonl    # matching history lines, not installed automatically
    session-index-lines.jsonl
  repo/
    git-status.txt
    git-refs.txt
    git-worktrees.txt
    repo.diff
```

---

## Path encoding

Claude Code derives its project directory name from the project root path by replacing every non-alphanumeric character with `-`. The `-t` flag tells the bundle script which path the project will live at on the target machine so `install.sh` and `MANIFEST.md` use the correct encoded name.

Example: `/Users/mark/Projects/codex` → `-Users-mark-Projects-codex`

If the project lives at the same path on both machines, omit `-t`.
