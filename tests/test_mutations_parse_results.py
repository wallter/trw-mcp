from __future__ import annotations

import json

import pytest

from . import _mutations_support as _mutations_support
from trw_mcp.tools.mutations import _parse_mutmut_results


class TestParseMutmutResults:
    """Tests for _parse_mutmut_results."""

    def test_parses_valid_json_killed_survived(self) -> None:
        """Parses basic killed/survived counts from valid JSON."""
        data = json.dumps(
            {
                "killed": 30,
                "survived": 10,
                "timeout": 2,
                "suspicious": 1,
            }
        )
        result = _parse_mutmut_results(data)
        assert result["killed"] == 30
        assert result["survived"] == 10
        assert result["timeout"] == 2
        assert result["suspicious"] == 1

    def test_returns_parse_error_dict_on_invalid_json(self) -> None:
        """Returns parse_error key on invalid JSON."""
        result = _parse_mutmut_results("not json {]}")
        assert "parse_error" in result
        assert result["killed"] == 0
        assert result["survived"] == 0
        assert result["mutation_score"] is None

    def test_calculates_mutation_score_correctly(self) -> None:
        """mutation_score = killed / (killed + survived), rounded to 4 decimal places."""
        data = json.dumps({"killed": 8, "survived": 2})
        result = _parse_mutmut_results(data)
        assert result["mutation_score"] == pytest.approx(0.8, rel=1e-3)

    def test_returns_none_score_when_total_is_zero(self) -> None:
        """mutation_score is None when killed + survived == 0."""
        data = json.dumps({"killed": 0, "survived": 0})
        result = _parse_mutmut_results(data)
        assert result["mutation_score"] is None

    def test_handles_non_dict_json_list(self) -> None:
        """When JSON is a list (not a dict), returns zeros with no crash."""
        data = json.dumps([1, 2, 3])
        result = _parse_mutmut_results(data)
        assert result["killed"] == 0
        assert result["survived"] == 0
        assert result["mutation_score"] is None

    def test_extracts_and_sorts_surviving_mutants(self) -> None:
        """surviving_mutants are extracted and sorted by line number, capped at 20."""
        mutants = [
            {"file": "foo.py", "line": 50, "description": "mutated X"},
            {"file": "foo.py", "line": 10, "description": "mutated Y"},
            {"file": "foo.py", "line": 30, "description": "mutated Z"},
        ]
        data = json.dumps(
            {
                "killed": 5,
                "survived": 3,
                "survived_mutants": mutants,
            }
        )
        result = _parse_mutmut_results(data)
        survivors = result["surviving_mutants"]
        assert isinstance(survivors, list)
        assert len(survivors) == 3
        lines = [int(str(m["line"])) for m in survivors]
        assert lines == sorted(lines)

    def test_surviving_mutants_capped_at_20(self) -> None:
        """More than 20 surviving mutants are truncated to 20."""
        mutants = [{"file": "foo.py", "line": i, "description": f"mut {i}"} for i in range(1, 35)]
        data = json.dumps(
            {
                "killed": 10,
                "survived": 34,
                "survived_mutants": mutants,
            }
        )
        result = _parse_mutmut_results(data)
        assert len(result["surviving_mutants"]) == 20

    def test_total_mutants_includes_timeout_and_suspicious(self) -> None:
        """total_mutants = killed + survived + timeout + suspicious."""
        data = json.dumps(
            {
                "killed": 10,
                "survived": 5,
                "timeout": 3,
                "suspicious": 2,
            }
        )
        result = _parse_mutmut_results(data)
        assert result["total_mutants"] == 20

    def test_description_truncated_to_200_chars(self) -> None:
        """Mutant description strings are truncated to 200 characters."""
        long_desc = "x" * 300
        mutants = [{"file": "foo.py", "line": 1, "description": long_desc}]
        data = json.dumps(
            {
                "killed": 1,
                "survived": 1,
                "survived_mutants": mutants,
            }
        )
        result = _parse_mutmut_results(data)
        desc = result["surviving_mutants"][0]["description"]
        assert len(str(desc)) <= 200

    def test_handles_empty_json_string(self) -> None:
        """Empty string raises parse error gracefully."""
        result = _parse_mutmut_results("")
        assert "parse_error" in result


