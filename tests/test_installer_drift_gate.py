"""PRD-INFRA-150 FR01 — template->dist drift gate.

``dist/install-trw.py`` is generated from ``scripts/install-trw.template.py`` by
``build_installer.py`` (version/wheel/checksum substitution). The operator report
``sub_x2O2h3CYyzKZWLu2#b`` proved the dist artifact can silently LAG the template
(``_read_credentials_key`` present in the template, absent from a shipped dist) —
which can only happen if ``make installer`` was not re-run/re-published.

``verify_dist_matches_template`` renders the template once (applying ONLY the
deterministic substitution fields) and normalized-compares against a generated
release candidate, ignoring the named substitution-exclusion fields (wheel blobs,
``WHEEL_SHA256``/``MEMORY_WHEEL_SHA256``, ``TRW_VERSION``, wheel filenames). Any
OTHER divergence — a template symbol absent from dist — fails the gate.

NFR06: the gate is text-only — it never imports/exec's either file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_installer.py"


def _load_build_installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_installer_drift", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def build() -> ModuleType:
    return _load_build_installer()


def _render(build: ModuleType, template_text: str) -> str:
    """Render template text into a normalized dist-equivalent for comparison."""
    return build.render_template_for_drift(template_text)


def test_exclusion_constant_is_named(build: ModuleType) -> None:
    """NFR05: substitution-exclusion fields are a named, documented constant."""
    names = build.DRIFT_SUBSTITUTION_FIELDS
    assert "TRW_VERSION" in names
    assert "WHEEL_SHA256" in names
    assert "MEMORY_WHEEL_SHA256" in names
    assert "WHEEL_FILENAME" in names
    assert "MEMORY_WHEEL_FILENAME" in names


def test_fresh_render_passes(build: ModuleType, tmp_path: Path) -> None:
    """dist == a freshly-substituted build of the template -> gate passes (rc 0)."""
    template = build._read_template(build.TEMPLATES["py"][0])
    dist_text = _substitute(template, "0.55.99", "c" * 64)
    dist_path = tmp_path / "install-trw.py"
    dist_path.write_text(dist_text, encoding="utf-8")

    rc, message, drifted = build.verify_dist_matches_template(
        template_path=build.TEMPLATES["py"][0], dist_path=dist_path
    )
    assert rc == 0, message
    assert drifted == []


def test_missing_template_symbol_fails(build: ModuleType, tmp_path: Path) -> None:
    """dist missing a function present in the template -> gate fails (rc != 0)."""
    template = build._read_template(build.TEMPLATES["py"][0])
    dist_text = _substitute(template, "0.55.99", "c" * 64)
    # Simulate a stale dist: drop the _read_credentials_key definition.
    stale = dist_text.replace("def _read_credentials_key(", "def _DROPPED_read_credentials_key(")
    assert stale != dist_text, "fixture precondition: symbol must exist to drop"
    dist_path = tmp_path / "install-trw.py"
    dist_path.write_text(stale, encoding="utf-8")

    rc, message, drifted = build.verify_dist_matches_template(
        template_path=build.TEMPLATES["py"][0], dist_path=dist_path
    )
    assert rc != 0
    assert "DRIFT" in message
    assert "make installer" in message


def _substitute(template: str, version: str, sha: str) -> str:
    """Produce a built-like dist by substituting the deterministic fields only."""
    out = template.replace("{{VERSION}}", version)
    out = out.replace("{{WHEEL_FILENAME}}", f"trw_mcp-{version}-py3-none-any.whl")
    out = out.replace("{{MEMORY_WHEEL_FILENAME}}", f"trw_memory-{version}-py3-none-any.whl")
    out = out.replace("{{WHEEL_SHA256}}", sha)
    out = out.replace("{{MEMORY_WHEEL_SHA256}}", sha)
    # Embedded wheel base64 placeholders -> distinct comment-prefixed bytes.
    out = out.replace("# {{WHEEL_BASE64}}", "# QUJDREVG")
    out = out.replace("# {{MEMORY_WHEEL_BASE64}}", "# R0hJSktM")
    return out


def test_substitution_fields_excluded(build: ModuleType, tmp_path: Path) -> None:
    """Two built dists differing ONLY in version/checksum/filename -> gate passes."""
    template = build._read_template(build.TEMPLATES["py"][0])
    # Build a dist from concrete-but-different substitution values; the only
    # divergence from a fresh render is in the EXCLUDED fields, so the gate must
    # report no drift (no false positive).
    dist_text = _substitute(template, "9.9.99", "a" * 64)
    dist_path = tmp_path / "install-trw.py"
    dist_path.write_text(dist_text, encoding="utf-8")

    rc, message, drifted = build.verify_dist_matches_template(
        template_path=build.TEMPLATES["py"][0], dist_path=dist_path
    )
    assert rc == 0, f"excluded fields must not trip the gate: {message}"
    assert drifted == []


def test_drift_gate_deterministic(build: ModuleType, tmp_path: Path) -> None:
    """NFR02: two consecutive runs on an unchanged tree produce identical output."""
    template = build._read_template(build.TEMPLATES["py"][0])
    dist_text = _substitute(template, "0.55.99", "c" * 64)
    dist_path = tmp_path / "install-trw.py"
    dist_path.write_text(dist_text, encoding="utf-8")

    first = build.verify_dist_matches_template(template_path=build.TEMPLATES["py"][0], dist_path=dist_path)
    second = build.verify_dist_matches_template(template_path=build.TEMPLATES["py"][0], dist_path=dist_path)
    assert first == second


def test_clean_clone_build_is_faithful_to_template(
    build: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real builder produces a faithful candidate without pre-existing dist."""
    dist_dir = tmp_path / "dist"
    monkeypatch.setattr(build, "DIST_DIR", dist_dir)
    mcp_wheel = tmp_path / "trw_mcp-0.57.0-py3-none-any.whl"
    memory_wheel = tmp_path / "trw_memory-0.11.0-py3-none-any.whl"
    mcp_wheel.write_bytes(b"mcp-wheel")
    memory_wheel.write_bytes(b"memory-wheel")

    dist_path = build.build_installer(
        wheel_path=mcp_wheel,
        memory_wheel_path=memory_wheel,
        fmt="py",
    )
    rc, message, drifted = build.verify_dist_matches_template(
        template_path=build.TEMPLATES["py"][0],
        dist_path=dist_path,
    )

    assert rc == 0, message
    assert drifted == []
    dist_text = dist_path.read_text(encoding="utf-8")
    assert "_read_credentials_key" in dist_text
    assert "{{WHEEL_SHA256}}" not in dist_text
    assert "{{MEMORY_WHEEL_SHA256}}" not in dist_text
