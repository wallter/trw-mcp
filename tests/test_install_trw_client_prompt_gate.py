"""The install-trw.py client-selection gate, exercised on the REAL function.

Background: the Mac install bug — the bash bootstrap pre-creates ``.trw/`` so
``trw-mcp auth login`` can save ``config.yaml``. The legacy gate treated *any*
``.trw/`` directory as evidence of a prior install and silently auto-selected
detected client surfaces, never prompting the user. The fix gates ``is_update``
on a stronger sentinel: ``.trw/installer-meta.yaml`` (only written by
``init-project`` / ``update-project``) OR a non-empty ``target_platforms`` in
prior config.

These tests used to run against a hand-maintained "pure-function replica" of the
gate that lived in this file, with a comment asking the next editor to keep it in
sync with the template. A replica cannot fail when the real gate regresses — it
is a test of the copy — so it is gone. ``phase_project_setup`` is now driven
directly and the assertions are on observable behaviour: which client surfaces
were configured, and whether the first client got ``init-project`` (first-time
install) or ``update-project`` (prior install).

Only the TEMPLATE is loaded, not ``dist/install-trw.py``: the dist artifact is a
generated build output (gitignored) whose parity with the template is a separate
gate (``make installer-drift-check`` / ``tests/test_installer_drift_gate.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from tests._install_trw_pip_target_contract_support import _load_installer_module

_TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


def _project(tmp_path: Path, *, prior_targets: list[str] | None = None, meta: bool = False) -> Path:
    """A target dir shaped like the state under test."""
    target = tmp_path / "project"
    (target / ".git").mkdir(parents=True)
    trw = target / ".trw"
    trw.mkdir()
    if meta:
        (trw / "installer-meta.yaml").write_text("framework_version: v25_TRW\n", encoding="utf-8")
    config = "installation_id: proj\n"
    if prior_targets is not None:
        config += "target_platforms:\n" + "".join(f"  - {t}\n" for t in prior_targets)
    (trw / "config.yaml").write_text(config, encoding="utf-8")
    return target


def _drive(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    *,
    interactive: bool,
    ide: list[str] | None = None,
    detected_clis: list[str] | None = None,
    detected_ides: list[str] | None = None,
    prompt_choice: list[str] | None = None,
) -> tuple[list[str], list[list[str]], list[tuple[list[str], list[str], list[str] | None]]]:
    """Run the real ``phase_project_setup``.

    Returns ``(resolved_targets, trw_cmds_run, prompt_calls)``.
    """
    run_calls: list[list[str]] = []
    prompt_calls: list[tuple[list[str], list[str], list[str] | None]] = []

    def _fake_prompt(clis: list[str], ides: list[str], prior_targets: list[str] | None = None) -> list[str] | None:
        prompt_calls.append((clis, ides, prior_targets))
        return prompt_choice

    monkeypatch.setattr(installer, "_detect_installed_clis", lambda: list(detected_clis or []))
    monkeypatch.setattr(installer, "_detect_project_ides", lambda _p: list(detected_ides or []))
    monkeypatch.setattr(installer, "_prompt_ide_selection", _fake_prompt)
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    monkeypatch.setattr(installer, "run_with_progress", lambda _ui, _label, cmd, **_k: run_calls.append(cmd) or True)

    resolved = installer.phase_project_setup(
        MagicMock(),
        3,
        4,
        sys.executable,
        target,
        False,
        interactive=interactive,
        ide=ide,
    )
    return resolved, run_calls, prompt_calls


def _first_action(run_calls: list[list[str]]) -> str:
    assert run_calls, "phase_project_setup ran no trw-mcp command"
    return run_calls[0][1]


class TestFreshInstallWithBashBootstrap:
    """First `curl ...install.sh | bash`: ``.trw/`` exists, ``installer-meta.yaml`` does not."""

    def test_prompts_when_clients_detected_in_project(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pre-existing client config must NOT auto-select that client silently."""
        target = _project(tmp_path)
        (target / ".cursor").mkdir()

        resolved, run_calls, prompts = _drive(
            installer,
            monkeypatch,
            target,
            interactive=True,
            detected_clis=["claude-code"],
            detected_ides=["cursor-ide"],
            prompt_choice=["claude-code", "cursor-ide"],
        )

        assert prompts, "the user must be asked; no installer-meta.yaml means no prior install"
        assert resolved == ["claude-code", "cursor-ide"], "the prompt choice is honored"
        assert _first_action(run_calls) == "init-project", "no prior install ⇒ init, not update"

    def test_prompts_even_when_only_trw_dir_exists(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``.trw/`` from the bash bootstrap must not be mistaken for a prior install."""
        target = _project(tmp_path)

        resolved, run_calls, prompts = _drive(
            installer, monkeypatch, target, interactive=True, prompt_choice=["claude-code"]
        )

        assert prompts
        assert resolved == ["claude-code"]
        assert _first_action(run_calls) == "init-project"

    def test_user_skipping_prompt_configures_nothing(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _project(tmp_path)

        resolved, run_calls, _ = _drive(
            installer,
            monkeypatch,
            target,
            interactive=True,
            detected_clis=["cursor-ide"],
            detected_ides=["cursor-ide"],
            prompt_choice=None,  # user pressed 's' to skip
        )

        assert resolved == []
        assert run_calls == [], "a skipped prompt must not configure a client anyway"


class TestRealPriorInstall:
    """A real prior init-project / update-project wrote installer-meta.yaml."""

    def test_reuses_prior_targets_without_prompt(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _project(tmp_path, prior_targets=["claude-code"], meta=True)

        resolved, run_calls, prompts = _drive(
            installer,
            monkeypatch,
            target,
            interactive=True,
            detected_clis=["claude-code", "cursor-ide"],
            detected_ides=["claude-code"],
            prompt_choice=["should-not-be-used"],
        )

        assert prompts == [], "a recorded prior choice must not be re-asked"
        assert resolved == ["claude-code"]
        assert _first_action(run_calls) == "update-project", "installer-meta.yaml ⇒ update"

    def test_meta_present_but_no_prior_targets_still_prompts(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Edge: a legacy install left meta but no target_platforms field."""
        target = _project(tmp_path, meta=True)

        resolved, run_calls, prompts = _drive(
            installer,
            monkeypatch,
            target,
            interactive=True,
            detected_clis=["claude-code"],
            detected_ides=["claude-code"],
            prompt_choice=["claude-code"],
        )

        assert prompts, "nothing recorded ⇒ ask"
        assert resolved == ["claude-code"]
        assert _first_action(run_calls) == "update-project"


class TestPriorTargetsWithoutMeta:
    """Defensive: someone hand-edited config.yaml to add target_platforms."""

    def test_prior_targets_alone_treated_as_prior_install(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _project(tmp_path, prior_targets=["codex"])

        resolved, run_calls, prompts = _drive(
            installer, monkeypatch, target, interactive=True, prompt_choice=["should-not-be-used"]
        )

        assert prompts == []
        assert resolved == ["codex"]
        assert _first_action(run_calls) == "update-project"


class TestHeadlessMode:
    """Non-interactive (CI) installs auto-configure without prompting."""

    def test_headless_first_install_ignores_detection(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Detected clients never widen the default set (operator, 2026-10-05)."""
        target = _project(tmp_path)

        resolved, run_calls, prompts = _drive(
            installer,
            monkeypatch,
            target,
            interactive=False,
            detected_clis=["claude-code", "cursor-ide"],
            detected_ides=["cursor-ide", "opencode"],
        )

        assert prompts == []
        assert resolved == ["claude-code", "codex", "antigravity-cli"]
        assert _first_action(run_calls) == "init-project"

    def test_headless_first_install_default_when_nothing_detected(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _project(tmp_path)

        resolved, _run_calls, _ = _drive(installer, monkeypatch, target, interactive=False)

        assert resolved == ["claude-code", "codex", "antigravity-cli"]

    def test_headless_existing_config_target_platforms_preserved(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _project(tmp_path, prior_targets=["cursor-ide"], meta=True)

        resolved, run_calls, _ = _drive(installer, monkeypatch, target, interactive=False)

        assert resolved == ["cursor-ide"]
        assert _first_action(run_calls) == "update-project"

    def test_default_ides_are_the_three_clients(self, installer: ModuleType) -> None:
        assert installer._DEFAULT_IDES == ["claude-code", "codex", "antigravity-cli"]
        assert set(installer._DEFAULT_IDES) <= set(installer._SUPPORTED_IDES)

    def test_interactive_prompt_preselects_default_not_detected(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no tty the prompt returns its pre-selection: the defaults, not the detected clients."""
        monkeypatch.setattr(installer, "_open_tty", lambda: None)

        chosen = installer._prompt_ide_selection(["cursor-cli"], ["cursor-ide"], prior_targets=None)

        assert chosen == ["claude-code", "codex", "antigravity-cli"]


class TestExplicitIDEFlag:
    """``--ide`` bypasses the prompt entirely."""

    @pytest.mark.parametrize("interactive", [True, False])
    def test_ide_override_wins(
        self,
        installer: ModuleType,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        interactive: bool,
    ) -> None:
        target = _project(tmp_path, prior_targets=["codex"], meta=True)

        resolved, run_calls, prompts = _drive(
            installer,
            monkeypatch,
            target,
            interactive=interactive,
            ide=["copilot"],
            detected_clis=["claude-code"],
            detected_ides=["cursor-ide"],
            prompt_choice=["should-not-be-used"],
        )

        assert prompts == []
        assert resolved == ["copilot"]
        assert _first_action(run_calls) == "update-project"


class TestInstallerTemplateAntigravityContract:
    """Contract tests ensuring install-trw.template.py includes antigravity-cli.

    The installer template is a standalone script; these tests scan it as text to
    verify _SUPPORTED_IDES parity with bootstrap._utils.SUPPORTED_IDES. When the
    template _SUPPORTED_IDES drifts from the runtime list, users selecting
    antigravity-cli interactively get a silent ValueError at install time.
    """

    _TEMPLATE = _TEMPLATE

    @pytest.mark.unit
    def test_template_supported_ides_contains_antigravity_cli(self) -> None:
        """install-trw.template.py _SUPPORTED_IDES must include antigravity-cli."""
        text = self._TEMPLATE.read_text(encoding="utf-8")
        assert '"antigravity-cli"' in text, (
            "install-trw.template.py _SUPPORTED_IDES is missing 'antigravity-cli'. "
            "Users who select antigravity-cli interactively will hit a ValueError. "
            "Add it to _SUPPORTED_IDES and _IDE_META in the installer template."
        )

    @pytest.mark.unit
    def test_template_ide_meta_contains_antigravity_cli(self) -> None:
        """install-trw.template.py _IDE_META must have an antigravity-cli entry for the menu."""
        text = self._TEMPLATE.read_text(encoding="utf-8")
        ide_meta_pos = text.find("_IDE_META")
        assert ide_meta_pos != -1, "_IDE_META dict not found in installer template"
        assert '"antigravity-cli"' in text[ide_meta_pos:], (
            "install-trw.template.py _IDE_META is missing an 'antigravity-cli' entry. "
            "The interactive menu will show a blank or raise KeyError for antigravity-cli."
        )

    @pytest.mark.unit
    def test_installed_version_marker_records_resolved_not_intended(self) -> None:
        """Truthful marker (installer-client bug 2026-07-21): the installer must
        stamp ``.trw/installed-version.json`` with the PATH-RESOLVED trw-mcp
        version (what the MCP client actually runs), not the blind intended
        ``TRW_VERSION``. Stamping the intended version when a stale install
        shadows PATH produces a marker that lies and makes the runtime advise a
        ``/mcp`` reload that cannot help."""
        text = self._TEMPLATE.read_text(encoding="utf-8")
        assert "def _resolve_path_trw_mcp_version" in text, (
            "installer must resolve the PATH trw-mcp version to write an honest marker"
        )
        assert '("version", marker_version)' in text, (
            "installed-version.json must record the resolved marker_version, not TRW_VERSION"
        )
        assert 'json.dumps({"version": TRW_VERSION' not in text, (
            "installed-version.json must NOT blindly stamp the intended TRW_VERSION"
        )

    @pytest.mark.unit
    def test_installer_warns_on_stale_shadow_mismatch(self) -> None:
        """When the resolved trw-mcp differs from the freshly-installed version,
        the installer must LOUDLY warn about the shadow (not silently mask it)."""
        text = self._TEMPLATE.read_text(encoding="utf-8")
        assert "resolved_version and effective_version and resolved_version != effective_version" in text, (
            "installer must detect a PATH-shadow mismatch against what THIS run decided "
            "is resident (I2), not the blind bundle constant"
        )
        assert "shadowing" in text.lower(), "installer must name the shadow condition to the user"

    @pytest.mark.unit
    def test_interactive_determination_honors_controlling_tty(self) -> None:
        """Regression guard: the interactive-mode determination must NOT gate on
        ``sys.stdin.isatty()`` alone.

        Under ``curl … | bash`` (the standard install path) install-trw.py's
        stdin is the pipe, so ``sys.stdin.isatty()`` is False. The prompts read
        from ``/dev/tty`` (``_open_tty``), so a fresh install must still ASK which
        clients to configure. Gating interactivity on stdin alone silently
        skipped the client-selection prompt and auto-configured detected clients.
        The determination must fall back to a controlling-TTY check.
        """
        import re

        text = self._TEMPLATE.read_text(encoding="utf-8")
        assert "def _has_controlling_tty" in text, (
            "installer template must define _has_controlling_tty() so curl|bash "
            "installs can detect a reachable /dev/tty and prompt for clients."
        )
        match = re.search(r"^\s*interactive = .*$", text, re.MULTILINE)
        assert match is not None, "could not find the `interactive = ...` determination"
        determination = match.group(0)
        assert "_has_controlling_tty" in determination, (
            "the `interactive = ...` determination must fall back to "
            "_has_controlling_tty() — gating on sys.stdin.isatty() alone skips the "
            f"client-selection prompt under curl|bash. Found: {determination.strip()!r}"
        )


class TestPromptApiKeyConsentGate:
    """PRD-INFRA-200 FR06: consent before device auth.

    ``_prompt_api_key`` used to call ``_device_auth_login`` unconditionally as
    its first action — no confirmation preceded the device-code POST or the
    ``webbrowser.open`` call, so a user who declined by pressing nothing at the
    manual-key fallback had already had a network request made and a browser
    tab opened on their behalf.
    """

    def _ui(self, installer: ModuleType) -> object:
        return installer.UI(interactive=True, quiet=True)

    def test_declining_consent_makes_zero_device_auth_calls(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device_auth = MagicMock()
        monkeypatch.setattr(installer, "_device_auth_login", device_auth)
        monkeypatch.setattr(installer, "prompt_yes_no", lambda *a, **k: False)
        monkeypatch.setattr(installer, "prompt_input", lambda *a, **k: "")

        key = installer._prompt_api_key(self._ui(installer))

        assert device_auth.call_count == 0, "declining consent must never reach _device_auth_login"
        assert key == ""

    def test_accepting_consent_proceeds_to_device_auth_exactly_as_before(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        device_auth = MagicMock(return_value={"api_key": "trw_" + "a" * 40})
        monkeypatch.setattr(installer, "_device_auth_login", device_auth)
        monkeypatch.setattr(installer, "prompt_yes_no", lambda *a, **k: True)
        monkeypatch.setattr(installer, "validate_api_key", lambda _key: True)

        key = installer._prompt_api_key(self._ui(installer))

        assert device_auth.call_count == 1
        assert key == "trw_" + "a" * 40

    def test_non_interactive_run_makes_no_device_auth_call(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-interactive run (no TTY) takes the same default as declining."""
        device_auth = MagicMock()
        monkeypatch.setattr(installer, "_device_auth_login", device_auth)
        # No TTY: prompt_yes_no's own no-TTY fallback returns the default ("n").
        monkeypatch.setattr(installer, "_open_tty", lambda: None)
        monkeypatch.setattr(installer, "prompt_input", lambda *a, **k: "")

        key = installer._prompt_api_key(self._ui(installer))

        assert device_auth.call_count == 0
        assert key == ""


def test_installer_python_preflight_matches_the_pyproject_floor(installer: ModuleType) -> None:
    """PRD-INFRA-200 FR04, codex sol fix-delta round 1 on lane-infra-200-ac: the

    installer's own preflight must refuse the interpreter version the package
    manifests no longer support -- accepting 3.10 here only lets a doomed
    install proceed to the pip-level rejection instead of a clear message.
    """
    assert installer.MIN_PYTHON_VERSION == (3, 11)


def test_declining_platform_consent_still_yields_the_full_local_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: object
) -> None:
    """PRD-INFRA-200 FR06 amendment (B80-41, lead direction): VISION §8 requires

    sign-in to be opt-in and never to cripple the local install. Declining
    platform consent -- interactively or by the non-interactive default --
    must still yield the FULL local install: framework content via
    init-project, embeddings/model-cache readiness, and migration. Only
    platform features (an api key, telemetry, a proprietary license) may be
    absent.

    A ``--script`` run with no api key, no ``--name`` and no prior config has
    ``has_config`` false (``main()``'s own gate), so ``phase_configure`` --
    the ONE phase any consent question lives in -- never runs at all. This
    proves the local-install phases do not depend on it: they are scheduled
    and executed (project_setup=True runs init-project for real, against a
    scratch HOME) entirely before that gate is even evaluated.
    """
    from tests._install_trw_main_support import drive_main
    from tests._install_trw_pip_target_contract_support import _load_installer_module

    installer = _load_installer_module(_TEMPLATE)

    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))  # type: ignore[attr-defined]
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)

    target = tmp_path / "project"
    (target / ".git").mkdir(parents=True)

    # phase_migrate_store is NOT one of drive_main's stubbed phases -- it runs
    # for real. Wrap (not replace) it so the test asserts its actual outcome
    # (codex sol fix-delta round 1: assert an outcome, not just that a stub
    # fired) rather than only the semantic-readiness invocation count.
    migrate_outcomes: list[bool] = []
    real_migrate_store = installer.phase_migrate_store

    def _migrate_and_record(*args: object, **kwargs: object) -> bool:
        outcome = real_migrate_store(*args, **kwargs)  # type: ignore[arg-type]
        migrate_outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(installer, "phase_migrate_store", _migrate_and_record)

    run = drive_main(
        installer,
        monkeypatch,
        target,
        extra_argv=("--ide", "claude-code"),
        project_setup=True,
        semantic="ok",
    )

    # Framework content via init-project: this ran for REAL (project_setup=True
    # bypasses the stub), so its actual on-disk output is the proof, not a mock call.
    assert (target / ".trw" / "config.yaml").is_file(), "init-project must write local framework content"
    assert (target / ".claude" / "agents").is_dir(), "bundled agents must install regardless of consent"

    # Embeddings/model-cache readiness is unconditional, run before the
    # consent-gated phase, and did not depend on it having run.
    assert run.calls["semantic"], "embeddings/model-cache readiness must still run without consent"

    # Migration ran for real (no stub) and reported success -- a fresh project
    # has no checkout store to move, so its own early-return path (`not
    # db.is_file(): return True`) is exactly the "nothing blocked" outcome
    # this asserts, not a mocked call count.
    assert migrate_outcomes == [True], "migration must run and succeed (or have nothing to move) without consent"

    # The one platform-feature phase any consent question lives in never even
    # fired: has_config was false, so nothing platform-side was required for
    # the local install above to have already completed in full.
    assert not run.calls["configure"], "phase_configure (platform features only) must not gate the local install"


def test_declining_platform_consent_runs_the_real_semantic_readiness_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: object
) -> None:
    """INFRA-200-FR06-TEST-GAP: the test above stubs ``phase_semantic_readiness``, so it proves only that

    ``main()`` schedules the phase. Here the phase itself runs for real on the non-interactive
    (consent-declined) path; only its subprocess leaves are replaced -- the model lookup, the
    cache probe, and the fetch. It must find the missing weights, fetch them without asking,
    re-probe to OK, and leave ``embeddings_enabled: true``, all while ``phase_configure`` never runs.
    """
    from tests._install_trw_main_support import drive_main
    from tests._install_trw_pip_target_contract_support import _load_installer_module

    installer = _load_installer_module(_TEMPLATE)

    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))  # type: ignore[attr-defined]
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)

    target = tmp_path / "project"
    (target / ".git").mkdir(parents=True)

    probes = iter([installer.SEMANTIC_MISSING_WEIGHTS, installer.SEMANTIC_OK])
    fetched: list[str] = []
    monkeypatch.setattr(installer, "daemon_embedding_model", lambda *_a, **_k: "org/model")
    monkeypatch.setattr(installer, "probe_semantic_stack", lambda *_a, **_k: next(probes))
    monkeypatch.setattr(installer, "download_semantic_model", lambda _py, model, **_k: fetched.append(model) or True)
    monkeypatch.setattr(installer, "prompt_yes_no", MagicMock(side_effect=AssertionError("--script never prompts")))

    run = drive_main(
        installer,
        monkeypatch,
        target,
        extra_argv=("--ide", "claude-code"),
        project_setup=True,
        semantic=None,
    )

    assert fetched == ["org/model"], "the real phase must repair the missing weights without consent"
    assert next(probes, None) is None, "the phase must re-probe after the fetch"
    assert "embeddings_enabled: true" in (target / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert not run.calls["configure"], "phase_configure (platform features only) must not gate the local install"


def test_the_configure_phase_assertion_above_is_not_vacuous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: object
) -> None:
    """Non-vacuity for the previous test: ``--name`` alone flips ``has_config``

    true, so ``phase_configure`` DOES fire when there is something to
    configure -- proving the omission above is a real signal, not an
    always-empty call list regardless of input.
    """
    from tests._install_trw_main_support import drive_main
    from tests._install_trw_pip_target_contract_support import _load_installer_module

    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))  # type: ignore[attr-defined]
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)

    target = tmp_path / "project"
    (target / ".git").mkdir(parents=True)

    run = drive_main(
        _load_installer_module(_TEMPLATE),
        monkeypatch,
        target,
        extra_argv=("--ide", "claude-code", "--name", "myproj"),
        project_setup=True,
        semantic="ok",
    )

    assert run.calls["configure"], "--name must make has_config true and fire phase_configure"
