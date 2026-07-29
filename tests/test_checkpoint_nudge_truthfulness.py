"""Truthfulness (CONSTITUTION §1) — a not-recorded checkpoint claims nothing.

``trw_checkpoint`` correctly returns ``recorded: False`` / ``status:
"not_recorded"`` when no run is resolvable, and correctly withholds the
ceremony ``checkpoint_count`` increment. But the ceremony-status decorator that
runs on the SAME response built its ``NudgeContext`` with the *default*
``tool_success=True``, so the reactive checkpoint message ("Progress saved. …")
could be attached to a call that persisted nothing.

Why the pre-existing coverage missed it:
``tests/test_checkpoint_no_run_degrades_truthfully.py`` asserts against
``execute_checkpoint`` — one layer BELOW the nudge decorator — and greps for the
single token ``checkpoint_created``, which the offending prose does not contain.

These tests therefore (a) run at the TOOL layer, where the decorator is live,
(b) assert a property over EVERY string in the whole response rather than one
token, and (c) prove non-vacuity: the very same scanner, the very same forced
nudge pool, and a genuinely recorded checkpoint DO produce success prose.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests._tools_orchestration_support import orch_tools  # noqa: F401

# A family of claims that only a PERSISTED checkpoint may make. Deliberately
# broader than the one token the old test grepped for: the defect shipped prose
# ("Progress saved.") that shared no substring with ``checkpoint_created``.
_SUCCESS_CLAIM = re.compile(
    r"progress\s+saved"
    r"|saved\s+your\s+progress"
    r"|checkpoint_created"
    r"|checkpoint(?:\s+\w+){0,2}\s+(?:created|recorded|saved|written|persisted)"
    r"|(?:created|recorded|saved|written|persisted)\s+(?:the\s+|a\s+|your\s+)?checkpoint",
    re.IGNORECASE,
)


def _walk_strings(value: object, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield ``(json_path, text)`` for every string reachable in *value*."""
    if isinstance(value, str):
        yield path or "<root>", value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}[{index}]")


def _success_claims(response: object) -> list[tuple[str, str]]:
    """Return every ``(field_path, text)`` in *response* asserting checkpoint success."""
    return [(where, text) for where, text in _walk_strings(response) if _SUCCESS_CLAIM.search(text)]


@pytest.fixture
def force_context_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the reactive per-tool ("context") nudge fire on EVERY call.

    Two independent production routes reach ``_context_reactive_message``; both
    are pinned so neither can go quiet and turn these assertions vacuous:

    * the ``contextual`` messenger calls it unconditionally whenever a
      ``NudgeContext`` exists (``_ceremony_nudge_selectors``), and
    * the standard messenger reaches it only on a weighted-random ``context``
      draw (10/100 by default) — forced here by pinning the pool draw, since a
      1-in-10 test is flaky-by-luck rather than deterministic.
    """
    from trw_mcp.models.config import _reset_config
    from trw_mcp.state import _nudge_rules

    monkeypatch.setenv("TRW_NUDGE_ENABLED", "true")
    monkeypatch.setattr(_nudge_rules._RNG, "choices", lambda pools, weights, k: ["context"])
    _reset_config()


def _prepare_project(project_root: Path) -> None:
    """Create the empty runs tree the no-pin branch requires.

    Without ``.trw/runs/`` the resolver raises "cannot auto-detect" instead of
    reaching the softened not-recorded branch, and the test would never exercise
    the path under assertion.
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    (project_root / config.runs_root).mkdir(parents=True, exist_ok=True)
    (project_root / config.trw_dir).mkdir(parents=True, exist_ok=True)


def _seed_run(project_root: Path, task: str = "task-a", run_id: str = "20260101T000000Z-aaaa1111") -> Path:
    from trw_mcp.models.config import get_config

    run_dir = project_root / get_config().runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\ntask: {task}\nstatus: active\nphase: implement\n",
        encoding="utf-8",
    )
    return run_dir


def _checkpoint(orch_tools: dict[str, Any], session_id: str, **kwargs: Any) -> dict[str, object]:
    result = orch_tools["trw_checkpoint"].fn(ctx=SimpleNamespace(session_id=session_id), **kwargs)
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize("messenger", ["standard", "contextual"])
def test_not_recorded_checkpoint_response_makes_no_success_claim_anywhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    orch_tools: dict[str, Any],
    force_context_nudge: None,
    messenger: str,
) -> None:
    """No field of a ``recorded: False`` response may claim the checkpoint happened.

    Asserted over the WHOLE payload — ``nudge_content`` is where the defect
    lived, but the property is field-agnostic on purpose so the next field to
    carry reactive prose is covered without a test edit.
    """
    from trw_mcp.models.config import _reset_config

    monkeypatch.setenv("TRW_NUDGE_MESSENGER", messenger)
    monkeypatch.setenv("TRW_SESSION_ID", f"truthful-unpinned-{messenger}")
    _reset_config()
    _prepare_project(tmp_path)

    result = _checkpoint(orch_tools, f"truthful-unpinned-{messenger}", message="never persisted")

    assert result["recorded"] is False, "precondition: this call must persist nothing"
    # Proves the ceremony/nudge decorator actually RAN on this response, so an
    # empty claim list below means "suppressed", not "never reached".
    assert "ceremony_status" in result, "ceremony decoration did not run — assertion would be vacuous"
    assert _success_claims(result) == [], (
        f"a checkpoint that persisted nothing claimed success ({messenger} messenger): {_success_claims(result)}"
    )


