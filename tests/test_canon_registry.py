"""Strict typed canon registry tests (PRD-INFRA-164 FR01/FR02/NFR01-03).

Runs against the REAL bundled ``framework_canons.json`` and real ``data/``
bodies (FPI-1) plus targeted mutation fixtures.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

from trw_mcp.canons import registry as reg
from trw_mcp.canons._errors import CanonErrorCode, CanonRegistryError
from trw_mcp.canons._loader import SUPPORTED_SCHEMA_VERSION, parse_registry
from trw_mcp.canons._models import ArtifactKind, InstallRole
from trw_mcp.canons._views import (
    install_view,
    managed_install_view,
    runtime_view,
    source_view,
    template_artifact,
)


def _canonical_manifest() -> dict[str, object]:
    return json.loads(reg.bundled_manifest_bytes().decode("utf-8"))


def _mutate(**overrides: object) -> bytes:
    data = _canonical_manifest()
    data.update(overrides)
    return json.dumps(data).encode("utf-8")


# --------------------------------------------------------------------------- #
# FR01 — strict typed, path-contained, deterministic                          #
# --------------------------------------------------------------------------- #


def test_registry_v2_is_strict_typed_and_path_contained() -> None:
    reg.clear_cache()
    registry = reg.load_registry()
    assert registry.schema_version == SUPPORTED_SCHEMA_VERSION == 2
    # Every artifact is frozen + uniquely addressable.
    ids = [a.id for a in registry.artifacts]
    assert ids == sorted(ids) or len(ids) == len(set(ids))
    framework = registry.artifact("framework")
    assert framework.kind is ArtifactKind.CANON
    assert framework.authoring_source == "trw-mcp/src/trw_mcp/data/framework.md"
    with pytest.raises(Exception):
        framework.tracked_mirrors = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda d: {**d, "schema_version": 1}, CanonErrorCode.UNSUPPORTED_SCHEMA),
        (lambda d: {**d, "bogus": 1}, CanonErrorCode.UNKNOWN_FIELD),
        (lambda d: {k: v for k, v in d.items() if k != "artifacts"}, CanonErrorCode.MISSING_FIELD),
        (lambda d: {**d, "artifacts": []}, CanonErrorCode.WRONG_TYPE),
    ],
)
def test_top_level_mutations_fail_with_stable_code(mutator: object, code: CanonErrorCode) -> None:
    data = mutator(_canonical_manifest())  # type: ignore[operator]
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    assert exc.value.code is code


def test_absolute_path_is_rejected() -> None:
    data = _canonical_manifest()
    artifacts = data["artifacts"]
    assert isinstance(artifacts, list)
    artifacts[0]["authoring_source"] = "/etc/passwd"
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    assert exc.value.code is CanonErrorCode.ABSOLUTE_PATH


def test_traversing_path_is_rejected() -> None:
    data = _canonical_manifest()
    data["artifacts"][0]["tracked_mirrors"] = ["../escape.md"]  # type: ignore[index]
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    assert exc.value.code is CanonErrorCode.TRAVERSING_PATH


def test_duplicate_artifact_id_is_rejected() -> None:
    data = _canonical_manifest()
    dup = dict(data["artifacts"][0])  # type: ignore[index]
    dup["tracked_mirrors"] = []
    dup["install_targets"] = []
    data["artifacts"].append(dup)  # type: ignore[union-attr]
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    assert exc.value.code is CanonErrorCode.DUPLICATE_ID


def test_unsupported_extractor_is_rejected() -> None:
    data = _canonical_manifest()
    data["artifacts"][0]["version"] = {"extractor": "nope", "config_field": "x"}  # type: ignore[index]
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    assert exc.value.code is CanonErrorCode.UNSUPPORTED_EXTRACTOR


def test_control_character_is_rejected() -> None:
    data = _canonical_manifest()
    data["artifacts"][0]["id"] = "frame\x00work"  # type: ignore[index]
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    assert exc.value.code is CanonErrorCode.CONTROL_CHARACTER


# --------------------------------------------------------------------------- #
# FR02 — registry-derived views, no independent list                          #
# --------------------------------------------------------------------------- #


def test_registry_views_are_complete_and_have_no_independent_consumer_lists() -> None:
    reg.clear_cache()
    registry = reg.load_registry()
    src = source_view(registry)
    assert {v.id for v in src} == {a.id for a in registry.artifacts}
    rt = runtime_view(registry)
    # Only canon artifacts have runtime targets; the template has none.
    assert {v.id for v in rt} == {"framework", "aaref"}
    inst = install_view(registry)
    assert (".trw/frameworks/FRAMEWORK.md") in {dest for _, dest in inst}
    runtime_only = managed_install_view(registry, InstallRole.RUNTIME)
    assert ("framework.md", ".trw/frameworks/FRAMEWORK.md") in runtime_only
    compiled = registry.compiled_canon("framework")
    assert compiled.runtime_compact_core == ".trw/frameworks/FRAMEWORK-CORE.md"
    assert compiled.runtime_reference == ".trw/frameworks/FRAMEWORK-REFERENCE.md"
    assert compiled.runtime_combined == ".trw/frameworks/FRAMEWORK.md"
    tmpl = template_artifact(registry)
    assert tmpl.kind is ArtifactKind.TEMPLATE


def test_compiled_combined_runtime_path_must_match_artifact_runtime_authority() -> None:
    data = _canonical_manifest()
    data["compiled_canons"][0]["runtime_combined"] = ".trw/frameworks/UNDECLARED.md"  # type: ignore[index]
    with pytest.raises(CanonRegistryError, match="runtime_combined") as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    assert exc.value.code is CanonErrorCode.MALFORMED_VALUE


def test_template_is_a_registry_record_bound_to_real_bundled_bytes() -> None:
    """FR06: the PRD template joins the managed registry and its version is readable."""
    reg.clear_cache()
    registry = reg.load_registry()
    tmpl = template_artifact(registry)
    assert tmpl.package_resource == "prd_template.md"
    # The registry version binding extracts the real 3.2 footer from the bundled body.
    assert reg.bundled_source_version(tmpl) == "3.2"
    # The template has no runtime/install target — it is a source-role artifact only.
    assert tmpl.install_targets == ()
    assert tmpl.id not in {v.id for v in runtime_view(registry)}


def test_seeded_artifact_reaches_every_selected_role() -> None:
    data = _canonical_manifest()
    data["artifacts"].append(  # type: ignore[union-attr]
        {
            "id": "seeded",
            "kind": "canon",
            "package_resource": "framework.md",
            "authoring_source": "trw-mcp/src/trw_mcp/data/framework.md",
            "tracked_mirrors": ["SEEDED_MIRROR.md"],
            "install_targets": [{"path": ".trw/frameworks/SEEDED.md", "role": "runtime", "update_policy": "managed"}],
            "version": {"extractor": "framework_header", "config_field": "framework_version"},
        }
    )
    registry = parse_registry(json.dumps(data).encode("utf-8"))
    assert "seeded" in {v.id for v in source_view(registry)}
    assert "seeded" in {v.id for v in runtime_view(registry)}
    assert (".trw/frameworks/SEEDED.md") in {dest for _, dest in install_view(registry)}


# --------------------------------------------------------------------------- #
# NFR01 — independent authority layers truth table                            #
# --------------------------------------------------------------------------- #


def test_authority_layers_have_independent_truth_tables() -> None:
    reg.clear_cache()
    registry = reg.load_registry()
    # Source view derives from tracked mirrors; runtime view from install targets.
    # They must be independently derivable and not equal by construction.
    src_ids = {v.id for v in source_view(registry)}
    rt_ids = {v.id for v in runtime_view(registry)}
    assert src_ids != rt_ids  # template is source-only
    # A registry that drops runtime targets keeps source parity intact.
    data = _canonical_manifest()
    for art in data["artifacts"]:  # type: ignore[union-attr]
        art["install_targets"] = []
    data["compiled_canons"] = []
    dropped = parse_registry(json.dumps(data).encode("utf-8"))
    assert {v.id for v in source_view(dropped)} == src_ids
    assert runtime_view(dropped) == ()


# --------------------------------------------------------------------------- #
# NFR02 — standard-library only + deterministic                               #
# --------------------------------------------------------------------------- #


def test_registry_core_is_standard_library_only_and_deterministic() -> None:
    reg.clear_cache()
    first = reg.load_registry(reg.bundled_manifest_bytes())
    reg.clear_cache()
    second = reg.load_registry(reg.bundled_manifest_bytes())
    assert first.digest == second.digest

    # AST-inspect every canon core module: no import may resolve to a
    # non-stdlib, non-canon-core package (NFR02).
    import ast
    from pathlib import Path

    stdlib = set(sys.stdlib_module_names)
    core_dir = Path(reg.__file__).parent
    core_files = [
        core_dir / name
        for name in ("_errors.py", "_models.py", "_extractors.py", "_loader.py", "_views.py", "registry.py")
    ]
    offenders: list[str] = []
    for path in core_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                if root == "trw_mcp" or root in stdlib:
                    continue
                offenders.append(f"{path.name}: {root}")
    assert not offenders, f"canon core imports non-stdlib packages: {offenders}"


# --------------------------------------------------------------------------- #
# NFR03 — bounded + content-bound cache                                       #
# --------------------------------------------------------------------------- #


def test_registry_resolution_is_bounded_and_cache_key_is_content_bound() -> None:
    reg.clear_cache()
    raw = reg.bundled_manifest_bytes()
    durations: list[float] = []
    for _ in range(100):
        reg.clear_cache()
        start = time.perf_counter()
        reg.load_registry(raw)
        durations.append((time.perf_counter() - start) * 1000.0)
    durations.sort()
    p95 = durations[94]
    assert p95 <= 50.0, f"p95 {p95:.2f}ms exceeds 50ms budget"
    # Cache hit returns the identical object.
    reg.clear_cache()
    a = reg.load_registry(raw)
    b = reg.load_registry(raw)
    assert a is b
    # A mutated manifest is a different cache key (does not return stale object).
    mutated = _mutate(policy="changed policy text")
    c = reg.load_registry(mutated)
    assert c is not a
    assert c.digest != a.digest
