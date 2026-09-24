"""The isolated-review lane: a confined child in a standalone snapshot (PRD-CORE-297-FR04).

Belongs to the ``trw_mcp.dispatch`` package; ``_runner.dispatch`` enters it for an
admitted ``posture="isolated-review"`` request and hands in the ordinary spawn
path as ``spawn``. The lane:

1. opens a :func:`standalone_snapshot` of the caller (baseline includes the
   caller's HEAD/index/refs/config);
2. proves the client's MCP is off from inside the snapshot, confined, with the
   temp HOME: exit 0 and a listing that is exactly the empty marker;
3. rebinds ``req.cwd`` to the snapshot BEFORE argv exists (agy copies it into
   ``--add-dir``) and spawns with the temp HOME;
4. reports any snapshot or caller change as contamination, which makes ``ok``
   False while keeping the child's text.

Every failure refuses or fails the run (NFR01); nothing falls back to the caller.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from trw_mcp.dispatch._client_specs import client_spec_for
from trw_mcp.dispatch._confine import confinement_prefix
from trw_mcp.dispatch._env import build_subprocess_env
from trw_mcp.dispatch._posture import ReviewerPostureError
from trw_mcp.dispatch._snapshot import Snapshot, standalone_snapshot
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult

__all__ = ["IsolationFailedError", "run_isolated"]

_MCP_LIST_TIMEOUT_S = 60


class IsolationFailedError(RuntimeError):
    """The snapshot, preflight or diff failed; the run fails rather than degrade."""


def _require_mcp_off(req: DispatchRequest, snap: Snapshot) -> None:
    lane = client_spec_for(req.client).isolated_review
    if lane is None:  # pragma: no cover - verify_reviewer_posture admits only a spec with a lane
        raise ReviewerPostureError(f"client {req.client!r} has no isolated_review lane")
    env = build_subprocess_env(req.client) | {"HOME": str(snap.home)}
    try:
        listed = subprocess.run(  # noqa: S603 - registry argv, no shell
            [*confinement_prefix(snap.home), *lane.mcp_list_argv],
            cwd=snap.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=_MCP_LIST_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewerPostureError(f"{req.client}: MCP could not be turned off: {exc}") from exc
    if listed.returncode != 0 or listed.stdout.strip() != lane.mcp_empty_marker:
        output = (listed.stdout + listed.stderr).strip()[:400]
        raise ReviewerPostureError(f"{req.client}: MCP could not be turned off: {output}")


def run_isolated(req: DispatchRequest, spawn: Callable[[DispatchRequest, Path], DispatchResult]) -> DispatchResult:
    """Run *req* in a fresh snapshot of its cwd; raises on refusal or isolation failure."""
    lane = client_spec_for(req.client).isolated_review
    strip = lane.strip_paths if lane is not None else ()
    try:
        with standalone_snapshot(req.cwd or Path.cwd(), strip) as snap:
            _require_mcp_off(req, snap)
            result = spawn(req.model_copy(update={"cwd": snap.root}), snap.home)
            changed = snap.diff()
            # The lane always spawns confined, so the child's own note names the mechanism.
            dropped = f", dropped escaping links {snap.dropped_links}" if snap.dropped_links else ""
            note = f"standalone snapshot {snap.root} (removed after the run{dropped}), HOME={snap.home}; {result.sandbox_note}"
    except (OSError, subprocess.CalledProcessError) as exc:
        raise IsolationFailedError(f"isolated snapshot failed: {exc}") from exc
    return result.model_copy(
        update={
            "isolation": "snapshot-write-confined",
            "contamination": "contaminated" if changed else "clean",
            "changed_paths": changed,
            "sandbox_note": note,
        }
    )
