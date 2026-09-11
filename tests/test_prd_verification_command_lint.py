"""PRD-INFRA-179-FR02 + the valid/error invariant.

FR02: a ``verification_commands`` entry that is not a runnable invocation is an
``error``-severity finding at validate time, instead of an exit-127 tail from
``bash -c`` inside ``scripts/prd_verify_check.py``.

Invariant: ``valid=False`` always names at least one ``error``-severity rule. A
warning-only rejection is a verdict the reader cannot act on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation._prd_quality_refresh import refresh_dynamic_prd_validation
from trw_mcp.state.validation._prd_scoring_counts import _VERIFICATION_COMMAND_RE
from trw_mcp.state.validation._prd_validation_findings import (
    VALID_WITHOUT_ERROR_RULE,
    VERIFICATION_COMMAND_RULE,
    enforce_valid_invariant,
    verification_command_failures,
)
from trw_mcp.state.validation._verification_command_lint import (
    VERIFICATION_COMMAND_NAMES,
    malformed_verification_command_reason,
)
from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

REPO_ROOT = Path(__file__).resolve().parents[2]


def _prd(commands: list[str]) -> str:
    entries = "\n".join(f'  - "{c}"' for c in commands)
    return (
        "---\n"
        "prd:\n"
        "  id: PRD-INFRA-999\n"
        '  title: "fixture"\n'
        '  version: "1.0"\n'
        "  status: draft\n"
        "  priority: P2\n"
        "traceability:\n"
        "  implements:\n"
        '    - "fixture source"\n'
        "verification_commands:\n"
        f"{entries}\n"
        "---\n\n"
        "# Fixture\n\n## 1. Problem Statement\n\nBody.\n"
    )


# --- FR02: the lint itself -------------------------------------------------


def test_malformed_first_token_is_rejected() -> None:
    """The exact shape observed in draft PRDs: an FR label pasted before the command."""
    bad = "FR01: cd trw-mcp && ../.venv/bin/python -m pytest tests/test_x.py -q"
    reason = malformed_verification_command_reason(bad, repo_root=REPO_ROOT)
    assert reason is not None
    assert "FR01:" in reason

    result = validate_prd_quality_v2(_prd([bad]), project_root=str(REPO_ROOT))
    offending = [f for f in result.failures if f.rule == VERIFICATION_COMMAND_RULE]
    assert offending, [f.rule for f in result.failures]
    assert offending[0].severity == "error"
    assert bad in offending[0].message
    assert result.valid is False


def test_well_formed_pytest_command_passes(tmp_path: Path) -> None:
    # The validator checks leading executable paths. Supply a real executable
    # in a private project, not an ambient monorepo editable-install assumption.
    executable = tmp_path / ".venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    (tmp_path / "trw-mcp").mkdir()
    for good in (
        "pytest trw-mcp/tests/test_x.py -q",
        "cd trw-mcp && ../.venv/bin/python -m pytest tests/test_x.py -q",
        ".venv/bin/python -m pytest trw-mcp/tests/test_x.py -q",
        "make prd-verify-check",
        "grep -rn 'symbol' trw-mcp/src",
        "TRW_DEBUG=1 python3 scripts/prd_verify_check.py",
    ):
        assert malformed_verification_command_reason(good, repo_root=tmp_path) is None, good

    result = validate_prd_quality_v2(_prd(["pytest trw-mcp/tests/test_x.py -q"]), project_root=str(tmp_path))
    assert [f for f in result.failures if f.rule == VERIFICATION_COMMAND_RULE] == []


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ("command_succeeds `pytest tests -q`", "command_succeeds"),
        ("# Verify the CTA exists", "shell comment"),
        ("../.venv/bin/python -m pytest tests -q", "does not exist"),
        ("   ", "empty"),
    ],
)
def test_non_runnable_shapes_are_named(entry: str, fragment: str) -> None:
    reason = malformed_verification_command_reason(entry, repo_root=REPO_ROOT)
    assert reason is not None and fragment in reason, (entry, reason)


def test_lint_and_scoring_regex_share_one_command_name_set() -> None:
    """RISK-002: the names the lint accepts are the names the scorer counts."""
    for name in VERIFICATION_COMMAND_NAMES:
        assert malformed_verification_command_reason(f"{name} test", repo_root=REPO_ROOT) is None
    assert _VERIFICATION_COMMAND_RE.search("run pytest now")
    assert _VERIFICATION_COMMAND_RE.search("npm run test")


def test_non_string_entry_is_reported() -> None:
    failures = verification_command_failures({"verification_commands": [42]})
    assert len(failures) == 1
    assert failures[0].severity == "error"


# --- The valid/error invariant --------------------------------------------


def test_warning_only_integrity_findings_do_not_flip_valid() -> None:
    """A warning-severity integrity finding must not reject a PRD.

    Before this fix ``result.valid = result.valid and not integrity_failures``
    treated a *warning* (e.g. "bare filename has multiple matches") as blocking,
    producing ``valid: False`` with no error anywhere in the output.
    """
    content = _prd(["pytest trw-mcp/tests/test_x.py -q"])
    base = validate_prd_quality_v2(content, project_root=str(REPO_ROOT), include_dynamic_checks=False)
    base.valid = True

    warning = ValidationFailure(field="traceability", rule="repo_path_exists", message="ambiguous", severity="warning")
    refreshed = refresh_dynamic_prd_validation(
        base,
        content,
        project_root=str(REPO_ROOT),
        integrity_checker=lambda *a, **k: ([warning], []),
    )
    assert refreshed.valid is True
    assert warning in refreshed.failures


def test_error_severity_integrity_finding_still_blocks() -> None:
    content = _prd(["pytest trw-mcp/tests/test_x.py -q"])
    base = validate_prd_quality_v2(content, project_root=str(REPO_ROOT), include_dynamic_checks=False)
    base.valid = True

    error = ValidationFailure(field="traceability", rule="repo_path_exists", message="missing", severity="error")
    refreshed = refresh_dynamic_prd_validation(
        base,
        content,
        project_root=str(REPO_ROOT),
        integrity_checker=lambda *a, **k: ([error], []),
    )
    assert refreshed.valid is False
    assert any(f.severity == "error" for f in refreshed.failures)


def test_valid_false_without_an_error_gets_a_named_backstop_finding() -> None:
    class _Result:
        valid = False
        failures = [ValidationFailure(field="x", rule="advisory_only", message="m", severity="warning")]

    result = _Result()
    enforce_valid_invariant(result)
    backstop = [f for f in result.failures if f.rule == VALID_WITHOUT_ERROR_RULE]
    assert len(backstop) == 1
    assert backstop[0].severity == "error"
    assert "advisory_only" in backstop[0].message


def test_invariant_is_a_no_op_when_an_error_is_already_present() -> None:
    class _Result:
        valid = False
        failures = [ValidationFailure(field="x", rule="real_rule", message="m", severity="error")]

    result = _Result()
    enforce_valid_invariant(result)
    assert [f.rule for f in result.failures] == ["real_rule"]


def test_missing_traceability_names_the_quality_gate_it_failed() -> None:
    """V1's numeric gate previously rejected with only a *warning* finding."""
    content = (
        "---\n"
        "prd:\n"
        "  id: PRD-INFRA-998\n"
        '  title: "fixture"\n'
        '  version: "1.0"\n'
        "  status: draft\n"
        "  priority: P2\n"
        "---\n\n# Fixture\n\n## 1. Problem Statement\n\nBody.\n"
    )
    result = validate_prd_quality_v2(content, project_root=str(REPO_ROOT))
    assert result.valid is False
    errors = [f for f in result.failures if f.severity == "error"]
    assert errors, [f.rule for f in result.failures]
    assert any(f.rule == "quality_gate_threshold" for f in errors), [f.rule for f in errors]


def test_shipped_prd_infra_179_validates_clean() -> None:
    """The PRD this work implements must not be rejected by warnings alone."""
    if not (REPO_ROOT / "release-packages.yaml").is_file():
        pytest.skip("shipped PRD assertion requires the monorepo requirements corpus")
    path = (
        REPO_ROOT
        / "docs/requirements-aare-f/prds/PRD-INFRA-179-agent-dx-six-frictions-from-the-2026-09-03-release-run.md"
    )
    result = validate_prd_quality_v2(path.read_text(encoding="utf-8"), project_root=str(REPO_ROOT))
    blocking = [f for f in result.failures if f.severity == "error"]
    assert blocking == [], [f.message for f in blocking]
    assert result.valid is True
