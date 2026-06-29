#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: bundle-codex-session.sh [OPTIONS]

Bundle a Codex CLI session for transfer to another machine. Produces a tar.gz
archive, a target-machine install script, and a ready-to-paste continuation
prompt.

Options:
  -s, --session-id <id>     Session ID to bundle
  -t, --target-path <path>  Project root on the target machine
                            (default: same path as source)
  -o, --output-dir <dir>    Output directory (default: ~/codex-session-bundles)
  -h, --help                Show this help

Environment:
  CODEX_HOME    Defaults to ~/.codex

Examples:
  codex/bundle.sh -s 019dd9be-0baf-7710-92e1-aca6e3d68ea5
  codex/bundle.sh -s 019dd9be-0baf-7710-92e1-aca6e3d68ea5 -t /Users/mark/Projects/myproject
USAGE
}

session_id=""
target_path=""
output_dir="${HOME}/codex-session-bundles"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--session-id)
      if [[ $# -lt 2 ]]; then
        printf 'Missing value for %s\n' "$1" >&2
        usage >&2
        exit 2
      fi
      if [[ -n "$session_id" && "$session_id" != "$2" ]]; then
        printf 'Conflicting session IDs: %s and %s\n' "$session_id" "$2" >&2
        usage >&2
        exit 2
      fi
      session_id="$2"
      shift 2 ;;
    -t|--target-path)
      if [[ $# -lt 2 ]]; then
        printf 'Missing value for %s\n' "$1" >&2
        usage >&2
        exit 2
      fi
      target_path="$2"
      shift 2 ;;
    -o|--output-dir)
      if [[ $# -lt 2 ]]; then
        printf 'Missing value for %s\n' "$1" >&2
        usage >&2
        exit 2
      fi
      output_dir="$2"
      shift 2 ;;
    -h|--help)        usage; exit 0 ;;
    --) shift ;;
    -*)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2 ;;
    *)
      printf 'Unexpected argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      shift ;;
  esac
done

