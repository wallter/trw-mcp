"""Internal helper functions for the SAFE-001 dispatch path."""

from __future__ import annotations

import difflib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trw_mcp.meta_tune.sandbox import SandboxResult


def resolve_repo(target_path: Path) -> Path:
    from trw_mcp.meta_tune.boot_checks import _resolve_repo_root

    try:
        return _resolve_repo_root(cwd=target_path.parent)
    except Exception:
        return target_path.parent.resolve()


def resolve_repo_path(path_str: str, *, repo_root: Path) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve()


def build_diff(target_path: Path, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{target_path.as_posix()}",
            tofile=f"b/{target_path.as_posix()}",
        )
    )


def materialize_sandbox_command(
    sandbox_command: list[str],
    *,
    candidate_path: Path,
    live_target_path: Path,
    corpus_path: Path,
    repo_root: Path,
) -> list[str]:
    replacements = {
        "{candidate_path}": str(candidate_path),
        "{target_path}": str(live_target_path),
        "{corpus_path}": str(corpus_path),
        "{repo_root}": str(repo_root),
    }
    rendered: list[str] = []
    for token in sandbox_command:
        current = token
        for placeholder, value in replacements.items():
            current = current.replace(placeholder, value)
        rendered.append(current)
    return rendered


def parse_sandbox_stdout(stdout: str) -> dict[str, Any]:
    stripped = stdout.strip()
    if not stripped:
        raise ValueError("sandbox stdout missing JSON payload")

    candidates = [line.strip() for line in stripped.splitlines() if line.strip()]
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("sandbox stdout did not contain a JSON object payload")


def derive_outcome_trace(payload: dict[str, Any]) -> list[dict[str, Any]]:
    trace = payload.get("outcome_trace")
    if isinstance(trace, list):
        return [row for row in trace if isinstance(row, dict)]
    scores = payload.get("scores")
    if isinstance(scores, dict):
        return [
            {"task": str(task_id), "score": float(score)}
            for task_id, score in scores.items()
            if isinstance(score, (int, float))
        ]
    return []


def sandbox_escape_signals(sandbox_result: SandboxResult) -> tuple[str, ...]:
    signals: list[str] = []
    if sandbox_result.writes_outside_tmp:
        signals.append("writes_outside_tmp")
    if sandbox_result.network_attempted:
        signals.append("network_attempted")
    return tuple(signals)


def persist_snapshot(
    *,
    edit_id: str,
    state_dir: Path,
    target_path: Path,
    original_path: Path,
    promotion_session_id: str,
) -> None:
    snapshot = {
        "proposal_id": edit_id,
        "target_path": str(target_path),
        "backup_path": str(original_path),
        "promotion_ts": datetime.now(timezone.utc).isoformat(),
        "promotion_session_id": promotion_session_id,
        "rollback_attempts": 0,
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{edit_id}.json").write_text(json.dumps(snapshot), encoding="utf-8")
