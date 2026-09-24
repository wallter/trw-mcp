"""PRD-CORE-291-FR02: ``trw_learn`` and ``trw_learn_update`` are one tool.

No learning_id creates; a learning_id updates. The old update tool's name is gone
with no alias, and arguments that belong to only one mode are refused in the
other. A removed flat argument (the old ``fields``/``feedback``) is fastmcp's
unknown-keyword error, never a silent no-op (NFR01).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.exceptions import ValidationError as FastMCPValidationError

from tests.conftest import extract_tool_fn, get_tools_sync


def _server() -> FastMCP:
    from trw_mcp.tools.learning import register_learning_tools

    server = FastMCP("test")
    register_learning_tools(server)
    return server


def _learn() -> Any:
    return extract_tool_fn(_server(), "trw_learn")


def test_only_one_learning_write_tool_is_registered() -> None:
    names = set(get_tools_sync(_server()))
    assert "trw_learn" in names
    assert "trw_learn_update" not in names


@pytest.mark.parametrize(
    ("kwargs", "mode"),
    [
        ({"summary": "s", "detail": "d"}, "create"),
        ({"learning_id": "L-1", "status": "resolved"}, "update"),
    ],
)
def test_trw_learn_merged_create_and_update(
    kwargs: dict[str, object], mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One registration routes to the create path or the update path by learning_id alone."""
    calls: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "trw_mcp.tools._learn_impl.execute_learn",
        lambda **kw: calls.setdefault("create", kw) and {"status": "recorded", "learning_id": "L-new"},
    )

    def _update(trw_dir: Path, **kw: Any) -> dict[str, str]:
        calls["update"] = kw
        return {"status": "no_changes", "learning_id": str(kw["learning_id"])}

    monkeypatch.setattr("trw_mcp.tools.learning.adapter_update", _update)

    _learn()(**kwargs)

    assert set(calls) == {mode}
    if mode == "update":
        assert calls["update"]["learning_id"] == "L-1"
        assert calls["update"]["status"] == "resolved"
        # Partial update: nothing the caller did not name is touched.
        assert calls["update"]["summary"] is None and calls["update"]["tags"] is None


def test_update_mode_takes_typed_fields_through_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "trw_mcp.tools.learning.adapter_update", lambda _d, **kw: seen.update(kw) or {"status": "no_changes"}
    )

    _learn()(learning_id="L-1", confidence="high", metadata={"supersedes": "L-0", "expires": "2027-01-01"})

    assert (seen["supersedes"], seen["expires"], seen["confidence"]) == ("L-0", "2027-01-01", "high")
    assert "feedback" not in seen  # PRD-CORE-293: no reward-loop signal reaches the adapter


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"summary": "s", "detail": "d", "status": "resolved"}, "message"),  # status without an id
        ({"learning_id": "L-1", "evidence": ["x"]}, "error"),  # create-only argument in update mode
        ({"learning_id": "L-1", "scope": "user"}, "error"),
        ({"learning_id": "L-1", "metadata": {"source_identity": "x"}}, "error"),  # create-only key
        ({"summary": "s", "detail": "d", "metadata": {"supersedes": "L-0"}}, "message"),  # update-only key
        ({"learning_id": "L-1", "metadata": {"feedback": "helpful"}}, "error"),  # PRD-CORE-293: key removed
    ],
)
def test_arguments_of_the_other_mode_are_refused(
    kwargs: dict[str, object], field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path)
    out = _learn()(**kwargs)
    assert out["status"] in {"invalid", "rejected"}
    assert out[field]


@pytest.mark.parametrize("removed", ["fields", "feedback", "supersedes", "reverify_anchors", "source_type"])
async def test_a_removed_flat_argument_is_a_tool_error(removed: str) -> None:
    """NFR01: the old flat names fail loudly through the real MCP call path."""
    with pytest.raises((ToolError, FastMCPValidationError), match="Unexpected keyword argument"):
        await _server().call_tool("trw_learn", {"learning_id": "L-1", removed: "x"})
