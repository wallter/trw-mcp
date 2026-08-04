"""``command_results`` must diagnose the fault it actually hit — never invent one.

Before 2026-07-27 the parser read ``command_id``/``exit_code``, silently dropped
every other key, and defaulted a missing ``exit_code`` to ``1`` (= failed). A
caller who guessed the shape — ``{"id": "tests", "passed": true}`` is the guess
people actually make, because no bundled guidance states the key names — got
their green run recorded as a FAILED build, and then got told
``"tests_passed contradicts typed command result 'tests'"``: an error accusing
them of misreporting a run they had reported correctly.

Two distinct defects, both constitutional:

* **Silent inversion (HB-4).** A passing run became failing evidence with no
  error at all when the legacy boolean was omitted too. Evidence was fabricated
  in the pessimistic direction.
* **Misdiagnosis (HB-1).** The error asserted a contradiction that did not
  occur, sending the caller to audit their test run instead of their JSON keys.

The fix is to fail loudly and name the field. These tests pin *which* fault each
malformed payload is blamed for, because a correct-but-wrongly-explained
rejection is the half that cost the time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from trw_mcp.tools._evidence_writers import parse_build_command_results


def _entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "command_id": "tests",
        "label": "pytest -q",
        "command_class": "test",
        "exit_code": 0,
    }
    base.update(overrides)
    return base


def _both_passing() -> list[dict[str, object]]:
    return [
        _entry(),
        _entry(command_id="static_checks", label="ruff+mypy", command_class="static"),
    ]


class TestUnknownKeysAreNamed:
    """The originating bug: a key-name mismatch must be reported as one."""

    def test_id_and_passed_spelling_names_both_offending_keys(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            parse_build_command_results([{"id": "tests", "passed": True}])
        message = str(excinfo.value)
        assert "'id'" in message and "'passed'" in message, (
            f"the rejection must name the keys the caller actually sent: {message}"
        )
        assert "command_id" in message and "exit_code" in message, (
            f"the rejection must show the accepted spelling so the caller can fix it: {message}"
        )

    def test_unknown_key_is_not_blamed_on_a_contradiction(self) -> None:
        """The exact misdiagnosis this file exists to prevent."""
        with pytest.raises(ValueError) as excinfo:
            parse_build_command_results([{"id": "tests", "passed": True}])
        assert "contradict" not in str(excinfo.value).lower(), (
            "a wrong key name is not a contradiction between two reported outcomes; "
            "saying so sends the caller to audit the wrong thing"
        )

    def test_a_plausible_but_unsupported_extra_field_is_rejected_not_dropped(self) -> None:
        """Silently dropping an unknown field loses evidence the caller believed they filed."""
        with pytest.raises(ValueError, match=r"stderr"):
            parse_build_command_results([_entry(stderr="boom")])


class TestExitCodeIsRequired:
    """``exit_code`` is the sole pass/fail carrier, so it may not be defaulted."""

    def test_absent_exit_code_raises_instead_of_defaulting_to_failed(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            parse_build_command_results([{"command_id": "tests", "label": "pytest", "command_class": "test"}])
        message = str(excinfo.value)
        assert "exit_code" in message
        assert "required" in message

    def test_null_exit_code_raises(self) -> None:
        with pytest.raises(ValueError, match=r"exit_code.*required"):
            parse_build_command_results([_entry(exit_code=None)])

    def test_boolean_exit_code_is_rejected_not_coerced(self) -> None:
        """``True`` is an ``int`` in Python; coercing it would silently mean exit_code=1."""
        with pytest.raises(ValueError, match=r"exit_code.*integer"):
            parse_build_command_results([_entry(exit_code=True)])

    def test_non_numeric_exit_code_names_the_field(self) -> None:
        with pytest.raises(ValueError, match=r"exit_code.*integer"):
            parse_build_command_results([_entry(exit_code="green")])

    def test_zero_exit_code_is_a_pass(self) -> None:
        parsed = parse_build_command_results([_entry(exit_code=0)])
        assert parsed is not None
        assert parsed[0].passed is True

    def test_nonzero_exit_code_is_a_failure(self) -> None:
        parsed = parse_build_command_results([_entry(exit_code=2)])
        assert parsed is not None
        assert parsed[0].passed is False


class TestStructuralRejections:
    def test_missing_command_id_names_the_field(self) -> None:
        with pytest.raises(ValueError, match=r"command_id"):
            parse_build_command_results([{"label": "pytest", "command_class": "test", "exit_code": 0}])

    def test_blank_command_id_is_not_accepted_as_an_id(self) -> None:
        with pytest.raises(ValueError, match=r"command_id"):
            parse_build_command_results([_entry(command_id="   ")])

    def test_unknown_command_class_lists_the_accepted_values(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            parse_build_command_results([_entry(command_class="linting")])
        message = str(excinfo.value)
        assert "'linting'" in message
        assert "static" in message and "test" in message

    def test_non_object_entry_is_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"expected an object"):
            parse_build_command_results(["tests passed"])  # type: ignore[list-item]

    def test_empty_list_is_a_caller_error_not_a_legacy_report(self) -> None:
        """Claiming typed evidence and supplying none is not the same as omitting it."""
        with pytest.raises(ValueError, match=r"empty"):
            parse_build_command_results([])

    def test_omitted_entirely_still_means_legacy_booleans(self) -> None:
        assert parse_build_command_results(None) is None

    def test_error_locates_the_offending_entry_by_index(self) -> None:
        with pytest.raises(ValueError, match=r"command_results\[1\]"):
            parse_build_command_results([_entry(), _entry(command_id="static_checks", exit_code=None)])


class TestReconciliationDiagnosis:
    """``trw_build_check``'s own reconciliation, exercised through the live tool."""

    def test_missing_required_id_names_it_rather_than_recording_a_failure(
        self,
        build_check_invoke: Any,
    ) -> None:
        with pytest.raises(ValueError) as excinfo:
            build_check_invoke(
                tests_passed=None,
                command_results=[_entry(command_id="static_checks", command_class="static")],
            )
        message = str(excinfo.value)
        assert "tests" in message and "missing" in message.lower()
        assert "contradict" not in message.lower(), (
            "an absent required command result is a missing-evidence fault, not a contradiction"
        )

    def test_missing_required_id_does_not_persist_a_fabricated_failure(
        self,
        tmp_project: Path,
        build_check_invoke: Any,
    ) -> None:
        """The dangerous half: pre-fix this recorded tests_passed=False and returned 200."""
        with pytest.raises(ValueError):
            build_check_invoke(
                tests_passed=None,
                command_results=[_entry(command_id="static_checks", command_class="static")],
            )
        cache_path = tmp_project / ".trw" / "context" / "build-status.yaml"
        assert not cache_path.exists(), "a rejected report must not leave build evidence behind"

    def test_genuine_contradiction_is_still_rejected_and_quotes_the_exit_code(
        self,
        build_check_invoke: Any,
    ) -> None:
        results = _both_passing()
        results[0]["exit_code"] = 1
        with pytest.raises(ValueError) as excinfo:
            build_check_invoke(tests_passed=True, command_results=results)
        message = str(excinfo.value)
        assert "tests" in message
        assert "exit_code=1" in message, f"the caller needs the evidence that disagrees with them: {message}"

    def test_static_checks_contradiction_is_attributed_to_static_checks(
        self,
        build_check_invoke: Any,
    ) -> None:
        results = _both_passing()
        results[1]["exit_code"] = 1
        with pytest.raises(ValueError) as excinfo:
            build_check_invoke(tests_passed=True, static_checks_clean=True, command_results=results)
        assert "static_checks" in str(excinfo.value)

    def test_agreeing_booleans_are_accepted(self, build_check_invoke: Any) -> None:
        result = build_check_invoke(tests_passed=True, static_checks_clean=True, command_results=_both_passing())
        assert result["tests_passed"] is True
        assert result["static_checks_clean"] is True

    def test_booleans_may_be_omitted_and_the_command_results_decide(self, build_check_invoke: Any) -> None:
        results = _both_passing()
        results[0]["exit_code"] = 3
        result = build_check_invoke(tests_passed=None, command_results=results)
        assert result["tests_passed"] is False
        assert result["static_checks_clean"] is True

    def test_extra_command_ids_beyond_the_required_two_are_allowed(self, build_check_invoke: Any) -> None:
        results = [*_both_passing(), _entry(command_id="schema", label="alembic check", command_class="schema")]
        result = build_check_invoke(tests_passed=None, command_results=results)
        assert result["tests_passed"] is True


