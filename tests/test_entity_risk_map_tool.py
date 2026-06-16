"""Tests for trw_entity_risk_map MCP consumer (PRD-CORE-167)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from trw_mcp.state._entitlements import Tier, sign_entitlement_for_dev
from trw_mcp.tools._sidecar_substrate import SCHEMA_VERSION_ACCEPTED
from trw_mcp.tools.entity_risk_map import (
    EntityRiskMapResult,
    EntityRiskScorePayload,
    compute_entity_risk_map,
)


def _make_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "foo.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path).decode().strip()


def _write_entitlement(trw_dir: Path, tier: Tier) -> None:
    trw_dir.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(tier=tier, issued_to="t@t", expires_at=future)
    (trw_dir / "entitlements.yaml").write_text(
        f"tier: {tier}\nissued_to: t@t\nexpires_at: '{future}'\nsignature: {sig}\n",
        encoding="utf-8",
    )


def _write_envelope(
    path: Path,
    sha: str,
    payload: object,
    *,
    schema_version: str = SCHEMA_VERSION_ACCEPTED,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "schema_version": schema_version,
        "sha": sha,
        "generated_at_unix": 1714000000.0,
        "payload": payload,
    }
    path.write_text(json.dumps(envelope), encoding="utf-8")


def _entity(
    qualified_name: str,
    *,
    risk_score: float,
    blast_radius_count: int,
    changed: bool,
    file_path: str = "foo.py",
) -> dict[str, object]:
    return {
        "qualified_name": qualified_name,
        "entity_kind": "function",
        "file_path": file_path,
        "start_line": 1,
        "end_line": 3,
        "exposure": "public",
        "risk_score": risk_score,
        "blast_radius_count": blast_radius_count,
        "changed": changed,
        "reasons": ["public API"],
        "dependency_path_samples": [["api.entry", qualified_name]],
    }


class TestEntityRiskMapTool:
    def test_free_tier_blocked_returns_structured_status(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)

        result = compute_entity_risk_map(repo_root=str(tmp_path))

        assert result.tier == "free"
        assert result.distill_status == "tier_required"
        assert result.entity_count == 0
        assert "tier" in (result.distill_action or "").lower()

    def test_missing_sidecar_returns_actionable_status(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")

        result = compute_entity_risk_map(repo_root=str(tmp_path))

        assert result.tier == "pro"
        assert result.distill_status == "sidecar_missing"
        assert "entity-risk-map" in (result.distill_action or "")
        assert result.entity_risk == []

    def test_valid_sidecar_returns_deterministic_entity_rows(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"entity-risk-map-{sha}.json",
            sha,
            [
                _entity("pkg.low", risk_score=0.1, blast_radius_count=99, changed=True),
                _entity("pkg.beta", risk_score=0.9, blast_radius_count=3, changed=True),
                _entity("pkg.alpha", risk_score=0.9, blast_radius_count=3, changed=True),
                _entity("pkg.not_changed", risk_score=1.0, blast_radius_count=100, changed=False),
            ],
        )

        result = compute_entity_risk_map(repo_root=str(tmp_path), changed_only=True, top_n=2)

        assert result.distill_status == "hint_available"
        assert result.distill_sidecar_sha == sha
        assert result.entity_count == 4
        assert [row.qualified_name for row in result.entity_risk] == ["pkg.alpha", "pkg.beta"]
        assert result.entity_risk[0].file_path == "foo.py"

    def test_top_n_zero_returns_all_matching_rows(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"entity-risk-map-{sha}.json",
            sha,
            [
                _entity("pkg.b", risk_score=0.4, blast_radius_count=2, changed=True),
                _entity("pkg.a", risk_score=0.4, blast_radius_count=4, changed=True),
            ],
        )

        result = compute_entity_risk_map(repo_root=str(tmp_path), top_n=0)

        assert [row.qualified_name for row in result.entity_risk] == ["pkg.a", "pkg.b"]

    def test_stale_schema_and_malformed_cases_return_statuses(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        sidecar_path = cache_dir / f"entity-risk-map-{sha}.json"

        _write_envelope(sidecar_path, "0" * 40, [])
        assert compute_entity_risk_map(repo_root=str(tmp_path)).distill_status == "stale_sha"

        _write_envelope(sidecar_path, sha, [], schema_version="risk-report-sidecar/v99")
        assert compute_entity_risk_map(repo_root=str(tmp_path)).distill_status == "schema_mismatch"

        sidecar_path.write_text("{ not json", encoding="utf-8")
        assert compute_entity_risk_map(repo_root=str(tmp_path)).distill_status == "sidecar_malformed"

        _write_envelope(sidecar_path, sha, [{"unexpected": "boom"}])
        assert compute_entity_risk_map(repo_root=str(tmp_path)).distill_status == "sidecar_malformed"


class TestEntityRiskMapModelContracts:
    def test_payload_is_strict_bounded_and_forbids_source_bodies(self) -> None:
        with pytest.raises(Exception):
            EntityRiskScorePayload.model_validate(
                {
                    **_entity("pkg.bad", risk_score=1.1, blast_radius_count=1, changed=True),
                    "source_body": "def bad():\n    pass\n",
                },
            )

    def test_result_is_frozen(self) -> None:
        result = EntityRiskMapResult(tier="free")
        with pytest.raises(Exception):
            result.tier = "pro"  # type: ignore[misc]

    def test_consumer_has_no_producer_import_boundary_violation(self) -> None:
        source = Path("src/trw_mcp/tools/entity_risk_map.py").read_text(encoding="utf-8")
        assert "trw_distill" not in source
        assert "source_body" not in EntityRiskScorePayload.model_fields

    def test_register_dump_shape_is_privacy_safe(self) -> None:
        row = EntityRiskScorePayload.model_validate(
            _entity("pkg.safe", risk_score=0.5, blast_radius_count=1, changed=True)
        )
        dumped = cast("dict[str, object]", row.model_dump())
        assert dumped["qualified_name"] == "pkg.safe"
        assert "source" not in dumped
        assert "body" not in dumped
