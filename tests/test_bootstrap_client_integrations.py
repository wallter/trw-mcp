"""FIX 4 regression: per-IDE installer isolation + init_project exception boundary.

``init_project`` documents a dict-contract return, but a raised exception from a
per-IDE installer (installers were unguarded in ``run_install_integrations``)
escaped as a raw traceback. The fix guards each per-IDE dispatch individually and
wraps ``init_project``'s body in a top-level try/except that appends to
``result['errors']`` and returns the partial dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import _client_integrations as ci
from trw_mcp.bootstrap import _init_project as ip


@pytest.mark.unit
class TestRunInstallIntegrationsIsolation:
    """One failing per-IDE installer must not abort the remaining installers."""

    def test_failing_ide_isolated_others_still_install(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def good_install(
            target: Path, force: bool, result: dict[str, list[str]], ide_targets: list[str] | None
        ) -> None:
            calls.append("good")
            result["created"].append("good-installed")

        def bad_install(target: Path, force: bool, result: dict[str, list[str]], ide_targets: list[str] | None) -> None:
            calls.append("bad")
            raise RuntimeError("boom in bad installer")

        def _noop_update(
            target: Path, result: dict[str, list[str]], ide_override: str | None, mh: dict[str, str] | None
        ) -> None:
            return None

        bad = ci.ClientIntegration("bad", ("y",), bad_install, _noop_update)
        good = ci.ClientIntegration("good", ("x",), good_install, _noop_update)
        # bad runs first → prove good still runs after bad raises.
        monkeypatch.setattr(ci, "iter_matching_integrations", lambda ide: (bad, good))

        result: dict[str, list[str]] = {"created": [], "errors": []}
        # Must not raise.
        ci.run_install_integrations(tmp_path, ["x", "y"], force=False, result=result)

        assert calls == ["bad", "good"]
        assert "good-installed" in result["created"]
        assert any("bad install failed" in e and "RuntimeError" in e for e in result["errors"])

    def test_no_failure_reports_no_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def ok_install(target: Path, force: bool, result: dict[str, list[str]], ide_targets: list[str] | None) -> None:
            result["created"].append("ok")

        def _noop_update(
            target: Path, result: dict[str, list[str]], ide_override: str | None, mh: dict[str, str] | None
        ) -> None:
            return None

        integ = ci.ClientIntegration("ok", ("x",), ok_install, _noop_update)
        monkeypatch.setattr(ci, "iter_matching_integrations", lambda ide: (integ,))

        result: dict[str, list[str]] = {"created": [], "errors": []}
        ci.run_install_integrations(tmp_path, ["x"], force=False, result=result)

        assert result["created"] == ["ok"]
        assert result["errors"] == []


@pytest.mark.unit
class TestRunUpdateIntegrationsIsolation:
    """One failing updater must not abort later registry integrations."""

    def test_failing_update_isolated_others_still_update(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def _noop_install(
            target: Path, force: bool, result: dict[str, list[str]], ide_targets: list[str] | None
        ) -> None:
            return None

        def bad_update(
            target: Path, result: dict[str, list[str]], ide_override: str | None, mh: dict[str, str] | None
        ) -> None:
            calls.append("bad")
            raise RuntimeError("boom in bad updater")

        def good_update(
            target: Path, result: dict[str, list[str]], ide_override: str | None, mh: dict[str, str] | None
        ) -> None:
            calls.append("good")
            result["updated"].append("good-updated")

        bad = ci.ClientIntegration("bad", ("y",), _noop_install, bad_update)
        good = ci.ClientIntegration("good", ("x",), _noop_install, good_update)
        monkeypatch.setattr(ci, "iter_matching_integrations", lambda ide: (bad, good))

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        ci.run_update_integrations(
            tmp_path,
            ["x", "y"],
            ide_override=None,
            result=result,
            manifest_hashes=None,
        )

        assert calls == ["bad", "good"]
        assert result["updated"] == ["good-updated"]
        assert any("bad update failed" in error and "RuntimeError" in error for error in result["errors"])


@pytest.mark.unit
class TestInitProjectExceptionBoundary:
    """A phase exception must be captured into the dict contract, never escape."""

    def test_phase_exception_captured_in_result(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".git").mkdir()

        def _boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("kaboom in a phase")

        monkeypatch.setattr(ip, "_run_init_phases", _boom)

        # Must return a dict, not raise.
        result = ip.init_project(tmp_path)

        assert isinstance(result, dict)
        assert any("init-project failed" in e and "RuntimeError" in e for e in result["errors"])

    def test_non_git_repo_still_returns_dict(self, tmp_path: Path) -> None:
        """The pre-existing git guard still short-circuits with the dict contract."""
        result = ip.init_project(tmp_path)  # no .git/
        assert isinstance(result, dict)
        assert any("not a git repository" in e for e in result["errors"])
