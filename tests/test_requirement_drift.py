"""PRD-CORE-321 FR02, FR03 and NFR01: drift and orphan detection against a git-derived baseline.

Every test imports the module under test inside its body, so an absent module
fails one test rather than collection of the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest

from tests._requirement_drift_fixtures import Mapping, Repo, prd_text

_FR01: Mapping = {"id": "PRD-X-001-FR01", "criteria": ["first", "second"], "evidence": "tests/test_x.py"}
_FR02: Mapping = {"id": "PRD-X-001-FR02", "criteria": ["kept"], "evidence": "tests/test_y.py"}
_FR03: Mapping = {"id": "PRD-X-001-FR03", "criteria": ["new"], "evidence": "tests/test_z.py"}


def _baseline(*mappings: Mapping, implemented: bool = False, chains: dict[str, tuple[str, ...]] | None = None) -> Any:
    """A resolved ``BaselineResolution`` of the FR01 shape, built without git."""
    from trw_mcp.state.validation.chain_declarations import ChainDeclaration
    from trw_mcp.state.validation.requirement_baseline import BaselineRequirement, BaselineResolution

    chains = chains or {}
    requirements = tuple(
        BaselineRequirement(
            requirement_id=m["id"],
            acceptance_criteria=tuple(m["criteria"]),
            evidence_artifact=m["evidence"],
            call_chain=ChainDeclaration(chain=chains.get(m["id"], ())),
        )
        for m in mappings
    )
    return BaselineResolution(
        prd_id="PRD-X-001",
        status="resolved",
        current_status="approved",
        baseline_sha="a" * 40,
        baseline_date="2026-01-02",
        requirements=requirements,
        implemented_in_history=implemented,
    )


def _findings(findings: list[Any]) -> list[tuple[str, str, tuple[str, ...], str]]:
    return [(f.requirement_id, f.kind, f.changed_fields, f.reason) for f in findings]


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        pytest.param([_FR01, _FR02], [], id="identical"),
        pytest.param(
            [{**_FR01, "criteria": ["first", "second, reworded"]}, _FR02],
            [("PRD-X-001-FR01", "changed", ("acceptance_criteria",), "")],
            id="criterion_reworded",
        ),
        pytest.param(
            [{**_FR01, "criteria": ["second", "first"]}, _FR02],
            [("PRD-X-001-FR01", "changed", ("acceptance_criteria",), "")],
            id="criterion_reordered",
        ),
        pytest.param(
            [{**_FR01, "evidence": "tests/test_other.py"}, _FR02],
            [("PRD-X-001-FR01", "changed", ("evidence_artifact",), "")],
            id="evidence_artifact_changed",
        ),
        pytest.param([_FR02], [("PRD-X-001-FR01", "dropped", (), "")], id="requirement_removed"),
        pytest.param([_FR01, _FR02, _FR03], [], id="requirement_added_after_approval"),
        pytest.param(
            [{**_FR01, "criteria": ["  first ", "second\n"], "evidence": " tests/test_x.py "}, _FR02],
            [],
            id="surrounding_whitespace_is_not_drift",
        ),
    ],
)
def test_drift_classification(tmp_path: Path, current: list[Mapping], expected: list[tuple[Any, ...]]) -> None:
    """FR02: the exact finding, or its absence, and the exact changed-field list per case."""
    from trw_mcp.state.validation.requirement_drift import detect_requirement_drift

    findings = detect_requirement_drift(
        _baseline(_FR01, _FR02),
        prd_text("PRD-X-001", "approved", list(current)),
        current_status="approved",
        repo_root=tmp_path,
        source_root=tmp_path,
    )

    assert _findings(findings) == expected


# --------------------------------------------------------------------------- #
# FR03(a) — evidence_artifact normalisation and shapes
# --------------------------------------------------------------------------- #

_CHAIN = (
    "trw_mcp.tools.ceremony.trw_deliver",
    "trw_mcp.tools._ceremony_deliver_tool.run_trw_deliver",
    "trw_mcp.tools._deliver_gate_dispatch.evaluate_delivery_gates",
)
_CHAIN_CELL = " -> ".join(f"`{hop}`" for hop in _CHAIN)
#: The same chain with its middle hop removed: trw_deliver does not call evaluate_delivery_gates directly.
_BROKEN_CHAIN = (_CHAIN[0], _CHAIN[2])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("tests/test_x.py", "tests/test_x.py"),
        ("  tests/test_x.py  ", "tests/test_x.py"),
        ("tests/test_x.py::TestX::test_a", "tests/test_x.py"),
        ("docs/spec.md#section-2", "docs/spec.md"),
        ("tests/test_x.py::test_a#note", "tests/test_x.py"),
        ("", None),
        ("::test_a", None),
        ("run pytest -k drift by hand", None),
        ("/abs/tests/test_x.py", None),
        ("C:\\repo\\tests\\test_x.py", None),
        ("../outside.py", None),
        ("tests/../../outside.py", None),
    ],
)
def test_evidence_path(value: str, expected: str | None) -> None:
    """FR03(a), direct: ``::`` then ``#`` stripped; empty, spaced, absolute or ``..`` values are not paths."""
    from trw_mcp.state.validation.requirement_drift import evidence_path

    assert evidence_path(value) == expected


