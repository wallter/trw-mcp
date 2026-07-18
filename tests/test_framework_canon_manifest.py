"""Monorepo regression guard for the manifest-driven tracked canon mirrors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "trw-mcp/src/trw_mcp/data/framework_canons.json"

if not (_REPO_ROOT / "scripts").is_dir():
    pytest.skip("monorepo-only canon mirror manifest", allow_module_level=True)


# --------------------------------------------------------------------------- #
# PRD-CORE-207 — compiled compact-core / reference / combined generation       #
# --------------------------------------------------------------------------- #


def _registry():
    from trw_mcp.canons.registry import bundled_manifest_bytes, clear_cache, load_registry

    clear_cache()
    return load_registry(bundled_manifest_bytes())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_migration_inventory_covers_every_frozen_canon_span() -> None:
    """FR01: complete, non-overlapping, unique-ID span coverage + frozen digest."""
    from trw_mcp.canons.registry import all_families, compile_canon, covered_families

    registry = _registry()
    assert {c.id for c in registry.compiled_canons} == {"framework", "aaref"}
    for compiled in registry.compiled_canons:
        source = (_REPO_ROOT / compiled.authoring_source).read_text(encoding="utf-8")
        baseline = (_REPO_ROOT / compiled.combined).read_text(encoding="utf-8")
        result = compile_canon(compiled.id, source, source_basename="x.md")

        # Frozen baseline digest binds source -> baseline (no drift).
        assert _sha(result.combined) == compiled.frozen_baseline_digest
        assert result.combined == baseline

        # Unique stable IDs.
        ids = [s.id for s in result.spans]
        assert len(ids) == len(set(ids)), "duplicate obligation id"

        # 100% non-blank source coverage: every non-blank baseline line is owned
        # by exactly one span (spans are contiguous, markers are the only added lines).
        span_nonblank = sum(len(s.nonblank) for s in result.spans)
        baseline_nonblank = sum(1 for line in baseline.split("\n") if line.strip())
        assert span_nonblank == baseline_nonblank

        # Inventory is machine-readable with source + generated-output digests.
        inv = result.inventory
        assert inv["span_count"] == len(result.spans)
        assert inv["source_digest_combined"] == _sha(result.combined)
        assert inv["core_digest"] == _sha(result.core)
        assert inv["reference_digest"] == _sha(result.reference)
        assert len(inv["obligations"]) == len(result.spans)  # type: ignore[arg-type]

        # Every load-bearing invariant family maps to >=1 present core obligation.
        assert covered_families(compiled.id, result.core) == all_families(compiled.id)


def test_canon_compiler_is_deterministic_and_fail_closed() -> None:
    """FR02: two builds are byte-identical; malformed sources fail closed."""
    from trw_mcp.canons.registry import CanonRegistryError, compile_canon

    registry = _registry()
    for compiled in registry.compiled_canons:
        source = (_REPO_ROOT / compiled.authoring_source).read_text(encoding="utf-8")
        a = compile_canon(compiled.id, source, source_basename="x.md")
        b = compile_canon(compiled.id, source, source_basename="x.md")
        assert (a.core, a.reference, a.combined, a.inventory) == (
            b.core,
            b.reference,
            b.combined,
            b.inventory,
        )

    good = "<!-- trw:span id=a dest=both class=normative -->\nv1\n"
    # Malformed marker (missing dest) -> fail closed.
    with pytest.raises(CanonRegistryError):
        compile_canon("t", "<!-- trw:span id=a class=normative -->\nv1\n")
    # Duplicate id.
    with pytest.raises(CanonRegistryError):
        compile_canon("t", good + "<!-- trw:span id=a dest=core class=normative -->\nx\n")
    # Unknown enum value.
    with pytest.raises(CanonRegistryError):
        compile_canon("t", "<!-- trw:span id=a dest=nowhere class=normative -->\nx\n")
    # Non-blank content before the first marker.
    with pytest.raises(CanonRegistryError):
        compile_canon("t", "leading text\n<!-- trw:span id=a dest=core class=normative -->\nx\n")
    # Reference-only normative obligation (self-sufficiency, FR03 boundary).
    with pytest.raises(CanonRegistryError):
        compile_canon("t", "<!-- trw:span id=a dest=reference class=normative -->\nx\n")


def test_reference_and_combined_documents_are_generated_outputs() -> None:
    """FR04: combined == composition == baseline; hand edits fail parity."""
    from trw_mcp.canons.registry import check_generation, compile_canon

    registry = _registry()
    for compiled in registry.compiled_canons:
        source = (_REPO_ROOT / compiled.authoring_source).read_text(encoding="utf-8")
        result = compile_canon(compiled.id, source, source_basename=compiled.authoring_source.rsplit("/", 1)[-1])
        # Combined is the deterministic composition and equals the legacy file.
        assert result.combined == (_REPO_ROOT / compiled.combined).read_text(encoding="utf-8")
        # Generated core + reference declare their source and regen command (FR04).
        for text in (result.core, result.reference):
            assert compiled.authoring_source.rsplit("/", 1)[-1] in text
            assert "compile-framework-canons.py --write" in text
        # On-disk generated files match the compiled bytes.
        assert (_REPO_ROOT / compiled.compact_core).read_text(encoding="utf-8") == result.core
        assert (_REPO_ROOT / compiled.reference).read_text(encoding="utf-8") == result.reference

    # A hand edit to a generated output is caught by the read-only parity check.
    assert check_generation(_REPO_ROOT, registry) == []


def test_reference_hand_edit_fails_parity(tmp_path: Path) -> None:
    """FR04/FR05: a seeded generated-output drift fails with source + regen command."""
    from trw_mcp.canons.registry import check_generation

    registry = _registry()
    compiled = registry.compiled_canon("framework")
    # Build an isolated repo tree mirroring the managed paths.
    for rel in (
        compiled.authoring_source,
        compiled.combined,
        compiled.compact_core,
        compiled.reference,
        compiled.obligation_inventory,
    ):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((_REPO_ROOT / rel).read_bytes())
    # Clean tree passes.
    assert [e for e in check_generation(tmp_path, registry) if e.startswith("framework")] == []
    # Corrupt one generated output byte.
    core = tmp_path / compiled.compact_core
    core.write_text(core.read_text(encoding="utf-8") + "HAND EDIT\n", encoding="utf-8")
    errors = [e for e in check_generation(tmp_path, registry) if e.startswith("framework")]
    assert errors
    joined = "\n".join(errors)
    assert compiled.authoring_source in joined
    assert "--write" in joined


def test_manifest_v2_covers_all_compiled_outputs_and_mirrors() -> None:
    """FR05: schema v2 declares each compiled output once; duplicates/undeclared fail."""
    from trw_mcp.canons.registry import CanonRegistryError, parse_registry

    registry = _registry()
    assert registry.schema_version == 2

    # Every compiled generated output is declared exactly once (no duplicate role).
    all_outputs: list[str] = []
    for compiled in registry.compiled_canons:
        all_outputs.extend([compiled.compact_core, compiled.reference, compiled.obligation_inventory])
    assert len(all_outputs) == len(set(all_outputs))

    # The legacy combined filename stays a manifest-declared artifact authoring source.
    artifact_sources = {a.authoring_source for a in registry.artifacts}
    for compiled in registry.compiled_canons:
        assert compiled.combined in artifact_sources

    # A duplicate generated output across compiled canons fails the strict parser.
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    raw["compiled_canons"][1]["compact_core"] = raw["compiled_canons"][0]["compact_core"]
    with pytest.raises(CanonRegistryError):
        parse_registry(json.dumps(raw).encode("utf-8"))

    # An unknown compiled field fails closed.
    raw2 = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    raw2["compiled_canons"][0]["surprise"] = "x"
    with pytest.raises(CanonRegistryError):
        parse_registry(json.dumps(raw2).encode("utf-8"))


def test_canon_compiler_is_environment_deterministic() -> None:
    """NFR02: no timestamp / host path / CRLF / locale drift enters outputs."""
    from trw_mcp.canons.registry import compile_canon

    registry = _registry()
    for compiled in registry.compiled_canons:
        source = (_REPO_ROOT / compiled.authoring_source).read_text(encoding="utf-8")
        r1 = compile_canon(compiled.id, source, source_basename="x.md")
        # CRLF-normalized source produces byte-identical output (NFR02 line endings).
        r2 = compile_canon(compiled.id, source.replace("\n", "\r\n"), source_basename="x.md")
        assert r1.core == r2.core and r1.reference == r2.reference and r1.combined == r2.combined
        for text in (r1.core, r1.reference):
            assert "\r" not in text
            assert "/home/" not in text and str(_REPO_ROOT) not in text


def test_compact_canon_promotion_is_atomic_and_fail_closed() -> None:
    """FR09: any missing gate blocks promotion with no version/default drift."""
    from trw_mcp.canons.registry import PROMOTION_GATES, evaluate_promotion_gates

    all_green = dict.fromkeys(PROMOTION_GATES, True)
    assert evaluate_promotion_gates(all_green).promote is True

    for missing in PROMOTION_GATES:
        gates = dict(all_green)
        gates[missing] = False
        decision = evaluate_promotion_gates(gates)
        assert decision.promote is False
        assert missing in decision.blocking

    # An empty gate map blocks everything (absence never promotes).
    assert evaluate_promotion_gates({}).promote is False

    # Shadow mode: version defaults remain on the prior generation (not v26.2).
    ceremony = (_REPO_ROOT / "trw-mcp/src/trw_mcp/models/config/_fields_ceremony.py").read_text(encoding="utf-8")
    assert 'framework_version: str = "v26.1_TRW"' in ceremony
    assert "v26.2" not in ceremony


def test_manifest_names_one_authoring_source_and_all_tracked_mirrors() -> None:
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    specs = {item["id"]: item for item in raw["artifacts"]}

    assert specs["framework"]["authoring_source"] == "trw-mcp/src/trw_mcp/data/framework.md"
    assert set(specs["framework"]["tracked_mirrors"]) == {
        "FRAMEWORK.md",
        ".trw/frameworks/FRAMEWORK.md",
        "trw-mcp/FRAMEWORK.md",
    }
    assert specs["aaref"]["authoring_source"] == "trw-mcp/src/trw_mcp/data/aaref.md"
    assert set(specs["aaref"]["tracked_mirrors"]) == {
        "AARE-F-FRAMEWORK.md",
        ".trw/frameworks/AARE-F-FRAMEWORK.md",
    }

    # This registry is shipped in the public wheel. Monorepo-private vendor
    # projections are not usable install/runtime paths and must not leak into it.
    assert "trw-eval/" not in json.dumps(raw)

    for spec in specs.values():
        source = (_REPO_ROOT / spec["authoring_source"]).read_bytes()
        for mirror in spec["tracked_mirrors"]:
            assert (_REPO_ROOT / mirror).read_bytes() == source


def test_shared_worktree_policy_has_one_normative_source() -> None:
    """PRD-CORE-206-FR05: one authoring source governs the shared-worktree policy.

    The coherent-green + Git-decision-matrix policy is authored once in
    ``framework.md``; every tracked mirror is byte-identical (source parity), and no
    other tracked mirror re-authors the full decision matrix independently.
    """
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    specs = {item["id"]: item for item in raw["artifacts"]}
    framework = specs["framework"]

    source_path = _REPO_ROOT / framework["authoring_source"]
    source_bytes = source_path.read_bytes()
    source_text = source_bytes.decode("utf-8")

    # The normative policy lives in the single authoring source.
    marker = "Commit each coherent, focused, green milestone promptly"
    hazard = "command-specific operator authorization and exclusive ownership"
    assert marker in source_text
    assert hazard in source_text

    # Every tracked mirror is a byte-identical projection of that one source — the
    # policy is never re-authored, only mirrored.
    for mirror in framework["tracked_mirrors"]:
        assert (_REPO_ROOT / mirror).read_bytes() == source_bytes, f"mirror drift: {mirror}"


def test_nested_monorepo_instructions_resolve_parent_frameworks() -> None:
    instructions = (_REPO_ROOT / "trw-mcp/AGENTS.md").read_text(encoding="utf-8")
    assert "../.trw/frameworks/FRAMEWORK.md" in instructions
    assert "../.trw/frameworks/AARE-F-FRAMEWORK.md" in instructions
    assert "take precedence over ignored" in instructions
    generated = instructions.split("<!-- trw:start -->", 1)[1]
    assert "read `.trw/frameworks/FRAMEWORK.md`" not in generated
    assert "Read `.trw/frameworks/FRAMEWORK.md`" not in generated
    assert "Do NOT call `trw_deliver` unless" in instructions
    assert "Agent " + "Teams" not in instructions


def test_promoted_runtime_instruction_surfaces_load_compact_core() -> None:
    """FR09: current generated runtime guidance selects the compact path."""
    surfaces = (
        "trw-mcp/src/trw_mcp/data/messages/messages.yaml",
        "trw-mcp/src/trw_mcp/data/hooks/session-start.sh",
        "trw-mcp/src/trw_mcp/data/hooks/post-compact.sh",
        "trw-mcp/src/trw_mcp/data/claude_code/loop.md",
        "trw-mcp/src/trw_mcp/server/_app.py",
    )
    for relative in surfaces:
        text = (_REPO_ROOT / relative).read_text(encoding="utf-8")
        assert ".trw/frameworks/FRAMEWORK-CORE.md" in text, relative
        assert ".trw/frameworks/FRAMEWORK.md" not in text, relative
