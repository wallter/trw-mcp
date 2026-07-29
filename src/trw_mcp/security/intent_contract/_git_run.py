"""Minimal, hardened git subprocess wrapper (PRD-SEC-013 R9).

Every git invocation in this package goes through here so that
``GIT_NO_REPLACE_OBJECTS=1`` is always set — otherwise ``refs/replace`` can
substitute the very commit/blob graph the checker is inspecting — and so the
child process never inherits the full environment (repo Subprocess Env Hygiene
convention). ``shell=False`` always; argv is never built from contract content.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

__all__ = [
    "BlobUnreadable",
    "GitCommandError",
    "git_can_answer",
    "path_in_history",
    "read_blob",
    "run_git",
    "run_git_bytes",
]

#: Environment variables a child git process may see. Signature verification
#: needs HOME/GNUPGHOME (keyring + allowedSignersFile), so they stay in.
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "GNUPGHOME",
    "XDG_CONFIG_HOME",
    "SSH_AUTH_SOCK",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_NOSYSTEM",
    "TMPDIR",
    "LANG",
    "LC_ALL",
)

_DEFAULT_TIMEOUT = 30.0


class BlobUnreadable(RuntimeError):
    """A blob read could not be ANSWERED — as distinct from "no such path there".

    :func:`read_blob` returned ``None`` for both, and every caller reads ``None``
    as "there genuinely is no contract at this rev". That erasure survived the
    round-3 sweep because the sweep stopped at the detectors: ``_contract_at``
    raises ``_Unanswerable`` when a blob is present-but-unparsable, and never
    when the FETCH failed. A stub that fails only ``git rev-parse <rev>:<path>``
    and execs the real git for everything else took both pre-commit and pre-push
    from ``1 BLOCKED`` to a silent ``0``, with enrollment still reading
    ``current`` (finding F-B, 2026-07-25).

    Raised by the reader rather than inferred at each call site, because there
    were five call sites and each one had to get the same inference right.
    """


class GitCommandError(RuntimeError):
    """A git invocation failed, timed out, or could not be launched."""

    def __init__(self, args: tuple[str, ...], returncode: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(args)} failed ({returncode})")
        self.args_run = args
        self.returncode = returncode
        self.stderr = stderr


def _child_env() -> dict[str, str]:
    env = {name: os.environ[name] for name in _ENV_ALLOWLIST if name in os.environ}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def run_git_bytes(repo_root: Path, *args: str, timeout: float = _DEFAULT_TIMEOUT) -> bytes:
    """Run ``git *args`` in *repo_root*, returning raw stdout. Raises on failure."""
    try:
        # Fixed argv, shell=False, stripped env: argv is never built from contract content.
        completed = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=str(repo_root),
            capture_output=True,
            shell=False,
            timeout=timeout,
            env=_child_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitCommandError(args, -1, type(exc).__name__) from exc
    if completed.returncode != 0:
        raise GitCommandError(args, completed.returncode, completed.stderr.decode("utf-8", errors="replace"))
    return completed.stdout


def run_git(repo_root: Path, *args: str, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Run ``git *args`` in *repo_root*, returning decoded stdout."""
    return run_git_bytes(repo_root, *args, timeout=timeout).decode("utf-8", errors="replace")


def path_in_history(repo_root: Path, rel_path: str, *, unanswerable_means_present: bool = True) -> bool:
    """True when git DURABLY records *rel_path* — checking HEAD first, then the index.

    HEAD is the load-bearing half. The index is writable by the same attacker who
    deletes the file: ``git rm -f <path>`` removes the working-tree copy AND its
    index entry in one uncommitted command, so an index-only signal read as "this
    file never existed" and disarmed every control point anchored to it (probe
    finding N3, 2026-07-24 — one command, no commit, fully disarmed). HEAD cannot
    change without producing a commit, and a COMMITTED removal is precisely what
    the C9 control-plane check exists to catch.

    The index is still consulted, as a strictly-additional signal, so a marker
    that was staged but never committed also counts as durable.

    A path that was never committed AND never staged yields False — that is the
    inert-by-default property, and it must not regress. But False is only
    returned when git could ANSWER: see :func:`git_can_answer`. Two failing
    queries used to be indistinguishable from two negative answers, so shadowing
    ``git`` with a two-line stub in any writable PATH directory turned every
    caller's "no durable record" branch on at will.

    *unanswerable_means_present* is that fail-closed default. Pass ``False`` ONLY
    where the caller holds an independent, git-free signal for the same question
    and has therefore already ruled out the shadowed-git case — see
    :func:`trw_mcp.security.intent_contract.enrollment.marker_is_tracked`, whose
    docstring records why a silent git alone must not arm a never-enrolled
    project.
    """
    for args in (("cat-file", "-e", f"HEAD:{rel_path}"), ("ls-files", "--error-unmatch", rel_path)):
        try:
            run_git(repo_root, *args)
        except GitCommandError:
            continue
        return True
    # Both legs failed. Absence is only established if git was able to speak.
    return unanswerable_means_present and not git_can_answer(repo_root)


