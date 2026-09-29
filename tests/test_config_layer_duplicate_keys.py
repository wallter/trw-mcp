"""A repeated config key must not take every tool down, and must never leak a value (P0 after promotion #4).

An IDENTICAL repeat is read once and logged by key and line; a CONFLICTING repeat still refuses (which value
wins would be a guess) with a message naming the key and line, not the values. Writes keep refusing a file
that repeats a key. Sentinel values prove nothing on a config line reaches an error or a log.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from trw_mcp.exceptions import StateError
from trw_mcp.state import _factory_experiment as fx
from trw_mcp.state._namespace_pin_read import read_config_layer
from trw_mcp.state.persistence import FileStateReader

pytestmark = pytest.mark.integration

_IDENTICAL = "framework_version: v1\ncc03_hook_enabled: false\nplatform_api_key: SECRET-A\ncc03_hook_enabled: false\n"
_CONFLICTING = "framework_version: v1\nplatform_api_key: SECRET-A\nplatform_api_key: SECRET-B\n"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_an_identical_repeat_is_read_once_and_logged_by_key_and_line(tmp_path: Path) -> None:
    path = _write(tmp_path, _IDENTICAL)

    with capture_logs() as logs:
        layer = read_config_layer(path)

    assert layer["cc03_hook_enabled"] is False and layer["platform_api_key"] == "SECRET-A"
    (entry,) = [e for e in logs if e["event"] == "yaml_identical_duplicate_key_tolerated"]
    assert entry["keys"] == ["cc03_hook_enabled"] and entry["lines"] == [4]
    assert "SECRET" not in repr(logs), "a config value reached a log line"


def test_a_conflicting_repeat_refuses_naming_the_key_and_line_but_no_value(tmp_path: Path) -> None:
    path = _write(tmp_path, _CONFLICTING)

    with pytest.raises(StateError) as raised:
        read_config_layer(path)

    text = str(raised.value)
    assert "platform_api_key" in text and "line 3" in text and "values not shown" in text, text
    assert "SECRET" not in text and raised.value.__cause__ is None, "the ruamel error text quotes both values"


def test_a_syntax_error_names_the_kind_and_line_and_never_the_source_text(tmp_path: Path) -> None:
    path = _write(tmp_path, "platform_api_key: SECRET-A\nbroken: [unclosed\nmore: 1\n")

    with pytest.raises(StateError) as raised:
        read_config_layer(path)

    text = str(raised.value)
    assert "Failed to read YAML" in text and "line" in text, text
    assert "SECRET" not in text and "broken" not in text, text


def test_every_other_reader_stays_strict_about_an_identical_repeat(tmp_path: Path) -> None:
    """Only config layers opt in; run.yaml and the rest still refuse any repeated key."""
    with pytest.raises(StateError, match="duplicate key"):
        FileStateReader().read_yaml(_write(tmp_path, _IDENTICAL))


def test_a_write_refuses_a_config_that_repeats_a_key_and_leaves_it_byte_identical(tmp_path: Path) -> None:
    from trw_mcp.state._store_migration import _set_pin

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    path = _write(trw_dir, _IDENTICAL)
    before = path.read_bytes()

    with pytest.raises(Exception, match=r"(?i)duplicate"):
        _set_pin(trw_dir, "some-namespace")

    assert path.read_bytes() == before, "a writer rewrote a config it could not read unambiguously"


def test_the_factory_gate_reads_through_an_identical_repeat_without_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    _write(project / ".trw", _IDENTICAL + "factory_enabled: true\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    monkeypatch.delenv("TRW_FACTORY_ENABLED", raising=False)
    monkeypatch.setattr(fx, "_utc_now", lambda: datetime(2026, 9, 29, 12, tzinfo=timezone.utc))

    with capture_logs() as logs:
        gate = fx.check()

    events = {e["event"] for e in logs}
    assert gate.state == "enabled"
    assert "yaml_identical_duplicate_key_tolerated" in events and "factory_switch_read_alone" not in events


def test_a_repeated_collection_is_never_treated_as_identical(tmp_path: Path) -> None:
    """ruamel constructs nested collections lazily; two different lists must not compare equal."""
    path = _write(tmp_path, "target_platforms: [claude-code]\nother: 1\ntarget_platforms: [cursor-ide]\n")

    with pytest.raises(StateError, match="target_platforms"):
        read_config_layer(path)
