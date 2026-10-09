"""The automatic enrollment re-bless under adversarial timing, operator formatting and redirected references.

Second file for ``_enrollment_rebless`` (the first, ``test_update_enrollment_rebless.py``, holds the end-to-end
behaviour). Each test here names the exact sequence it simulates; every one failed against the first version
of the helper, which compared the installed hooks and then blessed a SECOND read of them, claimed marker bytes
it read back as its own write, and re-serialized a marker the operator had formatted.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from trw_mcp._checkout_write import recording_writes, written_this_run
from trw_mcp.bootstrap import _enrollment_rebless as rb
from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._utils import _DATA_DIR
from trw_mcp.security.intent_contract import enrollment
from trw_mcp.security.intent_contract.enrollment import compute_digests, enrollment_drift, write_enrollment

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

_MARKER = ".trw/contracts/enrollment.yaml"
_LIB = "lib-trw.sh"
_GUARD = "pre-tool-intent-guard.sh"
_BUNDLE = _DATA_DIR / "hooks"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _age(root: Path, name: str) -> None:
    """Make *name* an OLDER vendor build: different bytes that the manifest records as TRW's own write."""
    hook = root / ".claude" / "hooks" / name
    old = _sha(hook.read_bytes())
    hook.write_bytes(hook.read_bytes() + b"# an older vendor build\n")
    for record in (root / ".trw" / "managed-artifacts.yaml", root / ".trw" / "runtime" / "written-digests.json"):
        text = record.read_text(encoding="utf-8")
        assert old in text, f"fixture: {record.name} must record {name}"
        record.write_text(text.replace(old, _sha(hook.read_bytes())), encoding="utf-8")


