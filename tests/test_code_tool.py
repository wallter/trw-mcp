"""``trw_code``: one code-navigation tool with search, symbol and hint modes (PRD-CORE-300-FR12).

S10 folds four tools into ``trw_code``. Search and symbol read the local code index
built by ``trw-mcp code index``; hint returns prior learnings for each file plus the
trw-distill sidecar half when one exists. The tool is registered in every install,
including one without trw-distill, and under the reviewer role its hint mode writes
nothing (no exposure rows, no telemetry, no transition-nudge state).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from trw_mcp.code_index.update import update_code_index
from trw_mcp.models.config import reload_config
from trw_mcp.tools._learnings_collector import LearningSummary
from trw_mcp.tools.code import MAX_HINT_FILES, register_code_tools

#: The absent-sidecar statuses: none of them carries a hint, each says why.
_ABSENT_SIDECAR = {"tier_required", "sidecar_missing", "no_git_sha", "no_repo_root"}


def _call(**arguments: object) -> dict[str, Any]:
    """Drive the REGISTERED tool over MCP, so the assertion covers the wire schema."""
    server = FastMCP("code-tool-test")
    register_code_tools(server)

    async def _run() -> dict[str, Any]:
        async with Client(server) as client:
            result = await client.call_tool("trw_code", arguments)
            data = result.structured_content
            assert isinstance(data, dict)
            return data

    return asyncio.run(_run())


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".trw").mkdir()
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    reload_config(None)
    from trw_mcp.state import _surface_role

    _surface_role.reset_surface_role_state()
    yield tmp_path
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    reload_config(None)
    _surface_role.reset_surface_role_state()


def test_trw_code_is_registered_and_the_four_old_tools_are_not() -> None:
    from trw_mcp.server import mcp

    registered = {tool.name for tool in asyncio.run(mcp._list_tools())}
    assert "trw_code" in registered
    old = {"trw_" + name for name in ("code_search", "code_symbol", "before_edit_hint", "before_edit_hint_batch")}
    assert registered.isdisjoint(old)


def test_search_mode_reports_a_missing_index(project: Path) -> None:
    (project / "app.py").write_text("def hidden() -> str:\n    return 'secret'\n", encoding="utf-8")

    result = _call(mode="search", query="hidden", repo_root=str(project))

    assert result["status"] == "failed"
    assert result["error_code"] == "index_missing"
    assert "secret" not in str(result)


def test_search_mode_returns_ranked_snippets_after_index(project: Path) -> None:
    (project / "app.py").write_text("def find_me() -> str:\n    return 'bounded snippet value'\n", encoding="utf-8")
    update_code_index(project)

    result = _call(mode="search", query="bounded snippet", top_k=3, repo_root=str(project))

    assert result["status"] == "ok"
    assert result["results"][0]["path"] == "app.py"


def test_search_mode_defaults_repo_root_to_the_project(project: Path) -> None:
    (project / "app.py").write_text("def find_me() -> str:\n    return 'defaulted root'\n", encoding="utf-8")
    update_code_index(project)

    result = _call(mode="search", query="defaulted root")

    assert result["status"] == "ok"
    assert result["results"][0]["path"] == "app.py"


def test_symbol_mode_finds_the_exact_definition(project: Path) -> None:
    (project / "pkg").mkdir()
    (project / "pkg" / "one.py").write_text("def duplicate() -> str:\n    return 'one'\n", encoding="utf-8")
    (project / "pkg" / "two.py").write_text("def duplicate_more() -> str:\n    return 'two'\n", encoding="utf-8")
    update_code_index(project)

    result = _call(mode="symbol", query="duplicate", repo_root=str(project))

    assert result["status"] == "ok"
    assert result["results"][0]["symbol"] == {"name": "duplicate", "kind": "function"}
    assert result["results"][0]["path"] == "pkg/one.py"


def test_search_and_symbol_need_a_query(project: Path) -> None:
    for mode in ("search", "symbol"):
        result = _call(mode=mode, repo_root=str(project))
        assert result["status"] == "failed"
        assert "query" in str(result["error"])


def test_an_unknown_mode_is_refused(project: Path) -> None:
    result = _call(mode="semantic", query="x")

    assert result["status"] == "failed"
    assert "semantic" in str(result["error"])


def test_hint_mode_takes_one_path_without_trw_distill(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR12 AC: no trw-distill -> the learnings half returns and the sidecar is named absent."""
    from trw_mcp.tools import _sidecar_substrate

    monkeypatch.setattr(_sidecar_substrate, "distill_installed", lambda: False)

    result = _call(mode="hint", files="app.py")

    assert result["count"] == 1
    (hint,) = result["hints"]
    assert hint["file_path"] == "app.py"
    assert hint["distill_hint"] is None
    assert hint["distill_status"] in _ABSENT_SIDECAR
    assert isinstance(hint["learnings"], list)


def test_hint_mode_takes_a_list_of_paths(project: Path) -> None:
    result = _call(mode="hint", files=["a.py", "b.py"])

    assert result["count"] == 2
    assert [hint["file_path"] for hint in result["hints"]] == ["a.py", "b.py"]


def test_hint_mode_refuses_no_files_and_too_many(project: Path) -> None:
    assert _call(mode="hint")["status"] == "failed"
    over = [f"f{i}.py" for i in range(MAX_HINT_FILES + 1)]
    result = _call(mode="hint", files=over)
    assert result["status"] == "failed"
    assert str(MAX_HINT_FILES) in str(result["error"])


def _snapshot(trw_dir: Path) -> dict[str, str]:
    return {
        str(path.relative_to(trw_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in trw_dir.rglob("*")
        if path.is_file()
    }


def _with_a_learning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every file one learning, so exposure and the nudge have something to record."""
    learning = LearningSummary(id="L-code1", summary="edit app.py with care", impact=0.9)
    monkeypatch.setattr(
        "trw_mcp.tools._before_edit_hint_core._collect_learnings",
        lambda _file_path: [learning],
    )


def test_hint_mode_records_exposure_for_an_agent(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-vacuity for the reviewer test below: the same call writes as an agent."""
    _with_a_learning(monkeypatch)
    before = _snapshot(project / ".trw")

    _call(mode="hint", files="app.py")

    assert _snapshot(project / ".trw") != before


def test_hint_mode_writes_nothing_under_the_reviewer_role(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR12 / NFR02: a reviewer's hint records no exposure, telemetry or nudge state."""
    _with_a_learning(monkeypatch)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    reload_config(None)
    before = _snapshot(project / ".trw")

    result = _call(mode="hint", files=["app.py", "b.py"])

    assert [hint["learnings"][0]["id"] for hint in result["hints"]] == ["L-code1", "L-code1"]
    assert "transition_nudge" not in result["hints"][0]
    assert _snapshot(project / ".trw") == before


def test_one_failing_file_does_not_cost_the_others_their_hints(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools import code

    real = code.compute_before_edit_hint

    def flaky(*, file_path: str, repo_root: str | None = None) -> Any:
        if file_path == "bad.py":
            raise RuntimeError("boom")
        return real(file_path=file_path, repo_root=repo_root)

    monkeypatch.setattr(code, "compute_before_edit_hint", flaky)

    result = _call(mode="hint", files=["bad.py", "good.py"])

    assert result["count"] == 2
    assert result["hints"][0] == {"file_path": "bad.py", "status": "failed", "error": "boom"}
    assert result["hints"][1]["file_path"] == "good.py"
    assert "learnings" in result["hints"][1]
