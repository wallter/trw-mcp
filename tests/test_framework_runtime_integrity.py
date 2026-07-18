"""Focused source/deployment integrity coverage for FRAMEWORK v26.1."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import _DATA_FILE_MAP
from trw_mcp.bootstrap._template_updater import _ALWAYS_UPDATE
from trw_mcp.framework_integrity import inspect_framework_runtime, repair_framework_runtime
from trw_mcp.server._doctor_framework_integrity import check_framework_integrity

FRAMEWORK_VERSION = "v26.1_TRW"
AAREF_VERSION = "v3.2.0"
FRAMEWORK_SOURCE = "v26.1_TRW — MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK\n"
AAREF_SOURCE = "# AARE-F\n\n**Version**: 3.2.0\n"


def _write_runtime(
    target: Path,
    *,
    framework: str = FRAMEWORK_SOURCE,
    aaref: str = AAREF_SOURCE,
    framework_version: str = FRAMEWORK_VERSION,
    aaref_version: str = AAREF_VERSION,
) -> None:
    frameworks = target / ".trw" / "frameworks"
    frameworks.mkdir(parents=True)
    (frameworks / "FRAMEWORK.md").write_text(framework, encoding="utf-8")
    (frameworks / "AARE-F-FRAMEWORK.md").write_text(aaref, encoding="utf-8")
    (frameworks / "VERSION.yaml").write_text(
        f"framework_version: {framework_version}\naaref_version: {aaref_version}\ntrw_mcp_version: 0.55.18\n",
        encoding="utf-8",
    )


def test_runtime_integrity_detects_stale_config_body_and_stamp(tmp_path: Path) -> None:
    _write_runtime(
        tmp_path,
        framework="v24.4_TRW — stale\n",
        aaref="# stale\n\n**Version**: 1.1.0\n",
        framework_version="v24.6_TRW",
        aaref_version="v2.0.0",
    )
    (tmp_path / ".trw" / "config.yaml").write_text(
        "framework_version: v25_TRW\naaref_version: v2.0.0\nkeep_me: true\n",
        encoding="utf-8",
    )

    report = inspect_framework_runtime(
        tmp_path,
        framework_source=FRAMEWORK_SOURCE,
        aaref_source=AAREF_SOURCE,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
    )

    assert not report.ok
    joined = "\n".join(report.errors)
    assert "effective config pin framework_version=v25_TRW" in joined
    assert "deployed FRAMEWORK body declares v24.4_TRW" in joined
    assert "deployed AARE-F body declares v1.1.0" in joined
    assert "deployment stamp framework_version=v24.6_TRW" in joined


def test_explicit_repair_preserves_unrelated_config_and_regenerates_runtime(tmp_path: Path) -> None:
    _write_runtime(
        tmp_path,
        framework="v24.4_TRW — stale\n",
        aaref="# stale\n\n**Version**: 1.1.0\n",
        framework_version="v24.6_TRW",
        aaref_version="v2.0.0",
    )
    config = tmp_path / ".trw" / "config.yaml"
    config.write_text("framework_version: v25_TRW\nkeep_me: true\n", encoding="utf-8")

    report = repair_framework_runtime(
        tmp_path,
        framework_source=FRAMEWORK_SOURCE,
        aaref_source=AAREF_SOURCE,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
        trw_mcp_version="9.9.9",
    )

    assert report.ok, report.errors
    assert config.read_text(encoding="utf-8") == "framework_version: v26.1_TRW\nkeep_me: true\n"
    assert (tmp_path / ".trw/frameworks/FRAMEWORK.md").read_text(encoding="utf-8") == FRAMEWORK_SOURCE
    assert (tmp_path / ".trw/frameworks/AARE-F-FRAMEWORK.md").read_text(encoding="utf-8") == AAREF_SOURCE
    stamp = (tmp_path / ".trw/frameworks/VERSION.yaml").read_text(encoding="utf-8")
    assert "framework_version: v26.1_TRW" in stamp
    assert "aaref_version: v3.2.0" in stamp
    assert "trw_mcp_version: 9.9.9" in stamp


def test_doctor_detects_intentionally_stale_runtime_fixture(tmp_path: Path) -> None:
    _write_runtime(
        tmp_path,
        framework="v24.4_TRW — stale\n",
        aaref="# stale\n\n**Version**: 1.1.0\n",
        framework_version="v24.6_TRW",
        aaref_version="v2.0.0",
    )

    status, message = check_framework_integrity(
        tmp_path,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
    )

    assert status == "FAIL"
    assert "deployed framework integrity mismatch" in message
    assert "v24.4_TRW" in message or "v24.6_TRW" in message


def test_doctor_reports_non_utf8_bundled_source_as_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".trw").mkdir()
    monkeypatch.setattr(
        "trw_mcp.server._doctor_framework_integrity.bundled_source_bytes",
        lambda _artifact: b"\xff",
    )

    status, message = check_framework_integrity(
        tmp_path,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
    )

    assert status == "FAIL"
    assert "unreadable" in message


def test_runtime_registry_checks_bytes_and_digests_even_when_versions_match(tmp_path: Path) -> None:
    """FR04: a same-version one-byte body mutation still fails with body_digest_mismatch."""
    # Deploy a runtime whose stamp/config versions all MATCH, but mutate one body byte.
    _write_runtime(tmp_path, framework=FRAMEWORK_SOURCE + "X")  # same version token, +1 byte
    report = inspect_framework_runtime(
        tmp_path,
        framework_source=FRAMEWORK_SOURCE,
        aaref_source=AAREF_SOURCE,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
        registry_digest="reg-abc",
    )
    assert not report.ok
    joined = "\n".join(report.errors)
    assert "body_digest_mismatch" in joined
    # A pre-digest stamp is surfaced as needs_upgrade (warning), not silently current.
    assert any("needs_upgrade" in w for w in report.warnings)


def test_runtime_registry_digest_mismatch_and_repair_binds_generation(tmp_path: Path) -> None:
    """FR04: stamped registry_digest must match; repair binds a coherent generation."""
    _write_runtime(tmp_path)
    # Clean bodies but a stamp digest that disagrees -> hard error.
    version_path = tmp_path / ".trw/frameworks/VERSION.yaml"
    version_path.write_text(
        version_path.read_text(encoding="utf-8") + "registry_digest: stale-digest\n",
        encoding="utf-8",
    )
    stale = inspect_framework_runtime(
        tmp_path,
        framework_source=FRAMEWORK_SOURCE,
        aaref_source=AAREF_SOURCE,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
        registry_digest="reg-abc",
    )
    assert not stale.ok
    assert any("registry_digest=stale-digest" in e for e in stale.errors)

    # Explicit repair writes matching digests and produces a passing generation.
    repaired = repair_framework_runtime(
        tmp_path,
        framework_source=FRAMEWORK_SOURCE,
        aaref_source=AAREF_SOURCE,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
        registry_digest="reg-abc",
    )
    assert repaired.ok, repaired.errors
    stamp = version_path.read_text(encoding="utf-8")
    assert "registry_digest: reg-abc" in stamp
    assert "framework_digest:" in stamp
    assert "aaref_digest:" in stamp


def _deploy_generation(target: Path):
    """Deploy a healthy 2-canon compiled generation; return the expectations."""
    import hashlib

    from trw_mcp.canons.registry import (
        GenerationExpectation,
        bundled_manifest_bytes,
        clear_cache,
        compile_canon,
        generation_digest,
        load_registry,
    )

    repo_root = Path(__file__).resolve().parents[2]
    clear_cache()
    registry = load_registry(bundled_manifest_bytes())
    frameworks = target / ".trw" / "frameworks"
    frameworks.mkdir(parents=True)
    expectations = []
    for compiled in registry.compiled_canons:
        source = (repo_root / compiled.authoring_source).read_text(encoding="utf-8")
        result = compile_canon(compiled.id, source, source_basename="x.md")
        bodies = {"compact_core": result.core, "reference": result.reference, "combined": result.combined}
        role_paths = {role: f".trw/frameworks/{compiled.id}-{role}.md" for role in bodies}
        role_digests = {}
        for role, text in bodies.items():
            (target / role_paths[role]).write_text(text, encoding="utf-8")
            role_digests[role] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        expectations.append(
            GenerationExpectation(
                canon_id=compiled.id,
                role_paths=role_paths,
                role_digests=role_digests,
                generation_digest=generation_digest(role_digests),
            )
        )
    composite = generation_digest({e.canon_id: e.generation_digest for e in tuple(expectations)})
    return tuple(expectations), composite


def test_runtime_integrity_checks_compiled_canon_generation(tmp_path: Path) -> None:
    """FR06: healthy generation passes; missing/stale/cross-generation fail distinctly."""
    from trw_mcp.canons.registry import inspect_compiled_generation

    expectations, composite = _deploy_generation(tmp_path)

    # Healthy generation passes.
    healthy = inspect_compiled_generation(
        tmp_path, expectations, stamp_path=".trw/frameworks/VERSION.yaml", stamp_generation_digest=composite
    )
    assert healthy.ok, healthy.errors

    # Missing a compact core -> distinct "missing" error.
    (tmp_path / expectations[0].role_paths["compact_core"]).unlink()
    missing = inspect_compiled_generation(
        tmp_path, expectations, stamp_path=".trw/frameworks/VERSION.yaml", stamp_generation_digest=composite
    )
    assert not missing.ok
    assert any("compact_core missing" in e for e in missing.errors)

    # Byte-drift one body -> distinct "stale" error.
    expectations2, composite2 = _deploy_generation(tmp_path / "b")
    ref = (tmp_path / "b") / expectations2[0].role_paths["reference"]
    ref.write_text(ref.read_text(encoding="utf-8") + "DRIFT\n", encoding="utf-8")
    stale = inspect_compiled_generation(
        tmp_path / "b", expectations2, stamp_path=".trw/frameworks/VERSION.yaml", stamp_generation_digest=composite2
    )
    assert any("reference stale" in e for e in stale.errors)

    # Stamp names a different generation -> distinct "cross-generation" error.
    expectations3, _ = _deploy_generation(tmp_path / "c")
    cross = inspect_compiled_generation(
        tmp_path / "c", expectations3, stamp_path=".trw/frameworks/VERSION.yaml", stamp_generation_digest="deadbeef"
    )
    assert any("cross-generation" in e for e in cross.errors)

    # Absent stamp generation_digest -> needs_upgrade (never silently current).
    expectations4, _ = _deploy_generation(tmp_path / "d")
    no_stamp = inspect_compiled_generation(
        tmp_path / "d", expectations4, stamp_path=".trw/frameworks/VERSION.yaml", stamp_generation_digest=None
    )
    assert any("needs_upgrade" in e for e in no_stamp.errors)


def test_bootstrap_init_and_update_regenerate_both_frameworks() -> None:
    init_map = set(_DATA_FILE_MAP)
    update_map = set(_ALWAYS_UPDATE)
    required = {
        ("framework.md", ".trw/frameworks/FRAMEWORK.md"),
        ("aaref.md", ".trw/frameworks/AARE-F-FRAMEWORK.md"),
    }
    assert required <= init_map
    assert required <= update_map