def _raw_mapping(evidence_yaml: str) -> str:
    return (
        "      - requirement_id: PRD-X-001-FR01\n"
        "        acceptance_criteria:\n"
        '          - "c"\n'
        f"        evidence_artifact: {evidence_yaml}\n"
    )


def _baseline_from_text(text: str, *, implemented: bool = True) -> Any:
    from trw_mcp.state.validation.requirement_baseline import BaselineResolution, requirements_from_text

    return BaselineResolution(
        prd_id="PRD-X-001",
        status="resolved",
        current_status="implemented",
        baseline_sha="a" * 40,
        baseline_date="2026-01-02",
        requirements=requirements_from_text("PRD-X-001", text),
        implemented_in_history=implemented,
    )


@pytest.mark.parametrize(
    ("evidence_yaml", "files", "expected"),
    [
        pytest.param("tests/test_x.py::test_a", ["tests/test_x.py"], [], id="node_id_file_present"),
        pytest.param(
            "tests/test_x.py::test_a",
            [],
            [("PRD-X-001-FR01", "orphaned", (), "evidence artifact missing")],
            id="node_id_file_absent",
        ),
        pytest.param(
            "tests/test_x.py::test_a\n          continued_on_the_next_line",
            ["tests/test_x.py"],
            [],
            id="two_line_plain_scalar",
        ),
        pytest.param(
            ">-\n          tests/test_x.py::test_a\n          and_a_folded_tail",
            ["tests/test_x.py"],
            [],
            id="folded_block_scalar",
        ),
        pytest.param("|\n          tests/test_x.py", ["tests/test_x.py"], [], id="literal_block_scalar"),
        pytest.param("docs/spec.md#section-2", ["docs/spec.md"], [], id="hash_fragment"),
        pytest.param(
            '"run the drift suite by hand"',
            [],
            [("PRD-X-001-FR01", "evidence_not_checkable", (), "evidence_artifact is not a checkable repository path")],
            id="prose",
        ),
    ],
)
def test_evidence_shape(tmp_path: Path, evidence_yaml: str, files: list[str], expected: list[Any]) -> None:
    """FR03(a): one case per evidence shape; each present-file case fails for a raw-string existence check."""
    from trw_mcp.state.validation.requirement_drift import detect_requirement_drift

    for name in files:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("", encoding="utf-8")
    text = prd_text("PRD-X-001", "implemented", [_raw_mapping(evidence_yaml)])

    findings = detect_requirement_drift(
        _baseline_from_text(text), text, current_status="implemented", repo_root=tmp_path, source_root=tmp_path
    )

    assert _findings(findings) == expected


