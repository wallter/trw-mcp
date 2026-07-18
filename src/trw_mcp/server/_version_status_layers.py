"""Live-process and historical status layers for the unified version taxonomy.

PRD-INFRA-164 FR09. Belongs to the ``_subcommands_release.py`` facade — extracted
so ``collect_version_status`` stays under the 350-effective-LOC module gate while
adding the connected-live-process and historical-installer layers.

These layers make doctor, ``version-status``, and ``trw://framework/versions``
share one taxonomy: the same authoring/deployed/live/historical labels, the same
frozen fingerprint, and the same stable currentness codes. The live-process layer
is the ONLY evidence for a separately-connected process; a fresh CLI cannot borrow
its own version to attest a different running stdio process. The historical
installer snapshot is reported in its own section and is NEVER a current authority.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.canons.fingerprint import compare_generation, get_frozen_fingerprint
from trw_mcp.canons.registry import load_registry, managed_source_digests

logger = structlog.get_logger(__name__)

# Fresh-CLI scope note (FR09): doctor/version-status run as a fresh process and
# cannot attest a *different* already-running stdio MCP process.
FRESH_CLI_ATTEST_NOTE = (
    "This is the connected process's own frozen surface; a fresh CLI cannot "
    "attest that a separately-connected stdio MCP process is current — that "
    "process's own live fingerprint (via its session/resource) is the evidence."
)

HISTORICAL_INSTALLER_NOTE = (
    "Historical install-time snapshot; preserved as history and never a current runtime authority (PRD-INFRA-164 D-26)."
)


def _expected_generation() -> tuple[str | None, dict[str, str] | None]:
    """Current bundled deployed-canon generation (registry digest + source digests)."""
    try:
        registry = load_registry()
        return registry.digest, managed_source_digests(registry)
    except Exception:  # justified: unreadable registry -> unknown, never fabricated
        logger.debug("expected_generation_unavailable", exc_info=True)
        return None, None


def live_process_layer() -> dict[str, object]:
    """Currentness + frozen fingerprint of the connected process (FR09).

    Absent frozen fingerprint or unresolvable expected generation yields
    ``currentness="unknown"`` — never green (NFR07). A frozen digest that no
    longer matches the current deployed generation yields ``"stale"``.
    """
    frozen = get_frozen_fingerprint()
    expected_registry_digest, expected_source_digests = _expected_generation()
    currentness = compare_generation(
        frozen,
        expected_registry_digest=expected_registry_digest,
        expected_source_digests=expected_source_digests,
    )
    return {
        "currentness": currentness.value,
        "present": frozen is not None,
        "digest": frozen.digest if frozen is not None else None,
        "framework_version": frozen.framework_version if frozen is not None else None,
        "aaref_version": frozen.aaref_version if frozen is not None else None,
        "template_version": frozen.template_version if frozen is not None else None,
        "registry_digest": frozen.registry_digest if frozen is not None else None,
        "expected_registry_digest": expected_registry_digest,
        "attest_note": FRESH_CLI_ATTEST_NOTE,
    }


def historical_installer_layer(root: Path) -> dict[str, object]:
    """Installer-meta snapshot, labeled historical and never a current authority (FR09/D-26).

    Reads the v2 install-time fields when present and falls back to the legacy
    ``framework_version``/``package_version`` fields as history only (NFR06). The
    returned block carries ``record_kind="historical_install_snapshot"`` and is
    never placed in a current ``must_match`` pair.
    """
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.persistence import FileStateReader

    path = root / ".trw" / "installer-meta.yaml"
    if not path.exists():
        return {"present": False, "record_kind": "historical_install_snapshot"}
    try:
        data: dict[str, object] = FileStateReader(base_dir=root).read_yaml(path)
    except (StateError, ValueError, TypeError, OSError):
        logger.debug("installer_meta_unreadable", path=str(path), exc_info=True)
        return {
            "present": True,
            "record_kind": "historical_install_snapshot",
            "error": "installer-meta.yaml unreadable",
        }

    def _hist(v2_key: str, legacy_key: str) -> object:
        return data.get(v2_key) if data.get(v2_key) is not None else data.get(legacy_key)

    return {
        "present": True,
        "record_kind": str(data.get("record_kind") or "historical_install_snapshot"),
        "framework_version_at_install": _hist("framework_version_at_install", "framework_version"),
        "aaref_version_at_install": _hist("aaref_version_at_install", "aaref_version"),
        "trw_mcp_version_at_install": _hist("trw_mcp_version_at_install", "package_version"),
        "recorded_at": _hist("recorded_at", "last_updated"),
        "note": HISTORICAL_INSTALLER_NOTE,
    }


__all__ = [
    "FRESH_CLI_ATTEST_NOTE",
    "HISTORICAL_INSTALLER_NOTE",
    "historical_installer_layer",
    "live_process_layer",
]
