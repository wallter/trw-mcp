"""A fault inside a blocking deliver gate must block, never pass (DISPATCH-FAIL-CLOSED-BUILD-SCOPE).

CONSTITUTION 1.a: a code bug must not turn into a pass on a blocking gate. Each test injects an exception into the gate's
own machinery and asserts the verdict is a block that names the exception TYPE and the gate site, never the message
(the message can carry a path or a secret), and that the documented escape (allow_unverified + an acceptable-failure
record) is what the text offers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.tools import _delivery_build_gates as bg
from trw_mcp.tools import _delivery_helpers as dh

_SECRET = "s3cr3t-token-abc-do-not-leak"


def _boom(*_a: object, **_k: object) -> object:
    raise ValueError(_SECRET)


def _assert_named_block(text: str | None, site: str, remedy: str = "allow_unverified") -> None:
    assert text is not None, "a faulting gate returned no verdict (fail-open)"
    assert text.startswith("Delivery blocked:"), text  # verdict first, diagnostics after
    assert "ValueError" in text and site in text
    assert _SECRET not in text  # never the exception message
    assert remedy in text
    if remedy == "allow_unverified":
        assert "acceptable-failure" in text


def test_core_build_check_fault_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bg, "_degenerate_build_evidence_reason", _boom)
    build_warning, _ = bg._check_build_and_work_events([{"event": "file_modified"}])
    _assert_named_block(build_warning, "build-check")


def test_core_build_check_fault_keeps_the_premature_guard_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bg, "_build_passed", _boom)
    build_warning, _ = bg._check_build_and_work_events([{"event": "run_init"}])
    _assert_named_block(build_warning, "build-check")


def test_review_scope_fault_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    monkeypatch.setattr(dh, "_count_file_modified_current_session", _boom)
    text = dh._check_review_file_count_gate(tmp_path, [{"event": "file_modified"}])
    _assert_named_block(text, "review-scope", "trw_review()")
    assert "allow_unverified" not in str(text)  # review-scope is a NO_ESCAPE gate: never offer an override it ignores


def test_review_scope_honest_paths_are_unchanged(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    assert dh._check_review_file_count_gate(tmp_path, []) is None


# --- E2E-INC-115 (b): no registered change-evidence writer -> uncomputable, never zero -------------------------------
from trw_mcp.client_profiles.change_evidence import CHANGE_EVIDENCE_WRITERS
from trw_mcp.tools._delivery_event_checks import unpinned_session_changed_files


def _trw_dir(tmp_path: Path) -> Path:
    trw = tmp_path / ".trw"
    (trw / "context").mkdir(parents=True)
    (trw / "context" / "ceremony-state.json").write_text('{"session_started": true}', encoding="utf-8")
    return trw


@pytest.mark.parametrize("profile", ["cursor-ide", "codex", "cursor-cli", "unknown", "some-new-client"])
def test_a_client_without_a_writer_cannot_show_zero_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, profile: str
) -> None:
    monkeypatch.setenv("TRW_CLIENT_PROFILE", profile)
    for var in ("CLAUDE_CODE_VERSION", "CLAUDE_CODE_ENTRYPOINT", "CODEX_CLI_VERSION", "CURSOR_TRACE_ID"):
        monkeypatch.delenv(var, raising=False)
    assert profile not in CHANGE_EVIDENCE_WRITERS
    assert unpinned_session_changed_files(_trw_dir(tmp_path), "sid") is None


def test_a_client_with_a_writer_keeps_the_honest_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "claude-code")
    assert unpinned_session_changed_files(_trw_dir(tmp_path), "sid") == 0


def test_the_environment_signal_identifies_a_writer_when_no_profile_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TRW_CLIENT_PROFILE", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    assert unpinned_session_changed_files(_trw_dir(tmp_path), "sid") == 0


@pytest.mark.parametrize(("profile", "blocked"), [("cursor-ide", True), ("codex", True), ("claude-code", False)])
def test_unpinned_deliver_with_no_build_and_no_records_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, profile: str, blocked: bool
) -> None:
    """swarm-e2e's cursor repro: edits made, no hook wrote them, no build recorded. Only a client whose hook exists
    may read the empty record set as 'nothing changed'."""
    from typing import Any, cast

    from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

    monkeypatch.setenv("TRW_CLIENT_PROFILE", profile)
    monkeypatch.setenv("TRW_SESSION_ID", "sid-115")
    trw = _trw_dir(tmp_path)
    (trw / "context" / "ceremony-state.json").write_text(
        '{"session_started": true, "session_build_results": {}}', encoding="utf-8"
    )
    results: dict[str, Any] = {}
    assert evaluate_build_authority(cast("Any", results), [], None, trw, False, "") is blocked


def test_a_failing_logger_does_not_turn_the_no_writer_block_into_a_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from trw_mcp.tools import _delivery_event_checks as ec

    monkeypatch.setenv("TRW_CLIENT_PROFILE", "cursor-ide")
    monkeypatch.setattr(ec.logger, "warning", _boom)
    assert ec.unpinned_session_changed_files(_trw_dir(tmp_path), "sid") is None


def test_an_unidentifiable_client_has_no_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.client_profiles import change_evidence as ce

    monkeypatch.setattr("trw_mcp.tools._client_detection.resolve_client_profile", _boom)
    assert ce.active_client_writes_change_evidence() is False


def test_every_builtin_profile_but_claude_code_is_uncomputable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The writer registry is DEFAULT-DENY: a profile counts only by being listed, so a new profile, a client that
    edits through a shell, or one whose hook merely logs (copilot, opencode, grok, antigravity-cli, cursor-cli)
    fails closed without anyone remembering to add it to a deny list."""
    from trw_mcp.models.config._profiles import builtin_client_ids

    ids = builtin_client_ids()
    assert "cursor-ide" in ids and "codex" in ids and "claude-code" in ids  # the enumeration is real
    assert CHANGE_EVIDENCE_WRITERS == frozenset({"claude-code"}), "a new writer needs a deliberate test-visible change"
    for profile in ids:
        monkeypatch.setenv("TRW_CLIENT_PROFILE", profile)
        expected = 0 if profile in CHANGE_EVIDENCE_WRITERS else None
        assert unpinned_session_changed_files(_trw_dir(tmp_path / profile), "sid") == expected, profile