# --------------------------------------------------------------------------- #
# FR03 — orphans are checked from the BASELINE's inputs, against the right roots
# --------------------------------------------------------------------------- #


def test_dropped_requirement_is_still_orphan_checked(tmp_path: Path) -> None:
    """FR03: a dropped requirement's evidence and chain come from the baseline, so both orphan reasons fire."""
    from trw_mcp.state.validation.requirement_drift import detect_requirement_drift
    from trw_mcp.tools._deliver_capability_integration import _source_root

    baseline = _baseline(_FR01, _FR02, implemented=True, chains={_FR01["id"]: _BROKEN_CHAIN})
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_y.py").write_text("", encoding="utf-8")  # FR02's evidence exists; FR01's does not

    findings = detect_requirement_drift(
        baseline,
        prd_text("PRD-X-001", "implemented", [_FR02]),
        current_status="implemented",
        repo_root=tmp_path,
        source_root=_source_root(),
    )

    assert _findings(findings) == [
        ("PRD-X-001-FR01", "dropped", (), ""),
        ("PRD-X-001-FR01", "orphaned", (), "evidence artifact missing"),
        ("PRD-X-001-FR01", "orphaned", (), "call chain no longer verifies"),
    ]


@pytest.mark.parametrize(
    ("use_source_root", "expected"),
    [
        pytest.param(True, [], id="source_root_intact"),
        pytest.param(
            False, [("PRD-X-001-FR01", "orphaned", (), "call chain no longer verifies")], id="repo_root_is_wrong"
        ),
    ],
)
def test_chain_verified_under_the_running_package(tmp_path: Path, use_source_root: bool, expected: list[Any]) -> None:
    """FR03(b), direct: an intact chain of real trw_mcp symbols is wired only under the package's source root."""
    from trw_mcp.state.validation.requirement_drift import detect_requirement_drift
    from trw_mcp.tools._deliver_capability_integration import _source_root

    mapping: Mapping = {**_FR01, "evidence": "README.md"}
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    baseline = _baseline(mapping, implemented=True, chains={_FR01["id"]: _CHAIN})
    current = prd_text("PRD-X-001", "implemented", [mapping], chains={"FR01": _CHAIN_CELL})

    findings = detect_requirement_drift(
        baseline,
        current,
        current_status="implemented",
        repo_root=tmp_path,
        source_root=_source_root() if use_source_root else tmp_path,
    )

    assert _findings(findings) == expected


def _compute_for(project: Path, scope: list[str]) -> Any:
    from unittest.mock import patch

    from trw_mcp.tools._deliver_requirement_drift import compute_requirement_drift

    run = project / ".trw" / "runs" / "task" / "run-1"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "run.yaml").write_text(f"run_id: run-1\nprd_scope: {scope!r}\n", encoding="utf-8")
    with patch("trw_mcp.state._paths.resolve_project_root", return_value=project):
        return compute_requirement_drift(run)


def test_intact_chain_from_a_fixture_root_without_trw_mcp_is_not_orphaned(tmp_path: Path) -> None:
    """FR03(b) through the caller: compute_requirement_drift must pass the SOURCE root, not the git root.

    The git fixture's root holds no ``trw_mcp`` package, so an implementation that
    handed the fixture root to ``verify_chain`` reports this chain isolated.
    """
    repo = Repo(tmp_path)
    mapping: Mapping = {**_FR01, "evidence": "tests/test_x.py::test_a"}
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("", encoding="utf-8")
    text = prd_text("PRD-X-001", "implemented", [mapping], chains={"FR01": _CHAIN_CELL})
    repo.write("PRD-X-001-chain.md", text)
    sha = repo.commit("implemented")
    assert not (tmp_path / "trw_mcp").exists()

    entry = _compute_for(tmp_path, ["PRD-X-001"])["prds"]["PRD-X-001"]

    assert (entry["baseline_status"], entry["baseline_sha"], entry["orphan_check"]) == ("resolved", sha, "applied")
    assert entry["findings"] == []


