"""E2E-LEARN-VALIDATION: create/recall inputs are refused up front, with the field named and no residue."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools import _learn_impl, learning


def _tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    monkeypatch.setattr(learning, "get_config", lambda: TRWConfig(telemetry_enabled=False))
    monkeypatch.setattr(learning, "resolve_trw_dir", lambda: tmp_path / ".trw")
    server = FastMCP("learn-validation-test")
    learning.register_learning_tools(server)
    return get_tools_sync(server)


def _forbid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    for module, name in (
        (learning, "generate_learning_id"),
        (_learn_impl, "journal_accepted"),
        (learning, "adapter_store"),
    ):
        monkeypatch.setattr(module, name, Mock(side_effect=AssertionError(f"reached {name}")))


@pytest.mark.parametrize("summary", [None, "", "  \t"])
def test_missing_summary_is_refused_naming_the_field_with_no_residue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, summary: str | None
) -> None:
    tools = _tools(monkeypatch, tmp_path)
    _forbid_state(monkeypatch)
    result = tools["trw_learn"].fn(summary=summary, detail="x", metadata={"client_profile": "", "model_id": ""})
    assert result["status"] == "rejected" and result["reason"] == "missing_summary"
    assert "summary" in result["message"]
    assert not (tmp_path / ".trw").exists()  # no journal, dead_letter or consumed id


@pytest.mark.parametrize("impact", [1.5, -0.1, 99.0])
def test_out_of_range_impact_is_rejected_on_create_like_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, impact: float
) -> None:
    tools = _tools(monkeypatch, tmp_path)
    _forbid_state(monkeypatch)
    result = tools["trw_learn"].fn(
        summary="s", detail="d", impact=impact, metadata={"client_profile": "", "model_id": ""}
    )
    assert result["status"] == "rejected" and result["reason"] == "invalid_impact"
    assert "impact" in result["message"]
    assert not (tmp_path / ".trw").exists()


@pytest.mark.parametrize("status", ["bogus", "ACTIVE", "open", ""])
def test_recall_rejects_a_status_outside_the_stored_enum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str
) -> None:
    tools = _tools(monkeypatch, tmp_path)
    with pytest.raises(ToolError, match=r"Invalid status .*active.*resolved.*obsolete"):
        tools["trw_recall"].fn(query="*", status=status)


@pytest.mark.parametrize("impact", [1.5, -0.1, 99.0])
def test_out_of_range_impact_on_update_has_the_create_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, impact: float
) -> None:
    """INC-085: one rule, one answer: update rejects like create (status rejected + reason + message), changing nothing."""
    tools = _tools(monkeypatch, tmp_path)
    monkeypatch.setattr(learning, "adapter_update", Mock(side_effect=AssertionError("reached adapter_update")))
    result = tools["trw_learn"].fn(learning_id="L-abc123", impact=impact)
    assert result["status"] == "rejected"
    assert result["reason"] == "invalid_impact"
    assert "between 0 and 1" in result["message"]
    assert "nothing was changed" in result["message"]
    assert "error" not in result


def test_an_invalid_update_field_is_rejected_without_echoing_its_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """INC-085 sweep: trw-memory's parse_patch quotes the refused input; the tool answer names the field, not the value."""
    tools = _tools(monkeypatch, tmp_path)
    monkeypatch.setattr(learning, "adapter_update", Mock(side_effect=AssertionError("reached adapter_update")))
    secretish = "sk-ant-api03-" + "Q" * 40
    result = tools["trw_learn"].fn(learning_id="L-abc123", type=secretish)
    assert result["status"] == "rejected"
    assert result["reason"] == "invalid_type"
    assert "type" in result["message"]
    assert "sk-ant" not in result["message"]


@pytest.mark.parametrize(
    ("store", "reason"),
    [
        ({"status": "not_found", "error_type": "learning_not_found", "error": "no L-x"}, "learning_not_found"),
        ({"status": "conflict", "error": "changed concurrently"}, "conflict"),
        (
            {"status": "invalid", "error": "bad", "reason": "confidence_promotion_needs_basis"},
            "confidence_promotion_needs_basis",
        ),
    ],
)
def test_store_refusals_come_back_in_the_create_shape(store: dict[str, str], reason: str) -> None:
    from trw_mcp.tools._learn_update_impl import _as_rejection

    out = _as_rejection(store)
    assert out["status"] == "rejected"
    assert out["reason"] == reason
    assert out["message"] == store["error"]
    assert _as_rejection({"status": "updated", "learning_id": "L-x"}) == {"status": "updated", "learning_id": "L-x"}


def test_a_store_refusal_that_quotes_a_secret_is_redacted() -> None:
    """codex r1 KI: the store's own error text reaches the client through the secret detector."""
    from trw_mcp.tools._learn_update_impl import _as_rejection

    key = "sk-ant-api03-" + "Z" * 40
    out = _as_rejection({"status": "invalid", "error": f"Invalid source {key!r}: not allowed"})
    assert out["status"] == "rejected"
    assert "sk-ant-api03" not in out["message"]
