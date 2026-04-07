"""Unit and integration tests for phase_gates_build.py.

Covers uncovered paths in state/validation/phase_gates_build.py:
- _best_effort_check: exception swallowing (lines 45-46)
- _check_build_status:
    - build_check_enabled=False / enforcement=off bypass (line 77)
    - missing cache returns info failure (lines 79-88)
    - unreadable cache (lines 90-103)
    - fresh build with tests_passed=False (lines 141-155)
    - failure snippet formatting with single and multiple failures
    - invalid timestamp handling (lines 133-134)
    - mypy failures at full scope (lines 157-167)
    - coverage failures at validate/deliver (lines 169-181)
    - stale build detected (lines 108-132)
    - strict gate enforcement (lines 137-138)
- _best_effort_build_check: delegates to _check_build_status
- _best_effort_integration_check: unregistered tools and missing tests (lines 218-249)
- _best_effort_orphan_check: orphan modules (lines 268-292)
- check_migration_gate: model changes without migration (lines 394-426)
- _best_effort_migration_check: disabled vs enabled (lines 429-460)
- _best_effort_dry_check: disabled path (lines 463-509)
- _best_effort_semantic_check: disabled path (lines 512-556)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation.phase_gates_build import (
    _BUILD_STALENESS_SECS,
    _best_effort_check,
    _best_effort_integration_check,
    _best_effort_migration_check,
    _best_effort_build_check,
    _best_effort_dry_check,
    _best_effort_orphan_check,
    _best_effort_semantic_check,
    _check_build_status,
    check_migration_gate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw directory with context subdirectory."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    return trw_dir


def _write_build_status(
    trw_dir: Path,
    *,
    tests_passed: bool = True,
    mypy_clean: bool = True,
    coverage_pct: float = 90.0,
    scope: str = "full",
    age_secs: int = 0,
) -> Path:
    """Write a build-status.yaml with controlled content."""
    if age_secs > 0:
        # Build a timestamp that is age_secs old
        ts = datetime.fromtimestamp(time.time() - age_secs, tz=timezone.utc)
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        ts_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cache_path = trw_dir / "context" / "build-status.yaml"
    content = (
        f"tests_passed: {'true' if tests_passed else 'false'}\n"
        f"mypy_clean: {'true' if mypy_clean else 'false'}\n"
        f"coverage_pct: {coverage_pct}\n"
        f"scope: {scope}\n"
        f'timestamp: "{ts_str}"\n'
    )
    cache_path.write_text(content, encoding="utf-8")
    return cache_path


# ---------------------------------------------------------------------------
# _best_effort_check
# ---------------------------------------------------------------------------


class TestBestEffortCheck:
    """Unit tests for _best_effort_check exception swallowing."""

    @pytest.mark.unit
    def test_runs_function_normally(self) -> None:
        """Normal function executes without error."""
        results: list[int] = []

        def fn() -> None:
            results.append(42)

        _best_effort_check(fn, "test_check")
        assert results == [42]

    @pytest.mark.unit
    def test_swallows_exception(self) -> None:
        """Exception in fn is swallowed, not propagated."""

        def fn() -> None:
            raise RuntimeError("boom")

        # Must NOT raise
        _best_effort_check(fn, "test_check")

    @pytest.mark.unit
    def test_swallows_any_exception_type(self) -> None:
        """Even non-RuntimeError exceptions are swallowed."""

        def fn() -> None:
            raise ValueError("bad value")

        _best_effort_check(fn, "val_check")

    @pytest.mark.unit
    def test_mutations_still_visible_on_success(self) -> None:
        """Side effects from fn are visible after successful call."""
        out: list[str] = []

        def fn() -> None:
            out.append("done")

        _best_effort_check(fn, "mut_check")
        assert out == ["done"]


# ---------------------------------------------------------------------------
# _check_build_status — bypass conditions
# ---------------------------------------------------------------------------


class TestCheckBuildStatusBypass:
    """Tests for conditions where _check_build_status returns empty list."""

    @pytest.mark.unit
    def test_disabled_returns_empty(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        config = TRWConfig(build_check_enabled=False)
        result = _check_build_status(trw_dir, config, "implement")
        assert result == []

    @pytest.mark.unit
    def test_enforcement_off_returns_empty(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="off")
        result = _check_build_status(trw_dir, config, "implement")
        assert result == []

    def test_missing_cache_returns_info(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        # No build-status.yaml
        result = _check_build_status(trw_dir, config, "implement")
        assert len(result) == 1
        assert result[0].rule == "build_cache_exists"
        assert result[0].severity == "info"


# ---------------------------------------------------------------------------
# _check_build_status — unreadable cache
# ---------------------------------------------------------------------------


class TestCheckBuildStatusUnreadable:
    """Tests for unreadable / corrupt build-status.yaml."""

    def test_corrupt_yaml_returns_warning(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        cache_path = trw_dir / "context" / "build-status.yaml"
        # Write something that can't be parsed as valid YAML (null bytes)
        cache_path.write_bytes(b"\x00\x01\x02\x03 {corrupt: [yaml")
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "implement")
        # Should return a build_cache_readable warning
        rules = [f.rule for f in result]
        assert "build_cache_readable" in rules


# ---------------------------------------------------------------------------
# _check_build_status — fresh passing build
# ---------------------------------------------------------------------------


class TestCheckBuildStatusPassingBuild:
    """Tests for a fresh, passing build-status cache."""

    def test_all_pass_returns_no_failures(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=True, mypy_clean=True, coverage_pct=90.0)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        assert result == []

    def test_tests_failed_adds_failure(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "implement")
        rules = [f.rule for f in result]
        assert "tests_passed" in rules

    def test_mypy_failed_adds_failure(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, mypy_clean=False)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "type_check_clean" in rules

    def test_coverage_low_at_validate_adds_failure(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, coverage_pct=50.0)
        config = TRWConfig(
            build_check_enabled=True,
            build_gate_enforcement="strict",
            build_check_coverage_min=80.0,
        )
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "coverage_min" in rules

    def test_coverage_low_at_implement_no_failure(self, tmp_path: Path) -> None:
        """Coverage check only runs at validate/deliver, not implement."""
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, coverage_pct=10.0)
        config = TRWConfig(
            build_check_enabled=True,
            build_gate_enforcement="strict",
            build_check_coverage_min=80.0,
        )
        result = _check_build_status(trw_dir, config, "implement")
        rules = [f.rule for f in result]
        assert "coverage_min" not in rules

    def test_coverage_low_at_deliver_adds_failure(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, coverage_pct=40.0)
        config = TRWConfig(
            build_check_enabled=True,
            build_gate_enforcement="strict",
            build_check_coverage_min=80.0,
        )
        result = _check_build_status(trw_dir, config, "deliver")
        rules = [f.rule for f in result]
        assert "coverage_min" in rules

    def test_implement_uses_warning_severity(self, tmp_path: Path) -> None:
        """IMPLEMENT gate always uses 'warning' severity, not 'error'."""
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "implement")
        tests_failure = [f for f in result if f.rule == "tests_passed"]
        assert tests_failure
        assert tests_failure[0].severity == "warning"

    def test_strict_validate_uses_error_severity(self, tmp_path: Path) -> None:
        """VALIDATE with strict enforcement uses 'error' severity."""
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        tests_failure = [f for f in result if f.rule == "tests_passed"]
        assert tests_failure
        assert tests_failure[0].severity == "error"

    def test_mypy_scope_only_mypy_checked(self, tmp_path: Path) -> None:
        """When scope='mypy', mypy failures are checked."""
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, mypy_clean=False, scope="mypy")
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "type_check_clean" in rules

    def test_scope_pytest_no_mypy_check(self, tmp_path: Path) -> None:
        """When scope='pytest', mypy is not checked."""
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, mypy_clean=False, scope="pytest")
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "type_check_clean" not in rules


# ---------------------------------------------------------------------------
# _check_build_status — failure snippet formatting
# ---------------------------------------------------------------------------


class TestCheckBuildStatusFailureSnippet:
    """Tests for failure message snippet formatting."""

    def _make_build_status_with_failures(
        self, trw_dir: Path, failures_list: list[str]
    ) -> None:
        """Write build-status.yaml with a list of failure messages."""
        ts_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines = [
            "tests_passed: false\n",
            "mypy_clean: true\n",
            "coverage_pct: 90.0\n",
            "scope: full\n",
            f'timestamp: "{ts_str}"\n',
            "failures:\n",
        ]
        for f in failures_list:
            lines.append(f'  - "{f}"\n')
        (trw_dir / "context" / "build-status.yaml").write_text(
            "".join(lines), encoding="utf-8"
        )

    def test_single_failure_in_snippet(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        self._make_build_status_with_failures(trw_dir, ["test_foo_bar FAILED"])
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        tests_f = [f for f in result if f.rule == "tests_passed"]
        assert tests_f
        assert "test_foo_bar FAILED" in tests_f[0].message
        assert "(+" not in tests_f[0].message

    def test_multiple_failures_snippet_shows_count(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        self._make_build_status_with_failures(
            trw_dir, ["test_foo FAILED", "test_bar FAILED", "test_baz FAILED"]
        )
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        tests_f = [f for f in result if f.rule == "tests_passed"]
        assert tests_f
        assert "+2 more" in tests_f[0].message

    def test_empty_failures_list_no_snippet(self, tmp_path: Path) -> None:
        """Empty failures list → no snippet, just 'Tests did not pass'."""
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        tests_f = [f for f in result if f.rule == "tests_passed"]
        assert tests_f
        assert tests_f[0].message == "Tests did not pass"


# ---------------------------------------------------------------------------
# _check_build_status — invalid timestamp handling
# ---------------------------------------------------------------------------


class TestCheckBuildStatusInvalidTimestamp:
    """Tests for invalid/unparsable timestamp fallback behavior."""

    def test_unparsable_timestamp_treated_as_fresh(self, tmp_path: Path) -> None:
        """Unparsable timestamp is skipped; build treated as fresh."""
        trw_dir = _make_trw_dir(tmp_path)
        cache_path = trw_dir / "context" / "build-status.yaml"
        # Write a status with a garbage timestamp
        cache_path.write_text(
            'tests_passed: false\nmypy_clean: true\ncoverage_pct: 90.0\n'
            'scope: full\ntimestamp: "NOT_A_DATE"\n',
            encoding="utf-8",
        )
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        # Staleness rule NOT added; tests_passed failure IS added
        rules = [f.rule for f in result]
        assert "build_staleness" not in rules
        assert "tests_passed" in rules

    def test_missing_timestamp_treated_as_fresh(self, tmp_path: Path) -> None:
        """Missing timestamp is treated as fresh — no staleness failure."""
        trw_dir = _make_trw_dir(tmp_path)
        cache_path = trw_dir / "context" / "build-status.yaml"
        cache_path.write_text(
            "tests_passed: true\nmypy_clean: true\ncoverage_pct: 90.0\nscope: full\n",
            encoding="utf-8",
        )
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "build_staleness" not in rules


# ---------------------------------------------------------------------------
# _check_build_status — stale build detection
# ---------------------------------------------------------------------------


class TestCheckBuildStatusStaleness:
    """Tests for stale build detection (>30 minutes)."""

    def test_fresh_build_not_stale(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, age_secs=60)  # 1 minute old
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "build_staleness" not in rules

    def test_stale_build_adds_warning(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, age_secs=_BUILD_STALENESS_SECS + 120)  # Over threshold
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "build_staleness" in rules

    def test_stale_build_uses_warning_severity_even_with_strict(self, tmp_path: Path) -> None:
        """Stale builds are always warnings even with strict enforcement."""
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(
            trw_dir, tests_passed=False, age_secs=_BUILD_STALENESS_SECS + 120
        )
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        for failure in result:
            assert failure.severity == "warning", (
                f"Stale build gate should use warning, got {failure.severity} for {failure.rule}"
            )

    def test_build_status_constant_value(self) -> None:
        """Verify staleness threshold is 30 minutes."""
        assert _BUILD_STALENESS_SECS == 1800


# ---------------------------------------------------------------------------
# _best_effort_build_check
# ---------------------------------------------------------------------------


class TestBestEffortBuildCheck:
    """Tests for _best_effort_build_check wrapper."""

    def test_delegates_to_check_build_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify it calls _check_build_status and appends results to failures."""
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False)
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates_build.resolve_trw_dir"
            if hasattr(__import__("trw_mcp.state.validation.phase_gates_build", fromlist=["resolve_trw_dir"]), "resolve_trw_dir")
            else "trw_mcp.state._paths.resolve_trw_dir",
            lambda: trw_dir,
        )
        # Use monkeypatch to redirect resolve_trw_dir inside the module
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_trw_dir", lambda: trw_dir)

        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        failures: list[ValidationFailure] = []
        _best_effort_build_check(config, "validate", failures)
        rules = [f.rule for f in failures]
        assert "tests_passed" in rules

    def test_exception_in_resolve_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If resolve_trw_dir raises, _best_effort_build_check swallows it."""
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_trw_dir", lambda: (_ for _ in ()).throw(OSError("no dir")))
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        # Must NOT raise
        _best_effort_build_check(config, "validate", failures)


# ---------------------------------------------------------------------------
# _best_effort_integration_check
# ---------------------------------------------------------------------------


class TestBestEffortIntegrationCheck:
    """Tests for _best_effort_integration_check inner logic."""

    def test_no_src_dir_is_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When trw-mcp/src/trw_mcp doesn't exist, no failures added."""
        import trw_mcp.state._paths as _paths_mod

        # Point project root to a dir where trw-mcp/src/trw_mcp doesn't exist
        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)

        failures: list[ValidationFailure] = []
        _best_effort_integration_check(failures, severity="warning")
        assert failures == []

    def test_unregistered_tools_add_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unregistered tools in check_integration result add tool_registration failures."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import integration_check as ic

        # Create a fake src dir
        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(
            ic, "check_integration", lambda _: {"unregistered": ["new_tool"], "missing_tests": []}
        )

        failures: list[ValidationFailure] = []
        _best_effort_integration_check(failures, severity="error")
        rules = [f.rule for f in failures]
        assert "tool_registration" in rules

    def test_missing_tests_add_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing test files in check_integration result add test_coverage failures."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import integration_check as ic

        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(
            ic, "check_integration", lambda _: {"unregistered": [], "missing_tests": ["test_foo.py"]}
        )

        failures: list[ValidationFailure] = []
        _best_effort_integration_check(failures, severity="warning")
        rules = [f.rule for f in failures]
        assert "test_coverage" in rules

    def test_exception_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exception in integration check is swallowed."""
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(
            _paths_mod, "resolve_project_root", lambda: (_ for _ in ()).throw(OSError("no root"))
        )
        failures: list[ValidationFailure] = []
        _best_effort_integration_check(failures)


# ---------------------------------------------------------------------------
# _best_effort_orphan_check
# ---------------------------------------------------------------------------


class TestBestEffortOrphanCheck:
    """Tests for _best_effort_orphan_check inner logic."""

    def test_no_src_dir_is_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When trw-mcp/src/trw_mcp doesn't exist, no failures added."""
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)

        failures: list[ValidationFailure] = []
        _best_effort_orphan_check(failures, severity="warning")
        assert failures == []

    def test_orphan_modules_add_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Orphan modules in check_orphan_modules result add module_reachability failures."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import integration_check as ic

        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(
            ic, "check_orphan_modules", lambda _: {"orphans": ["some_orphan_module"]}
        )

        failures: list[ValidationFailure] = []
        _best_effort_orphan_check(failures, severity="warning")
        rules = [f.rule for f in failures]
        assert "module_reachability" in rules

    def test_no_orphans_no_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty orphans list produces no failures."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import integration_check as ic

        src_dir = tmp_path / "trw-mcp" / "src" / "trw_mcp"
        src_dir.mkdir(parents=True)
        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(ic, "check_orphan_modules", lambda _: {"orphans": []})

        failures: list[ValidationFailure] = []
        _best_effort_orphan_check(failures, severity="warning")
        assert failures == []

    def test_exception_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exception in orphan check is swallowed."""
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(
            _paths_mod, "resolve_project_root", lambda: (_ for _ in ()).throw(OSError("no root"))
        )
        failures: list[ValidationFailure] = []
        _best_effort_orphan_check(failures)


# ---------------------------------------------------------------------------
# _get_changed_files (via subprocess mock)
# ---------------------------------------------------------------------------


class TestGetChangedFiles:
    """Tests for _get_changed_files via subprocess mock."""

    def test_returns_deduped_file_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Files from diff, staged, and untracked are merged and deduped."""
        import subprocess as subprocess_mod
        from trw_mcp.state.validation import phase_gates_build as pgb

        call_count = 0

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess_mod.CompletedProcess:  # type: ignore[type-arg]
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # git diff HEAD
                return subprocess_mod.CompletedProcess(cmd, 0, stdout="foo.py\nbar.py\n")
            elif call_count == 2:  # git diff --cached
                return subprocess_mod.CompletedProcess(cmd, 0, stdout="bar.py\nbaz.py\n")
            else:  # git ls-files --others
                return subprocess_mod.CompletedProcess(cmd, 0, stdout="qux.py\n")

        monkeypatch.setattr(subprocess_mod, "run", fake_run)
        result = pgb._get_changed_files(tmp_path)
        # Should have foo.py, bar.py (deduplicated), baz.py, qux.py
        assert "foo.py" in result
        assert "bar.py" in result
        assert "baz.py" in result
        assert "qux.py" in result
        assert len(result) == len(set(result))  # deduped

    def test_returns_empty_on_subprocess_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SubprocessError is caught and empty list returned."""
        import subprocess
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.SubprocessError("fail")),
        )
        result = pgb._get_changed_files(tmp_path)
        assert result == []

    def test_returns_empty_on_file_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FileNotFoundError is caught when git is not installed."""
        import subprocess
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("git not found")),
        )
        result = pgb._get_changed_files(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# _check_nullable_defaults (via subprocess mock)
# ---------------------------------------------------------------------------


class TestCheckNullableDefaults:
    """Tests for _check_nullable_defaults via subprocess mock."""

    def test_detects_nullable_false_column(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Added lines with nullable=False and no server_default produce warning."""
        import subprocess
        from trw_mcp.state.validation import phase_gates_build as pgb

        diff_output = (
            "diff --git a/user.py b/user.py\n"
            "+++ b/user.py\n"
            "+    email = Column(String, nullable=False)\n"
            "+    name = Column(String, nullable=False, server_default='anon')\n"
        )

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            r = subprocess.CompletedProcess(cmd, 0)
            r.stdout = diff_output
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = pgb._check_nullable_defaults(tmp_path, ["backend/models/database/user.py"])
        # Only the line without server_default should be flagged
        assert len(result) == 1
        assert "NOT NULL column" in result[0]
        assert "email" in result[0]

    def test_no_nullable_columns_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No nullable=False columns → empty warnings."""
        import subprocess
        from trw_mcp.state.validation import phase_gates_build as pgb

        diff_output = (
            "diff --git a/user.py b/user.py\n"
            "+++ b/user.py\n"
            "+    name = Column(String)\n"
        )

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            r = subprocess.CompletedProcess(cmd, 0)
            r.stdout = diff_output
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = pgb._check_nullable_defaults(tmp_path, ["backend/models/database/user.py"])
        assert result == []

    def test_subprocess_error_continues_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SubprocessError on a file continues to next file."""
        import subprocess
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.SubprocessError("fail")),
        )
        result = pgb._check_nullable_defaults(
            tmp_path, ["backend/models/database/user.py"]
        )
        assert result == []

    def test_empty_file_list_returns_empty(self, tmp_path: Path) -> None:
        from trw_mcp.state.validation import phase_gates_build as pgb

        result = pgb._check_nullable_defaults(tmp_path, [])
        assert result == []


# ---------------------------------------------------------------------------
# _best_effort_dry_check — enabled path
# ---------------------------------------------------------------------------


class TestBestEffortDryCheckEnabled:
    """Tests for _best_effort_dry_check inner logic when enabled."""

    def test_no_changed_py_files_is_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No changed Python files → no failures added."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["only_a_yaml.yaml"])

        config = TRWConfig(dry_check_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_dry_check(config, failures)
        assert failures == []

    def test_duplicated_blocks_add_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Duplicated blocks from find_duplicated_blocks add duplication_detected failures."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import phase_gates_build as pgb
        from trw_mcp.state import dry_check as dc
        from unittest.mock import MagicMock

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["src/foo.py"])

        # Build a mock DuplicatedBlock
        mock_loc = MagicMock()
        mock_loc.file_path = "src/foo.py"
        mock_loc.start_line = 10
        mock_block = MagicMock()
        mock_block.locations = [mock_loc, mock_loc]
        mock_block.block_hash = "abc123"

        monkeypatch.setattr(dc, "find_duplicated_blocks", lambda *args, **kwargs: [mock_block])

        config = TRWConfig(dry_check_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_dry_check(config, failures)
        rules = [f.rule for f in failures]
        assert "duplication_detected" in rules

    def test_test_files_excluded_from_dry_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Files in /tests/ directories are excluded from dry check."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import phase_gates_build as pgb
        from trw_mcp.state import dry_check as dc

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["trw-mcp/tests/test_foo.py"])

        called_with: list[list[str]] = []

        def fake_find(files: list[str], **kwargs: object) -> list[object]:
            called_with.extend(files)
            return []

        monkeypatch.setattr(dc, "find_duplicated_blocks", fake_find)
        config = TRWConfig(dry_check_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_dry_check(config, failures)
        # test files should have been excluded
        assert not any("/tests/" in f for f in called_with)


# ---------------------------------------------------------------------------
# _best_effort_semantic_check — enabled path
# ---------------------------------------------------------------------------


class TestBestEffortSemanticCheckEnabled:
    """Tests for _best_effort_semantic_check inner logic when enabled."""

    def test_no_scannable_files_is_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No .py/.ts/.tsx/.js files → no failures added."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["only_a_yaml.yaml"])

        config = TRWConfig(semantic_checks_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_semantic_check(config, failures)
        assert failures == []

    def test_semantic_findings_add_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Warning/error findings from run_semantic_checks add failures."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import phase_gates_build as pgb
        from trw_mcp.state import semantic_checks as sc
        from unittest.mock import MagicMock

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["src/foo.py"])

        mock_finding = MagicMock()
        mock_finding.check_id = "NO_BARE_EXCEPT"
        mock_finding.severity = "warning"
        mock_finding.description = "Bare except clause"
        mock_finding.file_path = "src/foo.py"
        mock_finding.line_number = 42

        mock_result = MagicMock()
        mock_result.findings = [mock_finding]
        monkeypatch.setattr(sc, "run_semantic_checks", lambda _: mock_result)

        config = TRWConfig(semantic_checks_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_semantic_check(config, failures)
        rules = [f.rule for f in failures]
        assert "NO_BARE_EXCEPT" in rules

    def test_info_severity_findings_excluded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Info-level findings are not added to failures."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import phase_gates_build as pgb
        from trw_mcp.state import semantic_checks as sc
        from unittest.mock import MagicMock

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: ["src/foo.py"])

        mock_finding = MagicMock()
        mock_finding.check_id = "STYLE_NOTE"
        mock_finding.severity = "info"  # Should be excluded

        mock_result = MagicMock()
        mock_result.findings = [mock_finding]
        monkeypatch.setattr(sc, "run_semantic_checks", lambda _: mock_result)

        config = TRWConfig(semantic_checks_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_semantic_check(config, failures)
        assert failures == []


# ---------------------------------------------------------------------------
# check_migration_gate
# ---------------------------------------------------------------------------


class TestCheckMigrationGate:
    """Tests for check_migration_gate (PRD-INFRA-035)."""

    def test_no_changed_files_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no files changed, no warnings returned."""
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: [])
        result = check_migration_gate(tmp_path)
        assert result == []

    def test_model_change_without_migration_adds_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Model file changed but no migration → warning returned."""
        from trw_mcp.state.validation import phase_gates_build as pgb

        changed = ["backend/models/database/user.py"]
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: changed)
        monkeypatch.setattr(pgb, "_check_nullable_defaults", lambda _root, _files: [])

        result = check_migration_gate(tmp_path)
        assert len(result) == 1
        assert "model" in result[0].lower() or "migration" in result[0].lower()

    def test_model_change_with_migration_no_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Model file changed AND migration present → no model/migration warning."""
        from trw_mcp.state.validation import phase_gates_build as pgb

        changed = [
            "backend/models/database/user.py",
            "backend/alembic/versions/0001_add_user.py",
        ]
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: changed)
        monkeypatch.setattr(pgb, "_check_nullable_defaults", lambda _root, _files: [])

        result = check_migration_gate(tmp_path)
        # Nullable check is empty, no model-without-migration warning
        assert result == []

    def test_nullable_default_warnings_appended(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nullable column warnings from _check_nullable_defaults are included."""
        from trw_mcp.state.validation import phase_gates_build as pgb

        changed = ["backend/models/database/user.py"]
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: changed)
        monkeypatch.setattr(
            pgb,
            "_check_nullable_defaults",
            lambda _root, _files: ["NOT NULL column without server_default in user.py: col = Column(...)"],
        )

        result = check_migration_gate(tmp_path)
        # Both the model-without-migration warning and the nullable warning
        assert any("NOT NULL" in w for w in result)

    def test_non_model_files_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-model file changes don't trigger migration warning."""
        from trw_mcp.state.validation import phase_gates_build as pgb

        changed = ["trw-mcp/src/trw_mcp/tools/ceremony.py"]
        monkeypatch.setattr(pgb, "_get_changed_files", lambda _: changed)

        result = check_migration_gate(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# _best_effort_migration_check
# ---------------------------------------------------------------------------


class TestBestEffortMigrationCheck:
    """Tests for _best_effort_migration_check."""

    def test_disabled_returns_immediately(self) -> None:
        """When migration_gate_enabled=False, nothing is checked."""
        config = TRWConfig(migration_gate_enabled=False)
        failures: list[ValidationFailure] = []
        _best_effort_migration_check(config, failures)
        assert failures == []

    def test_enabled_appends_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When enabled and a warning exists, it's appended as ValidationFailure."""
        import trw_mcp.state._paths as _paths_mod
        from trw_mcp.state.validation import phase_gates_build as pgb

        monkeypatch.setattr(_paths_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(
            pgb,
            "check_migration_gate",
            lambda _: ["model changed without migration"],
        )

        config = TRWConfig(migration_gate_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_migration_check(config, failures)
        rules = [f.rule for f in failures]
        assert "migration_check" in rules

    def test_exception_in_check_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exceptions in the check are swallowed."""
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(
            _paths_mod, "resolve_project_root", lambda: (_ for _ in ()).throw(OSError("no root"))
        )
        config = TRWConfig(migration_gate_enabled=True)
        failures: list[ValidationFailure] = []
        # Must NOT raise
        _best_effort_migration_check(config, failures)


# ---------------------------------------------------------------------------
# _best_effort_dry_check
# ---------------------------------------------------------------------------


class TestBestEffortDryCheck:
    """Tests for _best_effort_dry_check disabled path."""

    def test_disabled_returns_immediately(self) -> None:
        """When dry_check_enabled=False, nothing is checked."""
        config = TRWConfig(dry_check_enabled=False)
        failures: list[ValidationFailure] = []
        _best_effort_dry_check(config, failures)
        assert failures == []

    def test_exception_in_check_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exceptions are swallowed."""
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(
            _paths_mod, "resolve_project_root", lambda: (_ for _ in ()).throw(OSError("no root"))
        )
        config = TRWConfig(dry_check_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_dry_check(config, failures)


# ---------------------------------------------------------------------------
# _best_effort_semantic_check
# ---------------------------------------------------------------------------


class TestBestEffortSemanticCheck:
    """Tests for _best_effort_semantic_check disabled path."""

    def test_disabled_returns_immediately(self) -> None:
        """When semantic_checks_enabled=False, nothing is checked."""
        config = TRWConfig(semantic_checks_enabled=False)
        failures: list[ValidationFailure] = []
        _best_effort_semantic_check(config, failures)
        assert failures == []

    def test_exception_in_check_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exceptions are swallowed."""
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(
            _paths_mod, "resolve_project_root", lambda: (_ for _ in ()).throw(OSError("no root"))
        )
        config = TRWConfig(semantic_checks_enabled=True)
        failures: list[ValidationFailure] = []
        _best_effort_semantic_check(config, failures)