@pytest.mark.parametrize(
    ("current_status", "implemented_in_history", "applies"),
    [
        ("approved", False, False),
        ("approved", True, True),
        ("implemented", False, True),
        ("done", False, True),
        ("draft", True, True),
        ("partial", False, False),
    ],
)
def test_orphan_check_applicability(
    tmp_path: Path, current_status: str, implemented_in_history: bool, applies: bool
) -> None:
    """FR03, direct on both functions: applicability comes from the current status OR the history."""
    from trw_mcp.state.validation.requirement_drift import detect_requirement_drift, orphan_check_applies

    baseline = _baseline(_FR01, implemented=implemented_in_history)  # its evidence file is absent under tmp_path

    findings = detect_requirement_drift(
        baseline,
        prd_text("PRD-X-001", current_status, [_FR01]),
        current_status=current_status,
        repo_root=tmp_path,
        source_root=tmp_path,
    )

    assert orphan_check_applies(baseline, current_status) is applies
    assert _findings(findings) == ([("PRD-X-001-FR01", "orphaned", (), "evidence artifact missing")] if applies else [])


def test_approved_never_implemented_entry_says_not_applicable(tmp_path: Path) -> None:
    """FR03: the per-PRD entry names the skipped orphan check, even with the evidence file absent."""
    repo = Repo(tmp_path)
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", [_FR01]))
    repo.commit("approve")

    entry = _compute_for(tmp_path, ["PRD-X-001"])["prds"]["PRD-X-001"]

    assert (entry["baseline_status"], entry["orphan_check"], entry["findings"]) == ("resolved", "not_applicable", [])


def test_empty_baseline_is_a_defined_outcome(tmp_path: Path) -> None:
    """An approved-or-later PRD with no verification.mappings resolves with no finding (the PRD-CORE-191 shape)."""
    from trw_mcp.state.validation.requirement_drift import detect_requirement_drift

    repo = Repo(tmp_path)
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", []))
    approval = repo.commit("approve with no mappings")
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "implemented", [_FR01]))
    repo.commit("implemented, one mapping added after approval")

    entry = _compute_for(tmp_path, ["PRD-X-001"])["prds"]["PRD-X-001"]

    assert (entry["baseline_status"], entry["baseline_sha"]) == ("resolved", approval)
    assert (entry["orphan_check"], entry["findings"]) == ("applied", [])
    empty = _baseline(implemented=True)
    assert detect_requirement_drift(empty, "", current_status=None, repo_root=tmp_path, source_root=tmp_path) == []


# --------------------------------------------------------------------------- #
# NFR01 — budget on the real PRD-CORE-191 file; §7 reachability ban
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_191 = _REPO_ROOT / "docs/requirements-aare-f/prds/PRD-CORE-191-acceptable-failure-schema-deliver-override.md"


@pytest.mark.skipif(not _CORE_191.is_file(), reason="PRD-CORE-191 lives in the monorepo checkout")
def test_baseline_resolves_prd_core_191() -> None:
    """Correctness half (unmarked, gating): the speed half below only measures time."""
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    resolution = resolve_requirement_baseline("PRD-CORE-191", _CORE_191)
    assert resolution.status == "resolved" and resolution.repo_root is not None


@pytest.mark.requires_local_timing
@pytest.mark.skipif(not _CORE_191.is_file(), reason="PRD-CORE-191 lives in the monorepo checkout")
def test_baseline_plus_drift_on_prd_core_191_within_budget() -> None:
    """NFR01: resolve + detect for one scoped PRD <= 10s, best of 3, on the real repository."""
    import time

    from tests._timing import assert_budget
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline
    from trw_mcp.state.validation.requirement_drift import detect_requirement_drift
    from trw_mcp.tools._deliver_capability_integration import _source_root

    best = float("inf")
    for _attempt in range(3):
        started = time.monotonic()
        resolution = resolve_requirement_baseline("PRD-CORE-191", _CORE_191)
        if resolution.status != "resolved" or resolution.repo_root is None:
            pytest.fail("resolve_requirement_baseline did not resolve mid-timing loop")
        detect_requirement_drift(
            resolution,
            _CORE_191.read_text(encoding="utf-8"),
            current_status=resolution.current_status,
            repo_root=resolution.repo_root,
            source_root=_source_root(),
        )
        best = min(best, time.monotonic() - started)
    assert_budget("requirement_drift_wall_time", best, 10.0, "s")


