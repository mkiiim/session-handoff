#!/usr/bin/env python3
"""Interactive Claude Code session migration: inventory → pick → bundle → transfer."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path


# ── inventory ────────────────────────────────────────────────────────────────

def decode_project_dir(encoded: str) -> str:
    if encoded.startswith("-"):
        return "/" + encoded[1:].replace("-", "/")
    return encoded


def iso_from_mtime(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def extract_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return " ".join(parts).strip()
    return ""


def clean_text(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text[: limit - 3] + "..." if len(text) > limit else text


def parse_transcript(path: Path, prompt_chars: int = 120) -> dict:
    ai_title = ""
    last_branch = ""
    started_at = ""
    first_user: str | None = None
    last_user: str | None = None
    line_count = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line_count += 1
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not started_at:
                    ts = record.get("timestamp", "")
                    if ts:
                        started_at = str(ts)
                rtype = record.get("type", "")
                if rtype == "ai-title":
                    ai_title = record.get("aiTitle", "") or ""
                if record.get("gitBranch"):
                    last_branch = record["gitBranch"]
                if rtype == "user":
                    content = record.get("message", {}).get("content", "")
                    text = extract_text(content)
                    if text:
                        cleaned = clean_text(text, prompt_chars)
                        if first_user is None:
                            first_user = cleaned
                        last_user = cleaned
    except OSError:
        pass
    return {
        "ai_title": ai_title,
        "last_branch": last_branch,
        "started_at": started_at,
        "last_user_text": last_user or "",
        "line_count": line_count,
    }


def build_inventory(claude_home: Path) -> list[dict]:
    projects_dir = claude_home / "projects"
    rows: list[dict] = []

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        encoded = project_dir.name
        project_root = decode_project_dir(encoded)
        p = Path(project_root)
        project_exists = p.exists()
        is_git = False
        if project_exists:
            try:
                r = subprocess.run(
                    ["git", "-C", str(p), "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True,
                )
                is_git = r.returncode == 0
            except (OSError, FileNotFoundError):
                pass
        repo_name = p.name if project_root else ""
        memory_dir = project_dir / "memory"
        has_memory = memory_dir.is_dir() and any(f for f in memory_dir.rglob("*") if f.is_file())

        for transcript in sorted(project_dir.glob("*.jsonl")):
            stat = transcript.stat()
            size_bytes = stat.st_size
            meta = parse_transcript(transcript)
            rows.append({
                "project_root": project_root,
                "target_project_root": project_root,
                "project_exists": project_exists,
                "repo_name": repo_name,
                "session_id": transcript.stem,
                "started_at": meta["started_at"] or iso_from_mtime(transcript),
                "last_modified": iso_from_mtime(transcript),
                "size_mb": f"{size_bytes / 1024 / 1024:.2f}",
                "line_count": meta["line_count"],
                "claude_project_dir": encoded,
                "transcript_path": str(transcript),
                "memory_dir": str(memory_dir) if has_memory else "",
                "has_memory": has_memory,
                "ai_title": meta["ai_title"],
                "last_branch": meta["last_branch"],
                "last_user_text": meta["last_user_text"],
            })

    rows.sort(key=lambda r: (r["repo_name"], r["started_at"], r["session_id"]))
    return rows


# ── picker ───────────────────────────────────────────────────────────────────

def pick_sessions(rows: list[dict]) -> list[dict]:
    if not shutil.which("fzf"):
        raise SystemExit("fzf is required but not found in PATH. Install it with: brew install fzf")

    lines = []
    for i, row in enumerate(rows):
        path = row["project_root"] or row["claude_project_dir"]
        path = ("…" + path[-35:]) if len(path) > 36 else path
        flag = "?" if not row["project_exists"] else " "
        date = row["last_modified"][:10]
        size = f"{float(row['size_mb']):5.2f}MB"
        title = (row["ai_title"] or row["last_user_text"] or "")[:55]
        lines.append(f"{i:04d}\t{path:<36}  {flag}  {date}  {size}  {title}")

    col_header = f"XXXX\t{'PATH':<36}  ?  {'DATE':<10}  {'SIZE':>7}  TITLE"

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

def encode_path(path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


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

printf '\\nDone. Open Claude Code in each target project and use /resume.\\n'
"""


