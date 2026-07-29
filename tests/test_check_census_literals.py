"""The census-data lint must catch a copied count and never a design ceiling.

``scripts/check_census_literals.py`` (PRD-INFRA-174) reports a literal number
asserted against a population the repository can enumerate. Six such literals
were fixed by hand on 2026-07-24; the worst was an auto-loaded ``.claude/rules``
file claiming *tools/* held 17 files against an actual 140, for 133 days.

The precision half matters as much as the detection half. A rule of the form
"flag every literal near a count" reports 111 findings on the measured corpus of
which 108 are correct code, and a check at 2.7% precision is suppressed within a
week — leaving the appearance of coverage, which is the exact failure this lint
exists to close. So every protected class is asserted absent by name, and every
detection is paired with a PLANTED VIOLATION proving it fails for the right
reason. :func:`test_every_check_has_a_planted_violation` fails when one is
missing.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = REPO_ROOT / "scripts"
_CLI = _SCRIPTS / "check_census_literals.py"

# Monorepo-only invariant: the repo-root scripts/ layout is absent from the
# standalone trw-mcp mirror. Skip cleanly there.
if not _CLI.is_file():
    pytest.skip("monorepo-only invariant (repo-root scripts/ absent in mirror)", allow_module_level=True)


def _load(name: str, path: Path, package_dir: Path | None = None) -> Any:
    """Load a repo-root script/package WITHOUT putting scripts/ on ``sys.path``.

    Inserting the repo-root ``scripts/`` directory onto ``sys.path`` would make
    every module in it importable under its bare name for the rest of the
    session, including a ``scripts/tests`` namespace portion. Loading by
    location keeps the blast radius at this module — the same reason
    ``test_agent_contract_lint.py`` does it this way.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        name,
        path,
        submodule_search_locations=[str(package_dir)] if package_dir else None,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so relative imports and dataclass annotations resolve.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


census = _load("census", _SCRIPTS / "census" / "__init__.py", _SCRIPTS / "census")
Kind = census.Kind
Measure = census.Measure
Rooting = census.Rooting
Verdict = census.Verdict
classify = census.classify

REAL_SCOPE = census.load_scope(_SCRIPTS / "census-scope.yaml")


# ──────────────────────────────────────────────────────────────────────────────
# Fixture-tree helpers
# ──────────────────────────────────────────────────────────────────────────────


def _scan(tmp_path: Path, files: dict[str, str]) -> census.ScanReport:
    """Scan a throwaway tree carrying the given files, with the real vocabulary."""
    for rel, body in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    scope = dataclasses.replace(REAL_SCOPE, python_trees=("pkg",), markdown_globs=("rules/*.md",))
    return census.run_scan(scope, tmp_path)


def _kinds(report: census.ScanReport) -> list[str]:
    return [f.kind.value for f in report.findings]


#: A repo-rooted census assertion: the root is bound INSIDE the function, which
#: is the shape the PRD's prototype measurably missed.
_FUNCTION_LOCAL_ROOT = """
from pathlib import Path

def test_bundle() -> None:
    root = Path(__file__).resolve().parents[2]
    mirrors = sorted(root.glob("data/skills/*/SKILL.md"))
    assert len(mirrors) >= 7
"""

_MODULE_LEVEL_ROOT = """
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[2] / "data" / "agents"

def _agent_files() -> list[Path]:
    return sorted(AGENTS_DIR.glob("*.md"))

def test_count() -> None:
    bundled = [p for p in _agent_files() if p.name != "dev.md"]
    assert len(bundled) == 12
"""

_FIXTURE_ROOTED = """
from pathlib import Path

def test_writes(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x")
    entries = sorted(tmp_path.glob("*.md"))
    assert len(entries) == 1
"""

_PER_MEMBER_BUDGET = """
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[2] / "data" / "agents"

def test_budget() -> None:
    authored = (AGENTS_DIR / "trw-adversarial-auditor.md").read_text()
    assert len(authored.split()) <= 500
    assert len(authored.split()) <= 900
"""

_ZERO_GUARD = """
from pathlib import Path

PROFILES = Path(__file__).resolve().parents[2] / "data" / "profiles"

def test_nonempty() -> None:
    count = sum(1 for p in PROFILES.iterdir() if p.suffix == ".yaml")
    assert count > 0, "no profiles discovered"
"""

