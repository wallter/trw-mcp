"""E2E-INC-099: trw_prd_validate says "not a PRD" for an empty, binary or notes-only file instead of scoring it."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError


@pytest.fixture
def validate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from trw_mcp.tools import requirements as _req
    from trw_mcp.tools._prd_validate_tool import run_prd_validate

    monkeypatch.setattr(_req, "resolve_project_root", lambda: tmp_path)

    def _run(name: str, data: bytes) -> object:
        path = tmp_path / name
        path.write_bytes(data)
        return run_prd_validate(prd_path=str(path), fast=True)

    return _run


@pytest.mark.parametrize(
    ("name", "data", "fragment"),
    [
        ("empty.md", b"", "is empty"),
        ("blank.md", b"  \n\t\n", "is empty"),
        ("junk.bin", bytes(range(256)) * 4, "is not UTF-8 text"),
        ("notes.md", b"just some notes\nabout nothing in particular\n", "no PRD frontmatter"),
        ("notes2.md", b"- a list\n- of notes\n#hashtag not a heading\n", "no markdown headings"),
    ],
)
def test_a_file_that_is_not_a_prd_is_named_as_such(validate, name: str, data: bytes, fragment: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(StateError, match=r"not a PRD: .*" + fragment):
        validate(name, data)


def test_markdown_with_sections_is_still_scored(validate) -> None:  # type: ignore[no-untyped-def]
    """A PRD missing its frontmatter is still a PRD draft: it gets the normal verdict, not a refusal."""
    result = validate("draft.md", b"# Draft\n\n## Problem Statement\n\nSomething.\n")
    assert isinstance(result, dict)
    assert "valid" in result or "quality_tier" in result
