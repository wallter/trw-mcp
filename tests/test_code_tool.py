"""``trw_code``: one code-navigation tool with symbol and hint modes (PRD-CORE-300-FR12).

S10 folded four tools into ``trw_code``. Symbol reads the local code index built by
``trw-mcp code index``; hint returns prior learnings for each file plus the
trw-distill sidecar half when one exists. ``mode="search"`` (full-text lexical
search) was retired in 8.0 in favor of `rg`/`grep` and the `trw-distill` CLI and
now returns an actionable error. The tool is registered in every install,
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
from trw_mcp.tools._code_modes import LIVE_MODES, RETIRED_MODES
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
    from tests._served_app import served_app

    mcp = served_app()
    registered = {tool.name for tool in asyncio.run(mcp._list_tools())}
    assert "trw_code" in registered
    old = {"trw_" + name for name in ("code_search", "code_symbol", "before_edit_hint", "before_edit_hint_batch")}
    assert registered.isdisjoint(old)


def test_search_mode_is_retired_and_names_its_replacement(project: Path) -> None:
    """8.0: ``mode="search"`` returns an actionable error naming rg/grep and trw-distill."""
    (project / "app.py").write_text("def hidden() -> str:\n    return 'secret'\n", encoding="utf-8")

    result = _call(mode="search", query="hidden", repo_root=str(project))

    assert result["status"] == "failed"
    assert "rg" in result["error"]
    assert "trw-distill" in result["error"]
    assert "secret" not in str(result)


def test_search_mode_is_retired_even_without_a_query(project: Path) -> None:
    """The retirement error fires before argument validation, not after it."""
    result = _call(mode="search", repo_root=str(project))

    assert result["status"] == "failed"
    assert "retired" in result["error"]


@pytest.mark.parametrize("mode", sorted(RETIRED_MODES))
def test_every_retired_mode_is_refused_with_its_registry_error(project: Path, mode: str) -> None:
    """The refusal and the instruction-surface lint read one list (``_code_modes.RETIRED_MODES``)."""
    result = _call(mode=mode, repo_root=str(project))

    assert result == {"status": "failed", "error": RETIRED_MODES[mode]}


def test_unknown_mode_names_only_the_live_modes(project: Path) -> None:
    result = _call(mode="semantic", repo_root=str(project))

    assert result["status"] == "failed"
    assert result["error"] == f"unknown mode; use one of {', '.join(LIVE_MODES)}"
    assert not set(LIVE_MODES) & set(RETIRED_MODES)


def test_symbol_mode_finds_the_exact_definition(project: Path) -> None:
    (project / "pkg").mkdir()
    (project / "pkg" / "one.py").write_text("def duplicate() -> str:\n    return 'one'\n", encoding="utf-8")
    (project / "pkg" / "two.py").write_text("def duplicate_more() -> str:\n    return 'two'\n", encoding="utf-8")
    update_code_index(project)

    result = _call(mode="symbol", query="duplicate", repo_root=str(project))

    assert result["status"] == "ok"
    assert result["results"][0]["symbol"] == {"name": "duplicate", "kind": "function"}
    assert result["results"][0]["path"] == "pkg/one.py"


def test_symbol_needs_a_query(project: Path) -> None:
    result = _call(mode="symbol", repo_root=str(project))
    assert result["status"] == "failed"
    assert "query" in str(result["error"])


def test_an_unknown_mode_is_refused(project: Path) -> None:
    result = _call(mode="semantic", query="x")

    assert result["status"] == "failed"
    assert "semantic" not in str(result["error"])  # the caller's value is never repeated (E2E-INC-125)
    assert "symbol" in str(result["error"]) and "hint" in str(result["error"])  # the accepted modes are named


def test_the_default_mode_is_hint(project: Path) -> None:
    """8.0: with search retired, hint (not symbol) is the coherent default -- it needs
    no query and matches the pre-edit workflow every install's hooks already call."""
    result = _call(files="app.py")

    assert "status" not in result  # status appears only when every file failed
    assert len(result["hints"]) == 1


