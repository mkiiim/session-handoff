#!/usr/bin/env python3
"""Create a Codex session migration bundle from checked rows in an inventory CSV."""

from __future__ import annotations

import argparse
import csv
import json
import os
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
        default=str(Path.home() / "codex-session-migration-bundles"),
        help="Directory for the archive, sibling installer, and manifest",
    )
    parser.add_argument(
        "--codex-home",
        default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
        help="Codex home directory",
    )
    parser.add_argument("--name", help="Bundle name; default is codex-session-migration-<utc timestamp>")
    return parser.parse_args()


def checked(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_VALUES


def codex_relpath(path: Path, codex_home: Path) -> Path:
    try:
        return path.relative_to(codex_home)
    except ValueError as exc:
        raise SystemExit(f"Refusing to bundle path outside CODEX_HOME: {path}") from exc


def copy_codex_file(src: Path, codex_home: Path, bundle_root: Path) -> str:
    if not src.exists():
        raise SystemExit(f"Selected file does not exist: {src}")
    rel = codex_relpath(src, codex_home)
    dest = bundle_root / "codex" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return str(rel)


def rewrite_string(value: str, source_path: str, target_path: str) -> str:
    return value.replace(source_path, target_path) if source_path and target_path else value


def rewrite_paths(value: object, source_path: str, target_path: str) -> object:
    if isinstance(value, str):
        return rewrite_string(value, source_path, target_path)
    if isinstance(value, list):
        return [rewrite_paths(item, source_path, target_path) for item in value]
    if isinstance(value, dict):
        return {key: rewrite_paths(item, source_path, target_path) for key, item in value.items()}
    return value


def rewrite_rollout_paths(path: Path, source_path: str, target_path: str, source_stat: os.stat_result) -> bool:
    if not source_path or not target_path or source_path == target_path:
        return False

    changed = False
    rewritten: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            raw = line.rstrip("\n")
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                rewritten.append(line)
                continue
            updated = rewrite_paths(record, source_path, target_path)
            if updated != record:
                changed = True
            rewritten.append(json.dumps(updated, separators=(",", ":"), ensure_ascii=False) + "\n")

    if changed:
        path.write_text("".join(rewritten), encoding="utf-8")
        os.utime(path, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
    return changed


def matching_state_lines(path: Path, session_ids: set[str], key: str) -> list[str]:
    if not path.exists():
        return []
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get(key) in session_ids:
                lines.append(line if line.endswith("\n") else line + "\n")
    return lines


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def installer_text(bundle_name: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

BUNDLE_NAME="{bundle_name}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARCHIVE="$SCRIPT_DIR/$BUNDLE_NAME.tar.gz"
CODEX_HOME="${{CODEX_HOME:-$HOME/.codex}}"
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
        cp -p "$src" "$dest"
        printf '  replaced:       %s\\n' "$dest" ;;
      interactive)
        if [[ ! -t 0 ]]; then
          printf '  skip (no tty):  %s\\n' "$dest"
          return
        fi
        printf '  overwrite %s? [y/N] ' "$dest"
        read -r answer || answer=""
        if [[ "${{answer:-}}" =~ ^[Yy]$ ]]; then
          cp -p "$src" "$dest"
          printf '  replaced:       %s\\n' "$dest"
        else
          printf '  skipped:        %s\\n' "$dest"
        fi ;;
    esac
  else
    cp -p "$src" "$dest"
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

tmpdir="$(mktemp -d "${{TMPDIR:-/tmp}}/codex-session-migration.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

tar -xzf "$ARCHIVE" -C "$tmpdir"
bundle_dir="$tmpdir/$BUNDLE_NAME"

printf 'Installing Codex session migration...\\n'
printf '  archive:    %s\\n' "$ARCHIVE"
printf '  codex_home: %s\\n' "$CODEX_HOME"
printf '  mode:       %s\\n\\n' "$mode"

mkdir -p "$CODEX_HOME"
install_tree "Session rollouts" "$bundle_dir/codex/sessions" "$CODEX_HOME/sessions"
install_tree "Archived sessions" "$bundle_dir/codex/archived_sessions" "$CODEX_HOME/archived_sessions"
install_tree "Shell snapshots" "$bundle_dir/codex/shell_snapshots" "$CODEX_HOME/shell_snapshots"

printf '\\nState files were bundled for review but not installed automatically.\\n'
printf 'Inspect these paths inside the archive if needed:\\n'
printf '  codex/history-lines.jsonl\\n'
printf '  codex/session-index-lines.jsonl\\n'
printf '  manifest.csv\\n'

printf '\\nDone. Clone or sync the related repos separately, then run Codex from the target repo and use /resume.\\n'
"""


def read_inventory(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit("Inventory CSV has no header row")
        if "migrate" not in reader.fieldnames:
            raise SystemExit("Inventory CSV must include a migrate column")
        if "session_id" not in reader.fieldnames or "rollout_path" not in reader.fieldnames:
            raise SystemExit("Inventory CSV must include session_id and rollout_path columns")
        return [dict(row) for row in reader]


def main() -> int:
    args = parse_args()
    codex_home = Path(args.codex_home).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    inventory_path = Path(args.inventory).expanduser()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_name = args.name or f"codex-session-migration-{timestamp}"

    rows = read_inventory(inventory_path)
    selected = [row for row in rows if checked(row.get("migrate"))]
    if not selected:
        raise SystemExit("No rows selected. Mark migrate=yes for at least one session.")

    seen: set[str] = set()
    unique_rows: list[dict[str, str]] = []
    for row in selected:
        session_id = (row.get("session_id") or "").strip()
        if not session_id:
            raise SystemExit("Selected row is missing session_id")
        if session_id in seen:
            continue
        seen.add(session_id)
        unique_rows.append(row)

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex-session-migration.") as tmp:
        bundle_root = Path(tmp) / bundle_name
        (bundle_root / "codex").mkdir(parents=True)
        selected_session_ids = {row["session_id"].strip() for row in unique_rows}

        manifest_rows: list[dict[str, str]] = []
        for row in unique_rows:
            rollout_path = Path(row["rollout_path"]).expanduser()
            rollout_stat = rollout_path.stat()
            rollout_rel = copy_codex_file(rollout_path, codex_home, bundle_root)
            source_cwd = (row.get("recorded_cwd") or "").strip()
            target_cwd = (row.get("target_repo_root") or "").strip() or source_cwd
            path_rewritten = rewrite_rollout_paths(
                bundle_root / "codex" / rollout_rel,
                source_cwd,
                target_cwd,
                rollout_stat,
            )
            shell_relpaths: list[str] = []
            for raw_path in (row.get("shell_snapshot_paths") or "").split("|"):
                raw_path = raw_path.strip()
                if not raw_path:
                    continue
                shell_relpaths.append(copy_codex_file(Path(raw_path).expanduser(), codex_home, bundle_root))
            manifest_row = dict(row)
            manifest_row["target_cwd"] = target_cwd
            manifest_row["path_rewritten"] = str(path_rewritten).lower()
            manifest_row["bundle_rollout_relpath"] = rollout_rel
            manifest_row["bundle_shell_snapshot_relpaths"] = "|".join(shell_relpaths)
            manifest_rows.append(manifest_row)

        history_lines = matching_state_lines(codex_home / "history.jsonl", selected_session_ids, "session_id")
        session_index_lines = matching_state_lines(codex_home / "session_index.jsonl", selected_session_ids, "id")
        write_text(bundle_root / "codex" / "history-lines.jsonl", "".join(history_lines))
        write_text(bundle_root / "codex" / "session-index-lines.jsonl", "".join(session_index_lines))

        manifest_fields = list(manifest_rows[0].keys()) if manifest_rows else []
        for extra in ["target_cwd", "path_rewritten", "bundle_rollout_relpath", "bundle_shell_snapshot_relpaths"]:
            if extra not in manifest_fields:
                manifest_fields.append(extra)
        manifest_path = bundle_root / "manifest.csv"
        with manifest_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=manifest_fields)
            writer.writeheader()
            writer.writerows(manifest_rows)

        readme = f"""# Codex Session Migration Bundle

Bundle: `{bundle_name}`

Selected sessions: `{len(unique_rows)}`

Run the sibling installer on the target machine:

```bash
./{bundle_name}.install.sh
```

The installer restores Codex session rollout files, archived session rollout
files, and shell snapshots into `$CODEX_HOME`. It does not modify
`history.jsonl` or `session_index.jsonl`.

Repos are not included. Clone, pull, or rsync the related repos separately.
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
    print(f"Archive: {archive_path}")
    print(f"Installer: {installer_path}")
    print(f"Manifest: {sibling_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
