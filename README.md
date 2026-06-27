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

Then open Claude Code in the target project directory, start a new session, and tell it to read the continuation prompt:

```
Read /tmp/claude-session-<id>-<timestamp>/continuation-prompt.md and continue from there.
```

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
codex/bundle.sh -s <session-id>

# Bundle for a different project path on the target machine
codex/bundle.sh -s <session-id> -t /Users/mark/Projects/myproject
```

Output:

```
Bundle directory: ~/codex-session-bundles/codex-session-<id>-<timestamp>/
Archive: ~/codex-session-bundles/codex-session-<id>-<timestamp>.tar.gz
Prompt: ~/codex-session-bundles/codex-session-<id>-<timestamp>/continuation-prompt.md
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

Open Codex in the target project directory, start a new session, and tell it to read the continuation prompt:

> Read `/tmp/codex-session-<id>-<timestamp>/continuation-prompt.md` and continue from there.

#### Duplicate handling flags

| Flag | Behavior |
|------|----------|
| *(none)* | skip files that already exist at the destination; report which were skipped |
| `--replace` | overwrite existing files without prompting |
| `--interactive` | prompt before overwriting each existing file |

Open Codex in the target project directory, start a new session, and tell it to read the continuation prompt:

> Read `/tmp/codex-session-<id>-<timestamp>/continuation-prompt.md` and continue from there.

---

## Resume after restore

The bundle includes `continuation-prompt.md`, but a successful native resume is the preferred path. Treat the prompt as a fallback or audit summary when native resume is unavailable, incomplete, or confusing.

### Claude Code

After running `install.sh`, open Claude Code in the target project directory and use `/resume` to pick up the migrated session. If the session appears and loads, Claude has the full conversation transcript available as context — which is more complete than `continuation-prompt.md`, since the prompt truncates and summarizes while the `.jsonl` is the raw record.

**What Claude actually has after a successful resume:**

- The full conversation history from the `.jsonl`, including tool calls and their output
- Everything after the last compaction boundary in full; earlier exchanges as a compaction summary only — the raw turns before that boundary are no longer in context
- Project memory files, if installed, available to any session in the project directory
- Project-level instructions from `CLAUDE.md`, loaded automatically

**What the transcript does not capture — verify before acting:**

- **File state on disk.** Claude may recall editing a file in the prior session, but the target machine may have a different version (different commit, local edits, or the change was never committed). Read files before assuming their content.
- **Git state.** Branch, HEAD, and uncommitted changes are not embedded in the transcript. Run `git status` and `git log` before doing anything that touches the tree.
- **Environment.** Secrets, env vars, credentials, installed tools, and running services that the prior session depended on may not exist on the target machine. Nothing in the transcript confirms they do.
- **Repo diff.** `repo/git-state.txt` in the bundle captures the state at bundle time. If the target checkout is not at the same commit, treat that as a gap to resolve first.

Use `continuation-prompt.md` when `/resume` cannot find the session, the resumed context looks truncated or confusing, or you want an explicit structured handoff summary before continuing work.

### Codex CLI

After running `install.sh`, start Codex in the target project and try `/resume`. If the migrated session appears and resumes, Codex has generally loaded the session rollout and enough session metadata to continue from the transferred transcript.

That does not prove the target machine is safe to modify automatically. Before making risky edits, confirm the target checkout matches the bundled repo state: branch, commit, uncommitted changes, ignored local files, env vars, secrets, database state, and any running services that the prior session depended on.

The most important restored Codex files are:

```
$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<timestamp>-<session-id>.jsonl
$CODEX_HOME/shell_snapshots/<session-id>.<timestamp>.sh
```

`history.jsonl` and `session_index.jsonl` lines are bundled for review but are not installed automatically. If `/resume` works, leave those files alone unless there is a specific discoverability issue to debug.

Use `continuation-prompt.md` when `/resume` cannot find the session, the resumed context looks incomplete, or you want an explicit handoff summary before continuing.

---

## What's in a bundle

### Claude Code

```
claude-session-<id>-<timestamp>/
  install.sh               # run this on the target machine to restore the session
  continuation-prompt.md   # tell Claude to read this file to restore context
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
  continuation-prompt.md   # tell Codex to read this file to restore context
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
