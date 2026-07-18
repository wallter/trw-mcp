"""Shared builders for PRD-CORE-205 receipt tests (not collected as tests)."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.models._evidence_core import ContentBinding, RunOwnedScope
from trw_mcp.models._evidence_plans import (
    BuildCommandResult,
    CommandClass,
    RequiredReviewPlan,
    RequiredValidationPlan,
    ReviewVerdict,
    VerificationOutcome,
)
from trw_mcp.models._evidence_records import (
    BuildReceipt,
    ReviewReceipt,
    VerificationReceipt,
)
from trw_mcp.tools._evidence_binding import build_content_binding, mint_run_owned_scope


def write_journal(run_path: Path, files: list[str]) -> None:
    meta = run_path / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"ts": f"2026-07-10T00:00:0{i}Z", "event": "file_modified", "file": f}) for i, f in enumerate(files)
    ]
    (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def project_with_binding(tmp_path: Path, files: dict[str, str]) -> tuple[Path, ContentBinding, RunOwnedScope]:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    run = project / "run"
    changed: list[str] = []
    for rel, content in files.items():
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        changed.append(str(project / rel))
    write_journal(run, changed)
    scope = mint_run_owned_scope(run, project, scope_id="sc1")
    outcome = build_content_binding(scope, project)
    assert outcome.binding is not None, outcome.reason_code
    return project, outcome.binding, scope


def review_plan(
    binding: ContentBinding,
    *,
    governing_digest: str = "gov1",
    rubric_ids: tuple[str, ...] = ("R1", "R2"),
    roles: tuple[str, ...] = ("independent",),
) -> RequiredReviewPlan:
    return RequiredReviewPlan(
        plan_id="rplan1",
        plan_digest=_rp_digest(binding, governing_digest, rubric_ids, roles),
        scope_id=binding.scope_id,
        scope_digest=binding.scope_digest,
        governing_content_digest=governing_digest,
        required_rubric_ids=rubric_ids,
        required_reviewer_roles=roles,
    )


def _rp_digest(
    binding: ContentBinding, governing_digest: str, rubric_ids: tuple[str, ...], roles: tuple[str, ...]
) -> str:
    from trw_mcp.models._evidence_core import domain_digest

    return domain_digest(
        "review_plan",
        {
            "plan_id": "rplan1",
            "scope_id": binding.scope_id,
            "scope_digest": binding.scope_digest,
            "governing_prd_ids": [],
            "governing_content_digest": governing_digest,
            "required_rubric_ids": sorted(rubric_ids),
            "required_reviewer_roles": sorted(roles),
            "policy_version": "",
        },
    )


def review_receipt(
    binding: ContentBinding,
    plan: RequiredReviewPlan,
    *,
    verdict: ReviewVerdict = ReviewVerdict.PASS,
    realized_rubric_ids: tuple[str, ...] | None = None,
    realized_roles: tuple[str, ...] | None = None,
    degraded_reason: str = "",
) -> ReviewReceipt:
    realized_rubric_ids = plan.required_rubric_ids if realized_rubric_ids is None else realized_rubric_ids
    realized_roles = plan.required_reviewer_roles if realized_roles is None else realized_roles
    receipt = ReviewReceipt(
        receipt_id="review-test",
        review_id="rv1",
        run_id="run1",
        completed_at="2026-07-10T00:00:00Z",
        method="independent_manual",
        reviewer_origin="human",
        reviewer_identity="alice",
        reviewer_family="human",
        reviewer_roles_realized=realized_roles,
        content_binding=binding,
        review_plan_id=plan.plan_id,
        review_plan_digest=plan.plan_digest,
        review_input_digest="",
        realized_rubric_ids=realized_rubric_ids,
        verdict=verdict,
        degraded_reason=degraded_reason,
    )
    return receipt.model_copy(
        update={"review_input_digest": receipt.expected_input_digest(plan.governing_content_digest)}
    )


def validation_plan(
    binding: ContentBinding,
    *,
    required_command_ids: tuple[str, ...] = ("pytest", "mypy"),
    coverage_threshold: float | None = None,
) -> RequiredValidationPlan:
    from trw_mcp.models._evidence_core import domain_digest

    digest = domain_digest(
        "validation_plan",
        {
            "plan_id": "vplan1",
            "scope_id": binding.scope_id,
            "scope_digest": binding.scope_digest,
            "governing_prd_ids": [],
            "governing_content_digest": "",
            "policy_config_digest": "",
            "required_command_ids": sorted(required_command_ids),
            "optional_command_ids": [],
            "coverage_threshold": coverage_threshold,
            "policy_version": "",
        },
    )
    return RequiredValidationPlan(
        plan_id="vplan1",
        plan_digest=digest,
        scope_id=binding.scope_id,
        scope_digest=binding.scope_digest,
        required_command_ids=required_command_ids,
        coverage_threshold=coverage_threshold,
    )


def build_command(command_id: str, exit_code: int = 0, cls: CommandClass = CommandClass.TEST) -> BuildCommandResult:
    return BuildCommandResult(command_id=command_id, label=command_id, command_class=cls, exit_code=exit_code)


def build_receipt(
    binding: ContentBinding,
    plan: RequiredValidationPlan,
    *,
    command_results: tuple[BuildCommandResult, ...] | None = None,
    coverage_pct: float | None = None,
    legacy_tests_passed: bool | None = None,
) -> BuildReceipt:
    if command_results is None:
        command_results = tuple(build_command(cid) for cid in plan.required_command_ids)
    return BuildReceipt(
        receipt_id="build-test",
        run_id="run1",
        completed_at="2026-07-10T00:00:00Z",
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        content_binding=binding,
        command_results=command_results,
        coverage_pct=coverage_pct,
        legacy_tests_passed=legacy_tests_passed,
    )


def verification_receipt(
    binding: ContentBinding,
    *,
    mapping_digest: str = "map1",
    outcome: VerificationOutcome = VerificationOutcome.PASS,
) -> VerificationReceipt:
    return VerificationReceipt(
        receipt_id="verify-test",
        run_id="run1",
        requirement_id="PRD-CORE-205-FR06",
        mapping_digest=mapping_digest,
        method="test",
        completed_at="2026-07-10T00:00:00Z",
        content_binding=binding,
        outcome=outcome,
    )
