"""PRD-QUAL-114 method-neutral mapping and cache regressions."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationResultV2, VerificationMapping
from trw_mcp.state.validation._prd_scoring_readiness import score_implementation_readiness
from trw_mcp.tools._prd_validation_cache import CacheBounds, load_pure_result, store_pure_result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("requirement_id", "   "),
        ("evidence_artifact", "\t"),
        ("pass_condition", "\n"),
        ("acceptance_criteria", ["valid", "  "]),
    ],
)
def test_verification_mapping_rejects_whitespace_only_fields(field: str, value: object) -> None:
    payload: dict[str, object] = {
        "requirement_id": "PRD-QUAL-114-FR01",
        "acceptance_criteria": ["observable outcome"],
        "method": "inspection",
        "evidence_artifact": "artifact.json",
        "pass_condition": "field is present",
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        VerificationMapping.model_validate(payload, strict=False)


def test_verification_mapping_normalizes_text() -> None:
    mapping = VerificationMapping.model_validate(
        {
            "requirement_id": "  PRD-QUAL-114-FR01  ",
            "acceptance_criteria": ["  observable outcome  "],
            "method": "analysis",
            "evidence_artifact": "  artifacts/result.json  ",
            "pass_condition": "  p95 <= 20 ms  ",
            "automation_infeasible_reason": "  Human inspection required  ",
        },
        strict=False,
    )
    assert mapping.requirement_id == "PRD-QUAL-114-FR01"
    assert mapping.acceptance_criteria == ["observable outcome"]
    assert mapping.evidence_artifact == "artifacts/result.json"
    assert mapping.automation_infeasible_reason == "Human inspection required"


def test_verification_mapping_rejects_blank_optional_reason_when_supplied() -> None:
    with pytest.raises(ValidationError):
        VerificationMapping.model_validate(
            {
                "requirement_id": "PRD-QUAL-114-FR01",
                "acceptance_criteria": ["observable outcome"],
                "method": "inspection",
                "evidence_artifact": "artifact.json",
                "pass_condition": "field is present",
                "automation_infeasible_reason": "   ",
            },
            strict=False,
        )


def _result(score: float) -> ValidationResultV2:
    return ValidationResultV2(total_score=score)


def test_corrupt_cache_entry_is_isolated_miss(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    key = "a" * 64
    entry = root / "aa" / f"{key}.json"
    entry.parent.mkdir(parents=True)
    entry.write_text("{truncated", encoding="utf-8")
    assert load_pure_result(root, key) is None

    other = "b" * 64
    store_pure_result(root, other, _result(42.0))
    loaded = load_pure_result(root, other)
    assert loaded is not None and loaded.total_score == 42.0


def test_schema_valid_cache_result_mutation_is_a_miss(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    key = "c" * 64
    store_pure_result(root, key, _result(42.0))
    entry = root / "cc" / f"{key}.json"
    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["pure_result"]["valid"] = True
    payload["pure_result"]["total_score"] = 99.0
    entry.write_text(json.dumps(payload), encoding="utf-8")
    assert load_pure_result(root, key) is None


def test_cache_root_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "cache"
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="must not be a symlink"):
        store_pure_result(root, "d" * 64, _result(42.0))
    assert not list(outside.rglob("*.json"))


def test_traceability_compatibility_alias_cannot_diverge() -> None:
    result = ValidationResultV2(measured_traceability_coverage=0.5)
    assert result.implementation_test_link_coverage == 0.5
    with pytest.raises(ValidationError):
        ValidationResultV2(
            measured_traceability_coverage=0.5,
            implementation_test_link_coverage=0.75,
        )


def test_concurrent_distinct_cache_writers_preserve_entries(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    keys = [f"{value:064x}" for value in range(1, 25)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda pair: store_pure_result(root, pair[0], _result(pair[1])), zip(keys, range(1, 25))))
    assert [load_pure_result(root, key).total_score for key in keys if load_pure_result(root, key)] == list(
        range(1, 25)
    )


def test_cache_bounds_preserve_new_entry(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    keys = [f"{value:064x}" for value in range(1, 6)]
    # WHY: Track B (commit 65e520fa5) replaced the store_pure_result
    # max_entries/max_total_bytes kwargs with a single bounds=CacheBounds param,
    # and moved eviction onto a write-count cadence (DEFAULT_MAINTENANCE_INTERVAL
    # =32). maintenance_interval=1 drives the sweep on every write so the eviction
    # assertion below is exercised deterministically; the assertion is unchanged.
    bounds = CacheBounds(max_entries=3, max_total_bytes=10_000_000, maintenance_interval=1)
    for value, key in enumerate(keys, start=1):
        store_pure_result(root, key, _result(value), bounds=bounds)
    assert load_pure_result(root, keys[-1]) is not None
    assert len(list(root.glob("*/*.json"))) <= 3


def _method_prd(method: str) -> tuple[dict[str, object], str]:
    frontmatter: dict[str, object] = {
        "category": "CORE",
        "verification": {
            "mappings": [
                {
                    "requirement_id": "PRD-CORE-999-FR01",
                    "acceptance_criteria": ["Given input, When processed, Then output is stable"],
                    "method": method,
                    "evidence_artifact": "artifacts/evidence.json",
                    "pass_condition": "The declared observable equals the target",
                    "automated": method == "test",
                    "automation_infeasible_reason": None
                    if method == "test"
                    else "Human or quantitative method required",
                }
            ]
        },
    }
    content = """
### PRD-CORE-999-FR01: Stable output
**Implementation**: `trw-mcp/src/trw_mcp/models/requirements.py`
### Primary Control Points
| Surface | Change |
|---|---|
| Model | normalize |
### Behavior Switch Matrix
| Requirement | Old | New |
|---|---|---|
| FR01 | hollow | normalized |
### Key Files
| File | Change |
|---|---|
| `trw-mcp/src/trw_mcp/models/requirements.py` | normalize |
### Completion Evidence (Definition of Done)
Mapped behavior is verified.
### Migration / Backward Compatibility
Wire shape remains compatible.
"""
    return frontmatter, content


@pytest.mark.parametrize("method", ["test", "analysis", "inspection", "demonstration"])
def test_readiness_base_score_is_verification_method_neutral(method: str) -> None:
    frontmatter, content = _method_prd(method)
    score = score_implementation_readiness(frontmatter, content, TRWConfig()).score
    baseline_frontmatter, baseline_content = _method_prd("inspection")
    baseline = score_implementation_readiness(baseline_frontmatter, baseline_content, TRWConfig()).score
    assert score == baseline