def git_can_answer(repo_root: Path) -> bool:
    """Can git give a DEFINITIVE answer about *repo_root*'s history?

    Three states, and only the middle one is evidence of anything:

    * no ``.git`` at all -> True. There is genuinely no repository to consult, so
      "this path is not in history" is a sound conclusion and callers stay inert.
      The test is a plain filesystem check, so it cannot itself be sabotaged by
      shadowing the git binary.
    * ``.git`` present and ``rev-parse --git-dir`` succeeds -> True. Queries that
      returned non-zero really did mean "not found".
    * ``.git`` present but the sentinel fails -> False. Something is broken or
      shadowed; callers must NOT read a failed query as a negative answer.

    ``rev-parse --git-dir`` is the sentinel because it cannot fail inside a real
    repository and needs no network, no objects and no refs.
    """
    if not (repo_root / ".git").exists():
        return True
    try:
        run_git(repo_root, "rev-parse", "--git-dir")
    except GitCommandError:
        return False
    return True


def read_blob(repo_root: Path, rev: str | None, path: str) -> bytes | None:
    """Read *path* at *rev* BY OID (``rev-parse`` then ``cat-file blob``).

    Resolving the OID first pins the read to exact addressed content instead of
    re-resolving ref+path twice. ``rev=None`` means the empty tree (root commit
    baseline) and always yields ``None``.

    ``None`` means one thing only: *that tree does not contain that path*. When
    the read could not be ANSWERED this raises :class:`BlobUnreadable` — see that
    class for the bypass the conflation cost.
    """
    if rev is None:
        return None
    try:
        oid = run_git(repo_root, "rev-parse", f"{rev}:{path}").strip()
    except GitCommandError as exc:
        # "not in that tree" and "git could not answer" are the SAME exit 128
        # here, so separate them by CONSTRUCTION rather than by parsing git's
        # error text (which is localized, version-dependent, and attacker-
        # influenced): the identical query shape against the rev's own root tree
        # must succeed for any rev git can resolve. `<rev>:` is deliberately the
        # same `<rev>:<path>` form as the failed query — a control that used a
        # DIFFERENT form (`<rev>^{tree}`) would be answered by the very stub it
        # is supposed to detect.
        if not _rev_is_resolvable(repo_root, rev):
            raise BlobUnreadable(f"git could not resolve {rev} ({exc.returncode})") from exc
        return None
    if not oid:
        raise BlobUnreadable(f"`git rev-parse {rev}:{path}` succeeded but named no object")
    try:
        return run_git_bytes(repo_root, "cat-file", "blob", oid)
    except GitCommandError as exc:
        # The OID resolved, so the content is addressed and SHOULD be readable.
        raise BlobUnreadable(f"blob {oid} ({rev}:{path}) could not be read ({exc.returncode})") from exc


def _rev_is_resolvable(repo_root: Path, rev: str) -> bool:
    """Positive control for :func:`read_blob`: can git answer about *rev* at all?

    Deliberately NOT memoized. The answer depends on ``PATH`` (a shadowed ``git``
    stub is the whole threat here) and on the working tree, both of which can
    change inside one process — a cache of "yes" is exactly the stale permissive
    answer this control exists to refuse.
    """
    try:
        run_git(repo_root, "rev-parse", f"{rev}:")
    except GitCommandError:
        return False
    return True
