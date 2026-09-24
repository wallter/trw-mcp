"""``trw_prd_validate`` registration, split from the ``requirements.py`` facade.

Belongs to ``requirements.py``, which calls :func:`_register_prd_validate_tool`
from ``register_requirements_tools``. Extracted for the 350 effective-LOC gate;
``requirements.py`` was 379 and is the older of the two registrations' homes.

**Every name the tests patch is resolved through the parent module at call time**
(``_req.get_config``, ``_req.logger``, ``_req.resolve_project_root``,
``_req.validate_prd_quality_v2``). ``test_*`` files monkeypatch those on
``trw_mcp.tools.requirements``; binding them here at import time would silently
bypass the patches and make those tests assert nothing. The import is call-time
because ``requirements`` imports THIS module, so a module-level import would
close a cycle.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from fastmcp import Context

from trw_mcp.exceptions import StateError
from trw_mcp.models.typed_dicts import ValidateResultDict
from trw_mcp.state.prd_utils import extract_sections as _extract_sections
from trw_mcp.state.prd_utils import parse_frontmatter
from trw_mcp.state.validation import refresh_dynamic_prd_validation
from trw_mcp.state.validation.template_variants import get_required_sections
from trw_mcp.tools._prd_validate_payload import build_validate_payload
from trw_mcp.tools._prd_validation_cache import (
    CacheBounds as _PRDValidationCacheBounds,
)
from trw_mcp.tools._prd_validation_cache import (
    cache_key as _prd_validation_cache_key,
)
from trw_mcp.tools._prd_validation_cache import (
    cache_metadata as _prd_validation_cache_metadata,
)
from trw_mcp.tools._prd_validation_cache import (
    cache_path as _prd_validation_cache_path,
)
from trw_mcp.tools._prd_validation_cache import (
    load_pure_result_with_reason,
    retire_legacy_cache,
    store_pure_result,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = ["_register_prd_validate_tool"]


def _register_prd_validate_tool(server: FastMCP) -> None:
    # Call-time import: `requirements` imports this module, so a module-level
    # import would close a cycle. Also what makes the test monkeypatches land.
    from trw_mcp.tools import requirements as _req

    """Register the PRD validation tool."""

    @server.tool(output_schema=None)
    def trw_prd_validate(
        ctx: Context | None = None,
        prd_path: str = "",
        fast: bool = False,
        verbose: bool = False,
    ) -> ValidateResultDict:
        """Score a PRD against the validation suite; returns a READY/NEEDS-WORK verdict.

        Use when a PRD just landed and you need ambiguity/completeness/
        traceability gates checked before coding.

        A time budget bounds every call; exceeding it sets validation_partial
        (never a silent pass). quality_tier: skeleton|draft|review|approved.

        Output: total_score, quality_tier, grade, valid, failures, dimensions.

        Args:
            prd_path: path to the PRD markdown file (required).
            fast: text-only score (skips repo-grounded checks); sets
                validation_partial=true and checks_skipped. Re-run without it
                for a full verdict.
            verbose: full diagnostic payload instead of the compact default;
                scores/verdicts unchanged.
        """
        # prd_path has an empty default so FastMCP can inject ctx as the first
        # typed kwarg (PRD-CORE-141 FR03); an empty path is still rejected.
        if not prd_path:
            raise StateError("prd_path is required", path="")
        path = Path(prd_path).resolve()

        # QUAL-042-FR03: Path containment --- prevent reading files outside project

        project_root = _req.resolve_project_root()
        if not path.is_relative_to(project_root):
            raise StateError(
                f"PRD path escapes project root: {path}",
                path=str(path),
            )

        if not path.exists():
            raise StateError(f"PRD file not found: {path}", path=str(path))

        content = path.read_text(encoding="utf-8")

        config = _req.get_config()
        cache_path = _prd_validation_cache_path(project_root)
        cache_bounds = _PRDValidationCacheBounds.from_config(config)
        # One-time retirement of the disposable legacy monolithic YAML cache.
        try:
            retire_legacy_cache(project_root)
        except Exception:  # justified: legacy retirement never blocks validation
            _req.logger.debug("prd_validation_legacy_retire_failed", exc_info=True)
        cache_metadata = _prd_validation_cache_metadata(content, config)
        cache_key = _prd_validation_cache_key(content, config)
        try:
            pure_result, cache_miss_reason = load_pure_result_with_reason(
                cache_path, cache_key, max_entry_bytes=cache_bounds.max_entry_bytes
            )
        except Exception:  # justified: any cache-load fault degrades to a miss
            _req.logger.debug("prd_validation_cache_load_failed", path=str(cache_path), exc_info=True)
            pure_result, cache_miss_reason = None, "corrupt"
        cache_hit = pure_result is not None

        if pure_result is None:
            pure_result = _req.validate_prd_quality_v2(content, config, include_dynamic_checks=False)
            try:
                store_pure_result(cache_path, cache_key, pure_result, bounds=cache_bounds)
            except Exception:  # justified: cache failure degrades to fresh validation
                _req.logger.debug("prd_validation_cache_write_failed", path=str(cache_path), exc_info=True)

        # Repository, wiring, duplicate, and seam-expiry truth is deliberately
        # recomputed on every call even when pure text scoring is a cache hit.
        # PRD-FIX-112: bound the dynamic portion by a monotonic-clock deadline so
        # a future slowdown can never re-train gate bypass; ``fast`` skips the
        # dynamic portion entirely. Both surface the SAME visibly-partial shape.
        budget_report: dict[str, object] = {}
        deadline = time.monotonic() + max(float(config.prd_validate_budget_seconds), 0.0)
        v2_result = refresh_dynamic_prd_validation(
            pure_result,
            content,
            config=config,
            project_root=str(project_root),
            deadline=deadline,
            fast=fast,
            budget_report=budget_report,
        )

        sections = _extract_sections(content)
        frontmatter = parse_frontmatter(content)
        sections_expected = get_required_sections(str(frontmatter.get("category", "") or ""))

        # Auto-update phase to PLAN (PRD-CORE-141 FR03/FR05: ctx-aware
        # find_active_run suppresses mtime-scan hijack for fresh sessions).
        from trw_mcp.models.run import Phase
        from trw_mcp.state._paths import (
            TRWCallContext,
            find_active_run,
            resolve_pin_key,
        )
        from trw_mcp.state.phase import try_update_phase

        _pin_key = resolve_pin_key(ctx=ctx, explicit=None)
        _raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
        _call_ctx = TRWCallContext(
            session_id=_pin_key,
            client_hint=None,
            explicit=False,
            fastmcp_session=_raw_session if isinstance(_raw_session, str) else None,
        )
        try_update_phase(find_active_run(context=_call_ctx), Phase.PLAN)

        _prd_id_str = str(path.stem)
        _req.logger.info(
            "trw_prd_validated",
            path=str(path),
            valid=v2_result.valid,
            total_score=v2_result.total_score,
            quality_tier=v2_result.quality_tier,
            failures=len(v2_result.failures),
        )

        validate_result: ValidateResultDict = build_validate_payload(
            v2_result,
            path=path,
            sections=sections,
            sections_expected=sections_expected,
            frontmatter=frontmatter,
            cache_hit=cache_hit,
            cache_key=cache_key,
            cache_miss_reason=cache_miss_reason,
            cache_metadata=cache_metadata,
            verbose=verbose,
        )

        # PRD-FIX-112: surface the budget/fast partial markers on the wire dict.
        # The loud ``validation_partial:`` warning already rides in
        # integrity_warnings (copied by build_validate_payload); these two fields
        # are the machine-readable form. Default calls (no fast, generous budget)
        # yield validation_partial=False + checks_skipped=[] — byte-identical
        # otherwise to the pre-change payload.
        validate_result["validation_partial"] = bool(budget_report.get("validation_partial", False))
        _skipped = budget_report.get("checks_skipped", [])
        validate_result["checks_skipped"] = _skipped if isinstance(_skipped, list) else []

        # Substrate-First gate (PRD-DIST-218 FR-2). Heuristic check:
        # flag PRDs that propose module-level hardcoded vocabulary
        # collections without an acknowledged justification.
        try:
            from trw_mcp.tools._substrate_first_check import substrate_first_check

            substrate_result = substrate_first_check(content)
            validate_result["substrate_first"] = substrate_result.to_payload()
            if substrate_result.verdict == "fail":
                _req.logger.warning(
                    "substrate_first_gate_fail",
                    prd_id=_prd_id_str,
                    flagged_count=len(substrate_result.flagged_collections),
                )
        except Exception:  # justified: gate must not break prd_validate
            _req.logger.debug("substrate_first_check_skipped", exc_info=True)

        # Inject ceremony progress summary.
        try:
            from trw_mcp.state._paths import resolve_trw_dir as _resolve_trw_dir
            from trw_mcp.tools._ceremony_status_context import append_ceremony_status_for_tool

            _validate_dict = cast("dict[str, object]", validate_result)
            append_ceremony_status_for_tool(_validate_dict, _resolve_trw_dir(), tool_name="prd_validate")
        except Exception:  # justified: fail-open — ceremony status must not break prd_validate
            _req.logger.debug("prd_validate_ceremony_status_skipped", exc_info=True)

        return validate_result
