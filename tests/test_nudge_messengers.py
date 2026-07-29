"""PRD-CORE-145 messenger acceptance and compatibility tests."""

from __future__ import annotations

from pathlib import Path
from typing import get_args
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.ceremony_nudge import CeremonyState, compute_nudge_contextual
from trw_mcp.tools._ceremony_status import append_ceremony_status


def _configured_trw_dir(root: Path, messenger: str | None) -> Path:
    trw_dir = root / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    lines = ["nudge_enabled: true"]
    if messenger is not None:
        lines.append(f"nudge_messenger: {messenger}")
    (trw_dir / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return trw_dir


def test_config_rejects_unknown_messenger() -> None:
    with pytest.raises(ValidationError, match="nudge_messenger") as exc_info:
        TRWConfig(nudge_messenger="not-a-messenger")  # type: ignore[arg-type]
    message = str(exc_info.value)
    for allowed in ("standard", "minimal", "contextual", "contextual_action"):
        assert allowed in message


def test_config_rejects_retired_learning_injection_messenger() -> None:
    """PRD-CORE-241-FR07: the retired arm is a validation error, not a silent route.

    iter-22 measured it at 50.0% vs 66.7% for plain trw-full (n=30, p=0.1527);
    the migration target named in the error's allowed set is ``contextual``.
    """
    with pytest.raises(ValidationError, match="nudge_messenger"):
        TRWConfig(nudge_messenger="learning_injection")  # type: ignore[arg-type]

    from trw_mcp.models.config._fields_ceremony import NudgeMessengerLiteral

    allowed = set(get_args(NudgeMessengerLiteral))
    assert "learning_injection" not in allowed
    assert "contextual" in allowed


def test_config_still_accepts_the_live_messengers() -> None:
    for live in ("standard", "minimal", "contextual", "contextual_action"):
        assert TRWConfig(nudge_messenger=live).effective_nudge_messenger == live  # type: ignore[arg-type]


def test_config_none_resolves_standard() -> None:
    assert TRWConfig(nudge_messenger=None).effective_nudge_messenger == "standard"


def test_workspace_yaml_round_trip_preserves_messenger(tmp_path: Path) -> None:
    from trw_mcp.tools._ceremony_status import _load_config_for_trw_dir

    trw_dir = _configured_trw_dir(tmp_path, "contextual")
    loaded = _load_config_for_trw_dir(trw_dir)
    assert loaded.nudge_messenger == "contextual"
    assert loaded.effective_nudge_messenger == "contextual"


def test_standard_default_matches_pre_core145_snapshot(tmp_path: Path) -> None:
    unset_dir = _configured_trw_dir(tmp_path / "unset", None)
    explicit_dir = _configured_trw_dir(tmp_path / "explicit", "standard")
    expected = (
        (Path(__file__).parent / "fixtures" / "nudge_pre_PRD_CORE_145.txt").read_text(encoding="utf-8").rstrip("\n")
    )

    with (
        patch("trw_mcp.state.ceremony_nudge._select_nudge_pool", return_value="workflow"),
        patch("trw_mcp.tools._ceremony_status._has_cached_learning_weights", return_value=False),
        patch("trw_mcp.state._nudge_content.load_pool_message", return_value=expected),
    ):
        unset = append_ceremony_status({"status": "ok"}, unset_dir)
        explicit = append_ceremony_status({"status": "ok"}, explicit_dir)

    assert unset["nudge_content"] == expected
    assert explicit["nudge_content"] == expected


def test_contextual_render_is_bounded_and_fail_open(tmp_path: Path) -> None:
    """PRD-CORE-241-FR09: the surviving arm keeps the bound and the fail-open path."""
    trw_dir = _configured_trw_dir(tmp_path, "contextual")
    state = CeremonyState(session_started=True, phase="implement")
    recall_context = type("RecallContext", (), {"modified_files": ["src/parser.py"]})()
    long_summary = "relevant parser invariant " * 20

    with (
        patch("trw_mcp.state.recall_context.build_recall_context", return_value=recall_context),
        patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[{"id": "L-parser", "summary": long_summary, "score": 0.92}],
        ),
    ):
        rendered = compute_nudge_contextual(state, trw_dir)

    assert "L-parser" in rendered
    assert "NEXT: " in rendered
    assert len(rendered) <= 400

    with patch("trw_mcp.state.recall_context.build_recall_context", side_effect=RuntimeError("boom")):
        fallback = compute_nudge_contextual(state, trw_dir)
    assert isinstance(fallback, str)
    assert len(fallback) <= 200


def test_candidate_selector_rejects_below_threshold_match(tmp_path: Path) -> None:
    """The shared ``_select_learning_injection_candidate`` score floor survives FR07.

    Retargeted from the retired ``learning_injection`` messenger onto the
    ``contextual`` arm, which calls the same candidate selector.
    """
    trw_dir = _configured_trw_dir(tmp_path, "contextual")
    state = CeremonyState(session_started=True, phase="implement")
    recall_context = type("RecallContext", (), {"modified_files": ["src/parser.py"]})()
    with (
        patch("trw_mcp.state.recall_context.build_recall_context", return_value=recall_context),
        patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[{"id": "L-low", "summary": "weak match", "score": 0.69}],
        ),
    ):
        rendered = compute_nudge_contextual(state, trw_dir)
    assert "L-low" not in rendered
    assert "Watch-out" not in rendered
