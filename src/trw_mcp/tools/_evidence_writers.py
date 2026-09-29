"""Server-plan-bound BuildReceipt writer and loader (CORE-205 FR04/FR05)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models._evidence_core import ReceiptState, ReceiptValidationResult, ScopeConfidence, domain_digest
from trw_mcp.models._evidence_plans import BuildCommandResult, CommandClass, RequiredValidationPlan
from trw_mcp.models._evidence_records import BuildReceipt
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools._command_results import REQUIRED_BUILD_COMMAND_IDS
from trw_mcp.tools._evidence_binding import build_content_binding, mint_run_owned_scope
from trw_mcp.tools._evidence_gates import validate_build_receipt
from trw_mcp.tools._evidence_git import clean_git_sha
from trw_mcp.tools._evidence_persistence import WriteOutcome, generate_receipt_id, write_receipt

logger = structlog.get_logger(__name__)

_POLICY_VERSION = "v26.1-build-receipts"


def _legacy_results(tests_passed: bool, static_checks_clean: bool, scope_label: str) -> tuple[BuildCommandResult, ...]:
    """Observe-only adapter; enforce mode never calls this compatibility path."""
    return (
        BuildCommandResult(
            command_id="tests",
            label=f"{scope_label}: tests",
            command_class=CommandClass.TEST,
            exit_code=0 if tests_passed else 1,
        ),
        BuildCommandResult(
            command_id="static_checks",
            label=f"{scope_label}: static checks",
            command_class=CommandClass.STATIC,
            exit_code=0 if static_checks_clean else 1,
        ),
    )


def _governing_prd_paths(run_path: Path, project_root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Resolve governing PRDs from durable run state and return IDs + paths."""
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state.prd_utils import discover_governing_prds

        ids = tuple(discover_governing_prds(run_path, get_config()))
    except Exception:  # justified: no governing PRD is a valid non-PRD task, not a caller fallback
        ids = ()
    paths: list[str] = []
    prds_dir = project_root / "docs" / "requirements-aare-f" / "prds"
    missing: list[str] = []
    for prd_id in sorted(ids):
        exact = prds_dir / f"{prd_id}.md"
        matches = [exact] if exact.is_file() else sorted(prds_dir.glob(f"{prd_id}-*.md"))
        if len(matches) > 1:
            # Genuinely ambiguous: two files claim the same ID. That is a
            # requirements-integrity problem and binding evidence to an
            # arbitrary one of them would be worse than refusing.
            raise ValueError(
                f"governing PRD {prd_id!r} matches {len(matches)} files in {prds_dir}: "
                + ", ".join(m.name for m in matches)
            )
        if not matches:
            # NOT ambiguous — absent. A run can outlive the PRD it was scoped to
            # (archived, renamed, superseded), and that is a data condition, not
            # an integrity violation.
            #
            # This distinction is load-bearing. The single `!= 1` check that used
            # to be here raised "did not resolve uniquely" for a MISSING file,
            # and because record_build_receipt is fail-open, the receipt was
            # silently skipped — so every trw_build_check on such a run wrote no
            # typed receipt, and trw_deliver then blocked forever on the LAST
            # good receipt with "content-stale build evidence". The operator sees
            # a staleness message about a file, for a run whose real problem is a
            # PRD id that no longer resolves. Observed 2026-07-28 on a run scoped
            # to PRD-CORE-098, which exists only as an execution plan.
            missing.append(prd_id)
            continue
        paths.append(matches[0].relative_to(project_root).as_posix())
    if missing:
        logger.info(
            "governing_prd_unresolved",
            prd_ids=missing,
            prds_dir=str(prds_dir),
            note="run references PRDs with no file; receipt binds the ones that resolved",
        )
    return ids, tuple(paths)


def _governing_digest(project_root: Path, paths: tuple[str, ...]) -> str:
    return domain_digest(
        "build_governing_content",
        [
            {"path": path, "sha256": hashlib.sha256((project_root / path).read_bytes()).hexdigest()}
            for path in sorted(paths)
        ],
    )


def _validation_plan(
    *,
    scope_id: str,
    scope_digest: str,
    governing_prd_ids: tuple[str, ...],
    governing_content_digest: str,
    coverage_threshold: float | None,
    policy_mode: str,
) -> RequiredValidationPlan:
    # MCP/JSON callers may supply an integral threshold (``90``) even though
    # the model canonicalizes it to ``90.0``. Normalize before every digest so
    # construction and later validation bind identical numeric bytes.
    normalized_coverage = float(coverage_threshold) if coverage_threshold is not None else None
    policy_digest = domain_digest(
        "build_policy_config",
        {
            "required_command_ids": REQUIRED_BUILD_COMMAND_IDS,
            "coverage_threshold": normalized_coverage,
            "evidence_receipt_mode": policy_mode,
            "policy_version": _POLICY_VERSION,
        },
    )
    identity = domain_digest(
        "validation_plan_identity",
        {
            "scope_id": scope_id,
            "scope_digest": scope_digest,
            "governing_prd_ids": sorted(governing_prd_ids),
            "governing_content_digest": governing_content_digest,
            "policy_config_digest": policy_digest,
            "required_command_ids": REQUIRED_BUILD_COMMAND_IDS,
            "coverage_threshold": normalized_coverage,
            "policy_version": _POLICY_VERSION,
        },
    )
    plan_id = f"validation-plan-{identity[:24]}"
    fields = {
        "plan_id": plan_id,
        "scope_id": scope_id,
        "scope_digest": scope_digest,
        "governing_prd_ids": sorted(governing_prd_ids),
        "governing_content_digest": governing_content_digest,
        "policy_config_digest": policy_digest,
        "required_command_ids": sorted(REQUIRED_BUILD_COMMAND_IDS),
        "optional_command_ids": [],
        "coverage_threshold": normalized_coverage,
        "policy_version": _POLICY_VERSION,
    }
    return RequiredValidationPlan(
        plan_id=plan_id,
        plan_digest=domain_digest("validation_plan", fields),
        scope_id=scope_id,
        scope_digest=scope_digest,
        governing_prd_ids=governing_prd_ids,
        governing_content_digest=governing_content_digest,
        policy_config_digest=policy_digest,
        required_command_ids=REQUIRED_BUILD_COMMAND_IDS,
        coverage_threshold=normalized_coverage,
        policy_version=_POLICY_VERSION,
    )


