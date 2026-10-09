"""update-project and the intent-contract enrollment marker: gated re-bless, survival, the closing warning.

Observed 2026-10-08 on an enrolled project whose marker was uncommitted: the update wrote new vendor hook
bytes, re-blessed the marker, then the uncommitted-file restore put the OLD marker back. The guard blocked
every Write and Edit and the update's output never mentioned enrollment. The negative cases come first: the
automatic refresh must never bless hook bytes this package did not ship.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._enrollment_rebless import hooks_differing_from_bundle, rebless_intent_hook_digest
from trw_mcp.bootstrap._utils import _DATA_DIR
from trw_mcp.security.intent_contract.enrollment import (
    enrollment_drift,
    enrollment_evidence_present,
    refresh_hook_digest,
    write_enrollment,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

_MARKER = ".trw/contracts/enrollment.yaml"
_LIB = "lib-trw.sh"
_GUARD = "pre-tool-intent-guard.sh"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _age(root: Path, name: str) -> None:
    """Make *name* an OLDER vendor build: different bytes that the manifest records as TRW's own write."""
    hook = root / ".claude" / "hooks" / name
    old = _sha(hook)
    hook.write_bytes(hook.read_bytes() + b"# an older vendor build\n")
    for record in (root / ".trw" / "managed-artifacts.yaml", root / ".trw" / "runtime" / "written-digests.json"):
        text = record.read_text(encoding="utf-8")
        assert old in text, f"fixture: {record.name} must record {name}"
        record.write_text(text.replace(old, _sha(hook)), encoding="utf-8")


@pytest.fixture
def enrolled(tmp_path: Path) -> Path:
    """A committed install with one older vendor hook, enrolled over it; the marker is left UNCOMMITTED."""
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    assert not init_project(root, ide="claude-code")["errors"]
    _age(root, _LIB)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "installed")
    write_enrollment(root)
    assert enrollment_drift(root) == ()
    return root


def _stale_lines(result: dict[str, list[str]]) -> list[str]:
    return [w for w in result["warnings"] if "INTENT-CONTRACT ENROLLMENT" in w]


# --- negative cases: what must NOT be blessed -------------------------------------------------------------


