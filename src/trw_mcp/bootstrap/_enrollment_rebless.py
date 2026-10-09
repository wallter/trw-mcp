"""The intent-contract enrollment marker during ``update-project``: gated re-bless, survival, stale warning.

Belongs to ``_template_updater._update_hooks`` (the re-bless), ``_rerender.preserve_uncommitted_changes`` (the
re-bless surviving the uncommitted-file restore) and ``_update_project.update_project`` (the closing warning).

Three rules, each a security property of PRD-SEC-013's marker, not a convenience:

* The hook digest is refreshed automatically ONLY when every digest-covered hook file on disk is byte-equal
  to the file this package ships now. A hook kept as "edited", a tampered one, a missing one: no refresh.
  ``refresh_hook_digest`` blesses whatever is on disk, so this gate is what makes "the installer just wrote
  these bytes" true rather than assumed.
* A git-dirty marker keeps the refresh only when the refresh provably changed nothing but the hook digest and
  its timestamp, in a file that was already in TRW's own serialization. Anything else is put back whole.
* The closing check reads; it never writes the marker, the sidecar or the enrollment evidence.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
from pathlib import Path

import structlog

from trw_mcp.security.intent_contract.paths import write_text_confined

from ._trash import remove_if_hash

logger = structlog.get_logger(__name__)

#: Private ``result`` key: set by the dry run when its scratch copy ended with a drifted hook digest.
SCRATCH_HOOK_DRIFT_KEY = "_enrollment_scratch_hook_drift"
_HOOK_KEYS = frozenset({"expected_hook_digest", "hook_digest_refreshed_at"})
_REFRESH = "python -m trw_mcp.security.intent_contract.enrollment"
#: No hook is near this size; a larger file is not read into memory and counts as differing.
_MAX_HOOK_BYTES = 4 * 1024 * 1024


def _marker_path(root: Path) -> Path:
    from trw_mcp.security.intent_contract.enrollment import enrollment_path

    return enrollment_path(root)


def marker_rel() -> str:
    """The marker's repo-relative posix path."""
    from trw_mcp.security.intent_contract.paths import ENROLLMENT_PATH

    return str(ENROLLMENT_PATH)


def _package_hooks_dir() -> Path:
    """The hook directory inside this installed package: the only reference an automatic refresh trusts."""
    from . import _utils

    return _utils._DATA_DIR / "hooks"


def _hook_names() -> tuple[str, ...]:
    from trw_mcp.security.intent_contract._control_plane import HOOK_SUPPORT_FILES, INTENT_HOOK_FILES

    return (*INTENT_HOOK_FILES, *HOOK_SUPPORT_FILES)


def _read_regular(path: Path) -> bytes | None:
    """*path*'s bytes when it is a regular file, not a symlink, of a sane size; None for anything else.

    The kind is decided on the open descriptor, never by name first: a file swapped for a FIFO between a
    stat and an open would block the open for ever. The shared opener (``_checkout_access.open_under``)
    opens the last component ``O_NOFOLLOW | O_NONBLOCK`` relative to its directory, so that open returns
    at once and never follows a link.
    """
    from trw_mcp._checkout_access import open_under

    try:
        fd = open_under(path.parent, path.name)
    # trw-fail-silent-allow: not silent; the caller reports the hook as differing
    except (OSError, ValueError, NotImplementedError) as exc:
        logger.info("intent_hook_unreadable_for_rebless", path=str(path), error=str(exc))
        return None
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode) or status.st_size > _MAX_HOOK_BYTES:
            return None
        chunks: list[bytes] = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
            if sum(map(len, chunks)) > _MAX_HOOK_BYTES:  # it grew while being read
                return None
        return b"".join(chunks)
    except OSError as exc:  # trw-fail-silent-allow: not silent; the caller reports the hook as differing
        logger.info("intent_hook_unreadable_for_rebless", path=str(path), error=str(exc))
        return None
    finally:
        os.close(fd)


