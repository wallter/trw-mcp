"""Contract assertions over the audit protocol (PRD-QUAL-128 FR05-FR08, FR11, NFR03).

These tests replace 22 phrase-lock literals that used to pin whole sentences out
of ``trw-mcp/tests/test_bundled_agents.py``. Each one now asserts what the
prompt must *guarantee* — a schema key set, an enum, a cross-surface parity, a
heading disjointness — so rewording a prompt costs one edit instead of two.

The trade is only safe if the replacements still fail closed, so every
conversion is paired with a planted-violation fixture and
:func:`test_every_conversion_has_a_failing_fixture` fails when one is missing.
Disposition for all 35 original literals: PRD-QUAL-128 §14.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._audit_protocol_support import (
    Protocol,
    config_max_audit_cycles,
    fenced_yaml,
    level_two_headings,
    load_protocol,
    report_schema,
    section,
    strip_fragments,
    table_with_header,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

if not (REPO_ROOT / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )


# --------------------------------------------------------------------------
# FR08 — retired-identifier denylist (KEEP: an identifier is a contract)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RetiredIdentifier:
    """One retired identifier no shipped surface may reintroduce."""

    text: str
    #: The two raw event names may appear inside a sentence that documents their
    #: RETIREMENT — trw-auditor.md names them to explain why there is nothing to
    #: check. Any other occurrence re-mandates a check whose producer was deleted
    #: in PRD-FIX-076, which recorded "missing" on every audit and taught agents
    #: to discount findings. A mandating use carries no retirement notice.
    retirement_notice_exempt: bool = False


#: Union of the pre-PRD-QUAL-128 agent-side list (4, in test_bundled_agents.py)
#: and skill-side list (4, in test_bundled_skills.py); two overlapped, so six.
RETIRED_IDENTIFIERS: tuple[RetiredIdentifier, ...] = (
    RetiredIdentifier("Verify the implementer logged the pre-coding checklist for this PRD"),
    RetiredIdentifier("record `self_review_alignment: missing`"),
    RetiredIdentifier("preflight_verification:"),
    RetiredIdentifier("self_review_alignment: matches|underreported|missing"),
    RetiredIdentifier("pre_implementation_checklist_complete", retirement_notice_exempt=True),
    RetiredIdentifier("pre_audit_self_review", retirement_notice_exempt=True),
)

_RETIREMENT_NOTICE = re.compile(r"(?i)\bretired\b")


def find_retired_identifiers(surfaces: dict[str, str]) -> list[tuple[str, str]]:
    """Return ``(surface, identifier)`` for every live reintroduction."""
    hits: list[tuple[str, str]] = []
    for name, text in sorted(surfaces.items()):
        for paragraph in re.split(r"\n\s*\n", text):
            for entry in RETIRED_IDENTIFIERS:
                if entry.text not in paragraph:
                    continue
                if entry.retirement_notice_exempt and _RETIREMENT_NOTICE.search(paragraph):
                    continue
                hits.append((name, entry.text))
    return hits


def assert_no_retired_identifiers(protocol: Protocol) -> None:
    hits = find_retired_identifiers(protocol.surfaces)
    assert not hits, "retired identifier reintroduced: " + "; ".join(f"{s} -> {i!r}" for s, i in hits)


# --------------------------------------------------------------------------
# FR05 — the report schema is parsed, not pinned
# --------------------------------------------------------------------------

EXPECTED_FINDING_KEYS = frozenset(
    {"severity", "category", "legacy_category", "evidence_tier", "location", "issue", "evidence", "fix"}
)
EXPECTED_PRIOR_LEARNING_KEYS = frozenset({"known_patterns", "verified_patterns", "missed_patterns"})
EXPECTED_LEARNING_FIELDS = ("tags", "phase_affinity")
EXPECTED_ANGLE_COUNT = 9


def _enum(value: object) -> list[str]:
    assert isinstance(value, str), f"expected a pipe-separated enum, got {value!r}"
    return [part.strip() for part in value.split("|")]


def assert_report_schema_contract(protocol: Protocol) -> None:
    """Section G's schema carries the finding keys, both enums, and the angle set."""
    schema = report_schema(protocol)
    for required in ("fr_verdicts", "prior_learning_verification", "summary"):
        assert required in schema, f"Section G report schema lost its top-level `{required}` key"
    finding = schema["fr_verdicts"][0]["findings"][0]
    finding_keys = frozenset(finding)
    assert finding_keys == EXPECTED_FINDING_KEYS, (
        f"Section G finding keys drifted: missing {sorted(EXPECTED_FINDING_KEYS - finding_keys)}, "
        f"unexpected {sorted(finding_keys - EXPECTED_FINDING_KEYS)}"
    )

    taxonomy = section(protocol.framework, "B")
    _, category_rows = table_with_header(taxonomy, "Category", "Description")
    declared = [row[0] for row in category_rows]
    assert _enum(finding["category"]) == declared, (
        f"Section G `category` enum {_enum(finding['category'])} != Section B taxonomy {declared}"
    )

    _, legacy_rows = table_with_header(taxonomy, "Legacy label")
    assert _enum(finding["legacy_category"]) == [row[0] for row in legacy_rows] + ["null"], (
        "Section G `legacy_category` enum does not match Section B's legacy mapping plus null"
    )

    plv = schema["prior_learning_verification"]
    assert isinstance(plv, dict), "`prior_learning_verification` must be a MAPPING, not a scalar enum (drift D5)"
    assert frozenset(plv) == EXPECTED_PRIOR_LEARNING_KEYS, f"prior-learning keys drifted: {sorted(plv)}"

    angles = schema["summary"]["audit_angles_completed"]
    assert isinstance(angles, list) and len(angles) == EXPECTED_ANGLE_COUNT, (
        f"expected {EXPECTED_ANGLE_COUNT} declared audit angles, got {angles}"
    )

    assert "trw_recall(" in protocol.auditor, "the auditor no longer calls trw_recall for prior-learning recall"
    capture = protocol.auditor.split("Learning capture")[-1]
    for field_name in EXPECTED_LEARNING_FIELDS:
        assert f"`{field_name}`" in capture, f"learning-capture block no longer names `{field_name}`"

    # Section E states the finding schema standalone; Section G states it again
    # nested under fr_verdicts, because a report has to show the shape in place.
    # Two statements in one file is still two statements, so pin them together —
    # that in-file pair is the same mechanism that produced drift D6 across files.
    section_e = fenced_yaml(section(protocol.framework, "E"))
    assert len(section_e) == 1, f"Section E must hold exactly one finding schema, found {len(section_e)}"
    standalone = section_e[0]
    assert frozenset(standalone) == finding_keys, (
        f"Section E's finding schema and Section G's disagree: "
        f"E-only {sorted(frozenset(standalone) - finding_keys)}, G-only {sorted(finding_keys - frozenset(standalone))}"
    )
    for enum_field in ("category", "legacy_category", "evidence_tier"):
        assert _enum(standalone[enum_field]) == _enum(finding[enum_field]), (
            f"Section E and Section G disagree on the `{enum_field}` enum"
        )

    normalized = {
        re.sub(r"\s*\(.*?\)\s*$", "", h.removeprefix("## ")).casefold()
        for h in level_two_headings(protocol.implementer)
    }
    assert "pre-implementation checklist" in normalized, (
        "trw-implementer lost its pre-implementation checklist heading (normalized name, PRD id free)"
    )


