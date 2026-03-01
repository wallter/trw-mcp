"""Tests for trw_mcp.telemetry.sender.BatchSender — PRD-CORE-031 FR06-FR08."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from trw_mcp.telemetry.sender import BatchSender


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_events(path: Path, events: list[dict[str, object]]) -> None:
    """Write a list of dicts to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")


def _read_events(path: Path) -> list[dict[str, object]]:
    """Read all events from a JSONL file."""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            records.append(json.loads(stripped))
    return records


def _make_sender(
    tmp_path: Path,
    *,
    platform_url: str = "https://api.example.com",
    batch_size: int = 100,
    max_retries: int = 3,
    backoff_base: float = 0.0,
) -> tuple[BatchSender, Path]:
    input_path = tmp_path / "logs" / "tool-telemetry.jsonl"
    urls = [platform_url] if platform_url else []
    sender = BatchSender(
        platform_urls=urls,
        input_path=input_path,
        batch_size=batch_size,
        max_retries=max_retries,
        backoff_base=backoff_base,
    )
    return sender, input_path


# ===========================================================================
# Offline / empty queue early-exit paths
# ===========================================================================


class TestSendEarlyExit:
    def test_send_offline_mode(self, tmp_path: Path) -> None:
        sender, _ = _make_sender(tmp_path, platform_url="")
        result = sender.send()
        assert result["sent"] == 0
        assert result["failed"] == 0
        assert result["remaining"] == 0
        assert result["skipped_reason"] == "offline_mode"

    def test_send_no_events(self, tmp_path: Path) -> None:
        """Missing input file returns skipped_reason='no_events'."""
        sender, input_path = _make_sender(tmp_path)
        assert not input_path.exists()
        result = sender.send()
        assert result["skipped_reason"] == "no_events"
        assert result["sent"] == 0

    def test_send_empty_queue(self, tmp_path: Path) -> None:
        """Empty JSONL file (all blank lines) returns skipped_reason='empty_queue'."""
        sender, input_path = _make_sender(tmp_path)
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text("\n\n", encoding="utf-8")
        result = sender.send()
        assert result["skipped_reason"] == "empty_queue"
        assert result["sent"] == 0


# ===========================================================================
# Successful send
# ===========================================================================


class TestSendSuccess:
    def test_send_success(self, tmp_path: Path) -> None:
        """Mock HTTP POST returning True; all events marked as sent."""
        sender, input_path = _make_sender(tmp_path)
        events = [{"event_type": "tool_invocation", "tool": f"t{i}"} for i in range(5)]
        _write_events(input_path, events)

        with patch.object(sender, "_http_post", return_value=True):
            result = sender.send()

        assert result["sent"] == 5
        assert result["failed"] == 0
        assert result["remaining"] == 0
        assert result["skipped_reason"] is None

    def test_send_removes_sent_events(self, tmp_path: Path) -> None:
        """After a fully successful send the queue file is cleared."""
        sender, input_path = _make_sender(tmp_path)
        events = [{"idx": i} for i in range(3)]
        _write_events(input_path, events)

        with patch.object(sender, "_http_post", return_value=True):
            sender.send()

        remaining = _read_events(input_path)
        assert remaining == []

    def test_send_result_contains_no_skipped_reason_on_success(
        self, tmp_path: Path
    ) -> None:
        sender, input_path = _make_sender(tmp_path)
        _write_events(input_path, [{"k": "v"}])

        with patch.object(sender, "_http_post", return_value=True):
            result = sender.send()

        assert result["skipped_reason"] is None


# ===========================================================================
# Batch splitting
# ===========================================================================


class TestBatchSplitting:
    def test_send_batch_splitting(self, tmp_path: Path) -> None:
        """250 events with batch_size=100 should produce 3 HTTP calls."""
        sender, input_path = _make_sender(tmp_path, batch_size=100)
        events = [{"idx": i} for i in range(250)]
        _write_events(input_path, events)

        call_args: list[list[dict[str, object]]] = []

        def _post(url: str, payload: list[dict[str, object]]) -> bool:
            call_args.append(payload)
            return True

        with patch.object(sender, "_http_post", side_effect=_post):
            result = sender.send()

        assert len(call_args) == 3
        assert len(call_args[0]) == 100
        assert len(call_args[1]) == 100
        assert len(call_args[2]) == 50
        assert result["sent"] == 250

    def test_batch_size_one_sends_individually(self, tmp_path: Path) -> None:
        sender, input_path = _make_sender(tmp_path, batch_size=1)
        events = [{"idx": i} for i in range(4)]
        _write_events(input_path, events)

        call_count = {"n": 0}

        def _post(url: str, payload: list[dict[str, object]]) -> bool:
            call_count["n"] += 1
            return True

        with patch.object(sender, "_http_post", side_effect=_post):
            result = sender.send()

        assert call_count["n"] == 4
        assert result["sent"] == 4


# ===========================================================================
# Retry logic
# ===========================================================================


