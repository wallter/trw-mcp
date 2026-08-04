"""Claude MD rendering coverage tests split from test_prd_audit_claudemd."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig


class TestLoadClaudeMdTemplateInlineFallback:
    """Cover line 99: inline fallback when no project-local or bundled template."""

    def test_inline_fallback_when_no_templates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Resolution step 3: neither a project-local nor a bundled template exists.

        The previous version of this test never reached step 3. It patched
        ``trw_mcp.state.claude_md.Path`` with a MagicMock it then made a
        pass-through, entered a ``with patch("...Path.__file__", create=True)``
        block whose body was ``pass``, and finally called the loader OUTSIDE
        every patch — so it exercised the BUNDLED path and asserted a
        disjunction (``markers or {{behavioral_protocol}}``) that the bundled
        template satisfies on its own. The inline branch was unreached and
        unasserted.

        ``_parser`` derives the bundled directory from its own ``__file__``, so
        relocating that is the honest way to make the bundled template absent.
        """
        from trw_mcp.state.claude_md import _parser, load_claude_md_template

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        elsewhere = tmp_path / "not-the-package" / "state" / "claude_md"
        elsewhere.mkdir(parents=True)
        monkeypatch.setattr(_parser, "__file__", str(elsewhere / "_parser.py"))

        with patch("trw_mcp.state.claude_md._parser.get_config", return_value=TRWConfig()):
            assert not (Path(_parser.__file__).parent.parent.parent / "data" / "templates" / "claude_md.md").exists(), (
                "bundled template still resolvable — step 3 would not be reached"
            )
            result = load_claude_md_template(trw_dir)

        # The inline fallback is placeholder-only: it carries the markers AND the
        # substitution tokens, and none of the bundled template's prose.
        assert _parser.TRW_MARKER_START in result
        assert "{{behavioral_protocol}}" in result
        assert "{{categorized_learnings}}" in result

    def test_bundled_template_wins_over_the_inline_fallback(self, tmp_path: Path) -> None:
        """Resolution step 2 — the positive control for the test above.

        Without this, relocating ``__file__`` could stop mattering (e.g. the
        bundled lookup moves to importlib.resources) and the inline-fallback
        test would keep passing while silently testing the wrong branch.
        """
        from trw_mcp.state.claude_md import load_claude_md_template

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        with patch("trw_mcp.state.claude_md._parser.get_config", return_value=TRWConfig()):
            result = load_claude_md_template(trw_dir)

        bundled = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data" / "templates" / "claude_md.md"
        assert bundled.is_file(), f"bundled template missing at {bundled}"
        assert result == bundled.read_text(encoding="utf-8")

    def test_project_local_template_takes_priority(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import load_claude_md_template

        trw_dir = tmp_path / ".trw"
        templates_dir = trw_dir / "templates"
        templates_dir.mkdir(parents=True)
        custom = "# Custom Template\n{{behavioral_protocol}}\n"
        (templates_dir / "claude_md.md").write_text(custom, encoding="utf-8")

        # Patch get_config to return default config with templates_dir="templates"
        with patch("trw_mcp.state.claude_md._parser.get_config", return_value=TRWConfig()):
            result = load_claude_md_template(trw_dir)
        assert result == custom
