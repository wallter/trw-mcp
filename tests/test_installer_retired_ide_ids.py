"""Installer handling of client ids that no longer exist (removal audit, REMOVE-S1/S3).

The installer used to rewrite ``cursor`` (split into cursor-ide / cursor-cli in v0.44) silently,
and answered ``aider`` / ``gemini`` with a 'retired' message. All of them are now ordinary unknown
ids: the CLI says so (with a did-you-mean where one fits), and a prior config carrying one drops it
instead of crashing the upgrade.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_retired_ide_ids", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_cursor_alias_is_gone() -> None:
    assert not hasattr(_load(), "_LEGACY_IDE_ALIASES")


def test_ide_cursor_on_the_command_line_is_an_unknown_id_with_a_hint() -> None:
    installer = _load()

    with pytest.raises(ValueError, match="cursor-ide"):
        installer._normalize_ide_targets(["cursor"], strict=True)


def test_a_prior_config_with_cursor_drops_it_instead_of_migrating() -> None:
    installer = _load()

    assert installer._normalize_ide_targets(["cursor", "claude-code"], strict=False) == ["claude-code"]


@pytest.mark.parametrize("withdrawn", ["aider", "gemini"])
def test_a_withdrawn_id_on_the_command_line_is_an_ordinary_unknown_id(withdrawn: str) -> None:
    """REMOVE-S3: no 'retired' message any more; the generic unknown-id error answers."""
    installer = _load()

    with pytest.raises(ValueError, match="Unknown --ide value"):
        installer._normalize_ide_targets([withdrawn], strict=True)


@pytest.mark.parametrize("withdrawn", ["aider", "gemini"])
def test_a_prior_config_with_a_withdrawn_id_drops_it_silently(
    withdrawn: str, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = _load()

    assert installer._normalize_ide_targets([withdrawn, "codex"], strict=False) == ["codex"]
    assert withdrawn not in capsys.readouterr().err
