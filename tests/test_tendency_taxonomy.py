"""Tests for the AI-development tendency taxonomy (PRD-QUAL-109 FR-01).

The taxonomy is a closed enum + a *total* metadata registry: every member must
have exactly one metadata record, every record must have non-empty fields, and
the member set must equal the audited 9-tendency catalogue. Adding a 10th
member without metadata must fail ``test_tendency_taxonomy_metadata_total``.
"""

from __future__ import annotations

import pytest

from trw_mcp.tendencies.taxonomy import (
    TENDENCY_METADATA,
    TendencyMetadata,
    TendencyType,
)

# The exact 9-member catalogue from PRD-QUAL-109 FR-01.
_EXPECTED_MEMBERS = {
    "FAKE_DONE",
    "PREMATURE_SCAFFOLDING",
    "NIH_UNDER_RESEARCH",
    "QUOTA_GAMING",
    "BENCHMARK_SATURATION",
    "DOC_DRIFT",
    "CLAIM_PROPAGATION",
    "ADDITIVE_BACKLOG",
    "SELF_SILENCING",
}


def test_tendency_taxonomy_exact_member_set() -> None:
    """The enum is the exact, closed 9-member set (no more, no fewer)."""
    members = {m.name for m in TendencyType}
    assert members == _EXPECTED_MEMBERS
    assert len(TendencyType) == 9


def test_tendency_taxonomy_metadata_total() -> None:
    """Every member has exactly one metadata record; no orphan records (FR-01)."""
    metadata_keys = set(TENDENCY_METADATA.keys())
    member_set = set(TendencyType)
    # No member without metadata, no metadata without a member.
    assert metadata_keys == member_set


def test_tendency_metadata_fields_non_empty() -> None:
    """Each record has non-empty name/description/detection_signals/countermeasure."""
    for member in TendencyType:
        meta = TENDENCY_METADATA[member]
        assert isinstance(meta, TendencyMetadata)
        assert meta.name.strip(), f"{member} has empty name"
        assert meta.description.strip(), f"{member} has empty description"
        assert meta.detection_signals, f"{member} has no detection_signals"
        assert all(s.strip() for s in meta.detection_signals), f"{member} has blank signal"
        assert meta.countermeasure_pointer.strip(), f"{member} has empty countermeasure_pointer"


def test_countermeasure_pointer_is_string_reference_no_execution() -> None:
    """The countermeasure_pointer is a *string* reference, never a callable/gate."""
    for member in TendencyType:
        meta = TENDENCY_METADATA[member]
        assert isinstance(meta.countermeasure_pointer, str)
        assert not callable(meta.countermeasure_pointer)


def test_detection_signals_are_immutable_tuple() -> None:
    """detection_signals is a tuple (immutable closed vocabulary, US-2)."""
    for member in TendencyType:
        assert isinstance(TENDENCY_METADATA[member].detection_signals, tuple)


@pytest.mark.parametrize("member", list(TendencyType))
def test_each_member_addressable_by_name(member: TendencyType) -> None:
    """An agent can reach a member's metadata by the stable enum name (US-2)."""
    assert TendencyType[member.name] is member
    assert member in TENDENCY_METADATA
