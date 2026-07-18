"""Frozen live-process fingerprint over loaded versions + realized MCP surface.

PRD-INFRA-164 FR07/NFR07. STRICT STANDARD LIBRARY ONLY (NFR02): dataclasses,
enum, json, hashlib. No fastmcp/structlog import — the server adapter passes
already-listed public declarations as plain data so this core stays dependency
free and unit-testable.

The fingerprint answers one operational question: *which declared
package/canon/public-MCP surface did this connected process freeze?* It is
identity, not attestation (Non-Goal). The digest binds versions, registry +
managed-source digests, and the sorted realized public tool/resource/prompt
declarations. Volatile metadata (timestamp, PID, checkout path, discovery
order, secrets) is excluded, so two identical surfaces in different locations
or orders produce the same digest.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from enum import Enum
from types import CodeType, FunctionType, ModuleType

FINGERPRINT_SCHEMA_VERSION = 2


class Currentness(str, Enum):
    """First-class currentness result — UNKNOWN is never a green/current state."""

    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PublicToolDecl:
    """Normalized realized public tool declaration."""

    name: str
    description: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]


@dataclass(frozen=True)
class PublicResourceDecl:
    """Normalized realized public resource declaration."""

    uri: str
    name: str
    description: str


@dataclass(frozen=True)
class PublicPromptDecl:
    """Normalized realized public prompt declaration."""

    name: str
    description: str


@dataclass(frozen=True)
class RealizedSurface:
    """Sorted, normalized public MCP surface after exposure filtering."""

    tools: tuple[PublicToolDecl, ...]
    resources: tuple[PublicResourceDecl, ...]
    prompts: tuple[PublicPromptDecl, ...]

    def as_payload(self) -> dict[str, object]:
        """Deterministic dict payload with sorted declarations."""
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "output_schema": t.output_schema,
                }
                for t in sorted(self.tools, key=lambda x: x.name)
            ],
            "resources": [
                {"uri": r.uri, "name": r.name, "description": r.description}
                for r in sorted(self.resources, key=lambda x: x.uri)
            ],
            "prompts": [
                {"name": p.name, "description": p.description} for p in sorted(self.prompts, key=lambda x: x.name)
            ],
        }


@dataclass(frozen=True)
class ProcessFingerprint:
    """Frozen process identity: bound versions, digests, and surface digest."""

    schema_version: int
    trw_mcp_version: str
    framework_version: str
    aaref_version: str
    template_version: str
    registry_digest: str
    source_digests: dict[str, str]
    loaded_module_digest: str
    surface_digest: str
    digest: str

    def public_payload(self) -> dict[str, object]:
        """Secret-free serializable payload (safe for status/resources/logs)."""
        return {
            "schema_version": self.schema_version,
            "trw_mcp_version": self.trw_mcp_version,
            "framework_version": self.framework_version,
            "aaref_version": self.aaref_version,
            "template_version": self.template_version,
            "registry_digest": self.registry_digest,
            "source_digests": dict(sorted(self.source_digests.items())),
            "loaded_module_digest": self.loaded_module_digest,
            "surface_digest": self.surface_digest,
            "digest": self.digest,
        }


def _canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _code_payload(code: CodeType) -> dict[str, object]:
    """Return a location-independent representation of a loaded code object."""

    def constant_payload(value: object) -> object:
        if isinstance(value, CodeType):
            return _code_payload(value)
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        if isinstance(value, tuple):
            return [constant_payload(item) for item in value]
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return {"type": type(value).__qualname__}

    return {
        "code": code.co_code.hex(),
        "consts": [constant_payload(value) for value in code.co_consts],
        "names": code.co_names,
        "varnames": code.co_varnames,
        "freevars": code.co_freevars,
        "cellvars": code.co_cellvars,
        "flags": code.co_flags,
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
    }


def _module_code_digests(name: str, module: ModuleType) -> list[tuple[str, str]]:
    """Hash functions and methods whose live code belongs to ``module``."""
    functions: list[tuple[str, FunctionType]] = []
    for attr_name, value in sorted(vars(module).items()):
        if isinstance(value, FunctionType) and value.__module__ == name:
            functions.append((attr_name, value))
            continue
        if isinstance(value, type) and value.__module__ == name:
            for method_name, descriptor in sorted(vars(value).items()):
                if isinstance(descriptor, (staticmethod, classmethod)):
                    descriptor = descriptor.__func__
                if isinstance(descriptor, FunctionType) and descriptor.__module__ == name:
                    functions.append((f"{attr_name}.{method_name}", descriptor))
    return [
        (qualname, hashlib.sha256(_canonical(_code_payload(function.__code__)).encode("utf-8")).hexdigest())
        for qualname, function in functions
    ]


def digest_loaded_modules(package: str = "trw_mcp") -> str:
    """Hash the bytes of package modules already loaded in this process.

    Module names, rather than filesystem paths, identify entries so equivalent
    installs at different locations produce the same digest. Unreadable or
    non-file modules are represented explicitly instead of being omitted; this
    keeps degraded/mixed deployments diagnosable without leaking paths.
    """
    entries: list[dict[str, object]] = []
    prefix = f"{package}."
    for name, module in sorted(sys.modules.items()):
        if name != package and not name.startswith(prefix):
            continue
        if not isinstance(module, ModuleType):
            continue
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str):
            source_digest = "no-file"
        else:
            try:
                with open(origin, "rb") as stream:
                    source_digest = hashlib.sha256(stream.read()).hexdigest()
            except OSError:
                source_digest = "unreadable"
        entries.append(
            {
                "name": name,
                "source_digest": source_digest,
                "runtime_code": _module_code_digests(name, module),
            }
        )
    payload: dict[str, object] = {"package": package, "modules": entries}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def freeze_fingerprint(
    *,
    trw_mcp_version: str,
    framework_version: str,
    aaref_version: str,
    template_version: str,
    registry_digest: str,
    source_digests: dict[str, str],
    surface: RealizedSurface,
    loaded_module_digest: str = "unknown",
) -> ProcessFingerprint:
    """Freeze a canonical, secret-free process fingerprint and its digest.

    The returned record is immutable; callers cache it and must NOT recompute
    it from changed disk bytes on later reads (that would let a stale editable
    install falsely inherit new bytes — FR07).
    """
    surface_payload = surface.as_payload()
    surface_digest = hashlib.sha256(_canonical(surface_payload).encode("utf-8")).hexdigest()
    digest_payload: dict[str, object] = {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "trw_mcp_version": trw_mcp_version,
        "framework_version": framework_version,
        "aaref_version": aaref_version,
        "template_version": template_version,
        "registry_digest": registry_digest,
        "source_digests": dict(sorted(source_digests.items())),
        "loaded_module_digest": loaded_module_digest,
        "surface_digest": surface_digest,
    }
    digest = hashlib.sha256(_canonical(digest_payload).encode("utf-8")).hexdigest()
    return ProcessFingerprint(
        schema_version=FINGERPRINT_SCHEMA_VERSION,
        trw_mcp_version=trw_mcp_version,
        framework_version=framework_version,
        aaref_version=aaref_version,
        template_version=template_version,
        registry_digest=registry_digest,
        source_digests=dict(sorted(source_digests.items())),
        loaded_module_digest=loaded_module_digest,
        surface_digest=surface_digest,
        digest=digest,
    )


def compare_generation(
    frozen: ProcessFingerprint | None,
    *,
    expected_registry_digest: str | None,
    expected_source_digests: dict[str, str] | None,
) -> Currentness:
    """Compare a frozen process against the current deployed/source generation.

    Missing frozen data or missing expected data yields UNKNOWN — absence never
    means current (FR08/NFR07). Digest divergence yields STALE. Only an exact
    registry + managed-source-digest match yields CURRENT.
    """
    if frozen is None or expected_registry_digest is None or expected_source_digests is None:
        return Currentness.UNKNOWN
    if frozen.registry_digest != expected_registry_digest:
        return Currentness.STALE
    if dict(sorted(frozen.source_digests.items())) != dict(sorted(expected_source_digests.items())):
        return Currentness.STALE
    return Currentness.CURRENT


# Process-global frozen fingerprint (set once after MCP registration).
_FROZEN: ProcessFingerprint | None = None


def set_frozen_fingerprint(fingerprint: ProcessFingerprint) -> None:
    """Store the startup fingerprint once; reject a changed-generation refreeze."""
    global _FROZEN
    if _FROZEN is None:
        _FROZEN = fingerprint
        return
    if _FROZEN.digest != fingerprint.digest:
        raise RuntimeError("live process fingerprint is already frozen for a different generation")


def get_frozen_fingerprint() -> ProcessFingerprint | None:
    """Return the frozen process fingerprint, or ``None`` if not yet frozen."""
    return _FROZEN


def reset_frozen_fingerprint() -> None:
    """Clear the frozen fingerprint (test hook)."""
    global _FROZEN
    _FROZEN = None


__all__ = [
    "FINGERPRINT_SCHEMA_VERSION",
    "Currentness",
    "ProcessFingerprint",
    "PublicPromptDecl",
    "PublicResourceDecl",
    "PublicToolDecl",
    "RealizedSurface",
    "compare_generation",
    "digest_loaded_modules",
    "freeze_fingerprint",
    "get_frozen_fingerprint",
    "reset_frozen_fingerprint",
    "set_frozen_fingerprint",
]
