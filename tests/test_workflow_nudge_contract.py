"""Emitted workflow advice respects general tasks and shared-worktree ownership.

These are instruction-content contracts, not evidence of model effectiveness.
Only random choice is controlled; the resolver and bundled YAML remain real.
"""

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state import _nudge_content
from trw_mcp.state.ceremony_progress import CeremonyState
from trw_mcp.tools._ceremony_status_pool import resolve_pool_content


@pytest.mark.unit
@pytest.mark.parametrize("phase", ["early", "review", "deliver"])
def test_failing_test_advice_is_conditional(phase: str, monkeypatch: pytest.MonkeyPatch) -> None:
    message = _render("read_test_first", phase, monkeypatch)
    assert "If the task includes a failing test" in message


@pytest.mark.unit
def test_targeted_green_does_not_replace_required_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _render("verify_with_targeted_check", "validate", monkeypatch)
    assert "For a bug fix" in message
    assert "required validation" in message
    assert "green there is your proof" not in message


@pytest.mark.unit
@pytest.mark.parametrize("phase", ["implement", "review", "deliver"])
def test_scope_advice_preserves_other_contributors_edits(phase: str, monkeypatch: pytest.MonkeyPatch) -> None:
    message = _render("minimize_changes", phase, monkeypatch)
    assert "your own diff" in message
    assert "preserve unrelated work" in message
    assert "diff every other edit back out" not in message


@pytest.mark.unit
def test_root_cause_advice_is_conditional(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _render("explain_root_cause", "implement", monkeypatch)
    assert "For a bug fix" in message


@pytest.mark.unit
@pytest.mark.parametrize("phase", ["implement", "review", "deliver"])
def test_test_integrity_does_not_ban_authorized_test_work(phase: str, monkeypatch: pytest.MonkeyPatch) -> None:
    message = _render("dont_modify_tests", phase, monkeypatch)
    assert "task permits" in message
    assert "do not weaken assertions" in message
    assert "Do not modify existing test files" not in message


def _render(message_id: str, phase: str, monkeypatch: pytest.MonkeyPatch) -> str:
    class SelectMessage:
        def choice(self, candidates: list[dict[str, str]]) -> dict[str, str]:
            return next(candidate for candidate in candidates if candidate["id"] == message_id)

    monkeypatch.setattr(_nudge_content, "_RNG", SelectMessage())
    result = resolve_pool_content("workflow", CeremonyState(phase=phase), TRWConfig(), None, Path("unused"))
    assert isinstance(result, str)
    return result