def build_bundle(selected: list[dict], claude_home: Path, bundle_name: str, tmp: Path) -> tuple[Path, Path]:
    bundle_root = tmp / bundle_name
    (bundle_root / "claude" / "projects").mkdir(parents=True)

    copied_memory: set[str] = set()
    for row in selected:
        session_id = row["session_id"]
        transcript_path = Path(row["transcript_path"])
        target_root = (row.get("target_project_root") or row.get("project_root") or "").strip()
        target_encoded = encode_path(target_root) if target_root else row.get("claude_project_dir", "")

        dest_project = bundle_root / "claude" / "projects" / target_encoded
        dest_project.mkdir(parents=True, exist_ok=True)
        shutil.copy2(transcript_path, dest_project / f"{session_id}.jsonl")

        memory_dir_str = (row.get("memory_dir") or "").strip()
        if memory_dir_str and memory_dir_str not in copied_memory:
            memory_src = Path(memory_dir_str)
            if memory_src.is_dir():
                shutil.copytree(memory_src, dest_project / "memory", dirs_exist_ok=True)
                copied_memory.add(memory_dir_str)

    # Global config — review-only
    config_dest = bundle_root / "config"
    for fname in ("settings.json", "CLAUDE.md"):
        src = claude_home / fname
        if src.exists():
            config_dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, config_dest / fname)

    archive_path = tmp / f"{bundle_name}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(bundle_root, arcname=bundle_name)

    installer_path = tmp / f"{bundle_name}.install.sh"
    installer_path.write_text(installer_text(bundle_name), encoding="utf-8")
    installer_path.chmod(0o755)

    return archive_path, installer_path


# ── target path confirmation ─────────────────────────────────────────────────

def input_with_prefill(prompt: str, prefill: str) -> str:
    result = subprocess.run(
        [
            "bash", "-c",
            f"read -e -i {shlex.quote(prefill)} -p {shlex.quote(prompt)} REPLY"
            f" && printf '%s' \"$REPLY\"",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    return result.stdout or prefill


def confirm_target_paths(selected: list[dict]) -> None:
    """For each unresolved (?) project in the selection, prompt for target path."""
    # Collect unique ? projects in selection order
    seen: set[str] = set()
    unknown_projects: list[str] = []
    for row in selected:
        key = row["claude_project_dir"]
        if not row["project_exists"] and key not in seen:
            seen.add(key)
            unknown_projects.append(key)

    if not unknown_projects:
        return

    print(f"\n{len(unknown_projects)} project path(s) could not be verified on this machine.")
    print("Edit each target path — this determines where the session lands on the destination.\n")

    overrides: dict[str, str] = {}
    for encoded in unknown_projects:
        # Use the decoded guess from the first matching row
        prefill = next(r["target_project_root"] for r in selected if r["claude_project_dir"] == encoded)
        value = input_with_prefill(f"  {encoded}\n  target: ", prefill).strip()
        overrides[encoded] = value or prefill

    # Apply overrides to all sessions in the affected projects
    for row in selected:
        key = row["claude_project_dir"]
        if key in overrides:
            row["target_project_root"] = overrides[key]


# ── main ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "host",
        nargs="?",
        help="SSH target (user@host). If omitted, bundle is saved locally.",
    )
    parser.add_argument(
        "--claude-home",
        default=str(Path.home() / ".claude"),
        help="Claude home directory (default: ~/.claude)",
    )
    parser.add_argument(
        "--target-dir",
        default="~/Downloads",
        help="Directory on target host to receive the bundle (default: ~/Downloads)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / "Downloads" / "claude-session-migration-bundles"),
        help="Local output directory when no host is given (default: ~/Downloads/claude-session-migration-bundles)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    claude_home = Path(args.claude_home).expanduser()

    if not claude_home.exists():
        raise SystemExit(f"Claude home not found: {claude_home}")

    print("Scanning sessions...", flush=True)
    rows = build_inventory(claude_home)
    if not rows:
        raise SystemExit("No sessions found.")

    selected = pick_sessions(rows)
    if not selected:
        print("No sessions selected.")
        return 0

    confirm_target_paths(selected)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_name = f"claude-session-migration-{timestamp}"

    print(f"\nBundling {len(selected)} session(s)...", flush=True)

    with tempfile.TemporaryDirectory(prefix="claude-migrate.") as tmp:
        tmp_path = Path(tmp)
        archive_path, installer_path = build_bundle(selected, claude_home, bundle_name, tmp_path)

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
            print(f"\nArchive:   {output_dir / archive_path.name}")
            print(f"Installer: {output_dir / installer_path.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