_INTERNAL_ROSTER = """
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"

def live_names() -> set[str]:
    return {p.stem for p in (DATA / "tools").glob("*.py")}

EXPECTED = frozenset({"a", "b", "c"})

def test_roster() -> None:
    assert live_names() == EXPECTED
    assert len(EXPECTED) == 3
"""

_STALE_RULE = """---
paths: ["trw-mcp/**/*.py"]
---

- `tools/` — 17 tool files grouped: ceremony, learning, orchestration
- `state/` — 28 modules: persistence, validation, analytics
"""

_CORRECTED_RULE = """---
paths: ["trw-mcp/**/*.py"]
---

- `tools/` — grouped by domain: ceremony, learning, orchestration
- `state/` — persistence, validation, analytics, recall_search
- sentinel-backed: <!-- inv:tools -->46<!-- /inv --> tools

```text
17 tool files listed here are sample OUTPUT, not a claim
```
"""


# ──────────────────────────────────────────────────────────────────────────────
# FR11 — every check is paired with a planted violation
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlantedViolation:
    """A seeded defect the check MUST report, and the label it must carry."""

    requirement: str
    what_it_proves: str
    files: dict[str, str]
    expect_kind: Kind
    expect_detail: str


PLANTED: dict[str, PlantedViolation] = {
    "python_function_local_root": PlantedViolation(
        requirement="FR02",
        what_it_proves=(
            "a root bound inside the test FUNCTION is resolved — the prototype "
            "resolved module scope only and found 2 of 3 true candidates"
        ),
        files={"pkg/test_a.py": _FUNCTION_LOCAL_ROOT},
        expect_kind=Kind.CENSUS_LITERAL,
        expect_detail="literal 7 compared '>='",
    ),
    "python_module_level_root": PlantedViolation(
        requirement="FR02",
        what_it_proves="a root reached through a module-level helper function is resolved",
        files={"pkg/test_b.py": _MODULE_LEVEL_ROOT},
        expect_kind=Kind.CENSUS_LITERAL,
        expect_detail="literal 12 compared '=='",
    ),
    "markdown_population_claim": PlantedViolation(
        requirement="FR03",
        what_it_proves="a hand-written count in an auto-loaded rule is reported",
        files={"rules/trw-mcp-python.md": _STALE_RULE},
        expect_kind=Kind.POPULATION_CLAIM,
        expect_detail="17 tool files",
    ),
    "suppression_without_reason": PlantedViolation(
        requirement="FR06",
        what_it_proves="silencing a finding without saying why is itself a finding",
        files={
            "pkg/test_c.py": _FUNCTION_LOCAL_ROOT.replace(
                "assert len(mirrors) >= 7", "assert len(mirrors) >= 7  # census-ok"
            )
        },
        expect_kind=Kind.SUPPRESSION_WITHOUT_REASON,
        expect_detail="no reason text",
    ),
    "unused_suppression": PlantedViolation(
        requirement="FR06",
        what_it_proves=(
            "a suppression that outlives its finding is a one-entry frozen "
            "baseline — the PRD-INFRA-173 stale-entry hole, reported here"
        ),
        files={"pkg/test_d.py": "x = 1  # census-ok: this line never produced a finding\n"},
        expect_kind=Kind.UNUSED_SUPPRESSION,
        expect_detail="no longer produces a finding",
    ),
}


def test_every_check_has_a_planted_violation() -> None:
    """FR11 meta-test: a detection without a planted violation cannot ship."""
    detecting_requirements = {"FR02", "FR03", "FR06"}
    covered = {p.requirement for p in PLANTED.values()}
    assert covered == detecting_requirements, (
        f"every reporting requirement needs a planted violation; missing {detecting_requirements - covered}"
    )
    assert {p.expect_kind for p in PLANTED.values()} == set(Kind), (
        "every finding LABEL the report can emit needs a planted violation that produces it"
    )
    for name, planted in PLANTED.items():
        assert planted.what_it_proves.strip(), f"{name}: a planted fixture must say what it proves"


