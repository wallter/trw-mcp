"""Pure f-string MDC template functions for Cursor IDE/CLI channels.

No I/O. No Jinja2. No datetime.now() calls. All functions are deterministic:
same inputs always produce same output. Timestamps come from caller (sidecar ts).

PRD-DIST-2401 Phase A.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ConventionRecord",
    "EdgeCaseRecord",
    "HotspotRecord",
    "assemble_mdc_frontmatter",
    "derive_directory_glob",
    "dir_slug",
    "render_conventions_t0",
    "render_conventions_t1",
    "render_dangerous_edits_t0",
    "render_dangerous_edits_t1",
    "render_hotspot_dir_t0",
    "render_hotspot_dir_t1",
    "render_presence_beacon_mdc",
    "render_tombstone_mdc",
    "validate_mdc_frontmatter",
    "validate_minimatch_glob",
]

# ---------------------------------------------------------------------------
# Record dataclasses (simple, typed payloads from sidecar)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConventionRecord:
    slug: str
    title: str
    body: str


@dataclass(frozen=True)
class HotspotRecord:
    file_path: str
    risk_score: float
    reason: str = ""


@dataclass(frozen=True)
class EdgeCaseRecord:
    file_path: str
    description: str
    survived: bool = False


# ---------------------------------------------------------------------------
# dir_slug — kebab-case slug from directory path, max 60 chars
# ---------------------------------------------------------------------------

_NON_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def dir_slug(directory_path: str, max_chars: int = 60) -> str:
    """Convert a directory path to a kebab-case slug (max 60 chars).

    Replaces '/', '_', '.', and other non-alphanumeric chars with '-'.
    Lowercases everything. Truncates to max_chars with trailing '-' removed.

    Examples:
        'trw-mcp/src/trw_mcp/state' -> 'trw-mcp-src-trw-mcp-state'
        'backend/routers' -> 'backend-routers'
    """
    normalized = directory_path.replace("/", "-").replace("_", "-").replace(".", "-").lower()
    cleaned = _NON_SLUG_RE.sub("-", normalized)
    # Remove leading/trailing dashes and collapse multiple dashes
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip("-")
    return cleaned


# ---------------------------------------------------------------------------
# derive_directory_glob — convert file path to directory minimatch pattern
# ---------------------------------------------------------------------------


def derive_directory_glob(file_path: str) -> str:
    """Derive a minimatch directory glob from a file path.

    'backend/routers/admin.py' -> 'backend/routers/**/*.py'
    'src/foo.ts' -> 'src/**/*.ts'
    'foo.py' -> '**/*.py'
    """
    parts = file_path.replace("\\", "/").rsplit("/", 1)
    if len(parts) == 1:
        # No directory component — use project-wide glob for the extension
        fname = parts[0]
    else:
        fname = parts[1]

    # Extract extension
    if "." in fname:
        ext = fname.rsplit(".", 1)[1]
        ext_glob = f"*.{ext}"
    else:
        ext_glob = "*"

    if len(parts) == 1:
        return f"**/{ext_glob}"
    directory = parts[0]
    return f"{directory}/**/{ext_glob}"


# ---------------------------------------------------------------------------
# validate_minimatch_glob (FR14 / P0-12)
# ---------------------------------------------------------------------------

_LITERAL_FILE_RE = re.compile(r"\.[a-zA-Z0-9]{1,10}$")


def validate_minimatch_glob(pattern: str) -> tuple[bool, str]:
    """Check that *pattern* is a minimatch directory pattern, not a literal path.

    Accepts: '**/*.py', 'trw-mcp/src/**/*.py', 'backend/routers/**/*.py',
             'docs/**/*.{md,mdx}', 'src/**/*.{ts,tsx}'
    Rejects: 'backend/routers/admin.py' (literal file path with extension)

    Returns:
        (valid, reason) where reason is "" on success.
    """
    stripped = pattern.strip()

    # A pattern containing '**' is always a minimatch glob, not a literal path.
    if "**" in stripped:
        return True, ""

    # A pattern containing '{...}' is a brace expansion — valid minimatch.
    if "{" in stripped and "}" in stripped:
        return True, ""

    # Check if the last segment looks like a literal file (has a plain extension
    # without wildcards, e.g. 'admin.py' vs '*.py')
    last_segment = stripped.rsplit("/", 1)[-1]
    if "*" not in last_segment and _LITERAL_FILE_RE.search(last_segment):
        return False, "literal file path — use directory pattern instead"

    return True, ""


# ---------------------------------------------------------------------------
# validate_mdc_frontmatter (FR13)
# ---------------------------------------------------------------------------

_ALLOWED_FM_KEYS = frozenset({"description", "globs", "alwaysApply"})


def validate_mdc_frontmatter(content: str) -> tuple[bool, str]:
    """Parse and validate the YAML frontmatter in an MDC content string.

    Rules:
    - Must start with '---'
    - Must close with '---' on its own line
    - Allowed keys: description, globs, alwaysApply only (no extras)
    - description: string (empty string allowed for tombstones)
    - alwaysApply: must be 'true' or 'false' (boolean)
    - globs: string or '[]' (empty list)

    Returns:
        (valid, reason) where reason is "" on success.
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return False, "frontmatter must start with ---"

    end_idx: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return False, "frontmatter not closed with ---"

    fm_lines = lines[1:end_idx]
    parsed: dict[str, str] = {}
    for line in fm_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            return False, f"invalid frontmatter line: {line!r}"
        key, _, val = line.partition(":")
        key = key.strip()
        parsed[key] = val.strip()

    unknown = set(parsed.keys()) - _ALLOWED_FM_KEYS
    if unknown:
        return False, f"unknown key: {next(iter(unknown))}"

    if "alwaysApply" in parsed and parsed["alwaysApply"] not in ("true", "false"):
        return False, f"alwaysApply must be boolean, got: {parsed['alwaysApply']!r}"

    return True, ""


