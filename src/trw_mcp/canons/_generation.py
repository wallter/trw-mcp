"""Repo-level compiled-canon generation + parity (PRD-CORE-207 FR02/FR04/FR05).

Belongs to the ``trw_mcp.canons.registry`` facade. Re-exported there.

This module turns the typed ``compiled_canons`` registry records into concrete
generated files under a repository root, and provides the two gate flows the
``scripts/compile-framework-canons.py`` CLI exposes:

* ``check_generation`` -- read-only parity: recompile from source, verify the
  combined view is byte-identical to the frozen baseline (FR02/FR04) and that
  the on-disk core/reference/inventory equal the freshly compiled bytes; report
  every drift with the regeneration command (FR05). Never writes.
* ``write_generation`` -- deterministic ``--write``: compile, validate every
  invariant (byte budget NFR04, core anchor coverage FR03/NFR01, portability
  NFR05), render outputs to temp files, and atomically replace only after the
  whole generation is valid (compiler contract step 9). Fail-closed: a single
  invalid canon aborts before any output changes.

Standard-library only (NFR02): json, hashlib, os, pathlib.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from trw_mcp.canons._compiler import CompileResult, compile_canon, core_byte_ratio
from trw_mcp.canons._errors import CanonErrorCode, CanonRegistryError
from trw_mcp.canons._invariants import missing_core_anchors, scan_forbidden
from trw_mcp.canons._models import CanonRegistry, CompiledCanon

_REGEN_CMD = "python3 scripts/compile-framework-canons.py --write"

# NFR03: legacy combined paths stay valid for at least this many minor releases.
COMBINED_COMPATIBILITY_MIN_RELEASES = 2


@dataclass(frozen=True)
class GeneratedOutput:
    """One generated file's relative path and freshly compiled bytes."""

    path: str
    content: str


def _read(repo_root: Path, rel: str) -> str:
    return (repo_root / rel).read_text(encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def serialize_inventory(inventory: dict[str, object]) -> str:
    """Deterministic JSON (valid YAML) inventory text: sorted keys, trailing newline."""
    return json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def compile_registry_canon(repo_root: Path, compiled: CompiledCanon) -> CompileResult:
    """Compile one registry canon from its marked source under ``repo_root``.

    Fail-closed: the compiler rejects malformed markers / reference-only
    obligations, and this adds the frozen-baseline and byte-budget guards.
    """
    source_text = _read(repo_root, compiled.authoring_source)
    basename = compiled.authoring_source.rsplit("/", 1)[-1]
    result = compile_canon(compiled.id, source_text, source_basename=basename)

    if _sha256(result.combined) != compiled.frozen_baseline_digest:
        raise CanonRegistryError(
            CanonErrorCode.MALFORMED_VALUE,
            f"{compiled.id}: compiled combined digest {_sha256(result.combined)} != frozen baseline "
            f"{compiled.frozen_baseline_digest}; source drifted from baseline. Regenerate baseline or source.",
        )
    baseline_bytes = len((repo_root / compiled.combined).read_bytes())
    ratio = core_byte_ratio(result, baseline_bytes)
    if ratio > compiled.max_core_ratio:
        raise CanonRegistryError(
            CanonErrorCode.MALFORMED_VALUE,
            f"{compiled.id}: compact core is {ratio:.3f} of baseline; exceeds max_core_ratio {compiled.max_core_ratio}",
        )
    missing = missing_core_anchors(compiled.id, result.core)
    if missing:
        raise CanonRegistryError(
            CanonErrorCode.MALFORMED_VALUE,
            f"{compiled.id}: compact core missing required normative anchors: {list(missing)}",
        )
    forbidden = scan_forbidden(result.core)
    if forbidden:
        raise CanonRegistryError(
            CanonErrorCode.MALFORMED_VALUE,
            f"{compiled.id}: compact core contains non-portable tokens (NFR05): {forbidden}",
        )
    return result


def generated_outputs(repo_root: Path, compiled: CompiledCanon) -> tuple[GeneratedOutput, ...]:
    """Freshly compiled ``(path, content)`` for core, reference, and inventory."""
    result = compile_registry_canon(repo_root, compiled)
    return (
        GeneratedOutput(compiled.compact_core, result.core),
        GeneratedOutput(compiled.reference, result.reference),
        GeneratedOutput(compiled.obligation_inventory, serialize_inventory(result.inventory)),
    )


def check_generation(repo_root: Path, registry: CanonRegistry) -> list[str]:
    """Read-only parity check across every compiled canon (FR02/FR04/FR05).

    Returns a list of human-readable drift errors (empty == parity holds). Never
    writes. Combined byte-identity, core/reference/inventory on-disk parity, and
    every validation guard are enforced.
    """
    errors: list[str] = []
    for compiled in registry.compiled_canons:
        try:
            outputs = generated_outputs(repo_root, compiled)
        except (CanonRegistryError, FileNotFoundError, OSError) as exc:
            errors.append(f"{compiled.id}: {exc}")
            continue
        for output in outputs:
            target = repo_root / output.path
            if not target.is_file():
                errors.append(f"{compiled.id}: missing generated output {output.path}; run: {_REGEN_CMD}")
                continue
            on_disk = target.read_text(encoding="utf-8")
            if on_disk != output.content:
                errors.append(
                    f"{compiled.id}: generated output drift: {output.path} "
                    f"(hand edit or stale) — source is {compiled.authoring_source}; run: {_REGEN_CMD}"
                )
    return errors


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)


