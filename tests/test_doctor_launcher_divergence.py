"""PRD-INFRA-200 FR01: doctor flags a managed client config whose resolved

launcher diverges from the project venv build (N17 regression class: a config
left pointing at a stale global PATH install reports doctor PASS today).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.server._doctor_launcher_divergence import launcher_divergence_row

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_trw_on_path(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """These rows test the LOCATION rule. Which build a bare ``trw-mcp`` finds on the machine running them is the
    version rule's input (``test_doctor_version_agreement``), so keep it out of here."""
    monkeypatch.setenv("PATH", str(tmp_path_factory.mktemp("empty-path")))


def _make_dev_checkout(root: Path) -> None:
    """A directory shaped like this monorepo's own dev checkout (FR01's own scope test)."""
    pkg = root / "trw-mcp" / "src" / "trw_mcp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("__version__ = '99.0.0'\n", encoding="utf-8")


def test_bare_trw_mcp_in_codex_config_is_red(tmp_path: Path) -> None:
    """The exact N17 incident shape: .codex/config.toml names a bare, PATH-resolved trw-mcp."""
    _make_dev_checkout(tmp_path)
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        '[mcp_servers]\n\n[mcp_servers.trw]\ncommand = "trw-mcp"\nargs = []\n', encoding="utf-8"
    )

    status, message = launcher_divergence_row(tmp_path)

    assert status == "WARN"
    assert ".codex/config.toml" in message
    assert "trw-mcp" in message


def test_every_managed_config_resolving_to_the_project_venv_is_green(tmp_path: Path) -> None:
    _make_dev_checkout(tmp_path)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": ".venv/bin/trw-mcp", "args": []}}}), encoding="utf-8"
    )
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": ".venv/bin/trw-mcp", "args": []}}}), encoding="utf-8"
    )
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text(
        json.dumps({"servers": {"trw": {"command": "${workspaceFolder}/.venv/bin/trw-mcp", "args": []}}}),
        encoding="utf-8",
    )
    (tmp_path / "opencode.json").write_text(
        json.dumps({"mcp": {"trw": {"command": [".venv/bin/trw-mcp"], "enabled": True}}}), encoding="utf-8"
    )
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        '[mcp_servers]\n\n[mcp_servers.trw]\ncommand = ".venv/bin/trw-mcp"\nargs = []\n', encoding="utf-8"
    )

    status, message = launcher_divergence_row(tmp_path)

    assert status == "PASS"
    assert "project venv" in message


def test_a_user_project_is_always_green_even_with_a_shadowed_config(tmp_path: Path) -> None:
    """No ``trw-mcp/src/trw_mcp`` under the root: this is not a dev checkout, so the row never applies."""
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        '[mcp_servers]\n\n[mcp_servers.trw]\ncommand = "trw-mcp"\nargs = []\n', encoding="utf-8"
    )

    status, message = launcher_divergence_row(tmp_path)

    assert status == "PASS"
    assert "not a dev checkout" in message


def test_a_python3_fallback_command_is_not_flagged(tmp_path: Path) -> None:
    """``python3 -m trw_mcp.server`` (the third resolver fallback) cannot be verified without

    executing it (NFR01 forbids a subprocess); with no project venv to diverge from it is not flagged.
    """
    _make_dev_checkout(tmp_path)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": "python3", "args": ["-m", "trw_mcp.server"]}}}),
        encoding="utf-8",
    )

    status, _message = launcher_divergence_row(tmp_path)

    assert status == "PASS"


@pytest.mark.parametrize("interpreter", ["python3", "python", "python3.12"])
def test_a_bare_interpreter_launcher_warns_once_a_project_venv_exists(tmp_path: Path, interpreter: str) -> None:
    """INFRA-200-FR01-PYTHON3-LAUNCHER: with ``.venv/bin/trw-mcp`` present the resolver would write that
    launcher, so a config still naming a bare, PATH-resolved interpreter runs a build outside the project venv.
    """
    _make_dev_checkout(tmp_path)
    venv_launcher = tmp_path / ".venv" / "bin" / "trw-mcp"
    venv_launcher.parent.mkdir(parents=True)
    venv_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": interpreter, "args": ["-m", "trw_mcp.server"]}}}),
        encoding="utf-8",
    )

    status, message = launcher_divergence_row(tmp_path)

    assert status == "WARN"
    assert f".mcp.json (resolves to {interpreter!r}" in message


def test_equivalent_project_venv_paths_never_false_warn(tmp_path: Path) -> None:
    """codex sol fix-delta round 1 on lane-infra-200-d: a project-relative,

    ``./``-prefixed, or absolute form of the SAME project-venv path must all
    be treated as identical -- a bare string comparison flagged
    ``./.venv/bin/trw-mcp`` and an equivalent absolute path as a divergence
    from the canonical ``.venv/bin/trw-mcp``, though all three name one file.
    """
    _make_dev_checkout(tmp_path)
    absolute_launcher = str((tmp_path / ".venv" / "bin" / "trw-mcp").resolve())
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": "./.venv/bin/trw-mcp", "args": []}}}), encoding="utf-8"
    )
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": absolute_launcher, "args": []}}}), encoding="utf-8"
    )

    status, _message = launcher_divergence_row(tmp_path)

    assert status == "PASS"


