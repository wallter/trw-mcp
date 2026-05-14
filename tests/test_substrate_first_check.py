"""Tests for the Substrate-First gate (PRD-DIST-218 FR-2).

Covers:
  - AC-2 — gate triggers on Sprint 112 Phase 1 style diff (FAIL)
  - AC-3 — gate passes on a structural-alternative PRD (PASS)
  - AC-5 — gate triggers on cataloged hand-curation patterns
  - WARN/FAIL/PASS/disabled state machine
  - Protocol-internal name allowlist
  - Operator sign-off + frontmatter ack interaction
"""

from __future__ import annotations

import pytest

from trw_mcp.tools._substrate_first_check import (
    ENV_DISABLE,
    FlaggedCollection,
    SubstrateFirstResult,
    _count_top_level_entries,
    substrate_first_check,
)

# ---- helpers --------------------------------------------------------


def _prd(*, body: str, frontmatter: str = "") -> str:
    fm = ""
    if frontmatter:
        fm = "---\n" + frontmatter + "\n---\n\n"
    return fm + "# Test PRD\n\n" + body


def _flagged_python_block(name: str = "DOC_IMMUNE_NAMES") -> str:
    """Sprint 112 Phase 1 style hand-curated frozenset (8 entries > 5)."""
    return (
        "```python\n"
        f"{name} = frozenset({{\n"
        '    "__init__", "__str__", "__repr__", "__eq__",\n'
        '    "__hash__", "__lt__", "__le__", "__gt__",\n'
        "})\n"
        "```\n"
    )


# ---- _count_top_level_entries ---------------------------------------


def test_count_top_level_entries_empty() -> None:
    assert _count_top_level_entries("") == 0
    assert _count_top_level_entries("   \n  ") == 0


def test_count_top_level_entries_simple() -> None:
    assert _count_top_level_entries('"a", "b", "c"') == 3


def test_count_top_level_entries_skips_nested_commas() -> None:
    body = '"a", {"x": 1, "y": 2}, "b"'
    assert _count_top_level_entries(body) == 3


def test_count_top_level_entries_trailing_comma_ok() -> None:
    assert _count_top_level_entries('"a", "b", "c",') == 3


# ---- AC-3: structural-alternative PRD passes ------------------------


def test_passes_when_no_python_collections() -> None:
    prd = _prd(body="This PRD adds a per-repo inferred convention set.")
    result = substrate_first_check(prd)
    assert result.verdict == "pass"
    assert result.flagged_collections == []


def test_passes_when_python_block_under_threshold() -> None:
    body = '```python\nSMALL_ENUM = ("a", "b")\n```\n'
    result = substrate_first_check(_prd(body=body))
    assert result.verdict == "pass"


def test_passes_when_protocol_internal_name_allowlisted() -> None:
    body = '```python\n_TRW_INTERNAL_BASENAMES = frozenset({\n    "a", "b", "c", "d", "e", "f", "g", "h",\n})\n```\n'
    result = substrate_first_check(_prd(body=body))
    assert result.verdict == "pass"
    assert result.flagged_collections == []


# ---- AC-2: Sprint 112 Phase 1 fails ---------------------------------


def test_fails_on_sprint_112_phase_1_style_diff() -> None:
    prd = _prd(body=_flagged_python_block())
    result = substrate_first_check(prd)
    assert result.verdict == "fail"
    assert len(result.flagged_collections) == 1
    assert result.flagged_collections[0].name == "DOC_IMMUNE_NAMES"
    assert result.flagged_collections[0].entry_count >= 6
    assert "no Substrate-First acknowledgment" in result.diagnostic


# ---- WARN state: acknowledged + evidence, no sign-off ---------------


def test_warns_when_acknowledged_with_evidence_no_sign_off() -> None:
    body = _flagged_python_block() + (
        "\n## Substrate-First evidence\n\n"
        "Considered TS regex inference; rejected because language "
        "isn't on the supported list.\n"
    )
    prd = _prd(
        body=body,
        frontmatter="hand_curation_acknowledged: true",
    )
    result = substrate_first_check(prd)
    assert result.verdict == "warn"
    assert result.acknowledged is True
    assert result.evidence_section_present is True
    assert result.sign_off_present is False