# --------------------------------------------------------------------------
# FR06 — verdict thresholds are a cross-surface parity, not two pinned copies
# --------------------------------------------------------------------------

_VERDICTS = ("PASS", "CONDITIONAL", "FAIL")
_VERDICT_RE = re.compile(r"\b(PASS|CONDITIONAL|FAIL)\b")
_SEVERITY_RE = re.compile(r"\bP[012]\b")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def verdict_criteria(text: str) -> dict[str, str] | None:
    """Parse a verdict-criteria table into ``{verdict: criteria}``, or None."""
    try:
        _, rows = table_with_header(text, "Verdict", "Criteria")
    except AssertionError:
        return None
    parsed = {row[0].upper(): row[1] for row in rows if row and row[0].upper() in _VERDICTS}
    return parsed if set(parsed) == set(_VERDICTS) else None


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def threshold_sentences(text: str) -> list[tuple[str, str]]:
    """Sentences that STATE a threshold: one verdict token plus a severity token.

    A sentence naming all three verdicts is a *reference* ("use the Section E
    criteria (P0/P1/P2; PASS/CONDITIONAL/FAIL)"), not a restatement — which is
    the distinction that lets pointers stay legal.
    """
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            continue
        for sentence in _SENTENCE_RE.split(line):
            named = set(_VERDICT_RE.findall(sentence))
            if len(named) == 1 and _SEVERITY_RE.search(sentence):
                found.append((named.pop(), sentence.strip()))
    return found


