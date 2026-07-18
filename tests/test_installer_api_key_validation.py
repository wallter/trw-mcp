"""Installer api-key validator + PEP 668 bootstrap-resilience wiring guards.

Production feedback ``sub_CgkvsxHaWprG3ZXq`` reported two installer defects:

1. ``--api-key`` was falsely rejected for legitimate keys. ``validate_api_key``
   used the character class ``[a-zA-Z0-9_]`` which excludes ``-``, but device
   keys are ``trw_dk_`` + ``secrets.token_urlsafe(32)`` (base64url, which uses
   BOTH ``-`` and ``_``). Every device key carrying a hyphen was rejected. The
   fix widens the class to ``[a-zA-Z0-9_-]``. These tests drive the pure
   ``validate_api_key`` surface loaded from the template by file path.

2. The curl|bash bootstraps (``platform/public/install.sh`` and
   ``scripts/install.sh``) tried three ``pip`` forms and, on an
   externally-managed Python (PEP 668, the Homebrew/modern-distro default),
   never fell back to ``pipx`` — then printed the exact ``pip`` command that
   had just failed. The bootstrap-wiring guards below assert (grep-level — a
   wiring guard, NOT an end-to-end shell test) that both bootstraps now invoke
   ``pipx`` in the fallback ladder and no longer suggest the failed command.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_TEMPLATE = _TESTS_DIR.parent / "scripts" / "install-trw.template.py"
_REPO_ROOT = _TESTS_DIR.parent.parent
_SERVED_BOOTSTRAP = _REPO_ROOT / "platform" / "public" / "install.sh"
_REPO_BOOTSTRAP = _REPO_ROOT / "scripts" / "install.sh"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_api_key", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer() -> ModuleType:
    return _load()


# ── api-key validator: base64url (hyphen) acceptance ─────────────────────────


def test_device_key_with_hyphen_accepted(installer: ModuleType) -> None:
    """A realistic ``trw_dk_`` device key containing a hyphen must validate.

    ``secrets.token_urlsafe(32)`` yields base64url; hyphens are common. This is
    the exact class of key the production report saw falsely rejected.
    """
    key = "trw_dk_xY3-9abcDEF_ghijKLmno-pqrsTUvwx-yz012345AB6789"
    assert "-" in key  # guard: the case must actually exercise the hyphen path
    assert installer.validate_api_key(key) is True


def test_org_key_with_hyphen_accepted(installer: ModuleType) -> None:
    """An org key (``trw_`` + token_urlsafe, no ``dk_``) with a hyphen validates."""
    key = "trw_AbC-def_GHI-jklMNO_pqr-stuVWX_yz0-123456789abcDEF"
    assert installer.validate_api_key(key) is True


def test_plain_alphanumeric_key_still_accepted(installer: ModuleType) -> None:
    """Regression: keys without a hyphen keep validating (no behavior loss)."""
    assert installer.validate_api_key("trw_dk_abc123DEF456ghi789") is True
    assert installer.validate_api_key("trw_abc123DEF456ghi789") is True


def test_bad_prefix_rejected(installer: ModuleType) -> None:
    """A key without the ``trw_`` prefix is rejected."""
    assert installer.validate_api_key("sk_dk_abc-123") is False
    assert installer.validate_api_key("trwdk_abc-123") is False
    assert installer.validate_api_key("dk_abc-123") is False


def test_empty_and_prefix_only_rejected(installer: ModuleType) -> None:
    """Empty string and a bare prefix (no body) are rejected."""
    assert installer.validate_api_key("") is False
    assert installer.validate_api_key("trw_") is False


def test_illegal_chars_still_rejected(installer: ModuleType) -> None:
    """Widening to base64url must NOT admit spaces, slashes, or shell metachars."""
    assert installer.validate_api_key("trw_dk_abc def") is False
    assert installer.validate_api_key("trw_dk_abc/def") is False
    assert installer.validate_api_key("trw_dk_abc$def") is False


def test_overlong_key_rejected(installer: ModuleType) -> None:
    """The 128-char ceiling is preserved."""
    assert installer.validate_api_key("trw_dk_" + "a-" * 100) is False


# ── api-key validator: whitespace / full-string anchoring ────────────────────
#
# Codex LOW + P2-2: the validator used ``re.match(r"...$", key)``. ``$`` matches
# BEFORE a trailing newline, so ``re.match`` accepted ``"trw_abc\n"`` (a pasted
# key with a stray newline). ``re.fullmatch`` requires the WHOLE string to match
# and the char class excludes whitespace, so trailing/leading/embedded newlines,
# spaces, and CRLF are all rejected.


def test_trailing_newline_rejected(installer: ModuleType) -> None:
    """A key with a trailing newline (re.match+$ regression) must be rejected."""
    assert installer.validate_api_key("trw_abc\n") is False
    assert installer.validate_api_key("trw_dk_abc123DEF\n") is False


def test_trailing_carriage_return_rejected(installer: ModuleType) -> None:
    """CRLF / bare CR terminators must be rejected."""
    assert installer.validate_api_key("trw_abc\r\n") is False
    assert installer.validate_api_key("trw_abc\r") is False


def test_trailing_and_leading_space_rejected(installer: ModuleType) -> None:
    """Surrounding whitespace must be rejected (not silently trimmed here)."""
    assert installer.validate_api_key("trw_abc ") is False
    assert installer.validate_api_key(" trw_abc") is False
    assert installer.validate_api_key("trw_abc\t") is False


def test_embedded_newline_rejected(installer: ModuleType) -> None:
    """A newline in the middle must not slip through via multiline anchoring."""
    assert installer.validate_api_key("trw_abc\ndef") is False


def test_exact_128_char_key_accepted(installer: ModuleType) -> None:
    """A key whose total length is exactly 128 is accepted (boundary)."""
    key = "trw_" + "a" * 124
    assert len(key) == 128
    assert installer.validate_api_key(key) is True


def test_129_char_key_rejected(installer: ModuleType) -> None:
    """One over the ceiling (129 chars) is rejected (boundary)."""
    key = "trw_" + "a" * 125
    assert len(key) == 129
    assert installer.validate_api_key(key) is False


# ── pip_install PEP 668 dedicated-venv fallback (finding 1, third-stage) ──────
#
# The third-stage bootstrap (this python installer) previously escalated
# normal -> --user -> --break-system-packages and, on a PEP 668 Python that
# declined system mutation, only PRINTED a pipx suggestion and died. pipx is
# insufficient here because downstream steps must ``import trw_mcp`` in the SAME
# interpreter, so the fix is a dedicated importable venv whose interpreter is
# rebound downstream. These tests drive that behavior functionally (no real
# subprocess): build_install_cmd is stubbed to a trivial argv so the assertion
# is purely which interpreter each install command targets.


def test_pip_install_falls_back_to_dedicated_venv_on_pep668(installer: ModuleType, monkeypatch, tmp_path) -> None:
    """On PEP 668 (system+user pip fail, system mutation declined), pip_install
    must install into a dedicated venv and return True — never just die."""
    module = installer
    module._INSTALL_BACKEND = ("pip", ["sys-python", "-B", "-m", "pip", "install"])
    module._FALLBACK_VENV_PYTHON = None
    module._ALLOW_SYSTEM_PYTHON = False  # operator declined --break-system-packages

    venv_dir = tmp_path / "venv"
    venv_python = venv_dir / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n")  # pre-existing venv → _ensure reuses it
    monkeypatch.setenv("TRW_FALLBACK_VENV", str(venv_dir))

    # Trivial, backend-free command builder so we assert on the interpreter only.
    monkeypatch.setattr(module, "build_install_cmd", lambda py, ui, packages, **kw: [py, "install", *packages])
    calls: list[list[str]] = []

    def fake_run_quiet(cmd, **_kw):
        calls.append(cmd)
        # System-python pip attempts FAIL (PEP 668); venv-python installs succeed.
        return cmd[0] == str(venv_python)

    monkeypatch.setattr(module, "_run_quiet", fake_run_quiet)

    ok = module.pip_install("sys-python", "trw-mcp", "trw-mcp", MagicMock())

    assert ok is True, "PEP 668 install must succeed via the venv fallback, not die"
    assert module._FALLBACK_VENV_PYTHON == str(venv_python), "venv interpreter must be cached"
    # The successful install command targeted the venv interpreter, and a
    # system-python attempt was tried first (proving the ladder ran).
    assert any(cmd[0] == str(venv_python) for cmd in calls)
    assert any(cmd[0] == "sys-python" for cmd in calls)


def test_pip_install_routes_subsequent_packages_into_existing_venv(installer: ModuleType, monkeypatch) -> None:
    """Once the venv fallback is active, later packages route into the SAME venv
    (one interpreter owns the whole stack) without retrying the system Python."""
    module = installer
    module._FALLBACK_VENV_PYTHON = "/fake/venv/bin/python"
    module._INSTALL_BACKEND = ("pip", ["sys-python", "-B", "-m", "pip", "install"])
    monkeypatch.setattr(module, "build_install_cmd", lambda py, ui, packages, **kw: [py, "install", *packages])
    calls: list[list[str]] = []

    def fake_run_quiet(cmd, **_kw):
        calls.append(cmd)
        return True

    monkeypatch.setattr(module, "_run_quiet", fake_run_quiet)

    ok = module.pip_install("sys-python", "sentence-transformers>=2.0.0", "st", MagicMock())

    assert ok is True
    assert calls, "an install command must have been issued"
    assert all(cmd[0] == "/fake/venv/bin/python" for cmd in calls)
    assert not any(cmd[0] == "sys-python" for cmd in calls), "must not touch system Python"


def test_pip_install_venv_fallback_never_reached_for_pip_target(installer: ModuleType, monkeypatch) -> None:
    """The --pip-target path is its own isolation and must NOT reroute to a venv,
    even when a fallback venv is already cached from a prior call."""
    module = installer
    module._FALLBACK_VENV_PYTHON = "/fake/venv/bin/python"
    module._INSTALL_BACKEND = ("pip", ["sys-python", "-B", "-m", "pip", "install"])
    monkeypatch.setattr(module, "build_install_cmd", lambda py, ui, packages, **kw: [py, "install-target", *packages])
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "_run_quiet", lambda cmd, **_kw: (calls.append(cmd), True)[1])

    ok = module.pip_install("sys-python", "trw-mcp[ai]", "trw-mcp", MagicMock(), target_dir="/tmp/trw-pip")

    assert ok is True
    # target_dir set → top reroute is skipped; the system-python target install runs.
    assert calls and calls[0][0] == "sys-python"


# ── bootstrap wiring guards (grep-level, not e2e shell execution) ────────────


def _read(path: Path) -> str:
    assert path.is_file(), f"expected bootstrap at {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("bootstrap", [_SERVED_BOOTSTRAP, _REPO_BOOTSTRAP])
def test_bootstrap_has_pipx_fallback_rung(bootstrap: Path) -> None:
    """Both bootstraps must invoke ``pipx install trw-mcp`` in the ladder.

    Wiring guard: proves the pipx rung exists in the fallback ladder. It does
    NOT execute the shell (no e2e harness in-repo), so it cannot prove runtime
    behavior — only that the rung is present.
    """
    text = _read(bootstrap)
    assert "pipx install trw-mcp" in text
    # Guard the fallback is a real command-gated rung, not just prose in --help.
    assert "command -v pipx" in text


@pytest.mark.parametrize("bootstrap", [_SERVED_BOOTSTRAP, _REPO_BOOTSTRAP])
def test_bootstrap_pipx_rung_precedes_break_system_gate(bootstrap: Path) -> None:
    """The pipx rung must come BEFORE the --break-system-packages gate."""
    text = _read(bootstrap)
    pipx_at = text.find("pipx install trw-mcp")
    # Anchor on the ladder's break-system SUCCESS warn (identical in both
    # bootstrap styles and unique to the rung region) — the bare
    # ``--break-system-packages`` token also appears earlier in --help / comment
    # prose, which would give a false-negative ordering.
    break_at = text.find("Installed with --break-system-packages")
    assert pipx_at != -1 and break_at != -1
    assert pipx_at < break_at, "pipx rung must precede the destructive break-system gate"


@pytest.mark.parametrize("bootstrap", [_SERVED_BOOTSTRAP, _REPO_BOOTSTRAP])
def test_bootstrap_does_not_suggest_the_failed_pip_command(bootstrap: Path) -> None:
    """The final failure guidance must not echo the pip command that just failed."""
    text = _read(bootstrap)
    assert "Try: $PYTHON -m pip install trw-mcp" not in text
    assert "pip install failed. Try:" not in text


@pytest.mark.parametrize("bootstrap", [_SERVED_BOOTSTRAP, _REPO_BOOTSTRAP])
def test_bootstrap_pipx_runs_ensurepath_for_path_persistence(bootstrap: Path) -> None:
    """Codex MEDIUM: after a pipx install, persist the bin dir via ensurepath.

    Prepending to the CURRENT shell PATH only helps this process; a non-
    interactive MCP client spawned later needs the bin dir on the PERSISTED
    PATH. ``pipx ensurepath`` writes it into the user's shell profile.
    """
    text = _read(bootstrap)
    assert "pipx ensurepath" in text


@pytest.mark.parametrize("bootstrap", [_SERVED_BOOTSTRAP, _REPO_BOOTSTRAP])
def test_bootstrap_warns_when_pipx_bindir_not_on_path(bootstrap: Path) -> None:
    """The bootstrap must warn (naming the dir) when the pipx bin dir is off PATH.

    Honesty guard for Codex MEDIUM: the success banner must not overclaim that
    non-interactive clients will find ``trw-mcp`` — when the bin dir was not
    already on the inherited PATH, an explicit warning naming the dir + the
    re-login / absolute-path caveat is required.
    """
    text = _read(bootstrap)
    assert "was not on your PATH" in text
    # The warning must mention the non-interactive / absolute-path caveat.
    assert "absolute path" in text