class TestRetryLogic:
    def test_send_retry_on_failure(self, tmp_path: Path) -> None:
        """First attempt fails (False), second attempt succeeds."""
        sender, input_path = _make_sender(
            tmp_path, max_retries=3, backoff_base=0.0
        )
        _write_events(input_path, [{"event_type": "test"}])

        attempt_results = [False, True]
        call_count = {"n": 0}

        def _post(url: str, payload: list[dict[str, object]]) -> bool:
            idx = call_count["n"]
            call_count["n"] += 1
            return attempt_results[idx]

        with patch("time.sleep"):
            with patch.object(sender, "_http_post", side_effect=_post):
                result = sender.send()

        assert result["sent"] == 1
        assert result["failed"] == 0
        assert call_count["n"] == 2

    def test_send_fail_open(self, tmp_path: Path) -> None:
        """All retries fail — events remain in queue (fail-open)."""
        sender, input_path = _make_sender(
            tmp_path, max_retries=2, backoff_base=0.0
        )
        events = [{"idx": i} for i in range(3)]
        _write_events(input_path, events)

        with patch("time.sleep"):
            with patch.object(sender, "_http_post", return_value=False):
                result = sender.send()

        assert result["sent"] == 0
        assert result["failed"] == 3
        # Events NOT removed from queue because nothing was sent
        remaining = _read_events(input_path)
        assert len(remaining) == 3

    def test_send_exception_is_caught(self, tmp_path: Path) -> None:
        """_http_post raising an exception is treated as a failure."""
        sender, input_path = _make_sender(
            tmp_path, max_retries=2, backoff_base=0.0
        )
        _write_events(input_path, [{"k": "v"}])

        with patch("time.sleep"):
            with patch.object(
                sender, "_http_post", side_effect=RuntimeError("boom")
            ):
                result = sender.send()

        assert result["sent"] == 0
        assert result["failed"] == 1


# ===========================================================================
# Partial success
# ===========================================================================


class TestPartialSuccess:
    def test_send_partial_success_remaining_events_stay(
        self, tmp_path: Path
    ) -> None:
        """First batch succeeds, second fails — only sent events are removed."""
        sender, input_path = _make_sender(
            tmp_path, batch_size=2, max_retries=1, backoff_base=0.0
        )
        events = [{"idx": i} for i in range(4)]
        _write_events(input_path, events)

        batch_num = {"n": 0}

        def _post(url: str, payload: list[dict[str, object]]) -> bool:
            result = batch_num["n"] == 0
            batch_num["n"] += 1
            return result

        with patch.object(sender, "_http_post", side_effect=_post):
            result = sender.send()

        assert result["sent"] == 2
        assert result["failed"] == 2
        assert result["remaining"] == 2

        remaining = _read_events(input_path)
        assert len(remaining) == 2
        assert remaining[0]["idx"] == 2
        assert remaining[1]["idx"] == 3


# ===========================================================================
# Backoff timing
# ===========================================================================


class TestBackoffTiming:
    def test_backoff_timing(self, tmp_path: Path) -> None:
        """Exponential backoff delays: backoff_base * 2^attempt."""
        sender, input_path = _make_sender(
            tmp_path, max_retries=3, backoff_base=1.0
        )
        _write_events(input_path, [{"k": "v"}])

        sleep_calls: list[float] = []

        def _fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        with patch("time.sleep", side_effect=_fake_sleep):
            with patch.object(sender, "_http_post", return_value=False):
                sender.send()

        # 3 retries → 2 sleeps (no sleep after last attempt)
        assert sleep_calls == [1.0, 2.0]

    def test_no_sleep_on_first_attempt_success(self, tmp_path: Path) -> None:
        sender, input_path = _make_sender(
            tmp_path, max_retries=3, backoff_base=1.0
        )
        _write_events(input_path, [{"k": "v"}])

        sleep_calls: list[float] = []

        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with patch.object(sender, "_http_post", return_value=True):
                sender.send()

        assert sleep_calls == []

    def test_zero_backoff_base_no_sleep(self, tmp_path: Path) -> None:
        """backoff_base=0 means no sleep time (0 * 2^n = 0)."""
        sender, input_path = _make_sender(
            tmp_path, max_retries=3, backoff_base=0.0
        )
        _write_events(input_path, [{"k": "v"}])

        sleep_calls: list[float] = []

        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with patch.object(sender, "_http_post", return_value=False):
                sender.send()

        # sleep is still called but with 0.0 delay
        assert all(s == 0.0 for s in sleep_calls)


# ===========================================================================
# from_config factory
# ===========================================================================