def assert_verdict_parity(protocol: Protocol) -> None:
    """Exactly one surface defines the thresholds; every restatement matches it."""
    canonical = verdict_criteria(section(protocol.framework, "E"))
    assert canonical is not None, "audit-framework.md Section E no longer defines the verdict criteria"

    for name, text in sorted(protocol.surfaces.items()):
        other = verdict_criteria(text)
        assert other is None or other == canonical, (
            f"{name} restates the verdict criteria differently: {other} != {canonical}"
        )
        for verdict, sentence in threshold_sentences(text):
            assert _normalize(sentence).find(_normalize(canonical[verdict])) >= 0, (
                f"{name} states a {verdict} threshold that differs from the single source.\n"
                f"  surface: {sentence}\n  single source: {canonical[verdict]}"
            )

    # Section G restates each verdict as a YAML comment. Same file, second form:
    # its numeric thresholds must track the table's or drift D3 recurs in place.
    for verdict, sentence in threshold_sentences(section(protocol.framework, "G")):
        assert re.findall(r"\d+", sentence) == re.findall(r"\d+", canonical[verdict]), (
            f"Section G's {verdict} comment disagrees with the Section E table.\n"
            f"  comment: {sentence}\n  table: {canonical[verdict]}"
        )

    expected = config_max_audit_cycles()
    stated = re.findall(r"(?i)maximum audit cycles before escalation:\s*(\d+)", protocol.framework)
    assert stated, "the single source no longer states the maximum audit cycle count"
    for value in stated:
        assert int(value) == expected, (
            f"prose says max audit cycles = {value}, TRWConfig.max_audit_cycles default = {expected}"
        )
    for name, text in sorted(protocol.surfaces.items()):
        assert not re.findall(r"(?i)maximum audit cycles before escalation:\s*\d+", text), (
            f"{name} restates the max-audit-cycle default; its owner is TRWConfig.max_audit_cycles"
        )


# --------------------------------------------------------------------------
# FR07 — the adapter's headings are disjoint from the base's
# --------------------------------------------------------------------------

AUTHORED_WORD_BUDGET = 500
TOTAL_WORD_BUDGET = 900


def assert_adapter_disjoint(protocol: Protocol) -> None:
    """The adapter restates NO section the base owns, and names its base."""
    base_headings = level_two_headings(protocol.auditor)
    adapter_headings = level_two_headings(protocol.adapter)
    assert base_headings, "the base auditor has no level-2 headings; the disjointness check would be vacuous"
    overlap = base_headings & adapter_headings
    assert overlap == set(), f"the adapter restates sections the base already owns: {sorted(overlap)}"

    body = protocol.adapter.split("---", 2)[2]
    assert "trw-auditor.md" in "\n".join(body.splitlines()[:30]), "the adapter no longer names its base file"

    authored = strip_fragments(body)
    assert len(authored.split()) <= AUTHORED_WORD_BUDGET, "authored prose exceeds the thin-adapter budget"
    assert len(body.split()) <= TOTAL_WORD_BUDGET, "total prompt exceeds the thin-adapter budget"


# --------------------------------------------------------------------------
# NFR03 — the rewrite may not weaken a constraint
# --------------------------------------------------------------------------

#: Constraint clauses captured from the surfaces at the commit BEFORE this PRD
#: rewrote them. The post-change set must be a superset: a rewrite may add a
#: NEVER clause or a denial, never drop one.
BASELINE_NEVER_CLAUSES: dict[str, tuple[str, ...]] = {
    "auditor": (
        "NEVER modify code files",
        'NEVER accept "tests pass" as evidence of spec compliance',
        "NEVER downgrade severity to avoid blocking delivery",
    ),
    "skill": (
        "NEVER modify code files",
        'NEVER accept "tests pass" as evidence of spec compliance',
        "NEVER use PARTIAL to soften a failed acceptance criterion",
        "NEVER skip NFR checklist items",
        "NEVER downgrade severity to avoid blocking",
    ),
}
BASELINE_DISALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "auditor": frozenset({"Edit", "Write", "NotebookEdit", "WebSearch", "WebFetch"}),
    "adapter": frozenset({"Bash", "Edit", "Write", "NotebookEdit", "WebSearch", "WebFetch"}),
}
REQUIRED_FRAGMENTS = ("trw:mcp-retry-protocol", "trw:negative-existence-rule")