def test_unknown_argument_is_still_rejected_by_the_tools_input_schema(project: Path) -> None:
    """``mode`` accepting any ``str`` (rather than a closed ``Literal``) only widens what
    *mode itself* accepts; it does not widen the schema to accept unrelated keys."""
    with pytest.raises(Exception, match=r"unexpected_kwarg|Unexpected|additional"):
        _call(mode="hint", files="app.py", unexpected_kwarg="boom")


def test_hint_mode_takes_one_path_without_trw_distill(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR12 AC: no trw-distill -> the learnings half returns and the sidecar is named absent."""
    from trw_mcp.tools import _sidecar_substrate

    monkeypatch.setattr(_sidecar_substrate, "distill_installed", lambda: False)

    result = _call(mode="hint", files="app.py")

    (hint,) = result["hints"]
    assert hint["file_path"] == "app.py"
    assert "distill_hint" not in hint  # empty values are omitted
    assert hint["distill_status"] in _ABSENT_SIDECAR
    assert "learnings" not in hint and "learnings_count" not in hint


def test_hint_mode_takes_a_list_of_paths(project: Path) -> None:
    result = _call(mode="hint", files=["a.py", "b.py"])

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
        lambda _file_path, _repo_root: ([learning], "ok"),
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

    assert len(result["hints"]) == 2
    assert result["hints"][0] == {
        "file_path": "bad.py",
        "status": "failed",
        "error": "the hint could not be computed (RuntimeError)",
    }
    assert result["hints"][1]["file_path"] == "good.py"
    assert result["hints"][1].get("status") != "failed"


def test_a_hint_for_a_path_outside_the_project_fails_clearly(project: Path) -> None:
    result = _call(mode="hint", files=["../../../etc/passwd", "/etc/hosts", "ok.py"])

    outside, absolute, inside = result["hints"]
    assert outside["status"] == "failed" and "outside the project root" in outside["error"]
    assert absolute["status"] == "failed" and "outside the project root" in absolute["error"]
    assert "status" not in inside or inside["status"] != "failed"
    assert result["failed_count"] == 2 and "status" not in result


def test_only_outside_paths_make_the_whole_call_fail(project: Path) -> None:
    result = _call(mode="hint", files="../escape.py")

    assert result["status"] == "failed"
    assert len(result["hints"]) == 1 and "outside the project root" in result["hints"][0]["error"]


def test_a_hint_for_a_missing_file_inside_the_project_is_marked_not_found(project: Path) -> None:
    (project / "real.py").write_text("x = 1\n", encoding="utf-8")

    real, typo = _call(mode="hint", files=["real.py", "raelpy.py"])["hints"]

    assert "path_status" not in real
    assert typo["path_status"] == "not_found" and "does not exist" in typo["path_note"]


def test_a_symbol_with_no_match_says_why_and_still_has_results(project: Path) -> None:
    (project / "pkg").mkdir()
    (project / "pkg" / "w.py").write_text("class Widget:\n    def render(self):\n        return 1\n", encoding="utf-8")
    update_code_index(project)

    found = _call(mode="symbol", query="Widget", repo_root=str(project))
    method = _call(mode="symbol", query="render", repo_root=str(project))

    assert found["results"] and "message" not in found
    assert method["status"] == "ok" and method["results"] == []
    assert "a method is inside its class" in method["message"]


def test_the_docstring_names_the_index_step_and_the_method_limit() -> None:
    from tests._served_app import served_app

    mcp = served_app()
    tool = next(t for t in asyncio.run(mcp._list_tools()) if t.name == "trw_code")

    assert "trw-mcp code index" in (tool.description or "")
    assert "method" in (tool.description or "")


def test_a_malformed_path_fails_its_own_hint_not_the_batch(project: Path) -> None:
    good, bad = _call(mode="hint", files=["ok.py", "bad\x00name.py"])["hints"]

    assert bad["status"] == "failed" and "not a usable path" in bad["error"]
    assert good.get("status") != "failed"


def test_repo_root_is_confined_to_the_project_like_files_are(project: Path) -> None:
    for mode, extra in (("hint", {"files": ["hosts"]}), ("symbol", {"query": "x"})):
        result = _call(mode=mode, repo_root="/etc", **extra)

        assert result["status"] == "failed", f"{mode}: a repo_root outside the project was accepted"
        assert "/etc" not in str(result["error"]), "the refusal repeated the caller's value"
        assert "project root" in str(result["error"])


def test_repo_root_inside_the_project_is_still_accepted(project: Path) -> None:
    (project / "sub").mkdir()
    (project / "sub" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = _call(mode="hint", files=["a.py"], repo_root=str(project / "sub"))

    assert "status" not in result and result["hints"][0]["file_path"] == "a.py"


def test_a_huge_path_is_neither_echoed_in_the_reason_nor_repeated_in_full(project: Path) -> None:
    huge = "/" + "p" * 5000

    result = _call(mode="hint", files=[huge])

    entry = result["hints"][0]
    assert entry["status"] == "failed"
    assert "p" * 300 not in str(entry["error"])
    assert len(entry["file_path"]) <= 210
    assert len(str(result)) < 2000, "a 5000-character path must not come back twice"


def test_repo_root_cannot_escape_by_symlink_dotdot_or_a_nul_byte(project: Path, tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-elsewhere"  # the project IS tmp_path, so this is outside it
    outside.mkdir()
    (project / "link").symlink_to(outside)
    for bad in (str(project / "link"), str(project / ".." / outside.name), "../..", "a\x00b"):
        result = _call(mode="hint", files=["x.py"], repo_root=bad)

        assert result["status"] == "failed", f"repo_root {bad!r} was accepted"
        assert "link" not in str(result["error"]) and "elsewhere" not in str(result["error"])


def test_a_path_label_is_at_most_200_characters_including_the_ellipsis(project: Path) -> None:
    result = _call(mode="hint", files=["/" + "q" * 400])

    assert len(result["hints"][0]["file_path"]) == 200


def test_compact_response_states_shared_remediation_once_and_drops_internal_fields() -> None:
    """Every per-file field must help a pre-edit decision; shared text is hoisted once."""
    from trw_mcp.tools.code import _compact_response

    def raw(path: str, learnings: list[dict[str, object]]) -> dict[str, object]:
        return {
            "file_path": path,
            "tier": "public",
            "distill_hint": None,
            "distill_status": "target_not_in_sidecar",
            "distill_action": f"Batch sidecar does not cover {path!r} ... run: trw-distill ... --file {path}",
            "distill_sidecar_path": "/somewhere/before-edit-batch-abc.json",
            "distill_sidecar_sha": "abc",
            "learnings": learnings,
            "learnings_count": len(learnings),
            "learnings_status": "ok",
            "distill_as_of": None,
            "enrichment": {"tier_applied": "T1"},
            **({"transition_nudge": "Top learning for this file: L-1. ..."} if learnings else {}),
        }

    learning = {"id": "L-1", "summary": "keep the cache warm", "impact": 0.6, "tags": ["x"]}
    result = _compact_response([raw("a.py", []), raw("b.py", [learning])])

    assert result == {
        "hints": [
            {"file_path": "a.py", "distill_status": "target_not_in_sidecar"},
            {
                "file_path": "b.py",
                "distill_status": "target_not_in_sidecar",
                "learnings": [{"id": "L-1", "summary": "keep the cache warm"}],
            },
        ],
        "not_in_sidecar": result["not_in_sidecar"],
        "learnings_note": result["learnings_note"],
    }
    assert result["not_in_sidecar"].endswith("--files a.py,b.py")
    assert "by id" in result["learnings_note"]


def test_compact_response_hoists_a_repeated_action_once() -> None:
    from trw_mcp.tools.code import _compact_response

    same = {"distill_status": "sidecar_missing", "distill_action": "run trw-distill sidecar build", "learnings": []}
    result = _compact_response([{"file_path": "a.py", **same}, {"file_path": "b.py", **same}])

    assert result["distill_action"] == "run trw-distill sidecar build"
    assert all("distill_action" not in hint for hint in result["hints"])
