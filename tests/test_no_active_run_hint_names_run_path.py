"""PRD-CORE-233 FR03 — no-run diagnostics name a remedy the caller can execute.

Both no-active-run message sites used to say only "Call trw_init() to create a
run or trw_adopt_run(run_path=...) to resume one". Neither tool is granted to
any of the seven bundled sub-agents that hold ``trw_checkpoint``, so the printed
remedy was unexecutable for exactly the callers who hit the error. FR03 adds the
``run_path=`` parameter — which the tool already accepts — to both sites and
binds them to ONE shared remedy set so they cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from trw_mcp.exceptions import StateError


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Scratch project root with an empty runs tree and no pins."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    from trw_mcp.models.config import _reset_config, get_config

    _reset_config()
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    config = get_config()
    (tmp_path / config.runs_root).mkdir(parents=True, exist_ok=True)
    (tmp_path / config.trw_dir).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _resolver_error(session_id: str = "fr03-no-pin") -> StateError:
    """Trigger the ctx-aware no-pin refusal and return the raised StateError."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.state._paths import TRWCallContext, resolve_run_path

    ctx = TRWCallContext(
        session_id=session_id,
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )
    with pytest.raises(StateError) as exc_info:
        resolve_run_path(context=ctx)
    return exc_info.value


def _names_run_path_outside_adopt_run(text: str) -> bool:
    """True when ``run_path=`` is offered on its own, not only inside ``trw_adopt_run``.

    Pre-change both message sites already contained the substring ``run_path=``
    — but ONLY as part of ``trw_adopt_run(run_path=...)``, a tool none of the
    affected callers hold. A bare substring check would pass vacuously, so the
    adopt-run mention is masked out before looking.
    """
    return "run_path=" in text.replace("trw_adopt_run(run_path=...)", "trw_adopt_run(<run dir>)")


def test_shared_hint_names_run_path_as_an_executable_remedy() -> None:
    """FR03: ``_no_active_run_hint`` names ``run_path=`` alongside the tools."""
    from trw_mcp.tools._ceremony_runtime_helpers import _no_active_run_hint

    hint = _no_active_run_hint([])

    assert _names_run_path_outside_adopt_run(hint), f"hint must name the run_path parameter; got {hint!r}"
    # The ceremony tools are RETAINED, not substituted — the orchestrator-side
    # reader still needs them (FR03 boundary semantics).
    assert "trw_init" in hint
    assert "trw_adopt_run" in hint


def test_resolver_message_and_suggestion_name_run_path(isolated_project: Path) -> None:
    """FR03: the resolver raise site names ``run_path=`` in message AND suggestion."""
    exc = _resolver_error()

    assert _names_run_path_outside_adopt_run(str(exc)), f"resolver message must name run_path=; got {str(exc)!r}"
    assert _names_run_path_outside_adopt_run(exc.suggestion), f"suggestion must name run_path=; got {exc.suggestion!r}"
    assert "trw_init" in str(exc)
    assert "trw_adopt_run" in str(exc)


def test_both_message_sites_offer_the_same_remedy_set(isolated_project: Path) -> None:
    """FR03: neither site may offer an option the other omits."""
    from trw_mcp.state._no_active_run import NO_ACTIVE_RUN_REMEDIES
    from trw_mcp.tools._ceremony_runtime_helpers import _no_active_run_hint

    # Non-vacuity: the shared set is real, and at least one entry is the
    # remedy a checkpoint-only caller can actually execute.
    assert len(NO_ACTIVE_RUN_REMEDIES) >= 3
    assert any("run_path=" in remedy for remedy in NO_ACTIVE_RUN_REMEDIES)

    hint = _no_active_run_hint([])
    message = str(_resolver_error("fr03-consistency"))
    for remedy in NO_ACTIVE_RUN_REMEDIES:
        assert remedy in hint, f"hint omits shared remedy {remedy!r}"
        assert remedy in message, f"resolver message omits shared remedy {remedy!r}"


def test_candidate_run_advisory_is_preserved(isolated_project: Path) -> None:
    """Regression: the candidate-runs advisory sentence still follows the remedy."""
    from trw_mcp.tools._ceremony_runtime_helpers import _no_active_run_hint

    hint = _no_active_run_hint([{"run_path": "/x", "pin_key": "k"}])

    assert "will not auto-adopt another session's run" in hint
    assert _names_run_path_outside_adopt_run(hint)


def test_resolver_error_carries_machine_readable_reason(isolated_project: Path) -> None:
    """FR02 depends on distinguishing this refusal from other state faults."""
    from trw_mcp.state._no_active_run import NO_ACTIVE_RUN_REASON, is_no_active_run

    exc = _resolver_error("fr03-reason")

    assert exc.context.get("reason") == NO_ACTIVE_RUN_REASON
    assert is_no_active_run(exc) is True
    # PRD-CORE-141 structured fields are unchanged (FR03 boundary semantics).
    assert exc.context.get("pin_key") == "fr03-reason"
    assert exc.context.get("project_root") == str(isolated_project)


def test_other_state_errors_are_not_classified_as_no_active_run(tmp_path: Path) -> None:
    """Non-vacuity for ``is_no_active_run``: it must not match every StateError."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.state._no_active_run import is_no_active_run

    assert is_no_active_run(StateError("something else", path=str(tmp_path))) is False
