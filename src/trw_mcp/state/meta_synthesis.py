"""Meta-layer artifact synthesis -- meta.yaml and emergent skills.

PRD-CORE-109: Produces layered overlays per C-2 constraint.
meta.yaml output is versioned by model_family and trw_version per C-5.

This module handles:
- .trw/meta.yaml generation and merging
- Skill file generation from learning clusters (triple gate)
- Team sync report generation
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

_logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# C-3: Only these 6 adaptive knobs are allowed in overlay adjustments
# ---------------------------------------------------------------------------

ALLOWED_KNOBS: frozenset[str] = frozenset(
    {
        "surface_intensity",
        "surface_timing",
        "evidence_burden",
        "wording_style",
        "review_strictness",
        "withholding_rate",
    }
)

# ---------------------------------------------------------------------------
# Default base profile values
# ---------------------------------------------------------------------------

_DEFAULT_BASE_PROFILE: dict[str, Any] = {
    "surface_intensity": 3,
    "withholding_rate": 0.15,
}


# ---------------------------------------------------------------------------
# meta.yaml synthesis
# ---------------------------------------------------------------------------


def _load_existing_meta(meta_path: Path) -> dict[str, Any] | None:
    """Load existing meta.yaml safely, returning None if corrupt or missing."""
    if not meta_path.exists():
        return None

    try:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        data = yaml.load(meta_path)
        if not isinstance(data, dict):
            _logger.warning("meta_yaml_invalid_type", path=str(meta_path))
            return None
        return data
    except Exception:
        _logger.warning("meta_yaml_parse_error", path=str(meta_path), exc_info=True)
        return None


def _compute_sensitive_paths(
    learnings: list[dict[str, Any]],
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Compute sensitive_paths from incident-type learnings.

    A path is sensitive if it has >3 incident learnings anchored to it.
    """
    path_counts: dict[str, int] = {}

    for entry in learnings:
        if entry.get("type") != "incident":
            continue
        for anchor in entry.get("anchors", []):
            file_path = str(anchor.get("file", ""))
            if file_path:
                path_counts[file_path] = path_counts.get(file_path, 0) + 1

    result: list[dict[str, Any]] = []
    for path, count in sorted(path_counts.items()):
        if count >= 3:
            result.append({"path": path, "incident_count": count})

    return result


