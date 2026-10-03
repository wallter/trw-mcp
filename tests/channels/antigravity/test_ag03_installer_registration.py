"""The bootstrap no longer writes the AG-03 hook to a path agy does not read (UF-BOOT-08).

History: AG-03 was written to ``.antigravitycli/hooks.json`` (flat
``{"PreToolUse": [{"matcher", "command"}]}``) on the strength of agy v1.0.2 binary
string analysis.  Re-verified 2026-10-02 against agy 1.2.14 in a scratch workspace:

- ``<workspace>/.agents/hooks.json`` with the grouped, named-hook schema
  ``{"<name>": {"PreToolUse": [{"matcher", "hooks": [{"type", "command"}]}]}}`` is
  listed by ``agy -p /hooks --output-format json``.
- The same flat file under ``.antigravitycli/`` or ``.agents/`` is NOT listed.
- agy's embedded hooks guide says the hook payload is camelCase (``toolCall.name``) and a
  PreToolUse hook answers ``{"decision": "allow|deny|ask|force_ask"}``; the installed
  script answers ``{"continue": true}`` and reads snake_case keys, so it would be inert
  even at the right path.

Rewriting the installer, its uninstall surfaces and the managed-artifact recorder is
wider than this packet, so the bootstrap withholds the hook rather than guess
(never write an unverified hook).  These tests pin that behaviour.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import structlog

from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

_LEGACY_HOOKS_JSON = ".antigravitycli/hooks.json"
_LEGACY_SCRIPT = ".antigravitycli/hooks/trw_before_edit_telemetry.py"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    return tmp_path


def test_install_writes_no_legacy_hook_files(repo: Path) -> None:
    result = install_antigravity_distill_channels(repo)

    assert not (repo / _LEGACY_HOOKS_JSON).exists()
    assert not (repo / _LEGACY_SCRIPT).exists()
    assert _LEGACY_HOOKS_JSON not in result["created"] + result["updated"] + result["preserved"]
    assert _LEGACY_SCRIPT not in result["created"] + result["updated"] + result["preserved"]
    assert not result["errors"], result["errors"]


def test_install_writes_no_agents_hooks_json_either(repo: Path) -> None:
    """No guessed schema at the new path: nothing is written until it is wired end to end."""
    install_antigravity_distill_channels(repo, force=True)

    assert not (repo / ".agents" / "hooks.json").exists()


def test_install_never_calls_the_legacy_installer(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.channels.antigravity as ag

    def _boom(*_a: object, **_k: object) -> dict[str, object]:
        raise AssertionError("legacy install_before_edit_hook was called by the bootstrap")

    monkeypatch.setattr(ag, "install_before_edit_hook", _boom)

    result = install_antigravity_distill_channels(repo, force=True)

    assert not any("AG-03" in e for e in result["errors"]), result["errors"]


def test_install_logs_why_the_hook_is_withheld(repo: Path) -> None:
    with structlog.testing.capture_logs() as logs:
        install_antigravity_distill_channels(repo)

    skipped = [e for e in logs if e.get("event") == "ag03_hook_skipped"]
    assert len(skipped) == 1, logs
    assert skipped[0]["reason"] == "unverified_path_and_schema"
    assert ".agents/hooks.json" in skipped[0]["agy_reads"]


def test_install_leaves_an_existing_legacy_hooks_json_byte_identical(repo: Path) -> None:
    legacy = repo / _LEGACY_HOOKS_JSON
    legacy.parent.mkdir(parents=True)
    body = json.dumps({"PreToolUse": [{"matcher": "glob", "command": "echo USER_PRE_HOOK"}]}, indent=2) + "\n"
    legacy.write_text(body, encoding="utf-8")

    install_antigravity_distill_channels(repo, force=True)

    assert legacy.read_text(encoding="utf-8") == body


def test_manifest_and_subagent_steps_still_run(repo: Path) -> None:
    install_antigravity_distill_channels(repo)

    assert (repo / ".trw" / "channels" / "manifest.yaml").exists()
