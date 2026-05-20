"""Read-only skill meta-discovery helpers for PRD-CORE-170."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.models.skill_manifest import (
    IssueSeverity,
    SkillManifest,
    SkillManifestIssue,
    SkillValidationMode,
    validate_skill_markdown,
)

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
) -> SkillDiscoveryResult:
    """Return ranked eligible skills from SKILL.md paths without executing them."""

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

        candidates.append(_candidate(skill_path, validation.manifest, query_terms))

    ranked = tuple(sorted(candidates, key=lambda candidate: (-candidate.score, candidate.name, candidate.path)))
    return SkillDiscoveryResult(candidates=ranked, warnings=tuple(warnings), executed=False)


def register_skill_discovery_tools(server: FastMCP) -> None:
    """Register read-only skill discovery MCP tools."""

    @server.tool(output_schema=None)
    def trw_skill_discovery(
        skill_paths: list[str],
        query: str,
        mode: SkillValidationMode = "compat",
        include_private: bool = False,
    ) -> dict[str, object]:
        """Rank eligible SKILL.md files without executing them.

        Use when an agent needs safe skill recommendations from explicit
        SKILL.md paths before invoking any workflow.

        Args:
            skill_paths: Explicit SKILL.md paths to inspect.
            query: Natural-language search terms.
            mode: Manifest validation mode, either "compat" or "strict".
            include_private: Include non-user-invocable skills when true.

        Returns:
            {"candidates": list, "warnings": list, "executed": false}
        """

        return discover_meta_skills(
            skill_paths,
            query=query,
            mode=mode,
            include_private=include_private,
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

    reasons = ["eligible for meta-discovery"]
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
