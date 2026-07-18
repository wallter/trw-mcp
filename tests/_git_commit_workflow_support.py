"""Test-only setup for the verified two-step candidate workflow."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


def create_verified_run(repo: Path, run_id: str) -> Path:
    run_dir = repo / ".trw" / "runs" / "test" / run_id.replace("/", "-")
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "run.yaml").write_text(f"run_id: {run_id}\nstatus: active\n", encoding="utf-8")
    (meta / "events.jsonl").touch()
    pins = repo / ".trw" / "runtime" / "pins.json"
    pins.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {}
    if pins.exists():
        payload = json.loads(pins.read_text(encoding="utf-8"))
    payload[f"pin-{run_id}"] = {
        "run_path": str(run_dir.resolve()),
        "last_heartbeat_ts": datetime.now().astimezone().isoformat(),
    }
    pins.write_text(json.dumps(payload), encoding="utf-8")
    return run_dir


def journal_edits_and_build(run_dir: Path, paths: tuple[str, ...], run_id: str) -> None:
    from trw_mcp.models._evidence_core import ContentEntry, EntryState, compute_manifest_digest

    events = run_dir / "meta" / "events.jsonl"
    with events.open("a", encoding="utf-8") as handle:
        for path in paths:
            handle.write(json.dumps({"event": "file_modified", "file": path}) + "\n")
    receipts = run_dir / "meta" / "receipts" / "build"
    receipts.mkdir(parents=True, exist_ok=True)
    receipt_id = "build-test-success"
    repo = run_dir.parents[3]
    entries: list[ContentEntry] = []
    for path in paths:
        target = repo / path
        if target.is_symlink():
            entries.append(ContentEntry(path=path, state=EntryState.SYMLINK, link_target=os.readlink(target)))
        elif target.is_file():
            raw = target.read_bytes()
            entries.append(
                ContentEntry(
                    path=path,
                    state=EntryState.FILE,
                    byte_digest=hashlib.sha256(raw).hexdigest(),
                    byte_size=len(raw),
                )
            )
        else:
            entries.append(ContentEntry(path=path, state=EntryState.DELETED))
    (receipts / f"{receipt_id}.json").write_text(
        json.dumps(
            {
                "receipt_id": receipt_id,
                "run_id": run_id,
                "completed_at": datetime.now().astimezone().isoformat(),
                "command_results": [
                    {"command_id": "tests", "exit_code": 0},
                    {"command_id": "static_checks", "exit_code": 0},
                ],
                "content_binding": {
                    "scope_id": "test-scope",
                    "scope_digest": "test-scope-digest",
                    "project_identity": repo.name,
                    "entries": [entry.model_dump(mode="json") for entry in entries],
                    "manifest_digest": compute_manifest_digest(tuple(entries)),
                },
            }
        ),
        encoding="utf-8",
    )


def verified_candidate(
    repo: Path,
    paths: tuple[str, ...],
    message: str,
    run_id: str,
    *,
    transaction_id: str = "",
    require_signature: bool = False,
) -> tuple[dict[str, Any], Path]:
    """Reconstruct the pre-edit baseline, claim, then restore test edits."""
    from trw_mcp.state.git_commit_workflow import prepare_candidate_claim, run_candidate_commit

    post: dict[str, tuple[str, bytes | str, int]] = {}
    for path in paths:
        target = repo / path
        if target.is_symlink():
            post[path] = ("symlink", os.readlink(target), 0)
        elif target.exists():
            post[path] = ("file", target.read_bytes(), target.stat().st_mode & 0o777)
        else:
            post[path] = ("absent", b"", 0)
        target.unlink(missing_ok=True)
        restored = subprocess.run(
            ["git", "-C", str(repo), "restore", "--source=HEAD", "--worktree", "--", path],
            capture_output=True,
            check=False,
        )
        if restored.returncode != 0:
            target.unlink(missing_ok=True)
    run_dir = create_verified_run(repo, run_id)
    manifest = prepare_candidate_claim(repo, paths, run_dir, transaction_id=transaction_id)
    for path, (kind, value, mode) in post.items():
        target = repo / path
        target.unlink(missing_ok=True)
        if kind == "file":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value if isinstance(value, bytes) else value.encode())
            target.chmod(mode)
        elif kind == "symlink":
            target.symlink_to(str(value))
    journal_edits_and_build(run_dir, paths, run_id)
    result = run_candidate_commit(
        repo,
        message,
        transaction_id=manifest.transaction_id,
        run_dir=run_dir,
        require_signature=require_signature,
    )
    return result, run_dir