def bundle_refusal(root: Path, hooks_dir: Path) -> str | None:
    """Why *hooks_dir* cannot serve as the trusted reference for *root*, or None when it can.

    A reference proves something only while it is independent of the files being checked, and what breaks
    that is ALIASING, not location: a bundled file that IS the installed file (one inode, two names), or a
    path from the data directory down to a hook that passes through a symlink, which could point anywhere.
    A bundle that merely lies inside the project is a separate copy and stays a valid reference: that is the
    ordinary layout (the package in ``<project>/.venv``), and for a package installed from a checkout inside
    the project it means the refresh happens only when the installed hooks are byte-equal to that source.
    """
    names = _hook_names()
    try:
        if any(p.is_symlink() for p in (hooks_dir.parent, hooks_dir, *(hooks_dir / name for name in names))):
            return "a symlink on the path to the bundled hooks"
        for name in names:
            installed = root / ".claude" / "hooks" / name
            if installed.exists() and os.path.samefile(installed, hooks_dir / name):
                return f"the installed {name} is the bundled file itself"
    except (OSError, ValueError) as exc:  # trw-fail-silent-allow: not silent; unprovable means refuse
        return f"the bundled hooks could not be inspected ({exc})"
    return None


def read_bundled_hooks(hooks_source: Path) -> dict[str, bytes]:
    """The digest-covered hook files *hooks_source* ships, read once; a missing or symlinked one is left out."""
    found = {name: _read_regular(hooks_source / name) for name in _hook_names()}
    return {name: data for name, data in found.items() if data is not None}


def hooks_differing_from_bundle(root: Path, hooks_source: Path, bundled: dict[str, bytes] | None = None) -> list[str]:
    """Names of digest-covered hook files whose installed bytes are not exactly the bundled bytes.

    Unreadable, absent or symlinked on either side counts as differing: only a positive byte-for-byte match
    is "the bytes this package ships". *bundled* is the reference already read (:func:`read_bundled_hooks`),
    so the caller can go on to use the very bytes that were compared.
    """
    reference = read_bundled_hooks(hooks_source) if bundled is None else bundled
    return [
        name
        for name in _hook_names()
        if name not in reference or _read_regular(root / ".claude" / "hooks" / name) != reference[name]
    ]


def rebless_intent_hook_digest(target_dir: Path, result: dict[str, list[str]], hooks_source: Path) -> None:
    """Re-bless the marker's HOOK half after a vendor resync, only over the bundled hook bytes.

    Without the re-bless, shipping a new bundled hook bricks every enrolled project (the marker reads
    ``stale`` and every Edit/Write is blocked though the user did nothing). Four conditions, all required:

    * *hooks_source* is this package's own hook directory. A caller that overrode the data directory gets no
      automatic refresh: a reference the caller chose proves nothing about what the package ships.
    * every digest-covered installed hook is byte-equal to the bundled one;
    * the digest recorded is computed from those BUNDLED bytes, the trusted side of the comparison, never
      from a second read of the installed files. A hook swapped after the comparison then leaves the marker
      disagreeing with disk, which reads stale, which is correct;
    * the marker on disk is already TRW's own serialization (checked by ``refresh_hook_digest_from``).

    The update's write ledger gets exactly the payload that was written, and only when one was. Fail-open: a
    problem here is a warning, never an aborted update; the closing :func:`warn_if_enrollment_stale` then
    tells the operator what to run.
    """
    marker = _marker_path(target_dir)
    if not marker.is_file() or marker.is_symlink():
        return  # never enrolled (or a link this module will not read through): nothing to bless
    refusal = (
        "data directory overridden"
        if hooks_source != _package_hooks_dir()
        else bundle_refusal(target_dir, hooks_source)
    )
    if refusal:
        logger.info("intent_enrollment_rebless_skipped", reason=refusal, path=str(target_dir))
        return
    try:
        from trw_mcp._checkout_write import record_run_write
        from trw_mcp.security.intent_contract.enrollment import hook_digest_of, refresh_hook_digest_from

        bundled = read_bundled_hooks(hooks_source)
        differing = hooks_differing_from_bundle(target_dir, hooks_source, bundled)
        if differing:
            logger.info("intent_enrollment_rebless_skipped", differing=differing, path=str(target_dir))
            return
        written = refresh_hook_digest_from(
            target_dir, hook_digest_of(bundled), lambda old, new: _publish_marker(target_dir, old, new, result)
        )
        if written is not None:
            record_run_write(marker, written)
            logger.info("intent_enrollment_hook_digest_refreshed", path=str(target_dir))
    except Exception as exc:  # justified: fail-open, an update must never abort here
        logger.warning("intent_enrollment_refresh_failed", error=str(exc))
        result.setdefault("warnings", []).append(f"intent-contract enrollment hook digest not refreshed: {exc}")


