#!/usr/bin/env bash
set -euo pipefail

# bundle-claude-session.sh — bundle a Claude Code session for transfer to another machine

usage() {
  cat <<'USAGE'
Usage: bundle-claude-session.sh [OPTIONS]

Bundle a Claude Code session and its project memory for transfer to another
machine. Produces a tar.gz archive and a ready-to-paste continuation prompt.

Options:
  -s, --session-id <id>     Session ID to bundle (default: most recent for project)
  -t, --target-path <path>  Project root on the target machine
                            (default: same path as source)
  -o, --output-dir <dir>    Output directory (default: ~/claude-session-bundles)
  -h, --help                Show this help

Environment:
  CLAUDE_HOME   Claude home directory (default: ~/.claude)

Examples:
  # Bundle most recent session; same path on target machine
  scripts/bundle-claude-session.sh

  # Bundle specific session for a different project path on target
  scripts/bundle-claude-session.sh \
    -s 83c799e2-9562-4c5c-a086-6ce9fea9bd6b \
    -t /Users/mark/Projects/codex
USAGE
}

# --- argument parsing ---

session_id=""
target_path=""
output_dir="${HOME}/claude-session-bundles"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--session-id)  session_id="$2";  shift 2 ;;
    -t|--target-path) target_path="$2"; shift 2 ;;
    -o|--output-dir)  output_dir="$2";  shift 2 ;;
    -h|--help)        usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

# --- resolve paths ---

claude_home="${CLAUDE_HOME:-$HOME/.claude}"
projects_dir="$claude_home/projects"

if [[ ! -d "$claude_home" ]]; then
  printf 'Claude home not found: %s\n' "$claude_home" >&2
  exit 1
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# Encode a filesystem path to the Claude project directory name:
# every character that is not alphanumeric is replaced with -.
encode_path() {
  printf '%s' "$1" | sed 's|[^a-zA-Z0-9]|-|g'
}

source_encoded="$(encode_path "$repo_root")"
source_project_dir="$projects_dir/$source_encoded"

if [[ ! -d "$source_project_dir" ]]; then
  printf 'No Claude project directory found for repo root: %s\n' "$repo_root" >&2
  printf 'Expected: %s\n' "$source_project_dir" >&2
  exit 1
fi

# --- find session JSONL ---

if [[ -n "$session_id" ]]; then
  session_file="$source_project_dir/${session_id}.jsonl"
  if [[ ! -f "$session_file" ]]; then
    printf 'Session not found: %s\n' "$session_file" >&2
    exit 1
  fi
else
  session_file="$(find "$source_project_dir" -maxdepth 1 -name '*.jsonl' -print0 \
    | xargs -0 ls -t 2>/dev/null | head -1)"
  if [[ -z "$session_file" ]]; then
    printf 'No session files found in: %s\n' "$source_project_dir" >&2
    exit 1
  fi
  session_id="$(basename "$session_file" .jsonl)"
fi

# --- target path encoding ---

if [[ -n "$target_path" ]]; then
  target_encoded="$(encode_path "$target_path")"
else
  target_encoded="$source_encoded"
  target_path="$repo_root"
fi

# --- create bundle directory ---

timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
bundle_name="claude-session-${session_id}-${timestamp}"
bundle_dir="${output_dir}/${bundle_name}"
mkdir -p "$bundle_dir/claude" "$bundle_dir/repo"

# --- copy session JSONL ---

cp "$session_file" "$bundle_dir/claude/"

# --- copy memory directory ---

if [[ -d "$source_project_dir/memory" ]]; then
  cp -r "$source_project_dir/memory" "$bundle_dir/claude/"
fi

# --- copy global Claude config files ---

for f in settings.json CLAUDE.md; do
  [[ -f "$claude_home/$f" ]] && cp "$claude_home/$f" "$bundle_dir/claude/"
done

# --- repo state snapshot ---