def write_generation(repo_root: Path, registry: CanonRegistry) -> list[str]:
    """Deterministic ``--write``: compile/validate all, then atomically replace.

    Fail-closed: every canon is compiled and validated (raising on any invalid
    generation) BEFORE a single output file is touched, so a bad canon never
    leaves a half-written generation (NFR06 compiler-side).
    """
    plan: list[GeneratedOutput] = []
    for compiled in registry.compiled_canons:
        plan.extend(generated_outputs(repo_root, compiled))
    written: list[str] = []
    for output in plan:
        _atomic_write(repo_root / output.path, output.content)
        written.append(output.path)
    return written


def compact_generation_write_plan(registry: CanonRegistry, *, stamp_path: str) -> list[tuple[str, str]]:
    """Ordered update steps for a compact-generation deployment (FR07/NFR03).

    Contract: every compiled body (legacy ``combined`` retained for backward
    compatibility, plus ``compact_core`` and ``reference``), the obligation
    inventory, and the version/digest stamp are written and verified BEFORE the
    single terminal ``instruction_pointer`` step flips generated instructions to
    compact paths. Failure injection after any body write therefore leaves the
    prior generation active (the pointer has not moved); the pointer only moves
    once the complete new generation is present.

    Returns ``(step_kind, path)`` tuples where ``step_kind`` in
    ``{"body", "inventory", "stamp", "instruction_pointer"}``.
    """
    steps: list[tuple[str, str]] = []
    for compiled in registry.compiled_canons:
        steps.append(("body", compiled.combined))  # legacy combined kept (NFR03)
        steps.append(("body", compiled.compact_core))
        steps.append(("body", compiled.reference))
        steps.append(("inventory", compiled.obligation_inventory))
    steps.append(("stamp", stamp_path))
    steps.append(("instruction_pointer", "compact"))  # terminal, last (FR07)
    return steps


def legacy_combined_paths(registry: CanonRegistry) -> tuple[str, ...]:
    """Every legacy combined path that stays a valid generated output (NFR03)."""
    return tuple(compiled.combined for compiled in registry.compiled_canons)


__all__ = [
    "COMBINED_COMPATIBILITY_MIN_RELEASES",
    "GeneratedOutput",
    "check_generation",
    "compact_generation_write_plan",
    "compile_registry_canon",
    "generated_outputs",
    "legacy_combined_paths",
    "serialize_inventory",
    "write_generation",
]
