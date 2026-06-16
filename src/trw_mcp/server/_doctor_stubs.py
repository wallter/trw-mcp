"""``trw-mcp doctor`` stubs-section helpers (PRD residual — stub visibility).

Belongs to the ``_subcommands_doctor.py`` facade: it owns the heavy lifting for
the ``stubs`` diagnostic check so the parent file stays under the 350
effective-LOC gate. ``_subcommands_doctor._check_stubs`` is the thin wrapper
that wires these into the doctor catalogue.

The section is strictly read-only and advisory:
  - It scans the project's PRD catalogue (``docs/requirements-aare-f/prds`` when
    present) for frontmatter ``functionality_level: stub|partial`` carrying a
    non-empty ``stubs:`` list or a ``status: implemented`` (a shipped-but-partial
    surface), reporting the count and the top few PRD IDs.
  - It greps the project's own source trees (``*/src``) for
    ``raise NotImplementedError`` in non-test files, reporting the count and the
    first few locations.

Fail-open: a project without a PRD catalogue and without source ``raise
NotImplementedError`` sites reports SKIP (advisory only). Any unexpected error
in one half degrades that half to an empty result rather than aborting.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Frontmatter levels that mark a PRD as not-fully-functional.
_STUB_LEVELS: frozenset[str] = frozenset({"stub", "partial"})

# Directories never descended into when grepping source for NotImplementedError.
_GREP_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".git",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".trw",
        "tests",
        "test",
    }
)

# Source extensions scanned for raise NotImplementedError.
_SOURCE_SUFFIXES: frozenset[str] = frozenset({".py"})

# Anchored at the start of the STRIPPED line so a genuine ``raise
# NotImplementedError`` statement matches but a docstring / regex-literal / prose
# mention of the phrase (which never begins the code line) does not. This keeps
# the advisory count honest rather than inflating it with self-references.
_NOT_IMPLEMENTED_RE = re.compile(r"^raise\s+NotImplementedError\b")

# Bound the surfaced detail so the doctor row stays readable.
_MAX_PRD_IDS = 5
_MAX_LOCATIONS = 5


def _prd_catalogue_dir(target: Path, prds_relative_path: str) -> Path | None:
    """Resolve the PRD catalogue directory under ``target``, or None if absent."""
    candidate = (target / prds_relative_path).resolve()
    try:
        candidate.relative_to(target.resolve())
    except ValueError:
        return None
    if candidate.is_dir():
        return candidate
    return None


def _is_partial_prd(frontmatter: dict[str, object]) -> bool:
    """True iff this PRD frontmatter marks a stub/partial surface worth flagging.

    The relevant fields live under the ``prd:`` mapping in this repo's PRDs but
    are tolerated at top level too (defensive). A PRD qualifies when:
      - ``functionality_level`` is ``stub`` or ``partial``, AND
      - it carries a non-empty ``stubs:`` list OR ``status: implemented``
        (a shipped-but-partial surface — the residual we want visible).
    """
    block = frontmatter.get("prd")
    fields: dict[str, object] = block if isinstance(block, dict) else {}
    # Top-level fallback for either field if not found under prd:.
    level = fields.get("functionality_level", frontmatter.get("functionality_level"))
    if not isinstance(level, str) or level.strip().lower() not in _STUB_LEVELS:
        return False
    stubs = fields.get("stubs", frontmatter.get("stubs"))
    has_stubs = isinstance(stubs, list) and len(stubs) > 0
    status = fields.get("status", frontmatter.get("status"))
    is_implemented = isinstance(status, str) and status.strip().lower() == "implemented"
    return has_stubs or is_implemented


def _prd_id(frontmatter: dict[str, object], fallback: str) -> str:
    """Extract the PRD id from frontmatter, falling back to the file stem."""
    block = frontmatter.get("prd")
    if isinstance(block, dict):
        pid = block.get("id")
        if isinstance(pid, str) and pid.strip():
            return pid.strip()
    top = frontmatter.get("id")
    if isinstance(top, str) and top.strip():
        return top.strip()
    return fallback


def scan_partial_prds(catalogue: Path) -> list[str]:
    """Return the sorted PRD ids flagged stub/partial in ``catalogue`` (recursive).

    Each ``.md`` file is parsed independently; a parse failure on one file is
    logged and skipped rather than aborting the scan.
    """
    from trw_mcp.state.prd_utils import parse_frontmatter

    flagged: list[str] = []
    for md in sorted(catalogue.rglob("*.md")):
        try:
            content = md.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
        except (OSError, UnicodeDecodeError, ValueError):
            logger.debug("doctor_stubs_prd_unreadable", path=str(md))
            continue
        if _is_partial_prd(fm):
            flagged.append(_prd_id(fm, md.stem))
    return sorted(set(flagged))


def _source_roots(target: Path) -> list[Path]:
    """Return the project's own ``*/src`` directories under ``target``."""
    roots: list[Path] = [src for src in sorted(target.glob("*/src")) if src.is_dir()]
    # A flat single-package layout (src/ directly under target) is also valid.
    flat = target / "src"
    if flat.is_dir() and flat not in roots:
        roots.append(flat)
    return roots