class TestJsonStringPayloadIsAccepted:
    """A serialized ``command_results`` must not force the caller off the evidence path.

    fastmcp 3.2.4 does no JSON-string pre-parsing (verified 2026-07-27 against
    the installed version): a client that serializes this argument is rejected
    by pydantic before any TRW code runs. The only workaround left to such a
    caller is to DROP ``command_results`` — which in enforce mode means no
    BuildReceipt is written. Accepting the string form is therefore a
    gate-STRENGTHENING change; the decoded payload is validated identically.
    """

    def test_serialized_array_parses_identically_to_the_native_form(self) -> None:
        import json

        native = parse_build_command_results(_both_passing())
        serialized = parse_build_command_results(json.dumps(_both_passing()))
        assert native == serialized

    def test_serialized_payload_is_still_strictly_validated(self) -> None:
        """Decoding must not become a bypass around the per-entry contract."""
        import json

        with pytest.raises(ValueError, match=r"exit_code.*required"):
            parse_build_command_results(json.dumps([{"command_id": "tests", "command_class": "test"}]))

    def test_malformed_json_says_so_instead_of_blaming_the_contents(self) -> None:
        with pytest.raises(ValueError, match=r"not valid JSON"):
            parse_build_command_results('[{"command_id": "tests",]')

    def test_json_scalar_is_rejected_as_not_an_array(self) -> None:
        with pytest.raises(ValueError, match=r"expected a JSON array"):
            parse_build_command_results('"tests passed"')

    def test_empty_string_routes_to_the_legacy_path_not_to_a_free_pass(
        self,
        build_check_invoke: Any,
    ) -> None:
        """Several clients encode an unset optional string as "". It must still demand an outcome."""
        assert parse_build_command_results("") is None
        with pytest.raises(ValueError, match=r"tests_passed is required"):
            build_check_invoke(tests_passed=None, command_results="")

    def test_serialized_payload_reaches_the_live_tool(self, build_check_invoke: Any) -> None:
        import json

        result = build_check_invoke(tests_passed=None, command_results=json.dumps(_both_passing()))
        assert result["tests_passed"] is True
        assert result["static_checks_clean"] is True


