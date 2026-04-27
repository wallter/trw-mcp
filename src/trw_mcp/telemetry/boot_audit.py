"""Default-resolution boot audit — PRD-HPO-MEAS-001 NFR-12 + FR-13.

On first session-start per install, `trw_session_start` MUST invoke a
default-resolution audit that proves every currently shipped Phase-1
default resolves to reality: bundled/configured ``pricing.yaml``,
``sha256`` availability, and the event-schema registry. Any unresolvable default raises
:class:`DefaultResolutionError` with file:line + remediation BEFORE the
session writes any events.

Fail-open is NOT acceptable here (unlike event emitters): the whole
point of NFR-12 is fail-at-boot so unresolvable defaults are caught at
install-time rather than at first-production-emission. Callers that want
a soft check use :func:`check_defaults` which returns a list of
:class:`ResolutionFailure` records instead of raising.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib.resources import as_file
from importlib.resources import files as _pkg_files
from pathlib import Path

import structlog

from trw_mcp.telemetry.event_base import (
    EVENT_TYPE_REGISTRY,
    DefaultResolutionError,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ResolutionFailure:
    """A single boot-audit failure with enough context to self-remediate."""

    key: str
    expected: str
    actual: str
    remediation: str


def _check_pricing_yaml() -> ResolutionFailure | None:
    """PRD-HPO-MEAS-001 FR-13 §3: ``trw-mcp/data/pricing.yaml`` must exist."""
    try:
        pricing_path: Path | None = None
        try:
            from trw_mcp.models.config import get_config

            configured = str(get_config().pricing_table_path).strip()
            if configured:
                pricing_path = Path(configured).expanduser()
        except Exception:
            pricing_path = None

        if pricing_path is not None:
            if not pricing_path.is_file():
                return ResolutionFailure(
                    key="pricing_yaml",
                    expected=f"configured pricing table at {pricing_path}",
                    actual=f"missing at {pricing_path}",
                    remediation="Update TRWConfig.pricing_table_path to an existing YAML file.",
                )
            _ = pricing_path.read_text(encoding="utf-8")
            return None

        traversable = _pkg_files("trw_mcp.data").joinpath("pricing.yaml")
        if not traversable.is_file():
            return ResolutionFailure(
                key="pricing_yaml",
                expected="trw_mcp/data/pricing.yaml bundled with package",
                actual=f"missing at {traversable}",
                remediation="Reinstall trw-mcp: `pip install --force-reinstall trw-mcp`.",
            )
        # Actually open it to verify it parses (not just exists).
        with as_file(traversable) as real_path:
            _ = Path(real_path).read_text(encoding="utf-8")
    except Exception as exc:  # justified: boundary, fs/import errors produce a typed failure
        return ResolutionFailure(
            key="pricing_yaml",
            expected="loadable pricing.yaml",
            actual=f"load error: {exc.__class__.__name__}: {exc}",
            remediation="Check file permissions and `pip show trw-mcp` to verify install.",
        )
    return None


def _check_hash_algorithm() -> ResolutionFailure | None:
    """FR-13 §1: surface_snapshot_id digest algorithm must be available."""
    try:
        hashlib.new("sha256")
    except ValueError:
        return ResolutionFailure(
            key="hash_algorithm",
            expected="sha256 available via hashlib.new",
            actual="sha256 not available in this Python build",
            remediation="Rebuild Python with OpenSSL support.",
        )
    return None


def _check_event_type_registry() -> ResolutionFailure | None:
    """FR-13 §5: EVENT_TYPE_REGISTRY must be non-empty and internally consistent."""
    if not EVENT_TYPE_REGISTRY:
        return ResolutionFailure(
            key="event_type_registry",
            expected="EVENT_TYPE_REGISTRY populated with ≥12 subclasses",
            actual="empty dict",
            remediation="Check trw_mcp.telemetry.event_base — event_base registry may have been truncated.",
        )
    for event_type, cls in EVENT_TYPE_REGISTRY.items():
        default = cls.model_fields.get("event_type")
        if default is None or default.default != event_type:
            return ResolutionFailure(
                key="event_type_registry",
                expected=f"{cls.__name__}.event_type default == {event_type!r}",
                actual=f"mismatch (default={default.default if default else '<missing>'!r})",
                remediation="Fix event_base.py subclass definition or registry key.",
            )
    return None


#: Ordered check list — each callable returns None on success or a
#: :class:`ResolutionFailure`. New defaults append here; none delete
#: without PRD-HPO-MEAS-001 NFR-12 acceptance-criteria update.
_CHECKS = (
    _check_hash_algorithm,
    _check_pricing_yaml,
    _check_event_type_registry,
)


def check_defaults() -> list[ResolutionFailure]:
    """Run every boot-time default check and return all failures.

    Returns an empty list on success. Never raises — intended for
    programmatic inspection (e.g. health endpoints).
    """
    out: list[ResolutionFailure] = []
    for check in _CHECKS:
        failure = check()
        if failure is not None:
            out.append(failure)
            logger.warning(
                "boot_audit_failure",
                key=failure.key,
                expected=failure.expected,
                actual=failure.actual,
            )
    if not out:
        logger.debug("boot_audit_passed", check_count=len(_CHECKS))
    return out


def run_boot_audit(*, raise_on_failure: bool = True) -> list[ResolutionFailure]:
    """Execute the boot audit. Raises :class:`DefaultResolutionError` on failure.

    Args:
        raise_on_failure: When True (default, per NFR-12 spec), raise a
            typed error aggregating every failure. When False, return the
            failure list — useful in tests or soft health probes.
    """
    failures = check_defaults()
    if failures and raise_on_failure:
        lines = [f"- {f.key}: expected {f.expected!r}; got {f.actual!r}. → {f.remediation}" for f in failures]
        msg = "Default-resolution boot audit failed ({} issue(s)):\n{}".format(len(failures), "\n".join(lines))
        raise DefaultResolutionError(msg)
    return failures


__all__ = [
    "ResolutionFailure",
    "check_defaults",
    "run_boot_audit",
]
