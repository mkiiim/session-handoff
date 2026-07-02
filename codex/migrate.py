#!/usr/bin/env python3
"""Interactive Codex session migration: inventory → pick → bundle → transfer."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path


SESSION_ID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


# ── inventory ────────────────────────────────────────────────────────────────

def iso_from_mtime(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def session_id_from_path(path: Path) -> str:
    match = SESSION_ID_RE.search(path.name)
    return match.group(1) if match else ""


def read_jsonl(path: Path) -> tuple[list[dict], int]:
    records: list[dict] = []
    line_count = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line_count += 1
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records, line_count


def text_from_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") in {"input_text", "text"}
        ]
        return " ".join(p for p in parts if p)
    return ""


def user_text(record: dict) -> str:
    payload = record.get("payload")
    if isinstance(payload, dict):
        if payload.get("role") == "user":
            return text_from_content(payload.get("content"))
    message = record.get("message")
    if isinstance(message, dict) and message.get("role") == "user":
        return text_from_content(message.get("content"))
    return ""


def clean_text(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text[: limit - 3] + "..." if len(text) > limit else text


def resolve_repo(cwd: str) -> tuple[str, bool]:
    if not cwd:
        return "", False
    p = Path(cwd).expanduser()
    if not p.exists():
        return "", False
    try:
        result = subprocess.run(
            ["git", "-C", str(p), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip(), True
    except (OSError, FileNotFoundError):
        pass
    return str(p), True


def history_counts(codex_home: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    history = codex_home / "history.jsonl"
    if not history.exists():
        return counts
    with history.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = record.get("session_id")
            if isinstance(sid, str):
                counts[sid] = counts.get(sid, 0) + 1
    return counts


def rollout_files(codex_home: Path) -> list[Path]:
    files = list((codex_home / "sessions").glob("**/*.jsonl"))
    files.extend((codex_home / "archived_sessions").glob("*.jsonl"))
    return sorted(p for p in files if p.is_file())


def build_inventory(codex_home: Path) -> list[dict]:
    hist = history_counts(codex_home)
    rows: list[dict] = []

    for rollout in rollout_files(codex_home):
        records, line_count = read_jsonl(rollout)
        meta = next(
            (r.get("payload", {}) for r in records
             if r.get("type") == "session_meta" and isinstance(r.get("payload"), dict)),
            {},
        )
        session_id = str(meta.get("id") or session_id_from_path(rollout))
        recorded_cwd = str(meta.get("cwd") or "")
        repo_root, repo_exists = resolve_repo(recorded_cwd)
        repo_name = Path(repo_root).name if repo_root else ""
        texts = [t for t in (user_text(r) for r in records) if t]
        snapshots = sorted((codex_home / "shell_snapshots").glob(f"{session_id}.*"))
        size_bytes = rollout.stat().st_size
        relpath = str(rollout.relative_to(codex_home))
        source_area = relpath.split("/", 1)[0]
        started_at = str(
            meta.get("timestamp")
            or (records[0].get("timestamp") if records else "")
            or iso_from_mtime(rollout)
        )

        rows.append({
            "repo_name": repo_name,
            "repo_root": repo_root,
            "repo_exists": repo_exists,
            "session_id": session_id,
            "started_at": started_at,
            "last_modified": iso_from_mtime(rollout),
            "size_mb": f"{size_bytes / 1024 / 1024:.2f}",
            "line_count": line_count,
            "recorded_cwd": recorded_cwd,
            "rollout_path": str(rollout),
            "rollout_relpath": relpath,
            "source_area": source_area,
            "shell_snapshot_paths": "|".join(str(p) for p in snapshots),
            "history_count": hist.get(session_id, 0),
            "first_user_text": clean_text(texts[0], 120) if texts else "",
            "last_user_text": clean_text(texts[-1], 120) if texts else "",
        })

    rows.sort(key=lambda r: (r["repo_name"], r["started_at"], r["session_id"]))
    return rows


# ── picker ───────────────────────────────────────────────────────────────────

def pick_sessions(rows: list[dict]) -> list[dict]:
    if not shutil.which("fzf"):
        raise SystemExit("fzf is required but not found in PATH. Install it with: brew install fzf")
    lines = []
    for i, row in enumerate(rows):
        path = row["repo_root"] or row["recorded_cwd"] or "?"
        path = ("…" + path[-31:]) if len(path) > 32 else path
        flag = "!" if not row["repo_exists"] else " "
        area = row["source_area"][:8]
        date = row["last_modified"][:10]
        size = f"{float(row['size_mb']):5.2f}MB"
        title = (row["first_user_text"] or "")[:55]
        lines.append(f"{i:04d}\t{path:<32}  {flag}  {area:<8}  {date}  {size}  {title}")

    col_header = f"XXXX\t{'PATH':<32}  !  {'AREA':<8}  {'DATE':<10}  {'SIZE':>7}  TITLE"

    result = subprocess.run(
        [
            "fzf",
            "--multi",
            "--with-nth=2..",
            "--delimiter=\t",
            "--header-lines=1",
            "--header=Tab·select  Enter·confirm  Esc·cancel",
            "--prompt=Sessions > ",
            "--height=80%",
            "--layout=reverse",
            "--info=inline",
            "--no-hscroll",
        ],
        input="\n".join([col_header] + lines),
        stdout=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        return []

    selected = []
    for line in result.stdout.strip().splitlines():
        if line:
            selected.append(rows[int(line.split("\t")[0])])
    return selected


# ── bundler ──────────────────────────────────────────────────────────────────

def copy_file(src: Path, codex_home: Path, bundle_root: Path) -> str:
    if not src.exists():
        raise SystemExit(f"File not found: {src}")
    try:
        rel = src.relative_to(codex_home)
    except ValueError:
        raise SystemExit(f"File is outside CODEX_HOME ({codex_home}): {src}")
    dest = bundle_root / "codex" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return str(rel)


def matching_state_lines(path: Path, session_ids: set[str], key: str) -> list[str]:
    if not path.exists():
        return []
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get(key) in session_ids:
                lines.append(line if line.endswith("\n") else line + "\n")
    return lines


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

printf '\\nDone. Clone or sync the related repos separately,\\n'
printf 'then run Codex from the target repo and use /resume.\\n'
"""


