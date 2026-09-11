"""Extracted helpers for trw_review tool — finding validation, mode handlers.

Keeps the tool closure in review.py focused on dispatch while
business logic lives in testable pure-ish functions.

Shared constants and low-level helpers (_normalize_severity, _compute_verdict,
_get_git_diff, _persist_review_artifact, etc.) live here as the canonical
definitions; review.py re-exports them so existing test patches at
``trw_mcp.tools.review.*`` continue to resolve.

Mode handler functions are extracted to sub-modules for module-size compliance:
- ``_review_auto.py``: handle_auto_mode
- ``_review_cross_model.py``: handle_cross_model_mode + the review-coverage
  vocabulary (COVERAGE_*/REASON_* tokens, ``_build_single_family_caveat``)
- ``_review_manual.py``: handle_manual_mode, handle_reconcile_mode, validate_manual_findings,
  count_by_severity, and reconciliation helpers
- ``_review_multi.py``: _run_multi_reviewer_analysis
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.dispatch import SUPPORTED_CLIENTS, DispatchRequest, apply_role, dispatch
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter

if TYPE_CHECKING:
    from trw_mcp.dispatch import DispatchResult
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._review_provenance import RunIdentity
    from trw_mcp.tools._review_receipt_writer import ReviewReceiptWriteResult

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared constants and low-level helpers (canonical definitions)
# ---------------------------------------------------------------------------

# Reviewer roles for multi-agent review (QUAL-027)
REVIEWER_ROLES: tuple[str, ...] = (
    "correctness",
    "security",
    "test-quality",
    "performance",
    "style",
    "spec-compliance",
)


def _get_git_diff(paths: list[str] | None = None, base: str | None = None) -> str:
    """Get a git diff, returning empty string on any error.

    Default (no args) diffs the working tree against ``HEAD`` — the original
    contract. PRD-CORE-213-FR04 extends this with:
      - ``base``: diff ``<base>..HEAD`` (the run's recorded base ref) instead of
        the uncommitted ``HEAD`` diff, so committed transitions are visible.
      - ``paths``: a path-limited diff (``git diff ... -- <paths>``) so the
        transition detector's cost is bounded by the PRD directory (NFR03).
    All arguments are trusted internal literals / repo-relative paths — never
    caller-tainted shell input.
    """
    cmd = ["git", "diff", f"{base}..HEAD" if base else "HEAD"]
    if paths:
        cmd.append("--")
        cmd.extend(paths)
    try:
        # git is a well-known VCS tool; all args are static literals / repo-relative
        # paths, never caller-tainted shell input.
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        logger.debug("review_git_diff", length=len(result.stdout))
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _normalize_severity(severity: str) -> str:
    """Map an external severity label to its internal level.

    Resolves through ``SEVERITY_ALIASES``, the same table the accept-check in
    ``normalize_review_finding`` is derived from, so a label can never be
    accepted with no meaning or carry a meaning it is not accepted under.
    Unknown labels fall back to ``info`` — but they are rejected upstream
    before reaching here, so the fallback is defensive, not a silent downgrade.
    """
    from trw_mcp.tools._review_validation import SEVERITY_ALIASES

    return SEVERITY_ALIASES.get(severity.lower().strip(), "info")


class CrossModelUnavailable(RuntimeError):
    """The configured reviewer could not be reached or refused to run.

    Outcome (c) in PRD-CORE-270-FR03: a missing binary, an unauthenticated CLI,
    an unsupported client id, or a non-zero exit. Distinct from "ran and found
    nothing" -- reporting this as an empty review would turn an absent check
    into an apparently passing one.
    """


class CrossModelIncomplete(RuntimeError):
    """The reviewer ran but did not produce a usable result.

    Outcome (d) in PRD-CORE-270-FR03: a timeout, or output no parser could turn
    into findings. Also must never render as "no findings".
    """


def _coerce_findings(raw: object) -> list[dict[str, str]] | None:
    """Stringify a reviewer's ``findings`` list, or reject it outright.

    Returns ``None`` -- which the caller escalates to
    :class:`CrossModelIncomplete` -- when the payload is not a list, or when any
    element is not an object. Dropping unreadable elements and returning the
    survivors would report ``{"findings": ["P0: arbitrary code execution"]}`` as
    "ran, found nothing", which is the exact inversion this module exists to
    prevent (PRD-CORE-270-FR05).
    """
    if not isinstance(raw, list):
        return None
    coerced: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        coerced.append({str(key): str(value) for key, value in item.items()})
    return coerced


def _findings_document(payload: object) -> list[dict[str, str]] | None:
    """Read a findings document, honoring an explicit error flag."""
    if not isinstance(payload, dict) or payload.get("is_error"):
        return None
    if "findings" not in payload:
        return None
    return _coerce_findings(payload["findings"])


def _decode_json_answer(text: str) -> object | None:
    """Decode a reviewer's answer, tolerating a Markdown code fence.

    Claude's dispatch normalizer puts the model's ANSWER in ``result.text`` and
    keeps the CLI envelope in ``result.structured`` (``dispatch/_normalize.py``),
    so a findings document produced by the ``code-review`` role arrives as text.
    """
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[A-Za-z0-9_-]*\n", "", body)
        body = re.sub(r"\n?```\s*$", "", body)
    try:
        decoded: object = json.loads(body)
    except (ValueError, RecursionError):
        return None
    return decoded


def _parse_cross_model_findings(result: DispatchResult) -> list[dict[str, str]] | None:
    """Extract findings from a dispatched reviewer's answer, or reject it.

    Returns ``None`` when the answer is not a findings document, which the
    caller raises as :class:`CrossModelIncomplete` (PRD-CORE-270-FR05) -- never
    as an empty list. Only an explicit, well-formed ``findings: []`` counts as
    "ran, found nothing".

    Prose is deliberately NOT accepted. Wrapping arbitrary text in one
    ``severity="info"`` finding made every non-empty answer a substantive
    passing cross-family review -- including
    ``"Overall verdict: BLOCK"``, ``"{not valid json"``, and
    ``"I could not review this diff because authentication is required."``
    (all three verified to yield ``verdict=pass, critical_count=0``). The
    reviewer's own words survive in the raised exception message instead, as
    diagnostics rather than as a verdict input.
    """
    findings = _findings_document(result.structured)
    if findings is not None:
        return findings
    text = result.text.strip()
    if not text:
        return None
    return _findings_document(_decode_json_answer(text))


def _invoke_cross_model_review(
    diff: str,
    config: TRWConfig,
) -> list[dict[str, str]] | None:
    """Invoke cross-model review by dispatching another coding-agent CLI.

    Returns ``None`` when NO transport was attempted, and a (possibly empty)
    list when one was. That distinction is load-bearing for honesty: collapsing
    both to ``[]`` made a configured-but-never-contacted provider report
    ``provider_returned_empty``, blaming the operator's provider for TRW's own
    gap. It is preserved here -- ``None`` now means "no reviewer configured"
    rather than "no transport exists" (PRD-CORE-270-FR01).

    ``config.cross_model_provider`` names a DISPATCH CLIENT (``codex``, ``agy``,
    ``claude``, ...), not a model. The model, when overridden at all, comes from
    the existing ``dispatch_default_models`` map, so there is one place an
    operator says "which model on which client" (PRD-CORE-270-FR02).

    The child runs read-only by two independent mechanisms: ``read_only=True``
    omits every write/permission-bypass flag and applies the client's own
    sandbox, and the ``code-review`` role preamble states the constraint in the
    prompt (NFR02).

    Raises:
        CrossModelUnavailable: outcome (c) -- unsupported client, missing or
            unauthenticated CLI, non-zero exit.
        CrossModelIncomplete: outcome (d) -- timed out, or output that yielded
            no parseable findings.
    """
    client = (config.cross_model_provider or "").strip()
    if not client:
        return None

    if client not in SUPPORTED_CLIENTS:
        raise CrossModelUnavailable(
            f"cross_model_provider={client!r} is not a dispatch client; "
            f"expected one of {', '.join(sorted(SUPPORTED_CLIENTS))}"
        )

    model = (config.dispatch_default_models or {}).get(client) or None
    request = DispatchRequest(
        client=client,
        prompt=apply_role("code-review", diff),
        model=model,
        read_only=True,
        timeout_s=max(1, int(config.cross_model_review_timeout_secs)),
    )
    result = dispatch(request)

    if result.timed_out:
        raise CrossModelIncomplete(f"{client} exceeded {request.timeout_s}s")
    if result.exit_code != 0:
        raise CrossModelUnavailable(f"{client} exited {result.exit_code}")

    findings = _parse_cross_model_findings(result)
    if findings is None:
        excerpt = " ".join(result.text.split())[:400]
        raise CrossModelIncomplete(
            f"{client} did not return a findings document" + (f"; answer began: {excerpt}" if excerpt else "")
        )
    return findings


def _cross_family_available(config: TRWConfig) -> bool:
    """Single source of truth for cross-family review availability (QUAL-108-FR04).

    Today this is config-only: a cross-family review is *available* iff the
    cross-model review feature is enabled AND a provider is configured. It does
    NOT assert the provider is reachable or that it returned findings — that
    *realized* signal is computed by the caller (NFR02 truthfulness invariant).

    # SEAM(PRD-DIST-2444): discovery-feed availability. A future discovered-model
    # inventory (D18 / PRD-DIST-2444, the proprietary trw-distill ledger) may
    # feed this predicate so availability reflects the realized fleet state
    # rather than config alone. This PRD makes NO discovery call here; the
    # inventory is read through a future thin adapter (QUAL-108 OQ2), never by
    # importing trw-distill internals. Expiry: revisit when PRD-DIST-2444 lands.
    """
    return bool(config.cross_model_review_enabled) and bool(config.cross_model_provider)


def _compute_verdict(findings: list[dict[str, str]]) -> str:
    """Compute review verdict from worst severity across findings."""
    critical_count = sum(1 for f in findings if f.get("severity") == "critical")
    warning_count = sum(1 for f in findings if f.get("severity") == "warning")
    logger.debug(
        "review_findings_count",
        count=len(findings),
        critical=critical_count,
        warnings=warning_count,
    )

    if critical_count > 0:
        return "block"
    if warning_count > 0:
        return "warn"
    return "pass"


def _stamp_reviewer_family(payload: dict[str, object], outcome: ReviewReceiptWriteResult) -> None:
    """PRD-CORE-255-FR02 — record the DERIVED reviewer family on a payload.

    ``review_family_coverage`` is upgraded to ``cross_family`` only when the
    family verified to ``cross_model``; it is never written by a caller claim.
    ``family_downgraded_reason`` is present only when a claim was refused, so an
    absent key means "nothing was refused", never "a refusal went unreported".
    """
    from trw_mcp.tools._review_reviewer_family import COVERAGE_CROSS_FAMILY

    if not outcome.reviewer_family:
        return
    payload["reviewer_family"] = outcome.reviewer_family
    if outcome.verified_cross_model:
        payload["review_family_coverage"] = COVERAGE_CROSS_FAMILY
    if outcome.family_downgraded_reason:
        payload["family_downgraded_reason"] = outcome.family_downgraded_reason


def _persist_review_artifact(
    resolved_run: Path | None,
    review_data: dict[str, object],
    event_fields: dict[str, object],
    result_payload: dict[str, object] | None = None,
    *,
    verified_reviewer_identity: RunIdentity | None = None,
) -> str:
    """Write review.yaml and log review_complete event.

    Specific to manual/cross_model/auto review modes — writes to
    ``meta/review.yaml`` and logs event type ``review_complete``.
    Do NOT use for reconciliation (which writes ``reconciliation.yaml``
    with event type ``spec_reconciliation``).

    Args:
        resolved_run: Run directory path, or None if no run active.
        review_data: Full review data dict to write to review.yaml.
        event_fields: Fields to include in the review_complete event.

    Returns:
        Path string to review.yaml, or empty string if no run.
    """
    if resolved_run is None:
        return ""

    writer = FileStateWriter()
    reader = FileStateReader()
    events = FileEventLogger(writer)

    events_path = resolved_run / "meta" / "events.jsonl"
    prd_ids = _resolve_review_prd_ids(resolved_run, reader, event_fields)
    review_payload = dict(review_data)
    # PRD-CORE-213-FR01: stamp reviewer provenance onto every persisted
    # review.yaml. Manual mode injects its own block (with any explicit
    # reviewer_source) upstream; this central call covers auto/cross_model
    # from their ``mode`` key. Fail-open — never blocks the artifact write.
    from trw_mcp.tools._review_provenance import ensure_reviewer_block

    ensure_reviewer_block(review_payload, resolved_run, reader, verified_identity=verified_reviewer_identity)

    # CORE-205 FR02/FR03: the typed receipt is authoritative.  review.yaml is
    # retained only as a derived projection.  Receipt failure is visible and,
    # under enforce mode, cannot retain a legacy positive ``substantive`` bit.
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._review_receipt_writer import record_review_receipt

    evidence_mode = str(getattr(get_config(), "evidence_receipt_mode", "enforce"))
    receipt_outcome = record_review_receipt(
        resolved_run,
        review_payload,
        tuple(prd_ids),
        policy_mode=evidence_mode,
    )
    review_payload["review_receipt_id"] = receipt_outcome.receipt_id
    review_payload["review_plan_id"] = receipt_outcome.plan_id
    review_payload["typed_receipt_state"] = receipt_outcome.state
    review_payload["typed_receipt_reason"] = receipt_outcome.reason_code
    if evidence_mode == "enforce" and not receipt_outcome.ok:
        review_payload["substantive"] = False
        review_payload["non_substantive_reason"] = receipt_outcome.reason_code
    # PRD-CORE-255-FR02: the family the SERVER derived (not the one the caller
    # claimed) and, on a refusal, which verification condition refused it. Both
    # are surfaced in the response — a downgrade that only reached structlog is
    # invisible to the agent whose claim was refused.
    _stamp_reviewer_family(review_payload, receipt_outcome)
    if result_payload is not None:
        result_payload["review_receipt_id"] = receipt_outcome.receipt_id
        result_payload["review_plan_id"] = receipt_outcome.plan_id
        result_payload["typed_receipt_state"] = receipt_outcome.state
        result_payload["typed_receipt_reason"] = receipt_outcome.reason_code
        if evidence_mode == "enforce" and not receipt_outcome.ok:
            result_payload["substantive"] = False
            result_payload["non_substantive_reason"] = receipt_outcome.reason_code
        _stamp_reviewer_family(result_payload, receipt_outcome)

    review_path = resolved_run / "meta" / "review.yaml"
    writer.write_yaml(review_path, review_payload)
    markdown_path = resolved_run / "meta" / "review.md"
    writer.write_text(markdown_path, render_review_markdown(review_payload))

    if events_path.parent.exists():
        verdict = str(review_data.get("verdict", event_fields.get("verdict", ""))).upper()
        finding_categories = _extract_review_finding_categories(review_data)
        for prd_id in prd_ids:
            events.log_event(
                events_path,
                "audit_cycle_complete",
                {
                    "prd_id": prd_id,
                    "verdict": verdict,
                    "finding_categories": finding_categories,
                },
            )
        events.log_event(events_path, "review_complete", event_fields)

    return str(review_path)


def render_review_markdown(review_data: dict[str, object]) -> str:
    """Render review data as PR-description friendly Markdown."""
    verdict = str(review_data.get("verdict", "unknown")).upper()
    mode = str(review_data.get("mode", review_data.get("phase", "manual")))
    findings = review_data.get("findings", review_data.get("cross_model_findings", []))
    lines = [f"# TRW Review: {verdict}", "", f"- Mode: `{mode}`"]
    if isinstance(findings, list):
        lines.append(f"- Findings: {len(findings)}")
        lines.append("")
        lines.append("| Severity | Category | Description |")
        lines.append("| --- | --- | --- |")
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = _markdown_table_cell(finding.get("severity", "info"))
            category = _markdown_table_cell(finding.get("category", "general"))
            description = _markdown_table_cell(finding.get("description", ""))
            lines.append(f"| {severity} | {category} | {description} |")
    return "\n".join(lines) + "\n"


def _markdown_table_cell(value: object) -> str:
    """Escape a value for safe single-line Markdown table rendering."""
    return " ".join(str(value).replace("|", "\\|").splitlines())


def _resolve_review_prd_ids(
    resolved_run: Path,
    reader: FileStateReader,
    event_fields: dict[str, object],
) -> list[str]:
    """Resolve PRD IDs for a review event from explicit fields or run scope."""
    raw_prd_ids = event_fields.get("prd_ids")
    if isinstance(raw_prd_ids, list):
        prd_ids = list(dict.fromkeys(str(prd_id) for prd_id in raw_prd_ids if str(prd_id)))
        if prd_ids:
            return prd_ids

    run_yaml_path = resolved_run / "meta" / "run.yaml"
    if not run_yaml_path.exists():
        return []

    run_data = reader.read_yaml(run_yaml_path)
    raw_scope = run_data.get("prd_scope", []) if isinstance(run_data, dict) else []
    if not isinstance(raw_scope, list):
        return []
    return list(dict.fromkeys(str(prd_id) for prd_id in raw_scope if str(prd_id)))


def _extract_review_finding_categories(review_data: dict[str, object]) -> list[str]:
    """Extract finding categories from persisted review data."""
    findings = review_data.get("findings", review_data.get("cross_model_findings"))
    if not isinstance(findings, list):
        return []
    return [
        str(finding.get("category", ""))
        for finding in findings
        if isinstance(finding, dict) and str(finding.get("category", ""))
    ]


# ---------------------------------------------------------------------------
# Lazy re-exports from sub-modules (preserves existing import paths)
# ---------------------------------------------------------------------------

# Mapping of re-exported names to their source sub-module
_REEXPORT_MAP: dict[str, str] = {
    # _review_auto.py
    "handle_auto_mode": "trw_mcp.tools._review_auto",
    # _review_cross_model.py
    "handle_cross_model_mode": "trw_mcp.tools._review_cross_model",
    # _review_manual.py
    "handle_manual_mode": "trw_mcp.tools._review_manual",
    "handle_reconcile_mode": "trw_mcp.tools._review_manual",
    "validate_manual_findings": "trw_mcp.tools._review_manual",
    "count_by_severity": "trw_mcp.tools._review_manual",
    "_extract_section": "trw_mcp.tools._review_manual",
    "_extract_identifiers": "trw_mcp.tools._review_manual",
    "_added_lines_only": "trw_mcp.tools._review_manual",
    "_extract_fr_mismatches": "trw_mcp.tools._review_manual",
    "_count_frs_in_prd": "trw_mcp.tools._review_manual",
    # _review_multi.py
    "_run_multi_reviewer_analysis": "trw_mcp.tools._review_multi",
}


def __getattr__(name: str) -> object:
    """Lazy re-export of mode handler functions from sub-modules.

    This avoids circular imports: sub-modules import shared helpers from
    this module, and this module re-exports mode handlers from sub-modules.
    The deferred ``__getattr__`` approach ensures sub-modules are only
    loaded when a re-exported name is actually accessed, by which time
    this module is fully initialized.
    """
    module_path = _REEXPORT_MAP.get(name)
    if module_path is not None:
        import importlib

        mod = importlib.import_module(module_path)
        value = getattr(mod, name)
        # Cache on module dict for subsequent fast access
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