{
  printf '$ git branch --show-current\n'
  git -C "$repo_root" branch --show-current 2>&1 || true
  printf '\n$ git rev-parse HEAD\n'
  git -C "$repo_root" rev-parse HEAD 2>&1 || true
  printf '\n$ git log --oneline -10\n'
  git -C "$repo_root" log --oneline -10 2>&1 || true
  printf '\n$ git status --short\n'
  git -C "$repo_root" status --short 2>&1 || true
  printf '\n$ git worktree list\n'
  git -C "$repo_root" worktree list 2>&1 || true
} > "$bundle_dir/repo/git-state.txt"

# --- generate continuation prompt ---

python3 - \
  "$session_file" \
  "$bundle_dir/claude/memory/MEMORY.md" \
  "$bundle_dir/repo/git-state.txt" \
  "$target_path" \
  "$bundle_dir/continuation-prompt.md" \
  <<'PYEOF'
import json, sys, os
from datetime import datetime, timezone

session_file      = sys.argv[1]
memory_index_path = sys.argv[2] if len(sys.argv) > 2 else ""
git_state_path    = sys.argv[3] if len(sys.argv) > 3 else ""
target_path       = sys.argv[4] if len(sys.argv) > 4 else ""
out_path          = sys.argv[5] if len(sys.argv) > 5 else ""

MAX_CONTENT_CHARS = 800


def extract_text(content):
    """Return plain text from a message content field (string or array)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if item.get("type") == "text"]
        return " ".join(parts).strip()
    return ""


records = []
with open(session_file) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass

# Session metadata
ai_title   = next((r.get("aiTitle", "") for r in reversed(records) if r.get("type") == "ai-title"), "")
last_prompt_rec = next((r for r in reversed(records) if r.get("type") == "last-prompt"), None)
last_prompt = last_prompt_rec.get("lastPrompt", "") if last_prompt_rec else ""
session_id = records[0].get("sessionId", "") if records else ""
slug       = next((r.get("slug", "") for r in reversed(records) if r.get("slug")), "")
last_branch = next((r.get("gitBranch", "") for r in reversed(records) if r.get("gitBranch")), "")

# Everything after the last compact_boundary is recent uncompacted context
last_compact_idx = -1
for i, r in enumerate(records):
    if r.get("type") == "system" and r.get("subtype") == "compact_boundary":
        last_compact_idx = i

recent_records = records[last_compact_idx + 1:] if last_compact_idx >= 0 else records

# Extract user+assistant exchanges (skip tool results and thinking blocks)
exchanges = []
for r in recent_records:
    role = r.get("type")
    if role not in ("user", "assistant"):
        continue
    content = extract_text(r.get("message", {}).get("content", ""))
    if not content:
        continue
    exchanges.append((role, content))

compaction_note = (
    f"*(context after last compaction boundary — {len(exchanges)} exchanges)*"
    if last_compact_idx >= 0
    else f"*(full session — {len(exchanges)} exchanges)*"
)

# Read memory index
memory_content = ""
if memory_index_path and os.path.exists(memory_index_path):
    with open(memory_index_path) as f:
        memory_content = f.read().strip()

# Read git state
git_state = ""
if git_state_path and os.path.exists(git_state_path):
    with open(git_state_path) as f:
        git_state = f.read().strip()


def fmt_exchange(role, content):
    label = "User" if role == "user" else "Claude"
    truncated = content[:MAX_CONTENT_CHARS]
    suffix = f"\n  [... {len(content) - MAX_CONTENT_CHARS} chars truncated]" if len(content) > MAX_CONTENT_CHARS else ""
    indented = "\n  ".join(truncated.split("\n"))
    return f"**{label}:** {indented}{suffix}"


exchanges_text = "\n\n".join(fmt_exchange(r, c) for r, c in exchanges)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

out = f"""# Claude Code Session Continuation

## Session Info

