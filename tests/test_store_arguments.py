"""PRD-CORE-251 FR03: the trw-mcp-decided half of a delegated store call.

These assertions used to live against ``_learning_to_memory_entry``, which built
a ``MemoryEntry`` by hand and handed it to ``backend.store``. Entry construction
now happens inside ``memory_store_impl`` (through the PRD-CORE-245 FR08
chokepoint), so what is left on this side — and what these tests cover — is the
routing decision, the metadata contract, the source whitelist, the enum coercion
and the anchor/assertion marshalling.

Coverage moved rather than deleted: every behaviour asserted here was asserted
before the delegation, against the retired builder.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from trw_mcp.state._store_arguments import build_store_arguments


def _args(**overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "summary": "s",
        "detail": "d",
        "tags": None,
        "shard_id": None,
        "source_type": "agent",
        "assertions": None,
        "type": "pattern",
        "confidence": "unverified",
        "domain": None,
        "phase_affinity": None,
        "protection_tier": "normal",
        "anchors": None,
        "impact": 0.5,
        "metadata": None,
        "scope": "auto",
    }
    kwargs.update(overrides)
    return build_store_arguments(**kwargs)


class TestRoutingAndNamespace:
    def test_project_route_uses_the_default_namespace(self) -> None:
        args = _args(scope="project")
        assert args.tier == "project"
        assert args.namespace == "default"

    def test_project_route_leaves_metadata_unstamped(self) -> None:
        """Back-compat: the project tier is the default and its ABSENCE is the signal."""
        assert _args(scope="project").metadata == {}

    def test_caller_injected_tier_is_stripped_on_a_project_route(self) -> None:
        """core185-METADATA-TIER-INJECT-5: routing is authoritative over caller metadata.

        A caller-supplied ``metadata["tier"]="user"`` would make ``tier_of_entry``
        mis-report the tier and divert a project entry into the box-wide user
        backend.
        """
        assert "tier" not in _args(scope="project", metadata={"tier": "user"}).metadata


class TestMetadataContract:
    def test_shard_id_becomes_metadata(self) -> None:
        assert _args(shard_id="shard-C").metadata == {"shard_id": "shard-C"}

    def test_no_shard_id_produces_empty_metadata(self) -> None:
        assert _args(shard_id=None).metadata == {}

    def test_empty_shard_id_string_produces_empty_metadata(self) -> None:
        assert _args(shard_id="").metadata == {}

    def test_caller_metadata_merges_with_shard_id(self) -> None:
        """PRD-DIST-254 FR02: caller-supplied metadata and shard_id co-exist."""
        metadata = _args(shard_id="shard-X", metadata={"utility_grade": "R3", "current_status": "current"}).metadata
        assert metadata["shard_id"] == "shard-X"
        assert metadata["utility_grade"] == "R3"
        assert metadata["current_status"] == "current"

    def test_caller_metadata_wins_on_collision_with_shard_id(self) -> None:
        assert _args(shard_id="internal", metadata={"shard_id": "caller"}).metadata["shard_id"] == "caller"

    def test_metadata_default_none_preserves_back_compat(self) -> None:
        assert _args(shard_id="only-shard", metadata=None).metadata == {"shard_id": "only-shard"}

    def test_metadata_without_shard_id_round_trips_keys(self) -> None:
        assert _args(metadata={"utility_grade": "R5", "evidence_count": "2"}).metadata == {
            "utility_grade": "R5",
            "evidence_count": "2",
        }


class TestSourceWhitelist:
    def test_default_source_is_agent(self) -> None:
        assert _args().source == "agent"

    def test_whitelisted_source_survives(self) -> None:
        assert _args(source_type="human").source == "human"

    def test_unknown_source_falls_back_to_agent(self) -> None:
        """The storage whitelist is a SUBSET of MemoryEntry's; an unknown value is coerced."""
        assert _args(source_type="not-a-real-source").source == "agent"


class TestQValuePreseed:
    def test_q_value_matches_the_preseed_formula(self) -> None:
        """Q1-03: impact 0.95 pre-seeds q_value 0.725 before any observation."""
        assert _args(impact=0.95).q_value == pytest.approx(0.725)

    def test_default_impact_preseeds_the_neutral_q_value(self) -> None:
        assert _args(impact=0.5).q_value == pytest.approx(0.5)


class TestAnchorMarshalling:
    def test_anchor_dicts_become_anchor_objects(self) -> None:
        anchor: dict[str, object] = {
            "file": "src/mod.py",
            "symbol_name": "my_func",
            "symbol_type": "function",
            "signature": "def my_func(): pass",
            "line_range": (1, 1),
        }

        anchors = _args(anchors=[anchor]).anchors

        assert len(anchors) == 1
        assert anchors[0].symbol_name == "my_func"
        assert anchors[0].symbol_type == "function"
        assert anchors[0].file == "src/mod.py"

    def test_absolute_path_is_converted_not_dropped(self) -> None:
        """``Anchor`` rejects absolute paths; converting keeps the anchor."""
        anchors = _args(
            anchors=[{"file": "/home/user/project/src/mod.py", "symbol_name": "abs_func"}],
        ).anchors

        assert len(anchors) == 1
        assert not anchors[0].file.startswith("/")

    def test_no_anchors_produces_an_empty_list(self) -> None:
        assert _args().anchors == []

    def test_malformed_anchor_is_skipped_with_a_debug_log(self) -> None:
        """Fail-open: one bad anchor must not fail the whole learning."""
        anchors = [
            {"file": "src/good.py", "symbol_name": "good_symbol"},
            {"file": "../bad.py", "symbol_name": "bad_symbol"},
        ]

        with patch("trw_mcp.state._store_arguments.logger.debug") as mock_debug:
            resolved = _args(anchors=anchors).anchors

        assert [anchor.file for anchor in resolved] == ["src/good.py"]
        mock_debug.assert_called_once()
        assert mock_debug.call_args.kwargs["anchor"] == anchors[1]

    def test_empty_symbol_name_is_skipped(self) -> None:
        assert _args(anchors=[{"file": "src/mod.py", "symbol_name": "", "symbol_type": "function"}]).anchors == []


class TestAssertionMarshalling:
    def test_assertion_dicts_are_validated_into_objects(self) -> None:
        assertions = _args(
            assertions=[
                {"type": "grep_present", "pattern": "def my_func", "target": "src/mod.py"},
                {"type": "glob_exists", "target": "src/main.py"},
            ],
        ).assertions

        assert len(assertions) == 2
        assert assertions[0].type == "grep_present"
        assert assertions[0].pattern == "def my_func"
        assert assertions[1].type == "glob_exists"
        assert assertions[1].target == "src/main.py"

    def test_no_assertions_produces_an_empty_list(self) -> None:
        assert _args().assertions == []
        assert _args(assertions=None).assertions == []


class TestEnumCoercion:
    def test_valid_enum_strings_are_coerced(self) -> None:
        args = _args(type="incident", confidence="verified", protection_tier="protected")
        assert args.type.value == "incident"
        assert args.confidence.value == "verified"
        assert args.protection_tier.value == "protected"

    @pytest.mark.parametrize(
        ("field", "value"),
        [("type", "not-a-type"), ("confidence", "not-a-confidence"), ("protection_tier", "not-a-tier")],
    )
    def test_an_invalid_enum_value_raises(self, field: str, value: str) -> None:
        """A deterministic refusal, not a silent default.

        The write-ahead journal dead-letters ``ValueError`` rather than replaying
        it forever, and a coerced default would store a mis-typed row instead.
        """
        with pytest.raises(ValueError):
            _args(**{field: value})
