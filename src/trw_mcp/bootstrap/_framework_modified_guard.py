"""Say so when a canon redeploy replaces a body someone edited (CODEX-P0-C step 2).

Belongs to ``_utils._write_version_yaml`` (split out for the 350-eLOC gate). The stamp guard in
``framework_integrity.newer_deployed_generation`` refuses a NEWER deployed generation; it compares versions, not bytes.
Under the SAME stamp a drifted body is deliberately repaired (``test_same_version_drift_still_repairs``: the canon is a
generated read-only reference, and repair is also how a truncated or corrupt file heals), and the deployer copies the
previous bytes under ``.trw/frameworks/.rollback/`` first, but the replacement used to be silent. The last deploy left a
receipt (``DEPLOYMENT.json`` ``artifact_digests``): a body whose bytes match neither that receipt nor the bundle being
deployed was written by someone else. That is now reported, with where the old bytes are. Absent or unreadable receipt:
no proof either way, no report.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from pathlib import Path

from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH
from trw_mcp.framework_integrity import FORCE_DEPLOY_ENV


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def modified_canon_bodies(target: Path, expected: dict[Path, bytes]) -> list[str]:
    """Repo-relative canon bodies whose bytes match neither the last deploy receipt nor *expected*."""
    if os.environ.get(FORCE_DEPLOY_ENV) == "1":
        return []
    receipt_path = target / DEPLOYMENT_RELATIVE_PATH
    if not receipt_path.is_file():  # a missing receipt, or a FIFO/socket that would block the read: no proof
        return []
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        deployed = receipt["artifact_digests"]
        if not isinstance(deployed, dict):
            return []
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ):  # trw-fail-silent-allow: no readable receipt = no ownership proof either way; the caller redeploys as before and a .rollback snapshot keeps the old bytes
        return []
    modified = []
    for relative, wanted in sorted(expected.items()):
        if relative.as_posix() not in deployed:  # the receipt says nothing about this body: no proof it was edited
            continue
        try:
            current = _sha256((target / relative).read_bytes())
        except OSError:  # trw-fail-silent-allow: a missing body is not an edit; the deploy recreates it
            continue
        if current not in (_sha256(wanted), deployed.get(relative.as_posix())):
            modified.append(relative.as_posix())
    return modified


_ROLLBACK_DIR = Path(".trw/frameworks/.rollback")


def newest_rollback(target: Path) -> Path | None:
    """The newest snapshot directory under ``.trw/frameworks/.rollback/`` (its name ends in a UTC timestamp), or None."""
    root = target / _ROLLBACK_DIR
    try:
        entries = [p for p in root.iterdir() if p.is_dir()]
    except OSError:  # trw-fail-silent-allow: no rollback directory (or unreadable) simply means nothing was saved
        return None
    return max(entries, key=lambda p: p.name.rsplit("-", 1)[-1], default=None)


def restore_command(target: Path, rollback: Path, relative: str) -> str:
    """The shell command that puts the saved bytes of *relative* back (paths relative to *target*)."""
    saved = (rollback / relative).relative_to(target).as_posix()
    return f"cp {shlex.quote(saved)} {shlex.quote(relative)}"  # a snapshot directory name is not trusted shell text


def snapshot_holding(target: Path, relative: str, digest: str) -> Path | None:
    """The newest snapshot whose saved *relative* has sha256 *digest* (the bytes a deploy just replaced), or None."""
    root = target / _ROLLBACK_DIR
    try:
        snapshots = sorted(
            (p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.rsplit("-", 1)[-1], reverse=True
        )
    except OSError:  # trw-fail-silent-allow: no rollback directory (or unreadable) simply means nothing was saved
        return None
    for snapshot in snapshots:
        try:
            if _sha256((snapshot / relative).read_bytes()) == digest:
                return snapshot
        except OSError:  # trw-fail-silent-allow: a snapshot without this body cannot be the one that holds it
            continue
    return None


def modified_warning(names: list[str], target: Path | None = None, digests: dict[str, str] | None = None) -> str:
    """Name the replaced bodies, where their old bytes are, and the exact command that restores each.

    Call it AFTER the deploy. *digests* maps each body to the sha256 of the bytes that were replaced, so the note
    points at the snapshot that really holds them even if another deploy made a newer snapshot meanwhile.
    """
    holders: dict[str, Path] = {}
    if target is not None:
        for name in names:
            found = (
                snapshot_holding(target, name, (digests or {}).get(name, "")) if digests else newest_rollback(target)
            )
            if found is not None and (found / name).is_file():
                holders[name] = found
    where = (
        ", ".join(sorted({p.relative_to(target).as_posix() for p in holders.values()}))
        if target is not None and holders
        else ".trw/frameworks/.rollback/"
    )
    restore = ""
    if target is not None and holders:
        commands = [restore_command(target, snapshot, name) for name, snapshot in holders.items()]
        restore = f" To keep your version instead, run: {'; '.join(commands)} (the next update will replace it again)."
    return (
        f"Framework canon replaced: {', '.join(names)} differed from the last deployed generation (DEPLOYMENT.json) and "
        f"from this package's copy, so they looked edited. The previous bytes are saved in {where}.{restore} "
        f"This directory is a generated reference: change the bundled source, not the copy."
    )


def saved_canon_copies(target: Path) -> list[tuple[str, Path]]:
    """``(canon body, snapshot dir)`` for each body whose newest EDITED saved copy still differs from what is deployed.

    A snapshot holds the receipt of the generation it replaced, so a saved body is an edit exactly when its bytes do
    not match that receipt; a plain upgrade's snapshot (bytes match its own receipt) is not reported, and a later
    clean deploy does not hide an earlier edit because every snapshot is scanned, newest first.
    """
    root = target / _ROLLBACK_DIR
    try:
        snapshots = sorted(
            (p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.rsplit("-", 1)[-1], reverse=True
        )
    except OSError:  # trw-fail-silent-allow: no rollback directory (or unreadable) simply means nothing was saved
        return []
    found: dict[str, Path] = {}
    for snapshot in snapshots:
        receipt = snapshot / DEPLOYMENT_RELATIVE_PATH
        if not receipt.is_file():  # a missing receipt, or a FIFO/socket that would block the read: no proof of an edit
            continue
        try:
            digests = json.loads(receipt.read_text(encoding="utf-8"))["artifact_digests"]
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
        ):  # trw-fail-silent-allow: no receipt in the snapshot means no proof of an edit
            continue
        for relative in (".trw/frameworks/FRAMEWORK.md", ".trw/frameworks/AARE-F-FRAMEWORK.md"):
            if relative in found or not isinstance(digests, dict) or relative not in digests:
                continue
            try:
                saved = (snapshot / relative).read_bytes()
                current = (target / relative).read_bytes()
            except OSError:  # trw-fail-silent-allow: an unreadable pair is not reported as a saved edit
                continue
            if _sha256(saved) != digests[relative] and saved != current:
                found[relative] = snapshot
    return sorted(found.items())