@pytest.mark.parametrize("name", sorted(PLANTED))
def test_planted_violation_is_reported_for_the_right_reason(name: str, tmp_path: Path) -> None:
    """Each planted defect produces its own label, not merely 'some finding'."""
    planted = PLANTED[name]
    report = _scan(tmp_path, planted.files)
    matching = [f for f in report.findings if f.kind is planted.expect_kind]
    assert matching, f"{name} ({planted.requirement}) produced {_kinds(report)}, expected {planted.expect_kind.value}"
    assert any(planted.expect_detail in f.detail for f in matching), (
        f"{name}: no finding mentioned {planted.expect_detail!r}; got {[f.detail for f in matching]}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# FR01 — the census predicate
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("literal", "measure", "rooting", "expected"),
    [
        # `len(skills) == 28` against a bundled-skills enumeration.
        (28, Measure.CARDINALITY, Rooting.EXTERNAL, Verdict.CENSUS),
        # `len(authored.split()) <= 500` — one agent's prose, not a cardinality.
        (500, Measure.PER_MEMBER, Rooting.EXTERNAL, Verdict.POLICY_THRESHOLD),
        (900, Measure.PER_MEMBER, Rooting.EXTERNAL, Verdict.POLICY_THRESHOLD),
        # `count > 0` over a repo-rooted iterdir — the empty/non-empty boundary.
        (0, Measure.CARDINALITY, Rooting.EXTERNAL, Verdict.OUT_OF_SCOPE),
        # `len(_FR04_EXPECTED_TOOLS) == 16` — the roster IS the specification.
        (16, Measure.CARDINALITY, Rooting.CHECKED_ROSTER, Verdict.OUT_OF_SCOPE),
        # a directory the test populated itself.
        (1, Measure.CARDINALITY, Rooting.FIXTURE, Verdict.OUT_OF_SCOPE),
        # an untraceable root costs recall, never precision.
        (3, Measure.CARDINALITY, Rooting.UNRESOLVED, Verdict.OUT_OF_SCOPE),
    ],
)
def test_predicate_classifies_each_property_independently(
    literal: int, measure: Measure, rooting: Rooting, expected: Verdict
) -> None:
    """FR01: extensional AND externally rooted AND non-vacuously derivable."""
    assert classify(literal, measure, rooting).verdict is expected


def test_zero_is_never_a_census_value_in_either_direction() -> None:
    """FR01: 0 is the empty/non-empty boundary, so it cannot rot."""
    for rooting in Rooting:
        assert classify(0, Measure.CARDINALITY, rooting).verdict is Verdict.OUT_OF_SCOPE


def test_classifier_carries_no_path_allowlist() -> None:
    """FR01 grep_absent: every exclusion is structural, so future cases inherit it."""
    source = (_SCRIPTS / "census" / "_predicate.py").read_text(encoding="utf-8")
    for protected in ("test_bundled_agents", "test_claude_md_loc", "test_tool_call_timing"):
        assert protected not in source, f"{protected} named in the classifier — that is a path allowlist"


# ──────────────────────────────────────────────────────────────────────────────
# FR02 — root resolution and the Python surface
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("operator", ["==", ">=", "<=", ">", "<"])
def test_all_five_operators_are_detected(operator: str, tmp_path: Path) -> None:
    """FR02 boundary semantics: all five appear in the measured corpus."""
    body = _FUNCTION_LOCAL_ROOT.replace(">= 7", f"{operator} 7")
    report = _scan(tmp_path, {"pkg/test_op.py": body})
    assert Kind.CENSUS_LITERAL.value in _kinds(report), f"operator {operator} was not detected"


def test_literal_on_the_left_is_read_from_the_counts_point_of_view(tmp_path: Path) -> None:
    """``7 <= len(mirrors)`` states the same bound as ``len(mirrors) >= 7``."""
    body = _FUNCTION_LOCAL_ROOT.replace("assert len(mirrors) >= 7", "assert 7 <= len(mirrors)")
    report = _scan(tmp_path, {"pkg/test_mirror.py": body})
    assert any("compared '>='" in f.detail for f in report.findings), [f.detail for f in report.findings]


def test_fixture_rooted_enumeration_is_never_reported(tmp_path: Path) -> None:
    """FR04: 108 of the 111 measured stage-1 candidates have this shape."""
    report = _scan(tmp_path, {"pkg/test_fx.py": _FIXTURE_ROOTED})
    assert report.findings == []
    assert report.candidates == 1, "the fixture case must still be COUNTED as a stage-1 candidate"
    assert report.externally_rooted == 0


def test_report_names_the_population_source_not_just_the_literal(tmp_path: Path) -> None:
    """FR02: the reader must see what the number was supposed to describe."""
    report = _scan(tmp_path, {"pkg/test_src.py": _FUNCTION_LOCAL_ROOT})
    assert any("`mirrors`" in f.detail for f in report.findings), [f.detail for f in report.findings]


