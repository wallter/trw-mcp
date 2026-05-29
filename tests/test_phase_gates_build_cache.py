from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests._phase_gates_build_support import _make_trw_dir, _write_build_status
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation.phase_gates_build import (
    _BUILD_STALENESS_SECS,
    _best_effort_build_check,
    _best_effort_check,
    _check_build_status,
)


class TestBestEffortCheck:
    """Unit tests for _best_effort_check exception swallowing."""

    @pytest.mark.unit
    def test_runs_function_normally(self) -> None:
        results: list[int] = []

        def fn() -> None:
            results.append(42)

        _best_effort_check(fn, "test_check")
        assert results == [42]

    @pytest.mark.unit
    def test_swallows_exception(self) -> None:
        def fn() -> None:
            raise RuntimeError("boom")

        _best_effort_check(fn, "test_check")

    @pytest.mark.unit
    def test_swallows_any_exception_type(self) -> None:
        def fn() -> None:
            raise ValueError("bad value")

        _best_effort_check(fn, "val_check")

    @pytest.mark.unit
    def test_mutations_still_visible_on_success(self) -> None:
        out: list[str] = []

        def fn() -> None:
            out.append("done")

        _best_effort_check(fn, "mut_check")
        assert out == ["done"]


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
        result = _check_build_status(trw_dir, config, "implement")
        assert len(result) == 1
        assert result[0].rule == "build_cache_exists"
        assert result[0].severity == "info"


class TestCheckBuildStatusUnreadable:
    """Tests for unreadable / corrupt build-status.yaml."""

    def test_corrupt_yaml_returns_warning(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        cache_path = trw_dir / "context" / "build-status.yaml"
        cache_path.write_bytes(b"\x00\x01\x02\x03 {corrupt: [yaml")
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "implement")
        rules = [f.rule for f in result]
        assert "build_cache_readable" in rules


class TestCheckBuildStatusFailureSnippet:
    """Tests for failure message snippet formatting."""

    def _make_build_status_with_failures(self, trw_dir: Path, failures_list: list[str]) -> None:
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
        (trw_dir / "context" / "build-status.yaml").write_text("".join(lines), encoding="utf-8")

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
            trw_dir,
            ["test_foo FAILED", "test_bar FAILED", "test_baz FAILED"],
        )
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        tests_f = [f for f in result if f.rule == "tests_passed"]
        assert tests_f
        assert "+2 more" in tests_f[0].message

    def test_empty_failures_list_no_snippet(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        tests_f = [f for f in result if f.rule == "tests_passed"]
        assert tests_f
        assert tests_f[0].message == "Tests did not pass"


class TestCheckBuildStatusInvalidTimestamp:
    """Tests for invalid/unparsable timestamp fallback behavior."""

    def test_unparsable_timestamp_treated_as_fresh(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        cache_path = trw_dir / "context" / "build-status.yaml"
        cache_path.write_text(
            'tests_passed: false\nmypy_clean: true\ncoverage_pct: 90.0\nscope: full\ntimestamp: "NOT_A_DATE"\n',
            encoding="utf-8",
        )
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "build_staleness" not in rules
        assert "tests_passed" in rules

    def test_missing_timestamp_treated_as_fresh(self, tmp_path: Path) -> None:
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


class TestCheckBuildStatusStaleness:
    """Tests for stale build detection (>30 minutes)."""

    def test_fresh_build_not_stale(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, age_secs=60)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "build_staleness" not in rules

    def test_stale_build_adds_warning(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, age_secs=_BUILD_STALENESS_SECS + 120)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        rules = [f.rule for f in result]
        assert "build_staleness" in rules

    def test_stale_build_uses_warning_severity_even_with_strict(self, tmp_path: Path) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False, age_secs=_BUILD_STALENESS_SECS + 120)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        result = _check_build_status(trw_dir, config, "validate")
        for failure in result:
            assert failure.severity == "warning", (
                f"Stale build gate should use warning, got {failure.severity} for {failure.rule}"
            )

    def test_build_status_constant_value(self) -> None:
        assert _BUILD_STALENESS_SECS == 1800


class TestBestEffortBuildCheck:
    """Tests for _best_effort_build_check wrapper."""

    def test_delegates_to_check_build_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        trw_dir = _make_trw_dir(tmp_path)
        _write_build_status(trw_dir, tests_passed=False)
        monkeypatch.setattr(
            "trw_mcp.state.validation.phase_gates_build.resolve_trw_dir"
            if hasattr(
                __import__("trw_mcp.state.validation.phase_gates_build", fromlist=["resolve_trw_dir"]),
                "resolve_trw_dir",
            )
            else "trw_mcp.state._paths.resolve_trw_dir",
            lambda: trw_dir,
        )
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_trw_dir", lambda: trw_dir)
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        failures: list[ValidationFailure] = []
        _best_effort_build_check(config, "validate", failures)
        rules = [f.rule for f in failures]
        assert "tests_passed" in rules

    def test_exception_in_resolve_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import trw_mcp.state._paths as _paths_mod

        monkeypatch.setattr(_paths_mod, "resolve_trw_dir", lambda: (_ for _ in ()).throw(OSError("no dir")))
        failures: list[ValidationFailure] = []
        config = TRWConfig(build_check_enabled=True, build_gate_enforcement="strict")
        _best_effort_build_check(config, "validate", failures)
