"""PRD integrity helpers for overlap detection and PRD snapshots."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

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
    shared_paths: set[str]


@dataclass(slots=True)
class _PrdScanResult:
    snapshots: list[_PrdSnapshot]
    scanned_files: int
    skipped_files: int
    truncated: bool


_DUPLICATE_SCAN_MAX_FILES = 1500
_DUPLICATE_SCAN_MAX_SECONDS = 1.5
_DUPLICATE_SCAN_MAX_BYTES = 512_000
_FRONTMATTER_FIELD_RE = re.compile(r"^\s*(id|title|status):\s*(.+?)\s*$", re.MULTILINE)


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
    scan = _scan_prd_snapshots(prds_dir, current_paths=current_paths)
    for snapshot in scan.snapshots:
        if snapshot.prd_id == current_id or snapshot.status == "deprecated":
            continue

        title_similarity = _jaccard_similarity(current_title_tokens, _title_tokens(snapshot.title))
        shared_paths = sorted(snapshot.shared_paths)

        if title_similarity >= 0.75 or (title_similarity >= 0.45 and shared_paths) or len(shared_paths) >= 2:
            reasons: list[str] = []
            if title_similarity >= 0.45:
                reasons.append(f"title similarity {title_similarity:.2f}")
            if shared_paths:
                shared_preview = ", ".join(f"`{path}`" for path in shared_paths[:3])
                reasons.append(f"shared control points {shared_preview}")
            reason_text = "; ".join(reasons) if reasons else "overlapping scope"
            warnings.append(f"Potential overlap with {snapshot.prd_id}: {reason_text}.")

    if scan.truncated:
        warnings.append(
            "Duplicate overlap scan was truncated "
            f"after {scan.scanned_files} PRDs ({scan.skipped_files} skipped); "
            "run an offline catalogue audit for exhaustive overlap detection."
        )

    return warnings


def _scan_prd_snapshots(prds_dir: Path, *, current_paths: set[str]) -> _PrdScanResult:
    entries: list[_PrdSnapshot] = []
    directories = [prds_dir, prds_dir.parent / "archive" / "prds"]
    started = time.monotonic()
    scanned_files = 0
    skipped_files = 0
    truncated = False
    for directory in directories:
        if not directory.exists():
            continue
        for prd_file in sorted(directory.glob("PRD-*.md")):
            if scanned_files >= _DUPLICATE_SCAN_MAX_FILES or time.monotonic() - started > _DUPLICATE_SCAN_MAX_SECONDS:
                truncated = True
                break
            scanned_files += 1
            try:
                if prd_file.stat().st_size > _DUPLICATE_SCAN_MAX_BYTES:
                    skipped_files += 1
                    continue
                content = prd_file.read_text(encoding="utf-8")
            except OSError:
                logger.debug("prd_integrity_snapshot_skip", path=str(prd_file), reason="read_failed")
                skipped_files += 1
                continue

            prd_id, title, status = _extract_snapshot_fields(content, fallback_id=prd_file.stem)
            entries.append(
                _PrdSnapshot(
                    prd_id=prd_id,
                    title=title,
                    status=status,
                    shared_paths={path for path in current_paths if path in content},
                )
            )
        if truncated:
            break
    return _PrdScanResult(entries, scanned_files, skipped_files, truncated)


def _extract_snapshot_fields(content: str, *, fallback_id: str) -> tuple[str, str, str]:
    """Extract the duplicate-scan fields without YAML parsing or body logging.

    Duplicate detection is advisory and runs on the MCP validation hot path.
    Parsing every historical PRD's YAML frontmatter produced noisy debug logs
    and made validation cost scale with catalogue quirks. A tolerant regex
    pass over the frontmatter window is enough for overlap warnings.
    """

    frontmatter = _frontmatter_window(content)
    fields: dict[str, str] = {}
    for match in _FRONTMATTER_FIELD_RE.finditer(frontmatter):
        fields[match.group(1)] = match.group(2).strip().strip("\"'")
    return (
        fields.get("id", fallback_id).strip(),
        fields.get("title", "").strip(),
        fields.get("status", "draft").lower().strip(),
    )


def _frontmatter_window(content: str) -> str:
    if not content.startswith("---"):
        return content[:4096]
    marker = content.find("\n---", 3)
    if marker == -1:
        return content[:4096]
    return content[3:marker]


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