@pytest.mark.parametrize("messenger", ["standard", "contextual"])
def test_recorded_checkpoint_may_carry_success_prose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    orch_tools: dict[str, Any],
    force_context_nudge: None,
    messenger: str,
) -> None:
    """Non-vacuity: same scanner, same forced pool, a REAL checkpoint — prose appears.

    Without this, suppressing the nudge unconditionally (or a nudge layer that
    silently stopped emitting) would satisfy the test above while destroying the
    feature it guards.
    """
    from trw_mcp.models.config import _reset_config

    monkeypatch.setenv("TRW_NUDGE_MESSENGER", messenger)
    monkeypatch.setenv("TRW_SESSION_ID", f"truthful-recorded-{messenger}")
    _reset_config()
    run_dir = _seed_run(tmp_path)

    result = _checkpoint(
        orch_tools,
        f"truthful-recorded-{messenger}",
        run_path=str(run_dir),
        message="real progress",
    )

    assert result["recorded"] is True
    claims = _success_claims(result)
    assert claims, "the reactive checkpoint nudge stopped firing on the success path"
    assert any(where == "nudge_content" for where, _text in claims), (
        f"the context-pool nudge did not reach nudge_content — pool forcing is stale: {result}"
    )


def test_not_recorded_checkpoint_emits_no_reactive_checkpoint_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    orch_tools: dict[str, Any],
    force_context_nudge: None,
) -> None:
    """Unit-level pin on the seam: the suppression is keyed on the real outcome.

    The response-level property above can be satisfied by accident (e.g. a
    cooldown swallowing the pool). This asserts the actual decision: the tool
    layer hands the nudge layer ``tool_success=False``, and the reactive layer
    declines to speak for it.
    """
    from trw_mcp.models.config import _reset_config
    from trw_mcp.state._nudge_messages import _context_reactive_message
    from trw_mcp.state.ceremony_progress import CeremonyState
    from trw_mcp.tools._ceremony_status_context import build_nudge_context

    monkeypatch.setenv("TRW_SESSION_ID", "truthful-seam")
    _reset_config()
    state = CeremonyState()

    failed = build_nudge_context("CHECKPOINT", tool_success=False)
    assert _context_reactive_message(failed, state) is None

    succeeded = build_nudge_context("CHECKPOINT", tool_success=True)
    assert _SUCCESS_CLAIM.search(str(_context_reactive_message(succeeded, state))), (
        "non-vacuity: the success path must still produce the reactive checkpoint message"
    )


def test_checkpoint_tool_threads_the_real_outcome_into_the_nudge_layer() -> None:
    """Structural pin: dropping the ``tool_success`` keyword restores the defect silently.

    ``NudgeContext.tool_success`` defaults to ``True``, so a future refactor that
    drops the keyword re-introduces success prose on a not-recorded call while
    every behavioural test that does not force the context pool keeps passing —
    exactly how this shipped. Asserted structurally for the same reason
    ``build_passed`` is (see ``test_nudge_step_attribution.py``).
    """
    import ast

    src = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"

    def _calls(module: str, func_name: str) -> list[ast.Call]:
        tree = ast.parse((src / "tools" / module).read_text(encoding="utf-8"))
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == func_name
        ]

    injector_calls = _calls("_orchestration_lifecycle.py", "append_ceremony_status_for_tool")
    assert injector_calls, "_orchestration_lifecycle.py no longer decorates its response"
    assert all({kw.arg for kw in call.keywords} >= {"tool_name", "tool_success"} for call in injector_calls), (
        "the injector call dropped tool_success — a not-recorded checkpoint would claim success again"
    )

    apply_calls = _calls("orchestration.py", "_apply_ceremony_status")
    checkpoint_calls = [call for call in apply_calls if any(kw.arg == "mark_checkpoint_first" for kw in call.keywords)]
    assert checkpoint_calls, "trw_checkpoint no longer gates the ceremony counter on the persisted outcome"
    for call in checkpoint_calls:
        keywords = {kw.arg: kw.value for kw in call.keywords}
        assert "tool_success" in keywords, "trw_checkpoint does not thread its outcome into the nudge layer"
        # Both the counter gate and the nudge gate must read the SAME source of
        # truth; a constant or a divergent expression is the regression.
        assert ast.dump(keywords["tool_success"]) == ast.dump(keywords["mark_checkpoint_first"]), (
            "tool_success and mark_checkpoint_first diverged — one of the two gates can now lie"
        )