def test_no_reachability_construct_in_the_drift_modules() -> None:
    """§7: the drift guard reuses verify_chain; it builds no call graph of its own."""
    src = Path(__file__).resolve().parents[1] / "src/trw_mcp"
    modules = (
        "state/validation/requirement_baseline.py",
        "state/validation/requirement_drift.py",
        "tools/_deliver_requirement_drift.py",
    )
    combined = "\n".join((src / module).read_text(encoding="utf-8") for module in modules)
    for banned in ("call_graph", "CallGraph", "build_call_graph", "reachable_from"):
        assert banned not in combined, f"reachability construct {banned!r} found in a drift module"


_LOOSE: Mapping = {**_FR01, "criteria": ["loose"]}


@pytest.mark.parametrize(
    "current",
    [
        pytest.param([_LOOSE, _FR01, _FR02], id="weakened_first"),
        pytest.param([_FR01, _LOOSE, _FR02], id="weakened_last"),
    ],
)
def test_duplicate_current_requirement_id_is_refused_in_either_order(tmp_path: Path, current: list[Mapping]) -> None:
    """core321-s2 r1 P2: a second mapping with the same id must not hide a weakened one, whatever the row order."""
    from trw_mcp.state.validation.requirement_drift import DuplicateRequirementIdError, detect_requirement_drift

    with pytest.raises(DuplicateRequirementIdError, match="PRD-X-001-FR01"):
        detect_requirement_drift(
            _baseline(_FR01, _FR02),
            prd_text("PRD-X-001", "approved", list(current)),
            current_status="approved",
            repo_root=tmp_path,
            source_root=tmp_path,
        )


def test_duplicate_baseline_requirement_id_is_refused(tmp_path: Path) -> None:
    from trw_mcp.state.validation.requirement_drift import DuplicateRequirementIdError, detect_requirement_drift

    with pytest.raises(DuplicateRequirementIdError, match="baseline"):
        detect_requirement_drift(
            _baseline(_FR01, _LOOSE, _FR02),
            prd_text("PRD-X-001", "approved", [_FR01, _FR02]),
            current_status="approved",
            repo_root=tmp_path,
            source_root=tmp_path,
        )


# --------------------------------------------------------------------------- #
# FR04 — match_amendment (Slice 3)
# --------------------------------------------------------------------------- #

_APPROVED_ON = "2026-01-02"
_TODAY = "2026-09-26"


def _row(
    date: str = _APPROVED_ON,
    *,
    rid: str = "PRD-X-001-FR01",
    reason: str = "narrowed",
    owner: str = "lead",
    expiry: str = "2026-12-31",
) -> Any:
    from trw_mcp.state.validation.requirement_drift import AmendmentRow

    return AmendmentRow(requirement=rid, date=date, reason=reason, owner=owner, expiry=expiry)


def _match(rows: list[Any], mode: str = "warn", rid: str = "PRD-X-001-FR01") -> tuple[bool, str | None]:
    from datetime import date

    from trw_mcp.state.validation.requirement_drift import match_amendment

    block: Literal["warn", "block"] = "block" if mode == "block" else "warn"
    result = match_amendment(rid, rows, date.fromisoformat(_APPROVED_ON), block, date.fromisoformat(_TODAY))
    return result.recorded, result.reason


