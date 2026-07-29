"""Split bootstrap CLAUDE.md sync tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project

from ._bootstrap_test_support import fake_git_repo  # noqa: F401


class TestRunClaudeMdSync:
    """Tests for _run_claude_md_sync — fail-open + stdout suppression."""

    @staticmethod
    def _failing_llm_client() -> None:
        """Simulate LLMClient raising TypeError (anthropic SDK with no API key)."""
        raise TypeError("Could not resolve authentication")

    def test_auth_error_captured_as_warning(
        self,
        fake_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auth failures from LLMClient are captured as warnings, not errors."""
        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        # Provide a fake API key so the early-return guard is bypassed and
        # _run_claude_md_sync proceeds to call LLMClient().
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-auth-error")

        # Patch at the source module since _run_claude_md_sync imports locally
        monkeypatch.setattr(
            "trw_mcp.state.llm_helpers.LLMClient",
            self._failing_llm_client,
        )

        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        init_project(fake_git_repo)

        _run_claude_md_sync(fake_git_repo, result)

        # The TypeError from LLMClient is caught by the except-Exception handler
        # and recorded as a warning (format: "CLAUDE.md sync skipped: <exc>").
        assert any("CLAUDE.md sync skipped" in w for w in result["warnings"])
        assert result["errors"] == []

    def test_auth_error_does_not_leak_to_stdout(
        self,
        fake_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Auth errors must NOT leak to stdout (would corrupt installer progress pipe)."""
        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        monkeypatch.setattr(
            "trw_mcp.state.llm_helpers.LLMClient",
            self._failing_llm_client,
        )

        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        init_project(fake_git_repo)

        _run_claude_md_sync(fake_git_repo, result)

        captured = capsys.readouterr()
        # Filter out structlog lines (structured observability is expected);
        # the test intent is that raw tracebacks / SDK errors don't leak.
        non_structlog_lines = [
            line
            for line in captured.out.splitlines()
            if not ("[warning " in line or "[info " in line or "[debug " in line or "[error " in line)
        ]
        plain_output = "\n".join(non_structlog_lines)
        assert "authentication" not in plain_output.lower()
        assert "TypeError" not in plain_output

    def test_timeout_captured_as_warning(
        self,
        fake_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sync operations that exceed the timeout are captured as warnings."""
        import concurrent.futures

        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        # Provide a fake API key so the early-return guard is bypassed and
        # _run_claude_md_sync proceeds to call LLMClient().
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-timeout")

        class _TimeoutFuture:
            def result(self, timeout: float | None = None) -> dict[str, object]:
                raise concurrent.futures.TimeoutError()

        class _TimeoutExecutor:
            def __init__(self, max_workers: int = 1) -> None:
                self.max_workers = max_workers

            def submit(self, fn: object, /, *args: object, **kwargs: object) -> _TimeoutFuture:
                return _TimeoutFuture()

            def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
                return None

        monkeypatch.setattr(
            "concurrent.futures.ThreadPoolExecutor",
            _TimeoutExecutor,
        )

        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        init_project(fake_git_repo)

        _run_claude_md_sync(fake_git_repo, result, timeout=1)

        assert any("timed out" in w for w in result["warnings"])
        assert result["errors"] == []


class TestClaudeMdSyncTimeoutFix:
    """Tests for _run_claude_md_sync ThreadPoolExecutor timeout handling.

    The fix changed ``with ThreadPoolExecutor() as pool:`` to an explicit
    ``pool = ThreadPoolExecutor()`` + ``pool.shutdown(wait=False,
    cancel_futures=True)`` in a finally block, preventing the context-manager
    ``__exit__`` from blocking when a worker thread (e.g. LLMClient) hangs.
    """

    def test_sync_timeout_returns_promptly(
        self,
        fake_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When sync times out, the function returns within timeout + buffer — not indefinitely."""
        import concurrent.futures
        import time as time_mod

        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        # Provide a fake API key so the early-return guard is bypassed and
        # _run_claude_md_sync proceeds to call LLMClient().
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-timeout-fix")

        class _TimeoutFuture:
            def result(self, timeout: float | None = None) -> dict[str, object]:
                raise concurrent.futures.TimeoutError()

        class _TimeoutExecutor:
            def __init__(self, max_workers: int = 1) -> None:
                self.max_workers = max_workers

            def submit(self, fn: object, /, *args: object, **kwargs: object) -> _TimeoutFuture:
                return _TimeoutFuture()

            def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
                return None

        monkeypatch.setattr(
            "concurrent.futures.ThreadPoolExecutor",
            _TimeoutExecutor,
        )

        init_project(fake_git_repo)
        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }

        start = time_mod.monotonic()
        _run_claude_md_sync(fake_git_repo, result, timeout=2)
        elapsed = time_mod.monotonic() - start

        # Must complete well under 10s — the old code would block for 300s
        assert elapsed < 10, f"_run_claude_md_sync blocked for {elapsed:.1f}s; expected <10s (timeout was 2s)"
        assert any("timed out" in w for w in result["warnings"])

    def test_sync_success_adds_updated_entry(
        self,
        fake_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Successful sync adds a descriptive entry to result['updated']."""
        from unittest.mock import MagicMock

        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        # Provide a fake API key so the early-return guard is bypassed and
        # _run_claude_md_sync proceeds to call LLMClient().
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-success")

        monkeypatch.setattr(
            "trw_mcp.state.claude_md.execute_claude_md_sync",
            lambda **kwargs: {"learnings_promoted": 3},
        )
        monkeypatch.setattr(
            "trw_mcp.state.llm_helpers.LLMClient",
            lambda: MagicMock(),
        )

        init_project(fake_git_repo)
        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }

        _run_claude_md_sync(fake_git_repo, result, timeout=10)

        assert any("synced" in u for u in result["updated"])
        # Verify the learnings count is included in the message
        assert any("3" in u for u in result["updated"])

    def test_sync_generic_exception_adds_warning(
        self,
        fake_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A generic exception during sync adds a warning, doesn't crash."""
        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        # init_project must run BEFORE we break get_config
        init_project(fake_git_repo)

        def _broken_sync(**_kwargs: object) -> dict[str, object]:
            raise RuntimeError("sync broken")

        monkeypatch.setattr(
            "trw_mcp.state.claude_md.execute_claude_md_sync",
            _broken_sync,
        )

        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }

        _run_claude_md_sync(fake_git_repo, result, timeout=5)

        # Assert the *specific* handler message, not a bare "skipped" substring:
        # the retired no-API-key guard also emitted a warning containing
        # "skipped", so the loose form passed without ever reaching _broken_sync.
        assert any("CLAUDE.md sync skipped" in w for w in result["warnings"])
        assert result["errors"] == []


class TestSyncRunsWithoutApiKey:
    """The CLAUDE.md carrier decision is pure file I/O and must not need auth.

    Regression guard for the defect where ``_run_claude_md_sync`` returned early
    whenever ``ANTHROPIC_API_KEY`` was unset. A Claude Code *subscription* user
    has no such key, so on that (normal) path ``update-project`` ran only the
    carrier-unaware writer and silently reverted CLAUDE.md externalization on
    every run, reporting success with a buried warning.

    Nothing under ``state/claude_md/`` calls an LLM -- ``dispatch_for_profile``
    does ``del reader, llm`` and ``_build_sync_result`` hardcodes
    ``llm_used: False`` -- so the guard gated a deterministic write on an
    unrelated credential.
    """

    def test_sync_externalizes_claude_md_when_no_api_key_present(
        self,
        fake_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With no API key, the sync still runs and externalizes the TRW block."""
        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        init_project(fake_git_repo)
        claude_md = fake_git_repo / "CLAUDE.md"
        # init_project leaves an inline block; that is the pre-migration state.
        assert "<!-- trw:start -->" in claude_md.read_text(encoding="utf-8")

        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }

        _run_claude_md_sync(fake_git_repo, result, timeout=30)

        content = claude_md.read_text(encoding="utf-8")
        import_lines = [ln for ln in content.splitlines() if ln.strip().startswith("@")]

        assert import_lines, (
            "sync must externalize the TRW block to an @-import when no API key is "
            f"set; CLAUDE.md still carries no import directive. warnings={result['warnings']}"
        )
        assert import_lines[0].strip() == "@.trw/INSTRUCTIONS.md"
        sidecar = fake_git_repo / ".trw" / "INSTRUCTIONS.md"
        assert sidecar.exists(), "the @-import target must exist (no dangling import)"
        assert sidecar.read_text(encoding="utf-8").strip(), "sidecar must not be empty"

    def test_no_api_key_does_not_emit_a_skip_warning(
        self,
        fake_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The absence of an API key is no longer reported as a skipped sync."""
        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        init_project(fake_git_repo)

        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }

        _run_claude_md_sync(fake_git_repo, result, timeout=30)

        assert not any("ANTHROPIC_API_KEY" in w for w in result["warnings"]), (
            f"no-API-key must not gate the sync; warnings={result['warnings']}"
        )
        assert result["errors"] == []
