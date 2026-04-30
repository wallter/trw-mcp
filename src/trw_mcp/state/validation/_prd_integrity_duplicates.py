"""PRD integrity helpers for overlap detection and PRD snapshots."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.state.prd_utils import parse_frontmatter
from trw_mcp.state.validation._prd_integrity_paths import _extract_repo_path_refs

logger = structlog.get_logger(__name__)

_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TITLE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "from",
        "in",
        "of",
        "on",
        "the",
        "to",
        "with",
        "via",
        "spec",
        "docs",
        "document",
        "framework",
        "gates",
    }
)


@dataclass(slots=True)
class _PrdSnapshot:
    prd_id: str
    title: str
    status: str
    path_refs: set[str]


def _check_duplicate_candidates(
    content: str,
    frontmatter: dict[str, object],
    project_root: Path,
    prds_relative_path: str,
) -> list[str]:
    prds_dir = project_root / prds_relative_path
    if not prds_dir.exists():
        return []

    current_id = str(frontmatter.get("id", "")).strip()
    current_title_tokens = _title_tokens(str(frontmatter.get("title", "")))
    current_paths = set(_extract_repo_path_refs(content))
    if not current_title_tokens and not current_paths:
        return []

    warnings: list[str] = []
    for snapshot in _scan_prd_snapshots(prds_dir):
        if snapshot.prd_id == current_id or snapshot.status == "deprecated":
            continue

        title_similarity = _jaccard_similarity(current_title_tokens, _title_tokens(snapshot.title))
        shared_paths = sorted(current_paths & snapshot.path_refs)

        if title_similarity >= 0.75 or (title_similarity >= 0.45 and shared_paths) or len(shared_paths) >= 2:
            reasons: list[str] = []
            if title_similarity >= 0.45:
                reasons.append(f"title similarity {title_similarity:.2f}")
            if shared_paths:
                shared_preview = ", ".join(f"`{path}`" for path in shared_paths[:3])
                reasons.append(f"shared control points {shared_preview}")
            reason_text = "; ".join(reasons) if reasons else "overlapping scope"
            warnings.append(f"Potential overlap with {snapshot.prd_id}: {reason_text}.")

    return warnings


def _scan_prd_snapshots(prds_dir: Path) -> list[_PrdSnapshot]:
    entries: list[_PrdSnapshot] = []
    directories = [prds_dir, prds_dir.parent / "archive" / "prds"]
    for directory in directories:
        if not directory.exists():
            continue
        for prd_file in sorted(directory.glob("PRD-*.md")):
            try:
                content = prd_file.read_text(encoding="utf-8")
            except OSError:
                logger.debug("prd_integrity_snapshot_skip", path=str(prd_file), reason="read_failed")
                continue

            frontmatter = parse_frontmatter(content)
            prd_id = str(frontmatter.get("id", prd_file.stem)).strip()
            title = str(frontmatter.get("title", "")).strip()
            status = str(frontmatter.get("status", "draft")).lower().strip()
            entries.append(
                _PrdSnapshot(
                    prd_id=prd_id,
                    title=title,
                    status=status,
                    path_refs=set(_extract_repo_path_refs(content)),
                )
            )
    return entries


def _title_tokens(title: str) -> set[str]:
    return {
        token for token in _TITLE_TOKEN_RE.findall(title.lower()) if len(token) > 2 and token not in _TITLE_STOPWORDS
    }


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)