- **Title:** {ai_title}
- **Session ID:** `{session_id}`
- **Slug:** `{slug}`
- **Source project:** `{os.path.dirname(session_file)}`
- **Target project:** `{target_path}`
- **Git branch at bundle time:** `{last_branch}`
- **Bundled at:** {now} UTC

---

## Memory Index

{memory_content if memory_content else "*(no memory index found)*"}

---

## Repo State at Bundle Time

```
{git_state}
```

---

## Recent Session Context

{compaction_note}

{exchanges_text if exchanges_text else "*(no recent exchanges found)*"}

---

## Last User Message

{last_prompt}

---

## How to Resume

Open Claude Code in the target project directory (`{target_path}`).
Start a new session and tell it to read this file:

> Read `<path-to-this-file>/continuation-prompt.md` and continue from there.

Everything above is the context Claude needs — memory index, repo state, recent exchanges, and last prompt.
"""

if out_path:
    with open(out_path, "w") as f:
        f.write(out)
    print(f"Continuation prompt written: {out_path}", file=sys.stderr)
else:
    print(out)
PYEOF

# --- extract session slug for manifest ---

session_slug="$(python3 -c "
import json, sys
with open('$session_file') as f:
    records = [json.loads(l) for l in f if l.strip()]
print(next((r.get('slug','') for r in reversed(records) if r.get('slug','')), ''))
" 2>/dev/null || echo '')"

# --- manifest ---

{
  printf '# Claude Code Session Bundle\n\n'
  printf '## Bundle Info\n\n'
  printf -- '- session_id: `%s`\n' "$session_id"
  printf -- '- slug: `%s`\n' "$session_slug"
  printf -- '- source_project: `%s`\n' "$repo_root"
  printf -- '- target_project: `%s`\n' "$target_path"
  printf -- '- source_encoded: `%s`\n' "$source_encoded"
  printf -- '- target_encoded: `%s`\n' "$target_encoded"
  printf -- '- created_at_utc: `%s`\n\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  printf '## Contents\n\n'
  printf -- '- `claude/%s.jsonl` — session transcript\n' "$session_id"
  printf -- '- `claude/memory/` — project memory files\n'
  printf -- '- `claude/settings.json` — global Claude settings (if present)\n'
  printf -- '- `claude/CLAUDE.md` — global Claude instructions (if present)\n'
  printf -- '- `repo/git-state.txt` — repo state at bundle time\n'
  printf -- '- `continuation-prompt.md` — tell Claude to read this file to restore context\n'
  printf -- '- `install.sh` — automated install script for the target machine\n\n'

  printf '## Install on Target Machine\n\n'
  printf '```bash\n'
  printf '# 1. Extract\n'
  printf 'tar -xzf %s.tar.gz -C /tmp/\n\n' "$bundle_name"
  printf '# 2. Install (existing files are skipped by default)\n'
  printf '/tmp/%s/install.sh\n' "$bundle_name"
  printf '```\n\n'
  printf '### install.sh flags\n\n'
  printf '| Flag | Behavior |\n'
  printf '|------|----------|\n'
  printf '| *(none)* | skip files that already exist at the destination; report them |\n'
  printf '| `--replace` | overwrite existing files without prompting |\n'
  printf '| `--interactive` | prompt before overwriting each existing file |\n\n'
  printf 'The install script copies memory files and the session transcript into\n'
  printf '`~/.claude/projects/%s/`\n\n' "$target_encoded"
  printf 'Then open Claude Code in `%s`, start a new session,\n' "$target_path"
  printf 'and tell it to read the continuation prompt:\n\n'
  printf '```\n'
  printf 'Read /tmp/%s/continuation-prompt.md and continue from there.\n' "$bundle_name"
  printf '```\n\n'
  printf '### Global config\n\n'
  printf 'Settings and CLAUDE.md are included in the bundle but **not** installed\n'
  printf 'automatically — copy them manually only if the target machine lacks its own:\n\n'
  printf '```bash\n'
  printf '# cp /tmp/%s/claude/settings.json ~/.claude/\n' "$bundle_name"
  printf '# cp /tmp/%s/claude/CLAUDE.md ~/.claude/\n'   "$bundle_name"
  printf '```\n'
} > "$bundle_dir/MANIFEST.md"

# --- generate install script ---

cat > "$bundle_dir/install.sh" <<INSTALL_SCRIPT
#!/usr/bin/env bash
set -euo pipefail

# Restore a Claude Code session bundle on the target machine.
# Usage: ./install.sh [--replace] [--interactive]
#
# Default: skip files that already exist at the destination and report them.
# --replace      overwrite existing files without prompting
# --interactive  prompt before overwriting each existing file

BUNDLE_DIR="\$(cd "\$(dirname "\$0")" && pwd)"
CLAUDE_HOME="\${CLAUDE_HOME:-\$HOME/.claude}"
TARGET_PROJECT_DIR="\$CLAUDE_HOME/projects/${target_encoded}"
SESSION_ID="${session_id}"

mode="skip"

for arg in "\$@"; do
  case "\$arg" in
    --replace)      mode="replace" ;;
    --interactive)  mode="interactive" ;;
    *) printf 'unknown option: %s\n' "\$arg" >&2; exit 2 ;;
  esac
done

install_file() {
  local src="\$1" dest="\$2"
  if [[ -e "\$dest" ]]; then
    case "\$mode" in
      skip)
        printf '  skip (exists):  %s\n' "\$dest"
        return ;;
      replace)
        cp "\$src" "\$dest"
        printf '  replaced:       %s\n' "\$dest" ;;
      interactive)
        printf '  overwrite %s? [y/N] ' "\$dest"
        read -r answer
        if [[ "\${answer:-}" =~ ^[Yy]\$ ]]; then
          cp "\$src" "\$dest"
          printf '  replaced:       %s\n' "\$dest"
        else
          printf '  skipped:        %s\n' "\$dest"
        fi ;;
    esac
  else
    cp "\$src" "\$dest"
    printf '  installed:      %s\n' "\$dest"
  fi
}

printf 'Installing Claude session bundle...\n'
printf '  bundle:  %s\n' "\$BUNDLE_DIR"
printf '  target:  %s\n' "\$TARGET_PROJECT_DIR"
printf '  mode:    %s\n' "\$mode"
printf '\n'

mkdir -p "\$TARGET_PROJECT_DIR"

if [[ -d "\$BUNDLE_DIR/claude/memory" ]]; then
  printf 'Memory files:\n'
  mkdir -p "\$TARGET_PROJECT_DIR/memory"
  while IFS= read -r -d '' src; do
    rel="\${src#\$BUNDLE_DIR/claude/memory/}"
    dest="\$TARGET_PROJECT_DIR/memory/\$rel"
    mkdir -p "\$(dirname "\$dest")"
    install_file "\$src" "\$dest"
  done < <(find "\$BUNDLE_DIR/claude/memory" -type f -print0)
fi

printf 'Session transcript:\n'
install_file "\$BUNDLE_DIR/claude/\${SESSION_ID}.jsonl" "\$TARGET_PROJECT_DIR/\${SESSION_ID}.jsonl"

printf '\nDone.\n'
printf 'Open Claude Code in the target project and tell it to read the continuation prompt:\n\n'
printf '  Read "%s/continuation-prompt.md" and continue from there.\n\n' "\$BUNDLE_DIR"
INSTALL_SCRIPT
chmod +x "$bundle_dir/install.sh"

# --- archive ---

archive_path="${output_dir}/${bundle_name}.tar.gz"
tar -C "$output_dir" -czf "$archive_path" "$bundle_name"

printf 'Bundle:  %s\n' "$bundle_dir"
printf 'Archive: %s\n' "$archive_path"
printf 'Prompt:  %s/continuation-prompt.md\n' "$bundle_dir"
