"""Deployed compiled-generation coherence inspection (PRD-CORE-207 FR06).

Belongs to the ``trw_mcp.canons.registry`` facade. Re-exported there.

Repository source parity (``check-aaref-sync.py``) and deployed runtime
integrity are separate gates. This module adds the runtime-side gate for a
*compiled generation*: a deployed project may carry a compact core, a reference
module, a combined compatibility body, and a stamp -- and they MUST all belong
to the same compiled generation. Any missing, byte-drifted (stale), or
cross-generation body/stamp yields a DISTINCT, actionable error so doctor/repair
can name exactly what to regenerate.

This is a separate gate from ``framework_integrity.inspect_framework_runtime``
(which checks the legacy combined body); it does not replace it (Non-Goal:
"do not collapse those gates into one check").

Standard-library only (NFR02): dataclasses, hashlib, pathlib.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# The three deployed bodies that make one compiled generation, plus the stamp.
_BODY_ROLES = ("compact_core", "reference", "combined")


@dataclass(frozen=True)
class GenerationExpectation:
    """Expected deployed digests for one canon's compiled generation.

    ``role_paths`` maps each role in ``_BODY_ROLES`` to a target-relative path;
    ``role_digests`` maps each role to the expected sha256 of its bytes.
    """

    canon_id: str
    role_paths: Mapping[str, str]
    role_digests: Mapping[str, str]
    generation_digest: str


@dataclass(frozen=True)
class CompiledGenerationReport:
    """Result of inspecting one deployed compiled generation."""

    target: Path
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generation_digest(role_digests: Mapping[str, str]) -> str:
    """Deterministic single-generation identity over the per-role digests."""
    joined = "|".join(f"{role}={role_digests[role]}" for role in sorted(role_digests))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def inspect_compiled_generation(
    target: Path,
    expectations: tuple[GenerationExpectation, ...],
    *,
    stamp_path: str,
    stamp_generation_digest: str | None,
) -> CompiledGenerationReport:
    """Verify every deployed compiled body + stamp belongs to one generation.

    Distinct error classes:
      * ``<canon> <role> missing`` -- a required body is absent.
      * ``<canon> <role> stale`` -- a body's bytes drifted from the generation.
      * ``cross-generation`` -- the stamp names a different generation than the
        deployed bodies (mixed generation state).
    """
    target = target.resolve()
    errors: list[str] = []
    for expectation in expectations:
        for role in _BODY_ROLES:
            rel = expectation.role_paths.get(role)
            expected = expectation.role_digests.get(role)
            if rel is None or expected is None:
                errors.append(f"{expectation.canon_id} {role} unmapped: incomplete generation expectation")
                continue
            body_path = target / rel
            try:
                actual = _sha256_bytes(body_path.read_bytes())
            except FileNotFoundError:
                errors.append(f"{expectation.canon_id} {role} missing: {rel} (regenerate + redeploy generation)")
                continue
            except OSError as exc:
                errors.append(f"{expectation.canon_id} {role} unreadable: {rel}: {exc}")
                continue
            if actual != expected:
                errors.append(
                    f"{expectation.canon_id} {role} stale: {rel} body_digest_mismatch ({actual} != {expected})"
                )

    # Stamp / cross-generation check: the deployed stamp must name THIS generation.
    if stamp_generation_digest is None:
        errors.append(f"deployment stamp missing generation_digest: {stamp_path} (needs_upgrade)")
    else:
        expected_gen = {e.canon_id: e.generation_digest for e in expectations}
        composite = generation_digest({e.canon_id: e.generation_digest for e in expectations}) if expectations else ""
        if stamp_generation_digest != composite:
            errors.append(
                f"cross-generation: deployment stamp generation_digest={stamp_generation_digest} "
                f"does not match deployed bodies {composite} ({sorted(expected_gen)})"
            )

    return CompiledGenerationReport(target=target, errors=tuple(errors))


__all__ = [
    "CompiledGenerationReport",
    "GenerationExpectation",
    "generation_digest",
    "inspect_compiled_generation",
]
