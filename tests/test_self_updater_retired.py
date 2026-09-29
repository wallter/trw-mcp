"""PRD-INFRA-200 FR03: the in-process self-updater is retired (8.0.0, breaking).

It had no evidence of ever running end to end (every test mocked the network and
the filesystem), and the one supported upgrade is ``install-trw.py --upgrade``.
The config key an operator may still hold must say it does nothing now, through
the real config loader, rather than being dropped in silence.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_RETIRED_KEY = "auto_upgrade"
_PACKAGE = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_warned() -> object:
    from trw_mcp.models.config import _retired_keys

    _retired_keys._reset_warned_keys()
    yield
    _retired_keys._reset_warned_keys()


def test_a_config_still_setting_the_key_is_told_it_is_retired(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.models.config import _loader

    monkeypatch.setattr(_loader, "_read_yaml_overrides", lambda _path: {_RETIRED_KEY: True})
    monkeypatch.setattr(_loader, "resolve_platform_api_key", lambda _path: "")

    config = _loader._build_config()

    assert not hasattr(config, _RETIRED_KEY)
    err = capsys.readouterr().err
    assert _RETIRED_KEY in err and "no replacement" in err


def test_nothing_imports_or_names_the_retired_module() -> None:
    """The FR03 census: the module is gone and no source or test under trw-mcp names it (this file excepted)."""
    assert importlib.util.find_spec("trw_mcp.state." + _RETIRED_KEY) is None
    this = Path(__file__).resolve()
    naming = sorted(
        str(path.relative_to(_PACKAGE))
        for root in ("src", "tests")
        for path in (_PACKAGE / root).rglob("*.py")
        if path.resolve() != this and _RETIRED_KEY in path.read_text(encoding="utf-8")
    )
    assert naming == []