def _disallowed_tools(text: str) -> frozenset[str]:
    import yaml

    _, frontmatter, _ = text.split("---", 2)
    meta = yaml.safe_load(frontmatter)
    return frozenset(str(item) for item in meta.get("disallowedTools", []))


def assert_constraints_survive(protocol: Protocol) -> None:
    bodies = {"auditor": protocol.auditor, "skill": protocol.skill, "adapter": protocol.adapter}
    for surface, clauses in BASELINE_NEVER_CLAUSES.items():
        for clause in clauses:
            assert clause in bodies[surface], f"{surface} lost a NEVER clause: {clause!r}"
    for surface, denied in BASELINE_DISALLOWED_TOOLS.items():
        current = _disallowed_tools(bodies[surface])
        assert denied <= current, f"{surface} weakened disallowedTools: lost {sorted(denied - current)}"
    for surface in ("auditor", "adapter"):
        for fragment in REQUIRED_FRAGMENTS:
            assert f"<!-- {fragment}:start -->" in bodies[surface], f"{surface} lost the {fragment} fragment"


# --------------------------------------------------------------------------
# FR11 — every conversion carries a planted-violation fixture
# --------------------------------------------------------------------------

Planter = Callable[[Protocol], Protocol]
Checker = Callable[[Protocol], None]


@dataclass(frozen=True)
class Conversion:
    """One ledger row converted from a phrase lock to a contract assertion."""

    ledger_row: int
    literal: str
    check: Checker
    plant: Planter


def _drop_from_section_g_enum(value: str) -> Planter:
    """Remove one member from an enum inside Section G's report schema."""
    return lambda p: p.with_section_g(f"{value}|", "")


