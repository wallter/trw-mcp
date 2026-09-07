"""A committed proprietary marker must never abort someone else's public install.

``.trw/proprietary-installed.json`` records which proprietary packages this
machine installed. It was not in the bundled ``.trw/.gitignore``, so it was
committed — and then read back by ``_resolve_proprietary_from_marker`` on
*every* clone. A teammate with no ``credentials.yaml`` and no ``TRW_API_KEY``
running ``curl … | bash`` therefore had the proprietary path inferred for them,
hit ``_PROPRIETARY_PRECONDITION_ERROR``, and watched ``main()`` exit 2 before a
single public package was installed. PRD-INFRA-126 FR05 says the proprietary
path failing must never roll back the public install.

Both halves are covered here:
  (a) the marker is git-ignored, on a fresh install AND on an existing project;
  (b) an INFERRED proprietary path with no key warns and continues, while an
      EXPLICIT ``--with-proprietary`` still fails hard.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from tests._install_trw_main_support import drive_main, make_project
from tests._install_trw_pip_target_contract_support import _load_installer_module

_TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
_MARKER_RULE = "proprietary-installed.json"


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


def _write_marker(target: Path) -> None:
    (target / ".trw").mkdir(parents=True, exist_ok=True)
    (target / ".trw" / "proprietary-installed.json").write_text('{"trw-loop": "0.1.5"}\n', encoding="utf-8")


# ── (a) the marker is ignored, fresh and brownfield ──────────────────────


class TestTheMarkerIsGitIgnored:
    def test_the_bundled_gitignore_lists_the_marker(self) -> None:
        """Fresh installs deploy ``data/gitignore.txt`` verbatim."""
        bundled = (_SRC_ROOT / "data" / "gitignore.txt").read_text(encoding="utf-8")

        assert _MARKER_RULE in [line.strip() for line in bundled.splitlines()]

    def test_update_project_merge_ensures_the_rule_on_an_existing_gitignore(self, tmp_path: Path) -> None:
        """Brownfield half: the bundled file is only deployed on INIT.

        A project installed before this fix already has its own
        ``.trw/.gitignore`` (and probably a committed marker). ``update-project``
        has to add the rule without discarding the user's own ignores.
        """
        from trw_mcp.bootstrap._gitignore_merge import _ensure_credentials_gitignored

        trw = tmp_path / ".trw"
        trw.mkdir()
        custom = "# my own rules\nscratch/\ncredentials.yaml\nbackups/\n"
        (trw / ".gitignore").write_text(custom, encoding="utf-8")
        result: dict[str, list[str]] = {"created": [], "updated": [], "errors": []}

        _ensure_credentials_gitignored(tmp_path, result, dry_run=False)

        merged = (trw / ".gitignore").read_text(encoding="utf-8")
        assert merged.startswith(custom), "user's own ignores must survive"
        assert _MARKER_RULE in [line.strip() for line in merged.splitlines()]
        assert str(trw / ".gitignore") in result["updated"]

        _ensure_credentials_gitignored(tmp_path, result, dry_run=False)
        assert (trw / ".gitignore").read_text(encoding="utf-8") == merged, "idempotent"

    def test_real_git_ignores_the_marker_after_the_merge(self, tmp_path: Path) -> None:
        """The predicate that actually decides whether it gets committed."""
        from trw_mcp.bootstrap._gitignore_merge import _ensure_credentials_gitignored

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        trw = tmp_path / ".trw"
        trw.mkdir()
        (trw / ".gitignore").write_text("reflections/\n", encoding="utf-8")
        _write_marker(tmp_path)

        before = subprocess.run(
            ["git", "check-ignore", "-q", ".trw/proprietary-installed.json"], cwd=tmp_path, check=False
        )
        assert before.returncode != 0, "non-vacuity: not ignored before the merge"

        result: dict[str, list[str]] = {"created": [], "updated": [], "errors": []}
        _ensure_credentials_gitignored(tmp_path, result, dry_run=False)

        after = subprocess.run(
            ["git", "check-ignore", "-q", ".trw/proprietary-installed.json"], cwd=tmp_path, check=False
        )
        assert after.returncode == 0


# ── (b) inferred vs explicit, driven through the real main() ─────────────


class TestAnInferredProprietaryPathNeverBlocksTheInstall:
    def test_an_inferred_path_without_a_key_warns_and_completes(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE defect: a cloned marker + no credentials used to exit 2."""
        target = make_project(tmp_path)
        _write_marker(target)

        run = drive_main(installer, monkeypatch, target)

        assert run.calls["project_setup"], "the public install must still run"
        assert not run.calls["proprietary"], "no entitlement fetch without a key"
        assert any("no platform key" in w for w in run.calls["warnings"]), (
            f"the operator must be told why the proprietary packages were skipped: {run.calls['warnings']}"
        )

    def test_an_explicit_flag_without_a_key_still_fails_hard(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The user who ASKED for proprietary packages gets the actionable error."""
        target = make_project(tmp_path)
        _write_marker(target)

        with pytest.raises(SystemExit) as exc:
            drive_main(installer, monkeypatch, target, extra_argv=("--with-proprietary",))

        assert exc.value.code == 2

    def test_the_env_flag_is_explicit_too(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``TRW_WITH_PROPRIETARY=1`` is a request, not an inference."""
        target = make_project(tmp_path)

        with pytest.raises(SystemExit) as exc:
            drive_main(installer, monkeypatch, target, env={"TRW_WITH_PROPRIETARY": "1"})

        assert exc.value.code == 2

    def test_the_helper_downgrades_instead_of_raising_when_inferred(
        self, installer: ModuleType, tmp_path: Path
    ) -> None:
        """Unit-level: the same decision at the seam that makes it."""
        ui = MagicMock()

        license_key, with_proprietary = installer._resolve_proprietary_license(
            with_proprietary=True,
            explicit_license_key="",
            backend_url="https://example.test/v1",
            prior_config={},
            ui=ui,
            target_dir=tmp_path,
            inferred=True,
        )

        assert (license_key, with_proprietary) == ("", False)
        assert ui.step_warn.call_count == 1

    def test_the_helper_still_raises_when_not_inferred(self, installer: ModuleType, tmp_path: Path) -> None:
        """Negative control for the test above — the default is unchanged."""
        with pytest.raises(ValueError):
            installer._resolve_proprietary_license(
                with_proprietary=True,
                explicit_license_key="",
                backend_url="https://example.test/v1",
                prior_config={},
                ui=MagicMock(),
                target_dir=tmp_path,
                inferred=False,
            )