def _publish_marker(root: Path, validated: bytes, payload: str, result: dict[str, list[str]]) -> bool:
    """Replace the marker with *payload* only if it still holds *validated*; never overwrite anything else.

    Capture, then exclusive publish. The marker is renamed into a fresh ``.trw/trash`` folder and hashed
    THERE: bytes that are not the validated ones were saved by someone else meanwhile, and are linked back to
    the marker's name without replacing anything (``remove_if_hash``). The new marker is then created with
    ``O_EXCL``: a file that appeared at the name in between is left alone and the captured copy stays in
    trash. Only after a successful publish is the capture dropped; it held TRW's own previous serialization.
    """
    marker, rel = _marker_path(root), marker_rel()
    notes = result.setdefault("warnings", [])
    outcome = remove_if_hash(marker, root, hashlib.sha256(validated).hexdigest(), key=rel)
    if outcome.status != "removed":
        logger.info("intent_enrollment_rebless_skipped", reason=outcome.reason, status=outcome.status)
        if outcome.status == "retained":
            notes.append(f"{rel}: changed while the enrollment was being refreshed; a copy is at {outcome.retained_at}")
        return False
    captured = outcome.retained_at
    try:
        write_text_confined(root, marker, payload, exclusive=True)
    except OSError as exc:
        logger.warning("intent_enrollment_publish_refused", error=str(exc))
        where = captured or ".trw/trash"
        if os.path.lexists(marker):
            notes.append(
                f"{rel}: a file appeared here while the enrollment was being refreshed and was left alone; "
                f"the marker that was there before is at {where}"
            )
            return False
        # Nothing is at the name (a full disk, a permission error, a read-only tree): without the marker the
        # project reads as blocked, so the captured one goes back. A link never replaces what is there.
        try:
            if captured is None:
                raise FileNotFoundError("the capture location is unknown")
            os.link(captured, marker, follow_symlinks=False)
        # trw-fail-silent-allow: not silent; the operator is told where the previous marker is
        except OSError as link_exc:
            notes.append(
                f"{rel}: the refreshed enrollment could not be written ({exc}) and the previous marker could not "
                f"be put back ({link_exc}); it is at {where}. Operator: copy it back to {rel}"
            )
            return False
        notes.append(f"{rel}: the refreshed enrollment could not be written ({exc}); the previous marker was restored")
        _drop_capture(captured, validated)
        return False
    if captured is not None:
        _drop_capture(captured, validated)
    return True


def _drop_capture(captured: Path, validated: bytes) -> None:
    """Remove the capture folder of a marker that is TRW's own previous serialization, once it is not needed."""
    if _read_regular(captured) == validated:
        from trw_memory._tree_removal import remove_tree

        remove_tree(captured.parent, purpose="capture of the enrollment marker this run replaced")
        with contextlib.suppress(OSError):  # only an EMPTY trash folder goes; it existed for this capture alone
            captured.parent.parent.rmdir()


def rebless_loses_nothing(root: Path, before: Path, after: Path) -> bool:
    """True when keeping this run's re-blessed marker costs the user no byte of their uncommitted copy.

    All of: *after* is exactly the payload the gated re-bless wrote this run (the write ledger, which holds
    the bytes passed to the writer); the hook digest it records is the digest of this package's BUNDLED
    hooks; *before* was already in TRW's own serialization; and the two differ in nothing but the hook
    digest and its timestamp, so every other key, the contract half included, is byte-for-byte the user's.
    Anything unprovable is False and the caller puts the user's copy back whole.
    """
    from trw_mcp._checkout_write import written_this_run
    from trw_mcp.security.intent_contract.enrollment import hook_digest_of, load_marker_fields, render_marker

    del root  # the reference is the package's bundle, never a read of the project's hooks
    try:
        if before.is_symlink() or after.is_symlink() or not (before.is_file() and after.is_file()):
            return False
        if written_this_run(after) != hashlib.sha256(after.read_bytes()).hexdigest():
            return False
        old, new = load_marker_fields(before), load_marker_fields(after)
        if not old or not new or render_marker(old).encode("utf-8") != before.read_bytes():
            return False
        if {k: v for k, v in old.items() if k not in _HOOK_KEYS} != {
            k: v for k, v in new.items() if k not in _HOOK_KEYS
        }:
            return False
        return str(new.get("expected_hook_digest", "")) == hook_digest_of(read_bundled_hooks(_package_hooks_dir()))
    except (OSError, KeyError) as exc:  # trw-fail-silent-allow: not silent; unproven means the user's copy is put back
        logger.info("intent_enrollment_rebless_unproven", error=str(exc))
        return False