def test_unparseable_file_is_skipped_not_dropped(tmp_path: Path) -> None:
    """NFR02: a silently-skipped file is a census hole of its own shape."""
    report = _scan(tmp_path, {"pkg/broken.py": "def (:\n", "pkg/test_ok.py": _FUNCTION_LOCAL_ROOT})
    assert [s.path.name for s in report.skips] == ["broken.py"]
    assert Kind.CENSUS_LITERAL.value in _kinds(report), "the scan must not abort on the unparseable file"


# ──────────────────────────────────────────────────────────────────────────────
# FR03 — auto-loaded markdown
# ──────────────────────────────────────────────────────────────────────────────


def test_pre_correction_rule_text_reports_both_claims(tmp_path: Path) -> None:
    """US-2: the 133-day claim and its sibling are both named."""
    report = _scan(tmp_path, {"rules/trw-mcp-python.md": _STALE_RULE})
    details = [f.detail for f in report.findings]
    assert any("17 tool files" in d for d in details), details
    assert any("28 modules" in d for d in details), details


def test_corrected_rule_text_is_clean(tmp_path: Path) -> None:
    """A sentinel-wrapped count and a fenced code block are both exempt."""
    report = _scan(tmp_path, {"rules/trw-mcp-python.md": _CORRECTED_RULE})
    assert report.findings == [], [f.detail for f in report.findings]


def test_real_rule_files_are_clean_at_head() -> None:
    """FR03: the corrected text at HEAD stays clean, so a regression fails here."""
    findings = [
        f
        for path in census.markdown_files(REAL_SCOPE, REPO_ROOT)
        for f in census.scan_markdown(path, path.read_text(encoding="utf-8"), REAL_SCOPE)
    ]
    assert findings == [], [f"{f.path}:{f.line} {f.detail}" for f in findings]


# ──────────────────────────────────────────────────────────────────────────────
# FR04 — protected classes, asserted against the REAL repository
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real_report() -> census.ScanReport:
    """One full-repository scan, shared by the regression assertions."""
    return census.run_scan(REAL_SCOPE, REPO_ROOT)


@pytest.mark.parametrize(
    "protected",
    [
        "trw-mcp/tests/test_bundled_agents.py",
        "trw-mcp/tests/test_claude_md_loc.py",
        "trw-mcp/tests/test_tool_call_timing.py",
    ],
)
def test_protected_cases_absent_from_a_real_repository_scan(protected: str, real_report: census.ScanReport) -> None:
    """FR04: a precision regression must fail a NAMED test, not degrade quietly."""
    hits = [f"{f.path}:{f.line}" for f in real_report.findings if f.path == REPO_ROOT / protected]
    assert hits == [], f"{protected} is a protected class and must never be reported: {hits}"


def test_real_repository_has_zero_unsuppressed_findings(real_report: census.ScanReport) -> None:
    """FR05: the corpus is clean when the gate turns on, which is what makes
    zero-tolerance viable without a frozen baseline."""
    assert real_report.findings == [], [f"{f.path}:{f.line} {f.detail}" for f in real_report.findings]


def test_the_stage_two_filter_removes_the_overwhelming_majority(real_report: census.ScanReport) -> None:
    """NFR03: the property is checked, not a frozen list of the 108 fixture lines.

    The PRD measured 97.3% removal (108 of 111). A collapse here means the
    external-rooting filter stopped working, which is the precision failure that
    gets a check suppressed.
    """
    assert real_report.candidates > 50, "stage 1 collapsed — the detector stopped finding candidates"
    assert real_report.stage1_reduction >= 0.9, (
        f"stage 1 -> 2 reduction fell to {real_report.stage1_reduction:.1%}; the PRD measured 97.3%"
    )


def test_stage_two_precision_holds_at_the_recorded_floor(real_report: census.ScanReport) -> None:
    """NFR03: precision on the measured corpus stays at or above the floor.

    The PRD measured 2 of 3 externally-rooted candidates as true findings, the
    third being the ``> 0`` non-emptiness guard that FR01 excludes by design.
    A suppressed census literal counts as a true positive: the detector was
    right and a human recorded why the derivation is wrong, so using the escape
    hatch correctly must not read as a precision regression.
    """
    assert real_report.precision >= 2 / 3, (
        f"stage-2 precision fell to {real_report.precision:.3f} over "
        f"{real_report.externally_rooted} candidate(s); the PRD floor is 2 of 3"
    )


def test_report_states_both_stage_counts(real_report: census.ScanReport) -> None:
    """NFR03: precision is auditable on every run without re-deriving it by hand."""
    rendered = "\n".join(census.render(real_report, REPO_ROOT))
    assert "stage 1  enumeration-backed candidates" in rendered
    assert "stage 2  externally-rooted candidates" in rendered
    assert "stage-2 precision" in rendered
    assert census.BASELINE in rendered