if [[ -z "$session_id" || $# -gt 0 ]]; then
  usage >&2
  exit 2
fi

codex_home="${CODEX_HOME:-$HOME/.codex}"

if [[ ! -d "$codex_home" ]]; then
  printf 'Codex home does not exist: %s\n' "$codex_home" >&2
  exit 1
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if [[ -z "$target_path" ]]; then
  target_path="$repo_root"
fi

timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
bundle_name="codex-session-$session_id-$timestamp"
bundle_dir="${output_dir}/${bundle_name}"
created_at_utc="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

mkdir -p "$bundle_dir/codex" "$bundle_dir/repo"

manifest="$bundle_dir/MANIFEST.md"
history_lines="$bundle_dir/codex/history-lines.jsonl"
session_index_lines="$bundle_dir/codex/session-index-lines.jsonl"
session_list="$bundle_dir/codex/session-files.txt"
shell_list="$bundle_dir/codex/shell-snapshot-files.txt"

session_files=()
while IFS= read -r file; do
  session_files+=("$file")
done < <(find "$codex_home/sessions" -type f -name "*$session_id*.jsonl" -print 2>/dev/null | sort)

shell_snapshot_files=()
while IFS= read -r file; do
  shell_snapshot_files+=("$file")
done < <(find "$codex_home/shell_snapshots" -type f -name "*$session_id*" -print 2>/dev/null | sort)

if [[ ${#session_files[@]} -eq 0 ]]; then
  printf 'No session rollout found for session ID: %s\n' "$session_id" >&2
  printf 'Searched under: %s/sessions\n' "$codex_home" >&2
  exit 1
fi

copy_codex_relative() {
  local src="$1"
  local rel="${src#$codex_home/}"
  local dest="$bundle_dir/codex/$rel"

  mkdir -p "$(dirname "$dest")"
  cp "$src" "$dest"
  printf '%s\n' "$rel"
}

: >"$session_list"
for file in "${session_files[@]}"; do
  copy_codex_relative "$file" >>"$session_list"
done

: >"$shell_list"
for file in "${shell_snapshot_files[@]}"; do
  copy_codex_relative "$file" >>"$shell_list"
done

if [[ -f "$codex_home/history.jsonl" ]]; then
  rg --fixed-strings "\"session_id\":\"$session_id\"" "$codex_home/history.jsonl" >"$history_lines" || true
else
  : >"$history_lines"
fi

if [[ -f "$codex_home/session_index.jsonl" ]]; then
  rg --fixed-strings "\"id\":\"$session_id\"" "$codex_home/session_index.jsonl" >"$session_index_lines" || true
else
  : >"$session_index_lines"
fi

{
  printf '# Codex Session Bundle\n\n'
  printf '## Bundle Info\n\n'
  printf -- '- session_id: `%s`\n' "$session_id"
  printf -- '- created_at_utc: `%s`\n' "$created_at_utc"
  printf -- '- codex_home: `%s`\n' "$codex_home"
  printf -- '- source_project: `%s`\n' "$repo_root"
  printf -- '- target_project: `%s`\n' "$target_path"
  printf -- '- bundle_dir: `%s`\n\n' "$bundle_dir"

  printf '## Included Codex Files\n\n'
  printf '### Session Rollouts\n\n'
  if [[ ${#session_files[@]} -eq 0 ]]; then
    printf 'None found.\n\n'
  else
    sed 's/^/- `codex\//' "$session_list" | sed 's/$/`/'
    printf '\n'
  fi

  printf '### Shell Snapshots\n\n'
  if [[ ${#shell_snapshot_files[@]} -eq 0 ]]; then
    printf 'None found.\n\n'
  else
    sed 's/^/- `codex\//' "$shell_list" | sed 's/$/`/'
    printf '\n'
  fi

  printf '### State Lines\n\n'
  printf -- '- `codex/history-lines.jsonl` — matching history lines, not installed automatically\n'
  printf -- '- `codex/session-index-lines.jsonl` — matching session index lines, not installed automatically\n\n'

  printf '## Install on Target Machine\n\n'
  printf '```bash\n'
  printf '# 1. Extract\n'
  printf 'tar -xzf %s.tar.gz -C /tmp/\n\n' "$(basename "$bundle_dir")"
  printf '# 2. Install (existing files are skipped by default)\n'
  printf '/tmp/%s/install.sh\n' "$(basename "$bundle_dir")"
  printf '```\n\n'
  printf '### install.sh flags\n\n'
  printf '| Flag | Behavior |\n'
  printf '|------|----------|\n'
  printf '| *(none)* | skip files that already exist at the destination; report them |\n'
  printf '| `--replace` | overwrite existing files without prompting |\n'
  printf '| `--interactive` | prompt before overwriting each existing file |\n'
  printf '| `--print-prompt` | print the continuation prompt after installing |\n\n'
  printf 'The install script restores Codex-shaped files into `$CODEX_HOME`:\n\n'
  printf -- '- `codex/sessions/...` -> `$CODEX_HOME/sessions/...`\n'
  printf -- '- `codex/shell_snapshots/...` -> `$CODEX_HOME/shell_snapshots/...`\n\n'
  printf '`history.jsonl` and `session_index.jsonl` are not modified automatically.\n'
  printf 'Review `codex/history-lines.jsonl` and `codex/session-index-lines.jsonl` manually if needed.\n\n'

  printf '## Notes\n\n'
  printf -- '- Session transcripts can contain sensitive prompts, paths, command output, and environment details.\n'
  printf -- '- This bundle captures repo metadata from the directory where the script was run.\n'
  printf -- '- Untracked file contents are not copied automatically unless they are named explicitly below by a known handoff artifact rule.\n'
} >"$manifest"

{
  printf '$ git status --short\n\n'
  git -C "$repo_root" status --short 2>&1 || true
} >"$bundle_dir/repo/git-status.txt"

{
  printf '$ git branch --show-current\n'
  git -C "$repo_root" branch --show-current 2>&1 || true
  printf '\n$ git rev-parse HEAD\n'
  git -C "$repo_root" rev-parse HEAD 2>&1 || true
  printf '\n$ git describe --tags --always --dirty\n'
  git -C "$repo_root" describe --tags --always --dirty 2>&1 || true
  printf '\n$ git remote -v\n'
  git -C "$repo_root" remote -v 2>&1 || true
} >"$bundle_dir/repo/git-refs.txt"

{
  printf '$ git worktree list --porcelain\n\n'
  git -C "$repo_root" worktree list --porcelain 2>&1 || true
} >"$bundle_dir/repo/git-worktrees.txt"

git -C "$repo_root" diff --binary >"$bundle_dir/repo/repo.diff" || true

handoff_doc="$repo_root/docs/line-branching-porting-brief.md"
if [[ -f "$handoff_doc" ]]; then
  cp "$handoff_doc" "$bundle_dir/repo/line-branching-porting-brief.md"
fi

{
  printf '# Codex Session Continuation\n\n'
  printf '## Session Info\n\n'
  printf -- '- **Session ID:** `%s`\n' "$session_id"
  printf -- '- **Source project:** `%s`\n' "$repo_root"
  printf -- '- **Target project:** `%s`\n' "$target_path"
  printf -- '- **Bundled at:** `%s`\n\n' "$created_at_utc"

  printf '## Restored File Shape\n\n'
  printf 'This bundle preserves Codex paths relative to `$CODEX_HOME`.\n\n'
  printf '### Session Rollouts\n\n'
  if [[ -s "$session_list" ]]; then
    sed 's/^/- `$CODEX_HOME\//' "$session_list" | sed 's/$/`/'
  else
    printf 'None found.\n'
  fi
  printf '\n### Shell Snapshots\n\n'
  if [[ -s "$shell_list" ]]; then
    sed 's/^/- `$CODEX_HOME\//' "$shell_list" | sed 's/$/`/'
  else
    printf 'None found.\n'
  fi
  printf '\n## Repo State at Bundle Time\n\n'
  printf '### git status\n\n```\n'
  cat "$bundle_dir/repo/git-status.txt"
  printf '```\n\n'
  printf '### git refs\n\n```\n'
  cat "$bundle_dir/repo/git-refs.txt"
  printf '```\n\n'
  printf '### git worktrees\n\n```\n'
  cat "$bundle_dir/repo/git-worktrees.txt"
  printf '```\n\n'
  if [[ -s "$bundle_dir/repo/repo.diff" ]]; then
    printf '### uncommitted diff\n\n```diff\n'
    cat "$bundle_dir/repo/repo.diff"
    printf '```\n\n'
  fi
  printf '## State Files\n\n'
  printf '`codex/history-lines.jsonl` and `codex/session-index-lines.jsonl` were bundled for review but are not installed automatically.\n\n'
  printf '## How to Resume\n\n'
  printf 'Open Codex in the target project directory (`%s`).\n' "$target_path"
  printf 'Start a new session and tell Codex to read this file:\n\n'
  printf '> Read `<path-to-this-file>/continuation-prompt.md` and continue from there.\n\n'
  printf 'Everything above this section is the context Codex needs — session info, restored files, and repo state.\n'
} >"$bundle_dir/continuation-prompt.md"

cat > "$bundle_dir/install.sh" <<'INSTALL_SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

# Restore a Codex CLI session bundle on the target machine.
# Usage: ./install.sh [--replace] [--interactive]
#
# Default: skip files that already exist at the destination and report them.
# --replace      overwrite existing files without prompting
# --interactive  prompt before overwriting each existing file

BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

mode="skip"

for arg in "$@"; do
  case "$arg" in
    --replace)      mode="replace" ;;
    --interactive)  mode="interactive" ;;
    *) printf 'unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

install_file() {
  local src="$1" dest="$2"
  if [[ -e "$dest" ]]; then
    case "$mode" in
      skip)
        printf '  skip (exists):  %s\n' "$dest"
        return ;;
      replace)
        cp "$src" "$dest"
        printf '  replaced:       %s\n' "$dest" ;;
      interactive)
        printf '  overwrite %s? [y/N] ' "$dest"
        read -r answer
        if [[ "${answer:-}" =~ ^[Yy]$ ]]; then
          cp "$src" "$dest"
          printf '  replaced:       %s\n' "$dest"
        else
          printf '  skipped:        %s\n' "$dest"
        fi ;;
    esac
  else
    cp "$src" "$dest"
    printf '  installed:      %s\n' "$dest"
  fi
}

install_tree() {
  local name="$1" src_root="$2" dest_root="$3"
  if [[ ! -d "$src_root" ]]; then
    return
  fi

  printf '%s:\n' "$name"
  mkdir -p "$dest_root"
  while IFS= read -r -d '' src; do
    rel="${src#$src_root/}"
    dest="$dest_root/$rel"
    mkdir -p "$(dirname "$dest")"
    install_file "$src" "$dest"
  done < <(find "$src_root" -type f -print0)
}

printf 'Installing Codex session bundle...\n'
printf '  bundle:     %s\n' "$BUNDLE_DIR"
printf '  codex_home: %s\n' "$CODEX_HOME"
printf '  mode:       %s\n' "$mode"
printf '\n'

mkdir -p "$CODEX_HOME"

install_tree "Session rollouts" "$BUNDLE_DIR/codex/sessions" "$CODEX_HOME/sessions"
install_tree "Shell snapshots" "$BUNDLE_DIR/codex/shell_snapshots" "$CODEX_HOME/shell_snapshots"

printf '\nState files not installed automatically:\n'
printf '  %s\n' "$BUNDLE_DIR/codex/history-lines.jsonl"
printf '  %s\n' "$BUNDLE_DIR/codex/session-index-lines.jsonl"

printf '\nDone.\n'
printf 'Open Codex in the target project directory and run:\n\n'
printf '  /resume\n\n'
printf 'Select the migrated session. If it does not appear, fall back to the continuation prompt:\n\n'
printf '  Read "%s/continuation-prompt.md" and continue from there.\n\n' "$BUNDLE_DIR"
INSTALL_SCRIPT
chmod +x "$bundle_dir/install.sh"

archive_path="$bundle_dir.tar.gz"
tar -C "$(dirname "$bundle_dir")" -czf "$archive_path" "$(basename "$bundle_dir")"

printf 'Bundle directory: %s\n' "$bundle_dir"
printf 'Archive: %s\n' "$archive_path"
printf 'Prompt: %s/continuation-prompt.md\n' "$bundle_dir"