# ---------------------------------------------------------------------------
# assemble_mdc_frontmatter — assemble strict Cursor frontmatter block
# ---------------------------------------------------------------------------


def assemble_mdc_frontmatter(
    *,
    description: str,
    globs: str | list[str],
    always_apply: bool = False,
) -> str:
    """Assemble strict Cursor MDC frontmatter.

    Only the three allowed fields are emitted: description, globs, alwaysApply.
    No extra keys. Provenance goes AFTER the closing ---.
    """
    always_apply_str = "true" if always_apply else "false"

    if isinstance(globs, list):
        if not globs:
            globs_str = "[]"
        else:
            globs_str = ", ".join(globs)
    else:
        globs_str = globs

    return (
        f"---\n"
        f"description: {description}\n"
        f"globs: {globs_str}\n"
        f"alwaysApply: {always_apply_str}\n"
        f"---\n"
    )


# ---------------------------------------------------------------------------
# T0 beacon / tombstone renderers
# ---------------------------------------------------------------------------


def render_presence_beacon_mdc(channel_id: str, regenerate_cmd: str) -> str:
    """Render a T0 presence beacon MDC (minimal, under 200 bytes body)."""
    fm = assemble_mdc_frontmatter(
        description="TRW distill data available — quota exceeded, use trw_codebase_risk_report() for full analysis",
        globs=[],
        always_apply=False,
    )
    return f"{fm}\n<!-- T0 beacon: {channel_id} | regenerate: {regenerate_cmd} -->\n"


def render_tombstone_mdc(channel_id: str, regenerate_cmd: str, reason: str) -> str:
    """Render a staleness tombstone MDC.

    globs: [] and description: "" to prevent agent pull-in (P2-08 fix).
    """
    fm = assemble_mdc_frontmatter(
        description="",
        globs=[],
        always_apply=False,
    )
    return (
        f"{fm}\n"
        f"<!-- TRW distill rules are stale. Regenerate: {regenerate_cmd} -->\n"
        f"<!-- channel: {channel_id} | reason: {reason} -->\n"
    )


