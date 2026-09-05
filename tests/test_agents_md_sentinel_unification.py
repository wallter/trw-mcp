"""PRD-CORE-243-FR06/FR08: one AGENTS.md writer seam, one marker dialect.

Reproduces the live defect fixed here (measured on this repository 2026-09-03,
`make instruction-surface-lint-strict` -> ``duplicate_block: AGENTS.md``):
cursor-cli's install-time writer (``generate_cursor_cli_agents_md``) used to
emit its own ``<!-- TRW:BEGIN -->``/``<!-- TRW:END -->`` block instead of
merging into the shared ``<!-- trw:start -->``/``<!-- trw:end -->`` block the
sync writer (``_sync_agents_md_if_needed``) uses -- so running BOTH against
one AGENTS.md left two disjoint TRW blocks, only one of which any later sync
ever refreshed.

Every test here exercises the REAL production writers against real files on
disk -- nothing is mocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._agents_md import _sync_agents_md_if_needed

_SHARED_START = "<!-- trw:start -->"
_LEGACY_START = "<!-- TRW:BEGIN -->"


def _no_learnings(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
    """Recall stub: the sync writer must not need a live memory backend."""
    return []


def _sync_agents_md(project_root: Path) -> None:
    """Run the real sync writer (what ``trw_instructions_sync``/``trw_deliver``

    call) against *project_root*'s AGENTS.md.
    """
    _sync_agents_md_if_needed(
        True,
        TRWConfig(),
        project_root,
        project_root / ".trw",
        recall_fn=_no_learnings,
    )


class TestFR06InstallThenSyncLeavesOneBlock:
    """PRD-CORE-243-FR06 evidence."""

    def test_install_then_sync_leaves_one_block(self, tmp_path: Path) -> None:
        # 1. Install-time writer runs first (what `init_project(ide="cursor-cli")`
        #    calls at scaffold time).
        install_result = generate_cursor_cli_agents_md(tmp_path, "INSTALL BODY")
        assert install_result["created"] == ["AGENTS.md"]
        after_install = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert after_install.count(_SHARED_START) == 1
        assert _LEGACY_START not in after_install

        # 2. Sync writer runs (what `trw_instructions_sync`/`trw_deliver` call).
        _sync_agents_md(tmp_path)
        after_sync = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

        # Exactly one TRW block of ANY recognised vocabulary remains -- the
        # duplicate_block lint's own predicate (scripts/lint-instruction-surfaces.py).
        assert after_sync.count(_SHARED_START) == 1
        assert _LEGACY_START not in after_sync
        assert "INSTALL BODY" not in after_sync, "the sync writer must refresh the block, not leave it stale"

    def test_second_sync_run_is_byte_identical(self, tmp_path: Path) -> None:
        generate_cursor_cli_agents_md(tmp_path, "INSTALL BODY")
        _sync_agents_md(tmp_path)
        first = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

        _sync_agents_md(tmp_path)
        second = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

        assert first == second

    def test_sync_then_install_also_leaves_one_block(self, tmp_path: Path) -> None:
        """Order independence: the finding measured install-then-sync, but a

        run in the opposite order (sync first, then a re-run of `init_project`
        with `--force` never happening in practice, but a second `update-project`
        invoking the cursor-cli path again) must converge the same way.
        """
        _sync_agents_md(tmp_path)
        generate_cursor_cli_agents_md(tmp_path, "INSTALL BODY")
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

        assert content.count(_SHARED_START) == 1
        assert _LEGACY_START not in content
        assert "INSTALL BODY" in content


class TestFR08MarkerMatchingIsLineAnchored:
    """PRD-CORE-243-FR08 evidence."""

    def test_marker_matching_is_line_anchored(self, tmp_path: Path) -> None:
        """An inline (not whole-line) prose mention of the sentinel survives a

        cursor-cli merge, and the REAL block below it -- the one whole-line
        occurrence -- is what gets replaced. A substring matcher binds to the
        FIRST occurrence anywhere in the text, including this inline mention,
        and deletes everything between it and the real end marker; that is the
        705-line ROADMAP corruption shape. PRD-CORE-243-N2 named
        ``_cursor_cli.py``'s retired ``begin in existing`` + ``str.partition``
        form as one of the two violators fixed here.
        """
        mention = f"Sentinels are `{_SHARED_START}` and `<!-- trw:end -->`."
        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text(
            f"# Agents\n\n{mention}\n\nTrailing user note.\n\n{_SHARED_START}\nOLD BODY\n<!-- trw:end -->\n",
            encoding="utf-8",
        )

        generate_cursor_cli_agents_md(tmp_path, "NEW BODY")
        content = agents_file.read_text(encoding="utf-8")

        assert mention in content, "the inline mention must be preserved verbatim"
        assert "Trailing user note." in content
        assert "NEW BODY" in content
        assert "OLD BODY" not in content
        # Exactly one WHOLE-LINE start marker remains: the inline mention above
        # (never a candidate region boundary) is excluded by counting only
        # lines whose STRIPPED content equals the marker exactly.
        whole_line_starts = sum(1 for line in content.splitlines() if line.strip() == _SHARED_START)
        assert whole_line_starts == 1

    @pytest.mark.parametrize(
        "forbidden_pattern",
        ["begin in existing", "existing.index(", "existing.find("],
    )
    def test_cursor_cli_module_contains_no_substring_marker_search(self, forbidden_pattern: str) -> None:
        """No marker search in the bootstrap tree uses substring containment."""
        from trw_mcp.bootstrap import _cursor_cli

        source = Path(_cursor_cli.__file__).read_text(encoding="utf-8")
        assert forbidden_pattern not in source
