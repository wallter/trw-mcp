"""Shared support for install-trw.py --pip-target contract tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_INSTALLER_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_INSTALLER_ARTIFACT = Path(__file__).resolve().parent.parent / "dist" / "install-trw.py"
_INSTALLER_PATHS = [_INSTALLER_TEMPLATE, _INSTALLER_ARTIFACT]

_ARTIFACT_SKIP_REASON = (
    "built installer artifact absent ({path}); trw-mcp/dist/ is gitignored, so only a machine that "
    "has run `python scripts/build_installer.py` (wheels first) can exercise the artifact half. The "
    "template half of the same parametrization ran."
)


def require_installer(installer_path: Path) -> None:
    """Skip when *installer_path* is the unbuilt artifact rather than the template.

    Every one of these files parametrizes over ``[template, artifact]`` to prove
    the shipped single-file installer carries the same behavior as its template.
    In a plain checkout the artifact does not exist and the loader died on
    ``FileNotFoundError`` instead of reporting why.
    """
    if not installer_path.is_file():
        pytest.skip(_ARTIFACT_SKIP_REASON.format(path=installer_path))


def _load_installer_module(installer_path: Path):
    require_installer(installer_path)
    spec = importlib.util.spec_from_file_location(f"install_trw_test_{installer_path.stem}", installer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
