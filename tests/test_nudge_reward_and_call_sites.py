"""Missing nudge call sites (UF-042/UF-043).

Two defects with the same shape: a mechanism that runs correctly but is not
connected to anything.

* **UF-042** — ``trw_build_check`` and ``trw_deliver`` never called the ceremony
  injector, so ``NudgeContext.build_passed`` had **no production writer
  anywhere**. That alone made the "Build failed -> revert to PLAN" branch of
  ``_reversion_prompt`` and the build-failure bypass in ``_select_nudge_pool``
  unreachable, independent of ledger UF-006.
* **UF-043** — ``trw_recall`` lost its ceremony-status injection in merge
  ``70bb84843f`` (2026-04-11), leaving ``ToolName.RECALL`` with no producer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.state._ceremony_progress_state import CeremonyState, NudgeContext, write_ceremony_state
from trw_mcp.state._ceremony_state_model import ToolName


@pytest.fixture
def status_spy(monkeypatch: pytest.MonkeyPatch) -> list[NudgeContext | None]:
    """Capture the ``context`` argument reaching ``append_ceremony_status``."""
    seen: list[NudgeContext | None] = []

    def _spy(
        response: dict[str, object],
        trw_dir: Path | None = None,
        context: NudgeContext | None = None,
    ) -> dict[str, object]:
        seen.append(context)
        return response

    monkeypatch.setattr("trw_mcp.tools._ceremony_status.append_ceremony_status", _spy)
    return seen


# ---------------------------------------------------------------------------
# UF-042: build_passed finally has a production writer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tests_passed", "static_clean", "expected"),
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_build_check_writes_build_passed_into_the_context(
    tests_passed: bool,
    static_clean: bool,
    expected: bool,
    tmp_project: Path,
    status_spy: list[NudgeContext | None],
) -> None:
    """The reactive signal reflects the FULL build verdict, not just tests."""
    server = make_test_server("build")
    extract_tool_fn(server, "trw_build_check")(
        tests_passed=tests_passed,
        static_checks_clean=static_clean,
        test_count=4,
        scope="pytest tests/",
    )

    assert len(status_spy) == 1, status_spy
    context = status_spy[0]
    assert context is not None, "trw_build_check still bypasses the injector"
    assert context.tool_name == ToolName.BUILD_CHECK
    assert context.build_passed is expected
    assert context.tool_success is expected


def test_build_check_failure_reaches_the_reversion_prompt_end_to_end(tmp_project: Path) -> None:
    """No spy: the real injector, the real pool, the real response field."""
    trw_dir = tmp_project / ".trw"
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\n", encoding="utf-8")
    write_ceremony_state(trw_dir, CeremonyState(session_started=True, checkpoint_count=1, phase="validate"))

    server = make_test_server("build")
    result = extract_tool_fn(server, "trw_build_check")(
        tests_passed=False,
        static_checks_clean=True,
        test_count=9,
        failure_count=2,
        scope="pytest tests/",
    )

    assert "ceremony_status" in result
    assert "revert to PLAN" in str(result.get("reversion_prompt", ""))
    # The build-failure context also force-selects the reactive pool.
    assert "nudge_content" in result


def test_build_check_pass_carries_no_reversion_prompt(tmp_project: Path) -> None:
    """Negative case: a green build must not suggest reverting to PLAN."""
    trw_dir = tmp_project / ".trw"
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\n", encoding="utf-8")
    write_ceremony_state(trw_dir, CeremonyState(session_started=True, checkpoint_count=1, phase="validate"))

    server = make_test_server("build")
    result = extract_tool_fn(server, "trw_build_check")(
        tests_passed=True,
        static_checks_clean=True,
        test_count=9,
        scope="pytest tests/",
    )
    assert "reversion_prompt" not in result


@pytest.mark.parametrize(
    "raising_target",
    [
        "trw_mcp.tools._ceremony_status.append_ceremony_status",
        "trw_mcp.tools._ceremony_status_context.append_ceremony_status_for_tool",
    ],
    ids=["injector", "seam"],
)
def test_build_check_survives_a_raising_nudge_injection(
    raising_target: str,
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nudge injection is telemetry riding the build hot path — it must fail open.

    ``trw_deliver`` wraps its equivalent call in a fail-open try/except. The
    build_check call site added with UF-042 did not, so a filesystem error, a
    concurrent lock, or a serialization failure anywhere inside injection
    propagated out and failed ``trw_build_check`` — telemetry breaking a build
    check that had already completed. Both the seam and the injector underneath
    it are exercised, because either can be the thing that raises.
    """

    def _boom(*args: object, **kwargs: object) -> dict[str, object]:
        raise OSError("nudge state unwritable")

    monkeypatch.setattr(raising_target, _boom)

    server = make_test_server("build")
    result = extract_tool_fn(server, "trw_build_check")(
        tests_passed=True,
        static_checks_clean=True,
        test_count=7,
        scope="pytest tests/",
    )

    # The verdict survives intact; only the decoration is lost.
    assert result["tests_passed"] is True
    assert result["test_count"] == 7
    assert result["scope"] == "pytest tests/"
    assert "ceremony_status" not in result


def test_deliver_supplies_the_recorded_build_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_spy: list[NudgeContext | None],
) -> None:
    """Deliver reads the session's build result back out of ceremony state."""
    from unittest.mock import patch

    from tests._ceremony_helpers import make_ceremony_server

    tools = make_ceremony_server(monkeypatch, tmp_path)
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    write_ceremony_state(trw_dir, CeremonyState(session_started=True, build_check_result="failed"))

    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        patch(
            "trw_mcp.tools.ceremony._do_instruction_sync",
            return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
        ),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
    ):
        tools["trw_deliver"].fn(
            skip_reflect=True,
            allow_unverified=True,
            unverified_reason="test fixture: synthetic run",
        )

    assert status_spy, "trw_deliver still bypasses the ceremony injector"
    context = status_spy[-1]
    assert context is not None
    assert context.tool_name == ToolName.DELIVER
    assert context.build_passed is False


# ---------------------------------------------------------------------------
# UF-043: ToolName.RECALL has a producer again
# ---------------------------------------------------------------------------


def test_recall_reaches_the_injector_with_a_recall_context(
    tmp_project: Path,
    status_spy: list[NudgeContext | None],
    fake_memory_store: object,
) -> None:
    server = make_test_server("learning")
    extract_tool_fn(server, "trw_recall")(query="nudge attribution")

    assert status_spy, "trw_recall still decorates nothing"
    assert status_spy[-1] is not None
    assert status_spy[-1].tool_name == ToolName.RECALL


def test_recall_response_carries_ceremony_status(tmp_project: Path, fake_memory_store: object) -> None:
    """Live path, no spy — the field actually lands on the payload."""
    server = make_test_server("learning")
    result = extract_tool_fn(server, "trw_recall")(query="nudge attribution")
    assert isinstance(result.get("ceremony_status"), str)
