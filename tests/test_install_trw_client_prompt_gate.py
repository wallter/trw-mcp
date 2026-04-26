"""Tests for the install-trw.py phase_project_setup gate that decides whether
to prompt the user for client selection.

Background: the Mac install bug — bash bootstrap pre-creates ``.trw/`` so
``trw-mcp auth login`` can save ``config.yaml``. The legacy gate treated *any*
``.trw/`` directory as evidence of a prior install and silently auto-selected
detected client surfaces, never prompting the user. The fix gates ``is_update``
on a stronger sentinel: ``.trw/installer-meta.yaml`` (only written by
``init-project`` / ``update-project``) OR a non-empty ``target_platforms`` in
prior config.

The functions under test live in ``install-trw.template.py`` (a standalone
script that cannot be imported). We replicate the gate logic here so the
behavior is testable. The replicated logic must mirror the template — when
the template changes, this file must be updated to match.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SUPPORTED_IDES = [
    "claude-code",
    "cursor-ide",
    "cursor-cli",
    "opencode",
    "codex",
    "copilot",
    "gemini",
    "aider",
]


def _normalize(ides: list[str]) -> list[str]:
    """Minimal stand-in for the template's normalize helper."""
    return list(dict.fromkeys(i for i in ides if i in _SUPPORTED_IDES))


def _resolve_client_targets(
    *,
    target_dir: Path,
    detected_clis: list[str],
    detected_ides: list[str],
    prior_targets: list[str],
    interactive: bool,
    ide_override: list[str] | None,
    prompt_choice: list[str] | None = None,
) -> tuple[list[str] | None, bool]:
    """Pure-function replica of phase_project_setup's client-target gate.

    Returns ``(resolved_targets, is_update)``. ``resolved_targets`` is the
    final list of client IDs to configure (or None when the user skipped
    interactively). ``is_update`` is whether the run is treated as an update
    (vs. first-time install) — drives ``init-project`` vs. ``update-project``.

    ``prompt_choice`` is what the interactive prompt would have returned;
    pass it to verify that the prompt path was actually taken.
    """
    if ide_override is not None:
        # CLI flag takes precedence; first-time vs. update determined by sentinel.
        has_prior_install = (target_dir / ".trw" / "installer-meta.yaml").is_file() or bool(prior_targets)
        return _normalize(ide_override), bool(has_prior_install)

    has_prior_install = (target_dir / ".trw" / "installer-meta.yaml").is_file() or bool(prior_targets)
    is_update = (target_dir / ".trw").is_dir() and has_prior_install

    if has_prior_install and interactive and prior_targets:
        return _normalize(prior_targets), is_update

    if interactive:
        # Always prompt on first-time install OR prior install missing target_platforms.
        return prompt_choice, is_update

    # Headless: prior_targets if any, else detected, else default claude-code.
    headless = _normalize(prior_targets or list(dict.fromkeys(detected_ides + detected_clis)))
    if not headless:
        headless = ["claude-code"]
    return headless, is_update


# ── Tests ────────────────────────────────────────────────────────────────


class TestFreshInstallWithBashBootstrap:
    """Mac/Linux user runs `curl ...install.sh | bash` for the first time.

    Bash bootstrap created ``.trw/`` (for auth) and wrote ``config.yaml`` with
    ``platform_api_key``. There is NO ``installer-meta.yaml`` because no
    init-project has run yet.
    """

    def test_prompts_when_clients_detected_in_project(self, tmp_path: Path) -> None:
        """Pre-existing GEMINI.md must NOT auto-select gemini silently."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("platform_api_key: trw_dk_x\n")
        # User had GEMINI.md from prior tooling — but no prior TRW install.
        (tmp_path / "GEMINI.md").write_text("# my gemini config\n")

        result, is_update = _resolve_client_targets(
            target_dir=tmp_path,
            detected_clis=["claude-code"],
            detected_ides=["gemini"],
            prior_targets=[],
            interactive=True,
            ide_override=None,
            prompt_choice=["claude-code", "gemini"],
        )

        assert is_update is False, "no installer-meta.yaml ⇒ first-time install"
        assert result == ["claude-code", "gemini"], "user's prompt choice is honored"

    def test_prompts_even_when_only_trw_dir_exists(self, tmp_path: Path) -> None:
        """``.trw/`` from bash bootstrap must not be mistaken for prior install."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("platform_api_key: trw_dk_x\n")

        result, is_update = _resolve_client_targets(
            target_dir=tmp_path,
            detected_clis=[],
            detected_ides=[],
            prior_targets=[],
            interactive=True,
            ide_override=None,
            prompt_choice=["claude-code"],
        )

        assert is_update is False
        assert result == ["claude-code"]

    def test_user_skipping_prompt_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / ".trw").mkdir()

        result, _ = _resolve_client_targets(
            target_dir=tmp_path,
            detected_clis=["gemini"],
            detected_ides=["gemini"],
            prior_targets=[],
            interactive=True,
            ide_override=None,
            prompt_choice=None,  # user pressed 's' to skip
        )

        assert result is None