CONVERSIONS: dict[str, Conversion] = {
    "C07-legacy-category-enum": Conversion(
        7, "legacy_category: prd-ambiguity|...|null", assert_report_schema_contract, _drop_from_section_g_enum("dry")
    ),
    "C08-category-spec-gap": Conversion(
        8, "category: spec_gap", assert_report_schema_contract, _drop_from_section_g_enum("spec_gap")
    ),
    "C09-category-enum": Conversion(
        9,
        "category: spec_gap|impl_gap|...",
        assert_report_schema_contract,
        lambda p: p.with_section_g("|traceability_gap\n", "\n"),
    ),
    "C10-prior-learning-key": Conversion(
        10,
        "prior_learning_verification:",
        assert_report_schema_contract,
        lambda p: p.with_section_g("\nprior_learning_verification:\n", "\nunrelated_block:\n"),
    ),
    "C11-known-patterns": Conversion(
        11,
        "known_patterns: []",
        assert_report_schema_contract,
        lambda p: p.with_section_g("  known_patterns: []\n", ""),
    ),
    "C12-verified-patterns": Conversion(
        12,
        "verified_patterns: []",
        assert_report_schema_contract,
        lambda p: p.with_section_g("  verified_patterns: []\n", ""),
    ),
    "C13-missed-patterns": Conversion(
        13,
        "missed_patterns: []",
        assert_report_schema_contract,
        lambda p: p.with_section_g("  missed_patterns: []\n", ""),
    ),
    "C14-recall-call": Conversion(
        14,
        "Call `trw_recall(query='<prd-domain> audit-finding')`",
        assert_report_schema_contract,
        lambda p: p.with_auditor("trw_recall(query=", "the recall step ("),
    ),
    "C15-learning-tags": Conversion(
        15,
        '- `tags`: ["audit-finding", ...]',
        assert_report_schema_contract,
        lambda p: p.with_auditor("- `tags`:", "- tags:"),
    ),
    "C16-learning-phase-affinity": Conversion(
        16,
        "`phase_affinity` named in the learning-capture block",
        assert_report_schema_contract,
        # phase_affinity moved into the `metadata` bag (PRD-CORE-234), so it is
        # no longer its own `- `phase_affinity`:` bullet. The contract asserts
        # the BACKTICKED token appears in the capture block, so stripping the
        # backticks is still the minimal violation that must fail closed.
        lambda p: p.with_auditor("`phase_affinity`", "phase_affinity"),
    ),
    "C17-audit-angles": Conversion(
        17,
        "audit_angles_completed: [spec, vision, ...]",
        assert_report_schema_contract,
        lambda p: p.with_section_g(", traceability]", "]"),
    ),
    "C18-pass-row": Conversion(
        18,
        "| **PASS** | Zero P0 ... |",
        assert_verdict_parity,
        lambda p: p.with_skill(
            "### Step 7.5: Spec Reconciliation",
            "PASS means all FRs pass and there are no P0/P1 findings.\n\n### Step 7.5: Spec Reconciliation",
        ),
    ),
    "C19-conditional-row": Conversion(
        19,
        "| **CONDITIONAL** | Zero P0 ... |",
        assert_verdict_parity,
        lambda p: p.with_auditor(
            "**PRD and sprint status review:**",
            "CONDITIONAL means zero P0 and 1-4 P1 findings.\n\n**PRD and sprint status review:**",
        ),
    ),
    "C20-fail-row": Conversion(
        20,
        "| **FAIL** | Any P0 ... |",
        assert_verdict_parity,
        lambda p: p.with_skill(
            "### Step 8: Summary",
            "FAIL means any P0 or 5+ P1 findings.\n\n### Step 8: Summary",
        ),
    ),
    "C21-max-audit-cycles": Conversion(
        21,
        "Maximum audit cycles before escalation: 3 (...)",
        assert_verdict_parity,
        lambda p: p.with_framework(
            "Maximum audit cycles before escalation: 3.", "Maximum audit cycles before escalation: 5."
        ),
    ),
    "C22-pass-comment": Conversion(
        22,
        "# PASS: zero P0, zero P1, ...",
        assert_verdict_parity,
        lambda p: p.with_framework("# PASS: zero P0, zero P1,", "# PASS: zero P0, up to 2 P1,"),
    ),
    "C23-conditional-comment": Conversion(
        23,
        "# CONDITIONAL: zero P0 and 1-2 P1 ...",
        assert_verdict_parity,
        lambda p: p.with_framework("# CONDITIONAL: zero P0 and 1-2 P1", "# CONDITIONAL: zero P0 and 1-4 P1"),
    ),
    "C24-fail-comment": Conversion(
        24,
        "# FAIL: any P0, 3+ P1 findings, ...",
        assert_verdict_parity,
        lambda p: p.with_framework("# FAIL: any P0, 3+ P1 findings", "# FAIL: any P0, 5+ P1 findings"),
    ),
    "C25-adapter-names-base": Conversion(
        25,
        "trw-auditor.md (in first 30 body lines)",
        assert_adapter_disjoint,
        lambda p: p.with_adapter("trw-auditor.md", "the sibling base agent", count=2),
    ),
    "C26-audit-protocol-heading": Conversion(
        26,
        "## Audit Protocol (7 Phases)",
        assert_adapter_disjoint,
        lambda p: p.with_adapter("## Red-team lens", "## Audit Protocol (7 Phases)\n\n## Red-team lens"),
    ),
    "C27-output-contract-heading": Conversion(
        27,
        "## Output Contract",
        assert_adapter_disjoint,
        lambda p: p.with_adapter("## Output delta", "## Rationalization Watchlist\n\n## Output delta"),
    ),
    "C28-implementer-checklist": Conversion(
        28,
        "Pre-Implementation Checklist (PRD-QUAL-056-FR03)",
        assert_report_schema_contract,
        lambda p: p.with_implementer("## Pre-Implementation Checklist", "## Before You Start"),
    ),
}

#: FR11's registry: conversion id -> the fixture that proves it fails closed.
NEGATIVE_FIXTURES: dict[str, Planter] = {key: conversion.plant for key, conversion in CONVERSIONS.items()}


@pytest.fixture(scope="module")
def protocol() -> Protocol:
    return load_protocol()