# ──────────────────────────────────────────────────────────────────────────────
# The six historical instances — recorded truthfully
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HistoricalInstance:
    """One of the six literals fixed by hand on 2026-07-24."""

    artifact: str
    pre_fix_shape: str
    mechanically_detected: bool
    why: str


HISTORICAL_INSTANCES: tuple[HistoricalInstance, ...] = (
    HistoricalInstance(
        "trw-mcp/tests/test_agent_frontmatter.py",
        "len(bundled_mirror) == 12 over AGENTS_DIR.glob via a module-level helper",
        True,
        "externally rooted at a module-level Path(__file__) binding",
    ),
    HistoricalInstance(
        "trw-mcp/tests/test_bootstrap_merge_metadata.py",
        "len(skills) == 28 over a manifest written into a fake_git_repo fixture",
        False,
        "FIXTURE-rooted: the population is read from a manifest the test's own "
        "init_project() wrote into a tmp repo. FR01 property 2 excludes it, and "
        "relaxing that filter is what drops precision from 67% to 2.7%.",
    ),
    HistoricalInstance(
        "trw-mcp/tests/test_agent_frontmatter_budgets.py",
        "a multiple-of-500 rounding rule on max_tokens",
        False,
        "carries no census literal at all; 'a guard with no subject' is an "
        "explicit PRD Non-Goal (OQ-3) because the schema is owned outside this repo",
    ),
    HistoricalInstance(
        "trw-mcp/tests/test_init_project_skills.py",
        "len(agent_paths) == 11 over an installer result dict",
        False,
        "FIXTURE-rooted and not enumeration-backed: the list comprehension reads "
        "result['created'] after installing into an empty_target fixture",
    ),
    HistoricalInstance(
        "trw-mcp/tests/test_antigravity_cli_bootstrap.py",
        "len(agent_files) == 4 over (fake_git_repo / _DIR).glob('trw-*.md')",
        False,
        "FIXTURE-rooted: the glob root is the fake_git_repo parameter, which is "
        "literally the shape of the 108 measured true negatives",
    ),
    HistoricalInstance(
        ".claude/rules/trw-mcp-python.md",
        "'tools/ — 17 tool files' and 'state/ — 28 modules'",
        True,
        "population claims on the auto-loaded markdown surface (FR03)",
    ),
)


def test_historical_instances_disposition_is_recorded_truthfully(tmp_path: Path) -> None:
    """FR01's assertion bullet claims the classifier returns census for ALL SIX
    pre-fix instances. It cannot, and the PRD contradicts itself on this point:
    instance 3 is an explicit Non-Goal (OQ-3), and instances 2, 4, and 5 are
    fixture-rooted — the very class §1 measures at 108 of 111 and requires be
    excluded. Three of the six are mechanically detectable. This test records
    that honestly rather than reinterpreting the requirement silently.
    """
    detected = {i.artifact for i in HISTORICAL_INSTANCES if i.mechanically_detected}
    assert detected == {
        "trw-mcp/tests/test_agent_frontmatter.py",
        ".claude/rules/trw-mcp-python.md",
    }
    for instance in HISTORICAL_INSTANCES:
        assert instance.why.strip(), f"{instance.artifact}: a disposition needs a reason"

    # The two detectable Python instances are reproduced in their pre-fix form.
    report = _scan(tmp_path, {"pkg/test_hist.py": _MODULE_LEVEL_ROOT, "rules/r.md": _STALE_RULE})
    assert sum(1 for f in report.findings if f.kind is Kind.CENSUS_LITERAL) == 1
    assert sum(1 for f in report.findings if f.kind is Kind.POPULATION_CLAIM) == 2


def test_protected_cases_are_reproduced_and_never_reported(tmp_path: Path) -> None:
    """G3: the four protected classes, on copies of the real cases."""
    report = _scan(
        tmp_path,
        {
            "pkg/test_budget.py": _PER_MEMBER_BUDGET,
            "pkg/test_zero.py": _ZERO_GUARD,
            "pkg/test_roster.py": _INTERNAL_ROSTER,
            "pkg/test_fixture.py": _FIXTURE_ROOTED,
        },
    )
    assert report.findings == [], [f"{f.path.name}:{f.line} {f.detail}" for f in report.findings]


