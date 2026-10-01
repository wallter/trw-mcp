"""Withdrawn client ids (aider, gemini, bare cursor) are ordinary unknown ids (removal audit, REMOVE-S3).

They used to carry their own 'retired' messages, migration hints and uninstall surfaces. Those are
gone: every entry point treats them exactly like any other unknown id, and a stale
``target_platforms`` entry still never crashes a tool or an update.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from structlog.testing import capture_logs

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

WITHDRAWN = ("aider", "gemini", "cursor")


@pytest.mark.parametrize("client_id", WITHDRAWN)
def test_a_withdrawn_id_resolves_through_the_generic_unknown_path(client_id: str) -> None:
    from trw_mcp.models.config._profiles import resolve_client_profile

    with capture_logs() as logs:
        profile = resolve_client_profile(client_id)

    assert profile.client_id == "claude-code"
    assert [entry["log_level"] + ":" + entry["event"] for entry in logs] == ["warning:unknown_client_id_fallback"]


def test_a_stale_target_platforms_list_is_kept_as_written(tmp_path: Path) -> None:
    """Never narrowed, never renamed: an unknown entry stays until the user removes it."""
    from trw_mcp.bootstrap._ide_targets_finalize import _update_config_target_platforms

    config = tmp_path / ".trw" / "config.yaml"
    config.parent.mkdir()
    config.write_text(yaml.safe_dump({"target_platforms": [*WITHDRAWN, "claude-code"]}), encoding="utf-8")
    result: dict[str, list[str]] = {}

    _update_config_target_platforms(tmp_path, [], result)

    assert yaml.safe_load(config.read_text(encoding="utf-8"))["target_platforms"] == [*WITHDRAWN, "claude-code"]
    assert not result.get("warnings")


@pytest.mark.usefixtures("no_memory_daemon")
def test_update_project_on_a_stale_aider_config_completes(initialized_repo: Path) -> None:
    from trw_mcp.bootstrap import update_project

    config = initialized_repo / ".trw" / "config.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    data["target_platforms"] = ["aider", "claude-code"]
    config.write_text(yaml.safe_dump(data), encoding="utf-8")

    result = update_project(initialized_repo)

    assert not result["errors"]
    assert not [w for w in result.get("warnings", []) if "retired" in w]


@pytest.mark.parametrize("client_id", WITHDRAWN)
def test_the_cli_rejects_a_withdrawn_id_as_an_invalid_choice(
    client_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    with pytest.raises(SystemExit):
        _build_arg_parser().parse_args(["init-project", ".", "--ide", client_id])

    assert "invalid choice" in capsys.readouterr().err