# ---------------------------------------------------------------------------
# T0 per-channel beacons
# ---------------------------------------------------------------------------


def render_conventions_t0(channel_id: str = "cursor-mdc-conventions") -> str:
    return render_presence_beacon_mdc(
        channel_id,
        "trw-distill self-improve mdc-emit",
    )


def render_hotspot_dir_t0(directory: str, channel_id: str = "cursor-mdc-hotspots") -> str:
    slug = dir_slug(directory)
    return render_presence_beacon_mdc(
        f"{channel_id}-{slug}",
        "trw-distill self-improve mdc-emit",
    )


def render_dangerous_edits_t0(channel_id: str = "cursor-mdc-dangerous-edits") -> str:
    return render_presence_beacon_mdc(
        channel_id,
        "trw-distill self-improve mdc-emit",
    )


# ---------------------------------------------------------------------------
# T1 conventions
# ---------------------------------------------------------------------------


def render_conventions_t1(
    records: list[ConventionRecord],
    hotspots: list[HotspotRecord],
    sha: str,
    ts: str,
    channel_id: str = "cursor-mdc-conventions",
) -> str:
    """Render T1 conventions MDC file."""
    fm = assemble_mdc_frontmatter(
        description="TRW distill: project coding conventions and hotspot risk summary",
        globs=[],
        always_apply=False,
    )

    provenance = _inline_provenance(channel_id, sha, ts)

    # Sort hotspots: risk_score desc, file_path asc
    sorted_hotspots = sorted(hotspots, key=lambda h: (-h.risk_score, h.file_path))

    convention_section = ""
    if records:
        rows = "\n".join(
            f"| `{r.slug}` | {r.title} |" for r in records
        )
        convention_section = (
            "\n## Conventions\n\n"
            "| Slug | Title |\n"
            "|---|---|\n"
            f"{rows}\n"
        )
        for rec in records:
            convention_section += f"\n### {rec.title}\n\n{rec.body}\n"

    hotspot_section = ""
    if sorted_hotspots:
        rows = "\n".join(
            f"| `{h.file_path}` | {h.risk_score:.2f} | {h.reason} |"
            for h in sorted_hotspots[:10]
        )
        hotspot_section = (
            "\n## High-Risk Files\n\n"
            "| File | Risk | Reason |\n"
            "|---|---|---|\n"
            f"{rows}\n"
        )

    heading = "# TRW Distill — Conventions"
    return f"{fm}\n{heading}\n\n{provenance}\n{convention_section}{hotspot_section}"


# ---------------------------------------------------------------------------
# T1 hotspot per-directory
# ---------------------------------------------------------------------------


def render_hotspot_dir_t1(
    directory: str,
    records: list[HotspotRecord],
    edge_cases: list[EdgeCaseRecord],
    sha: str,
    ts: str,
    channel_id: str = "cursor-mdc-hotspots",
) -> str:
    """Render T1 per-directory hotspot MDC file."""
    glob_pattern = f"{directory}/**/*.py" if not directory.endswith("/**/*.py") else directory

    # Validate the glob before using it
    valid, _reason = validate_minimatch_glob(glob_pattern)
    if not valid:
        # Derive a safe fallback
        glob_pattern = derive_directory_glob(f"{directory}/file.py")

    fm = assemble_mdc_frontmatter(
        description=f"TRW distill hotspot warnings for {directory}/",
        globs=glob_pattern,
        always_apply=False,
    )

    slug = dir_slug(directory)
    provenance = _inline_provenance(f"{channel_id}-{slug}", sha, ts)

    sorted_records = sorted(records, key=lambda h: (-h.risk_score, h.file_path))

    file_rows = "\n".join(
        f"| `{r.file_path}` | {r.risk_score:.2f} | {r.reason} |"
        for r in sorted_records
    ) if sorted_records else "_no high-risk files_"

    ec_section = ""
    if edge_cases:
        ec_rows = "\n".join(
            f"| `{e.file_path}` | {e.description[:80]} |"
            for e in edge_cases[:5]
        )
        ec_section = (
            "\n## Edge Cases\n\n"
            "| File | Description |\n"
            "|---|---|\n"
            f"{ec_rows}\n"
        )

    heading = f"# TRW Distill — Hotspots: {directory}/"
    file_table = (
        "## High-Risk Files\n\n"
        "| File | Risk | Reason |\n"
        "|---|---|---|\n"
        f"{file_rows}\n"
    )

    return f"{fm}\n{heading}\n\n{provenance}\n\n{file_table}{ec_section}"


