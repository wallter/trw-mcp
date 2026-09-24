"""PRD-QUAL-143 FR03/FR04: each canon rule is stated fully once, in its home.

FR03: a scan over bundled canon/skills/agents/surfaces, ``.claude/skills``, and
``docs/documentation`` asserts that each rule's exact normative sentence
(whitespace-normalized) occurs only in its home file. Any other hit is a
restatement unless it matches an entry in ``ALLOWED_POINTERS`` (a short pointer
that *names* the home without restating the rule).

FR04: ``audit-framework.md`` holds the single P0-P3 -> critical/warning/info
severity table, and ``_review_helpers._compute_verdict`` reads only
critical/warning to decide block/warn/pass.

Baseline-red evidence (quoted in the READY message) is captured by running
this file BEFORE the accompanying canon edits landed; see the implementer's
final report for the per-row failure summary.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

# Anything shipped inside the trw-mcp package (docstrings, bundled data) is
# addressed from PACKAGE_ROOT so this scan still runs in the public layout,
# where the repo root IS the package root. REPO_ROOT is only used for paths
# that exist solely in the monorepo (.claude/skills, docs/documentation);
# it is None in the public layout and those rows/tests are requires_monorepo.
REPO_ROOT = MONOREPO_ROOT
TRW_MCP_SRC = PACKAGE_ROOT / "src" / "trw_mcp"
# Placeholder base for constructing monorepo-only Path objects at module load
# time without crashing collection when REPO_ROOT is None — the rows/tests
# built from it are always requires_monorepo, so the phony path is never read.
_MONOREPO_ROOT_OR_PACKAGE = REPO_ROOT if REPO_ROOT is not None else PACKAGE_ROOT

# ---------------------------------------------------------------------------
# Scan set
# ---------------------------------------------------------------------------

SCAN_ROOTS: tuple[Path, ...] = (
    TRW_MCP_SRC / "data" / "framework.md",
    TRW_MCP_SRC / "data" / "surfaces",
    TRW_MCP_SRC / "data" / "skills",
    TRW_MCP_SRC / "data" / "agents",
    *((REPO_ROOT / ".claude" / "skills", REPO_ROOT / "docs" / "documentation") if REPO_ROOT is not None else ()),
    TRW_MCP_SRC / "tools" / "learning.py",
    TRW_MCP_SRC / "tools" / "_review_provenance.py",
    TRW_MCP_SRC / "state" / "validation" / "phase_gates.py",
)

# Generated mirrors: byte-for-byte copies produced by tooling, not hand
# restatements. sync-instruction-surfaces.py mirrors data/surfaces/*.md into
# docs/documentation/*.md of the same name (PRD-QUAL-104 S1 flipped bundled
# dir -> canonical, docs/ -> generated mirror). A whole-file generated mirror
# is not a "second copy" in the FR03 sense; exclude it from the scan.
STATIC_GENERATED_MIRRORS: frozenset[Path] = frozenset(
    {REPO_ROOT / "docs" / "documentation" / "memory-routing.md"} if REPO_ROOT is not None else set()
)

# .claude/skills/<name>/** whose <name> also exists under the bundled data
# dir is a client mirror synced by check_client_mirrors.py — a whole-file
# build artifact, not an authored restatement. The three repo-only review
# skills (plus the _shared dir R2-029/030 lives in) are NOT bundled and stay
# in scope.
CLAUDE_SKILLS_DIR = REPO_ROOT / ".claude" / "skills" if REPO_ROOT is not None else None
BUNDLED_SKILLS_DIR = TRW_MCP_SRC / "data" / "skills"
REPO_ONLY_CLAUDE_SKILLS = frozenset({"_shared", "trw-a11y-check", "trw-visual-review", "trw-copy-review"})


def _is_bundled_skill_mirror(path: Path) -> bool:
    if CLAUDE_SKILLS_DIR is None or not path.is_relative_to(CLAUDE_SKILLS_DIR):
        return False
    relative = path.relative_to(CLAUDE_SKILLS_DIR)
    top = relative.parts[0] if relative.parts else ""
    if top in REPO_ONLY_CLAUDE_SKILLS:
        return False
    return (BUNDLED_SKILLS_DIR / top).is_dir()


def _iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if root.is_file():
            files.append(root)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            if path.is_file():
                files.append(path)
        for path in root.rglob("*.py"):
            if path.is_file():
                files.append(path)
    return [f for f in files if f not in STATIC_GENERATED_MIRRORS and not _is_bundled_skill_mirror(f)]


def normalize(text: str) -> str:
    """Collapse whitespace (incl. newlines) and strip markdown/comment markers."""
    # Strip a leading '#' (Python/YAML/shell comment) or '//' (JS/Rust) marker
    # per line FIRST, so a multi-line comment block normalizes the same as the
    # equivalent prose would — otherwise the marker survives whitespace
    # collapsing and sits mid-sentence ("enforces # most of the strings").
    lines = [re.sub(r"^\s*(#|//)\s?", "", line) for line in text.splitlines()]
    joined = "\n".join(lines)
    # Strip markdown emphasis/bold/code markers consistently before matching so
    # a rewrap or a `**bold**` vs plain difference doesn't hide a real duplicate.
    stripped = re.sub(r"[*_`]", "", joined)
    return re.sub(r"\s+", " ", stripped).strip()


def _count_occurrences(haystack: str, needle: str) -> int:
    if not needle:
        return 0
    return haystack.count(needle)


# ---------------------------------------------------------------------------
# RULES: (row_id, home_path, exact normative sentence)
# ---------------------------------------------------------------------------

RULES: list[tuple[str, Path, str]] = [
    (
        "R2-006/007",
        TRW_MCP_SRC / "data" / "surfaces" / "memory-routing.md",
        "Prefer `trw_learn()` for durable engineering discoveries that should be available across TRW sessions.",
    ),
    (
        "R2-008",
        TRW_MCP_SRC / "tools" / "learning.py",
        "supersedes (prior id; closes its window)",
    ),
    (
        "R2-034",
        TRW_MCP_SRC / "data" / "surfaces" / "memory-routing.md",
        "Keep one authoritative record per material fact: update or link existing knowledge "
        "rather than maintaining competing copies.",
    ),
    (
        "R2-005",
        TRW_MCP_SRC / "tools" / "_review_provenance.py",
        "A caller-supplied identity alone would be a self-mintable receipt",
    ),
    (
        "R2-004",
        TRW_MCP_SRC / "data" / "framework.md",
        "Low-delta stop: two consecutive research/implement iterations with materially no new "
        "findings or unchanged results SHOULD trigger an immediate re-plan or DELIVER, not more "
        "iteration.",
    ),
    (
        "R2-033",
        TRW_MCP_SRC / "data" / "framework.md",
        "`trw_build_check(tests_passed, test_count, failure_count, static_checks_clean, scope)` "
        "— record observed project-native validation at VALIDATE and before DELIVER after "
        "code/test changes; it does not run checks",
    ),
    (
        "R2-016",
        TRW_MCP_SRC / "data" / "framework.md",
        "Multi-reviewer pass: at least `ceil(2n/3)` reviewers/checks support pass, with every "
        "critical dissent resolved explicitly.",
    ),
    (
        "R2-017",
        TRW_MCP_SRC / "data" / "skills" / "trw-audit" / "audit-framework.md",
        "Code carrying a `# trw:intentional <reason>` (or `// trw:intentional <reason>`) comment "
        "on or just above the flagged line records a settled, deliberate decision a prior "
        "reviewer or auditor already litigated",
    ),
    (
        "R2-018",
        TRW_MCP_SRC / "data" / "framework.md",
        "Bundled helpers use the narrower MCP policy for a failed or unavailable `trw_*` call: "
        "retry once, then record the skipped ceremony step loudly before continuing.",
    ),
    (
        "R2-019",
        TRW_MCP_SRC / "data" / "skills" / "trw-test-strategy" / "SKILL.md",
        "Do not call `trw_build_check` from this read-only audit: return the evidence to the "
        "owning orchestrator, which may record it only when it satisfies the active validation "
        "plan.",
    ),
    (
        "R2-032",
        TRW_MCP_SRC / "data" / "framework.md",
        "A safe switch point is the nearest point where checkpointed state and observable side "
        "effects agree — not necessarily the end of the current shard or task.",
    ),
    (
        "R2-002",
        TRW_MCP_SRC / "state" / "validation" / "phase_gates.py",
        "Entries without a checker are display-only: nothing in this module enforces most of "
        "the strings below against run state, so treat an unimplemented criterion as "
        "documentation of intent, not a gate.",
    ),
    (
        "R2-003",
        TRW_MCP_SRC / "data" / "framework.md",
        "The Dynamic Research >30% `open_questions` threshold (see PHASES) is "
        "agent-estimated, not machine-computed — no tool counts `open_questions` "
        "for you.",
    ),
    (
        "R2-029/030",
        _MONOREPO_ROOT_OR_PACKAGE / ".claude" / "skills" / "_shared" / "review-triage.md",
        "Surface a finding whenever you notice it, even one you are unsure survives scrutiny or judge minor",
    ),
    (
        "R2-031",
        _MONOREPO_ROOT_OR_PACKAGE / ".claude" / "skills" / "trw-visual-review" / "SKILL.md",
        "The auth flow only — `brand-green #3ECF8E` / `glass-elevated` / `rounded-2xl`; do not "
        "apply Sovereign Console rules there",
    ),
    (
        "FR04-severity-table",
        TRW_MCP_SRC / "data" / "skills" / "trw-audit" / "audit-framework.md",
        "The 4-to-3 collapse (P2 and P3 both land on `info`) is deliberate",
    ),
]

# These two rows' homes only exist in the monorepo (.claude/skills is not
# shipped in the public trw-mcp package); the other rows are package-owned
# and keep running in the public layout.
_MONOREPO_ONLY_ROW_IDS = frozenset({"R2-029/030", "R2-031"})
RULE_PARAMS = [
    pytest.param(*row, marks=requires_monorepo) if row[0] in _MONOREPO_ONLY_ROW_IDS else row for row in RULES
]

# ---------------------------------------------------------------------------
# ALLOWED_POINTERS: (path, pointer text) — names the home, does not restate it
# ---------------------------------------------------------------------------

ALLOWED_POINTERS: list[tuple[Path, str]] = [
    (
        TRW_MCP_SRC / "data" / "agents" / "trw-reviewer.md",
        "P0-P3 checklist priority maps to severity via the one table in `audit-framework.md` Section H2",
    ),
    (
        TRW_MCP_SRC / "data" / "agents" / "trw-reviewer.md",
        "see `audit-framework.md` Section H1 for the bar",
    ),
    (
        TRW_MCP_SRC / "data" / "agents" / "trw-auditor.md",
        "**Respect the `trw:intentional` marker** — see `audit-framework.md` Section H1 for the bar",
    ),
    (
        _MONOREPO_ROOT_OR_PACKAGE / ".claude" / "skills" / "trw-a11y-check" / "SKILL.md",
        "Triage and completeness rules: [`_shared/review-triage.md`](../_shared/review-triage.md).",
    ),
    (
        _MONOREPO_ROOT_OR_PACKAGE / ".claude" / "skills" / "trw-visual-review" / "SKILL.md",
        "Triage and completeness rules: [`_shared/review-triage.md`](../_shared/review-triage.md).",
    ),
    (
        _MONOREPO_ROOT_OR_PACKAGE / ".claude" / "skills" / "trw-copy-review" / "SKILL.md",
        "[`_shared/review-triage.md`](../_shared/review-triage.md) — triage and report-completeness rules",
    ),
]


def _normalized_file_cache() -> dict[Path, str]:
    cache: dict[Path, str] = {}
    for path in _iter_scan_files():
        cache[path] = normalize(path.read_text(encoding="utf-8", errors="replace"))
    return cache


def _outside_pointers(path: Path, content: str) -> str:
    """*content* with this file's allowed pointer spans cut out.

    A pointer names the home rule; only a hit INSIDE that span is legal. The same
    sentence anywhere else in a pointer file is still a restatement.
    """
    for pointer_path, text in ALLOWED_POINTERS:
        if pointer_path == path:
            content = content.replace(normalize(text), "\x00")
    return content


def test_a_restatement_outside_the_pointer_span_is_still_caught() -> None:
    path, text = ALLOWED_POINTERS[0]
    pointer = normalize(text)
    needle = pointer[: len(pointer) // 2]
    # Inside the pointer span: legal. The same words after it: a restatement.
    assert _count_occurrences(_outside_pointers(path, pointer), needle) == 0
    assert _count_occurrences(_outside_pointers(path, f"{pointer} Elsewhere: {needle}"), needle) == 1


@pytest.fixture(scope="module")
def normalized_files() -> dict[Path, str]:
    return _normalized_file_cache()


@pytest.mark.parametrize("row_id,home,sentence", RULE_PARAMS, ids=[r[0] for r in RULES])
def test_rule_stated_once(row_id: str, home: Path, sentence: str, normalized_files: dict[Path, str]) -> None:
    needle = normalize(sentence)
    assert needle, f"{row_id}: empty normalized sentence"

    home_norm = normalized_files.get(home)
    assert home_norm is not None, f"{row_id}: home file not found or unreadable: {home}"
    home_count = _count_occurrences(home_norm, needle)
    assert home_count == 1, f"{row_id}: defining sentence appears {home_count} times in home {home}, expected once"

    offenders = [
        path
        for path, content in normalized_files.items()
        if path != home and _count_occurrences(_outside_pointers(path, content), needle)
    ]

    assert not offenders, f"{row_id}: sentence restated outside home {home} in: {offenders}"


# Pointers rooted under .claude/skills are monorepo-only; the trw-mcp
# agents-md pointers ship in the package and keep running everywhere.
_ALLOWED_POINTER_PARAMS = [
    pytest.param(path, text, marks=requires_monorepo)
    if path.is_relative_to(_MONOREPO_ROOT_OR_PACKAGE / ".claude" / "skills")
    else (path, text)
    for path, text in ALLOWED_POINTERS
]


@pytest.mark.parametrize("path,text", _ALLOWED_POINTER_PARAMS)
def test_allowed_pointers_present_and_not_restating(path: Path, text: str, normalized_files: dict[Path, str]) -> None:
    """Every declared pointer actually exists in its file and is short (a name, not a restatement)."""
    content = normalized_files.get(path)
    assert content is not None, f"pointer file missing: {path}"
    assert normalize(text) in content, f"declared pointer text not found in {path}: {text!r}"


def test_r2_028_fw_qol_changes_absent() -> None:
    """R2-028 (verify, PRD disposition table): the retired span-id marker has zero hits.

    framework.source.md (and its 'fw-qol-changes' span-id convention break) was
    deleted entirely by worker-2/s4; framework.md is now hand-edited canon with
    no span-id markup at all, so the literal string cannot appear anywhere in
    the scan set.
    """
    hits = []
    for path in _iter_scan_files():
        content = path.read_text(encoding="utf-8", errors="replace")
        if "fw-qol-changes" in content:
            hits.append(path)
    assert hits == [], f"R2-028: retired span-id marker still present in: {hits}"


def test_negative_control_detects_a_synthetic_duplicate(tmp_path: Path) -> None:
    """Prove the scanner mechanism actually catches a second copy (not just a vacuous pass)."""
    home = tmp_path / "home.md"
    dup = tmp_path / "dup.md"
    clean = tmp_path / "clean.md"
    sentence = "The widget SHALL rotate exactly once per cycle."
    home.write_text(f"# Home\n\n{sentence}\n", encoding="utf-8")
    dup.write_text(f"# Duplicate\n\nSee also: {sentence}\n", encoding="utf-8")
    clean.write_text("# Unrelated\n\nNothing to see here.\n", encoding="utf-8")

    files = {
        home: normalize(home.read_text(encoding="utf-8")),
        dup: normalize(dup.read_text(encoding="utf-8")),
        clean: normalize(clean.read_text(encoding="utf-8")),
    }
    needle = normalize(sentence)
    offenders = [p for p, c in files.items() if p != home and needle in c]
    assert offenders == [dup], "negative control: scanner failed to detect the synthetic duplicate"


# ---------------------------------------------------------------------------
# FR04: severity mapping is total, and _compute_verdict reads exactly it
# ---------------------------------------------------------------------------

AUDIT_FRAMEWORK_MD = TRW_MCP_SRC / "data" / "skills" / "trw-audit" / "audit-framework.md"


def _parse_severity_table() -> dict[str, str]:
    text = AUDIT_FRAMEWORK_MD.read_text(encoding="utf-8")
    heading_idx = text.find("Section H2. Reviewer Severity Mapping")
    assert heading_idx != -1, "FR04: severity mapping heading not found in audit-framework.md"
    section_text = text[heading_idx:]
    next_heading_idx = section_text.find("\n## ", 1)
    if next_heading_idx != -1:
        section_text = section_text[:next_heading_idx]
    table_lines = [line.strip() for line in section_text.splitlines() if line.strip().startswith("|")]
    assert table_lines, "FR04: severity mapping table not found in audit-framework.md"
    # First row is the header, second is the '---' separator; the rest are data rows.
    data_rows = [row for row in table_lines[2:] if not set(row.replace("|", "").strip()) <= {"-", ":", " "}]
    assert data_rows, "FR04: severity mapping table has no data rows"

    mapping: dict[str, str] = {}
    for row in data_rows:
        cells = [c.strip().strip("`") for c in row.strip("|").split("|")]
        assert len(cells) == 2, f"FR04: malformed table row: {row!r}"
        priority, severity = cells
        assert priority not in mapping, f"FR04: duplicate priority row {priority!r}"
        mapping[priority] = severity
    return mapping


def test_severity_mapping_is_total() -> None:
    mapping = _parse_severity_table()

    assert set(mapping) == {"P0", "P1", "P2", "P3"}, f"FR04: every P-level must appear exactly once: {mapping}"
    assert mapping["P0"] == "critical"
    assert mapping["P1"] == "warning"
    assert mapping["P2"] == "info"
    assert mapping["P3"] == "info"
    assert set(mapping.values()) <= {"critical", "warning", "info"}, (
        f"FR04: severities must be literal critical/warning/info: {mapping}"
    )


def test_severity_mapping_drives_compute_verdict() -> None:
    from trw_mcp.tools._review_helpers import _compute_verdict

    mapping = _parse_severity_table()
    expected_verdict = {"critical": "block", "warning": "warn", "info": "pass"}

    for priority, severity in mapping.items():
        finding = {"severity": severity}
        verdict = _compute_verdict([finding])
        assert verdict == expected_verdict[severity], (
            f"FR04: {priority}->{severity} finding produced verdict {verdict!r}, "
            f"expected {expected_verdict[severity]!r}"
        )
