"""Surface snapshot hashing — PRD-HPO-PROF-001 FR-4 / FR-13 / NFR-7.

Belongs to the ``trw_mcp.profile`` package facade. Re-exported there.

Two stable content-hashes are derived from a resolved composition:

* ``surface_snapshot_id`` — SHA-256 over the PERSISTENT layers only
  (defaults, org, domain, task-type, client). Session pins are excluded so
  H4 (meta-proposer) can aggregate outcomes across sessions that share a
  persistent surface (FR-13 rationale).
* ``session_override_hash`` — SHA-256 over the session layer's overrides
  only. Empty layer → empty-content hash.

Determinism (NFR-7): both hashes serialize with canonical JSON
(``sort_keys=True``, no whitespace) so identical layer content produces an
identical id across processes and OSes. The id is prefixed (``surf_`` /
``sess_``) for legibility in telemetry streams.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.profile.model import ProfileLayer

from trw_mcp.profile.model import PERSISTENT_LAYER_NAMES


def _canonical_json(payload: object) -> str:
    """Serialize ``payload`` to canonical JSON (sorted keys, no whitespace)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _overrides_dump(layer: ProfileLayer) -> dict[str, object]:
    """Dump a layer's explicitly-set overrides to a plain dict.

    Reads ``raw_overrides`` so the ``__unset__`` removal sentinel (a meaningful
    persistent surface decision) contributes to the hash. ``None`` values
    ("inherit") are dropped so an all-default layer contributes nothing.
    """
    return {key: value for key, value in layer.raw_overrides.items() if value is not None}


def compute_surface_snapshot_id(layers: list[ProfileLayer]) -> str:
    """Hash the PERSISTENT layers into a stable ``surf_`` id (FR-13).

    Layers are ordered canonically by name (not list position) so two
    compositions with the same persistent content hash identically regardless
    of how the caller ordered them. The session layer is excluded.
    """
    contributions: list[tuple[str, dict[str, object]]] = []
    for layer in layers:
        if layer.name not in PERSISTENT_LAYER_NAMES:
            continue
        dumped = _overrides_dump(layer)
        if not dumped:
            continue
        contributions.append((layer.name, dumped))
    contributions.sort(key=lambda item: item[0])
    digest = hashlib.sha256(_canonical_json(contributions).encode("utf-8")).hexdigest()
    return f"surf_{digest}"


def compute_session_override_hash(layers: list[ProfileLayer]) -> str:
    """Hash the SESSION layer overrides into a stable ``sess_`` id (FR-13).

    Returns the empty-content hash when no session layer contributed.
    """
    session_dump: dict[str, object] = {}
    for layer in layers:
        if layer.name == "session":
            session_dump = _overrides_dump(layer)
            break
    digest = hashlib.sha256(_canonical_json(session_dump).encode("utf-8")).hexdigest()
    return f"sess_{digest}"


__all__ = [
    "compute_session_override_hash",
    "compute_surface_snapshot_id",
]
