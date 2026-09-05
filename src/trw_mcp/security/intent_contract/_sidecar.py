"""PRD-CORE-254-FR01: the glob sidecar the two edit-time hooks read before Python.

Both hooks used to spawn a fresh interpreter on EVERY ``Write|Edit|MultiEdit``
just to learn that the edited path is anchored by no claim — 0.49s pre-write plus
0.50s post-edit, measured 2026-09-03, against a 1s pre-write budget that a loaded
box pushes past and then fails closed on. The anchor set only changes when an
operator edits the contract, so this module pre-renders it, at enrol/refresh time,
into a file POSIX ``sh`` can read with builtins alone.

WHAT THE SHELL IS ALLOWED TO CONCLUDE FROM IT, and no more: "no active,
machine-checkable, ``blocking_hook`` claim anchors this path, and nothing the
enrollment marker attests to has changed since this file was written". That is a
NEGATIVE-only optimization — it can turn a would-run-Python into ``exit 0``, and
it can never block, never allow a matching path, and never widen what the claim
set covers. Every other outcome falls through to the existing fail-closed Python
entry point.

Three properties make that conclusion sound, and each is enforced on BOTH sides:

* the rendered claim set is :func:`~trw_mcp.security.intent_contract._anchors.eligible_claims`
  — the same filter ``enforceable_claims`` applies, called from the same function,
  so the sidecar can never be more permissive than the Python matcher;
* the trailing ``sha256:`` line carries the marker's ``expected_contract_digest``,
  so a sidecar left over from a DIFFERENT contract does not match the live marker
  and the shell refuses it;
* every artifact the marker's three digests cover is listed as a ``g1``/``g0``
  freshness line, and the shell defers as soon as any of them is NEWER than this
  file (or has appeared, or gone). This is what keeps the fast path from outliving the
  enrollment it was derived from: an edited contract (a NEW anchor the sidecar
  has never heard of), a resynced hook, or a rewritten pre-commit registration
  each make the marker ``stale`` — which Python fails CLOSED on — and each also
  advances an mtime past the sidecar's, so the shell defers instead of
  self-approving a write Python would have blocked.

RESIDUAL, stated plainly rather than implied away, because the PRD's FR05 claims
more than this mechanism can deliver. The trailing digest is the CONTRACT's, so
it is copied into the sidecar rather than derived from the sidecar's own content:
an actor who can write this file can copy that line verbatim, empty the pattern
set, and hand the shell a "no claim anchors this path" answer for a path a claim
does anchor. No shell-readable cache can be proof against that — the shell has no
key the actor lacks, and the same actor can rewrite
``.claude/hooks/lib-intent-guard.sh``, which is sourced into the deciding shell
and is therefore already inside its trusted computing base (see that file's TRUST
BOUNDARY note). What is done instead is to keep this artifact no weaker than the
boundary that already exists:

* it is a TRACKED file, deliberately not gitignored, so a forged copy is visible
  to ``git status``/``git diff`` exactly as a tampered hook is. Hiding a
  control-plane artifact from git is its own finding (probe finding N7, where
  ``*.jsonl`` hid the override ledger from every git-side check);
* enrollment writes the sidecar BEFORE stamping the marker, and the hooks refuse
  a sidecar that is newer than the marker, so a lone rewrite is out of order and
  defers. Two filesystem operations instead of one — a speed bump, named as one;
* the marker's digests, the pre-commit/pre-push control-plane checks and C9 are
  unchanged and remain the durable answer.

The mtime freshness proof is likewise defeated by an actor who can backdate an
mtime, and it is second-granular under busybox. Both bound the same actor.

FILE FORMAT (line-oriented, ASCII, one meaning per prefix; anything else makes
the shell defer):

    # <comment>            ignored
    p <pattern>            POSIX `case` pattern for one rendered anchor
    g1 <repo-rel path>     guarded artifact PRESENT at write time
    g0 <repo-rel path>     guarded artifact ABSENT at write time
    sha256:<hex>           the marker's expected_contract_digest; LAST line

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.security.intent_contract._anchors import eligible_claims, is_glob_anchor
from trw_mcp.security.intent_contract._control_plane import (
    HOOK_SUPPORT_FILES,
    INTENT_HOOK_FILES,
)
from trw_mcp.security.intent_contract._models import Contract
from trw_mcp.security.intent_contract.loader import ContractLoadError, load_contract
from trw_mcp.security.intent_contract.paths import (
    PRE_COMMIT_CONFIG_PATH,
    is_regular_file,
    unlink_confined,
    write_text_confined,
)

__all__ = [
    "configured_glob_sidecar_path",
    "glob_sidecar_path",
    "guarded_artifacts",
    "render_glob_sidecar",
    "write_glob_sidecar",
]

#: Where an installed project's hook copies live — the same directory
#: ``compute_digests`` hashes for ``expected_hook_digest``.
_INSTALLED_HOOK_DIR = ".claude/hooks"

_HEADER = (
    "# GENERATED by trw_mcp.security.intent_contract.enrollment — do not edit.\n"
    "# Regenerate with `make refresh-enrollment`. Editing or deleting this file\n"
    "# only removes the hooks' latency shortcut; it never disarms enforcement.\n"
)


def configured_glob_sidecar_path() -> str:
    """The typed ``security.intent.glob_sidecar_path`` knob (repo-relative)."""
    from trw_mcp.models.config._loader import get_config

    return str(get_config().security.intent.glob_sidecar_path)


def glob_sidecar_path(root: Path) -> Path:
    return root / configured_glob_sidecar_path()


def guarded_artifacts(contract_rel_path: str) -> tuple[str, ...]:
    """Every artifact whose BYTES one of the marker's three digests covers.

    Kept in lockstep with ``compute_digests`` by
    ``test_intent_contract_enrollment.py::test_guarded_artifacts_cover_every_digested_file``:
    an artifact that is digested but not listed here is a drift the shell would
    never notice, which is exactly how a stale sidecar would outlive its marker.
    """
    hooks = tuple(f"{_INSTALLED_HOOK_DIR}/{name}" for name in (*INTENT_HOOK_FILES, *HOOK_SUPPORT_FILES))
    return (contract_rel_path, *hooks, PRE_COMMIT_CONFIG_PATH)


def _renderable(text: str) -> bool:
    """Only backslash-free printable ASCII survives to the shell, a byte matcher.

    The backslash is excluded because the two matchers disagree about it: POSIX
    ``case`` reads ``\\x`` as an escaped literal ``x`` while ``fnmatchcase`` reads
    both characters literally, so a rendered anchor containing one would make the
    shell match a different set of paths than Python does.

    An anchor carrying anything unrenderable is not emitted in a degraded form —
    the whole sidecar is withheld (see :func:`render_glob_sidecar`), so the fast
    path stays off and every write defers to Python. Under-approximating the
    pattern set is the one error this design cannot tolerate.
    """
    return bool(text) and all("\x20" <= char <= "\x7e" and char != "\\" for char in text)


def _patterns_for(anchor: str) -> tuple[str, ...] | None:
    """The ``case`` pattern(s) reproducing ``anchor_matches`` for one anchor.

    A glob anchor renders unchanged: ``fnmatchcase`` and POSIX ``case`` agree on
    ``*``/``?``/``[...]`` over a ``/``-containing string. A glob-free anchor
    renders as two patterns — the exact path and ``<path>/*`` — which is the
    directory-prefix branch ``anchor_matches`` takes for it.
    """
    normalized = anchor.rstrip("/")
    if not _renderable(normalized):
        return None
    if is_glob_anchor(normalized):
        return (normalized,)
    return (normalized, f"{normalized}/*")


def render_glob_sidecar(
    root: Path, contract: Contract | None, contract_rel_path: str, contract_digest: str
) -> str | None:
    """Render the sidecar text, or ``None`` when it cannot be rendered SAFELY.

    ``None`` is not an error path to recover from — it is the instruction to
    remove any existing sidecar so the hooks keep spawning Python.
    """
    if not _renderable(contract_digest):
        return None
    lines: list[str] = []
    claims = eligible_claims(contract) if contract is not None else ()
    for claim in claims:
        for anchor in claim.anchors:
            patterns = _patterns_for(anchor)
            if patterns is None:
                return None
            lines += [f"p {pattern}" for pattern in patterns]
    for artifact in guarded_artifacts(contract_rel_path):
        if not _renderable(artifact):
            return None
        lines.append(f"g1 {artifact}" if is_regular_file(root / artifact) else f"g0 {artifact}")
    lines.append(f"sha256:{contract_digest}")
    return _HEADER + "\n".join(lines) + "\n"


def write_glob_sidecar(root: Path, contract_rel_path: str, contract_digest: str) -> bool:
    """(Re)write the sidecar for *root*. True only when one was actually written.

    Called from ``write_enrollment`` and ``refresh_hook_digest`` — the two places
    that already establish the marker is real — so the sidecar and the marker
    cannot drift apart by being written from different code paths (the risk the
    PRD's own risk table names). An UNLOADABLE contract deliberately yields no
    sidecar: Python fails closed on it, so a shell shortcut there would be a
    would-BLOCK turned into an ALLOW.
    """
    path = glob_sidecar_path(root)
    try:
        contract = load_contract(root / contract_rel_path)
    except (ContractLoadError, OSError):
        return _discard(root, path)
    text = render_glob_sidecar(root, contract, contract_rel_path, contract_digest)
    if text is None:
        return _discard(root, path)
    try:
        write_text_confined(root, path, text)
    except OSError:
        return _discard(root, path)
    return True


def _discard(root: Path, path: Path) -> bool:
    unlink_confined(root, path)
    return False
