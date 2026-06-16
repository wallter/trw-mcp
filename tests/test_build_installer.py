"""Tests for build_installer.py wheel checksum substitution — PRD-SEC-006 FR01.

The installer template declares ``WHEEL_SHA256 = "{{WHEEL_SHA256}}"`` and a
matching ``MEMORY_WHEEL_SHA256`` placeholder, and ``_verify_checksum`` skips
verification while the value is a ``{{`` placeholder. The build MUST compute the
SHA-256 of both wheels, substitute the placeholders with 64-hex values, and
assert (failing the build otherwise) that no checksum placeholders remain.

These tests build against synthetic wheel files (the build step only reads the
bytes and base64-encodes them; it does not require a real zip). The script is
imported by file path because ``scripts/`` is not an installed package.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_installer.py"

# 64-hex SHA-256 anchored to the assignment line in the generated installer.
_MCP_SHA_RE = re.compile(r'^WHEEL_SHA256 = "([0-9a-f]{64})"$', re.MULTILINE)
_MEMORY_SHA_RE = re.compile(r'^MEMORY_WHEEL_SHA256 = "([0-9a-f]{64})"$', re.MULTILINE)


def _load_build_installer() -> ModuleType:
    """Import build_installer.py by file path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("build_installer", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_fake_wheels(dist_dir: Path) -> tuple[Path, Path, bytes, bytes]:
    """Create synthetic mcp + memory wheels with distinct, known content."""
    dist_dir.mkdir(parents=True, exist_ok=True)
    mcp_bytes = b"fake-trw-mcp-wheel-bytes-PRD-SEC-006"
    memory_bytes = b"fake-trw-memory-wheel-bytes-PRD-SEC-006-distinct"
    mcp_wheel = dist_dir / "trw_mcp-9.9.9-py3-none-any.whl"
    memory_wheel = dist_dir / "trw_memory-9.9.9-py3-none-any.whl"
    mcp_wheel.write_bytes(mcp_bytes)
    memory_wheel.write_bytes(memory_bytes)
    return mcp_wheel, memory_wheel, mcp_bytes, memory_bytes


@pytest.fixture
def build_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Build-installer module pointed at a tmp dist dir with fake wheels."""
    module = _load_build_installer()
    dist_dir = tmp_path / "dist"
    _make_fake_wheels(dist_dir)
    monkeypatch.setattr(module, "DIST_DIR", dist_dir)
    return module


@pytest.mark.integration
class TestChecksumSubstitution:
    """FR01: build computes + substitutes both wheel SHA-256 placeholders."""

    def test_checksums_substituted(self, build_env: ModuleType) -> None:
        """Generated installer has 64-hex checksums, not {{...}} placeholders."""
        output = build_env.build_installer()
        text = output.read_text(encoding="utf-8")

        assert "{{WHEEL_SHA256}}" not in text
        assert "{{MEMORY_WHEEL_SHA256}}" not in text
        assert _MCP_SHA_RE.search(text) is not None
        assert _MEMORY_SHA_RE.search(text) is not None

    def test_substituted_checksum_matches_wheel_bytes(self, build_env: ModuleType) -> None:
        """The substituted hash equals SHA-256 of the actual wheel bytes."""
        dist_dir = build_env.DIST_DIR
        mcp_wheel = next(dist_dir.glob("trw_mcp-*.whl"))
        memory_wheel = next(dist_dir.glob("trw_memory-*.whl"))
        expected_mcp = hashlib.sha256(mcp_wheel.read_bytes()).hexdigest()
        expected_memory = hashlib.sha256(memory_wheel.read_bytes()).hexdigest()

        output = build_env.build_installer()
        text = output.read_text(encoding="utf-8")

        mcp_match = _MCP_SHA_RE.search(text)
        memory_match = _MEMORY_SHA_RE.search(text)
        assert mcp_match is not None and mcp_match.group(1) == expected_mcp
        assert memory_match is not None and memory_match.group(1) == expected_memory

    def test_mcp_and_memory_checksums_differ(self, build_env: ModuleType) -> None:
        """Each placeholder gets its own wheel's hash (no cross-substitution)."""
        output = build_env.build_installer()
        text = output.read_text(encoding="utf-8")
        mcp = _MCP_SHA_RE.search(text)
        memory = _MEMORY_SHA_RE.search(text)
        assert mcp is not None and memory is not None
        assert mcp.group(1) != memory.group(1)


@pytest.mark.integration
class TestBuildAssertion:
    """FR01: build fails closed if checksum placeholders remain unsubstituted."""

    def test_assertion_fails_on_unsubstituted_placeholder(
        self, build_env: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the template lacks the SHA placeholders, the build aborts."""
        template_path, _ = build_env.TEMPLATES["py"]
        # Sabotage: strip the SHA assignment lines so substitution finds nothing
        # and the post-build assertion sees no 64-hex checksum.
        original = template_path.read_text(encoding="utf-8")
        broken = original.replace('WHEEL_SHA256 = "{{WHEEL_SHA256}}"', 'WHEEL_SHA256 = ""')
        broken = broken.replace(
            'MEMORY_WHEEL_SHA256 = "{{MEMORY_WHEEL_SHA256}}"',
            'MEMORY_WHEEL_SHA256 = ""',
        )
        monkeypatch.setattr(build_env, "_read_template", lambda _p: broken)

        with pytest.raises(SystemExit):
            build_env.build_installer()

    def test_assertion_message_mentions_checksum(self, build_env: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Failure path mentions the checksum so the operator can diagnose."""
        template_path, _ = build_env.TEMPLATES["py"]
        original = template_path.read_text(encoding="utf-8")
        broken = original.replace('WHEEL_SHA256 = "{{WHEEL_SHA256}}"', 'WHEEL_SHA256 = ""')
        broken = broken.replace(
            'MEMORY_WHEEL_SHA256 = "{{MEMORY_WHEEL_SHA256}}"',
            'MEMORY_WHEEL_SHA256 = ""',
        )
        monkeypatch.setattr(build_env, "_read_template", lambda _p: broken)

        with pytest.raises(SystemExit):
            build_env.build_installer()