def _install(tmp_path: Path, *, ignore_marker: bool = False, aged: bool = True) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    assert not init_project(root, ide="claude-code")["errors"]
    if aged:
        _age(root, _LIB)
    if ignore_marker:
        with (root / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write(f"\n{_MARKER}\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "installed")
    write_enrollment(root)
    assert enrollment_drift(root) == ()
    return root


@pytest.fixture
def enrolled(tmp_path: Path) -> Path:
    """A committed install with one older vendor hook, enrolled over it; the marker is uncommitted."""
    return _install(tmp_path)


@pytest.fixture
def refreshable(enrolled: Path) -> Path:
    """Every installed hook equals the bundle, and the marker still records the older one: a refresh is due."""
    (enrolled / ".claude" / "hooks" / _LIB).write_bytes((_BUNDLE / _LIB).read_bytes())
    assert enrollment_drift(enrolled) == ("expected_hook_digest",)
    return enrolled


def _stale_lines(result: dict[str, list[str]]) -> list[str]:
    return [w for w in result["warnings"] if "INTENT-CONTRACT ENROLLMENT" in w]


def _result() -> dict[str, list[str]]:
    return {"warnings": [], "errors": [], "preserved": []}


# --- 1. time of check to time of use ---------------------------------------------------------------------


def test_a_hook_swapped_after_the_gate_read_is_not_blessed(refreshable: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sequence: the gate reads four bundle-equal hooks; the guard hook is then replaced; the marker is written.

    The digest in the marker must be the bundle's, so the marker disagrees with the replaced file: stale.
    """
    guard = refreshable / ".claude" / "hooks" / _GUARD
    real_gate = rb.hooks_differing_from_bundle

    def gate_then_swap(*args: object, **kwargs: object) -> list[str]:
        differing = real_gate(*args, **kwargs)  # type: ignore[arg-type]
        guard.write_bytes(b"#!/bin/sh\nexit 0\n")  # the swap lands after the comparison, before the write
        return differing

    monkeypatch.setattr(rb, "hooks_differing_from_bundle", gate_then_swap)

    rb.rebless_intent_hook_digest(refreshable, _result(), _BUNDLE)

    recorded = enrollment.load_marker_fields(refreshable / _MARKER) or {}
    assert recorded["expected_hook_digest"] != compute_digests(refreshable)["expected_hook_digest"]
    assert enrollment_drift(refreshable) == ("expected_hook_digest",), "the replaced hook was never blessed"


# --- 2. the write ledger registers the payload, never a read-back ------------------------------------------


def _operator_saves_after_the_write(monkeypatch: pytest.MonkeyPatch, marker: Path) -> list[bytes]:
    """After TRW's marker write returns, an operator saves a marker in TRW's own format with another value."""
    saved: list[bytes] = []
    real_write = rb.write_text_confined

    def write_then_operator_saves(root: Path, path: Path, text: str, **kwargs: bool) -> None:
        real_write(root, path, text, **kwargs)
        if path == marker:
            theirs = re.sub(r"enrolled_at: '[^']*'", "enrolled_at: '1999-01-01T00:00:00+00:00'", text)
            assert theirs != text
            marker.write_bytes(theirs.encode("utf-8"))
            saved.append(theirs.encode("utf-8"))

    monkeypatch.setattr(rb, "write_text_confined", write_then_operator_saves)
    return saved


def test_the_ledger_registers_the_payload_written_not_what_is_on_disk_afterwards(
    refreshable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sequence: the re-bless writes payload P; an operator immediately saves canonical-looking bytes O."""
    marker = refreshable / _MARKER
    saved = _operator_saves_after_the_write(monkeypatch, marker)

    with recording_writes():
        rb.rebless_intent_hook_digest(refreshable, _result(), _BUNDLE)
        claimed = written_this_run(marker)

    assert saved and marker.read_bytes() == saved[-1], "fixture: the operator's save is what is on disk"
    assert claimed is not None and claimed != _sha(saved[-1]), "the operator's bytes are not this run's write"


def test_an_operator_save_right_after_the_rebless_write_is_never_deleted(
    enrolled: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sequence: update writes new hook bytes, re-blesses the dirty marker (P), the operator saves O over it,
    then the uncommitted-file restore runs. O must end in place or in ``.trw/trash``, never dropped."""
    marker = enrolled / _MARKER
    saved = _operator_saves_after_the_write(monkeypatch, marker)

    result = update_project(enrolled, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert saved, "fixture: the re-bless must have written the marker"
    kept = [p for p in (enrolled / ".trw" / "trash").rglob("*") if p.is_file()]
    assert marker.read_bytes() == saved[-1] or any(p.read_bytes() == saved[-1] for p in kept), result["warnings"]


# --- 3. a marker the operator formatted is never rewritten, with or without git's help ---------------------


def _commented_stale_marker(root: Path) -> bytes:
    marker = root / _MARKER
    text = re.sub(r"expected_hook_digest: '[^']*'", f"expected_hook_digest: '{'0' * 64}'", marker.read_text("utf-8"))
    marker.write_text("# operator note: reviewed by sec\n" + text, encoding="utf-8")
    return marker.read_bytes()


def test_a_commented_marker_survives_an_init_rerun_and_the_run_warns(tmp_path: Path) -> None:
    """Sequence: enrolled, hooks equal the bundle, the marker carries a comment and a wrong hook digest;
    ``init-project`` runs again (no dirty-file restore exists on this path)."""
    root = _install(tmp_path, aged=False)
    before = _commented_stale_marker(root)

    result = init_project(root, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert (root / _MARKER).read_bytes() == before
    (line,) = _stale_lines(result)
    assert "is STALE" in line and "refresh-hooks" in line


def test_a_commented_gitignored_marker_survives_an_update_and_the_run_warns(tmp_path: Path) -> None:
    """Sequence: the marker is git-ignored (so never in the dirty set) and commented; the update writes new
    vendor hook bytes, after which every hook equals the bundle."""
    root = _install(tmp_path, ignore_marker=True)
    marker = root / _MARKER
    marker.write_text(marker.read_text(encoding="utf-8") + "# operator note\n", encoding="utf-8")
    before = marker.read_bytes()

    result = update_project(root, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert (root / ".claude" / "hooks" / _LIB).read_bytes() == (_BUNDLE / _LIB).read_bytes()
    assert marker.read_bytes() == before
    assert len(_stale_lines(result)) == 1
    assert not (root / ".trw" / "trash").exists()


# --- 4. the reference bytes cannot be redirected ---------------------------------------------------------


def test_an_overridden_data_directory_gets_no_automatic_refresh(refreshable: Path, tmp_path: Path) -> None:
    other = tmp_path / "other-bundle" / "hooks"
    shutil.copytree(_BUNDLE, other)
    before = (refreshable / _MARKER).read_bytes()

    rb.rebless_intent_hook_digest(refreshable, _result(), other)

    assert (refreshable / _MARKER).read_bytes() == before


def test_a_symlinked_bundle_file_counts_as_differing(refreshable: Path, tmp_path: Path) -> None:
    other = tmp_path / "linked-bundle"
    shutil.copytree(_BUNDLE, other)
    (other / _GUARD).unlink()
    (other / _GUARD).symlink_to(_BUNDLE / _GUARD)
    assert rb.hooks_differing_from_bundle(refreshable, other) == [_GUARD]


# --- 5. the dry-run drift check never aborts a dry run ----------------------------------------------------


def test_a_marker_whose_contract_path_holds_a_nul_does_not_abort_a_dry_run(enrolled: Path) -> None:
    marker = enrolled / _MARKER
    text = re.sub(r"contract_path: '[^']*'", r'contract_path: "a\\0b"', marker.read_text(encoding="utf-8"))
    marker.write_text(text, encoding="utf-8")
    assert "\x00" in str((enrollment.load_marker_fields(marker) or {})["contract_path"]), "fixture"

    result = update_project(enrolled, ide="claude-code", dry_run=True)

    assert not result["errors"], result["errors"]
    assert len([w for w in result["warnings"] if "enrollment status could not be read" in w]) == 1
    assert marker.read_text(encoding="utf-8") == text


# --- 6. the gate, exercised through the helper and through init-project -----------------------------------


def _break_guard(root: Path, kind: str, tmp_path: Path) -> None:
    guard = root / ".claude" / "hooks" / _GUARD
    copy = tmp_path / "guard-copy.sh"
    copy.write_bytes(guard.read_bytes())
    guard.unlink()
    if kind == "symlinked":
        guard.symlink_to(copy)


@pytest.mark.parametrize("kind", ["missing", "symlinked"])
def test_the_rebless_leaves_the_marker_alone_over_a_missing_or_symlinked_hook(
    refreshable: Path, tmp_path: Path, kind: str
) -> None:
    _break_guard(refreshable, kind, tmp_path)
    before = (refreshable / _MARKER).read_bytes()

    rb.rebless_intent_hook_digest(refreshable, _result(), _BUNDLE)

    assert (refreshable / _MARKER).read_bytes() == before


def test_an_init_rerun_leaves_the_marker_alone_over_a_symlinked_hook(refreshable: Path, tmp_path: Path) -> None:
    """A re-init without --force writes no hook over what is at the name, so the gate must hold."""
    _break_guard(refreshable, "symlinked", tmp_path)
    before = (refreshable / _MARKER).read_bytes()

    result = init_project(refreshable, ide="claude-code")

    assert (refreshable / ".claude" / "hooks" / _GUARD).is_symlink(), "fixture: init left the name alone"
    assert (refreshable / _MARKER).read_bytes() == before
    assert len(_stale_lines(result)) == 1


def test_an_init_rerun_over_a_deleted_hook_records_only_the_bundle_digest_and_warns(
    refreshable: Path, tmp_path: Path
) -> None:
    """A re-init copies the deleted hook back, the re-bless runs, then init withdraws the hook again as a
    file the user deleted. Whatever the marker ends up recording, it is the BUNDLE's digest or the old one,
    never the digest of the tree with the hook absent; and the run says the project is stale."""
    _break_guard(refreshable, "missing", tmp_path)
    before = (refreshable / _MARKER).read_bytes()

    result = init_project(refreshable, ide="claude-code")

    assert not (refreshable / ".claude" / "hooks" / _GUARD).exists(), "fixture: the hook is absent at the end"
    recorded = (enrollment.load_marker_fields(refreshable / _MARKER) or {})["expected_hook_digest"]
    bundle = enrollment.hook_digest_of(rb.read_bundled_hooks(_BUNDLE))
    assert (refreshable / _MARKER).read_bytes() == before or recorded == bundle
    assert recorded != compute_digests(refreshable)["expected_hook_digest"]
    assert enrollment_drift(refreshable) == ("expected_hook_digest",) and len(_stale_lines(result)) == 1


# --- 7. the guard's second enrollment signal, and dry-run recovery sentences -------------------------------


def test_a_missing_marker_on_a_project_recorded_as_enrolled_is_warned_about(enrolled: Path) -> None:
    (enrolled / _MARKER).unlink()
    assert enrollment.enrollment_evidence_present(enrolled), "fixture: the durable second signal"

    result = update_project(enrolled, ide="claude-code")

    (line,) = _stale_lines(result)
    assert "marker is missing or unreadable" in line and "enrollment enroll" in line
    assert "An agent must not run" in line
    assert not (enrolled / _MARKER).exists(), "the check never mints a marker"


def test_a_dry_run_recovery_sentence_is_conditional_and_names_no_path() -> None:
    from trw_mcp.bootstrap._restore_proof import as_dry_run_note

    for real in (
        "AGENTS.md: your pre-update version is at /tmp/scratch-1/.trw/trash/20261008T1-ab/data/AGENTS.md",
        "AGENTS.md: your pre-update version could not be saved in the project; it is kept only at /tmp/snap/AGENTS.md",
    ):
        dry = as_dry_run_note(real)
        assert dry.startswith("AGENTS.md: ") and "/tmp/" not in dry and "would" in dry, dry
        assert " is at " not in dry and "is kept only at" not in dry


# --- 8. capture, then exclusive publish: a concurrent save is never overwritten ---------------------------


def _another_marker(marker: Path) -> bytes:
    text = marker.read_text(encoding="utf-8")
    theirs = re.sub(r"enrolled_at: '[^']*'", "enrolled_at: '1999-01-01T00:00:00+00:00'", text)
    assert theirs != text
    return theirs.encode("utf-8")


def test_an_operator_save_during_the_digest_computation_survives_and_nothing_is_written(
    refreshable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sequence: the writer validates marker A; while it recomputes digests an operator saves marker B;
    the writer then reaches its write. B must be at the marker path byte for byte, and nothing registered."""
    marker = refreshable / _MARKER
    theirs = _another_marker(marker)
    real_compute = enrollment.compute_digests

    def compute_then_operator_saves(*args: object, **kwargs: object) -> dict[str, str]:
        digests = real_compute(*args, **kwargs)  # type: ignore[arg-type]
        marker.write_bytes(theirs)
        return digests

    result = _result()
    with monkeypatch.context() as patch, recording_writes():
        patch.setattr(enrollment, "compute_digests", compute_then_operator_saves)
        rb.rebless_intent_hook_digest(refreshable, result, _BUNDLE)
        claimed = written_this_run(marker)

    assert marker.read_bytes() == theirs, "the operator's save was not overwritten"
    assert claimed is None, "nothing was written, so nothing is registered as this run's"
    assert enrollment_drift(refreshable) == ("expected_hook_digest",)


def test_a_file_that_appears_between_capture_and_publish_is_not_replaced(
    refreshable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sequence: marker A is captured into trash and verified; before the publish a file X is created at the
    marker path. X stays, A stays in trash, nothing is registered, and the run says so."""
    marker = refreshable / _MARKER
    ours, theirs = marker.read_bytes(), _another_marker(marker)
    real_capture = rb.remove_if_hash

    def capture_then_a_file_appears(*args: object, **kwargs: object) -> object:
        outcome = real_capture(*args, **kwargs)  # type: ignore[arg-type]
        assert outcome.status == "removed" and not marker.exists(), "fixture: A was captured"
        marker.write_bytes(theirs)
        return outcome

    monkeypatch.setattr(rb, "remove_if_hash", capture_then_a_file_appears)
    result = _result()
    with recording_writes():
        rb.rebless_intent_hook_digest(refreshable, result, _BUNDLE)
        claimed = written_this_run(marker)

    assert marker.read_bytes() == theirs and claimed is None
    kept = [p for p in (refreshable / ".trw" / "trash").rglob("data") if p.is_file()]
    assert [p.read_bytes() for p in kept] == [ours], "the captured marker is still in trash"
    assert [w for w in result["warnings"] if _MARKER in w and "left alone" in w], result["warnings"]


def test_the_normal_refresh_publishes_registers_its_payload_and_leaves_no_trash(refreshable: Path) -> None:
    marker = refreshable / _MARKER
    for run in (1, 2):
        with recording_writes():
            rb.rebless_intent_hook_digest(refreshable, _result(), _BUNDLE)
            claimed = written_this_run(marker)
        assert enrollment_drift(refreshable) == (), f"run {run}"
        assert claimed == (_sha(marker.read_bytes()) if run == 1 else None), "only a real write is registered"
        assert not (refreshable / ".trw" / "trash").exists(), f"run {run} left a capture behind"
        sidecar = refreshable / ".trw" / "contracts" / "enrollment.globs"
        assert sidecar.is_file(), "the sidecar is written once the marker is in place"
        assert marker.stat().st_mtime_ns >= sidecar.stat().st_mtime_ns, (
            "the hooks refuse a sidecar newer than the marker"
        )


# --- 9. a bundle that aliases the project is no reference --------------------------------------------------


def test_a_bundle_copied_into_a_virtual_environment_inside_the_project_still_refreshes(
    refreshable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary layout: the package lives in ``<project>/.venv``. A separate copy is an independent
    reference wherever it sits; only aliasing (a symlink, or the same file) makes it worthless."""
    inside = refreshable / ".venv" / "lib" / "python3.12" / "site-packages" / "trw_mcp" / "data" / "hooks"
    shutil.copytree(_BUNDLE, inside)
    monkeypatch.setattr(rb, "_package_hooks_dir", lambda: inside)

    rb.rebless_intent_hook_digest(refreshable, _result(), inside)

    assert enrollment_drift(refreshable) == ()


def test_a_publish_that_cannot_be_written_puts_the_previous_marker_back(
    refreshable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sequence: marker A is captured into trash; the exclusive create then fails with a permission error
    and nothing is at the marker path. A must be back at its path, nothing registered, and the run says so."""
    marker = refreshable / _MARKER
    before = marker.read_bytes()

    def refuse(root: Path, path: Path, text: str, **kwargs: bool) -> None:
        assert not marker.exists(), "fixture: the marker was captured before the publish"
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(rb, "write_text_confined", refuse)
    result = _result()
    with recording_writes():
        rb.rebless_intent_hook_digest(refreshable, result, _BUNDLE)
        claimed = written_this_run(marker)

    assert marker.read_bytes() == before and claimed is None
    (line,) = [w for w in result["warnings"] if _MARKER in w]
    assert "could not be written" in line and "previous marker was restored" in line
    assert "a file appeared" not in line
    assert enrollment_drift(refreshable) == ("expected_hook_digest",)


def test_an_installed_hook_that_is_the_bundled_file_itself_gets_no_automatic_refresh(
    refreshable: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "pkg" / "data" / "hooks"
    shutil.copytree(_BUNDLE, outside)
    guard = refreshable / ".claude" / "hooks" / _GUARD
    guard.unlink()
    os.link(outside / _GUARD, guard)  # one inode, two names: "equal to the bundle" proves nothing
    monkeypatch.setattr(rb, "_package_hooks_dir", lambda: outside)
    before = (refreshable / _MARKER).read_bytes()

    rb.rebless_intent_hook_digest(refreshable, _result(), outside)

    assert rb.hooks_differing_from_bundle(refreshable, outside) == [], "fixture: the bytes do compare equal"
    assert (refreshable / _MARKER).read_bytes() == before


def test_a_symlinked_component_of_the_bundle_path_gets_no_automatic_refresh(
    refreshable: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "pkg" / "real-data"
    shutil.copytree(_BUNDLE, real / "hooks")
    (tmp_path / "pkg" / "data").symlink_to(real)
    linked = tmp_path / "pkg" / "data" / "hooks"
    monkeypatch.setattr(rb, "_package_hooks_dir", lambda: linked)
    before = (refreshable / _MARKER).read_bytes()

    rb.rebless_intent_hook_digest(refreshable, _result(), linked)

    assert (refreshable / _MARKER).read_bytes() == before


# --- 10. a FIFO where a hook should be --------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
def test_a_fifo_swapped_in_for_a_hook_reads_as_differing_and_never_blocks(
    refreshable: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sequence: a check by name still sees a regular file (the stat is taken, then the swap lands), and the
    open meets a FIFO. Any by-name stat is made to report the bundled file, as the moment before the swap."""
    import threading

    guard = refreshable / ".claude" / "hooks" / _GUARD
    guard.unlink()
    os.mkfifo(guard)
    real_lstat = os.lstat
    monkeypatch.setattr(
        os, "lstat", lambda p, *a, **k: real_lstat(_BUNDLE / _GUARD if Path(p) == guard else p, *a, **k)
    )
    found: list[list[str]] = []
    worker = threading.Thread(
        target=lambda: found.append(rb.hooks_differing_from_bundle(refreshable, _BUNDLE)), daemon=True
    )
    worker.start()
    worker.join(timeout=10)
    blocked = worker.is_alive()
    if blocked:  # release a reader stuck in open(2) so the thread does not outlive the test
        os.close(os.open(guard, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(timeout=5)

    assert not blocked, "reading the hook blocked on a FIFO"
    assert found == [[_GUARD]]


def test_an_oversized_hook_counts_as_differing(refreshable: Path, tmp_path: Path) -> None:
    other = tmp_path / "big-bundle"
    shutil.copytree(_BUNDLE, other)
    huge = b"#" * (4 * 1024 * 1024 + 1)
    (other / _GUARD).write_bytes(huge)
    (refreshable / ".claude" / "hooks" / _GUARD).write_bytes(huge)
    assert rb.hooks_differing_from_bundle(refreshable, other) == [_GUARD]
