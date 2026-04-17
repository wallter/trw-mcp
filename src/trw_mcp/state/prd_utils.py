"""Shared PRD utility functions — parsing, extraction, and frontmatter updates.

Extracted from tools/requirements.py (PRD-FIX-006) to provide shared
infrastructure for PRD-CORE-007, CORE-008, CORE-009, and CORE-010.

All functions are pure or file-scoped — no MCP tool registration side effects.
"""

from __future__ import annotations

import os
import re
import tempfile
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from trw_mcp.exceptions import StateError
from trw_mcp.models.requirements import VALID_TRANSITIONS as VALID_TRANSITIONS
from trw_mcp.models.requirements import PRDStatus

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

# Compiled regex patterns (module-level for performance)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_SECTION_HEADING_RE = re.compile(r"^##\s+\d+\.\s+(.+)$", re.MULTILINE)
_PRD_REF_RE = re.compile(r"PRD-[A-Z]+-\d{3}")

# Patterns for non-substantive lines in content density calculation
_NON_SUBSTANTIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*$"),  # blank lines
    re.compile(r"^\s*---\s*$"),  # horizontal rules
    re.compile(r"^\s*<!--.*?-->\s*$"),  # HTML comments (single-line)
    re.compile(r"^\s*\|[\s\-:|]+\|\s*$"),  # table separator rows
    re.compile(r"^\s*#"),  # heading lines
]


