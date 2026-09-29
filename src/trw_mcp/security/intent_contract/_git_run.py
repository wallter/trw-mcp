"""Minimal, hardened git subprocess wrapper (PRD-SEC-013 R9).

Every git invocation in this package goes through here so that
``GIT_NO_REPLACE_OBJECTS=1`` is always set — otherwise ``refs/replace`` can
substitute the very commit/blob graph the checker is inspecting — and so the
child process never inherits the full environment (repo Subprocess Env Hygiene
convention). ``shell=False`` always; argv is never built from contract content.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO

__all__ = [
    "BlobUnreadable",
    "GitBlobBatch",
    "GitCommandError",
    "blob_batch",
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


#: Prepended to EVERY per-spawn git argv (this is the one place argv is built). On macOS, with
#: ``core.precomposeunicode=true`` (the Apple git default), git NFC-normalises non-ASCII names it receives on
#: argv, so an NFD-stored tree entry could be resolved or listed under a different spelling than the exact tree
#: bytes: ``ls-tree <rev>:<NFD dir>`` failed, and an absent leaf under it read as unreadable while the batch
#: process (which takes its specs on stdin, unnormalised) said absent. Forcing it off makes argv exact and both
#: readers compare raw tree bytes. Per ``git help config``, the option "is only used by the Mac OS
#: implementation of Git", so on Linux it is a no-op (inference from that docs text; not run on Linux here).
_GIT_EXACT_ARGV = ("-c", "core.precomposeunicode=false")


def run_git_bytes(repo_root: Path, *args: str, timeout: float = _DEFAULT_TIMEOUT) -> bytes:
    """Run ``git *args`` in *repo_root*, returning raw stdout. Raises on failure."""
    try:
        # Fixed argv, shell=False, stripped env: argv is never built from contract content.
        completed = subprocess.run(  # noqa: S603
            ["git", *_GIT_EXACT_ARGV, *args],  # noqa: S607
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
    batch = _ACTIVE_BATCH.get()
    if batch is not None and batch.repo_root == repo_root and _batchable(rev, path):
        outcome = batch.read(rev, path)
        if not isinstance(outcome, _Fallback):
            return outcome
    return _read_blob_per_spawn(repo_root, rev, path)


def _batchable(rev: str, path: str) -> bool:
    """Only specs the ``--batch`` line protocol carries verbatim go through it.

    A newline, carriage return or NUL would split or truncate the request line,
    and a leading ``-`` is not a revision; those reads use the per-spawn path.
    """
    return not (any(ch in rev or ch in path for ch in ("\n", "\r", "\0")) or rev.startswith("-"))


def _read_blob_per_spawn(repo_root: Path, rev: str, path: str) -> bytes | None:
    """The original two-spawn read (``rev-parse`` then ``cat-file blob``)."""
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
        # `rev-parse` failing on the FULL path is also what a deleted intermediate tree object looks like, so
        # "absent" is only concluded after every ancestor entry is seen in a readable parent tree.
        _confirm_absent(rev, path, lambda prefix: _ls_tree_per_spawn(repo_root, rev, prefix))
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


_HEADER_RE = re.compile(rb"^[0-9a-f]{40,64} (\w+) (\d+)$")


class _Fallback:
    """Marker: batch mode could not answer; use the per-spawn path."""


_FALLBACK = _Fallback()
_ACTIVE_BATCH: contextvars.ContextVar[GitBlobBatch | None] = contextvars.ContextVar("intent_blob_batch", default=None)


class GitBlobBatch:
    """One long-lived ``git cat-file --batch`` answering many ``<rev>:<path>`` reads.

    Spawning git twice per blob dominated the history walk (~30 ms per spawn).
    This keeps the same hardened env and cwd as :func:`run_git_bytes` and answers
    with the same values and errors as the per-spawn path. It never invents a
    result: if the batch process dies, times out, or speaks something other than
    the exact ``<oid> <type> <size>\\n<content>\\n`` / ``<spec> missing\\n``
    protocol, the read (and every later one) is served by the per-spawn path, which
    fails closed on its own terms. Use as a context manager; the child is closed on
    every exit path.
    """

    def __init__(self, repo_root: Path, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.repo_root = repo_root
        self._timeout = timeout
        self._proc: subprocess.Popen[bytes] | None = None
        self._broken = False
        # One request/response at a time: the contextvar reaches worker threads and tasks.
        self._lock = threading.Lock()

    def __enter__(self) -> GitBlobBatch:
        try:
            # Fixed argv, shell=False, stripped env: same hygiene as run_git_bytes.
            self._proc = subprocess.Popen(
                ["git", "cat-file", "--batch"],  # noqa: S607
                cwd=str(self.repo_root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                env=_child_env(),
            )
        except OSError:
            self._broken = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    with contextlib.suppress(OSError, ValueError):
                        stream.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        finally:
            self._broken = True

    def _query(self, spec: str) -> tuple[str, bytes, str] | None:
        """``(kind, content, oid)`` for *spec*; kind is ``"missing"`` or an object type. ``None`` = protocol failure."""
        proc = self._proc
        if self._broken or proc is None or proc.stdin is None or proc.stdout is None:
            return None
        watchdog = threading.Timer(self._timeout, proc.kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            proc.stdin.write(spec.encode("utf-8", errors="surrogateescape") + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline()
            if not header.endswith(b"\n"):
                return None
            header = header[:-1]
            if header.endswith(b" missing"):
                return ("missing", b"", "")
            match = _HEADER_RE.match(header)
            if match is None:
                return None
            size = int(match.group(2))
            content = _read_exact(proc.stdout, size + 1)
            if content is None or not content.endswith(b"\n"):
                return None
            return (match.group(1).decode("ascii"), content[:-1], header.split(b" ", 1)[0].decode("ascii"))
        except (
            OSError,
            ValueError,
        ):  # trw-fail-silent-allow: None = "batch cannot answer"; read() then uses the fail-closed per-spawn path
            self._broken = True
            return None
        except BaseException:
            # An interrupted exchange may leave a response in the pipe; never read it as the next answer.
            self._broken = True
            raise
        finally:
            watchdog.cancel()

    def read(self, rev: str, path: str) -> bytes | None | _Fallback:
        """Same contract as :func:`read_blob`, or ``_FALLBACK`` when batch mode cannot answer."""
        with self._lock:
            return self._read_locked(rev, path)

    def _read_locked(self, rev: str, path: str) -> bytes | None | _Fallback:
        first = self._query(f"{rev}:{path}")
        if first is None:
            self._broken = True
            return _FALLBACK
        kind, content, _oid = first
        if kind == "missing":
            return self._classify_missing(rev, path)
        if kind != "blob":
            raise BlobUnreadable(f"{rev}:{path} is a {kind}, not a blob")
        return content

    def _classify_missing(self, rev: str, path: str) -> None | _Fallback:
        """``--batch`` prints ``missing`` for an absent path AND for a tree entry whose object cannot be read
        (a gitlink, a blob or an intermediate tree deleted from the object store). ``rev-parse`` tells them apart
        because it only needs the ENTRY: present-but-unreadable must raise, never read as "no contract". Same
        ancestor walk as the per-spawn path (:func:`_confirm_absent`), fed by ``<rev>:<dir>`` listings from this
        process; anything not plainly normalised goes per-spawn.
        """

        def listing(prefix: str) -> dict[bytes, bytes] | None:
            answer = self._query(f"{rev}:{prefix}")
            if answer is None:
                self._broken = True
                raise _BatchCannotAnswer
            kind, tree, oid = answer
            entries = _tree_entries(tree, len(oid) // 2) if kind == "tree" else None
            if kind == "tree" and entries is None:
                self._broken = True
                raise _BatchCannotAnswer
            return entries

        try:
            _confirm_absent(rev, path, listing)
        except _BatchCannotAnswer:
            return _FALLBACK
        return None


class _BatchCannotAnswer(Exception):
    """Internal: the batch process failed mid-classification; the per-spawn path answers instead."""


_TREE_MODE = b"40000"


def _confirm_absent(rev: str, path: str, listing: Callable[[str], dict[bytes, bytes] | None]) -> None:
    """Return only when *path* is genuinely absent at *rev*; raise :class:`BlobUnreadable` otherwise.

    A failed path lookup is ambiguous: the path may not exist, or an ancestor tree object it passes through may
    have been deleted from the object store (which read as "no contract" and let an attacker erase a contract
    from every history check). Walk the ancestors from the root, one *listing* per directory: an entry that IS
    in its readable parent tree but whose object cannot be listed is unreadable, never absent. *listing*
    returns ``{name: mode}`` for a readable tree and ``None`` when the object cannot be read. A component that
    is missing from a readable tree, or is a file/gitlink used as a directory, means genuinely absent. A path that
    is not plainly normalised is left as "absent", as before.
    """
    parts = path.split("/")
    if not path or any(part in ("", ".", "..") for part in parts):
        return
    prefix = ""
    entries = listing(prefix)
    if entries is None:
        raise BlobUnreadable(f"git could not resolve {rev}")
    for index, part in enumerate(parts):
        mode = entries.get(part.encode("utf-8", errors="surrogateescape"))
        if mode is None:
            return
        if index == len(parts) - 1:
            raise BlobUnreadable(f"{rev}:{path} names an object that could not be read")
        if mode.lstrip(b"0") != _TREE_MODE:
            return
        prefix = f"{prefix}/{part}" if prefix else part
        entries = listing(prefix)
        if entries is None:
            raise BlobUnreadable(f"{rev}:{prefix} is a tree entry whose object could not be read")


def _ls_tree_per_spawn(repo_root: Path, rev: str, prefix: str) -> dict[bytes, bytes] | None:
    """``{name: mode}`` for the tree at ``<rev>:<prefix>``; ``None`` when git cannot list it."""
    try:
        raw = run_git_bytes(repo_root, "ls-tree", "-z", f"{rev}:{prefix}")
    except GitCommandError:  # trw-fail-silent-allow: None = "cannot list"; _confirm_absent raises BlobUnreadable on it
        return None
    entries: dict[bytes, bytes] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, tab, name = record.partition(b"\t")
        fields = meta.split(b" ")
        if not tab or len(fields) != 3:
            return None
        entries[name] = fields[0]
    return entries


def _tree_entries(tree: bytes, hash_len: int) -> dict[bytes, bytes] | None:
    """``{name: mode}`` of a raw tree object (``<mode> <name>\\0<hash>`` repeated); ``None`` if malformed."""
    entries: dict[bytes, bytes] = {}
    pos = 0
    while pos < len(tree):
        space = tree.find(b" ", pos)
        nul = tree.find(b"\0", space + 1) if space >= 0 else -1
        if space < 0 or nul < 0 or nul + 1 + hash_len > len(tree):
            return None
        entries[tree[space + 1 : nul]] = tree[pos:space]
        pos = nul + 1 + hash_len
    return entries


def _read_exact(stream: IO[bytes], count: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@contextlib.contextmanager
def blob_batch(repo_root: Path) -> Iterator[GitBlobBatch]:
    """Serve :func:`read_blob` calls for *repo_root* from one ``git cat-file --batch``.

    Scoped to the calling context; nested use reuses nothing and restores the
    outer batch on exit. The child process is closed on every exit path.
    """
    with GitBlobBatch(repo_root) as batch:
        token = _ACTIVE_BATCH.set(batch)
        try:
            yield batch
        finally:
            _ACTIVE_BATCH.reset(token)
