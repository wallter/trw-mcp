"""HINT-RECALL-BUDGET: the pre-edit hint's T1 recall never holds the T2 sidecar hint hostage.

Measured against the real checkout (2026-09-27): the unbounded recall behind
``compute_before_edit_hint`` cost 4.76s of a 5.98s hint against a 2522-entry
daemon store (3 ``take_hits`` page-growth round trips at ~1.5s each), which
starved the hook's 2.4s PreToolUse budget before the fast T2 sidecar half ever
ran. ``hint_recall_deadline_ms`` (default 600ms) plus single-paged recall
(``test_recall_take.py::test_single_page_never_grows_past_the_first_page``)
fix that; these tests cover the hint's own deadline-and-timeout wiring.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from tests.test_before_edit_hint_tool import _make_git_repo, _write_entitlement, _write_sidecar
from trw_mcp.models.config import TRWConfig, reload_config
from trw_mcp.tools._before_edit_hint_core import LearningSummary, compute_before_edit_hint

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_config() -> None:
    yield
    reload_config()


def _pro_repo(tmp_path: Path) -> str:
    sha = _make_git_repo(tmp_path)
    cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
    _write_sidecar(cache_dir, sha, "foo.py")
    _write_entitlement(tmp_path / ".trw", "pro")
    return sha


class TestSlowStoreTimesOutAndStillReturnsTheSidecarHint:
    def test_slow_store_times_out_and_still_returns_the_sidecar_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A recall that never finishes inside the deadline yields no learnings, but the T2 hint still renders."""
        result = self._run(tmp_path, monkeypatch, deadline_ms=100, sleep_s=2.0)

        assert result.learnings == []
        assert result.learnings_status == "recall_timeout"
        assert result.distill_status == "hint_available"
        assert result.distill_hint is not None and result.distill_hint.target_path == "foo.py"

    def _run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, deadline_ms: int, sleep_s: float) -> Any:
        _pro_repo(tmp_path)
        reload_config(TRWConfig(hint_recall_deadline_ms=deadline_ms))

        def _slow_collect(*args: object, **kwargs: object) -> list[LearningSummary]:
            time.sleep(sleep_s)
            return [LearningSummary(id="L-never-seen", summary="too slow to matter")]

        monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", _slow_collect)

        started = time.monotonic()
        result = compute_before_edit_hint(file_path="foo.py", repo_root=str(tmp_path))
        elapsed = time.monotonic() - started

        # Within the deadline plus generous scheduling epsilon — never anywhere near sleep_s.
        assert elapsed < (deadline_ms / 1000.0) + 1.0
        assert result.learnings == []
        assert result.learnings_status == "recall_timeout"
        # T2 sidecar hint is computed independently and still renders.
        assert result.distill_status == "hint_available"
        assert result.distill_hint is not None
        assert result.distill_hint.target_path == "foo.py"
        return result


class TestFastStoreStillReturnsLearningsWithinTheDeadline:
    def test_fast_store_still_returns_learnings_within_the_deadline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _pro_repo(tmp_path)
        reload_config(TRWConfig(hint_recall_deadline_ms=600))
        expected = [LearningSummary(id="L-fast", summary="answered well inside the budget")]

        calls: list[dict[str, object]] = []

        def _fast_collect(queries: list[str], **kwargs: object) -> list[LearningSummary]:
            calls.append({"queries": queries, **kwargs})
            return expected

        monkeypatch.setattr("trw_mcp.tools._learnings_collector.collect_learnings", _fast_collect)

        result = compute_before_edit_hint(file_path="foo.py", repo_root=str(tmp_path))

        assert result.learnings == expected
        assert result.learnings_status == "ok"
        assert result.learnings_count == 1
        # HINT-RECALL-BUDGET: the hint path always asks for a single page.
        assert calls[0]["single_page"] is True


class TestDefaultDeadlineIsAdmitted:
    def test_default_is_600ms_and_bounded(self) -> None:
        config = TRWConfig()
        assert config.hint_recall_deadline_ms == 600
        with pytest.raises(Exception):
            TRWConfig(hint_recall_deadline_ms=10)  # below the 50ms floor


class TestADaemonOlderThanTheRerankOptOut:
    """A same-major daemon that refuses ``rerank`` still delivers lessons, inside the deadline."""

    def _old_daemon(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, retry_sleep_s: float) -> list[bool]:
        from fastmcp.exceptions import ToolError

        from tests._anchor_daemon_fake import TextOnlyDaemon, lesson, use_daemon
        from tests.test_daemon_store_recall_page import _OLD_DAEMON_REFUSAL

        reranked: list[bool] = []

        class _Old(TextOnlyDaemon):
            async def recall(self, query: str, namespace: str, **kwargs: object) -> object:
                reranked.append("rerank" in kwargs)
                if "rerank" in kwargs:
                    raise ToolError(_OLD_DAEMON_REFUSAL)
                await asyncio.sleep(retry_sleep_s)
                return await super().recall(query, namespace, **kwargs)  # type: ignore[arg-type]

        use_daemon(monkeypatch, tmp_path, _Old([lesson("L-foo", "foo.py closes the transport")]))
        return reranked

    def test_lessons_arrive_through_the_retry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _pro_repo(tmp_path)
        reload_config(TRWConfig(hint_recall_deadline_ms=2000))
        reranked = self._old_daemon(monkeypatch, tmp_path, retry_sleep_s=0.0)

        result = compute_before_edit_hint(file_path="foo.py", repo_root=str(tmp_path))

        assert [item.id for item in result.learnings] == ["L-foo"]
        assert result.learnings_status == "ok"
        assert reranked[:2] == [True, False]  # each refused page retried once, without rerank

    def test_a_retry_past_the_deadline_returns_no_lessons(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _pro_repo(tmp_path)
        reload_config(TRWConfig(hint_recall_deadline_ms=100))
        self._old_daemon(monkeypatch, tmp_path, retry_sleep_s=2.0)

        started = time.monotonic()
        result = compute_before_edit_hint(file_path="foo.py", repo_root=str(tmp_path))

        assert time.monotonic() - started < 1.1
        assert result.learnings == [] and result.learnings_status == "recall_timeout"
        assert result.distill_status == "hint_available"


def test_learnings_status_is_in_the_response_only_when_the_recall_timed_out() -> None:
    """Advisory, so paid only when it carries signal (Tool Response Token Budget)."""
    from trw_mcp.tools._before_edit_hint_core import BeforeEditHintResult

    ok = BeforeEditHintResult(file_path="a.py", tier="pro", distill_status="sidecar_missing")
    timed_out = ok.model_copy(update={"learnings_status": "recall_timeout"})

    assert "learnings_status" not in ok.model_dump()
    assert timed_out.model_dump()["learnings_status"] == "recall_timeout"