# ---------------------------------------------------------------------------
# T1 dangerous edits
# ---------------------------------------------------------------------------


def render_dangerous_edits_t1(
    survivors: list[EdgeCaseRecord],
    undocumented: list[EdgeCaseRecord],
    sha: str,
    ts: str,
    channel_id: str = "cursor-mdc-dangerous-edits",
) -> str:
    """Render T1 dangerous-edits MDC file."""
    all_files = [e.file_path for e in survivors + undocumented]
    glob_pattern = _derive_glob_set(all_files)

    fm = assemble_mdc_frontmatter(
        description="TRW distill: survivor patterns and undocumented public symbol traps",
        globs=glob_pattern,
        always_apply=False,
    )

    provenance = _inline_provenance(channel_id, sha, ts)

    survivor_section = ""
    if survivors:
        rows = "\n".join(
            f"| `{e.file_path}` | {e.description[:80]} |" for e in survivors
        )
        survivor_section = (
            "\n## Survivor Patterns\n\n"
            f"_{len(survivors)} pattern(s) that survived review_\n\n"
            "| File | Description |\n"
            "|---|---|\n"
            f"{rows}\n"
        )

    undoc_section = ""
    if undocumented:
        rows = "\n".join(
            f"| `{e.file_path}` | {e.description[:80]} |" for e in undocumented
        )
        undoc_section = (
            "\n## Undocumented Traps\n\n"
            f"_{len(undocumented)} undocumented public symbol(s)_\n\n"
            "| File | Description |\n"
            "|---|---|\n"
            f"{rows}\n"
        )

    heading = "# TRW Distill — Dangerous Edits"
    return f"{fm}\n{heading}\n\n{provenance}\n{survivor_section}{undoc_section}"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _inline_provenance(channel_id: str, sha: str, ts: str) -> str:
    """Single-line provenance comment for T1 content (P2-06: after heading)."""
    regen = "trw-distill self-improve mdc-emit"
    return f"<!-- TRW:PROVENANCE channel_id={channel_id} sha={sha[:8]} ts={ts} regenerate={regen} -->"


def _derive_glob_set(file_paths: list[str]) -> str:
    """Derive a directory-level glob set covering all files without catch-all.

    Groups files by directory and produces patterns like
    'backend/routers/**/*.py, trw-mcp/src/**/*.ts'.
    Falls back to '**/*.py' only if files span more than 8 directories.
    """
    if not file_paths:
        return "[]"

    dirs: dict[str, set[str]] = {}
    for fp in file_paths:
        parts = fp.replace("\\", "/").rsplit("/", 1)
        directory = parts[0] if len(parts) > 1 else ""
        fname = parts[-1]
        ext = fname.rsplit(".", 1)[-1] if "." in fname else "*"
        dirs.setdefault(directory, set()).add(ext)

    if len(dirs) > 8:
        # Too many dirs — use a limited catch-all pattern
        return "**/*.py"

    patterns = []
    for directory, exts in sorted(dirs.items()):
        for ext in sorted(exts):
            if directory:
                patterns.append(f"{directory}/**/*.{ext}")
            else:
                patterns.append(f"**/*.{ext}")

    return ", ".join(patterns)
