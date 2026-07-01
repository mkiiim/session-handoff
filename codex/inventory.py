#!/usr/bin/env python3
"""Inventory local Codex session rollout files into one CSV."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SESSION_ID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
MISSING = ""


FIELDS = [
    "migrate",
    "target_repo_root",
    "repo_name",
    "repo_root",
    "repo_exists",
    "is_git_repo",
    "under_projects",
    "session_id",
    "started_at",
    "last_modified",
    "size_bytes",
    "size_mb",
    "line_count",
    "recorded_cwd",
    "rollout_path",
    "rollout_relpath",
    "source_area",
    "shell_snapshot_count",
    "shell_snapshot_size_bytes",
    "shell_snapshot_paths",
    "history_count",
    "first_user_text",
    "last_user_text",
    "cli_version",
    "source",
    "originator",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        default=str(Path.home() / "codex-session-inventory" / "codex-sessions-inventory.csv"),
        help="CSV output path",
    )
    parser.add_argument(
        "--codex-home",
        default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
        help="Codex home directory",
    )
    parser.add_argument(
        "--projects-root",
        default=str(Path.home() / "Projects"),
        help="Primary projects directory used for the under_projects column",
    )
    parser.add_argument(
        "--prompt-chars",
        type=int,
        default=240,
        help="Maximum characters to keep from first/last user text",
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> tuple[list[dict], int]:
    records: list[dict] = []
    line_count = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
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
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"input_text", "text"}:
                parts.append(str(item.get("text", "")))
        return " ".join(part for part in parts if part)
    return ""


def user_text(record: dict) -> str:
    payload = record.get("payload")
    if isinstance(payload, dict):
        if payload.get("type") == "message" and payload.get("role") == "user":
            return text_from_content(payload.get("content"))
        if payload.get("role") == "user":
            return text_from_content(payload.get("content"))
    message = record.get("message")
    if isinstance(message, dict) and message.get("role") == "user":
        return text_from_content(message.get("content"))
    return ""


def clean_text(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def iso_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def session_id_from_path(path: Path) -> str:
    match = SESSION_ID_RE.search(path.name)
    return match.group(1) if match else MISSING


def resolve_repo(cwd: str) -> tuple[str, bool, bool]:
    if not cwd:
        return MISSING, False, False
    cwd_path = Path(cwd).expanduser()
    if not cwd_path.exists():
        return MISSING, False, False
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd_path), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return str(cwd_path), True, False
    return result.stdout.strip(), True, True


def rel_to_codex_home(path: Path, codex_home: Path) -> str:
    try:
        return str(path.relative_to(codex_home))
    except ValueError:
        return MISSING


def history_counts(codex_home: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    history = codex_home / "history.jsonl"
    if not history.exists():
        return counts
    with history.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = record.get("session_id")
            if isinstance(session_id, str):
                counts[session_id] = counts.get(session_id, 0) + 1
    return counts


def rollout_files(codex_home: Path) -> list[Path]:
    files = list((codex_home / "sessions").glob("**/*.jsonl"))
    files.extend((codex_home / "archived_sessions").glob("*.jsonl"))
    return sorted(path for path in files if path.is_file())


def main() -> int:
    args = parse_args()
    codex_home = Path(args.codex_home).expanduser()
    projects_root = Path(args.projects_root).expanduser()
    output = Path(args.output).expanduser()

    if not codex_home.exists():
        raise SystemExit(f"Codex home does not exist: {codex_home}")

    history_by_session = history_counts(codex_home)
    rows: list[dict[str, object]] = []

    for rollout in rollout_files(codex_home):
        records, line_count = read_jsonl(rollout)
        meta_payload = next(
            (
                record.get("payload", {})
                for record in records
                if record.get("type") == "session_meta" and isinstance(record.get("payload"), dict)
            ),
            {},
        )
        session_id = str(meta_payload.get("id") or session_id_from_path(rollout))
        recorded_cwd = str(meta_payload.get("cwd") or MISSING)
        repo_root, repo_exists, is_git_repo = resolve_repo(recorded_cwd)
        repo_name = Path(repo_root).name if repo_root else MISSING
        user_texts = [text for text in (user_text(record) for record in records) if text]
        shell_snapshots = sorted((codex_home / "shell_snapshots").glob(f"{session_id}.*"))
        shell_size = sum(path.stat().st_size for path in shell_snapshots if path.is_file())
        size_bytes = rollout.stat().st_size
        relpath = rel_to_codex_home(rollout, codex_home)
        source_area = relpath.split("/", 1)[0] if relpath else MISSING

        rows.append(
            {
                "migrate": "",
                "target_repo_root": "",
                "repo_name": repo_name,
                "repo_root": repo_root,
                "repo_exists": str(repo_exists).lower(),
                "is_git_repo": str(is_git_repo).lower(),
                "under_projects": str(bool(repo_root and Path(repo_root).is_relative_to(projects_root))).lower(),
                "session_id": session_id,
                "started_at": meta_payload.get("timestamp") or records[0].get("timestamp") if records else MISSING,
                "last_modified": iso_from_mtime(rollout),
                "size_bytes": size_bytes,
                "size_mb": f"{size_bytes / 1024 / 1024:.3f}",
                "line_count": line_count,
                "recorded_cwd": recorded_cwd,
                "rollout_path": str(rollout),
                "rollout_relpath": relpath,
                "source_area": source_area,
                "shell_snapshot_count": len(shell_snapshots),
                "shell_snapshot_size_bytes": shell_size,
                "shell_snapshot_paths": "|".join(str(path) for path in shell_snapshots),
                "history_count": history_by_session.get(session_id, 0),
                "first_user_text": clean_text(user_texts[0], args.prompt_chars) if user_texts else "",
                "last_user_text": clean_text(user_texts[-1], args.prompt_chars) if user_texts else "",
                "cli_version": meta_payload.get("cli_version", ""),
                "source": meta_payload.get("source", ""),
                "originator": meta_payload.get("originator", ""),
            }
        )

    rows.sort(key=lambda row: (str(row["repo_name"]), str(row["started_at"]), str(row["session_id"])))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} sessions to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
