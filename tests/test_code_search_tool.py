from __future__ import annotations

from pathlib import Path

from trw_mcp.code_index.update import update_code_index
from trw_mcp.tools.code_search import trw_code_search, trw_code_symbol


def test_trw_code_search_returns_structured_failure_for_missing_index(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def hidden() -> str:\n    return 'secret'\n", encoding="utf-8")

    result = trw_code_search(repo_root=str(tmp_path), query="hidden", mode="lexical", top_k=10)

    assert result["status"] == "failed"
    assert result["error_code"] == "missing_index"
    assert "results" in result
    assert result["results"] == []
    assert "secret" not in str(result)


def test_trw_code_search_returns_ranked_capped_snippets_after_index(tmp_path: Path) -> None:
    body = "def find_me() -> str:\n    return 'bounded snippet value'\n"
    (tmp_path / "app.py").write_text(body, encoding="utf-8")
    update_code_index(tmp_path)

    result = trw_code_search(repo_root=str(tmp_path), query="bounded snippet", mode="lexical", top_k=3)

    assert result["status"] == "ok"
    assert result["results"][0]["path"] == "app.py"
    assert result["results"][0]["line_range"] == {"start": 1, "end": 2}
    assert result["results"][0]["symbol"] == {"name": "find_me", "kind": "function"}
    assert result["results"][0]["score"] > 0
    assert len(result["results"][0]["snippet"]) <= 800


def test_trw_code_search_rejects_unsafe_path_filters(tmp_path: Path) -> None:
    update_code_index(tmp_path)

    result = trw_code_search(repo_root=str(tmp_path), query="anything", mode="lexical", path="../outside.py")

    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_path"


def test_trw_code_symbol_returns_exact_match_with_disambiguating_location(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "one.py").write_text("def duplicate() -> str:\n    return 'one'\n", encoding="utf-8")
    (tmp_path / "pkg" / "two.py").write_text("def duplicate_more() -> str:\n    return 'two'\n", encoding="utf-8")
    update_code_index(tmp_path)

    result = trw_code_symbol(repo_root=str(tmp_path), symbol="duplicate", top_k=5)

    assert result["status"] == "ok"
    assert result["results"][0]["symbol"] == {"name": "duplicate", "kind": "function"}
    assert result["results"][0]["path"] == "pkg/one.py"
    assert result["results"][0]["line_range"] == {"start": 1, "end": 2}


def test_trw_code_search_skill_has_valid_frontmatter_and_usage_text() -> None:
    skill_path = Path(__file__).parents[1] / "src" / "trw_mcp" / "data" / "skills" / "trw-code-search" / "SKILL.md"

    content = skill_path.read_text(encoding="utf-8")

    assert content.startswith("---\n")
    assert "name: trw-code-search" in content
    assert "trw_code_index_update" in content
    assert "trw_code_search" in content
    assert "trw_code_symbol" in content

    # This used to assert the skill contained the literal
    # `pytest tests/test_code_chunking.py`. The skill was deliberately rewritten to
    # make no assumption about the host repository's language, test runner or build
    # system, which is what a BUNDLED skill must do — it ships to arbitrary user
    # projects, and CLAUDE.md's own rule is to "choose validation commands from
    # repo/package config, not framework defaults". The old assertion pinned this
    # monorepo's pytest invocation into a portable artifact, so it went red the
    # moment the artifact became portable, blaming the fix rather than the defect.
    #
    # Assert the PROPERTY the skill must now have instead of the string it must
    # not: it tells the reader to use the host project's own toolchain.
    assert "repository you are working in" in content, (
        "the bundled skill must direct the reader to the host project's toolchain"
    )
    assert "pytest tests/test_code_chunking.py" not in content, (
        "a bundled skill must not hardcode this monorepo's test invocation"
    )
