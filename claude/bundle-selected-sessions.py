#!/usr/bin/env python3
"""Create a Claude Code session migration bundle from checked rows in an inventory CSV."""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "y", "x", "checked", "migrate"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--inventory", required=True, help="Inventory CSV with migrate column")
    parser.add_argument(
        "-o",
        "--output-dir",
        default=str(Path.home() / "claude-session-migration-bundles"),
        help="Directory for the archive, sibling installer, and manifest",
    )
    parser.add_argument(
        "--claude-home",
        default=str(Path.home() / ".claude"),
        help="Claude home directory",
    )
    parser.add_argument("--name", help="Bundle name; default is claude-session-migration-<utc timestamp>")
    return parser.parse_args()


def checked(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_VALUES


def encode_path(path: str) -> str:
    """Encode a filesystem path to a Claude project directory name."""
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def installer_text(bundle_name: str) -> str:
    return f"""\
#!/usr/bin/env bash
set -euo pipefail

BUNDLE_NAME="{bundle_name}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARCHIVE="$SCRIPT_DIR/$BUNDLE_NAME.tar.gz"
CLAUDE_HOME="${{CLAUDE_HOME:-$HOME/.claude}}"
mode="skip"

usage() {{
  cat <<'USAGE'
Usage: ./<bundle>.install.sh [OPTIONS] [ARCHIVE]

Options:
  --replace       overwrite existing files
  --interactive   prompt before overwriting existing files
  -h, --help      show this help

If ARCHIVE is omitted, the installer looks for a sibling .tar.gz with the
same bundle name.
USAGE
}}

archive_arg=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --replace) mode="replace"; shift ;;
    --interactive) mode="interactive"; shift ;;
    -h|--help) usage; exit 0 ;;
    -*)
      printf 'unknown option: %s\\n' "$1" >&2
      usage >&2
      exit 2 ;;
    *)
      if [[ -n "$archive_arg" ]]; then
        printf 'unexpected argument: %s\\n' "$1" >&2
        usage >&2
        exit 2
      fi
      archive_arg="$1"
      shift ;;
  esac
done

if [[ -n "$archive_arg" ]]; then
  ARCHIVE="$archive_arg"
fi

if [[ ! -f "$ARCHIVE" ]]; then
  printf 'archive not found: %s\\n' "$ARCHIVE" >&2
  exit 1
fi

install_file() {{
  local src="$1" dest="$2"
  if [[ -e "$dest" ]]; then
    case "$mode" in
      skip)
        printf '  skip (exists):  %s\\n' "$dest"
        return ;;
      replace)
        cp "$src" "$dest"
        printf '  replaced:       %s\\n' "$dest" ;;
      interactive)
        if [[ ! -t 0 ]]; then
          printf '  skip (no tty):  %s\\n' "$dest"
          return
        fi
        printf '  overwrite %s? [y/N] ' "$dest"
        read -r answer || answer=""
        if [[ "${{answer:-}}" =~ ^[Yy]$ ]]; then
          cp "$src" "$dest"
          printf '  replaced:       %s\\n' "$dest"
        else
          printf '  skipped:        %s\\n' "$dest"
        fi ;;
    esac
  else
    cp "$src" "$dest"
    printf '  installed:      %s\\n' "$dest"
  fi
}}

install_tree() {{
  local name="$1" src_root="$2" dest_root="$3"
  if [[ ! -d "$src_root" ]]; then
    return
  fi
  printf '%s:\\n' "$name"
  mkdir -p "$dest_root"
  local file_list
  file_list="$tmpdir/${{name//[^A-Za-z0-9]/_}}.files"
  find "$src_root" -type f -print0 > "$file_list"
  while IFS= read -r -d '' src <&3; do
    rel="${{src#$src_root/}}"
    dest="$dest_root/$rel"
    mkdir -p "$(dirname "$dest")"
    install_file "$src" "$dest"
  done 3< "$file_list"
}}

tmpdir="$(mktemp -d "${{TMPDIR:-/tmp}}/claude-session-migration.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

tar -xzf "$ARCHIVE" -C "$tmpdir"
bundle_dir="$tmpdir/$BUNDLE_NAME"

printf 'Installing Claude session migration...\\n'
printf '  archive:     %s\\n' "$ARCHIVE"
printf '  claude_home: %s\\n' "$CLAUDE_HOME"
printf '  mode:        %s\\n\\n' "$mode"

mkdir -p "$CLAUDE_HOME/projects"

projects_dir="$bundle_dir/claude/projects"
if [[ -d "$projects_dir" ]]; then
  for project_dir in "$projects_dir"/*/; do
    [[ -d "$project_dir" ]] || continue
    project_dir="${{project_dir%/}}"
    encoded="$(basename "$project_dir")"
    dest_project="$CLAUDE_HOME/projects/$encoded"
    mkdir -p "$dest_project"
    install_tree "$encoded" "$project_dir" "$dest_project"
  done
fi

printf '\\nGlobal config was bundled for review but not installed automatically.\\n'
printf 'Inspect these paths inside the archive if needed:\\n'
printf '  config/settings.json\\n'
printf '  config/CLAUDE.md\\n'

printf '\\nDone. Clone or sync the related repos separately,\\n'
printf 'then open Claude Code in each target project and use /resume.\\n'
"""


def read_inventory(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise SystemExit("Inventory CSV has no header row")
        missing = {"migrate", "session_id", "transcript_path"} - set(reader.fieldnames)
        if missing:
            raise SystemExit(f"Inventory CSV missing required columns: {', '.join(sorted(missing))}")
        return [dict(row) for row in reader]


def main() -> int:
    args = parse_args()
    claude_home = Path(args.claude_home).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    inventory_path = Path(args.inventory).expanduser()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_name = args.name or f"claude-session-migration-{timestamp}"

    rows = read_inventory(inventory_path)
    selected = [row for row in rows if checked(row.get("migrate"))]
    if not selected:
        raise SystemExit("No rows selected. Mark migrate=yes for at least one session.")

    # Dedup by session_id, preserve order
    seen: set[str] = set()
    unique_rows: list[dict[str, str]] = []
    for row in selected:
        session_id = (row.get("session_id") or "").strip()
        if not session_id:
            raise SystemExit("A selected row is missing session_id")
        if session_id not in seen:
            seen.add(session_id)
            unique_rows.append(row)

    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="claude-session-migration.") as tmp:
        bundle_root = Path(tmp) / bundle_name
        (bundle_root / "claude" / "projects").mkdir(parents=True)

        copied_memory: set[str] = set()
        manifest_rows: list[dict[str, str]] = []

        for row in unique_rows:
            session_id = row["session_id"].strip()
            transcript_path = Path(row["transcript_path"]).expanduser()

            if not transcript_path.exists():
                raise SystemExit(f"Transcript not found: {transcript_path}")

            # Determine encoded target project directory
            target_root = (row.get("target_project_root") or row.get("project_root") or "").strip()
            if target_root:
                target_encoded = encode_path(target_root)
            else:
                target_encoded = (row.get("claude_project_dir") or "").strip()
            if not target_encoded:
                raise SystemExit(f"Cannot determine target path for session {session_id}")

            dest_project = bundle_root / "claude" / "projects" / target_encoded
            dest_project.mkdir(parents=True, exist_ok=True)

            shutil.copy2(transcript_path, dest_project / f"{session_id}.jsonl")
            transcript_rel = f"claude/projects/{target_encoded}/{session_id}.jsonl"

            # Memory is per-project; copy only once per unique memory_dir
            memory_dir_str = (row.get("memory_dir") or "").strip()
            memory_rel = ""
            if memory_dir_str and memory_dir_str not in copied_memory:
                memory_src = Path(memory_dir_str).expanduser()
                if memory_src.is_dir():
                    shutil.copytree(memory_src, dest_project / "memory", dirs_exist_ok=True)
                    memory_rel = f"claude/projects/{target_encoded}/memory"
                    copied_memory.add(memory_dir_str)

            manifest_row = dict(row)
            manifest_row["bundle_transcript_relpath"] = transcript_rel
            manifest_row["bundle_memory_relpath"] = memory_rel
            manifest_row["bundle_target_encoded"] = target_encoded
            manifest_rows.append(manifest_row)

        # Global config — bundled for review, not auto-installed
        config_dest = bundle_root / "config"
        for fname in ("settings.json", "CLAUDE.md"):
            src = claude_home / fname
            if src.exists():
                config_dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, config_dest / fname)

        # Manifest inside archive
        manifest_fields = list(manifest_rows[0].keys()) if manifest_rows else []
        for extra in ("bundle_transcript_relpath", "bundle_memory_relpath", "bundle_target_encoded"):
            if extra not in manifest_fields:
                manifest_fields.append(extra)
        manifest_path = bundle_root / "manifest.csv"
        with manifest_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=manifest_fields)
            writer.writeheader()
            writer.writerows(manifest_rows)

        readme = f"""# Claude Code Session Migration Bundle

Bundle: `{bundle_name}`

Selected sessions: `{len(unique_rows)}`

Run the sibling installer on the target machine:

```bash
./{bundle_name}.install.sh
```

The installer restores session transcripts and project memory files into
`$CLAUDE_HOME/projects/`. Global config (`config/`) is included for review
only and is not installed automatically.

Repos are not included. Clone, pull, or rsync the related repos separately,
then open Claude Code in each target project and use `/resume`.
"""
        write_text(bundle_root / "README.md", readme)

        archive_path = output_dir / f"{bundle_name}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(bundle_root, arcname=bundle_name)

        sibling_manifest = output_dir / f"{bundle_name}.manifest.csv"
        shutil.copy2(manifest_path, sibling_manifest)

    installer_path = output_dir / f"{bundle_name}.install.sh"
    write_text(installer_path, installer_text(bundle_name))
    installer_path.chmod(0o755)

    print(f"Selected sessions: {len(unique_rows)}")
    print(f"Archive:   {archive_path}")
    print(f"Installer: {installer_path}")
    print(f"Manifest:  {sibling_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
