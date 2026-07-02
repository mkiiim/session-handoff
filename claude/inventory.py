#!/usr/bin/env python3
"""Inventory local Claude Code session transcripts into one CSV."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


MISSING = ""

FIELDS = [
    "migrate",
    "target_project_root",
    "project_root",
    "project_exists",
    "is_git_repo",
    "repo_name",
    "session_id",
    "started_at",
    "last_modified",
    "size_bytes",
    "size_mb",
    "line_count",
    "claude_project_dir",
    "transcript_path",
    "memory_dir",
    "has_memory",
    "ai_title",
    "slug",
    "last_branch",
    "first_user_text",
    "last_user_text",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        default=str(Path.home() / "claude-session-inventory" / "claude-sessions-inventory.csv"),
        help="CSV output path",
    )
    parser.add_argument(
        "--claude-home",
        default=str(Path.home() / ".claude"),
        help="Claude home directory (default: ~/.claude)",
    )
    parser.add_argument(
        "--prompt-chars",
        type=int,
        default=240,
        help="Maximum characters for first/last user text",
    )
    return parser.parse_args()


def decode_project_dir(encoded: str) -> str:
    """Best-effort decode of a Claude encoded project directory name.

    Claude replaces every non-alphanumeric character with '-', so decoding is
    lossy. Paths with hyphens in the original will decode incorrectly; use
    project_exists to flag them and correct target_project_root in the CSV.
    """
    if encoded.startswith("-"):
        return "/" + encoded[1:].replace("-", "/")
    return encoded


def resolve_path(path_str: str) -> tuple[bool, bool]:
    """Return (exists, is_git_repo) for a filesystem path string."""
    if not path_str:
        return False, False
    path = Path(path_str).expanduser()
    if not path.exists():
        return False, False
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        return True, result.returncode == 0
    except (OSError, FileNotFoundError):
        return True, False


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
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def parse_transcript(path: Path, prompt_chars: int) -> dict:
    """Extract metadata from a Claude Code session JSONL transcript."""
    ai_title = MISSING
    slug = MISSING
    last_branch = MISSING
    started_at = MISSING
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
                    ai_title = record.get("aiTitle", MISSING) or MISSING

                if record.get("slug"):
                    slug = record["slug"]

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
        "slug": slug,
        "last_branch": last_branch,
        "started_at": started_at,
        "first_user_text": first_user or "",
        "last_user_text": last_user or "",
        "line_count": line_count,
    }


def main() -> int:
    args = parse_args()
    claude_home = Path(args.claude_home).expanduser()
    projects_dir = claude_home / "projects"
    output = Path(args.output).expanduser()

    if not claude_home.exists():
        raise SystemExit(f"Claude home not found: {claude_home}")
    if not projects_dir.exists():
        raise SystemExit(f"Claude projects directory not found: {projects_dir}")

    rows: list[dict] = []

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        encoded = project_dir.name
        project_root = decode_project_dir(encoded)
        project_exists, is_git = resolve_path(project_root)
        repo_name = Path(project_root).name if project_root else MISSING

        memory_dir = project_dir / "memory"
        has_memory = memory_dir.is_dir() and any(f for f in memory_dir.rglob("*") if f.is_file())

        for transcript in sorted(project_dir.glob("*.jsonl")):
            session_id = transcript.stem
            stat = transcript.stat()
            size_bytes = stat.st_size
            meta = parse_transcript(transcript, args.prompt_chars)

            rows.append(
                {
                    "migrate": "",
                    "target_project_root": project_root,
                    "project_root": project_root,
                    "project_exists": str(project_exists).lower(),
                    "is_git_repo": str(is_git).lower(),
                    "repo_name": repo_name,
                    "session_id": session_id,
                    "started_at": meta["started_at"] or iso_from_mtime(transcript),
                    "last_modified": iso_from_mtime(transcript),
                    "size_bytes": size_bytes,
                    "size_mb": f"{size_bytes / 1024 / 1024:.3f}",
                    "line_count": meta["line_count"],
                    "claude_project_dir": encoded,
                    "transcript_path": str(transcript),
                    "memory_dir": str(memory_dir) if has_memory else "",
                    "has_memory": str(has_memory).lower(),
                    "ai_title": meta["ai_title"],
                    "slug": meta["slug"],
                    "last_branch": meta["last_branch"],
                    "first_user_text": meta["first_user_text"],
                    "last_user_text": meta["last_user_text"],
                }
            )

    rows.sort(key=lambda r: (r["repo_name"], r["started_at"], r["session_id"]))

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} sessions to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
