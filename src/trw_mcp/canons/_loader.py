"""Strict fail-closed parser for framework_canons.json v2 (PRD-INFRA-164 FR01).

Belongs to the ``trw_mcp.canons.registry`` facade. Re-exported there.

Standard-library only (NFR02). Every validation failure raises a
:class:`CanonRegistryError` with a stable code BEFORE any consumer performs
I/O, and the parser never writes a file. Paths are contained (NFR04): relative,
no traversal, no control characters, normalized to POSIX form.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Final, TypeVar

from trw_mcp.canons._errors import CanonErrorCode, CanonRegistryError
from trw_mcp.canons._models import (
    ArtifactKind,
    CanonArtifact,
    CanonRegistry,
    CompiledCanon,
    InstallRole,
    InstallTarget,
    SurfaceUsage,
    UpdatePolicy,
    VersionBinding,
    VersionExtractor,
    VersionSurface,
)

SUPPORTED_SCHEMA_VERSION: Final[int] = 2
MAX_MANIFEST_BYTES: Final[int] = 256 * 1024

_ARTIFACT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "kind",
        "package_resource",
        "authoring_source",
        "tracked_mirrors",
        "install_targets",
        "version",
    }
)
_TARGET_FIELDS: Final[frozenset[str]] = frozenset({"path", "role", "update_policy"})
_VERSION_FIELDS: Final[frozenset[str]] = frozenset({"extractor", "config_field"})
_SURFACE_FIELDS: Final[frozenset[str]] = frozenset({"id", "path", "selector", "usage", "expected_value", "rationale"})
_COMPILED_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "authoring_source",
        "obligation_inventory",
        "compact_core",
        "reference",
        "combined",
        "runtime_compact_core",
        "runtime_reference",
        "runtime_combined",
        "frozen_baseline_digest",
        "max_core_ratio",
        "compiler_schema",
        "core_mirrors",
        "reference_mirrors",
    }
)
_TOP_FIELDS: Final[frozenset[str]] = frozenset(
    {"schema_version", "policy", "artifacts", "version_surfaces", "compiled_canons"}
)
_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(data: object) -> str:
    """Serialize ``data`` deterministically: sorted keys, compact separators, UTF-8."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(data: object) -> str:
    """Return the SHA-256 hex digest of the canonical JSON form of ``data``."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def _obj(value: object, code: CanonErrorCode, ctx: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CanonRegistryError(code, f"{ctx} must be an object")
    return {str(k): v for k, v in value.items()}


def _reject_unknown(obj: dict[str, object], allowed: frozenset[str], ctx: str) -> None:
    extra = set(obj) - allowed
    if extra:
        raise CanonRegistryError(CanonErrorCode.UNKNOWN_FIELD, f"{ctx} has unknown field(s): {sorted(extra)}")


def _require(obj: dict[str, object], key: str, ctx: str) -> object:
    if key not in obj:
        raise CanonRegistryError(CanonErrorCode.MISSING_FIELD, f"{ctx} missing field: {key}")
    return obj[key]


def _str(value: object, ctx: str) -> str:
    if not isinstance(value, str):
        raise CanonRegistryError(CanonErrorCode.WRONG_TYPE, f"{ctx} must be a string")
    if any(ord(ch) < 0x20 and ch != "\t" for ch in value):
        raise CanonRegistryError(CanonErrorCode.CONTROL_CHARACTER, f"{ctx} contains a control character")
    return value


def _non_empty_str(value: object, ctx: str) -> str:
    text = _str(value, ctx)
    if not text.strip():
        raise CanonRegistryError(CanonErrorCode.EMPTY_VALUE, f"{ctx} must be non-empty")
    return text


def _safe_path(value: object, ctx: str) -> str:
    """Validate a relative, contained, POSIX-normalized path."""
    raw = _non_empty_str(value, ctx)
    if raw.startswith(("/", "\\")) or (len(raw) > 1 and raw[1] == ":"):
        raise CanonRegistryError(CanonErrorCode.ABSOLUTE_PATH, f"{ctx} must be relative: {raw!r}")
    parts = raw.replace("\\", "/").split("/")
    if ".." in parts:
        raise CanonRegistryError(CanonErrorCode.TRAVERSING_PATH, f"{ctx} traverses parents: {raw!r}")
    normalized = "/".join(p for p in parts if p not in ("", "."))
    if not normalized:
        raise CanonRegistryError(CanonErrorCode.EMPTY_VALUE, f"{ctx} normalizes to empty: {raw!r}")
    return normalized


_E = TypeVar("_E", bound=Enum)


def _enum(enum_cls: type[_E], value: object, code: CanonErrorCode, ctx: str) -> _E:
    text = _non_empty_str(value, ctx)
    try:
        return enum_cls(text)
    except ValueError as exc:
        raise CanonRegistryError(code, f"{ctx} has unsupported value: {text!r}") from exc


def _parse_target(raw: object, ctx: str, seen_paths: set[str]) -> InstallTarget:
    obj = _obj(raw, CanonErrorCode.NOT_AN_OBJECT, ctx)
    _reject_unknown(obj, _TARGET_FIELDS, ctx)
    path = _safe_path(_require(obj, "path", ctx), f"{ctx}.path")
    if path in seen_paths:
        raise CanonRegistryError(CanonErrorCode.DUPLICATE_PATH_ROLE, f"{ctx} duplicate install target path: {path}")
    seen_paths.add(path)
    role = _enum(InstallRole, _require(obj, "role", ctx), CanonErrorCode.UNSUPPORTED_ROLE, f"{ctx}.role")
    policy = _enum(
        UpdatePolicy,
        _require(obj, "update_policy", ctx),
        CanonErrorCode.UNSUPPORTED_POLICY,
        f"{ctx}.update_policy",
    )
    return InstallTarget(path=path, role=role, update_policy=policy)


def _parse_version(artifact_obj: dict[str, object], ctx: str) -> VersionBinding:
    raw = _require(artifact_obj, "version", ctx)
    obj = _obj(raw, CanonErrorCode.NOT_AN_OBJECT, f"{ctx}.version")
    _reject_unknown(obj, _VERSION_FIELDS, f"{ctx}.version")
    extractor = _enum(
        VersionExtractor,
        _require(obj, "extractor", f"{ctx}.version"),
        CanonErrorCode.UNSUPPORTED_EXTRACTOR,
        f"{ctx}.version.extractor",
    )
    config_field_raw = obj.get("config_field")
    config_field = None if config_field_raw is None else _non_empty_str(config_field_raw, f"{ctx}.version.config_field")
    return VersionBinding(extractor=extractor, config_field=config_field)


def _parse_artifact(raw: object, index: int, seen_ids: set[str], seen_mirrors: set[str]) -> CanonArtifact:
    ctx = f"artifacts[{index}]"
    obj = _obj(raw, CanonErrorCode.NOT_AN_OBJECT, ctx)
    _reject_unknown(obj, _ARTIFACT_FIELDS, ctx)
    artifact_id = _non_empty_str(_require(obj, "id", ctx), f"{ctx}.id")
    if artifact_id in seen_ids:
        raise CanonRegistryError(CanonErrorCode.DUPLICATE_ID, f"duplicate artifact id: {artifact_id}")
    seen_ids.add(artifact_id)
    kind = _enum(ArtifactKind, _require(obj, "kind", ctx), CanonErrorCode.UNSUPPORTED_KIND, f"{ctx}.kind")

    mirrors_raw = _require(obj, "tracked_mirrors", ctx)
    if not isinstance(mirrors_raw, list):
        raise CanonRegistryError(CanonErrorCode.WRONG_TYPE, f"{ctx}.tracked_mirrors must be a list")
    mirrors: list[str] = []
    for m_index, m in enumerate(mirrors_raw):
        mirror = _safe_path(m, f"{ctx}.tracked_mirrors[{m_index}]")
        if mirror in seen_mirrors:
            raise CanonRegistryError(
                CanonErrorCode.DUPLICATE_PATH_ROLE, f"mirror appears in multiple artifacts: {mirror}"
            )
        seen_mirrors.add(mirror)
        mirrors.append(mirror)

    targets_raw = _require(obj, "install_targets", ctx)
    if not isinstance(targets_raw, list):
        raise CanonRegistryError(CanonErrorCode.WRONG_TYPE, f"{ctx}.install_targets must be a list")
    seen_target_paths: set[str] = set()
    targets = tuple(
        _parse_target(t, f"{ctx}.install_targets[{t_index}]", seen_target_paths)
        for t_index, t in enumerate(targets_raw)
    )

    return CanonArtifact(
        id=artifact_id,
        kind=kind,
        package_resource=_safe_path(_require(obj, "package_resource", ctx), f"{ctx}.package_resource"),
        authoring_source=_safe_path(_require(obj, "authoring_source", ctx), f"{ctx}.authoring_source"),
        tracked_mirrors=tuple(mirrors),
        install_targets=targets,
        version=_parse_version(obj, ctx),
    )


def _parse_surface(raw: object, index: int, seen_ids: set[str]) -> VersionSurface:
    ctx = f"version_surfaces[{index}]"
    obj = _obj(raw, CanonErrorCode.NOT_AN_OBJECT, ctx)
    _reject_unknown(obj, _SURFACE_FIELDS, ctx)
    surface_id = _non_empty_str(_require(obj, "id", ctx), f"{ctx}.id")
    if surface_id in seen_ids:
        raise CanonRegistryError(CanonErrorCode.DUPLICATE_ID, f"duplicate version surface id: {surface_id}")
    seen_ids.add(surface_id)
    selector_raw = obj.get("selector")
    selector = None if selector_raw is None else _non_empty_str(selector_raw, f"{ctx}.selector")
    usage = _enum(SurfaceUsage, _require(obj, "usage", ctx), CanonErrorCode.UNSUPPORTED_USAGE, f"{ctx}.usage")
    expected_raw = obj.get("expected_value")
    expected_value = None if expected_raw is None else _non_empty_str(expected_raw, f"{ctx}.expected_value")
    rationale_raw = obj.get("rationale")
    rationale = None if rationale_raw is None else _non_empty_str(rationale_raw, f"{ctx}.rationale")
    if usage is SurfaceUsage.HISTORICAL_RECORD and not (selector and expected_value and rationale):
        raise CanonRegistryError(
            CanonErrorCode.MISSING_FIELD,
            f"{ctx}: historical_record requires selector, expected_value, and rationale",
        )
    return VersionSurface(
        id=surface_id,
        path=_safe_path(_require(obj, "path", ctx), f"{ctx}.path"),
        selector=selector,
        usage=usage,
        expected_value=expected_value,
        rationale=rationale,
    )


def _parse_mirror_list(obj: dict[str, object], key: str, ctx: str, seen: set[str]) -> tuple[str, ...]:
    raw = obj.get(key, [])
    if not isinstance(raw, list):
        raise CanonRegistryError(CanonErrorCode.WRONG_TYPE, f"{ctx}.{key} must be a list")
    out: list[str] = []
    for i, item in enumerate(raw):
        path = _safe_path(item, f"{ctx}.{key}[{i}]")
        if path in seen:
            raise CanonRegistryError(
                CanonErrorCode.DUPLICATE_PATH_ROLE, f"{ctx}: generated output declared twice: {path}"
            )
        seen.add(path)
        out.append(path)
    return tuple(out)


def _parse_compiled(raw: object, index: int, seen_ids: set[str], seen_outputs: set[str]) -> CompiledCanon:
    ctx = f"compiled_canons[{index}]"
    obj = _obj(raw, CanonErrorCode.NOT_AN_OBJECT, ctx)
    _reject_unknown(obj, _COMPILED_FIELDS, ctx)
    canon_id = _non_empty_str(_require(obj, "id", ctx), f"{ctx}.id")
    if canon_id in seen_ids:
        raise CanonRegistryError(CanonErrorCode.DUPLICATE_ID, f"duplicate compiled canon id: {canon_id}")
    seen_ids.add(canon_id)

    def _output(key: str) -> str:
        path = _safe_path(_require(obj, key, ctx), f"{ctx}.{key}")
        if path in seen_outputs:
            raise CanonRegistryError(
                CanonErrorCode.DUPLICATE_PATH_ROLE, f"{ctx}: generated output declared twice: {path}"
            )
        seen_outputs.add(path)
        return path

    authoring_source = _safe_path(_require(obj, "authoring_source", ctx), f"{ctx}.authoring_source")
    inventory = _output("obligation_inventory")
    compact_core = _output("compact_core")
    reference = _output("reference")
    combined = _safe_path(_require(obj, "combined", ctx), f"{ctx}.combined")
    runtime_compact_core = _output("runtime_compact_core")
    runtime_reference = _output("runtime_reference")
    runtime_combined = _output("runtime_combined")

    digest = _non_empty_str(_require(obj, "frozen_baseline_digest", ctx), f"{ctx}.frozen_baseline_digest")
    if not _HEX64_RE.match(digest):
        raise CanonRegistryError(
            CanonErrorCode.MALFORMED_VALUE, f"{ctx}.frozen_baseline_digest must be a 64-char sha256 hex"
        )
    ratio = _require(obj, "max_core_ratio", ctx)
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not (0.0 < float(ratio) <= 1.0):
        raise CanonRegistryError(CanonErrorCode.MALFORMED_VALUE, f"{ctx}.max_core_ratio must be a number in (0, 1]")
    schema = _require(obj, "compiler_schema", ctx)
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        raise CanonRegistryError(CanonErrorCode.MALFORMED_VALUE, f"{ctx}.compiler_schema must be a positive integer")
    core_mirrors = _parse_mirror_list(obj, "core_mirrors", ctx, seen_outputs)
    reference_mirrors = _parse_mirror_list(obj, "reference_mirrors", ctx, seen_outputs)

    return CompiledCanon(
        id=canon_id,
        authoring_source=authoring_source,
        obligation_inventory=inventory,
        compact_core=compact_core,
        reference=reference,
        combined=combined,
        runtime_compact_core=runtime_compact_core,
        runtime_reference=runtime_reference,
        runtime_combined=runtime_combined,
        frozen_baseline_digest=digest,
        max_core_ratio=float(ratio),
        compiler_schema=schema,
        core_mirrors=core_mirrors,
        reference_mirrors=reference_mirrors,
    )


def parse_registry(raw_bytes: bytes) -> CanonRegistry:
    """Parse and strictly validate registry bytes into a frozen ``CanonRegistry``.

    Fails closed with a stable code on any schema, path, duplicate, control-char,
    or size violation before returning a partial plan or writing anything.
    """
    if len(raw_bytes) > MAX_MANIFEST_BYTES:
        raise CanonRegistryError(CanonErrorCode.OVERSIZED_INPUT, f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CanonRegistryError(CanonErrorCode.MALFORMED_VALUE, f"manifest is not valid JSON: {exc}") from exc

    obj = _obj(data, CanonErrorCode.NOT_AN_OBJECT, "manifest")
    _reject_unknown(obj, _TOP_FIELDS, "manifest")
    if obj.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise CanonRegistryError(
            CanonErrorCode.UNSUPPORTED_SCHEMA,
            f"unsupported schema_version: {obj.get('schema_version')!r} (expected {SUPPORTED_SCHEMA_VERSION})",
        )

    artifacts_raw = _require(obj, "artifacts", "manifest")
    if not isinstance(artifacts_raw, list) or not artifacts_raw:
        raise CanonRegistryError(CanonErrorCode.WRONG_TYPE, "manifest.artifacts must be a non-empty list")
    seen_ids: set[str] = set()
    seen_mirrors: set[str] = set()
    artifacts = tuple(_parse_artifact(a, i, seen_ids, seen_mirrors) for i, a in enumerate(artifacts_raw))

    surfaces_raw = obj.get("version_surfaces", [])
    if not isinstance(surfaces_raw, list):
        raise CanonRegistryError(CanonErrorCode.WRONG_TYPE, "manifest.version_surfaces must be a list")
    seen_surface_ids: set[str] = set()
    surfaces = tuple(_parse_surface(s, i, seen_surface_ids) for i, s in enumerate(surfaces_raw))

    compiled_raw = obj.get("compiled_canons", [])
    if not isinstance(compiled_raw, list):
        raise CanonRegistryError(CanonErrorCode.WRONG_TYPE, "manifest.compiled_canons must be a list")
    seen_compiled_ids: set[str] = set()
    seen_outputs: set[str] = set()
    compiled = tuple(_parse_compiled(c, i, seen_compiled_ids, seen_outputs) for i, c in enumerate(compiled_raw))
    artifacts_by_id = {artifact.id: artifact for artifact in artifacts}
    for canon in compiled:
        artifact = artifacts_by_id.get(canon.id)
        if artifact is None:
            raise CanonRegistryError(
                CanonErrorCode.MALFORMED_VALUE,
                f"compiled canon {canon.id!r} has no matching artifact record",
            )
        declared_runtime = {target.path for target in artifact.runtime_targets}
        if canon.runtime_combined not in declared_runtime:
            raise CanonRegistryError(
                CanonErrorCode.MALFORMED_VALUE,
                f"compiled canon {canon.id!r} runtime_combined is not an artifact runtime target",
            )

    return CanonRegistry(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        artifacts=artifacts,
        version_surfaces=surfaces,
        digest=digest_of(data),
        compiled_canons=compiled,
    )


__all__ = [
    "MAX_MANIFEST_BYTES",
    "SUPPORTED_SCHEMA_VERSION",
    "canonical_json",
    "digest_of",
    "parse_registry",
]