class TestFromConfig:
    def test_from_config(self, tmp_path: Path) -> None:
        """Factory creates sender with correct paths from config."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            trw_dir=str(tmp_path / ".trw"),
            logs_dir="logs",
            telemetry_file="tool-telemetry.jsonl",
            platform_url="https://api.trwframework.com",
        )
        trw_dir = tmp_path / ".trw"

        with (
            patch("trw_mcp.telemetry.sender.get_config", return_value=cfg),
            patch(
                "trw_mcp.telemetry.sender.resolve_trw_dir",
                return_value=trw_dir,
            ),
        ):
            sender = BatchSender.from_config()

        assert sender._platform_urls == ["https://api.trwframework.com"]
        assert sender._input_path == trw_dir / "logs" / "tool-telemetry.jsonl"

    def test_from_config_offline_when_no_platform_url(
        self, tmp_path: Path
    ) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            trw_dir=str(tmp_path / ".trw"),
            platform_url="",
        )
        trw_dir = tmp_path / ".trw"

        with (
            patch("trw_mcp.telemetry.sender.get_config", return_value=cfg),
            patch(
                "trw_mcp.telemetry.sender.resolve_trw_dir",
                return_value=trw_dir,
            ),
        ):
            sender = BatchSender.from_config()

        assert sender._platform_urls == []

        result = sender.send()
        assert result["skipped_reason"] == "offline_mode"


# ===========================================================================
# Parallel fan-out
# ===========================================================================


class TestParallelFanout:
    def test_fanout_both_urls_attempted_when_first_fails(
        self, tmp_path: Path
    ) -> None:
        """Both URLs are attempted even when the first one fails."""
        input_path = tmp_path / "logs" / "tool-telemetry.jsonl"
        sender = BatchSender(
            platform_urls=["https://url-a.example.com", "https://url-b.example.com"],
            input_path=input_path,
            batch_size=100,
            max_retries=1,
            backoff_base=0.0,
        )
        _write_events(input_path, [{"event_type": "test"}])

        attempted_urls: list[str] = []

        def _post(url: str, payload: list[dict[str, object]]) -> bool:
            attempted_urls.append(url)
            # First URL fails, second succeeds
            return "url-b" in url

        with patch.object(sender, "_http_post", side_effect=_post):
            result = sender.send()

        assert result["sent"] == 1
        assert result["failed"] == 0
        assert len(attempted_urls) == 2
        assert "https://url-a.example.com/v1/telemetry" in attempted_urls
        assert "https://url-b.example.com/v1/telemetry" in attempted_urls

    def test_fanout_both_urls_attempted_when_second_fails(
        self, tmp_path: Path
    ) -> None:
        """Both URLs are attempted even when the second one fails."""
        input_path = tmp_path / "logs" / "tool-telemetry.jsonl"
        sender = BatchSender(
            platform_urls=["https://url-a.example.com", "https://url-b.example.com"],
            input_path=input_path,
            batch_size=100,
            max_retries=1,
            backoff_base=0.0,
        )
        _write_events(input_path, [{"event_type": "test"}])

        attempted_urls: list[str] = []

        def _post(url: str, payload: list[dict[str, object]]) -> bool:
            attempted_urls.append(url)
            return "url-a" in url

        with patch.object(sender, "_http_post", side_effect=_post):
            result = sender.send()

        assert result["sent"] == 1
        assert len(attempted_urls) == 2

    def test_fanout_all_fail_returns_failed(self, tmp_path: Path) -> None:
        """When all URLs fail, result shows failed count."""
        input_path = tmp_path / "logs" / "tool-telemetry.jsonl"
        sender = BatchSender(
            platform_urls=["https://url-a.example.com", "https://url-b.example.com"],
            input_path=input_path,
            batch_size=100,
            max_retries=1,
            backoff_base=0.0,
        )
        _write_events(input_path, [{"event_type": "test"}])

        with patch.object(sender, "_http_post", return_value=False):
            result = sender.send()

        assert result["sent"] == 0
        assert result["failed"] == 1

    def test_fanout_single_url_uses_fast_path(self, tmp_path: Path) -> None:
        """Single URL avoids ThreadPoolExecutor overhead."""
        sender, input_path = _make_sender(tmp_path)
        _write_events(input_path, [{"k": "v"}])

        with patch.object(sender, "_http_post", return_value=True):
            result = sender.send()

        assert result["sent"] == 1


# ===========================================================================
# URL construction
# ===========================================================================


class TestUrlConstruction:
    def test_url_has_v1_telemetry_suffix(self, tmp_path: Path) -> None:
        sender, input_path = _make_sender(
            tmp_path, platform_url="https://api.example.com"
        )
        _write_events(input_path, [{"k": "v"}])

        captured_url: list[str] = []

        def _post(url: str, payload: list[dict[str, object]]) -> bool:
            captured_url.append(url)
            return True

        with patch.object(sender, "_http_post", side_effect=_post):
            sender.send()

        assert captured_url[0] == "https://api.example.com/v1/telemetry"

    def test_trailing_slash_stripped_from_url(self, tmp_path: Path) -> None:
        sender, input_path = _make_sender(
            tmp_path, platform_url="https://api.example.com/"
        )
        _write_events(input_path, [{"k": "v"}])

        captured_url: list[str] = []

        def _post(url: str, payload: list[dict[str, object]]) -> bool:
            captured_url.append(url)
            return True

        with patch.object(sender, "_http_post", side_effect=_post):
            sender.send()

        assert captured_url[0] == "https://api.example.com/v1/telemetry"