@pytest.mark.parametrize(
    ("killed", "survived", "expected_score"),
    [
        (10, 0, 1.0),
        (0, 10, 0.0),
        (8, 2, 0.8),
        (1, 3, 0.25),
        (0, 0, None),
    ],
)
def test_parse_mutmut_score_parametrized(
    killed: int,
    survived: int,
    expected_score: float | None,
) -> None:
    """Parametrized mutation score calculation for various killed/survived combos."""
    data = json.dumps({"killed": killed, "survived": survived})
    result = _parse_mutmut_results(data)
    if expected_score is None:
        assert result["mutation_score"] is None
    else:
        assert result["mutation_score"] == pytest.approx(expected_score, rel=1e-3)


class TestParseMutmutResultsEdgeCases:
    """Additional edge cases for _parse_mutmut_results."""

    def test_returns_parse_error_on_none_input(self) -> None:
        """None input (TypeError in json.loads) → parse_error returned, no crash."""
        result = _parse_mutmut_results(None)  # type: ignore[arg-type]
        assert "parse_error" in result
        assert result["killed"] == 0
        assert result["mutation_score"] is None

    def test_returns_parse_error_on_non_string_int_input(self) -> None:
        """Integer input (TypeError in json.loads) → parse_error returned."""
        result = _parse_mutmut_results(42)  # type: ignore[arg-type]
        assert "parse_error" in result

    def test_valid_dict_missing_all_optional_keys(self) -> None:
        """Valid JSON dict with no recognized keys → zeros, no crash."""
        result = _parse_mutmut_results("{}")
        assert result["killed"] == 0
        assert result["survived"] == 0
        assert result["timeout"] == 0
        assert result["suspicious"] == 0
        assert result["mutation_score"] is None
        assert result["surviving_mutants"] == []

    def test_survived_mutants_non_list_is_ignored(self) -> None:
        """survived_mutants field as dict (not list) → surviving_mutants is empty."""
        data = json.dumps(
            {
                "killed": 5,
                "survived": 3,
                "survived_mutants": {"file": "foo.py", "line": 1},
            }
        )
        result = _parse_mutmut_results(data)
        assert result["surviving_mutants"] == []
        assert result["killed"] == 5

    def test_survived_mutants_with_non_dict_items_skipped(self) -> None:
        """Non-dict items in survived_mutants list are silently skipped."""
        data = json.dumps(
            {
                "killed": 2,
                "survived": 2,
                "survived_mutants": [
                    "not-a-dict",
                    42,
                    None,
                    {"file": "foo.py", "line": 5, "description": "valid"},
                ],
            }
        )
        result = _parse_mutmut_results(data)
        assert len(result["surviving_mutants"]) == 1
        assert result["surviving_mutants"][0]["file"] == "foo.py"

    def test_mutant_missing_file_and_line_defaults_to_empty_and_zero(self) -> None:
        """Mutant dict missing file/line/description defaults gracefully."""
        data = json.dumps(
            {
                "killed": 1,
                "survived": 1,
                "survived_mutants": [{}],
            }
        )
        result = _parse_mutmut_results(data)
        assert len(result["surviving_mutants"]) == 1
        mutant = result["surviving_mutants"][0]
        assert mutant["file"] == ""
        assert mutant["line"] == 0
        assert mutant["description"] == ""

    def test_malformed_json_with_truncated_string(self) -> None:
        """Truncated/partial JSON → parse_error, no exception propagated."""
        result = _parse_mutmut_results('{"killed": 5, "survived":')
        assert "parse_error" in result
        assert result["mutation_score"] is None

    def test_total_mutants_excludes_score_none_case(self) -> None:
        """total_mutants sums all four fields even when score is None."""
        data = json.dumps(
            {
                "killed": 0,
                "survived": 0,
                "timeout": 4,
                "suspicious": 2,
            }
        )
        result = _parse_mutmut_results(data)
        assert result["total_mutants"] == 6
        assert result["mutation_score"] is None
