"""FR10: the enrollment marker that distinguishes never-installed from tampered.

Before this marker, "this project never opted in" and "the contract or hook was
deleted after opt-in" produced the SAME silent allow. Now:

* marker absent AND no durable second signal -> ``never_enrolled`` — clean no-op
  (NFR01 row 2);
* marker absent but ``.trw/intent-enrollment-evidence.yaml`` present -> ``stale``.
  That file is written once at first enrollment and is the ONLY signal here that
  needs no git, which is what lets a never-enrolled project stay inert when git
  cannot answer — an ordinary state (pruned worktree, partial clone, a typo in
  the user's global gitconfig, dubious ownership under a bind mount) that used to
  block every write. See :func:`marker_is_tracked` for the trade and its residual;
* marker absent but RECORDED IN HEAD (or merely staged) -> ``stale``: a
  working-tree deletion cannot disarm the controls, because git history is a
  durable second signal outside the tree (a deletion that is COMMITTED is caught
  by C9 instead). HEAD, not the index, is what makes that signal durable — see
  :func:`trw_mcp.security.intent_contract._git_run.path_in_history`;
* digests match  -> ``current``;
* any mismatch   -> ``stale`` — fail closed AND print an unsuppressible warning.

Enrollment REFUSES by default when the intent hook scripts are not installed
(:class:`EnrollmentError`). Absent hooks digest as ``<absent>``, which the marker
happily records as the expected state — so ``enroll`` printed success and
``status`` printed ``current`` for a project where no control point was
registered to run. That is the delivered-but-not-wired failure relocated into the
enablement UX, and reporting it as protection is a truthfulness defect in this
tool's own output. ``--allow-missing-hooks`` is the explicit opt-out.

Operational note: ``expected_contract_digest`` covers the contract bytes, so a
legitimate contract edit intentionally requires a re-enrollment (``python3 -m
trw_mcp.security.intent_contract.enrollment enroll``) — that re-enrollment is the
operator's explicit acknowledgement of the new claim set.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from trw_mcp.security.intent_contract._control_plane import (
    DEFAULT_CONTRACT_PATH,
    HOOK_SUPPORT_FILES,
    INTENT_HOOK_FILES,
    _pre_commit_intent_hooks,
)
from trw_mcp.security.intent_contract._git_run import path_in_history
from trw_mcp.security.intent_contract.paths import (
    ENROLLMENT_EVIDENCE_PATH,
    ENROLLMENT_PATH,
    is_regular_file,
    parse_root_arg,
    read_bytes_nofollow,
    repo_root,
    stray_options,
    unlink_confined,
    write_text_confined,
)

__all__ = [
    "ENROLLMENT_SCHEMA_VERSION",
    "EnrollmentError",
    "EnrollmentStatus",
    "check_enrollment_status",
    "clear_enrollment",
    "compute_digests",
    "enrollment_evidence_path",
    "enrollment_evidence_present",
    "enrollment_evidence_squatted",
    "enrollment_path",
    "marker_is_tracked",
    "missing_intent_hooks",
    "record_enrollment_evidence",
    "refresh_hook_digest",
    "stale_enrollment_warning",
    "write_enrollment",
]


ENROLLMENT_SCHEMA_VERSION = 1

EnrollmentStatus = Literal["never_enrolled", "current", "stale"]

#: Where an installed project's hook copies live (bundled sources are installed here).
_INSTALLED_HOOK_DIR = ".claude/hooks"
_ABSENT = "<absent>"


class EnrollmentError(RuntimeError):
    """Enrollment was REFUSED. No marker was written."""


def enrollment_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / ENROLLMENT_PATH


def enrollment_evidence_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / ENROLLMENT_EVIDENCE_PATH


def enrollment_evidence_present(root: Path) -> bool:
    """Is there enrollment evidence at *root*, by the rule the HOOKS also use?

    The hooks test ``[ -f "$root/$evidence" ]``. Python asked ``Path.exists()``,
    which is additionally true for a directory and for a symlink to ``/dev/null``
    — and in that gap both edit-time hooks went inert while ``enrollment status``
    printed ``stale`` and exited 1 (finding F-F, 2026-07-25). One shared shape
    rule, in one place, is what keeps the two layers from drifting again.
    """
    return is_regular_file(enrollment_evidence_path(root))


def enrollment_evidence_squatted(root: Path) -> bool:
    """Something occupies the evidence path but is NOT evidence by the shape rule.

    A directory, or a symlink to a device, satisfies neither ``[ -f ]`` nor
    :func:`is_regular_file`, so both layers correctly refuse to read it as proof
    of enrollment — treating it as proof would hand anyone who can create a
    directory the power to arm a project permanently (that is finding F-E from
    the other direction). But "not evidence" is not "nothing", and it also cannot
    be self-healed, since the write path refuses to create over an existing
    object. Reported so the operator sees the squatted path instead of a silent
    downgrade to ``never_enrolled``.
    """
    path = enrollment_evidence_path(root)
    try:
        path.lstat()
    except OSError:
        return False
    return not is_regular_file(path)


def record_enrollment_evidence(root: Path) -> bool:
    """Leave durable, git-free evidence that this project HAS been enrolled.

    Written once at first enrollment and never rewritten, so re-enrolling costs
    no churn and the file's mere EXISTENCE is the whole signal. Best-effort: a
    read-only or full filesystem must never turn enrollment into a hard failure,
    and the git legs still answer whenever git can.

    ONLY call this where genuine enrollment has already been established. It is
    not a cache-fill: minting evidence arms every control point permanently, and
    the caller that minted it from an unvalidated marker turned one planted file
    into an un-armable block on a project nobody opted into (finding F-E).

    Write discipline: no-follow, root-confined, and O_EXCL — see
    :func:`trw_mcp.security.intent_contract.paths.write_text_confined` for what a
    plain ``write_text`` here was worth to an attacker (finding F-D).

    Returns True only when this call created the file.
    """
    if enrollment_evidence_present(root):
        return False
    try:
        write_text_confined(
            root,
            enrollment_evidence_path(root),
            "# PRD-SEC-013 intent-contract enrollment evidence.\n"
            "# The presence of this file is how the control points establish that this\n"
            "# project HAS been enrolled without asking git — see paths.py.\n"
            "# Removing it while the enrollment marker is also absent makes the controls\n"
            "# go inert; a COMMITTED removal is a C9 control-plane finding. To opt OUT\n"
            "# deliberately, run `python3 -m trw_mcp.security.intent_contract.enrollment\n"
            "# unenroll`, which removes this file and the marker together.\n"
            f"schema_version: {ENROLLMENT_SCHEMA_VERSION}\n"
            f"first_enrolled_at: '{datetime.now(timezone.utc).isoformat()}'\n",
            exclusive=True,
        )
    except OSError:
        return False
    return True


def clear_enrollment(root: Path) -> tuple[str, ...]:
    """The explicit opt-out: remove the marker AND its durable evidence together.

    Until this existed, the only documented un-arm path was hand-deleting a file
    whose own first line said DO NOT DELETE — an instruction the operator has to
    disobey to follow, which is not a usable control. Removal is confined to
    *root* and unlinks a symlink rather than its target.

    Returns the repo-relative paths actually removed.
    """
    return tuple(rel for rel in (ENROLLMENT_PATH, ENROLLMENT_EVIDENCE_PATH) if unlink_confined(root, root / rel))


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return _digest(read_bytes_nofollow(path))
    except OSError:
        return _ABSENT


def compute_digests(root: Path, contract_rel_path: str = DEFAULT_CONTRACT_PATH) -> dict[str, str]:
    """Recompute the three protected digests for *root*.

    ``expected_hook_digest`` covers the two intent hooks AND the shared library
    they source. The library registers no hook of its own, which is exactly why
    it was the blind spot: ``chmod 000 .claude/hooks/lib-trw.sh`` changed no
    tracked byte, produced a clean ``git status``, left enrollment reading
    ``current``, and still made both hooks exit 0 (probe finding N6,
    2026-07-24). An unreadable file digests as ``<absent>``, so a mode change is
    now as visible here as a content change.
    """
    hook_parts = {
        name: _file_digest(root / _INSTALLED_HOOK_DIR / name) for name in (*INTENT_HOOK_FILES, *HOOK_SUPPORT_FILES)
    }
    pre_commit = _pre_commit_intent_hooks(lambda path: _read_optional(root / path))
    return {
        "expected_contract_digest": _file_digest(root / contract_rel_path),
        "expected_hook_digest": _digest(json.dumps(hook_parts, sort_keys=True).encode("utf-8")),
        "expected_pre_commit_config_digest": _digest(json.dumps(pre_commit, sort_keys=True).encode("utf-8")),
    }


def _read_optional(path: Path) -> bytes | None:
    try:
        return read_bytes_nofollow(path)
    except OSError:
        return None


def _load_marker(path: Path) -> dict[str, object] | None:
    raw = _read_optional(path)
    if raw is None:
        return None
    try:
        document = YAML(typ="safe").load(io.StringIO(raw.decode("utf-8", errors="strict")))
    except (UnicodeDecodeError, YAMLError):
        return {}
    return document if isinstance(document, dict) else {}


def marker_is_tracked(root: Path) -> bool:
    """True when a DURABLE second signal says this project was enrolled.

    A plain ``rm .trw/contracts/enrollment.yaml`` in the WORKING TREE would
    otherwise read as "never enrolled" and silently disarm every control point.

    Two independent signals, checked in that order:

    1. :data:`ENROLLMENT_EVIDENCE_PATH` on disk. A plain filesystem test, so it
       answers under a shadowed ``git`` binary AND under an ordinary broken repo.
    2. git history, anchored to HEAD rather than the index: an index-only check
       was itself bypassable with ``git rm -f .trw/contracts/enrollment.yaml`` —
       one uncommitted command that clears the working-tree copy and the index
       entry together (probe finding N3). ``.git/index`` deletion has the same
       shape. A deletion that IS committed is caught by C9's locator protection.

    When neither signal is available AND git cannot answer, the verdict is NOT
    enrolled. That direction is deliberate and it is a REVERSAL of the N10 ruling
    (2026-07-24), which stayed armed on any unanswerable git:

    * The N10 premise was that ``.git`` present + ``rev-parse --git-dir`` failing
      is pathological. It is not. A ``.git`` FILE pointing at a pruned worktree
      or a deinit'd submodule, a partial clone, a syntax error in the user's
      global ``~/.gitconfig``, and dubious-ownership under a Docker/CI bind mount
      or a sudo-created clone all produce it with no attacker present — and the
      two edit-time hooks are registered unconditionally on every installed
      project, so that blocked every write for users who never opted in.
    * What N10 was actually defending is preserved by signal 1: an enrolled
      project always has the evidence file, so shadowing git no longer converts
      "enrolled, marker deleted" into "never enrolled".

    RESIDUAL, stated plainly: deleting the evidence file AND the marker AND
    keeping git from answering is a disarm. That is three acts instead of two,
    the evidence file is git-tracked (so a committed removal is a C9 finding) and
    lives outside ``.trw/contracts`` (so ``rm -rf .trw/contracts`` misses it).
    The risk taken in the other direction — blocking every write for every
    never-enrolled user whose gitconfig has a typo — is both larger and certain.
    """
    if enrollment_evidence_present(root):
        return True
    return path_in_history(root, ENROLLMENT_PATH, unanswerable_means_present=False)


def check_enrollment_status(root: Path, contract_rel_path: str = DEFAULT_CONTRACT_PATH) -> EnrollmentStatus:
    """Classify enrollment for every FR01/FR02/FR05/FR07 control point.

    Not purely a read: a marker that VERIFIES also (re)creates the durable
    evidence file if it is missing. That is the upgrade path for projects
    enrolled before the file existed, and it is why ``enrollment status`` can
    create one byte of state.

    "Verifies" is load-bearing and was the F-E bug. The self-heal used to sit
    directly under the schema-version check, before any digest was compared, so
    the whole bar for minting permanent evidence was ``schema_version: 1``. One
    planted ``.trw/contracts/enrollment.yaml`` in a project that had never
    enrolled, one ``enrollment status``, then delete the plant: the project reads
    ``stale`` forever, every control point fails closed, and there is no trace of
    what did it. That is precisely the harm the previous round's fix existed to
    prevent, re-created by the fix itself. Minting now requires every digest in
    the marker to match the tree — which is exactly the state a real enrollment
    leaves behind, so the upgrade path is unchanged for real users.
    """
    marker = _load_marker(enrollment_path(root))
    if marker is None:
        return "stale" if marker_is_tracked(root) else "never_enrolled"
    version = marker.get("schema_version")
    if not isinstance(version, int) or version != ENROLLMENT_SCHEMA_VERSION:
        return "stale"
    recomputed = compute_digests(root, contract_rel_path)
    for key, value in recomputed.items():
        if str(marker.get(key, "")) != value:
            return "stale"
    record_enrollment_evidence(root)
    return "current"


def stale_enrollment_warning(root: Path, contract_rel_path: str = DEFAULT_CONTRACT_PATH) -> str:
    """The unsuppressible health warning text — NEVER gated by any override.

    Two DIFFERENT signals can put an absent marker into the fail-closed branch,
    and they know different things. Saying "this project is enrolled, restore the
    marker with git checkout" on the strength of a signal that never mentioned
    git is the same defect as the decisions this control keeps closing — a
    conclusion stated with more confidence than the evidence supports.
    """
    if _load_marker(enrollment_path(root)) is None:
        if enrollment_evidence_present(root):
            return (
                "INTENT-CONTRACT HEALTH WARNING (unsuppressible): the enrollment marker "
                f"{ENROLLMENT_PATH} is MISSING from the working tree, but {ENROLLMENT_EVIDENCE_PATH} "
                "records that this project HAS been enrolled. Every control point fails closed "
                "until the marker is restored. That evidence file is a plain filesystem record, "
                "so this says nothing about whether git still has the marker. ACTION: re-run "
                "`python3 -m trw_mcp.security.intent_contract.enrollment enroll`, or, if this "
                "project is deliberately opting OUT, run `... enrollment unenroll` and commit "
                "both removals as one reviewed change."
            )
        return (
            "INTENT-CONTRACT HEALTH WARNING (unsuppressible): the enrollment marker "
            f"{ENROLLMENT_PATH} is MISSING from the working tree but is still tracked by git. "
            "This project is enrolled, so every control point fails closed until the marker "
            "is restored (git checkout) or the removal is committed as a signed, reviewed change."
        )
    marker = _load_marker(enrollment_path(root)) or {}
    recomputed = compute_digests(root, contract_rel_path)
    drifted = [key for key, value in recomputed.items() if str(marker.get(key, "")) != value]
    return (
        "INTENT-CONTRACT HEALTH WARNING (unsuppressible): this project is ENROLLED but its "
        f"protection is stale — drifted: {', '.join(drifted) or 'schema_version'}. "
        "A contract, hook, or pre-commit registration changed or is missing since enrollment. "
        "Re-run enrollment after verifying the change was intentional."
    )


def _write_marker(root: Path, fields: dict[str, object]) -> None:
    """Serialize a marker. ``schema_version`` stays first and unquoted.

    Goes through the same no-follow, root-confined write as the evidence file:
    the marker is an artifact of this control too, and a symlink swapped in here
    would have the security tool write attacker-chosen content to an attacker-
    chosen path (finding F-D, same shape one file over). Unlike the evidence
    file this one legitimately REPLACES itself on re-enrollment, so it truncates
    rather than demanding exclusive creation.
    """
    version = fields.get("schema_version")
    lines = [f"schema_version: {version if isinstance(version, int) else ENROLLMENT_SCHEMA_VERSION}"]
    lines += [f"{key}: '{value}'" for key, value in sorted(fields.items()) if key != "schema_version"]
    write_text_confined(root, enrollment_path(root), "\n".join(lines) + "\n")


def missing_intent_hooks(root: Path) -> tuple[str, ...]:
    """Repo-relative paths of the FR05/FR07 hook scripts that are absent or unreadable.

    Only the two INTENT hooks count. ``lib-trw.sh`` is deliberately excluded: when
    the shared library is missing, an enrolled hook still runs and fails CLOSED
    (probe finding N6), so protection is live. Missing hook SCRIPTS are the case
    where nothing can fire at all.
    """
    return tuple(
        f"{_INSTALLED_HOOK_DIR}/{name}"
        for name in INTENT_HOOK_FILES
        if _file_digest(root / _INSTALLED_HOOK_DIR / name) == _ABSENT
    )


def _vacuous_enrollment_refusal(root: Path, missing: tuple[str, ...]) -> str:
    return (
        "REFUSED (intent-contract enrollment): the marker was NOT written.\n"
        "  The intent-contract hook scripts are not installed, so a marker here would\n"
        "  attest to a configuration that can never fire — 'enrolled / current' while\n"
        "  nothing runs is exactly the delivered-but-not-wired failure this control\n"
        "  exists to catch.\n"
        # .absolute(), not .resolve(): an unambiguous path for the operator without
        # collapsing symlinks (which would rewrite the path they actually passed).
        f"  looked in: {(root / _INSTALLED_HOOK_DIR).absolute()}\n"
        "  missing:   " + ", ".join(missing) + "\n\n"
        "NOTE ON SCOPE: this checks the CLAUDE CODE hook directory only. TRW is\n"
        "  client-agnostic, but the digest set is not yet — on codex/opencode/cursor\n"
        "  these paths are absent by design and enrollment there needs the opt-out\n"
        "  below until the client-agnostic hook locator lands.\n\n"
        "ACTION: install the hooks (trw-mcp init-project / update-project) and enroll\n"
        "  again, or pass --allow-missing-hooks to pre-enroll deliberately."
    )


def write_enrollment(
    root: Path,
    contract_rel_path: str = DEFAULT_CONTRACT_PATH,
    *,
    allow_missing_hooks: bool = False,
) -> dict[str, str]:
    """Mint/refresh the WHOLE marker. Operator action — never called by a hook path.

    Raises :class:`EnrollmentError` when the intent hooks are not installed, because
    a marker minted over absent hooks digests them as ``<absent>`` and then reports
    ``current`` forever: a vacuous success that tells the operator protection is on
    when no control point is registered to run. *allow_missing_hooks* is the
    explicit pre-enrollment opt-out.
    """
    if not allow_missing_hooks:
        missing = missing_intent_hooks(root)
        if missing:
            raise EnrollmentError(_vacuous_enrollment_refusal(root, missing))
    digests = compute_digests(root, contract_rel_path)
    # BEFORE the marker: if the process dies between the two writes, an orphan
    # evidence file fails CLOSED (and says so), while an orphan marker would be
    # the pre-fix exposure again.
    record_enrollment_evidence(root)
    _write_marker(
        root,
        {
            "schema_version": ENROLLMENT_SCHEMA_VERSION,
            "enrolled_at": datetime.now(timezone.utc).isoformat(),
            "contract_path": contract_rel_path,
            **digests,
        },
    )
    return digests


def refresh_hook_digest(root: Path) -> bool:
    """Re-bless ONLY the hook half of an existing marker, after a VENDOR resync.

    The upgrade hazard this closes: ``expected_hook_digest`` covers the bundled
    hook scripts, so shipping a new hook makes every enrolled project's marker
    read ``stale`` — both control points then fail closed and block every edit,
    with the user having done nothing wrong. Distinguishing "the vendor shipped
    new hooks" from "someone tampered with our hooks" is the installer's job,
    because only the installer knows it just wrote those exact bytes itself.

    Deliberately narrow, in three ways — each one is what keeps this from being
    a self-service disarm:

    * it NEVER mints a marker OR the durable enrollment evidence, so a project
      that never enrolled stays inert. It used to mint the evidence, gated only
      on a marker with the right ``schema_version`` — and the comment right here
      claimed that "can never enrol a project that never opted in", which was
      false: one planted two-word file, and the INSTALLER's own call arms the
      project permanently (finding F-E, 2026-07-25). The upgrade path it was
      serving is still served, by :func:`check_enrollment_status`, which mints
      only once every digest verifies;
    * it NEVER touches ``expected_contract_digest`` or
      ``expected_pre_commit_config_digest``. The contract is the user-controlled,
      tamper-sensitive half: a contract edit must keep failing closed until an
      operator re-enrolls, which is their explicit acknowledgement of the new
      claim set;
    * it refuses a marker that is absent, unparsable, or of an unknown schema
      version, so it can never launder a corrupt marker into a valid one.

    Returns True only when a marker was actually rewritten.
    """
    path = enrollment_path(root)
    marker = _load_marker(path)
    if not marker or marker.get("schema_version") != ENROLLMENT_SCHEMA_VERSION:
        return False
    contract_rel_path = str(marker.get("contract_path", "") or DEFAULT_CONTRACT_PATH)
    fresh = compute_digests(root, contract_rel_path)["expected_hook_digest"]
    if str(marker.get("expected_hook_digest", "")) == fresh:
        return False
    updated = dict(marker)
    updated["expected_hook_digest"] = fresh
    updated["hook_digest_refreshed_at"] = datetime.now(timezone.utc).isoformat()
    _write_marker(root, updated)
    return True


def main(argv: list[str] | None = None) -> int:
    """``... enrollment enroll [--allow-missing-hooks]|unenroll|refresh-hooks|status [--root PATH]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    root_arg, args = parse_root_arg(args)
    allow_missing_hooks = "--allow-missing-hooks" in args
    args = [item for item in args if item != "--allow-missing-hooks"]
    stray = stray_options(args)
    if stray:
        # `enroll --root` (no value) used to enroll under the DEFAULT root and
        # exit 0 — a marker minted in a project the operator never named.
        print(f"unrecognized argument(s): {' '.join(stray)}", file=sys.stderr)
        args = []
    command = args[0] if args else ""
    root = root_arg or repo_root()
    if command == "enroll":
        try:
            write_enrollment(root, allow_missing_hooks=allow_missing_hooks)
        except EnrollmentError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"enrolled: {enrollment_path(root)}")
        if allow_missing_hooks and missing_intent_hooks(root):
            print("WARNING: enrolled with --allow-missing-hooks — no intent hook is installed to run.")
        return 0
    if command == "unenroll":
        # The counterpart of `enroll`. Without it the only documented opt-out was
        # hand-deleting a file that tells the reader not to delete it.
        removed = clear_enrollment(root)
        print(f"unenrolled: removed {', '.join(removed)}" if removed else "no change (this project is not enrolled)")
        return 0
    if command == "refresh-hooks":
        # The manual counterpart of the installer's own call — recovery for a
        # project whose hooks were updated out of band. Hook half only.
        changed = refresh_hook_digest(root)
        print("hook digest refreshed" if changed else "no change (absent marker, or already current)")
        return 0
    if command == "status":
        status = check_enrollment_status(root)
        missing = missing_intent_hooks(root)
        if enrollment_evidence_squatted(root):
            print(
                f"WARNING: {ENROLLMENT_EVIDENCE_PATH} exists but is not a regular file, so neither the "
                "hooks nor this tool count it as enrollment evidence, and it cannot be self-healed "
                "while it is in the way. Remove it, then re-run enroll.",
                file=sys.stderr,
            )
        # `current` alone was misread as "protection is on". Say which it is.
        if status == "current" and missing:
            print("current (NO HOOKS INSTALLED — nothing is registered to run)")
        else:
            print(status)
        if missing and status != "never_enrolled":
            print(f"missing intent hooks under {root / _INSTALLED_HOOK_DIR}: {', '.join(missing)}", file=sys.stderr)
        if status == "stale":
            print(stale_enrollment_warning(root), file=sys.stderr)
            return 1
        return 0
    print(
        "usage: python3 -m trw_mcp.security.intent_contract.enrollment "
        "enroll [--allow-missing-hooks]|unenroll|refresh-hooks|status [--root PATH]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
