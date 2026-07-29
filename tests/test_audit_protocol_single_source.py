"""One owner for the audit protocol (PRD-QUAL-128 FR01-FR04, NFR01, NFR02, NFR04).

``audit-framework.md`` is the sole owner of the eight protocol elements. Before
this PRD the NFR checklist, verdict criteria, severity ladder, and report schema
were each written in two or three places, and six of those copies had already
drifted into behavior differences — the ``/trw-audit`` path ran a 10-item
checklist missing the Potemkin-gate item and a PASS rule stricter than the one
the agents used.

The gate under test counts *definitions*, recognized by structure rather than by
keyword, so a pointer stays legal and a paste does not.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._audit_protocol_support import (
    FRAMEWORK_PATH,
    SKILL_PROJECTIONS,
    strip_fragments,
    table_with_header,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_audit_protocol_single_source.py"

# Monorepo-only invariant: repo-root scripts/ is absent from the standalone
# trw-mcp mirror. Skip at COLLECTION time there, matching
# test_agent_contract_lint.py and test_agent_loc.py (NFR02).
if not _SCRIPT.is_file():
    pytest.skip("monorepo-only invariant (repo-root scripts/ absent in mirror)", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("check_audit_protocol_single_source", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_gate = importlib.util.module_from_spec(_spec)
# Register before exec so the module's @dataclass declarations resolve their
# annotations — dataclasses looks the defining module up in sys.modules.
sys.modules[_spec.name] = _gate
_spec.loader.exec_module(_gate)

_CONTRACT_LINT = REPO_ROOT / "scripts" / "check_agent_contracts.py"
AGENT_DIRS = (
    REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "agents",
    REPO_ROOT / ".claude" / "agents",
)

#: FR02/NFR04: 320 leaves >= 30 lines of headroom under the 350 authored-LOC cap
#: in test_agent_loc.py. trw-auditor.md sat at exactly 350/350 before this PRD.
AGENT_LINE_TARGET = 320
#: Section C ships 11 items; item 11 (Property reachability) is the one the
#: drifted skill copy had dropped, and it is the Potemkin-gate class.
NFR_CHECKLIST_ITEMS = 11


@pytest.fixture(scope="module")
def surfaces() -> list[Path]:
    return _gate.discover_surfaces(REPO_ROOT)


@pytest.fixture(scope="module")
def violations(surfaces: list[Path]) -> list[object]:
    return _gate.scan(REPO_ROOT, surfaces)


def test_surfaces_are_discovered(surfaces: list[Path]) -> None:
    """A scan that finds nothing would pass vacuously; guard the scan itself."""
    assert len(surfaces) >= 20, f"only {len(surfaces)} surfaces discovered"


def test_protocol_elements_have_exactly_one_definition(violations: list[object]) -> None:
    """FR01: each of the eight elements is defined on exactly one surface."""
    detail = "\n".join(f"  {v.path}:{v.line} [{v.element}] {v.message}" for v in violations)
    assert not violations, f"{len(violations)} protocol-duplication violation(s)\n{detail}"


def test_the_single_source_keeps_the_full_nfr_checklist() -> None:
    """The 11-item checklist is adopted whole; item 11 must survive verbatim."""
    framework = FRAMEWORK_PATH.read_text(encoding="utf-8")
    _, rows = table_with_header(framework, "NFR", "Check", "Common Miss")
    assert len(rows) == NFR_CHECKLIST_ITEMS, f"Section C has {len(rows)} rows, expected {NFR_CHECKLIST_ITEMS}"
    assert rows[-1][1].startswith("Property reachability"), "item 11 (Potemkin-gate class) is missing"
    assert "sub_zAfRqZYYq2KtF72d" in framework, "item 11 lost its operator-report attribution"
    assert "Pagination limits" not in framework, "the drifted NFR item 1 name reappeared (drift D2)"


@pytest.mark.parametrize("agents_dir", AGENT_DIRS, ids=lambda p: p.parent.name)
def test_auditor_agent_defers_protocol_and_keeps_headroom(agents_dir: Path) -> None:
    """FR02: the auditor is a role-and-phases prompt with headroom to grow."""
    path = agents_dir / "trw-auditor.md"
    text = path.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= AGENT_LINE_TARGET, (
        f"{path}: {len(text.splitlines())} lines exceeds the {AGENT_LINE_TARGET}-line target"
    )
    surface = _gate.parse_surface(text)
    defined = [s.element for s in _gate.SIGNATURES if _gate.find_definitions(s, surface)]
    assert not defined, f"{path} still defines protocol elements: {defined}"
    assert "audit-framework.md" in text, "the auditor no longer names its protocol source"
    for letter in ("G", "H"):
        assert f"Section {letter}" in text, f"<shared-protocol> does not name Section {letter}"


@pytest.mark.parametrize("projection", SKILL_PROJECTIONS, ids=lambda p: str(p).split("trw-framework/")[-1])
def test_every_skill_projection_is_an_invocation_adapter(projection: Path) -> None:
    """FR03: all seven projections point at the protocol instead of restating it."""
    text = projection.read_text(encoding="utf-8")
    surface = _gate.parse_surface(text)
    defined = [s.element for s in _gate.SIGNATURES if _gate.find_definitions(s, surface)]
    assert not defined, f"{projection} restates protocol elements: {defined}"
    assert "audit-framework.md" in text, f"{projection} does not name the single source"
    assert "Pagination limits" not in text, f"{projection} still carries the drifted NFR item 1 (D1/D2)"
    assert "prior_learning_verification: checked" not in text, f"{projection} still carries the scalar form (D5)"


def test_all_seven_projections_are_scanned() -> None:
    assert len(SKILL_PROJECTIONS) == 7, "guard coverage must reach 7 of 7 projections, not 4 of 7"


def test_pointer_prose_is_a_reference_not_a_definition() -> None:
    """The distinction that makes consolidation possible at all."""
    surface = _gate.parse_surface(
        "Assign the overall verdict from the audit-framework.md Section E verdict\n"
        "criteria table: PASS, CONDITIONAL, and FAIL are defined there, with the\n"
        "severity ladder (P0/P1/P2) and the NFR checklist in Section C.\n"
    )
    assert [s.element for s in _gate.SIGNATURES if _gate.find_definitions(s, surface)] == []


def test_a_verdict_table_is_a_definition() -> None:
    surface = _gate.parse_surface(
        "| Verdict | Criteria | Action |\n"
        "|---|---|---|\n"
        "| **PASS** | Zero P0 and zero P1 | advance |\n"
        "| **CONDITIONAL** | Zero P0 and 1-2 P1 | hold |\n"
        "| **FAIL** | Any P0 | revert |\n"
    )
    assert "verdict_criteria" in [s.element for s in _gate.SIGNATURES if _gate.find_definitions(s, surface)]


def test_a_malformed_protocol_table_is_a_violation_not_a_skip() -> None:
    """NFR02 fail-closed: an unparseable protocol table must not slip through."""
    surface = _gate.parse_surface("| Verdict | Criteria |\n| **PASS** | Zero P0 |\n")
    assert _gate.malformed_protocol_tables(surface), "a table with no separator row was silently ignored"


def test_a_missing_scan_root_fails_loudly(tmp_path: Path) -> None:
    """A moved directory must fail the gate, never shrink it."""
    with pytest.raises(FileNotFoundError):
        _gate.discover_surfaces(tmp_path)


def test_linter_reports_a_planted_duplicate(tmp_path: Path) -> None:
    """FR04: a planted second definition exits non-zero and names element/path/line."""
    planted = tmp_path / "planted-duplicate.md"
    planted.write_text(
        "# Planted surface\n\n"
        "Some prose about auditing.\n\n"
        "| Verdict | Criteria | Action |\n"
        "|---------|----------|--------|\n"
        "| **PASS** | Zero P0 findings and zero P1 findings | advance |\n"
        "| **CONDITIONAL** | Zero P0 and 1-2 P1 findings | hold |\n"
        "| **FAIL** | Any P0 finding | revert |\n",
        encoding="utf-8",
    )
    owner = REPO_ROOT / _gate.SINGLE_SOURCE
    found = _gate.scan(REPO_ROOT, [owner, planted])
    assert [v.element for v in found] == ["verdict_criteria"]
    assert found[0].path == str(planted)
    assert found[0].line == 5
    assert str(_gate.SINGLE_SOURCE) in found[0].competing
    assert _gate.run(check=True, surfaces=[owner, planted]) == 1
    assert _gate.run(check=True, surfaces=[owner]) == 0


def test_linter_runtime_is_bounded() -> None:
    """NFR01: both linters together add under 5s to make bundle-sync."""
    started = time.perf_counter()
    for script in (_SCRIPT, _CONTRACT_LINT):
        result = subprocess.run(
            [sys.executable, str(script), "--check"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, f"{script.name} failed:\n{result.stdout}\n{result.stderr}"
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"combined linter wall time {elapsed:.2f}s exceeds the 5s budget"


def test_module_skips_cleanly_without_repo_root_scripts(tmp_path: Path) -> None:
    """NFR02: in the standalone mirror these modules SKIP, they do not error."""
    mirror = tmp_path / "trw-mcp"
    tests = mirror / "tests"
    tests.mkdir(parents=True)
    (tests / "__init__.py").write_text("", encoding="utf-8")
    (mirror / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\n", encoding="utf-8"
    )
    here = Path(__file__).parent
    for name in ("_audit_protocol_support.py", "test_audit_protocol_single_source.py", "test_audit_protocol_contracts.py"):
        (tests / name).write_text((here / name).read_text(encoding="utf-8"), encoding="utf-8")
    assert not (tmp_path / "scripts").exists(), "the fixture must reproduce a scripts-less tree"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=mirror,
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = result.stdout + result.stderr
    assert "ImportError" not in combined, combined
    assert "error" not in result.stdout.lower(), result.stdout
    assert "2 skipped" in result.stdout, f"expected two clean module-level skips, got:\n{result.stdout}"
    # 0 = ok, 5 = nothing left to run once both modules skipped. Anything else
    # (2 interrupted, 3 internal, 4 usage) means collection broke, which is the
    # failure NFR02 forbids.
    assert result.returncode in {0, 5}, f"collection in the mirror broke: exit {result.returncode}\n{combined}"


def test_all_agents_keep_headroom_under_the_cap() -> None:
    """NFR04: every agent stays 30+ authored lines below the 350 hard cap."""
    oversize = {
        f"{path.parent.parent.name}/{path.name}": len(strip_fragments(path.read_text(encoding="utf-8")).splitlines())
        for directory in AGENT_DIRS
        for path in sorted(directory.glob("*.md"))
        if len(strip_fragments(path.read_text(encoding="utf-8")).splitlines()) > AGENT_LINE_TARGET
    }
    assert not oversize, f"agents over the {AGENT_LINE_TARGET}-line headroom target: {oversize}"


def test_docs_stub_stays_a_pointer() -> None:
    """The docs copy was collapsed to a pointer once; keep it that way."""
    stub = (REPO_ROOT / "docs" / "documentation" / "audit-framework.md").read_text(encoding="utf-8")
    assert re.search(r"trw-audit/audit-framework\.md", stub), "the docs stub no longer names the canonical source"
    surface = _gate.parse_surface(stub)
    assert [s.element for s in _gate.SIGNATURES if _gate.find_definitions(s, surface)] == []
