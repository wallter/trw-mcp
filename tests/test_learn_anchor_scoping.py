"""PRD-CORE-267 FR01/FR02 — anchors are session-scoped and evidence-backed.

Measured on the development store on 2026-09-05: 2,006 anchored rows spread
across only 370 distinct anchor sets, with the largest single set — three
unrelated backend symbols — carried by 381 learnings about release workflow,
line-number citations and git hygiene. Two derivation defects produced that:

* the candidate file set was the mtime-newest ``events.jsonl`` ANYWHERE under
  ``.trw`` (i.e. whichever peer session wrote last) with a name-only
  ``git diff`` over the shared working tree as a fallback; and
* when no changed range matched, the file's FIRST symbol was anchored.

Every test here fails on the parent commit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch project root with an empty runs tree and no pins."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    from trw_mcp.models.config import _reset_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    (tmp_path / ".trw" / "runs").mkdir(parents=True)
    return tmp_path


def _seed_run(project_root: Path, run_id: str, modified: list[str]) -> Path:
    """Create a run whose events name *modified* in the bundled hook's shape."""
    run_dir = project_root / ".trw" / "runs" / "task-a" / run_id
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "events.jsonl").write_text(
        "".join(json.dumps({"event": "file_modified", "tool": "Edit", "file": path}) + "\n" for path in modified),
        encoding="utf-8",
    )
    return run_dir


def _pin(run_dir: Path, session_id: str) -> None:
    from trw_mcp.state._paths import pin_active_run

    pin_active_run(run_dir, session_id=session_id)


def _no_ranges() -> Any:
    """A ``subprocess.run`` double reporting a repository with no diff."""
    return MagicMock(returncode=1, stdout="")


def _ranges(diff: str) -> Any:
    return MagicMock(returncode=0, stdout=diff)


# ---------------------------------------------------------------------------
# FR01 — the run comes from the caller's pin, never from disk mtimes
# ---------------------------------------------------------------------------


class TestSessionScopedRun:
    def test_peer_run_newer_events_file_is_ignored(self, project: Path) -> None:
        """A peer's strictly-newer events file contributes no anchor.

        This is the shared-checkout case that produced the 381-entry anchor set:
        two agents in one tree, and the learning belongs to only one of them.
        """
        from trw_mcp.tools._learn_anchors import resolve_learn_anchors

        mine = project / "mine.py"
        mine.write_text("def my_symbol(): pass\n")
        theirs = project / "theirs.py"
        theirs.write_text("def peer_symbol(): pass\n")

        my_run = _seed_run(project, "20260101T000000Z-mine0001", [str(mine)])
        peer_run = _seed_run(project, "20260101T000000Z-peer0001", [str(theirs)])
        # The peer wrote last — the pre-FR01 selector took exactly this file.
        peer_events = peer_run / "meta" / "events.jsonl"
        my_events = my_run / "meta" / "events.jsonl"
        assert peer_events.stat().st_mtime_ns >= my_events.stat().st_mtime_ns

        _pin(my_run, "sess-mine")

        with patch("trw_mcp.tools._learn_anchor_sources.subprocess.run", return_value=_no_ranges()):
            anchors, _validity = resolve_learn_anchors(
                project,
                "L-scoped",
                session_id="sess-mine",
                summary="About my_symbol",
                detail="This learning concerns my_symbol and nothing the peer touched.",
            )

        names = {a["symbol_name"] for a in anchors}
        assert names == {"my_symbol"}
        assert "peer_symbol" not in names

    def test_unpinned_session_derives_no_anchors(self, project: Path) -> None:
        """No resolvable run means no anchors — never a guess from the tree.

        The old code fell through to ``git diff --name-only HEAD``, which on a
        shared checkout reports every concurrent agent's uncommitted edits.
        """
        from trw_mcp.tools._learn_anchors import resolve_learn_anchors

        source = project / "mod.py"
        source.write_text("def some_symbol(): pass\n")
        _seed_run(project, "20260101T000000Z-other001", [str(source)])

        def explode(*_args: object, **_kwargs: object) -> Any:
            raise AssertionError("no git command may run without a resolvable run")

        with patch("trw_mcp.tools._learn_anchor_sources.subprocess.run", side_effect=explode):
            anchors, validity = resolve_learn_anchors(
                project,
                "L-unpinned",
                session_id="sess-with-no-pin",
                summary="About some_symbol",
                detail="Names some_symbol explicitly, and still gets nothing.",
            )

        assert anchors == []
        assert validity is None

    def test_flat_file_key_event_is_read(self, project: Path) -> None:
        """FR01: the flat ``file`` key the bundled hook writes is recognised.

        Before this change the reader looked only at ``data.path`` / ``path``,
        so no hook-written run ever contributed a path.
        """
        from trw_mcp.tools._learn_anchor_sources import modified_files_for_run

        run_dir = _seed_run(project, "20260101T000000Z-flat0001", ["/abs/a.py", "/abs/b.py"])
        assert modified_files_for_run(run_dir) == ["/abs/a.py", "/abs/b.py"]


