"""PRD-CORE-320 FR06 — a wired integration claim is bound to a commit.

Pure model tests, no filesystem I/O — unit tier. Builds a minimal in-memory
``ContentBinding`` (empty entries) rather than the tmp_path-based factory in
``tests/_evidence_factories.py``, since this file asserts on ``BuildReceipt``
construction alone.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.models._evidence_core import ContentBinding, compute_manifest_digest, domain_digest
from trw_mcp.models._evidence_plans import BuildCommandResult, CommandClass
from trw_mcp.models._evidence_records import BuildReceipt

pytestmark = pytest.mark.unit

_VALID_SHA = "a" * 40


def _binding() -> ContentBinding:
    manifest_digest = compute_manifest_digest(())
    scope_digest = domain_digest("scope", {"scope_id": "sc1", "project_identity": "proj1", "required_paths": []})
    return ContentBinding(
        scope_id="sc1",
        scope_digest=scope_digest,
        project_identity="proj1",
        entries=(),
        manifest_digest=manifest_digest,
    )


def _receipt_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "receipt_id": "build-test",
        "run_id": "run1",
        "completed_at": "2026-07-10T00:00:00Z",
        "plan_id": "vplan1",
        "plan_digest": "digest1",
        "content_binding": _binding(),
        "command_results": (),
    }
    base.update(overrides)
    return base


def _command(integration: str, call_site: str = "", command_id: str = "c1") -> BuildCommandResult:
    return BuildCommandResult(
        command_id=command_id,
        label=command_id,
        command_class=CommandClass.TEST,
        exit_code=0,
        integration=integration,  # type: ignore[arg-type]
        call_site=call_site,
    )


def test_a_wired_claim_with_no_git_sha_is_refused() -> None:
    with pytest.raises(ValidationError, match="a wired integration claim needs a git_sha"):
        BuildReceipt(
            **_receipt_kwargs(
                git_sha=None,
                command_results=(_command("wired", "pkg/mod.py:1"),),
            )
        )


def test_a_wired_claim_with_a_git_sha_passes() -> None:
    receipt = BuildReceipt(
        **_receipt_kwargs(
            git_sha=_VALID_SHA,
            command_results=(_command("wired", "pkg/mod.py:1"),),
        )
    )
    assert receipt.git_sha == _VALID_SHA
    assert receipt.command_results[0].integration == "wired"


@pytest.mark.parametrize("integration", ["not_applicable", "isolated"])
def test_a_non_wired_claim_without_a_git_sha_is_unaffected(integration: str) -> None:
    receipt = BuildReceipt(
        **_receipt_kwargs(
            git_sha=None,
            command_results=(_command(integration),),
        )
    )
    assert receipt.git_sha is None
    assert receipt.command_results[0].integration == integration


def test_a_wired_claim_among_other_non_wired_claims_still_needs_a_git_sha() -> None:
    with pytest.raises(ValidationError, match="a wired integration claim needs a git_sha"):
        BuildReceipt(
            **_receipt_kwargs(
                git_sha=None,
                command_results=(
                    _command("not_applicable", command_id="c0"),
                    _command("wired", "pkg/mod.py:1", command_id="c1"),
                ),
            )
        )


def test_old_receipt_payload_without_git_sha_and_without_wired_claims_still_parses() -> None:
    """NFR03: an archived receipt with no wired claims is unaffected."""
    receipt = BuildReceipt.model_validate(_receipt_kwargs(git_sha=None, command_results=(_command("not_applicable"),)))
    assert receipt.git_sha is None