MANIFEST_FIELDS = [
    "session_id", "repo_name", "repo_root", "repo_exists", "recorded_cwd",
    "source_area", "started_at", "last_modified", "size_mb", "line_count",
    "history_count", "first_user_text", "last_user_text",
    "bundle_rollout_relpath", "bundle_shell_snapshot_relpaths",
]


def build_bundle(selected: list[dict], codex_home: Path, bundle_name: str, tmp: Path) -> tuple[Path, Path, Path]:
    bundle_root = tmp / bundle_name
    (bundle_root / "codex").mkdir(parents=True)

    session_ids = {row["session_id"] for row in selected}
    manifest_rows: list[dict] = []

    for row in selected:
        rollout = Path(row["rollout_path"])
        rollout_rel = copy_file(rollout, codex_home, bundle_root)
        snapshot_rels: list[str] = []
        for raw in (row.get("shell_snapshot_paths") or "").split("|"):
            raw = raw.strip()
            if raw:
                snapshot_rels.append(copy_file(Path(raw), codex_home, bundle_root))
        manifest_rows.append({
            **{k: row.get(k, "") for k in MANIFEST_FIELDS if k not in ("bundle_rollout_relpath", "bundle_shell_snapshot_relpaths")},
            "bundle_rollout_relpath": rollout_rel,
            "bundle_shell_snapshot_relpaths": "|".join(snapshot_rels),
        })

    history_lines = matching_state_lines(codex_home / "history.jsonl", session_ids, "session_id")
    index_lines = matching_state_lines(codex_home / "session_index.jsonl", session_ids, "id")
    (bundle_root / "codex" / "history-lines.jsonl").write_text("".join(history_lines), encoding="utf-8")
    (bundle_root / "codex" / "session-index-lines.jsonl").write_text("".join(index_lines), encoding="utf-8")

    manifest_path = bundle_root / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(manifest_rows)

    archive_path = tmp / f"{bundle_name}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(bundle_root, arcname=bundle_name)

    installer_path = tmp / f"{bundle_name}.install.sh"
    installer_path.write_text(installer_text(bundle_name), encoding="utf-8")
    installer_path.chmod(0o755)

    return archive_path, installer_path, manifest_path


# ── main ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "host",
        nargs="?",
        help="SSH target (user@host). If omitted, bundle is saved locally.",
    )
    parser.add_argument(
        "--codex-home",
        default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
        help="Codex home directory (default: $CODEX_HOME or ~/.codex)",
    )
    parser.add_argument(
        "--target-dir",
        default="~/Downloads",
        help="Directory on target host to receive the bundle (default: ~/Downloads)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / "Downloads" / "codex-session-migration-bundles"),
        help="Local output directory when no host is given (default: ~/Downloads/codex-session-migration-bundles)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    codex_home = Path(args.codex_home).expanduser()

    if not codex_home.exists():
        raise SystemExit(f"Codex home not found: {codex_home}")

    print("Scanning sessions...", flush=True)
    rows = build_inventory(codex_home)
    if not rows:
        raise SystemExit("No sessions found.")

    selected = pick_sessions(rows)
    if not selected:
        print("No sessions selected.")
        return 0

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_name = f"codex-session-migration-{timestamp}"

    print(f"\nBundling {len(selected)} session(s)...", flush=True)

    with tempfile.TemporaryDirectory(prefix="codex-migrate.") as tmp:
        tmp_path = Path(tmp)
        archive_path, installer_path, manifest_path = build_bundle(selected, codex_home, bundle_name, tmp_path)

        if args.host:
            target_dir = args.target_dir
            print(f"Transferring to {args.host}:{target_dir} ...", flush=True)
            subprocess.run(
                ["scp", str(archive_path), str(installer_path), f"{args.host}:{target_dir}/"],
                check=True,
            )
            print(f"Installing on {args.host} ...", flush=True)
            subprocess.run(
                ["ssh", "-t", args.host, f"bash {target_dir}/{bundle_name}.install.sh"],
                check=True,
            )
            print(f"\nBundle kept at {args.host}:{target_dir}/")
        else:
            output_dir = Path(args.output_dir).expanduser()
            output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive_path, output_dir / archive_path.name)
            shutil.copy2(installer_path, output_dir / installer_path.name)
            sibling_manifest = output_dir / f"{bundle_name}.manifest.csv"
            shutil.copy2(manifest_path, sibling_manifest)
            print(f"\nArchive:   {output_dir / archive_path.name}")
            print(f"Installer: {output_dir / installer_path.name}")
            print(f"Manifest:  {sibling_manifest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
