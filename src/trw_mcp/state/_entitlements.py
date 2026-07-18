"""TRW tier entitlement check (PRD-DIST-1982, cycle 746 P1).

Reads ``.trw/entitlements.yaml`` (in the project ``.trw/`` directory)
and gates tier-restricted trw-mcp features (e.g.
``trw_before_edit_hint`` consumption of trw-distill sidecars).

v0 design (intentionally minimal):

- File at ``<trw_dir>/entitlements.yaml`` declares ``tier`` +
  ``signature`` + ``expires_at`` + ``issued_to``.
- Signature is HMAC-SHA256 of a canonical-stringified payload using
  a SHARED KEY. v0 ships a hard-coded dev key
  (``TRW_ENTITLEMENT_DEV_KEY``); real key distribution / signing
  service deferred to v1 (cross-package; not in c746 scope).
- Missing or invalid file = ``tier="free"`` (no gated features).
  This is intentional fail-open per CONSTITUTION truthfulness rule:
  the tool MUST work at free tier (returning learnings without
  trw-distill sidecar) rather than refusing to operate.
- Feature map is hard-coded for v0:
    free   → []
    team   → ["trw_before_edit_hint:distill_sidecar"]
    pro    → ["trw_before_edit_hint:distill_sidecar"]
    enterprise → ["trw_before_edit_hint:distill_sidecar"]
    beta   → ["trw_before_edit_hint:distill_sidecar"]

The ``beta`` tier is the tester-program bridge (production feedback
``sub_Y-f6QQ3Y_Os9b0vM``): the backend tester program grants beta/tester
access (``org.plan`` + a ``proprietary:install`` license) but that state
had NO representation here, so testers resolved ``tier="free"`` and were
shown a paid-tier remediation. ``beta`` unlocks the same distill-sidecar
feature as the paid tiers and is provisioned locally via
``trw-mcp tier issue --tier beta``. The backend's tester program plan name
is ``"alpha"`` (``backend/services/tester_program.py`` ``TESTER_PLAN``), so
``"alpha"`` is accepted as an ALIAS for ``beta`` at load/validation time —
one concept, one feature row (no separate ``alpha`` feature set).

Honest scope per CONSTITUTION §1:
- v0 entitlement is sentinel-only — anyone can copy a valid
  signature locally. Real entitlement enforcement requires a server-
  side rotation/revocation surface that lives outside this cycle.
- HMAC verification + expiry check are real, but the dev key is
  hard-coded for development. Production deployments must override
  via ``TRW_ENTITLEMENT_KEY`` env var.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from ruamel.yaml import YAML

# v0 dev key — operators in production should override via env.
_DEV_HMAC_KEY = b"trw-v0-dev-key-2026-05-17-replace-in-prod"

Tier = Literal["free", "team", "pro", "enterprise", "beta"]
_VALID_TIERS: tuple[Tier, ...] = ("free", "team", "pro", "enterprise", "beta")

# Backend tester-program plan name → canonical TRW tier. The tester program
# (backend/services/tester_program.py TESTER_PLAN="alpha") provisions "alpha";
# we alias it to "beta" so a backend-issued entitlement unlocks the same
# feature set without a duplicate feature row (one concept, one source of
# truth). Aliases are resolved AFTER signature verification (the signed
# payload carries the raw "alpha" value).
_TIER_ALIASES: dict[str, Tier] = {"alpha": "beta"}

# Raw tier strings accepted from an entitlements file (canonical tiers + aliases).
_ACCEPTED_RAW_TIERS: frozenset[str] = frozenset(_VALID_TIERS) | frozenset(_TIER_ALIASES)

# Tier → enabled feature flags. Additive: a tier always inherits prior tiers.
_TIER_FEATURES: dict[Tier, frozenset[str]] = {
    "free": frozenset(),
    "team": frozenset({"trw_before_edit_hint:distill_sidecar"}),
    "pro": frozenset({"trw_before_edit_hint:distill_sidecar"}),
    "enterprise": frozenset({"trw_before_edit_hint:distill_sidecar"}),
    # Tester-program bridge — same feature as paid tiers (sub_Y-f6QQ3Y_Os9b0vM).
    "beta": frozenset({"trw_before_edit_hint:distill_sidecar"}),
}


@dataclass(frozen=True)
class Entitlement:
    """Resolved entitlement state.

    Fields are deliberately minimal — v0 ships tier + reason. Future
    versions can add: issuer, audience, refresh-url, etc.
    """

    tier: Tier
    reason: Literal[
        "ok",
        "missing",
        "malformed",
        "bad_signature",
        "expired",
        "invalid_tier",
    ]
    expires_at_iso: str | None = None
    signed_payload_keys: tuple[str, ...] = ()

    def has_feature(self, feature: str) -> bool:
        return feature in _TIER_FEATURES[self.tier]


def _get_hmac_key() -> bytes:
    env_key = os.environ.get("TRW_ENTITLEMENT_KEY")
    if env_key:
        return env_key.encode("utf-8")
    return _DEV_HMAC_KEY


def _canonical_payload(tier: str, issued_to: str, expires_at: str) -> bytes:
    """Deterministic byte representation for HMAC signing/verification.

    Field order is FIXED: tier|issued_to|expires_at. Adding new signed
    fields requires a new schema version (deferred to v1).
    """
    return f"tier={tier}|issued_to={issued_to}|expires_at={expires_at}".encode()


def _verify_signature(tier: str, issued_to: str, expires_at: str, signature_hex: str) -> bool:
    payload = _canonical_payload(tier, issued_to, expires_at)
    expected = hmac.new(_get_hmac_key(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_hex)


def _parse_expiry(expires_at: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def load_entitlement(
    trw_dir: Path,
    *,
    now: datetime | None = None,
) -> Entitlement:
    """Resolve current tier from ``<trw_dir>/entitlements.yaml``.

    NEVER raises — every failure path returns an Entitlement with
    ``tier='free'`` and a structured ``reason``. Callers branch on the
    reason to decide whether to surface an actionable hint to the
    operator.

    Args:
        trw_dir: The project's ``.trw/`` directory.
        now: Optional datetime for deterministic expiry tests.

    Returns:
        Entitlement.
    """
    path = trw_dir / "entitlements.yaml"
    if not path.exists():
        return Entitlement(tier="free", reason="missing")

    try:
        yaml = YAML(typ="safe")
        parsed = yaml.load(path.read_text(encoding="utf-8"))
    except Exception:
        return Entitlement(tier="free", reason="malformed")
    if not isinstance(parsed, dict):
        return Entitlement(tier="free", reason="malformed")

    tier = parsed.get("tier")
    issued_to = parsed.get("issued_to", "")
    expires_at = parsed.get("expires_at", "")
    signature = parsed.get("signature", "")
    if (
        not isinstance(tier, str)
        or tier not in _ACCEPTED_RAW_TIERS
        or not isinstance(issued_to, str)
        or not isinstance(expires_at, str)
        or not isinstance(signature, str)
    ):
        return Entitlement(tier="free", reason="malformed")

    # Signature first — refuse to honor any value from an unsigned payload.
    if not _verify_signature(tier, issued_to, expires_at, signature):
        return Entitlement(tier="free", reason="bad_signature")

    expiry = _parse_expiry(expires_at)
    if expiry is None:
        return Entitlement(tier="free", reason="malformed")

    resolved_now = now or datetime.now(tz=timezone.utc)
    if resolved_now > expiry:
        return Entitlement(
            tier="free",
            reason="expired",
            expires_at_iso=expires_at,
        )

    # All checks passed. Resolve any alias (alpha→beta) to the canonical
    # tier now that the RAW signed value has been verified. `tier` is
    # guaranteed in _ACCEPTED_RAW_TIERS, so the alias-resolved result is a
    # valid Tier.
    valid_tier: Tier = cast("Tier", _TIER_ALIASES.get(tier, tier))
    return Entitlement(
        tier=valid_tier,
        reason="ok",
        expires_at_iso=expires_at,
        signed_payload_keys=("tier", "issued_to", "expires_at"),
    )


def sign_entitlement_for_dev(
    *,
    tier: Tier,
    issued_to: str,
    expires_at: str,
) -> str:
    """Generate an HMAC signature for a dev/test entitlement.

    USE ONLY for tests and local dev — production entitlements must be
    signed by an authorized service (deferred to v1).
    """
    payload = _canonical_payload(tier, issued_to, expires_at)
    return hmac.new(_get_hmac_key(), payload, hashlib.sha256).hexdigest()


__all__ = [
    "Entitlement",
    "Tier",
    "load_entitlement",
    "sign_entitlement_for_dev",
]
