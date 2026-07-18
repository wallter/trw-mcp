"""Behavior tests for the ``dispatch:`` config surface (DispatchConfig projection).

Proves the flat ``dispatch_*`` fields are projected into ``config.dispatch`` and
that an operator override in ``.trw/config.yaml`` is actually reflected — a
wiring test (set a non-default, assert it is read back), not an existence test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from trw_mcp.models.config import DispatchConfig, TRWConfig, get_config, reload_config


def test_default_dispatch_config_has_documented_defaults() -> None:
    """``TRWConfig().dispatch`` returns DispatchConfig with the documented defaults."""
    cfg = TRWConfig().dispatch
    assert isinstance(cfg, DispatchConfig)
    # Default derives from SUPPORTED_CLIENTS (the single source) — order follows
    # the DispatchClient Literal, not the prior hand-written list.
    assert cfg.dispatch_enabled_clients == ["claude", "codex", "agy", "opencode"]
    assert cfg.dispatch_default_client == "codex"
    assert cfg.dispatch_default_models == {}
    assert cfg.dispatch_default_timeout_s == 600
    assert cfg.dispatch_default_read_only is True
    assert cfg.dispatch_role_client == {}


def test_projection_copies_non_default_values() -> None:
    """A non-default flat field is actually copied into the projected sub-config.

    Wiring proof: the projection reads the real field value, not the sub-model
    default. (If ``_sub_config`` name-matching broke, these would fall back to
    the DispatchConfig defaults.)
    """
    cfg = TRWConfig(
        dispatch_default_client="claude",
        dispatch_default_timeout_s=42,
        dispatch_default_read_only=False,
        dispatch_default_models={"codex": "gpt-5.5"},
        dispatch_enabled_clients=["codex"],
        dispatch_role_client={"adversarial-audit": "claude"},
    )
    sub = cfg.dispatch
    assert sub.dispatch_default_client == "claude"
    assert sub.dispatch_default_timeout_s == 42
    assert sub.dispatch_default_read_only is False
    assert sub.dispatch_default_models == {"codex": "gpt-5.5"}
    assert sub.dispatch_enabled_clients == ["codex"]
    assert sub.dispatch_role_client == {"adversarial-audit": "claude"}


def test_dispatch_default_timeout_must_be_positive() -> None:
    """The timeout field enforces gt=0 (no magic non-positive default slips in)."""
    with pytest.raises(ValueError):
        TRWConfig(dispatch_default_timeout_s=0)


def test_enabled_clients_typo_fails_loud() -> None:
    """F-14/F-15: a misspelled client in dispatch_enabled_clients raises at load.

    The field is typed ``list[DispatchClient]`` (not ``list[str]``), so a typo
    like "codexx" fails LOUD with a pydantic ValidationError instead of silently
    disabling every client.
    """
    with pytest.raises(ValueError):
        TRWConfig(dispatch_enabled_clients=["codexx"])


def test_enabled_clients_accepts_valid_clients() -> None:
    """A valid subset of supported clients loads fine and projects through."""
    cfg = TRWConfig(dispatch_enabled_clients=["codex", "claude"])
    assert cfg.dispatch.dispatch_enabled_clients == ["codex", "claude"]


def test_dispatch_config_fields_exist_on_trwconfig() -> None:
    """F-17: every DispatchConfig field name also exists on TRWConfig.

    Guards the silent ``_sub_config`` fallback: the projection copies by EXACT
    field-name match, so a DispatchConfig field WITHOUT a matching flat TRWConfig
    field would silently project to the sub-model default (the operator's value
    would never be read). This catches that drift.
    """
    trw_fields = set(TRWConfig.model_fields)
    for name in DispatchConfig.model_fields:
        assert name in trw_fields, f"DispatchConfig.{name} has no matching TRWConfig field"


@pytest.fixture()
def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    project = tmp_path / "proj"
    (project / ".trw").mkdir(parents=True)
    empty_home = tmp_path / "home"
    (empty_home / ".trw").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
    monkeypatch.delenv("TRW_DISPATCH_DEFAULT_CLIENT", raising=False)
    reload_config()
    yield project
    reload_config()


def test_config_yaml_override_reflected_in_dispatch(_project: Path) -> None:
    """A ``dispatch_*`` override in ``.trw/config.yaml`` reaches ``config.dispatch``."""
    (_project / ".trw" / "config.yaml").write_text(
        "dispatch_default_client: claude\n"
        "dispatch_default_timeout_s: 120\n"
        "dispatch_enabled_clients:\n"
        "  - claude\n"
        "  - codex\n"
        "dispatch_default_models:\n"
        "  codex: gpt-5.5\n",
        encoding="utf-8",
    )
    sub = get_config().dispatch
    assert sub.dispatch_default_client == "claude"
    assert sub.dispatch_default_timeout_s == 120
    assert sub.dispatch_enabled_clients == ["claude", "codex"]
    assert sub.dispatch_default_models == {"codex": "gpt-5.5"}
