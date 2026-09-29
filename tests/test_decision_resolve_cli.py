"""PRD-CORE-329 FR04/FR08/NFR03 — ``trw-mcp decision resolve``.

Follows the existing ``_<noun>_cli.py`` direct-invocation test pattern
(``test_heartbeat_and_adopt.py::test_cli_adopt_pins_the_named_session_not_the_cli_process``):
call the handler function with a synthetic ``argparse.Namespace`` rather than
spawning a subprocess.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    from trw_mcp.models.config import _reset_config, get_config

    _reset_config()
    config = get_config()
    (tmp_path / config.trw_dir).mkdir(parents=True, exist_ok=True)
    (tmp_path / config.trw_dir / "runtime").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _trw_dir(project_root: Path) -> Path:
    from trw_mcp.models.config import get_config

    return project_root / get_config().trw_dir


# ---------------------------------------------------------------------------
# FR04: idempotent resolve
# ---------------------------------------------------------------------------


def test_resolve_idempotent(isolated_project: Path) -> None:
    from trw_mcp.state._decision_queue import read_decision_queue, record_decision
    from trw_mcp.tools._decision_cli import _resolve

    trw_dir = _trw_dir(isolated_project)
    decision_id = record_decision(
        trw_dir,
        run_path="run-a",
        question="Ship without the flaky test?",
        options=["ship", "hold"],
        why_unreachable="unattended loop, no operator online",
    )

    first, failed_first = _resolve(Namespace(id=decision_id, choice="ship"))
    assert failed_first is False
    assert first["status"] == "resolved"
    assert first["choice"] == "ship"

    # A retried / duplicated resolve (per the repo's MCP transport-loss retry
    # protocol) with a DIFFERENT choice must return the first resolution
    # unchanged, never double-apply.
    second, failed_second = _resolve(Namespace(id=decision_id, choice="hold"))
    assert failed_second is False
    assert second == first

    state = read_decision_queue(trw_dir)
    assert decision_id not in state.pending_ids
    assert decision_id in state.resolved_ids

    # Exactly one resolution record was appended, not two.
    lines = [line for line in (trw_dir / "runtime" / "decisions.jsonl").read_text().splitlines() if line.strip()]
    resolved_lines = [line for line in lines if '"status": "resolved"' in line]
    assert len(resolved_lines) == 1


def test_resolve_marks_pending_decision_not_pending_for_every_reader(isolated_project: Path) -> None:
    from trw_mcp.state._decision_queue import read_decision_queue, record_decision
    from trw_mcp.tools._decision_cli import _resolve

    trw_dir = _trw_dir(isolated_project)
    decision_id = record_decision(
        trw_dir,
        run_path="run-a",
        question="Q?",
        options=["a", "b"],
        why_unreachable="w",
    )
    assert decision_id in read_decision_queue(trw_dir).pending_ids

    _resolve(Namespace(id=decision_id, choice="a"))

    assert decision_id not in read_decision_queue(trw_dir).pending_ids


# ---------------------------------------------------------------------------
# NFR03: unknown id refused, nothing appended
# ---------------------------------------------------------------------------


def test_unknown_id_refused(isolated_project: Path) -> None:
    from trw_mcp.tools._decision_cli import _resolve

    trw_dir = _trw_dir(isolated_project)
    document, failed = _resolve(Namespace(id="does-not-exist", choice="x"))

    assert failed is True
    assert document["error"] == "unknown_decision_id"
    queue_path = trw_dir / "runtime" / "decisions.jsonl"
    assert not queue_path.exists() or queue_path.read_text() == ""


# ---------------------------------------------------------------------------
# FR08 layer 1: advisory refusal under each unattended marker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("TRW_LOOP_WORKER", "1"),
        ("TRW_DISPATCH_CHILD", "1"),
        ("TRW_SURFACE_ROLE", "reviewer"),
    ],
)
def test_resolve_refused_in_unattended_lanes(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
) -> None:
    from trw_mcp.state._decision_queue import read_decision_queue, record_decision
    from trw_mcp.tools._decision_cli import _resolve

    trw_dir = _trw_dir(isolated_project)
    decision_id = record_decision(
        trw_dir,
        run_path="run-a",
        question="Q?",
        options=["a"],
        why_unreachable="w",
    )
    monkeypatch.setenv(env_name, env_value)

    document, failed = _resolve(Namespace(id=decision_id, choice="a"))

    assert failed is True
    assert document["error"] == "resolve_refused_unattended"
    state = read_decision_queue(trw_dir)
    assert decision_id in state.pending_ids
    assert decision_id not in state.resolved_ids


def test_resolve_permitted_with_no_unattended_marker(isolated_project: Path) -> None:
    from trw_mcp.state._decision_queue import record_decision
    from trw_mcp.tools._decision_cli import _resolve

    trw_dir = _trw_dir(isolated_project)
    decision_id = record_decision(
        trw_dir,
        run_path="run-a",
        question="Q?",
        options=["a"],
        why_unreachable="w",
    )

    document, failed = _resolve(Namespace(id=decision_id, choice="a"))

    assert failed is False
    assert document["status"] == "resolved"
    assert document["actor"] == "attended"


def test_resolution_record_with_unattended_actor_leaves_decision_pending(isolated_project: Path) -> None:
    """FR08 rule 2 (via FR02's shared read): actor=unattended never accepts."""
    from trw_mcp.state._decision_queue import read_decision_queue, record_decision, resolve_decision

    trw_dir = _trw_dir(isolated_project)
    decision_id = record_decision(
        trw_dir,
        run_path="run-a",
        question="Q?",
        options=["a"],
        why_unreachable="w",
    )
    resolve_decision(trw_dir, decision_id=decision_id, choice="a", actor="unattended")

    state = read_decision_queue(trw_dir)
    assert decision_id in state.pending_ids
    assert decision_id not in state.resolved_ids


# ---------------------------------------------------------------------------
# FR08/NFR03: the reviewer-lane deny-by-default CLI guard also covers this verb
# ---------------------------------------------------------------------------


def test_decision_resolve_is_not_reviewer_allowlisted() -> None:
    """The generic bounded-lane CLI guard must never classify this verb as read-only."""
    from trw_mcp.server._cli_reviewer_policy import classified_command_paths

    assert "decision resolve" not in classified_command_paths()
