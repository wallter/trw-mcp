"""INC-078 (swarm-e2e S11 F10): ``profile explain`` names the client profile, its source, and what derived from it.

``explain`` never named the resolved client profile; ``ceremony_tier=MINIMAL`` for a light client reported only
``origin_layer: defaults``; and ``TRW_CLIENT_PROFILE`` -- which only tags distill telemetry -- was silently
ignored, even ``=bogus``. The profile is selected by ``target_platforms[0]`` alone; explain now says so.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.profile import explain_surface


def _explain(monkeypatch: pytest.MonkeyPatch, platforms: list[str], env: str | None = None) -> dict[str, object]:
    if env is None:
        monkeypatch.delenv("TRW_CLIENT_PROFILE", raising=False)
    else:
        monkeypatch.setenv("TRW_CLIENT_PROFILE", env)
    return explain_surface(TRWConfig(target_platforms=platforms), run_dir=None, trw_dir=None)


def test_explain_names_the_profile_and_where_it_was_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _explain(monkeypatch, ["codex"])

    client = payload["client_profile"]
    assert isinstance(client, dict)
    assert client["id"] == "codex"
    assert client["source"] == "target_platforms[0] in .trw/config.yaml"
    assert "notes" not in client


def test_a_light_clients_tier_says_it_derives_from_the_client_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _explain(monkeypatch, ["opencode"])

    tier = next(f for f in payload["fields"] if f["field"] == "ceremony_tier")  # type: ignore[union-attr]
    assert tier["value"] == "MINIMAL"
    assert tier["derived_from"] == "client_profile:opencode (ceremony_mode=light)"


def test_an_unknown_target_platform_is_named_with_the_valid_list(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _explain(monkeypatch, ["bogus"])

    client = payload["client_profile"]
    assert client["id"] == "claude-code"  # type: ignore[index]
    note = " ".join(client["notes"])  # type: ignore[index]
    assert "'bogus'" in note and "not a known client profile" in note and "codex" in note


def test_the_env_var_is_named_as_not_a_selector_with_the_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _explain(monkeypatch, ["claude-code"], env="bogus")

    note = " ".join(payload["client_profile"]["notes"])  # type: ignore[index]
    assert "TRW_CLIENT_PROFILE='bogus'" in note
    assert "does not select" in note
    assert "not a known client id" in note
    assert "set target_platforms in .trw/config.yaml or run init-project --ide <id>" in note


def test_no_target_platforms_reports_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _explain(monkeypatch, [])

    assert payload["client_profile"]["source"] == "default: no target_platforms set"  # type: ignore[index]


def test_the_cli_prints_each_note_loudly_in_human_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse

    from trw_mcp.tools import _profile_cli

    monkeypatch.setenv("TRW_CLIENT_PROFILE", "bogus")
    monkeypatch.setattr(_profile_cli, "_explain", lambda _args: _explain(monkeypatch, ["claude-code"], env="bogus"))

    _profile_cli.run_profile(argparse.Namespace(profile_command="explain", as_json=False))

    err = capsys.readouterr().err
    assert "NOTE: TRW_CLIENT_PROFILE='bogus' is set" in err


def test_a_default_profile_is_not_claimed_to_come_from_a_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """INC-126 (a): the field's default ['claude-code'] was reported as 'target_platforms[0] in .trw/config.yaml'
    even when no config file exists; the source is only the file when the file (or env) actually set it."""
    monkeypatch.delenv("TRW_CLIENT_PROFILE", raising=False)
    monkeypatch.delenv("TRW_TARGET_PLATFORMS", raising=False)
    payload = explain_surface(TRWConfig(), run_dir=None, trw_dir=None)
    client = payload["client_profile"]
    assert client["id"] == "claude-code"  # type: ignore[index]
    assert client["source"] == "default: no target_platforms set"  # type: ignore[index]