class TestZSuffixedTimestampsAreMeasurements:
    """PRD-FIX: ``...Z`` is the common CI form and must not degrade to unmeasured.

    ``datetime.fromisoformat`` did not accept a trailing ``Z`` until Python 3.11
    and this package declares ``>=3.10``. Without normalization a caller who
    supplied a real measurement had it silently discarded — the fabrication guard
    on ``derive_duration_secs`` failing in the direction that throws away truth.

    HONEST LIMIT: every test below EXCEPT
    ``test_z_handled_without_relying_on_fromisoformat_accepting_z`` also passes
    against the unfixed code when the suite runs on Python >=3.11, because the
    stdlib absorbs the ``Z`` itself. They pin the behaviour, not the fix. The
    one exception is the actual regression guard: it forces the 3.10 stdlib
    contract regardless of the running interpreter, so it fails if the
    normalization is removed.
    """

    def test_z_handled_without_relying_on_fromisoformat_accepting_z(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Simulate the declared Python floor: a stdlib that rejects a trailing ``Z``."""
        import datetime as _datetime_module

        from trw_mcp.tools.build import _build_check_helpers

        real_fromisoformat = _datetime_module.datetime.fromisoformat

        class _Py310Datetime(_datetime_module.datetime):
            @classmethod
            def fromisoformat(cls, date_string: str) -> _datetime_module.datetime:
                if date_string.endswith("Z"):
                    raise ValueError(f"Invalid isoformat string: {date_string!r}")
                return real_fromisoformat(date_string)

        monkeypatch.setattr(_build_check_helpers, "datetime", _Py310Datetime)

        parsed = parse_build_command_results(
            [_entry(started_at="2026-07-27T00:00:00Z", completed_at="2026-07-27T00:00:07Z")]
        )
        assert parsed is not None
        assert _build_check_helpers.derive_duration_secs(parsed) == pytest.approx(7.0), (
            "on the declared Python floor the Z-suffixed measurement is discarded unless _parse_iso normalizes it first"
        )

    def test_z_suffixed_span_is_derived(self) -> None:
        from trw_mcp.tools.build._build_check_helpers import derive_duration_secs

        parsed = parse_build_command_results(
            [_entry(started_at="2026-07-27T00:00:00Z", completed_at="2026-07-27T00:01:05Z")]
        )
        assert parsed is not None
        assert derive_duration_secs(parsed) == pytest.approx(65.0)

    def test_z_and_offset_forms_mix_without_degrading(self) -> None:
        """Both forms are UTC-aware after normalization, so the subtraction is legal."""
        from trw_mcp.tools.build._build_check_helpers import derive_duration_secs

        parsed = parse_build_command_results(
            [
                _entry(started_at="2026-07-27T00:00:00Z", completed_at="2026-07-27T00:00:30Z"),
                _entry(
                    command_id="static_checks",
                    command_class="static",
                    started_at="2026-07-27T00:00:10+00:00",
                    completed_at="2026-07-27T00:00:50+00:00",
                ),
            ]
        )
        assert parsed is not None
        assert derive_duration_secs(parsed) == pytest.approx(50.0)

    def test_z_timestamps_reach_the_persisted_build_status(
        self,
        tmp_project: Path,
        build_check_invoke: Any,
    ) -> None:
        """End-to-end: the measurement survives to the cache, not just the helper."""
        results = _both_passing()
        results[0]["started_at"] = "2026-07-27T00:00:00Z"
        results[0]["completed_at"] = "2026-07-27T00:00:12Z"
        results[1]["started_at"] = "2026-07-27T00:00:12Z"
        results[1]["completed_at"] = "2026-07-27T00:00:20Z"

        build_check_invoke(tests_passed=None, command_results=results)

        cached = YAML(typ="safe").load(
            (tmp_project / ".trw" / "context" / "build-status.yaml").read_text(encoding="utf-8")
        )
        assert cached["duration_secs"] == pytest.approx(20.0), (
            f"a Z-suffixed measurement degraded to {cached['duration_secs']!r}"
        )
