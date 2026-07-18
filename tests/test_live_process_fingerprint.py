"""Frozen live-process fingerprint tests (PRD-INFRA-164 FR07/NFR07)."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

from trw_mcp.canons.fingerprint import (
    Currentness,
    ProcessFingerprint,
    PublicPromptDecl,
    PublicResourceDecl,
    PublicToolDecl,
    RealizedSurface,
    compare_generation,
    digest_loaded_modules,
    freeze_fingerprint,
    get_frozen_fingerprint,
    reset_frozen_fingerprint,
    set_frozen_fingerprint,
)


def _surface(tool_desc: str = "does a thing") -> RealizedSurface:
    return RealizedSurface(
        tools=(
            PublicToolDecl(
                name="trw_learn",
                description=tool_desc,
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            ),
            PublicToolDecl(
                name="trw_recall",
                description="recall",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            ),
        ),
        resources=(PublicResourceDecl(uri="trw://framework/versions", name="versions", description="v"),),
        prompts=(PublicPromptDecl(name="ceremony", description="guide"),),
    )


def _freeze(surface: RealizedSurface, **overrides: str) -> ProcessFingerprint:
    kwargs: dict[str, object] = {
        "trw_mcp_version": "1.0.0",
        "framework_version": "v26.1_TRW",
        "aaref_version": "v3.2.0",
        "template_version": "3.2",
        "registry_digest": "reg-digest",
        "source_digests": {"framework": "aa", "aaref": "bb"},
        "loaded_module_digest": "modules-one",
        "surface": surface,
    }
    kwargs.update(overrides)
    return freeze_fingerprint(**kwargs)  # type: ignore[arg-type]


def test_fingerprint_binds_loaded_versions_canons_and_realized_public_surface() -> None:
    # Identical surfaces built independently produce identical digests (order/loc invariant).
    a = _freeze(_surface())
    b = _freeze(_surface())
    assert a.digest == b.digest

    # Reordered tool tuple => same digest (sorted normalization).
    reordered = RealizedSurface(
        tools=tuple(reversed(_surface().tools)),
        resources=_surface().resources,
        prompts=_surface().prompts,
    )
    assert _freeze(reordered).digest == a.digest


def test_every_bound_field_mutation_changes_the_digest() -> None:
    base = _freeze(_surface())
    assert _freeze(_surface(), framework_version="v25_TRW").digest != base.digest
    assert _freeze(_surface(), trw_mcp_version="2.0.0").digest != base.digest
    assert _freeze(_surface(), template_version="3.1").digest != base.digest
    assert _freeze(_surface(), registry_digest="other").digest != base.digest
    assert _freeze(_surface(), loaded_module_digest="modules-two").digest != base.digest
    # A tool description change (client-affecting) changes the surface digest.
    assert _freeze(_surface(tool_desc="different behavior")).digest != base.digest


def test_compare_generation_current_stale_unknown() -> None:
    fp = _freeze(_surface())
    # Exact match => current.
    assert (
        compare_generation(
            fp,
            expected_registry_digest="reg-digest",
            expected_source_digests={"framework": "aa", "aaref": "bb"},
        )
        is Currentness.CURRENT
    )
    # Registry drift => stale.
    assert (
        compare_generation(
            fp,
            expected_registry_digest="moved-on",
            expected_source_digests={"framework": "aa", "aaref": "bb"},
        )
        is Currentness.STALE
    )
    # Source byte drift => stale.
    assert (
        compare_generation(
            fp,
            expected_registry_digest="reg-digest",
            expected_source_digests={"framework": "CHANGED", "aaref": "bb"},
        )
        is Currentness.STALE
    )


def test_unknown_and_degraded_states_are_machine_readable_and_never_green() -> None:
    fp = _freeze(_surface())
    # Absent frozen fingerprint => unknown, never current.
    assert compare_generation(None, expected_registry_digest="x", expected_source_digests={}) is Currentness.UNKNOWN
    # Absent expected data (comparison failure) => unknown, never current.
    assert (
        compare_generation(fp, expected_registry_digest=None, expected_source_digests={"framework": "aa"})
        is Currentness.UNKNOWN
    )
    assert (
        compare_generation(fp, expected_registry_digest="reg-digest", expected_source_digests=None)
        is Currentness.UNKNOWN
    )
    # Currentness values are stable strings for machine consumers.
    assert [c.value for c in Currentness] == ["current", "stale", "unknown"]


def test_frozen_process_rejects_changed_generation_refreeze() -> None:
    """Editable-install byte drift cannot make an already-running process inherit new identity."""
    original = _freeze(_surface())
    changed = _freeze(_surface(), framework_version="v99_TRW")
    reset_frozen_fingerprint()
    try:
        set_frozen_fingerprint(original)
        set_frozen_fingerprint(original)  # exact idempotent registration is harmless
        with pytest.raises(RuntimeError, match="already frozen"):
            set_frozen_fingerprint(changed)
        assert get_frozen_fingerprint() is original
        assert (
            compare_generation(
                get_frozen_fingerprint(),
                expected_registry_digest=changed.registry_digest,
                expected_source_digests={"framework": "CHANGED", "aaref": "bb"},
            )
            is Currentness.STALE
        )
    finally:
        reset_frozen_fingerprint()


def test_real_file_mutation_makes_frozen_process_stale(tmp_path: Path) -> None:
    """FR07: post-start byte mutation cannot rewrite the frozen process identity."""
    body = tmp_path / "framework.md"
    body.write_bytes(b"generation-one\n")
    first_digest = hashlib.sha256(body.read_bytes()).hexdigest()
    frozen = freeze_fingerprint(
        trw_mcp_version="1.0.0",
        framework_version="v26.1_TRW",
        aaref_version="v3.2.0",
        template_version="3.2",
        registry_digest="registry-one",
        source_digests={"framework": first_digest},
        loaded_module_digest="modules-one",
        surface=_surface(),
    )

    body.write_bytes(b"generation-two\n")
    changed_digest = hashlib.sha256(body.read_bytes()).hexdigest()

    assert changed_digest != first_digest
    assert (
        compare_generation(
            frozen,
            expected_registry_digest="registry-one",
            expected_source_digests={"framework": changed_digest},
        )
        is Currentness.STALE
    )


def test_server_freezes_live_fingerprint_bound_to_registry_and_realized_surface() -> None:
    """FR07 wiring: the server adapter freezes a fingerprint that binds the loaded
    registry generation AND the realized public MCP surface (self-consistent with
    the deployed generation the process bundles)."""
    from fastmcp import FastMCP

    from trw_mcp.canons.fingerprint import compare_generation, get_frozen_fingerprint, reset_frozen_fingerprint
    from trw_mcp.canons.registry import load_registry, managed_source_digests
    from trw_mcp.server._live_fingerprint import freeze_live_process_fingerprint

    reset_frozen_fingerprint()
    try:
        server = FastMCP("test")
        from trw_mcp.tools.learning import register_learning_tools

        register_learning_tools(server)

        fp = freeze_live_process_fingerprint(server)
        assert fp is not None
        # The freeze is stored process-globally for status/session consumers.
        assert get_frozen_fingerprint() is fp

        registry = load_registry()
        # The frozen process binds the loaded registry generation and managed bytes,
        # so a self-comparison against the bundled generation is CURRENT.
        assert fp.registry_digest == registry.digest
        assert fp.source_digests == managed_source_digests(registry)
        assert len(fp.loaded_module_digest) == 64
        assert (
            compare_generation(
                fp,
                expected_registry_digest=registry.digest,
                expected_source_digests=managed_source_digests(registry),
            )
            is Currentness.CURRENT
        )
        # A moved-on deployed generation makes the frozen process STALE, never current.
        assert (
            compare_generation(
                fp,
                expected_registry_digest="moved-on-generation",
                expected_source_digests=managed_source_digests(registry),
            )
            is Currentness.STALE
        )
        # The realized public surface is actually bound: the registered tool shows up.
        assert any(t.name == "trw_learn" for t in fp_surface_tools(server))
    finally:
        reset_frozen_fingerprint()


def test_server_fingerprint_freeze_is_idempotent_after_more_modules_load() -> None:
    """Repeated registration returns the startup identity instead of a false mismatch."""
    from fastmcp import FastMCP

    from trw_mcp.server._live_fingerprint import freeze_live_process_fingerprint

    server = FastMCP("fingerprint-idempotence")
    reset_frozen_fingerprint()
    try:
        first = freeze_live_process_fingerprint(server)
        assert first is not None

        # A later import changes a fresh module census, but the process startup
        # identity is immutable once frozen.
        import trw_mcp.tools.telemetry  # noqa: F401

        second = freeze_live_process_fingerprint(server)
        assert second is first
    finally:
        reset_frozen_fingerprint()


def test_loaded_module_digest_detects_same_version_byte_mutation(tmp_path: Path) -> None:
    """Same declared version cannot hide different loaded Python artifact bytes."""
    module_name = "trw_mcp._fingerprint_mutation_probe"
    module_path = tmp_path / "probe.py"
    module_path.write_bytes(b"VALUE = 'generation-one'\n")
    module = ModuleType(module_name)
    module.__file__ = str(module_path)
    sys.modules[module_name] = module
    try:
        first = digest_loaded_modules()
        module_path.write_bytes(b"VALUE = 'generation-two'\n")
        second = digest_loaded_modules()
        assert first != second
    finally:
        sys.modules.pop(module_name, None)


def test_loaded_module_digest_binds_in_memory_code_not_only_current_disk(tmp_path: Path) -> None:
    """An old process remains distinguishable after same-version files are replaced."""
    module_name = "trw_mcp._fingerprint_stale_process_probe"
    module_path = tmp_path / "probe.py"
    current_body = "def identity():\n    return 'current'\n"
    module_path.write_text(current_body, encoding="utf-8")

    stale = ModuleType(module_name)
    stale.__file__ = str(module_path)
    exec("def identity():\n    return 'stale'\n", stale.__dict__)
    stale.identity.__module__ = module_name
    sys.modules[module_name] = stale
    try:
        stale_digest = digest_loaded_modules()
        current = ModuleType(module_name)
        current.__file__ = str(module_path)
        exec(current_body, current.__dict__)
        current.identity.__module__ = module_name
        sys.modules[module_name] = current
        assert digest_loaded_modules() != stale_digest
    finally:
        sys.modules.pop(module_name, None)


def fp_surface_tools(server: object) -> tuple[object, ...]:
    from trw_mcp.server._live_fingerprint import build_realized_surface

    return build_realized_surface(server).tools  # type: ignore[arg-type]