def _compute_fast_paths(
    learnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute fast_paths from learnings with consistently positive outcomes."""
    path_outcomes: dict[str, list[str]] = {}

    for entry in learnings:
        outcome = entry.get("outcome_correlation")
        if not outcome:
            continue
        for anchor in entry.get("anchors", []):
            file_path = str(anchor.get("file", ""))
            if file_path:
                path_outcomes.setdefault(file_path, []).append(str(outcome))

    result: list[dict[str, Any]] = []
    for path, outcomes in sorted(path_outcomes.items()):
        positive_count = sum(
            1 for o in outcomes if o in ("positive", "strong_positive")
        )
        rate = positive_count / len(outcomes) if outcomes else 0
        if rate >= 0.5:
            result.append(
                {"path": path, "positive_outcome_rate": round(rate, 2)}
            )

    return result


def _compute_domain_map(
    clusters: list[dict[str, Any]],
    existing_domain_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build domain map from clusters, preserving manual entries."""
    domain_map: dict[str, Any] = {}

    # Preserve manual entries from existing data
    if existing_domain_map:
        for key, value in existing_domain_map.items():
            if isinstance(value, dict) and value.get("manual"):
                domain_map[key] = value

    # Add cluster-derived domains
    for cluster in clusters:
        slug = cluster.get("domain_slug", "")
        if slug and slug not in domain_map:
            cluster_learnings = cluster.get("learnings", [])
            domain_map[slug] = {
                "learning_count": len(cluster_learnings),
                "description": f"Auto-detected domain: {slug}",
            }

    return domain_map


def synthesize_meta_yaml(
    trw_dir: Path,
    *,
    learnings: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    config: dict[str, Any],
) -> Path:
    """Generate or update .trw/meta.yaml with all required sections.

    C-2: Uses layered overlays (base_profile, overlays, quarantined).
    C-3: Only 6 adaptive knobs allowed in overlay adjustments.
    C-5: All overlays tagged with model_family and trw_version.

    Returns the path to the generated meta.yaml file.
    """
    meta_path = trw_dir / "meta.yaml"
    model_family = str(config.get("model_family", "unknown"))
    trw_version = str(config.get("trw_version", "unknown"))
    now_iso = datetime.now(timezone.utc).isoformat()

    # Load existing data if present
    existing = _load_existing_meta(meta_path)
    if existing is None:
        existing = {}
        if meta_path.exists():
            _logger.warning(
                "meta_yaml_recreated",
                reason="corrupt or invalid existing file",
            )

    # Upgrade legacy format: add 'meta' key if missing
    if "meta" not in existing:
        existing["meta"] = {
            "base_profile": dict(_DEFAULT_BASE_PROFILE),
            "overlays": [],
            "quarantined": [],
        }

    meta_block = existing["meta"]
    if "base_profile" not in meta_block:
        meta_block["base_profile"] = dict(_DEFAULT_BASE_PROFILE)
    if "overlays" not in meta_block:
        meta_block["overlays"] = []
    if "quarantined" not in meta_block:
        meta_block["quarantined"] = []

    # Ensure current model_family has an overlay
    overlays = meta_block["overlays"]
    has_current = any(
        o.get("model_family") == model_family for o in overlays
    )
    if not has_current:
        overlays.append(
            {
                "model_family": model_family,
                "trw_version": trw_version,
                "adjustments": {},
            }
        )

    # Compute data sections
    existing_domain_map = existing.get("domain_map")
    if isinstance(existing_domain_map, dict):
        domain_map = _compute_domain_map(clusters, existing_domain_map)
    else:
        domain_map = _compute_domain_map(clusters)

    output: dict[str, Any] = {
        "meta": meta_block,
        "sensitive_paths": _compute_sensitive_paths(learnings),
        "fast_paths": _compute_fast_paths(learnings),
        "domain_map": domain_map if domain_map else {},
        "last_tune_date": now_iso,
    }

    # Preserve any extra top-level keys from existing file
    for key in existing:
        if key not in output:
            output[key] = existing[key]

    # Atomic write: write to temp file, then rename
    from ruamel.yaml import YAML

    yaml_writer = YAML(typ="safe")
    yaml_writer.default_flow_style = False

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(trw_dir),
        suffix=".yaml.tmp",
        delete=False,
    ) as tmp_file:
        yaml_writer.dump(output, tmp_file)
        tmp_path = Path(tmp_file.name)

    tmp_path.rename(meta_path)

    _logger.info(
        "meta_yaml_synthesized",
        path=str(meta_path),
        model_family=model_family,
        trw_version=trw_version,
    )

    return meta_path


# ---------------------------------------------------------------------------
# Skill generation (triple gate)
# ---------------------------------------------------------------------------

_DEFAULT_MIN_SESSIONS = 10
_DEFAULT_CAUSAL_LIFT_THRESHOLD = 0.1
_DEFAULT_ANCHOR_VALIDITY_THRESHOLD = 0.67