# ──────────────────────────────────────────────────────────────────────────────
# FR05/FR06/FR07 — enforcement, suppression, self-check
# ──────────────────────────────────────────────────────────────────────────────


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd or REPO_ROOT),
    )


def _seeded_scope(tmp_path: Path) -> Path:
    """A scope config pointing at a throwaway tree, written next to it."""
    scope = tmp_path / "scope.yaml"
    scope.write_text(
        "python_trees: [pkg]\n"
        "exclude_dirs: [__pycache__]\n"
        "enumeration_calls: [glob, rglob, iterdir, listdir, scandir, walk]\n"
        "per_member_calls: [split, splitlines]\n"
        "markdown_globs: [rules/*.md]\n"
        "population_nouns: [tool files, tools, modules, files]\n",
        encoding="utf-8",
    )
    return scope


def test_exit_code_fails_closed_on_a_seeded_finding(tmp_path: Path) -> None:
    """FR05: a new census cannot survive a check run."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "test_seed.py").write_text(_FUNCTION_LOCAL_ROOT, encoding="utf-8")
    scope = _seeded_scope(tmp_path)
    seeded = _run_cli("--scope", str(scope), "--root", str(tmp_path))
    assert seeded.returncode == 1, seeded.stdout + seeded.stderr
    assert "CENSUS_LITERAL" in seeded.stdout

    (tmp_path / "pkg" / "test_seed.py").write_text("x = 1\n", encoding="utf-8")
    corrected = _run_cli("--scope", str(scope), "--root", str(tmp_path))
    assert corrected.returncode == 0, corrected.stdout + corrected.stderr


def test_report_mode_never_fails_but_still_reports(tmp_path: Path) -> None:
    """FR07: a developer running the script by hand is a legitimate workflow."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "test_seed.py").write_text(_FUNCTION_LOCAL_ROOT, encoding="utf-8")
    result = _run_cli("--scope", str(_seeded_scope(tmp_path)), "--root", str(tmp_path), "--report")
    assert result.returncode == 0
    assert "CENSUS_LITERAL" in result.stdout


def test_advisory_invocation_from_the_aggregate_target_fails(tmp_path: Path) -> None:
    """FR07: the gate cannot be quietly downgraded in the recipe."""
    result = _run_cli("--scope", str(_seeded_scope(tmp_path)), "--root", str(tmp_path), "--report", "--from-check")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "advisory" in result.stderr.lower()


