"""Layer file loading + legacy shim — PRD-HPO-PROF-001 FR-5/10/12.

Belongs to the ``trw_mcp.profile`` package facade. Re-exported there.

``load_layer(name, path)`` reads one YAML layer file into a ``ProfileLayer``.
Behavior (Behavior Switch Matrix §7.4):
  * Missing file → ``None`` (caller skips; layer not in ``layers_applied``)
    — FR-5 "absent org layer is the empty overlay".
  * Malformed YAML / schema failure → ``LayerLoadError`` (fail closed, with
    the offending path; NO silent fallback to defaults) — FR-12.

``discover_layers(trw_dir, domain, task_type)`` resolves the on-disk
persistent layers (org / domain / task-type) from ``.trw/profiles/``.

``translate_legacy_client_profile(client_id)`` is the FR-10 shim: it maps a
legacy ``client_profiles.*`` client id (including the removed bare
``cursor`` → ``cursor-cli``) to a ``client`` ProfileLayer, emitting a
DEPRECATION log once.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from trw_mcp.profile.model import Profile, ProfileLayer

logger = structlog.get_logger(__name__)

_yaml = YAML(typ="safe")

#: FR-10: legacy client-id remap. The bare ``cursor`` id was removed in
#: Sprint 91; map it to ``cursor-cli`` (the CLI transport) to preserve
#: behavior for one release cycle.
_LEGACY_CLIENT_REMAP: dict[str, str] = {"cursor": "cursor-cli"}


class LayerLoadError(ValueError):
    """Raised when a layer file is malformed or schema-invalid (FR-12).

    Carries the offending path so the resolver can fail loudly with the
    location, never silently falling back to defaults.
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"profile layer load failed at {path}: {reason}")


def _is_contained(path: Path, base_dir: Path) -> bool:
    """Return True iff ``path`` resolves to a location inside ``base_dir``.

    Round-2 path-traversal hardening: the layer file name is derived from an
    attacker-influenceable ``domain`` / ``task_type`` string (``infer_domain``
    trusts an explicit value verbatim). A value like ``x/../../secret`` or a
    symlink planted under ``.trw/profiles`` could otherwise make ``load_layer``
    read a file OUTSIDE the profiles dir. Both ``path`` and ``base_dir`` are
    fully resolved (``strict=False`` so a missing file still normalizes, and
    symlinks are followed) before the containment comparison, so neither ``..``
    traversal nor a symlink escape survives.
    """
    try:
        resolved = path.resolve(strict=False)
        base_resolved = base_dir.resolve(strict=False)
    except (OSError, RuntimeError):  # RuntimeError: symlink loop
        return False
    return resolved == base_resolved or base_resolved in resolved.parents


def load_layer(name: str, path: Path, *, base_dir: Path | None = None) -> ProfileLayer | None:
    """Load one YAML layer file into a ``ProfileLayer`` (FR-5/FR-12).

    Returns ``None`` when the file is absent (skip the layer). Raises
    ``LayerLoadError`` on malformed YAML or schema validation failure — the
    resolver MUST NOT degrade to defaults silently.

    ``base_dir`` (round-2 hardening): when supplied, the resolved ``path`` MUST
    stay inside ``base_dir`` (after following ``..`` and symlinks). A path that
    escapes the profiles directory raises ``LayerLoadError`` — a planted
    symlink or a ``../`` in the layer name can never read outside ``.trw/profiles``.
    """
    if base_dir is not None and not _is_contained(path, base_dir):
        raise LayerLoadError(
            str(path),
            f"layer path escapes the profiles directory {base_dir} (path traversal blocked)",
        )
    if not path.exists():
        logger.info("profile_layer_absent", layer=name, path=str(path))
        return None
    try:
        raw = _yaml.load(path.read_text(encoding="utf-8"))
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        raise LayerLoadError(str(path), f"malformed YAML: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise LayerLoadError(str(path), "top-level layer document must be a mapping")
    rationale = raw.pop("rationale", None)
    # Pass the raw override dict straight to ProfileLayer so the __unset__
    # sentinel survives (the layer's before-validator splits typed vs raw).
    try:
        layer = ProfileLayer.model_validate(
            {
                "name": name,
                "overrides": raw,
                "rationale": rationale if isinstance(rationale, str) else None,
                "source_path": str(path),
            }
        )
    except ValueError as exc:
        raise LayerLoadError(str(path), f"schema validation failed: {exc}") from exc
    return layer


def discover_layers(
    trw_dir: Path,
    *,
    domain: str | None = None,
    task_type: str | None = None,
) -> list[ProfileLayer]:
    """Discover the on-disk persistent layers under ``.trw/profiles/`` (FR-5).

    Resolves, in order: ``org.yaml`` (always probed), ``domain-{domain}.yaml``
    (when ``domain`` is a real area, not ``unknown``), and
    ``task-{task_type}.yaml`` (when not ``generic``). Absent files are
    skipped. Malformed files raise ``LayerLoadError`` (FR-12).
    """
    base = trw_dir / "profiles"
    layers: list[ProfileLayer] = []

    # base_dir containment is enforced on every load so a ``domain`` /
    # ``task_type`` carrying ``../`` or a planted symlink cannot read outside
    # ``.trw/profiles`` (round-2 path-traversal hardening).
    org = load_layer("org", base / "org.yaml", base_dir=base)
    if org is not None:
        layers.append(org)

    if domain and domain != "unknown":
        dom = load_layer("domain", base / f"domain-{domain}.yaml", base_dir=base)
        if dom is not None:
            layers.append(dom)

    if task_type and task_type != "generic":
        tt = load_layer("task-type", base / f"task-{task_type}.yaml", base_dir=base)
        if tt is not None:
            layers.append(tt)

    return layers


def translate_legacy_client_profile(client_id: str) -> ProfileLayer:
    """Translate a legacy client id into a ``client`` ProfileLayer (FR-10).

    Maps the removed bare ``cursor`` id to ``cursor-cli`` and emits a single
    DEPRECATION log so users migrate within the release cycle. The resulting
    layer carries no surface overrides itself — it records the resolved
    client id as provenance; the built-in ClientProfile registry continues to
    own transport concerns. This shim's job is to keep legacy config keys
    loading without error during the migration window.
    """
    resolved = _LEGACY_CLIENT_REMAP.get(client_id, client_id)
    if resolved != client_id:
        logger.warning(
            "profile_legacy_client_remap",
            legacy_client_id=client_id,
            resolved_client_id=resolved,
            message=(
                "legacy client_profiles key remapped; update target_platforms "
                "to the new client id within one release cycle"
            ),
        )
    return ProfileLayer(
        name="client",
        overrides=Profile(),
        rationale=f"legacy client_profiles shim -> {resolved}",
        source_path=f"client_profiles:{resolved}",
    )


__all__ = [
    "LayerLoadError",
    "discover_layers",
    "load_layer",
    "translate_legacy_client_profile",
]
