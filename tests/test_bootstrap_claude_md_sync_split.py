"""Split bootstrap instruction-file sync tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project

from ._bootstrap_test_support import fake_git_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


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
        # and recorded as a warning (format: "Instruction sync skipped: <exc>").
        assert any("Instruction sync skipped" in w for w in result["warnings"])
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


class TestClaudeMdSyncOutcomes:
    """What _run_claude_md_sync reports. It runs in the caller's thread with no timeout (B71-118): the old
    pool-thread timeout left a running sync writing after update-project's transaction had ended, and is
    covered by ``test_claude_md_sync_stays_inside_the_update.py``."""

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
            lambda **kwargs: {"status": "synced"},
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

        _run_claude_md_sync(fake_git_repo, result)

        assert "Instruction files synced" in result["updated"]

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

        _run_claude_md_sync(fake_git_repo, result)

        # Assert the *specific* handler message, not a bare "skipped" substring:
        # the retired no-API-key guard also emitted a warning containing
        # "skipped", so the loose form passed without ever reaching _broken_sync.
        assert any("Instruction sync skipped" in w for w in result["warnings"])
        assert result["errors"] == []


class TestSyncRunsWithoutApiKey:
    """The CLAUDE.md sync is pure file I/O and must not need auth.

    Regression guard for the defect where ``_run_claude_md_sync`` returned early
    whenever ``ANTHROPIC_API_KEY`` was unset. A Claude Code *subscription* user
    has no such key, so on that (normal) path ``update-project`` skipped the
    sync entirely, reporting success with a buried warning.

    Nothing under ``state/claude_md/`` calls an LLM -- ``dispatch_for_profile``
    does ``del reader, llm`` and ``_build_sync_result`` hardcodes
    ``llm_used: False`` -- so the guard gated a deterministic write on an
    unrelated credential.
    """

    def test_sync_runs_when_no_api_key_present(
        self,
        fake_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With no API key, the sync still runs: a missing AGENTS.md block is rewritten."""
        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        init_project(fake_git_repo)
        agents_md = fake_git_repo / "AGENTS.md"
        agents_md.write_text("# Project\n", encoding="utf-8")

        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }

        _run_claude_md_sync(fake_git_repo, result)

        content = agents_md.read_text(encoding="utf-8")
        # PRD-CORE-341: the block is the link; the protocol is in the generated file.
        assert "@.trw/INSTRUCTIONS.md" in content.splitlines(), f"sync did not run; warnings={result['warnings']}"
        assert "trw_session_start" in (fake_git_repo / ".trw" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
        assert content.startswith("# Project")

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

        _run_claude_md_sync(fake_git_repo, result)

        assert not any("ANTHROPIC_API_KEY" in w for w in result["warnings"]), (
            f"no-API-key must not gate the sync; warnings={result['warnings']}"
        )
        assert result["errors"] == []


class TestSyncSurfacesWriteRefusals:
    """A policy-refused instruction write must reach the operator (PRD-FIX-123).

    ``execute_claude_md_sync`` reports a guarded write it declined to perform in
    ``refusals`` and still returns normally. ``_run_claude_md_sync`` read only
    the (since removed) promotion count, so an oversized CLAUDE.md/AGENTS.md produced the line
    "CLAUDE.md synced" — the operator was told their instruction file had been
    updated when the writer had deliberately left it alone. Nobody but them can
    fix that, and nothing else in the update report said so.
    """

    _REFUSAL = {
        "error_code": "instruction_surface_oversized",
        "file": "AGENTS.md",
        "reason": "oversized",
        "lines": 900,
        "limit": 350,
        "current_non_generated_bytes": 40_000,
        "candidate_non_generated_bytes": 40_000,
        "current_total_bytes": 41_000,
        "candidate_total_bytes": 42_000,
        "detail": "instruction surface over the line limit",
    }

    @staticmethod
    def _blank_result() -> dict[str, list[str]]:
        return {"updated": [], "created": [], "preserved": [], "errors": [], "warnings": []}

    def test_a_refused_write_warns_naming_file_reason_and_limit(
        self, fake_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock

        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        monkeypatch.setattr(
            "trw_mcp.state.claude_md.execute_claude_md_sync",
            lambda **_kwargs: {"refusals": [self._REFUSAL]},
        )
        monkeypatch.setattr("trw_mcp.state.llm_helpers.LLMClient", lambda: MagicMock())

        init_project(fake_git_repo)
        result = self._blank_result()

        _run_claude_md_sync(fake_git_repo, result)

        warnings = result["warnings"]
        assert any("AGENTS.md" in w for w in warnings), f"the refused file must be named: {warnings}"
        assert any("oversized" in w for w in warnings), f"the refusal reason must be named: {warnings}"
        assert any("350" in w for w in warnings), f"the limit must be named: {warnings}"

    def test_a_refused_write_is_not_reported_as_synced(
        self, fake_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The truthfulness half: a write that did not happen is not an update."""
        from unittest.mock import MagicMock

        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        monkeypatch.setattr(
            "trw_mcp.state.claude_md.execute_claude_md_sync",
            lambda **_kwargs: {"refusals": [self._REFUSAL]},
        )
        monkeypatch.setattr("trw_mcp.state.llm_helpers.LLMClient", lambda: MagicMock())

        init_project(fake_git_repo)
        result = self._blank_result()

        _run_claude_md_sync(fake_git_repo, result)

        assert not any("synced" in u for u in result["updated"]), (
            f"a refused write must not be reported as a sync: {result['updated']}"
        )
        assert result["errors"] == [], "a refusal is a warning, not an error — the update continues"

    def test_no_refusal_still_reports_the_sync(self, fake_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Negative branch. An empty ``refusals`` list must not suppress the line."""
        from unittest.mock import MagicMock

        from trw_mcp.bootstrap._update_project import _run_claude_md_sync

        monkeypatch.setattr(
            "trw_mcp.state.claude_md.execute_claude_md_sync",
            lambda **_kwargs: {"refusals": []},
        )
        monkeypatch.setattr("trw_mcp.state.llm_helpers.LLMClient", lambda: MagicMock())

        init_project(fake_git_repo)
        result = self._blank_result()

        _run_claude_md_sync(fake_git_repo, result)

        assert "Instruction files synced" in result["updated"]
        assert result["warnings"] == []
