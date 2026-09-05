"""Sub-scope sync fits TRW's own section to the budget without touching user bytes.

PRD-FIX-123-FR01 made an over-budget merge a refusal instead of a truncation.
At default config the generated section is 94 lines and ``sub_claude_md_max_lines``
is 50, so sub-scope sync refused its OWN output and could never create a file —
even in an empty directory where ``current_non_generated_bytes`` was 0.

These tests pin the two halves of the rule TRW is allowed to apply:
TRW may shrink its own section to fit; it may never shrink the user's.

Project-root / trw-dir isolation comes from the autouse conftest
``_isolate_trw_dir`` fixture; the sync path late-resolves ``trw_mcp.state._paths``
at call time, so nothing extra is bound here.
"""

from __future__ import annotations

from pathlib import Path

from tests._tools_learning_shared import _get_tools
from trw_mcp.models.config import get_config

_USER_LINES = 40


def _sync_sub(sub_dir: Path) -> dict[str, object]:
    result = _get_tools()["trw_instructions_sync"].fn(scope="sub", target_dir=str(sub_dir))
    return dict(result)


class TestSubScopeSectionBudget:
    """The generated section is budgeted; user content is never cut to make room."""

    def test_empty_sub_dir_gets_the_pointer_form_within_budget(self, tmp_path: Path) -> None:
        """An empty directory is written, not refused: nothing there needed protecting."""
        sub_dir = tmp_path / "src" / "module"
        sub_dir.mkdir(parents=True)

        result = _sync_sub(sub_dir)

        assert result["status"] == "synced", result
        assert result.get("refusals") is None, result
        content = (sub_dir / "CLAUDE.md").read_text(encoding="utf-8")
        assert "trw:start" in content
        assert len(content.split("\n")) <= get_config().sub_claude_md_max_lines, content
        # A pointer that does not name the carrier it points at is not a pointer.
        assert "`CLAUDE.md`" in content
        assert "trw_session_start()" in content

    def test_user_content_plus_pointer_fits_and_stays_byte_identical(self, tmp_path: Path) -> None:
        """40 lines of user content survive the write byte-for-byte and still fit."""
        sub_dir = tmp_path / "src" / "module"
        sub_dir.mkdir(parents=True)
        target = sub_dir / "CLAUDE.md"
        user_text = "\n".join(f"# hand-written note {i}" for i in range(_USER_LINES)) + "\n"
        target.write_text(user_text, encoding="utf-8")

        result = _sync_sub(sub_dir)

        assert result["status"] == "synced", result
        content = target.read_text(encoding="utf-8")
        limit = get_config().sub_claude_md_max_lines
        assert len(content.split("\n")) <= limit, content
        assert "trw:start" in content
        # Byte-identity of the user's region, not merely "the strings appear".
        user_region = content.split("<!-- TRW AUTO-GENERATED")[0]
        assert user_region.encode("utf-8") == (user_text + "\n").encode("utf-8"), repr(user_region)

    def test_user_content_over_budget_is_refused_not_truncated(self, tmp_path: Path) -> None:
        """When the OVERFLOW is the user's bytes, the write is refused and nothing changes."""
        sub_dir = tmp_path / "src" / "module"
        sub_dir.mkdir(parents=True)
        target = sub_dir / "CLAUDE.md"
        limit = get_config().sub_claude_md_max_lines
        user_text = "\n".join(f"# hand-written note {i}" for i in range(limit + 5)) + "\n"
        target.write_text(user_text, encoding="utf-8")

        result = _sync_sub(sub_dir)

        assert result["status"] == "refused", result
        refusals = result["refusals"]
        assert isinstance(refusals, list) and refusals, result
        assert refusals[0]["reason"] == "oversized", refusals
        assert target.read_text(encoding="utf-8") == user_text

    def test_full_section_is_kept_when_the_budget_allows_it(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """The collapse is budget-driven, not hardcoded to sub scope's content."""
        from trw_mcp.models.config import reload_config

        sub_dir = tmp_path / "src" / "module"
        sub_dir.mkdir(parents=True)
        reload_config(get_config().model_copy(update={"sub_claude_md_max_lines": 400}))
        try:
            result = _sync_sub(sub_dir)
        finally:
            reload_config(None)

        assert result["status"] == "synced", result
        content = (sub_dir / "CLAUDE.md").read_text(encoding="utf-8")
        # The full section carries the deliver gate; the pointer form does not.
        assert "Deliver Gate" in content, content[:400]
        assert len(content.split("\n")) > get_config().sub_claude_md_max_lines