def record_build_receipt(
    run_path: Path | None,
    project_root: Path,
    *,
    tests_passed: bool,
    static_checks_clean: bool,
    scope_label: str,
    coverage_pct: float | None,
    policy_mode: str,
    command_results: tuple[BuildCommandResult, ...] | None = None,
    coverage_threshold: float | None = None,
) -> WriteOutcome | None:
    """Persist a BuildReceipt against a server-resolved two-command plan."""
    if run_path is None or (policy_mode == "enforce" and command_results is None):
        return None
    try:
        scope = mint_run_owned_scope(run_path, project_root, scope_id=f"build-{run_path.name}")
        if scope.confidence is ScopeConfidence.UNVERIFIABLE:
            return None
        governing_ids, governing_paths = _governing_prd_paths(run_path, project_root)
        scope = scope.model_copy(update={"proposed_paths": governing_paths})
        binding = build_content_binding(scope, project_root)
        if binding.binding is None:
            return None
        governing_digest = _governing_digest(project_root, governing_paths)
        plan = _validation_plan(
            scope_id=scope.scope_id,
            scope_digest=scope.scope_digest,
            governing_prd_ids=governing_ids,
            governing_content_digest=governing_digest,
            coverage_threshold=coverage_threshold,
            policy_mode=policy_mode,
        )
        plan_path = run_path / "meta" / "plans" / "validation" / f"{plan.plan_id}.json"
        FileStateWriter().write_text(plan_path, plan.model_dump_json(exclude_none=True) + "\n")
        realized = command_results or _legacy_results(tests_passed, static_checks_clean, scope_label)
        receipt_id = generate_receipt_id("build")
        receipt = BuildReceipt(
            receipt_id=receipt_id,
            run_id=run_path.name,
            git_sha=clean_git_sha(project_root),
            completed_at=datetime.now(timezone.utc).isoformat(),
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            content_binding=binding.binding,
            command_results=realized,
            coverage_pct=coverage_pct,
            limitations="" if command_results is not None else "observe-mode legacy aggregate adapter",
            policy_mode=policy_mode,
            config_digest=plan.policy_config_digest,
            legacy_tests_passed=tests_passed,
            legacy_static_checks_clean=static_checks_clean,
        )
        return write_receipt(run_path, "build", receipt_id, receipt)
    except (
        Exception
    ):  # trw-fail-silent-allow: writer failure is missing evidence, never a legacy positive in enforce mode
        logger.warning("build_receipt_write_failed", run=str(run_path), exc_info=True)
        return None


def latest_build_receipt(run_path: Path | None) -> BuildReceipt | None:
    if run_path is None:
        return None
    candidates = sorted(
        (run_path / "meta" / "receipts" / "build").glob("*.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for path in candidates:
        try:
            return BuildReceipt.model_validate_json(path.read_bytes())
        except Exception:
            logger.warning("build_receipt_load_failed", run=str(run_path), receipt=path.name, exc_info=True)
    return None


def load_latest_build_evidence(
    run_path: Path | None,
    project_root: Path,
) -> tuple[ReceiptValidationResult, BuildReceipt | None]:
    """Fully validate the newest typed build receipt against its persisted plan."""
    receipt = latest_build_receipt(run_path)
    if run_path is None or receipt is None:
        return (
            ReceiptValidationResult(
                state=ReceiptState.LEGACY_UNBOUND,
                reason_code="typed_absent",
                typed_present=False,
            ),
            None,
        )
    try:
        plan_path = run_path / "meta" / "plans" / "validation" / f"{receipt.plan_id}.json"
        plan = RequiredValidationPlan.model_validate_json(plan_path.read_bytes())
        return validate_build_receipt(receipt, plan, project_root), receipt
    except Exception:
        logger.warning("build_receipt_validation_failed", run=str(run_path), receipt=receipt.receipt_id, exc_info=True)
        return (
            ReceiptValidationResult(
                state=ReceiptState.INVALID,
                reason_code="typed_present_invalid",
                receipt_id=receipt.receipt_id,
                typed_present=True,
            ),
            receipt,
        )


__all__ = [
    "latest_build_receipt",
    "load_latest_build_evidence",
    "record_build_receipt",
]
