"""``trw_build_check`` reports an observed duration or none at all — never a fake zero.

Until 2026-07-27 the tool constructed its ``BuildStatus`` with a hardcoded
``duration_secs=0.0`` and echoed that literal into both the tool response and
the ``build_check_complete`` event. It reads as a measurement — "we timed it,
and it took no time" — and it never was one: the tool executes nothing, so it
has no clock.

The fix is not to delete the concept. When the caller supplies typed command
results they carry ``started_at`` / ``completed_at``, from which a genuine
wall-clock span is derivable. So: derive it when the evidence exists, and omit
the field entirely when it does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models._evidence_plans import BuildCommandResult, CommandClass
from trw_mcp.tools.build._build_check_helpers import derive_duration_secs

pytestmark = pytest.mark.unit


def _result(
    command_id: str = "tests",
    *,
    started_at: str = "",
    completed_at: str = "",
) -> BuildCommandResult:
    return BuildCommandResult(
        command_id=command_id,
        label=f"{command_id} command",
        command_class=CommandClass.TEST,
        exit_code=0,
        started_at=started_at,
        completed_at=completed_at,
    )


class TestDeriveDurationSecs:
    def test_no_command_results_is_unknown_not_zero(self) -> None:
        assert derive_duration_secs(None) is None
        assert derive_duration_secs(()) is None

    def test_missing_timestamps_are_unknown_not_zero(self) -> None:
        assert derive_duration_secs((_result(),)) is None

    def test_half_reported_timestamps_are_unknown(self) -> None:
        only_start = _result(started_at="2026-07-27T00:00:00+00:00")
        assert derive_duration_secs((only_start,)) is None

    def test_single_command_span(self) -> None:
        item = _result(
            started_at="2026-07-27T00:00:00+00:00",
            completed_at="2026-07-27T00:00:42+00:00",
        )
        assert derive_duration_secs((item,)) == pytest.approx(42.0)

    def test_span_covers_earliest_start_to_latest_completion(self) -> None:
        """Commands may overlap; the reported figure is total wall clock."""
        tests = _result(
            "tests",
            started_at="2026-07-27T00:00:10+00:00",
            completed_at="2026-07-27T00:00:50+00:00",
        )
        static = _result(
            "static_checks",
            started_at="2026-07-27T00:00:00+00:00",
            completed_at="2026-07-27T00:00:30+00:00",
        )
        assert derive_duration_secs((tests, static)) == pytest.approx(50.0)

    def test_unparseable_timestamps_are_unknown(self) -> None:
        item = _result(started_at="not-a-timestamp", completed_at="also-not")
        assert derive_duration_secs((item,)) is None

    def test_entries_without_timestamps_do_not_poison_a_valid_span(self) -> None:
        timed = _result(
            "tests",
            started_at="2026-07-27T00:00:00+00:00",
            completed_at="2026-07-27T00:00:20+00:00",
        )
        untimed = _result("static_checks")
        assert derive_duration_secs((timed, untimed)) == pytest.approx(20.0)

    def test_mixed_timezone_awareness_degrades_to_unknown(self) -> None:
        """Python refuses the subtraction; guessing a zone would fabricate the number."""
        aware = _result(
            "tests",
            started_at="2026-07-27T00:00:00+00:00",
            completed_at="2026-07-27T00:00:20+00:00",
        )
        naive = _result(
            "static_checks",
            started_at="2026-07-27T00:00:05",
            completed_at="2026-07-27T00:00:25",
        )
        assert derive_duration_secs((aware, naive)) is None

    def test_negative_span_degrades_to_unknown(self) -> None:
        """Transposed fields or clock skew are unusable, not instantaneous."""
        item = _result(
            started_at="2026-07-27T00:00:50+00:00",
            completed_at="2026-07-27T00:00:10+00:00",
        )
        assert derive_duration_secs((item,)) is None


class TestBuildStatusCarriesTheHonestValue:
    """The PERSISTED status, not just the response, must not carry a fake zero.

    An earlier version of this class asserted only that ``duration_secs`` was
    absent from the tool RESPONSE — which was already true at the parent
    commit, because the response never had that key. It pinned nothing. What
    needed pinning is the ``BuildStatus`` that reaches the build-status cache
    and the build receipt: that object DID carry a hardcoded 0.0 on every call.
    """

    def test_model_default_is_unknown_not_zero(self) -> None:
        """BuildStatus must be able to express "not measured" at all."""
        from trw_mcp.models.build import BuildStatus

        status = BuildStatus(tests_passed=True, scope="unit")
        assert status.duration_secs is None, "0.0 reads as 'measured, and it was instant'; unmeasured must be None"

    def test_model_still_rejects_a_negative_duration(self) -> None:
        """Making the field optional must not drop its ge=0.0 constraint."""
        from pydantic import ValidationError

        from trw_mcp.models.build import BuildStatus

        with pytest.raises(ValidationError):
            BuildStatus(tests_passed=True, scope="unit", duration_secs=-1.0)

    def test_untimed_call_persists_unknown_not_zero(self, tmp_project: object) -> None:
        """The common path (no typed command_results) caches no duration."""
        from ruamel.yaml import YAML

        from tests.conftest import extract_tool_fn, make_test_server

        build_check = extract_tool_fn(make_test_server("build"), "trw_build_check")
        result = build_check(tests_passed=True, test_count=3, failure_count=0, scope="unit")

        assert result["tests_passed"] is True
        assert "duration_secs" not in result

        cache_path = Path(str(result["cache_path"]))
        assert cache_path.is_file(), f"build status was not cached at {cache_path}"
        cached = YAML(typ="safe").load(cache_path.read_text(encoding="utf-8"))
        assert cached.get("duration_secs") is None, f"persisted a fabricated duration: {cached.get('duration_secs')!r}"
