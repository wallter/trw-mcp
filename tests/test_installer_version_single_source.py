"""The one-line installer shows the version its trw-mcp wheel carries, and nothing else names one.

``install-trw.py`` is built from ``install-trw.template.py`` around a trw-mcp wheel. The wheel's
filename version comes from ``trw-mcp/pyproject.toml`` (the build backend writes it), the builder reads it
from that filename, and the template holds only the ``{{VERSION}}`` placeholder. No release version is
typed anywhere else, so publishing an installer for a release cannot show a version that release does not
have. The hosted shell one-liner installer holds none either: it asks the release API.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_TEMPLATE = _SCRIPTS / "install-trw.template.py"
_INSTALL_SH = Path(__file__).resolve().parents[2] / "platform" / "public" / "install.sh"
_SEMVER = re.compile(r"\b\d+\.\d+\.\d+\b")


def _load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_installer", _SCRIPTS / "build_installer.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wheels(dist: Path, mcp: str, memory: str) -> None:
    dist.mkdir(parents=True, exist_ok=True)
    (dist / f"trw_mcp-{mcp}-py3-none-any.whl").write_bytes(b"mcp wheel")
    (dist / f"trw_memory-{memory}-py3-none-any.whl").write_bytes(b"memory wheel")


def test_the_template_holds_the_version_only_as_a_placeholder() -> None:
    text = _TEMPLATE.read_text(encoding="utf-8")

    assert re.search(r'^TRW_VERSION = "\{\{VERSION\}\}"$', text, re.MULTILINE)
    assert re.search(r"^Version: \{\{VERSION\}\}$", text, re.MULTILINE)
    typed = [
        line.strip()
        for line in text.splitlines()
        if re.match(r"\s*(TRW_|MCP_|MEMORY_)?[A-Z_]*VERSION[A-Z_]*\s*[:=]", line) and _SEMVER.search(line)
    ]
    assert typed == [], f"a version literal is typed into the installer template: {typed}"


@pytest.mark.integration
def test_the_built_installer_shows_the_version_of_the_wheel_it_embeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wheel version no pyproject or changelog here carries appears in the docstring and TRW_VERSION."""
    module = _load_builder()
    _wheels(tmp_path / "dist", mcp="41.42.43", memory="7.7.7")
    monkeypatch.setattr(module, "DIST_DIR", tmp_path / "dist")

    text = module.build_installer().read_text(encoding="utf-8")

    assert re.search(r'^TRW_VERSION = "41\.42\.43"$', text, re.MULTILINE)
    assert re.search(r"^Version: 41\.42\.43$", text, re.MULTILINE)
    assert "{{VERSION}}" not in text


@pytest.mark.integration
def test_the_installers_trw_version_follows_the_wheel_when_the_wheel_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_builder()
    versions = []
    for version in ("1.0.0", "2.0.0"):
        dist = tmp_path / version
        _wheels(dist, mcp=version, memory="7.7.7")
        monkeypatch.setattr(module, "DIST_DIR", dist)
        versions.append(
            re.search(r'^TRW_VERSION = "([^"]+)"$', module.build_installer().read_text(encoding="utf-8"), re.MULTILINE)
        )

    assert [m.group(1) for m in versions if m] == ["1.0.0", "2.0.0"]


@pytest.mark.skipif(
    not _INSTALL_SH.is_file(), reason="monorepo-only: the hosted installer script is not in the public trw-mcp export"
)
def test_the_shell_one_liner_types_no_release_version() -> None:
    """install.sh resolves the version from the release API (or an explicit pin), never a literal."""
    typed = [
        line.strip()
        for line in _INSTALL_SH.read_text(encoding="utf-8").splitlines()
        if re.match(r"\s*(TRW_VERSION|RESOLVED_VERSION|VERSION)=", line) and _SEMVER.search(line)
    ]

    assert typed == []