def test_self_check_runs_before_any_scan_and_cannot_be_suppressed(tmp_path: Path) -> None:
    """FR07: a suppressible self-check is not a self-check.

    The downgrade is refused even when the tree is empty (so no finding exists to
    annotate) and even when a marker is present, because the branch is evaluated
    before the scope is loaded and never consults the suppression ledger.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "test_m.py").write_text("x = 1  # census-ok: try to disable the self-check\n", encoding="utf-8")
    result = _run_cli("--scope", str(_seeded_scope(tmp_path)), "--root", str(tmp_path), "--from-check", "--report")
    assert result.returncode == 3
    cli_source = _CLI.read_text(encoding="utf-8")
    self_check = cli_source.split("if args.from_check and args.report:")[1].split("return EXIT_SELF_CHECK")[0]
    assert census.TOKEN not in self_check
    assert "suppress" not in self_check.lower()


def test_missing_scope_config_is_a_distinct_failure_not_a_silent_pass() -> None:
    """A scan that reports success because it scanned nothing is the defect class."""
    result = _run_cli("--scope", "/nonexistent/census-scope.yaml")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "scope config not found" in result.stderr


def test_empty_scan_scope_is_an_error(tmp_path: Path) -> None:
    """An empty scope is an error, not a pass."""
    empty = tmp_path / "empty.yaml"
    empty.write_text("python_trees: []\nmarkdown_globs: []\n", encoding="utf-8")
    with pytest.raises(census.ScopeError, match="no surfaces"):
        census.load_scope(empty)


def test_reasoned_suppression_silences_one_line_and_stays_visible(tmp_path: Path) -> None:
    """FR06: suppression is possible, cheap, and permanently visible."""
    body = _FUNCTION_LOCAL_ROOT.replace(
        "assert len(mirrors) >= 7", "assert len(mirrors) >= 7  # census-ok: 7 is an externally stated floor"
    )
    report = _scan(tmp_path, {"pkg/test_s.py": body})
    assert report.findings == []
    assert [s.reason for s in report.suppressions] == ["7 is an externally stated floor"]


def test_a_marker_applies_only_to_the_line_it_annotates(tmp_path: Path) -> None:
    """FR06: never a block, a function, or a file."""
    body = _FUNCTION_LOCAL_ROOT + (
        "\ndef test_second() -> None:\n"
        "    from pathlib import Path as P\n"
        "    root2 = P(__file__).resolve().parents[2]\n"
        '    other = sorted(root2.glob("data/agents/*.md"))\n'
        "    assert len(other) == 11\n"
    )
    body = body.replace("assert len(mirrors) >= 7", "assert len(mirrors) >= 7  # census-ok: externally stated floor")
    report = _scan(tmp_path, {"pkg/test_two.py": body})
    assert len(report.findings) == 1, [f.detail for f in report.findings]
    assert "literal 11" in report.findings[0].detail


def test_no_block_or_file_level_suppression_syntax_is_accepted(tmp_path: Path) -> None:
    """FR06 grep_absent: file-level suppression is how a gate dies quietly."""
    body = "# census-ok-file: silence everything below\n" + _FUNCTION_LOCAL_ROOT
    report = _scan(tmp_path, {"pkg/test_file_level.py": body})
    assert Kind.CENSUS_LITERAL.value in _kinds(report), "a file-level directive must not suppress"


def test_marker_inside_a_string_literal_is_not_a_suppression(tmp_path: Path) -> None:
    """Markers come from real comments, so documenting the syntax cannot silence."""
    body = _FUNCTION_LOCAL_ROOT + '\nDOCS = "write census-ok: reason to suppress one line"\n'
    report = _scan(tmp_path, {"pkg/test_str.py": body})
    assert Kind.CENSUS_LITERAL.value in _kinds(report)
    assert report.suppressions == []


def test_no_frozen_baseline_artifact_is_introduced() -> None:
    """FR05: a baseline would rebuild PRD-INFRA-173's stale-entry hole inside the
    check that exists to detect this defect class."""
    compliance = REPO_ROOT / ".trw" / "compliance"
    existing = {p.name for p in compliance.glob("*")} if compliance.is_dir() else set()
    assert not any("census" in name for name in existing), existing
    cli_source = _CLI.read_text(encoding="utf-8")
    assert "baseline" not in cli_source.lower() or "no frozen" in cli_source.lower()


# ──────────────────────────────────────────────────────────────────────────────
# FR05/FR07 — Makefile wiring
# ──────────────────────────────────────────────────────────────────────────────


def _makefile() -> str:
    return (REPO_ROOT / "Makefile").read_text(encoding="utf-8")


def test_census_check_is_a_dependency_of_the_aggregate_target() -> None:
    """FR05: removing the check from `make check` is detected here."""
    check_line = next(line for line in _makefile().splitlines() if line.startswith("check:"))
    assert " census-check " in f" {check_line} ", check_line


def test_census_recipe_carries_the_never_downgrade_comment_and_the_flag() -> None:
    """FR07: matching the idiom the wiring gate already ships at Makefile:39."""
    recipe = _makefile().split("census-check:")[1].split("\n\n")[0]
    assert "ENFORCING" in recipe
    assert "Never add the advisory flag" in recipe
    assert "--from-check" in recipe
    assert "--report" not in recipe.split("python3")[1]


# ──────────────────────────────────────────────────────────────────────────────
# FR08 — the sentinel surface
# ──────────────────────────────────────────────────────────────────────────────


def _sync_module() -> Any:
    return _load("sync_markdown_counts", _SCRIPTS / "sync_markdown_counts.py")


def test_auto_loaded_rules_are_in_the_sentinel_target_surface() -> None:
    """FR08: the surface of the worst instance joins the mechanism that prevents it."""
    sync = _sync_module()
    assert any(".claude/rules" in entry for entry in sync.TARGET_FILES)
    expanded = [p for p in sync.expand_targets(REPO_ROOT, sync.TARGET_FILES) if ".claude/rules" in str(p)]
    assert expanded, "the rule glob resolved to no files"
    assert all(p.suffix == ".md" for p in expanded)


def test_a_stale_sentinel_in_a_rule_file_is_rewritten_from_the_manifest(tmp_path: Path) -> None:
    """FR08: an author who wants a count in a rule file gets one, kept true."""
    sync = _sync_module()
    inventory = {"counts": {"tools": 46}}
    rule = tmp_path / "seeded.md"
    rule.write_text("- `tools/` — <!-- inv:tools -->3<!-- /inv --> tools\n", encoding="utf-8")

    stale = sync.process_file(rule, inventory, check_only=True)
    assert stale, "the drift must be reported before the sync runs"
    assert "3 → 46" in stale[0]

    sync.process_file(rule, inventory, check_only=False)
    assert "<!-- inv:tools -->46<!-- /inv -->" in rule.read_text(encoding="utf-8")
    assert sync.process_file(rule, inventory, check_only=True) == []


def test_a_sentinel_naming_an_unknown_key_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    """FR08: a sentinel that resolves to nothing is a count that only looks
    machine-maintained."""
    sync = _sync_module()
    rule = tmp_path / "bad.md"
    rule.write_text("<!-- inv:not_a_real_key -->7<!-- /inv -->\n", encoding="utf-8")
    with pytest.raises(sync.UnknownSentinelKeyError, match="not_a_real_key"):
        sync.process_file(rule, {"counts": {"tools": 46}}, check_only=True)


def test_the_convention_document_names_a_sync_script_that_exists() -> None:
    """FR08: a zero-context implementer following the convention must not hit a
    missing file."""
    for doc in (REPO_ROOT / "CLAUDE.md", REPO_ROOT / "docs" / "documentation" / "dry-inventory-system.md"):
        text = doc.read_text(encoding="utf-8")
        for named in set(re.findall(r"scripts/sync[-_]markdown[-_]counts\.py", text)):
            assert (REPO_ROOT / named).is_file(), f"{doc.name} names {named}, which does not exist"


# ──────────────────────────────────────────────────────────────────────────────
# NFR01/NFR02/NFR04
# ──────────────────────────────────────────────────────────────────────────────


def test_each_candidate_file_is_parsed_exactly_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """NFR01: one parse per file, one walk per tree."""
    seen: list[Path] = []
    original = census._engine.scan_source

    def counting(path: Path, text: str, scope: Any) -> Any:
        seen.append(path)
        return original(path, text, scope)

    monkeypatch.setattr(census._engine, "scan_source", counting)
    _scan(tmp_path, {"pkg/a.py": _FUNCTION_LOCAL_ROOT, "pkg/b.py": _FIXTURE_ROOTED})
    assert sorted(p.name for p in seen) == ["a.py", "b.py"]
    assert len(seen) == len(set(seen))

    scan_src = (_SCRIPTS / "census" / "_python_scan.py").read_text(encoding="utf-8")
    assert scan_src.count("ast.parse(") == 1, "more than one parse call in the Python surface"


def test_no_third_party_dependency_beyond_the_existing_yaml_loader() -> None:
    """NFR01: standard library only, matching every sibling gate in scripts/."""
    allowed = {"yaml"}
    stdlib = set(sys.stdlib_module_names)
    for module in sorted((_SCRIPTS / "census").glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [] if node.level else [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                assert name in stdlib or name in allowed, f"{module.name} imports {name}"


def test_two_scans_over_identical_inputs_are_byte_identical(tmp_path: Path) -> None:
    """NFR02: findings sort by path, then line, then label."""
    files = {"pkg/z.py": _FUNCTION_LOCAL_ROOT, "pkg/a.py": _MODULE_LEVEL_ROOT, "rules/r.md": _STALE_RULE}
    first = "\n".join(census.render(_scan(tmp_path, files), tmp_path))
    second = "\n".join(census.render(_scan(tmp_path, files), tmp_path))
    assert first == second
    lines = [f.sort_key() for f in _scan(tmp_path, files).findings]
    assert lines == sorted(lines)


def test_full_repository_scan_stays_within_a_gate_sized_budget() -> None:
    """NFR01: the scan must fit the budget of the fastest existing gate targets."""
    started = time.monotonic()
    census.run_scan(REAL_SCOPE, REPO_ROOT)
    elapsed = time.monotonic() - started
    assert elapsed < 120.0, f"full-repository scan took {elapsed:.1f}s"


def test_the_lint_is_absent_from_the_published_package() -> None:
    """NFR04: repo-development linting stays outside every distributed wheel."""
    pyproject = (REPO_ROOT / "trw-mcp" / "pyproject.toml").read_text(encoding="utf-8")
    assert "check_census_literals" not in pyproject
    assert "census" not in pyproject
    src = REPO_ROOT / "trw-mcp" / "src" / "trw_mcp"
    offenders = [
        p.relative_to(REPO_ROOT)
        for p in src.rglob("*.py")
        if re.search(r"^\s*(from|import)\s+census\b", p.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert offenders == [], offenders