@pytest.mark.parametrize(
    ("row_date", "expected"),
    [
        ("2026-01-01", (False, "amendment_predates_approval")),
        ("2026-01-02", (True, None)),
        ("2026-01-03", (True, None)),
        ("2026-01-02T23:59:00+00:00", (True, None)),
        ("2026-01-01T23:59:00+00:00", (False, "amendment_predates_approval")),
        ("not a date", (False, "amendment_incomplete")),
    ],
    ids=["day_before", "same_day", "day_after", "datetime_same_day", "datetime_day_before", "unparseable"],
)
def test_match_amendment_date_boundary(row_date: str, expected: tuple[bool, str | None]) -> None:
    """FR04: dates compare as DATES only, inclusive of the approval day."""
    assert _match([_row(row_date)]) == expected


@pytest.mark.parametrize(
    ("row", "mode", "expected"),
    [
        ({"owner": ""}, "block", (False, "amendment_incomplete")),
        ({"expiry": ""}, "block", (False, "amendment_incomplete")),
        ({"expiry": "2026-09-25"}, "block", (False, "amendment_expired")),
        ({"expiry": _TODAY}, "block", (True, None)),
        ({}, "block", (True, None)),
        ({"owner": "", "expiry": ""}, "warn", (True, None)),
        ({"expiry": "2026-09-25"}, "warn", (True, None)),
        ({"reason": ""}, "warn", (False, "amendment_incomplete")),
    ],
    ids=[
        "no_owner",
        "no_expiry",
        "expired",
        "expires_today",
        "complete",
        "warn_needs_neither",
        "warn_expired",
        "no_reason",
    ],
)
def test_match_amendment_block_fields(row: dict[str, str], mode: str, expected: tuple[bool, str | None]) -> None:
    """FR04: under block a row needs Owner and an unexpired Expiry (inclusive of today); under warn neither."""
    assert _match([_row(**row)], mode) == expected


def test_match_amendment_id_and_row_choice() -> None:
    """FR04: only a row naming the SAME id matches; any qualifying row records, else the first failure is named."""
    assert _match([_row(rid="PRD-X-001-FR02")]) == (False, None)
    assert _match([_row(rid="PRD-X-001-FR010")]) == (False, None)
    assert _match([]) == (False, None)
    assert _match([_row("2026-01-01"), _row()]) == (True, None)
    assert _match([_row(owner=""), _row(expiry="2026-01-01")], "block") == (False, "amendment_incomplete")


def test_amendment_rows_reads_only_its_section() -> None:
    """FR04: rows come from the ``## Requirement Amendments`` table; a same-shaped table elsewhere is ignored."""
    from trw_mcp.state.validation.requirement_drift import AmendmentRow, amendment_rows

    header = "| Requirement | Date | Reason | Owner | Expiry |\n|---|---|---|---|---|\n"
    text = (
        "# PRD\n\n## Notes\n\n" + header + "| PRD-X-001-FR09 | 2026-01-05 | elsewhere | a | 2099-01-01 |\n\n"
        "## Requirement Amendments\n\n" + header + "| PRD-X-001-FR01 | 2026-01-05 | narrowed | lead |  |\n"
        "| short row |\n\n## Changelog\n\n" + header + "| PRD-X-001-FR03 | 2026-01-05 | after | b | 2099-01-01 |\n"
    )

    assert amendment_rows(text) == (AmendmentRow("PRD-X-001-FR01", "2026-01-05", "narrowed", "lead", ""),)
    assert amendment_rows("# PRD without the section\n") == ()


def test_markdown_tables_groups_runs_and_drops_only_the_leading_separator() -> None:
    """FR04's shared table reader: a maximal ``|`` run is one table; only the row after the header is a separator."""
    from trw_mcp.state.validation.chain_declarations import MarkdownTable, markdown_tables

    text = "| A | B |\n|---|:---:|\n| 1 | 2 |\n| short |\n\nprose\n| C |\n| 3 |\n"

    assert markdown_tables(text) == [
        MarkdownTable(("A", "B"), (("1", "2"), ("short",))),
        MarkdownTable(("C",), (("3",),)),
    ]
    assert markdown_tables("no tables here\n") == []