def test_an_edited_hook_is_not_blessed_and_the_update_says_so(enrolled: Path) -> None:
    guard = enrolled / ".claude" / "hooks" / _GUARD
    guard.write_bytes(guard.read_bytes() + b"# a hand edit\n")
    edited, marker_before = guard.read_bytes(), (enrolled / _MARKER).read_bytes()

    result = update_project(enrolled, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert guard.read_bytes() == edited, "the edited hook is kept"
    assert (enrolled / _MARKER).read_bytes() == marker_before, "nothing was blessed over an edited hook"
    assert enrollment_drift(enrolled) == ("expected_hook_digest",)
    (line,) = _stale_lines(result)
    assert "is STALE" in line and "Write and Edit are blocked" in line
    assert "python -m trw_mcp.security.intent_contract.enrollment refresh-hooks" in line
    assert "An agent must not run either command" in line
    assert not (enrolled / ".trw" / "trash").exists()


def test_a_tampered_hook_is_never_blessed_by_the_rebless(enrolled: Path) -> None:
    """Direct: every digest-covered file but one equals the bundle, and that one differs by a byte."""
    hooks = enrolled / ".claude" / "hooks"
    (hooks / _LIB).write_bytes((_DATA_DIR / "hooks" / _LIB).read_bytes())
    (hooks / "lib-intent-guard.sh").write_bytes((hooks / "lib-intent-guard.sh").read_bytes() + b"\n")
    marker_before = (enrolled / _MARKER).read_bytes()
    result: dict[str, list[str]] = {"warnings": []}

    rebless_intent_hook_digest(enrolled, result, _DATA_DIR / "hooks")

    assert hooks_differing_from_bundle(enrolled, _DATA_DIR / "hooks") == ["lib-intent-guard.sh"]
    assert (enrolled / _MARKER).read_bytes() == marker_before
    assert enrollment_drift(enrolled) == ("expected_hook_digest",)


def test_a_missing_or_symlinked_hook_counts_as_differing(enrolled: Path, tmp_path: Path) -> None:
    hooks = enrolled / ".claude" / "hooks"
    (hooks / _LIB).write_bytes((_DATA_DIR / "hooks" / _LIB).read_bytes())
    assert hooks_differing_from_bundle(enrolled, _DATA_DIR / "hooks") == []
    elsewhere = tmp_path / "copy.sh"
    elsewhere.write_bytes((hooks / _GUARD).read_bytes())
    (hooks / _GUARD).unlink()
    (hooks / _GUARD).symlink_to(elsewhere)
    (hooks / "post-tool-intent-check.sh").unlink()
    assert hooks_differing_from_bundle(enrolled, _DATA_DIR / "hooks") == [_GUARD, "post-tool-intent-check.sh"]


def test_a_marker_whose_contract_keys_differ_keeps_them(enrolled: Path) -> None:
    """The user's uncommitted edit to the contract half survives byte for byte; only the hook lines move."""
    marker = enrolled / _MARKER
    text = marker.read_text(encoding="utf-8")
    edited = text.replace("expected_contract_digest: '<absent>'", "expected_contract_digest: 'user-edited-value'")
    assert edited != text, "fixture: the marker must carry the absent-contract digest"
    marker.write_text(edited, encoding="utf-8")

    result = update_project(enrolled, ide="claude-code")

    assert not result["errors"], result["errors"]

    def _other(body: str) -> list[str]:
        return [ln for ln in body.splitlines() if not ln.startswith(("expected_hook_digest", "hook_digest_refreshed"))]

    assert _other(marker.read_text(encoding="utf-8")) == _other(edited)
    assert enrollment_drift(enrolled) == ("expected_contract_digest",), "the contract half is never re-blessed"
    (line,) = _stale_lines(result)
    assert "expected_contract_digest" in line and "enrollment enroll" in line


def test_a_hand_formatted_dirty_marker_is_put_back_whole(enrolled: Path) -> None:
    """Not TRW's serialization: the refresh would replace the user's comment, so their copy comes back."""
    marker = enrolled / _MARKER
    marker.write_text(marker.read_text(encoding="utf-8") + "# my note\n", encoding="utf-8")
    before = marker.read_bytes()

    result = update_project(enrolled, ide="claude-code")

    assert marker.read_bytes() == before
    assert len(_stale_lines(result)) == 1
    assert not list((enrolled / ".trw").glob("trash/*/data/*")), "this run's own write is not kept as a capture"


# --- the positive case ------------------------------------------------------------------------------------


def test_new_vendor_hook_bytes_leave_an_uncommitted_marker_current(enrolled: Path) -> None:
    result = update_project(enrolled, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert (enrolled / ".claude" / "hooks" / _LIB).read_bytes() == (_DATA_DIR / "hooks" / _LIB).read_bytes()
    assert enrollment_drift(enrolled) == (), "the re-bless survived the uncommitted-file restore"
    assert _stale_lines(result) == []
    assert not [w for w in result["warnings"] if _MARKER in w], result["warnings"]
    assert not [p for p in result["preserved"] if _MARKER in p]
    assert not (enrolled / ".trw" / "trash").exists()

    again = update_project(enrolled, ide="claude-code")
    assert not again["errors"], again["errors"]
    assert _stale_lines(again) == [] and not (enrolled / ".trw" / "trash").exists()
    third = update_project(enrolled, ide="claude-code")
    assert not (enrolled / ".trw" / "trash").exists(), third["warnings"]


def test_a_committed_then_modified_marker_also_stays_current(enrolled: Path) -> None:
    _git(enrolled, "add", "-f", _MARKER)
    _git(enrolled, "commit", "-qm", "enrolled")
    hook = enrolled / ".claude" / "hooks" / "lib-intent-guard.sh"
    _age(enrolled, "lib-intent-guard.sh")
    _git(enrolled, "add", "-A", ".claude", ".trw/managed-artifacts.yaml")
    _git(enrolled, "commit", "-qm", "older hook")
    assert refresh_hook_digest(enrolled) is True  # tracked and modified, as an operator's refresh leaves it

    result = update_project(enrolled, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert hook.read_bytes() == (_DATA_DIR / "hooks" / "lib-intent-guard.sh").read_bytes()
    assert enrollment_drift(enrolled) == () and _stale_lines(result) == []


# --- dry run and the unenrolled project -------------------------------------------------------------------


def test_a_dry_run_never_says_were_moved_and_predicts_the_real_run(enrolled: Path) -> None:
    marker = enrolled / _MARKER
    marker.write_text(marker.read_text(encoding="utf-8") + "# my note\n", encoding="utf-8")
    before = marker.read_bytes()

    result = update_project(enrolled, ide="claude-code", dry_run=True)

    text = "\n".join(result["warnings"])
    assert "were moved" not in text and "/trash/2" not in text, text
    (line,) = _stale_lines(result)
    assert "would be stale after this update" in line and "would be blocked" in line
    assert marker.read_bytes() == before and not (enrolled / ".trw" / "trash").exists()


def test_a_restore_note_from_a_dry_run_is_conditional_and_names_no_path() -> None:
    from trw_mcp.bootstrap._restore_proof import _HELD, _MOVED_TO, _PUT_BACK, as_dry_run_note

    real = f"REVIEW.md: {_HELD}{_MOVED_TO} /proj/.trw/trash/20261008T085717Z-abc/data {_PUT_BACK}"
    assert "they were moved to /proj/.trw/trash/20261008T085717Z-abc/data before" in real, "a real run's wording"
    dry = as_dry_run_note(real)
    assert dry.startswith("REVIEW.md: ") and "would be moved to a new folder under .trw/trash" in dry
    assert "were moved" not in dry and "20261008T085717Z" not in dry
    assert as_dry_run_note("unrelated warning") == "unrelated warning"


def test_a_dry_run_over_a_refreshable_marker_predicts_no_stale_marker(enrolled: Path) -> None:
    before = (enrolled / _MARKER).read_bytes()
    result = update_project(enrolled, ide="claude-code", dry_run=True)
    assert _stale_lines(result) == [], result["warnings"]
    assert (enrolled / _MARKER).read_bytes() == before, "a dry run never writes the marker"


def test_an_unenrolled_project_gets_no_marker_no_evidence_and_no_warning(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    _git(root, "init", "-q")
    assert not init_project(root, ide="claude-code")["errors"]

    result = update_project(root, ide="claude-code")

    assert _stale_lines(result) == []
    assert not (root / _MARKER).exists() and not enrollment_evidence_present(root)
    assert not [key for key in result if key.startswith("_enrollment")]


# --- the kept-hook remedy and the guard's own message ------------------------------------------------------


def test_deleting_a_kept_hook_and_running_update_again_restores_it(tmp_path: Path) -> None:
    root = tmp_path / "kept"
    root.mkdir()
    _git(root, "init", "-q")
    assert not init_project(root, ide="claude-code")["errors"]
    config = root / ".trw" / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "cc03_hook_enabled: true\n", encoding="utf-8")
    assert not update_project(root, ide="claude-code")["errors"]
    rel = ".claude/hooks/pre-tool-distill-hint.sh"
    hook = root / rel
    bundled = hook.read_bytes()
    hook.write_bytes(bundled + b"# mine\n")

    first = update_project(root, ide="claude-code")
    (line,) = [w for w in first["warnings"] if w.startswith(f"{rel}: kept because it was edited")]
    assert "delete it and run update-project again" in line and "--reprovision" not in line

    hook.unlink()
    second = update_project(root, ide="claude-code")

    assert not second["errors"], second["errors"]
    assert hook.read_bytes() == bundled, "the sequence the warning names restores the bundled hook"
    assert rel not in (root / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8").split("skills:")[0]


def test_the_stale_block_tells_an_agent_to_stop_and_names_the_operator_command(
    enrolled: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.security.intent_contract import _hook_common

    guard = enrolled / ".claude" / "hooks" / _GUARD
    guard.write_bytes(guard.read_bytes() + b"# drift\n")
    monkeypatch.setattr(_hook_common, "repo_root", lambda: enrolled)

    decision = _hook_common.enrollment_gate(2)

    assert isinstance(decision, _hook_common.HookDecision) and decision.code == 2, "the block itself is unchanged"
    assert decision.message.startswith("enrollment marker is stale")
    assert "stop and report this block instead of writing by another route" in decision.message
    assert "whatever the target path" in decision.message
    assert "enrollment refresh-hooks" in decision.message and "enrollment enroll" in decision.message
    capsys.readouterr()