def scan_not_implemented(target: Path) -> list[str]:
    """Return ``relpath:lineno`` for each ``raise NotImplementedError`` in source.

    Test files and the excluded vendor/cache directories are skipped. Paths are
    relative to ``target`` for readability.
    """
    hits: list[str] = []
    base = target.resolve()
    for root in _source_roots(target):
        for path in sorted(root.rglob("*")):
            if path.suffix not in _SOURCE_SUFFIXES or not path.is_file():
                continue
            if any(part in _GREP_EXCLUDE_DIRS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _NOT_IMPLEMENTED_RE.match(line.strip()):
                    try:
                        rel = path.resolve().relative_to(base)
                    except ValueError:
                        rel = path
                    hits.append(f"{rel}:{lineno}")
    return hits


def build_stubs_message(
    target: Path,
    prds_relative_path: str,
) -> tuple[str, str]:
    """Build ``(status, message)`` for the stubs doctor check.

    Returns one of:
      - ``("SKIP", ...)`` when no PRD catalogue exists AND no source
        NotImplementedError sites are found — a clean / non-PRD project.
      - ``("WARN", ...)`` when any stub/partial PRD or NotImplementedError site
        is found (advisory; WARN never fails the doctor run).
      - ``("PASS", ...)`` when a PRD catalogue exists but reports zero
        stub/partial PRDs and zero NotImplementedError sites.
    """
    catalogue = _prd_catalogue_dir(target, prds_relative_path)
    partial_ids = scan_partial_prds(catalogue) if catalogue is not None else []
    ni_hits = scan_not_implemented(target)

    if catalogue is None and not ni_hits:
        return (
            "SKIP",
            "no PRD catalogue and no source NotImplementedError sites detected — advisory only.",
        )

    parts: list[str] = []
    if catalogue is not None:
        if partial_ids:
            top = ", ".join(partial_ids[:_MAX_PRD_IDS])
            more = "" if len(partial_ids) <= _MAX_PRD_IDS else f" (+{len(partial_ids) - _MAX_PRD_IDS} more)"
            parts.append(f"{len(partial_ids)} stub/partial PRD(s): {top}{more}")
        else:
            parts.append("0 stub/partial PRDs")
    if ni_hits:
        top_loc = ", ".join(ni_hits[:_MAX_LOCATIONS])
        more = "" if len(ni_hits) <= _MAX_LOCATIONS else f" (+{len(ni_hits) - _MAX_LOCATIONS} more)"
        parts.append(f"{len(ni_hits)} raise NotImplementedError site(s): {top_loc}{more}")

    status = "WARN" if (partial_ids or ni_hits) else "PASS"
    return status, "; ".join(parts)
