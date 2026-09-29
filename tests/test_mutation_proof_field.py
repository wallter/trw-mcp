"""PRD-CORE-320 FR03/NFR03 — optional revert-and-run mutation-proof field.

Pure model/rendering tests, no filesystem I/O — unit tier.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.models._evidence_core import EvidenceLimits
from trw_mcp.models._evidence_plans import BuildCommandResult, CommandClass

pytestmark = pytest.mark.unit

_BASE_KWARGS = {
    "command_id": "unit-test",
    "label": "unit test command",
    "command_class": CommandClass.TEST,
    "exit_code": 0,
}


def test_mutation_proof_defaults_to_empty() -> None:
    result = BuildCommandResult(**_BASE_KWARGS)
    assert result.mutation_proof == ""


def test_mutation_proof_is_bounded_text() -> None:
    oversized = "x" * (EvidenceLimits.MAX_FREE_TEXT_BYTES + 1)
    with pytest.raises(ValidationError):
        BuildCommandResult(**_BASE_KWARGS, mutation_proof=oversized)


def test_a_wired_claim_with_no_proof_renders_as_plain_wired() -> None:
    result = BuildCommandResult(**_BASE_KWARGS, integration="wired", call_site="pkg/mod.py:42")
    assert result.render_integration_claim() == "wired"


def test_a_revert_proof_renders_as_revert_proven() -> None:
    proof = "reverted the guard at pkg/mod.py:10, re-ran test_x, observed failure, restored the guard"
    result = BuildCommandResult(
        **_BASE_KWARGS,
        integration="wired",
        call_site="pkg/mod.py:42",
        mutation_proof=proof,
    )
    rendered = result.render_integration_claim()
    assert rendered.startswith("wired, revert-proven")
    # The proof text carries VERBATIM — never summarized or truncated.
    assert proof in rendered


def test_a_whitespace_only_mutation_proof_renders_as_plain_wired() -> None:
    """worker-3 review, P2 row (a): whitespace-only is no proof, never "revert-proven: "."""
    result = BuildCommandResult(
        **_BASE_KWARGS,
        integration="wired",
        call_site="pkg/mod.py:42",
        mutation_proof="   \n\t  ",
    )
    assert result.render_integration_claim() == "wired"


def test_isolated_claim_ignores_mutation_proof_in_rendering() -> None:
    result = BuildCommandResult(
        **_BASE_KWARGS,
        integration="isolated",
        mutation_proof="irrelevant proof text",
    )
    assert result.render_integration_claim() == "isolated"


def test_not_applicable_claim_renders_bare() -> None:
    result = BuildCommandResult(**_BASE_KWARGS)
    assert result.render_integration_claim() == "not_applicable"


def test_old_payload_without_mutation_proof_still_parses() -> None:
    """NFR03: an archived receipt written before this PRD still validates."""
    archived_payload = {
        "command_id": "archived-command",
        "label": "archived label",
        "command_class": CommandClass.TEST,
        "exit_code": 0,
        "integration": "wired",
        "call_site": "pkg/mod.py:1",
    }
    result = BuildCommandResult.model_validate(archived_payload)
    assert result.mutation_proof == ""
    assert result.render_integration_claim() == "wired"


def test_mutation_proof_is_frozen() -> None:
    result = BuildCommandResult(**_BASE_KWARGS)
    with pytest.raises(ValidationError):
        result.mutation_proof = "late addition"  # type: ignore[misc]


# ── sol core-320-s3 r1: the fields must reach the public evidence flow ─────────────


def test_trw_build_check_command_results_accept_the_integration_fields() -> None:
    """The typed command_results parser (the only public way in) accepts integration, call_site, mutation_proof."""
    from trw_mcp.tools._command_results import parse_build_command_results

    [result] = parse_build_command_results(
        [
            {
                "command_id": "tests",
                "command_class": "test",
                "exit_code": 0,
                "integration": "wired",
                "call_site": "trw_mcp/tools/ceremony.py:381",
                "mutation_proof": "reverted the guard; test_x failed; restored",
            }
        ]
    ) or [None]
    assert result is not None
    assert (result.integration, result.call_site) == ("wired", "trw_mcp/tools/ceremony.py:381")
    assert result.render_integration_claim() == "wired, revert-proven: reverted the guard; test_x failed; restored"


def test_an_unknown_integration_value_is_refused_by_name() -> None:
    from trw_mcp.tools._command_results import parse_build_command_results

    with pytest.raises(ValueError, match="integration"):
        parse_build_command_results([{"command_id": "tests", "exit_code": 0, "integration": "wird"}])


def test_build_check_reports_each_stated_integration_claim() -> None:
    """integration_claims() is what trw_build_check returns as ``integration_claims``."""
    from trw_mcp.tools._command_results import integration_claims, parse_build_command_results

    results = parse_build_command_results(
        [
            {
                "command_id": "tests",
                "exit_code": 0,
                "integration": "wired",
                "mutation_proof": "reverted; failed; restored",
            },
            {"command_id": "static_checks", "exit_code": 0, "integration": "isolated"},
            {"command_id": "lint", "exit_code": 0},
        ]
    )
    assert integration_claims(results) == {
        "tests": "wired, revert-proven: reverted; failed; restored",
        "static_checks": "isolated",
    }
    assert integration_claims(None) == {}


@pytest.mark.parametrize("value", [False, 0, {}, [], 1.5])
def test_a_non_string_proof_or_call_site_is_refused_by_name(value: object) -> None:
    """sol s3 r2: str(false) / str({}) became a non-empty "proof" and rendered as revert-proven."""
    from trw_mcp.tools._command_results import parse_build_command_results

    for field in ("mutation_proof", "call_site", "integration"):
        with pytest.raises(ValueError, match=field):
            parse_build_command_results([{"command_id": "tests", "exit_code": 0, field: value}])


def test_a_null_proof_is_no_proof() -> None:
    from trw_mcp.tools._command_results import parse_build_command_results

    [result] = parse_build_command_results(
        [{"command_id": "tests", "exit_code": 0, "integration": "wired", "mutation_proof": None, "call_site": None}]
    ) or [None]
    assert result is not None
    assert (result.mutation_proof, result.call_site, result.render_integration_claim()) == ("", "", "wired")
