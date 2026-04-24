"""Boot-time default-resolution audit for the meta-tune safety pipeline.

PRD-HPO-SAFE-001 FR-15 + NFR-10. Validates, at server startup, that the
critical defaults the meta-tune subsystem relies on are resolvable to a
real resource: the bundled ``pricing.yaml``, a supported compression
algorithm, and a supported hash algorithm. Failures raise
:class:`DefaultResolutionError` (from ``trw_mcp.telemetry.event_base``)
with a ``file:line + remediation`` message — never at first-use.

Must complete in ≤2s (NFR-10).
"""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.telemetry.event_base import DefaultResolutionError

logger = structlog.get_logger(__name__)


# Module-level constants so tests can monkeypatch them.
_REQUIRED_HASH_ALGO: str = "sha256"
#: Required compression algorithm. Default ``gzip`` is stdlib-always-available;
#: ``zstd`` is a preferred upgrade but is gated on the optional ``zstandard``
#: package — setting this to ``"zstd"`` requires ``pip install zstandard``.
#: Auditor WARN-5 (2026-04-23) flagged that the prior always-true membership
#: check hid the missing-dependency failure mode.
_REQUIRED_COMPRESSION: str = "gzip"
_SUPPORTED_COMPRESSION: frozenset[str] = frozenset({"zstd", "gzip", "lz4", "none"})


def _resolve_pricing_yaml() -> Path | None:
    """Resolve the bundled ``pricing.yaml``.

    Returns the concrete path when resolvable, otherwise ``None`` so the
    caller can raise :class:`DefaultResolutionError` with context.
    """
    try:
        ref = resources.files("trw_mcp.data").joinpath("pricing.yaml")
    except (ModuleNotFoundError, FileNotFoundError):  # justified: packaging_boundary
        return None
    try:
        # Only a real on-disk path is usable for our boot audit.
        with resources.as_file(ref) as concrete:
            p = Path(concrete)
            if p.exists() and p.is_file():
                return p
    except (FileNotFoundError, OSError):  # justified: io_boundary
        return None
    return None


def _hash_algo_available(name: str) -> bool:
    try:
        hashlib.new(name)
    except (ValueError, TypeError):  # justified: hashlib_validation
        return False
    return True


def _compression_supported(name: str) -> bool:
    """Verify the compression algorithm is BOTH declared + runtime-importable.

    Previously this function only checked static membership, which made it a
    no-op that never caught a real missing dependency (auditor WARN-5).
    Now it additionally attempts an import so missing optional packages
    (zstd/lz4) surface at boot rather than at first-compress call.
    """
    if name not in _SUPPORTED_COMPRESSION:
        return False
    # gzip + "none" are always available (stdlib + trivial). zstd / lz4
    # are optional C-extension packages and must be runtime-checked.
    if name in {"gzip", "none"}:
        return True
    import importlib

    for candidate in (name, f"{name}andard") if name == "zstd" else (name,):
        try:
            importlib.import_module(candidate)
            return True
        except ImportError:
            continue
    return False


def audit_defaults() -> dict[str, Any]:
    """Return a report dict describing the current default-resolution state."""
    pricing_path = _resolve_pricing_yaml()
    return {
        "pricing_yaml": {
            "resolved": pricing_path is not None,
            "path": str(pricing_path) if pricing_path else None,
        },
        "compression_algorithm": {
            "name": _REQUIRED_COMPRESSION,
            "supported": _compression_supported(_REQUIRED_COMPRESSION),
        },
        "hash_algorithm": {
            "name": _REQUIRED_HASH_ALGO,
            "available": _hash_algo_available(_REQUIRED_HASH_ALGO),
        },
    }


def validate_defaults() -> None:
    """Raise :class:`DefaultResolutionError` if any default is unresolvable."""
    pricing_path = _resolve_pricing_yaml()
    if pricing_path is None:
        msg = (
            "boot_checks.py:validate_defaults — bundled pricing.yaml cannot "
            "be resolved. Remediation: reinstall trw-mcp, or set "
            "TRW_PRICING_YAML to an explicit path."
        )
        logger.error(
            "default_resolution_failed",
            component="meta_tune.boot_checks",
            op="validate_defaults",
            outcome="error",
            which="pricing_yaml",
        )
        raise DefaultResolutionError(msg)
    if not _hash_algo_available(_REQUIRED_HASH_ALGO):
        msg = (
            f"boot_checks.py:validate_defaults — hash algorithm "
            f"{_REQUIRED_HASH_ALGO!r} not available from hashlib. "
            f"Remediation: upgrade Python or reinstall OpenSSL backend."
        )
        logger.error(
            "default_resolution_failed",
            component="meta_tune.boot_checks",
            op="validate_defaults",
            outcome="error",
            which="hash_algorithm",
        )
        raise DefaultResolutionError(msg)
    if not _compression_supported(_REQUIRED_COMPRESSION):
        msg = (
            f"boot_checks.py:validate_defaults — compression algorithm "
            f"{_REQUIRED_COMPRESSION!r} not in supported set "
            f"{sorted(_SUPPORTED_COMPRESSION)}. Remediation: set "
            f"config.meta_tune.compression to a supported value."
        )
        logger.error(
            "default_resolution_failed",
            component="meta_tune.boot_checks",
            op="validate_defaults",
            outcome="error",
            which="compression_algorithm",
        )
        raise DefaultResolutionError(msg)
    logger.info(
        "default_resolution_ok",
        component="meta_tune.boot_checks",
        op="validate_defaults",
        outcome="ok",
        pricing_yaml=str(pricing_path),
    )


__all__ = [
    "audit_defaults",
    "validate_defaults",
]