class TestRealPriorInstall:
    """A real prior init-project / update-project wrote installer-meta.yaml."""

    def test_reuses_prior_targets_without_prompt(self, tmp_path: Path) -> None:
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "installer-meta.yaml").write_text("framework_version: v24.6_TRW\n")

        result, is_update = _resolve_client_targets(
            target_dir=tmp_path,
            detected_clis=["claude-code", "gemini"],
            detected_ides=["claude-code"],
            prior_targets=["claude-code"],
            interactive=True,
            ide_override=None,
            prompt_choice=["should-not-be-used"],
        )

        assert is_update is True, "installer-meta.yaml ⇒ real prior install"
        assert result == ["claude-code"], "prior target_platforms is reused"

    def test_meta_present_but_no_prior_targets_still_prompts(self, tmp_path: Path) -> None:
        """Edge: legacy install left meta but no target_platforms field."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "installer-meta.yaml").write_text("framework_version: v24.6_TRW\n")

        result, is_update = _resolve_client_targets(
            target_dir=tmp_path,
            detected_clis=["claude-code"],
            detected_ides=["claude-code"],
            prior_targets=[],
            interactive=True,
            ide_override=None,
            prompt_choice=["claude-code"],
        )

        assert is_update is True, "installer-meta.yaml is the strong sentinel"
        assert result == ["claude-code"], "user is prompted; their choice wins"


class TestPriorTargetsWithoutMeta:
    """Defensive: someone hand-edited config.yaml to add target_platforms."""

    def test_prior_targets_alone_treated_as_prior_install(self, tmp_path: Path) -> None:
        (tmp_path / ".trw").mkdir()
        # No installer-meta.yaml, but config has target_platforms entries.

        result, is_update = _resolve_client_targets(
            target_dir=tmp_path,
            detected_clis=[],
            detected_ides=[],
            prior_targets=["codex"],
            interactive=True,
            ide_override=None,
            prompt_choice=["should-not-be-used"],
        )

        assert is_update is True
        assert result == ["codex"]


class TestHeadlessMode:
    """Non-interactive (CI) installs auto-configure without prompting."""

    def test_headless_first_install_uses_detected(self, tmp_path: Path) -> None:
        (tmp_path / ".trw").mkdir()

        result, is_update = _resolve_client_targets(
            target_dir=tmp_path,
            detected_clis=["claude-code"],
            detected_ides=["gemini"],
            prior_targets=[],
            interactive=False,
            ide_override=None,
        )

        assert is_update is False, "headless first install"
        assert result == ["gemini", "claude-code"]

    def test_headless_first_install_default_when_nothing_detected(self, tmp_path: Path) -> None:
        (tmp_path / ".trw").mkdir()

        result, _ = _resolve_client_targets(
            target_dir=tmp_path,
            detected_clis=[],
            detected_ides=[],
            prior_targets=[],
            interactive=False,
            ide_override=None,
        )

        assert result == ["claude-code"]


class TestExplicitIDEFlag:
    """``--ide`` flag bypasses prompt entirely."""

    @pytest.mark.parametrize("interactive", [True, False])
    def test_ide_override_wins(self, tmp_path: Path, interactive: bool) -> None:
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "installer-meta.yaml").write_text("framework_version: v24.6_TRW\n")

        result, _ = _resolve_client_targets(
            target_dir=tmp_path,
            detected_clis=["claude-code"],
            detected_ides=["gemini"],
            prior_targets=["codex"],
            interactive=interactive,
            ide_override=["copilot"],
            prompt_choice=["should-not-be-used"],
        )

        assert result == ["copilot"]
