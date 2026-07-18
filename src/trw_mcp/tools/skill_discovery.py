"""Read-only skill meta-discovery helpers for PRD-CORE-170."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import structlog
from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.models.skill_manifest import (
    IssueSeverity,
    SkillManifest,
    SkillManifestIssue,
    SkillValidationMode,
    validate_skill_markdown,
)
from trw_mcp.tools._skill_lifecycle import SkillLifecycleState, is_advertisable

logger = structlog.get_logger(__name__)

_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class SkillDiscoveryCandidate(BaseModel):
    """Ranked skill recommendation without execution side effects."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str
    name: str
    description: str
    score: float = Field(ge=0.0)
    reasons: tuple[str, ...]
    risk_warnings: tuple[str, ...]
    risk_level: str
    argument_hint: str | None = None


class SkillDiscoveryResult(BaseModel):
    """Read-only discovery output."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    candidates: tuple[SkillDiscoveryCandidate, ...]
    warnings: tuple[SkillManifestIssue, ...] = ()
    executed: bool = False


def discover_meta_skills(
    skill_paths: Sequence[str | Path],
    *,
    query: str,
    mode: SkillValidationMode = "compat",
    include_private: bool = False,
    active_cap: int | None = None,
    session_id: str = "",
    lifecycle_states: Mapping[str, SkillLifecycleState] | None = None,
) -> SkillDiscoveryResult:
    """Return ranked eligible skills from SKILL.md paths without executing them.

    PRD-QUAL-111 additions (both DEFAULT-OFF / no-op, NFR01):

    - ``active_cap`` (FR03): when ``None`` (default) all eligible candidates are
      returned exactly as before. When a positive integer, the result is
      truncated to the top ``active_cap`` candidates AFTER the existing sort
      (key unchanged: ``(-score, name, path)``), so ties resolve identically.
    - Surface tracking (FR01): when ``config.skill_surface_tracking_enabled`` is
      true, one append-only ``SkillSurfaceEvent`` is written per RETURNED
      candidate (after the cap). When the flag is false (default), zero events
      are written and the returned tuple is byte-for-byte identical to today.

    PRD-CORE-218-FR07: ``lifecycle_states`` maps a skill name to its lifecycle
    state. When supplied, skills whose state is NOT advertisable (retired,
    removed, or hidden) are withheld — discovery stops advertising a skill on its
    way out. ``None`` (default) preserves the prior behavior exactly.
    """

    candidates: list[SkillDiscoveryCandidate] = []
    warnings: list[SkillManifestIssue] = []
    query_terms = _terms(query)

    for skill_path_like in skill_paths:
        skill_path = Path(skill_path_like)
        content, read_issue = _read_skill(skill_path, mode)
        if read_issue is not None:
            warnings.append(read_issue)
            continue

        validation = validate_skill_markdown(content, path=skill_path, mode=mode)
        warnings.extend(validation.warnings)
        warnings.extend(validation.errors)
        if validation.manifest is None or not validation.ok:
            continue
        if not _eligible(validation.manifest, include_private=include_private):
            continue

        # FR07: a retired/removed/hidden skill is no longer advertised by discovery.
        if lifecycle_states is not None:
            state = lifecycle_states.get(validation.manifest.name)
            if state is not None and not is_advertisable(state):
                continue

        candidates.append(_candidate(skill_path, validation.manifest, query_terms))

    ranked = tuple(sorted(candidates, key=lambda candidate: (-candidate.score, candidate.name, candidate.path)))

    # FR03 active-cap: post-sort truncation only. None (default) = no-op.
    if active_cap is not None and active_cap >= 0:
        ranked = ranked[:active_cap]

    # FR01 surface tracking: guarded side-write, one event per returned
    # candidate. Default-off and fail-open -- never alters the returned tuple.
    _maybe_log_surface_events(ranked, query_terms=query_terms, session_id=session_id)

    return SkillDiscoveryResult(candidates=ranked, warnings=tuple(warnings), executed=False)


def _maybe_log_surface_events(
    ranked: Sequence[SkillDiscoveryCandidate],
    *,
    query_terms: frozenset[str],
    session_id: str,
) -> None:
    """Emit one skill-surface event per returned candidate, behind the flag.

    DEFAULT-OFF (NFR01): when ``skill_surface_tracking_enabled`` is false the
    function returns before any write. Fail-open (NFR02): any error during the
    enable-check or write is swallowed so discovery is never blocked.
    """
    try:
        from trw_mcp.models.config import get_config

        cfg = get_config()
        if not getattr(cfg, "skill_surface_tracking_enabled", False):
            return

        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.skill_surface_tracking import log_skill_surface_event

        trw_dir = resolve_trw_dir()
        for candidate in ranked:
            matched = len(query_terms & _terms(f"{candidate.name} {candidate.description}"))
            log_skill_surface_event(
                trw_dir,
                skill_name=candidate.name,
                surface_type="discovery",
                session_id=session_id,
                query_terms_matched=matched,
            )
    except Exception:  # trw:intentional fail-open: surface tracking must not block discovery
        logger.debug("skill_surface_tracking_skipped", exc_info=True)


def _load_lifecycle_states() -> Mapping[str, SkillLifecycleState]:
    """Load persisted lifecycle states for the advertising filter (FR07 wiring).

    The store loader is itself fail-open (missing/corrupt -> empty + WARN), but
    this wrapper adds a final belt-and-suspenders guard so an unexpected import
    or resolution error still degrades to "advertise everything" rather than
    breaking discovery. An empty map is a no-op filter in ``discover_meta_skills``.
    """
    try:
        from trw_mcp.state.skill_lifecycle_store import load_lifecycle_states

        return load_lifecycle_states()
    except Exception:  # trw:intentional fail-open: lifecycle load must not block discovery
        logger.warning("skill_lifecycle_states_load_skipped", exc_info=True)
        return {}


def register_skill_discovery_tools(server: FastMCP) -> None:
    """Register read-only skill discovery MCP tools."""

    @server.tool(output_schema=None)
    def trw_skill_discovery(
        skill_paths: list[str],
        query: str,
        mode: SkillValidationMode = "compat",
        include_private: bool = False,
        active_cap: int | None = None,
    ) -> dict[str, object]:
        """Rank eligible SKILL.md files without executing them.

        Use when an agent needs safe skill recommendations from explicit
        SKILL.md paths before invoking any workflow.

        Args:
            skill_paths: Explicit SKILL.md paths to inspect.
            query: Natural-language search terms.
            mode: Manifest validation mode, either "compat" or "strict".
            include_private: Include non-user-invocable skills when true.
            active_cap: Optional PRD-QUAL-111-FR03 bound. ``None`` (default) is a
                no-op (all eligible candidates returned). A positive integer
                truncates to the top-N after the existing sort.

        Returns:
            {"candidates": list, "warnings": list, "executed": false}
        """

        return discover_meta_skills(
            skill_paths,
            query=query,
            mode=mode,
            include_private=include_private,
            active_cap=active_cap,
            lifecycle_states=_load_lifecycle_states(),
        ).model_dump(mode="json")


def _read_skill(path: Path, mode: SkillValidationMode) -> tuple[str, SkillManifestIssue | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return "", _read_issue(path, "skill_path", "skill file does not exist", mode)
    except IsADirectoryError:
        return "", _read_issue(path, "skill_path", "skill path is a directory", mode)
    except UnicodeDecodeError as exc:
        return "", _read_issue(path, "skill_path", f"skill file is not UTF-8: {exc}", mode)


def _read_issue(path: Path, field: str, reason: str, mode: SkillValidationMode) -> SkillManifestIssue:
    severity: IssueSeverity = "error" if mode == "strict" else "warning"
    return SkillManifestIssue(path=str(path), field=field, reason=reason, mode=mode, severity=severity)


def _eligible(manifest: SkillManifest, *, include_private: bool) -> bool:
    if not manifest.meta_discovery:
        return False
    return include_private or manifest.user_invocable


def _candidate(path: Path, manifest: SkillManifest, query_terms: frozenset[str]) -> SkillDiscoveryCandidate:
    searchable_terms = _terms(f"{manifest.name} {manifest.description}")
    matched_terms = sorted(query_terms & searchable_terms)
    score = float(len(matched_terms))
    if manifest.name.lower() in query_terms:
        score += 2.0

    # "eligible for meta-discovery" is implied by list membership (a candidate
    # only exists here because ``_eligible`` already passed), so it carries zero
    # per-candidate signal — omit it and surface only the genuine query-match
    # reason when present.
    reasons: list[str] = []
    if matched_terms:
        reasons.append(f"query matched: {', '.join(matched_terms)}")

    return SkillDiscoveryCandidate(
        path=str(path),
        name=manifest.name,
        description=manifest.description,
        score=score,
        reasons=tuple(reasons),
        risk_warnings=_risk_warnings(manifest),
        risk_level=manifest.risk_level,
        argument_hint=manifest.argument_hint,
    )


def _risk_warnings(manifest: SkillManifest) -> tuple[str, ...]:
    warnings: list[str] = []
    if manifest.risk_level in {"high", "critical"}:
        warnings.append(f"risk level {manifest.risk_level}")
    if manifest.requires_verification:
        warnings.append("requires verification")
    if manifest.strict_execution:
        warnings.append("strict execution constraints declared")
    if manifest.forbidden_tools:
        warnings.append(f"forbidden tools declared: {', '.join(manifest.forbidden_tools)}")
    return tuple(warnings)


def _terms(text: str) -> frozenset[str]:
    return frozenset(token.lower() for token in _QUERY_TOKEN_RE.findall(text))