# ---------------------------------------------------------------------------
# FR02 — an anchor requires demonstrated overlap with the learning
# ---------------------------------------------------------------------------


class TestOverlapRequired:
    def _prepare(self, project: Path, source_text: str) -> Path:
        source = project / "svc.py"
        source.write_text(source_text)
        run_dir = _seed_run(project, "20260101T000000Z-ovl00001", [str(source)])
        _pin(run_dir, "sess-overlap")
        return source

    def test_symbol_with_no_overlap_is_not_anchored(self, project: Path) -> None:
        """A modified file the learning never mentions, with no hunk, anchors nothing.

        On the parent commit this returned the file's first symbol — the exact
        fabrication that put ``_redact_secrets`` on 543 unrelated learnings.
        """
        from trw_mcp.tools._learn_anchors import resolve_learn_anchors

        self._prepare(project, "def unrelated_helper():\n    return 1\n")

        with patch("trw_mcp.tools._learn_anchor_sources.subprocess.run", return_value=_no_ranges()):
            anchors, validity = resolve_learn_anchors(
                project,
                "L-nooverlap",
                session_id="sess-overlap",
                summary="Release workflow uses twine",
                detail="Nothing here names the modified file or any symbol inside it.",
            )

        assert anchors == []
        assert validity is None

    def test_symbol_named_in_detail_is_anchored(self, project: Path) -> None:
        """FR02 predicate 2: the author naming the symbol is sufficient."""
        from trw_mcp.tools._learn_anchors import resolve_learn_anchors

        self._prepare(project, "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n")

        with patch("trw_mcp.tools._learn_anchor_sources.subprocess.run", return_value=_no_ranges()):
            anchors, _validity = resolve_learn_anchors(
                project,
                "L-named",
                session_id="sess-overlap",
                summary="beta returns the wrong value",
                detail="The defect is inside beta, and no other symbol is named here.",
            )

        assert [a["symbol_name"] for a in anchors] == ["beta"]

    def test_symbol_named_only_in_evidence_is_anchored(self, project: Path) -> None:
        """Evidence strings count as the learning's own text, like summary/detail."""
        from trw_mcp.tools._learn_anchors import resolve_learn_anchors

        self._prepare(project, "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n")

        with patch("trw_mcp.tools._learn_anchor_sources.subprocess.run", return_value=_no_ranges()):
            anchors, _validity = resolve_learn_anchors(
                project,
                "L-evidence",
                session_id="sess-overlap",
                summary="A defect with no symbol in the summary",
                detail="Prose that names nothing.",
                evidence=["observed in beta during the failing run"],
            )

        assert [a["symbol_name"] for a in anchors] == ["beta"]

    def test_hunk_inside_symbol_span_is_anchored(self, project: Path) -> None:
        """FR02 predicate 3: a diff hunk inside the symbol's own body qualifies."""
        from trw_mcp.tools._learn_anchors import resolve_learn_anchors

        source = self._prepare(project, "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n")
        diff = f"--- a/svc.py\n+++ b/{source.name}\n@@ -6 +6 @@\n+    return 3\n"

        with patch("trw_mcp.tools._learn_anchor_sources.subprocess.run", return_value=_ranges(diff)):
            anchors, _validity = resolve_learn_anchors(
                project,
                "L-hunk",
                session_id="sess-overlap",
                summary="A wording that names no symbol at all",
                detail="Only the diff proves which code this is about.",
            )

        assert [a["symbol_name"] for a in anchors] == ["beta"]

    def test_hunk_outside_every_symbol_span_is_not_anchored(self, project: Path) -> None:
        """A module-level hunk encloses no symbol, so predicate 3 does not fire.

        The parent commit anchored the nearest definition at-or-before the
        change regardless, which is how an import-block edit acquired a
        function anchor.
        """
        from trw_mcp.tools._learn_anchors import resolve_learn_anchors

        source = self._prepare(project, "def alpha():\n    return 1\n\n\nCONSTANT = 2\n")
        diff = f"--- a/svc.py\n+++ b/{source.name}\n@@ -5 +5 @@\n+CONSTANT = 3\n"

        with patch("trw_mcp.tools._learn_anchor_sources.subprocess.run", return_value=_ranges(diff)):
            anchors, _validity = resolve_learn_anchors(
                project,
                "L-modlevel",
                session_id="sess-overlap",
                summary="A wording that names no symbol at all",
                detail="Only the diff is available, and it lands outside every function.",
            )

        assert anchors == []
