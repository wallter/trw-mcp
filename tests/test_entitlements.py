"""Tests for tier entitlement check (PRD-DIST-1982, cycle 746)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from trw_mcp.state._entitlements import (
    Entitlement,
    load_entitlement,
    sign_entitlement_for_dev,
)


def _write_entitlement(
    trw_dir: Path,
    *,
    tier: str,
    issued_to: str = "test@trw",
    expires_at: str | None = None,
    signature: str | None = None,
) -> None:
    trw_dir.mkdir(parents=True, exist_ok=True)
    if expires_at is None:
        expires_at = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    if signature is None:
        signature = sign_entitlement_for_dev(
            tier=tier,  # type: ignore[arg-type]
            issued_to=issued_to,
            expires_at=expires_at,
        )
    (trw_dir / "entitlements.yaml").write_text(
        f"tier: {tier}\n"
        f"issued_to: {issued_to}\n"
        f"expires_at: '{expires_at}'\n"
        f"signature: {signature}\n",
        encoding="utf-8",
    )


class TestMissingFile:
    def test_returns_free_tier(self, tmp_path: Path) -> None:
        e = load_entitlement(tmp_path)
        assert e.tier == "free"
        assert e.reason == "missing"

    def test_free_has_no_distill_feature(self, tmp_path: Path) -> None:
        e = load_entitlement(tmp_path)
        assert e.has_feature("trw_before_edit_hint:distill_sidecar") is False


class TestValidEntitlement:
    def test_team_tier_grants_distill(self, tmp_path: Path) -> None:
        _write_entitlement(tmp_path, tier="team")
        e = load_entitlement(tmp_path)
        assert e.tier == "team"
        assert e.reason == "ok"
        assert e.has_feature("trw_before_edit_hint:distill_sidecar")

    def test_pro_tier(self, tmp_path: Path) -> None:
        _write_entitlement(tmp_path, tier="pro")
        e = load_entitlement(tmp_path)
        assert e.tier == "pro"
        assert e.reason == "ok"

    def test_enterprise_tier(self, tmp_path: Path) -> None:
        _write_entitlement(tmp_path, tier="enterprise")
        e = load_entitlement(tmp_path)
        assert e.tier == "enterprise"


class TestBadSignature:
    def test_tampered_tier_caught(self, tmp_path: Path) -> None:
        # Sign as "free" but write tier as "enterprise"
        future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
        free_sig = sign_entitlement_for_dev(
            tier="free", issued_to="x@x", expires_at=future,
        )
        _write_entitlement(
            tmp_path, tier="enterprise", expires_at=future, signature=free_sig,
        )
        e = load_entitlement(tmp_path)
        assert e.tier == "free"
        assert e.reason == "bad_signature"

    def test_random_signature(self, tmp_path: Path) -> None:
        _write_entitlement(tmp_path, tier="pro", signature="deadbeef" * 8)
        e = load_entitlement(tmp_path)
        assert e.tier == "free"
        assert e.reason == "bad_signature"


class TestExpiry:
    def test_expired_caught(self, tmp_path: Path) -> None:
        past = (datetime.now(tz=timezone.utc) - timedelta(days=1)).isoformat()
        _write_entitlement(tmp_path, tier="enterprise", expires_at=past)
        e = load_entitlement(tmp_path)
        assert e.tier == "free"
        assert e.reason == "expired"
        assert e.expires_at_iso == past

    def test_deterministic_now(self, tmp_path: Path) -> None:
        future = "2030-01-01T00:00:00+00:00"
        _write_entitlement(tmp_path, tier="pro", expires_at=future)
        # Inject 'now' beyond expires_at
        injected = datetime(2031, 1, 1, tzinfo=timezone.utc)
        e = load_entitlement(tmp_path, now=injected)
        assert e.reason == "expired"


class TestMalformed:
    def test_invalid_tier_value(self, tmp_path: Path) -> None:
        _write_entitlement(tmp_path, tier="god-mode")
        e = load_entitlement(tmp_path)
        assert e.tier == "free"
        assert e.reason == "malformed"

    def test_not_a_dict(self, tmp_path: Path) -> None:
        (tmp_path / "entitlements.yaml").write_text("- a\n- b\n")
        e = load_entitlement(tmp_path)
        assert e.reason == "malformed"

    def test_unparseable_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "entitlements.yaml").write_text(":\n - [broken\n")
        e = load_entitlement(tmp_path)
        assert e.reason == "malformed"

    def test_unparseable_expiry(self, tmp_path: Path) -> None:
        sig = sign_entitlement_for_dev(
            tier="pro", issued_to="x@x", expires_at="not-a-date",
        )
        (tmp_path / "entitlements.yaml").write_text(
            f"tier: pro\nissued_to: x@x\nexpires_at: 'not-a-date'\n"
            f"signature: {sig}\n",
        )
        e = load_entitlement(tmp_path)
        assert e.reason == "malformed"


class TestFeatureMap:
    def test_free_has_no_features(self, tmp_path: Path) -> None:
        e = Entitlement(tier="free", reason="missing")
        assert e.has_feature("trw_before_edit_hint:distill_sidecar") is False

    def test_pro_has_distill_sidecar(self, tmp_path: Path) -> None:
        _write_entitlement(tmp_path, tier="pro")
        e = load_entitlement(tmp_path)
        assert e.has_feature("trw_before_edit_hint:distill_sidecar") is True

    def test_unknown_feature_always_false(self, tmp_path: Path) -> None:
        _write_entitlement(tmp_path, tier="enterprise")
        e = load_entitlement(tmp_path)
        assert e.has_feature("future-unimplemented-feature") is False