# ---- PASS state: full acknowledgment with operator sign-off ---------


def test_passes_when_full_acknowledgment_with_sign_off() -> None:
    body = (
        _flagged_python_block()
        + "\n## Substrate-First evidence\n\n"
        + "Operator considered TS structural inference; chosen to defer.\n\n"
        + "<!-- substrate_first_sign_off: ops-2026-05-03 -->\n"
    )
    prd = _prd(
        body=body,
        frontmatter="hand_curation_acknowledged: true",
    )
    result = substrate_first_check(prd)
    assert result.verdict == "pass"
    assert result.sign_off_present is True


# ---- FAIL state: ack without evidence section -----------------------


def test_fails_when_acknowledged_but_no_evidence_section() -> None:
    body = _flagged_python_block()
    prd = _prd(body=body, frontmatter="hand_curation_acknowledged: true")
    result = substrate_first_check(prd)
    assert result.verdict == "fail"
    assert result.acknowledged is True
    assert result.evidence_section_present is False


# ---- disabled state -------------------------------------------------


def test_disabled_via_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DISABLE, "0")
    prd = _prd(body=_flagged_python_block())
    result = substrate_first_check(prd)
    assert result.verdict == "disabled"
    assert result.flagged_collections == []


# ---- AC-5: cataloged patterns ---------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "DOC_IMMUNE_NAMES",
        "TS_DOC_IMMUNE",
        "GO_DOC_IMMUNE",
        "RUST_DOC_IMMUNE",
        "ZIG_DOC_IMMUNE",
        "WEB_FRAMEWORK_NAMES",
    ],
)
def test_ac5_recall_on_audit_catalog_patterns(name: str) -> None:
    """Each of the 6 reverted Sprint 112 Phase 1 frozensets must FAIL."""
    prd = _prd(body=_flagged_python_block(name))
    assert substrate_first_check(prd).verdict == "fail"


# ---- multiple Python fences -----------------------------------------


def test_aggregates_across_multiple_fences() -> None:
    body = _flagged_python_block("LIST_A") + "\n" + _flagged_python_block("LIST_B")
    result = substrate_first_check(_prd(body=body))
    assert result.verdict == "fail"
    assert len(result.flagged_collections) == 2
    names = {fc.name for fc in result.flagged_collections}
    assert names == {"LIST_A", "LIST_B"}


# ---- extra_python_sources ------------------------------------------


def test_extra_python_sources_scanned() -> None:
    extra = '_NEW_VOCAB = frozenset({\n    "a", "b", "c", "d", "e", "f", "g", "h",\n})\n'
    prd = _prd(body="empty body, no inline diff")
    result = substrate_first_check(prd, extra_python_sources=[extra])
    assert result.verdict == "fail"
    assert result.flagged_collections[0].name == "_NEW_VOCAB"


# ---- to_payload contract -------------------------------------------


def test_to_payload_shape_round_trip() -> None:
    result = SubstrateFirstResult(
        verdict="fail",
        flagged_collections=[FlaggedCollection(name="X", kind="frozenset", entry_count=8, line_hint='"a","b"')],
        evidence_section_present=False,
        sign_off_present=False,
        acknowledged=False,
        diagnostic="d",
    )
    payload = result.to_payload()
    assert payload["verdict"] == "fail"
    assert isinstance(payload["flagged_collections"], list)
    assert payload["flagged_collections"][0]["entry_count"] == 8  # type: ignore[index]
    assert payload["evidence_section_present"] is False
    assert payload["acknowledged"] is False


# ---- threshold tunable ---------------------------------------------


def test_threshold_tunable() -> None:
    body = '```python\n_THREE = ("a", "b", "c")\n```\n'
    # Default threshold is 5 — passes
    assert substrate_first_check(_prd(body=body)).verdict == "pass"
    # Threshold=2 — same content now fails (3 > 2 entries)
    assert substrate_first_check(_prd(body=body), threshold=2).verdict == "fail"