def parse_frontmatter(content: str) -> dict[str, object]:
    """Parse YAML frontmatter from markdown content.

    Extracts the YAML block between ``---`` delimiters at the start
    of the document. If a nested ``prd`` key is found, its contents
    are flattened to the top level (AARE-F convention).

    Args:
        content: Markdown content with optional YAML frontmatter.

    Returns:
        Parsed frontmatter as a dict, or empty dict if none found.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}

    # Use safe loader to prevent RCE via !!python/object tags in user-supplied PRD files.
    yaml = YAML(typ="safe")
    try:
        data = yaml.load(match.group(1))
        if isinstance(data, dict):
            # Flatten nested 'prd' key if present (AARE-F template nests under 'prd')
            if "prd" in data and isinstance(data["prd"], dict):
                prd_data: dict[str, object] = {
                    **data["prd"],
                    **{k: v for k, v in data.items() if k != "prd"},
                }
                return prd_data
            return dict(data)
    except (YAMLError, ValueError, TypeError, AttributeError) as exc:
        logger.debug("frontmatter_parse_failed", error=str(exc))
    return {}


def extract_sections(content: str) -> list[str]:
    """Extract ``## N. Section Name`` headings from PRD markdown.

    Only matches numbered section headings (e.g. ``## 1. Problem Statement``),
    not unnumbered headings like ``## Appendix`` or ``### Subsection``.

    Args:
        content: Markdown content.

    Returns:
        List of section heading names found (without the ``## N.`` prefix).
    """
    return _SECTION_HEADING_RE.findall(content)


def compute_content_density(content: str) -> float:
    """Calculate the ratio of substantive content lines to total lines.

    Non-substantive lines include: blank lines, horizontal rules (``---``),
    HTML comment placeholders (``<!-- ... -->``), table separator rows,
    and heading lines. Everything else is considered substantive.

    Args:
        content: Markdown content string.

    Returns:
        Float between 0.0 and 1.0. Returns 0.0 for empty content.
    """
    lines = content.split("\n")
    total = len(lines)
    if total == 0:
        return 0.0

    substantive = sum(1 for line in lines if not any(p.match(line) for p in _NON_SUBSTANTIVE_PATTERNS))
    return substantive / total


def extract_prd_refs(content: str) -> list[str]:
    """Extract PRD references from markdown content.

    Scans for the pattern ``PRD-{CATEGORY}-{NNN}`` (e.g. ``PRD-CORE-007``,
    ``PRD-FIX-006``). Returns a deduplicated, sorted list of matched IDs.

    Args:
        content: Markdown content to scan.

    Returns:
        Sorted list of unique PRD reference IDs found.
    """
    matches = _PRD_REF_RE.findall(content)
    return sorted(set(matches))


def update_frontmatter(path: Path, updates: dict[str, object]) -> None:
    """Update YAML frontmatter fields in a PRD file using round-trip YAML.

    Preserves existing formatting, comments, and key ordering for all
    non-updated fields. Updates are merged into the ``prd`` sub-key
    if present, otherwise into the top-level mapping.

    Args:
        path: Path to the PRD markdown file.
        updates: Dictionary of fields to update (e.g. ``{"status": "approved"}``).

    Raises:
        StateError: If the file does not exist or has no frontmatter.
    """
    if not path.exists():
        raise StateError(f"PRD file not found: {path}", path=str(path))

    content = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise StateError(f"No YAML frontmatter found in: {path}", path=str(path))

    # SECURITY: Use safe loader even for update — never parse untrusted YAML with
    # the round-trip loader. Round-trip is only used for the final dump (write-only).
    yaml_safe = YAML(typ="safe")
    fm_text = match.group(1)
    try:
        data = yaml_safe.load(fm_text)
    except Exception as exc:  # justified: boundary, wrap unknown I/O errors as StateError
        raise StateError(f"Failed to update frontmatter: {exc}", path=str(path)) from exc

    if not isinstance(data, dict):
        raise StateError(f"Frontmatter is not a mapping in: {path}", path=str(path))

    try:
        # Determine target dict: nested 'prd' key or top-level
        prd_val = data.get("prd")
        target = prd_val if isinstance(prd_val, dict) else data

        # Apply updates (support nested dicts via recursive merge)
        _deep_merge(target, updates)

        # Serialize updated frontmatter (round-trip for write formatting only)
        yaml_writer = YAML()
        yaml_writer.default_flow_style = False
        stream = StringIO()
        yaml_writer.dump(data, stream)
        new_fm = stream.getvalue()

        # Reconstruct file: new frontmatter + original body
        body = content[match.end() :]

        # FR02 (PRD-FIX-056): Sync prose Quick Reference status line when status changes
        if "status" in updates:
            new_status = str(updates["status"])
            body = re.sub(
                r"(- \*\*Status\*\*:\s*)(\w+)",
                lambda m: m.group(1) + new_status.capitalize(),
                body,
                count=1,
            )

        new_content = f"---\n{new_fm}---{body}"

        # Atomic write: write to temp, then rename
        tmp_fd, tmp_path_str = tempfile.mkstemp(dir=str(path.parent), suffix=".md.tmp")
        tmp_path = Path(tmp_path_str)
        try:
            os.close(tmp_fd)
            tmp_path.write_text(new_content, encoding="utf-8")
            tmp_path.rename(path)
        except Exception:  # justified: cleanup, temp file removal must not mask original error
            tmp_path.unlink(missing_ok=True)
            raise

        logger.info("frontmatter_updated", path=str(path), fields=list(updates.keys()))

    except Exception as exc:  # justified: boundary, wrap unknown I/O errors as StateError
        raise StateError(f"Failed to update frontmatter: {exc}", path=str(path)) from exc


# VALID_TRANSITIONS moved to models/requirements.py (next to PRDStatus enum)
# Re-imported above for backward compatibility.


def is_valid_transition(current: PRDStatus, target: PRDStatus) -> bool:
    """Check if a PRD status transition is valid per the state machine.

    Identity transitions (same state → same state) are always valid.

    Args:
        current: Current PRD status.
        target: Desired PRD status.

    Returns:
        True if the transition is allowed.
    """
    if current == target:
        return True
    return target in VALID_TRANSITIONS.get(current, set())


class TransitionResult(BaseModel):
    """Result of a PRD status transition attempt."""

    allowed: bool
    reason: str = ""
    guard_details: dict[str, object] = Field(default_factory=dict)


def check_transition_guards(
    current: PRDStatus,
    target: PRDStatus,
    prd_content: str,
    config: TRWConfig | None = None,
) -> TransitionResult:
    """Run guard checks for a PRD status transition.

    Guards:
    - DRAFT → REVIEW: content density must be >= prd_min_content_density
    - REVIEW → APPROVED: validate_prd_quality_v2 must classify >= REVIEW tier

    Other transitions have no guards and always pass.

    Args:
        current: Current PRD status.
        target: Desired PRD status.
        prd_content: Full PRD markdown content.
        config: Optional TRWConfig for threshold overrides.

    Returns:
        TransitionResult indicating whether guards passed.
    """
    from trw_mcp.models.config import get_config as _get_config
    from trw_mcp.state.validation.prd_status import validate_status_transition

    _config = config or _get_config()

    # Identity transition — no guard needed
    if current == target:
        return TransitionResult(allowed=True, reason="Identity transition (no-op).")

    # FR03 (PRD-FIX-056): Validate against the canonical state machine first
    if not validate_status_transition(current.value, target.value):
        return TransitionResult(
            allowed=False,
            reason=f"Transition '{current.value}' → '{target.value}' is not permitted by the state machine.",
            guard_details={"current": current.value, "target": target.value},
        )

    # PRD-QUAL-013: Apply risk-scaled config from frontmatter
    from trw_mcp.models.requirements import QualityTier
    from trw_mcp.state.validation import (
        derive_risk_level,
        get_risk_scaled_config,
        validate_prd_quality_v2,
    )

    fm = parse_frontmatter(prd_content)
    fm_priority = str(fm.get("priority", "P2"))
    fm_risk = fm.get("risk_level")
    effective_risk = derive_risk_level(fm_priority, str(fm_risk) if fm_risk else None)
    _config = get_risk_scaled_config(_config, effective_risk)

    # Guard: DRAFT → REVIEW — content density check
    if current == PRDStatus.DRAFT and target == PRDStatus.REVIEW:
        density = compute_content_density(prd_content)
        threshold = _config.prd_min_content_density
        density_details: dict[str, object] = {
            "density": density,
            "threshold": threshold,
            "risk_level": effective_risk,
        }
        if density < threshold:
            return TransitionResult(
                allowed=False,
                reason=f"Content density {density:.2f} is below threshold {threshold:.2f}.",
                guard_details=density_details,
            )
        return TransitionResult(
            allowed=True,
            reason="Content density check passed.",
            guard_details=density_details,
        )

    # Guard: REVIEW → APPROVED — V2 quality validation
    if current == PRDStatus.REVIEW and target == PRDStatus.APPROVED:
        result = validate_prd_quality_v2(prd_content, _config)
        quality_details: dict[str, object] = {
            "total_score": result.total_score,
            "quality_tier": result.quality_tier.value,
            "grade": result.grade,
            "risk_level": effective_risk,
        }
        if result.quality_tier in (QualityTier.SKELETON, QualityTier.DRAFT):
            return TransitionResult(
                allowed=False,
                reason=f"Quality tier '{result.quality_tier.value}' (score {result.total_score}) "
                f"is below REVIEW tier required for approval.",
                guard_details=quality_details,
            )
        return TransitionResult(
            allowed=True,
            reason="Quality validation passed.",
            guard_details=quality_details,
        )

    # FR06 (PRD-FIX-056): Warn on null approved_by for terminal transitions
    _TERMINAL_TARGETS = {PRDStatus.DONE, PRDStatus.DEPRECATED}
    if target in _TERMINAL_TARGETS:
        approved_by = fm.get("approved_by")
        if not approved_by:
            return TransitionResult(
                allowed=True,
                reason="No guard for this transition.",
                guard_details={"approval_warning": "approved_by is null on terminal transition"},
            )

    # All other transitions have no guards
    return TransitionResult(allowed=True, reason="No guard for this transition.")


def discover_governing_prds(run_path: Path, config: TRWConfig | None = None) -> list[str]:
    """Identify governing PRDs for a run using three-tier discovery.

    Tier 1 (explicit): Read ``prd_scope`` from ``run.yaml``.
    Tier 2 (plan scanning): Scan ``reports/plan.md`` for PRD references.
    Tier 3 (advisory): Return empty list — caller emits advisory warning.

    Args:
        run_path: Path to the run directory.
        config: Optional TRWConfig (unused currently, reserved for future).

    Returns:
        Sorted list of unique PRD IDs governing this run. Empty if none found.
    """
    from trw_mcp.state.persistence import FileStateReader

    reader = FileStateReader()

    # Tier 1: Explicit prd_scope from run.yaml
    run_yaml = run_path / "meta" / "run.yaml"
    if run_yaml.exists():
        try:
            state = reader.read_yaml(run_yaml)
            prd_scope = state.get("prd_scope", [])
            if isinstance(prd_scope, list) and prd_scope:
                return sorted(str(p) for p in prd_scope)
        except (StateError, ValueError, TypeError) as exc:
            logger.debug("prd_scope_read_failed", path=str(run_yaml), error=str(exc))

    # Tier 2: Scan plan.md for PRD references
    plan_path = run_path / "reports" / "plan.md"
    if plan_path.exists():
        try:
            plan_content = plan_path.read_text(encoding="utf-8")
            refs = extract_prd_refs(plan_content)
            if refs:
                return refs
        except (OSError, ValueError) as exc:
            logger.debug("plan_scan_failed", path=str(plan_path), error=str(exc))

    # Tier 3: No PRDs found — return empty list
    return []


def _deep_merge(target: object, source: dict[str, object]) -> None:
    """Recursively merge source dict into target dict (in-place).

    Args:
        target: Target mapping to merge into.
        source: Source mapping with values to apply.
    """
    if not isinstance(target, dict):
        return
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def next_prd_sequence(prds_dir: Path, category: str) -> int:
    """Scan existing PRD files and return max sequence + 1 for a category.

    Scans both the active ``prds/`` directory and the sibling
    ``archive/prds/`` directory to avoid reusing IDs from archived PRDs.

    Args:
        prds_dir: Directory containing PRD markdown files.
        category: PRD category (e.g., "CORE", "FIX").

    Returns:
        Next available sequence number (minimum 1).
    """
    prefix = f"PRD-{category}-"
    sequences: list[int] = []

    # Scan active and archived PRD directories
    dirs_to_scan = [prds_dir, prds_dir.parent / "archive" / "prds"]
    for scan_dir in dirs_to_scan:
        if not scan_dir.exists():
            continue
        for prd_file in scan_dir.glob("*.md"):
            name = prd_file.stem
            if name.startswith(prefix):
                try:
                    sequences.append(int(name[len(prefix) :]))
                except ValueError:
                    continue

    return max(sequences, default=0) + 1