def test_report_schema_keys_and_enums_are_derived_not_pinned(protocol: Protocol) -> None:
    """FR05: the schema is parsed from Section G; its prose wording is free."""
    assert_report_schema_contract(protocol)


def test_reworded_prose_around_the_schema_still_passes(protocol: Protocol) -> None:
    """FR05 acceptance: rewording is free, removing a key is not."""
    reworded = protocol.with_framework(
        "This is the one definition of the audit report.",
        "The audit report is defined here, once, and nowhere else.",
    )
    assert_report_schema_contract(reworded)


def test_section_e_and_section_g_finding_schemas_cannot_drift_apart(protocol: Protocol) -> None:
    """The in-file pair is the same drift mechanism D6 produced across files."""
    with pytest.raises(AssertionError, match="Section E"):
        assert_report_schema_contract(protocol.with_framework("evidence_tier: direct", "evidence_grade: direct"))


def test_verdict_thresholds_match_the_single_source(protocol: Protocol) -> None:
    """FR06: one definition, every restatement identical, prose == config default."""
    assert_verdict_parity(protocol)


def test_adapter_headings_are_disjoint_from_the_base(protocol: Protocol) -> None:
    """FR07: generalized from two named headings to every heading the base owns."""
    assert_adapter_disjoint(protocol)


def test_renaming_a_base_heading_alone_does_not_break_the_adapter(protocol: Protocol) -> None:
    """FR07 acceptance: base renames were pure coupling; they are free now."""
    assert_adapter_disjoint(protocol.with_auditor("## Audit Protocol (7 Phases)", "## Audit Protocol"))


def test_retired_identifiers_are_absent_from_every_surface(protocol: Protocol) -> None:
    """FR08: 6 identifiers over 18 surfaces (11 bundled agents + 7 projections)."""
    assert len(protocol.surfaces) == 18, f"expected 18 scanned surfaces, got {len(protocol.surfaces)}"
    assert len(RETIRED_IDENTIFIERS) == 6, "the denylist is the union of the prior agent and skill lists"
    assert_no_retired_identifiers(protocol)


@pytest.mark.parametrize("identifier", [e.text for e in RETIRED_IDENTIFIERS], ids=lambda t: t[:32])
def test_a_planted_retired_identifier_fails_on_any_surface(protocol: Protocol, identifier: str) -> None:
    """A mandating reintroduction fails, naming the surface and the identifier."""
    target = "skill:.agents/skills/trw-audit/SKILL.md"
    planted = protocol.with_surface(
        target, protocol.surfaces[target] + f"\n\nCheck that {identifier} was logged before auditing.\n"
    )
    with pytest.raises(AssertionError) as excinfo:
        assert_no_retired_identifiers(planted)
    assert target in str(excinfo.value)


def test_readonly_and_evidence_contracts_survive_the_rewrite(protocol: Protocol) -> None:
    """NFR03: the pre-change constraint set is a subset of the post-change set."""
    assert_constraints_survive(protocol)


@pytest.mark.parametrize("clause", BASELINE_NEVER_CLAUSES["auditor"])
def test_dropping_a_never_clause_is_caught(protocol: Protocol, clause: str) -> None:
    with pytest.raises(AssertionError):
        assert_constraints_survive(protocol.with_auditor(clause, "consider not doing this"))


@pytest.mark.parametrize("conversion_id", sorted(CONVERSIONS))
def test_each_conversion_fails_closed_on_its_planted_violation(protocol: Protocol, conversion_id: str) -> None:
    """FR11: the regression the old literal caught still fails the new assertion."""
    conversion = CONVERSIONS[conversion_id]
    conversion.check(protocol)  # clean tree passes, so the fixture is what fails
    with pytest.raises(AssertionError):
        conversion.check(conversion.plant(protocol))


def test_every_conversion_has_a_failing_fixture() -> None:
    """FR11 meta-test: a conversion without a negative fixture cannot ship."""
    assert set(CONVERSIONS) == set(NEGATIVE_FIXTURES)
    assert len(CONVERSIONS) == 22, "the ledger converts 22 of 35 literals (PRD-QUAL-128 §14)"
    rows = sorted(c.ledger_row for c in CONVERSIONS.values())
    assert rows == list(range(7, 29)), f"conversion ids must cover ledger rows 7-28, got {rows}"