def generate_skill(
    cluster: dict[str, Any],
    trw_dir: Path,
    *,
    min_sessions: int = _DEFAULT_MIN_SESSIONS,
    causal_lift_threshold: float = _DEFAULT_CAUSAL_LIFT_THRESHOLD,
    anchor_validity_threshold: float = _DEFAULT_ANCHOR_VALIDITY_THRESHOLD,
) -> Path | None:
    """Generate a SKILL.md file for a cluster that passes the triple gate.

    Triple gate:
    1. Exposure gate: cluster exposure >= min_sessions
    2. Causal lift gate: cluster causal_lift >= causal_lift_threshold
    3. Code stability gate: avg_anchor_validity >= anchor_validity_threshold

    Returns the path to the generated skill file, or None if the gate fails.

    NFR03: Only summary and nudge_line fields are used, never detail.
    """
    exposure = int(cluster.get("exposure", 0))
    causal_lift = float(cluster.get("causal_lift", 0.0))
    avg_anchor_validity = float(cluster.get("avg_anchor_validity", 0.0))
    domain_slug = str(cluster.get("domain_slug", "unknown"))

    # Gate 1: Exposure
    if exposure < min_sessions:
        _logger.info(
            "skill_gate_fail_exposure",
            domain=domain_slug,
            exposure=exposure,
            required=min_sessions,
        )
        return None

    # Gate 2: Causal lift
    if causal_lift < causal_lift_threshold:
        _logger.info(
            "skill_gate_fail_causal_lift",
            domain=domain_slug,
            causal_lift=causal_lift,
            required=causal_lift_threshold,
        )
        return None

    # Gate 3: Code stability (anchor validity)
    if avg_anchor_validity < anchor_validity_threshold:
        _logger.info(
            "skill_gate_fail_anchor_validity",
            domain=domain_slug,
            avg_anchor_validity=avg_anchor_validity,
            required=anchor_validity_threshold,
        )
        return None

    # Gate 4: Promotion safety gate (PRD-CORE-108-FR04)
    # Require at least half of member learnings to pass the promotion gate
    cluster_learnings = cluster.get("learnings", [])
    if cluster_learnings:
        try:
            from trw_mcp.scoring.attribution.promotion import check_promotion_gate

            pass_count = 0
            for member in cluster_learnings:
                result = check_promotion_gate(member)
                if result.passed:
                    pass_count += 1
            if pass_count < len(cluster_learnings) / 2:
                _logger.info(
                    "skill_gate_fail_promotion",
                    domain=domain_slug,
                    pass_count=pass_count,
                    total=len(cluster_learnings),
                )
                return None
        except ImportError:
            _logger.debug("promotion_gate_unavailable", domain=domain_slug)

    # All gates passed -- generate skill
    skill_dir = trw_dir / "skills" / domain_slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"

    cluster_learnings = cluster.get("learnings", [])
    now_iso = datetime.now(timezone.utc).isoformat()

    # Build source learning IDs for reference
    source_ids = [str(l.get("id", "unknown")) for l in cluster_learnings]

    # Build guidance from summaries only (never detail -- NFR03)
    guidance_lines: list[str] = []
    for learning in cluster_learnings:
        summary = str(learning.get("summary", ""))
        nudge = str(learning.get("nudge_line", ""))
        text = nudge if nudge else summary
        if text:
            guidance_lines.append(f"- {text}")

    content = f"""---
name: {domain_slug}
description: "Auto-generated skill for {domain_slug} domain"
auto_generated: true
generated_at: "{now_iso}"
source_learnings: {source_ids}
model: sonnet
---

# {domain_slug.replace("-", " ").title()}

This skill was auto-generated by the meta-tune synthesis process (PRD-CORE-109).
It consolidates {len(cluster_learnings)} learnings from the {domain_slug} domain.

## Guidance

{chr(10).join(guidance_lines) if guidance_lines else "No guidance available yet."}

## Source Learnings

{chr(10).join(f"- `mcp.trw.recall(id={sid})`" for sid in source_ids)}
"""

    skill_path.write_text(content)

    _logger.info(
        "skill_generated",
        domain=domain_slug,
        path=str(skill_path),
        learning_count=len(cluster_learnings),
    )

    return skill_path


# ---------------------------------------------------------------------------
# Team sync report
# ---------------------------------------------------------------------------


def generate_team_sync_report(
    report: Any,  # MetaTuneReport (avoid circular import)
) -> str:
    """Generate a human-readable team sync report from a MetaTuneReport.

    Args:
        report: MetaTuneReport instance with steps, totals, and warnings.

    Returns:
        Human-readable summary string.
    """
    lines: list[str] = [
        "=" * 60,
        "META-TUNE SYNTHESIS REPORT",
        "=" * 60,
        "",
        f"Total actions taken: {report.total_actions}",
        f"Learnings demoted:  {report.learnings_demoted}",
        f"Hypotheses resolved: {report.hypotheses_resolved}",
        f"Clusters detected:  {report.clusters_detected}",
        f"Skills generated:   {report.skills_generated}",
        "",
        "-" * 40,
        "Per-Step Status",
        "-" * 40,
    ]

    for step in report.steps:
        status_marker = {
            "ok": "[OK]",
            "skipped": "[SKIPPED]",
            "error": "[ERROR]",
        }.get(step.status, f"[{step.status.upper()}]")

        line = f"  {status_marker} {step.step}: {step.actions_taken} actions"
        if step.details:
            line += f" -- {step.details}"
        lines.append(line)

    if report.warnings:
        lines.append("")
        lines.append("-" * 40)
        lines.append("Warnings")
        lines.append("-" * 40)
        for warning in report.warnings:
            lines.append(f"  - {warning}")

    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)