def record_scratch_hook_drift(scratch: Path, result: dict[str, list[str]]) -> None:
    """Dry run only: note whether the scratch copy's marker ended with a drifted hook digest.

    Only the hook half is read from the scratch copy: it holds the managed surface, not the contract or the
    pre-commit configuration, so its other digests say nothing about the real project.
    """
    from trw_mcp.security.intent_contract.enrollment import enrollment_drift

    try:
        drift = enrollment_drift(scratch)
    # trw-fail-silent-allow: logged; the closing warning reads the same marker and reports it once
    except Exception as exc:  # justified: fail-open, a reporting step must never abort the dry run
        # Logged only: the closing warn_if_enrollment_stale reads the same marker and reports it once.
        logger.warning("intent_enrollment_scratch_status_unreadable", error=str(exc))
        return
    if drift and "expected_hook_digest" in drift:
        result[SCRATCH_HOOK_DRIFT_KEY] = ["expected_hook_digest"]


def warn_if_enrollment_stale(target_dir: Path, result: dict[str, list[str]], *, dry_run: bool) -> None:
    """Close the update with one loud line when the enrolled project's marker is (or would be) stale.

    Read-only: it loads the marker and recomputes digests, and writes nothing. The wording is for the
    operator; re-blessing is an operator acknowledgement, so an agent is told to stop and report.
    """
    from trw_mcp.security.intent_contract.enrollment import enrollment_drift, marker_is_tracked

    scratch_hook = result.pop(SCRATCH_HOOK_DRIFT_KEY, [])
    try:
        drift = enrollment_drift(target_dir)
        # No readable marker, but the guard's durable second signal says this project enrolled: it blocks.
        missing = drift is None and marker_is_tracked(target_dir)
    # trw-fail-silent-allow: not silent; logged and added to the update's warnings
    except Exception as exc:  # justified: fail-open, a reporting step must never abort the update
        logger.warning("intent_enrollment_status_unreadable", error=str(exc))
        result.setdefault("warnings", []).append(f"intent-contract enrollment status could not be read: {exc}")
        return
    if missing:
        result.setdefault("warnings", []).append(
            f"INTENT-CONTRACT ENROLLMENT marker is missing or unreadable ({marker_rel()}), and this project is "
            "recorded as enrolled, so Write and Edit are blocked in every session rooted here. Operator "
            f"action: restore the marker, or run `{_REFRESH} enroll` to enroll again. An agent must not run "
            "that command: stop and report this to the operator."
        )
    if drift is None:
        return
    if dry_run:  # the hook half as the scratch run left it; the other halves as the real project has them
        drift = tuple(sorted({*scratch_hook, *(key for key in drift if key != "expected_hook_digest")}))
    if not drift:
        return
    state, blocked = (
        ("would be stale after this update", "would be blocked") if dry_run else ("is STALE", "are blocked")
    )
    result.setdefault("warnings", []).append(
        f"INTENT-CONTRACT ENROLLMENT {state} (drifted: {', '.join(drift)}). This project is enrolled, so Write "
        f"and Edit {blocked} in every session rooted here until an operator refreshes the enrollment. "
        f"Operator action: check the change was intended, then run `{_REFRESH} refresh-hooks` for new hook "
        f"bytes, or `{_REFRESH} enroll` for a contract change. An agent must not run either command: stop and "
        "report this to the operator."
    )
