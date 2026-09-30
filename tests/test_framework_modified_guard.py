"""CODEX-P0-C step 2: an edited canon body under the SAME stamp is replaced LOUDLY, with its bytes saved.

The replacement itself is the pre-existing self-heal (``test_same_version_drift_still_repairs``); what is new is the warning
and the proof that the edited bytes survive under ``.rollback``. Runs through the real entrypoints: ``update_project`` (the CLI path) and ``_deploy_frameworks`` (the tool path).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project

FRAMEWORK = Path(".trw/frameworks/FRAMEWORK.md")
AAREF = Path(".trw/frameworks/AARE-F-FRAMEWORK.md")
RECEIPT = Path(".trw/frameworks/DEPLOYMENT.json")

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("TRW_FRAMEWORK_FORCE_DEPLOY", raising=False)
    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    return tmp_path


def _warned(result: dict[str, list[str]]) -> list[str]:
    return [w for w in result.get("warnings", []) if "Framework canon replaced" in w]


def _rollback_holds(project: Path, relative: Path, data: bytes) -> bool:
    return any(p.read_bytes() == data for p in (project / ".trw/frameworks/.rollback").rglob(relative.name))


def test_an_edited_body_under_the_same_stamp_is_replaced_loudly_and_its_bytes_are_kept(project: Path) -> None:
    body = project / FRAMEWORK
    edited_bytes = body.read_bytes() + b"\n<!-- LOCAL EDIT -->\n"
    body.write_bytes(edited_bytes)

    result = update_project(project)

    (warning,) = _warned(result)
    assert "FRAMEWORK.md" in warning and ".rollback" in warning
    assert body.read_bytes() != edited_bytes, "the generated reference is healed"
    assert _rollback_holds(project, FRAMEWORK, edited_bytes), "and the edit is recoverable"


def test_a_repeat_init_over_an_edited_body_says_so_too(project: Path) -> None:
    body = project / FRAMEWORK
    body.write_bytes(b"# my own framework\n")
    result = init_project(project, ide="claude-code")
    assert _warned(result) and _rollback_holds(project, FRAMEWORK, b"# my own framework\n")


def test_an_unedited_older_generation_is_upgraded_without_a_warning(project: Path) -> None:
    """The last deploy wrote old bytes and recorded them: that is TRW's own file."""
    body = project / FRAMEWORK
    body.write_text("older canon body\n", encoding="utf-8")
    receipt = json.loads((project / RECEIPT).read_text(encoding="utf-8"))
    receipt["artifact_digests"][FRAMEWORK.as_posix()] = _sha(body)
    (project / RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")

    result = update_project(project)

    assert body.read_text(encoding="utf-8") != "older canon body\n"
    assert not _warned(result)


def test_without_a_receipt_there_is_no_proof_of_an_edit_so_no_claim_is_made(project: Path) -> None:
    (project / RECEIPT).unlink()
    (project / FRAMEWORK).write_text("no receipt, unknown origin\n", encoding="utf-8")
    assert not _warned(update_project(project))


def test_a_receipt_that_does_not_list_a_body_makes_no_claim_about_it(project: Path) -> None:
    receipt = json.loads((project / RECEIPT).read_text(encoding="utf-8"))
    del receipt["artifact_digests"][FRAMEWORK.as_posix()]
    (project / RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
    (project / FRAMEWORK).write_text("body the receipt never described\n", encoding="utf-8")
    assert not _warned(update_project(project))


def test_the_force_switch_stays_silent(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / FRAMEWORK).write_text("edited\n", encoding="utf-8")
    monkeypatch.setenv("TRW_FRAMEWORK_FORCE_DEPLOY", "1")
    assert not _warned(update_project(project))


def test_repeat_init_of_an_untouched_project_is_a_no_op_for_the_canon(project: Path) -> None:
    before = {p: _sha(project / p) for p in (FRAMEWORK, AAREF, RECEIPT)}
    result = init_project(project, ide="claude-code")
    assert {p: _sha(project / p) for p in (FRAMEWORK, AAREF, RECEIPT)} == before
    assert not _warned(result)


def test_the_tool_path_reports_the_replacement(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools import _orchestration_helpers as helpers

    body = project / FRAMEWORK
    body.write_bytes(b"edited via the tool path\n")
    monkeypatch.chdir(project)
    result = helpers._deploy_frameworks(project / ".trw")
    assert result["status"] == "deployed" and result["replaced_modified"] == "" + FRAMEWORK.as_posix()
    assert "rollback" in result["nudge"] and _rollback_holds(project, FRAMEWORK, b"edited via the tool path\n")