def test_a_commented_vscode_config_is_parsed_not_treated_as_absent(tmp_path: Path) -> None:
    """codex sol fix-delta round 1: VS Code's ``mcp.json`` permits ``//``/``/* */`` comments (JSONC), so a

    strict ``json.loads`` on a commented, shadowed config silently returned "no command" (collapsing into
    the SAME case as a config with no ``trw`` entry) instead of reading the real, divergent command inside it.
    """
    _make_dev_checkout(tmp_path)
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text(
        '// user preference comment\n{"servers": {"trw": {"command": "/foreign/bin/trw-mcp", "args": []}}}\n',
        encoding="utf-8",
    )

    status, message = launcher_divergence_row(tmp_path)

    assert status == "WARN"
    assert "/foreign/bin/trw-mcp" in message


def test_a_genuinely_unparseable_config_warns_instead_of_silently_passing(tmp_path: Path) -> None:
    """codex sol fix-delta round 1: a present-but-unparseable config must never collapse into the same

    silent PASS a config with no ``trw`` entry gets -- it is an operator-visible problem this row exists
    to surface, distinct from an ordinary config shape unrelated to trw-mcp.
    """
    _make_dev_checkout(tmp_path)
    (tmp_path / ".mcp.json").write_text("{not json at all", encoding="utf-8")

    status, message = launcher_divergence_row(tmp_path)

    assert status == "WARN"
    assert ".mcp.json" in message
    assert "could not be parsed" in message


def test_invalid_utf8_in_a_config_warns_instead_of_raising(tmp_path: Path) -> None:
    """codex sol fix-delta round 2 on lane-infra-200-d: ``read_text(encoding="utf-8")`` raises

    ``UnicodeDecodeError`` on invalid UTF-8 bytes, which the extractors' own ``except OSError``
    around that call did not catch -- it escaped to the doctor's generic fail-open FAIL instead
    of this row's own distinct WARN, and skipped checking the rest of the managed configs.
    """
    _make_dev_checkout(tmp_path)
    (tmp_path / ".mcp.json").write_bytes(b"\xff\xfe not valid utf-8")

    status, message = launcher_divergence_row(tmp_path)

    assert status == "WARN"
    assert ".mcp.json" in message
    assert "could not be parsed" in message


def test_invalid_utf8_in_the_codex_toml_config_warns_instead_of_raising(tmp_path: Path) -> None:
    """codex sol fix-delta round 3 on lane-infra-200-d: ``tomllib.load`` on a binary handle also raises

    ``UnicodeDecodeError`` for invalid UTF-8, which ``_codex_command``'s own ``except (OSError,
    tomllib.TOMLDecodeError)`` (a separate handler from the JSON-family one round 2 fixed) did not
    catch -- and continuation to a later config in the same run must still happen.
    """
    _make_dev_checkout(tmp_path)
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_bytes(b"\xff\xfe not valid utf-8")
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": "trw-mcp", "args": []}}}), encoding="utf-8"
    )

    status, message = launcher_divergence_row(tmp_path)

    assert status == "WARN"
    assert ".codex/config.toml" in message
    assert "could not be parsed" in message
    assert ".mcp.json" in message, "a config after the unparseable one must still be checked"


def test_missing_configs_are_simply_skipped(tmp_path: Path) -> None:
    """No managed config files present at all: nothing to flag, still green."""
    _make_dev_checkout(tmp_path)

    status, message = launcher_divergence_row(tmp_path)

    assert status == "PASS"
    assert "project venv" in message


def test_a_project_venv_proxy_launcher_is_not_a_shadow(tmp_path: Path) -> None:
    """CODEX-PROXY-LAUNCHER: with ``shared_mcp`` on, every client config names ``.venv/bin/trw-mcp-proxy``, the
    project venv's own launcher. Reporting it as "outside the project venv" buried the one real offender (a bare
    ``trw-mcp``) under five false ones.
    """
    _make_dev_checkout(tmp_path)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": ".venv/bin/trw-mcp-proxy", "args": []}}}), encoding="utf-8"
    )
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text(
        json.dumps({"servers": {"trw": {"command": "${workspaceFolder}/.venv/bin/trw-mcp-proxy", "args": []}}}),
        encoding="utf-8",
    )
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(
        f'[mcp_servers.trw]\ncommand = "{tmp_path / ".venv" / "bin" / "trw-mcp-proxy"}"\nargs = []\n', encoding="utf-8"
    )

    status, message = launcher_divergence_row(tmp_path)

    assert (status, "project venv" in message) == ("PASS", True)


def test_a_bare_trw_mcp_proxy_still_warns_in_a_dev_checkout(tmp_path: Path) -> None:
    """Recognising the project venv's proxy must not bless a PATH-resolved one (the shadow this row targets)."""
    _make_dev_checkout(tmp_path)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": "trw-mcp-proxy", "args": []}}}), encoding="utf-8"
    )

    status, message = launcher_divergence_row(tmp_path)

    assert status == "WARN"
    assert ".mcp.json (resolves to 'trw-mcp-proxy'" in message
