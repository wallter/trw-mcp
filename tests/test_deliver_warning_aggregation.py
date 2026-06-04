"""F24 legibility: trw_deliver must distinguish warned-but-delivered from clean.

Advisory delivery-gate warnings (review_advisory, review_warning, untracked,
complexity_drift, etc.) are SOFT gates — they are surfaced on the deliver
result but never block, so historically a warned-but-delivered run was
byte-identical to a fully-clean one (``success=True``, no warning signal).
``trw_deliver`` now aggregates the present advisory-warning keys into
``warning_count`` / ``warnings_present`` / ``warnings`` so downstream eval /
false-completion scoring can separate the two cases.

These tests drive the REAL deliver path (``tools["trw_deliver"].fn(...)``) — no
mocking of the deliver unit itself — and assert real aggregate values.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server


def _common_patches(trw_dir: Path, run_dir: Path | None, tmp_path: Path) -> list[object]:
    """Patches that isolate the deliver path from real fs/index/sync side effects."""
    return [
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
        patch(
            "trw_mcp.tools.ceremony._do_reflect",
            return_value={"status": "success", "events_analyzed": 0, "learnings_produced": 0},
        ),
        patch(
            "trw_mcp.tools.ceremony._do_instruction_sync",
            return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
        ),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        # _check_untracked_files shells out to git; force it quiet so the only
        # advisory the warned case sees is the one we deliberately trigger.
        patch("trw_mcp.tools._delivery_helpers._check_untracked_files", return_value=None),
    ]


@pytest.mark.integration
class TestDeliverWarningAggregation:
    """F24: warning_count / warnings_present / warnings on the deliver result."""

    def _make_run(self, tmp_path: Path, *, with_passing_build: bool) -> tuple[Path, Path]:
        """Create a synthetic active run; returns (trw_dir, run_dir)."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        # No complexity_class => MINIMAL/light => no-review yields review_ADVISORY
        # (a soft, non-blocking warning), not a hard block.
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        events = ""
        if with_passing_build:
            # A passing build_check satisfies the build gate so deliver succeeds
            # WITHOUT allow_unverified — isolating the advisory-warning signal.
            events = (
                json.dumps(
                    {
                        "event": "build_check_complete",
                        "tests_passed": True,
                        "static_checks_clean": True,
                    }
                )
                + "\n"
            )
        (run_dir / "meta" / "events.jsonl").write_text(events, encoding="utf-8")
        return trw_dir, run_dir

    def test_warned_deliver_sets_count_flag_and_lists_keys(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An active run with no review fires review_advisory -> aggregate reflects it."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir, run_dir = self._make_run(tmp_path, with_passing_build=True)

        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _common_patches(trw_dir, run_dir, tmp_path):
                stack.enter_context(p)  # type: ignore[arg-type]
            result = tools["trw_deliver"].fn()

        # Deliver succeeded (the advisory does NOT block) ...
        assert result["success"] is True
        # ... and the advisory warning that fired is reflected in the aggregate.
        assert result["warnings_present"] is True
        assert result["warning_count"] >= 1
        assert "review_advisory" in result["warnings"]
        # The aggregate count matches the listed keys exactly.
        assert result["warning_count"] == len(result["warnings"])
        # The underlying advisory key is also present on the result (legibility).
        assert "review_advisory" in result

    def test_clean_deliver_has_zero_warnings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A deliver with no advisory warnings reports warning_count=0 / flag False.

        The no-active-run + skip_reflect path produces a successful deliver that
        triggers none of the advisory gates (no run => no review/untracked/drift
        checks; no work events => no build-gate warning).
        """
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True)

        assert result["success"] is True
        assert result["warnings_present"] is False
        assert result["warning_count"] == 0
        assert result["warnings"] == []

    def test_warned_and_clean_results_differ(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The whole point of F24: a warned deliver is no longer indistinguishable
        from a clean one. Both succeed, but the aggregate fields diverge.
        """
        # Warned deliver (active run, no review => review_advisory).
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir, run_dir = self._make_run(tmp_path, with_passing_build=True)

        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in _common_patches(trw_dir, run_dir, tmp_path):
                stack.enter_context(p)  # type: ignore[arg-type]
            warned = tools["trw_deliver"].fn()

        # Clean deliver (no run, skip_reflect).
        clean_root = tmp_path / "clean"
        clean_root.mkdir()
        tools2 = _make_ceremony_server(monkeypatch, clean_root)
        clean_trw = clean_root / ".trw"
        (clean_trw / "learnings" / "entries").mkdir(parents=True)
        (clean_trw / "context").mkdir(parents=True)
        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=clean_trw),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
        ):
            clean = tools2["trw_deliver"].fn(skip_reflect=True)

        # Both delivered successfully — success alone CANNOT tell them apart...
        assert warned["success"] is True
        assert clean["success"] is True
        assert warned["success"] == clean["success"]
        # ...but the F24 aggregate fields now do.
        assert warned["warnings_present"] != clean["warnings_present"]
        assert warned["warning_count"] > clean["warning_count"]
        assert warned["warnings"] != clean["warnings"]
