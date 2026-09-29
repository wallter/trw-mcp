"""Hook entry-point verification (PRD-CORE-320 FR04).

Soundness scope: a hook entry verifies only when the hook script's own text
invokes the next hop's module or CLI verb (the matching line is named as the
call site) AND this run's evidence contains an event that only the hook's
real, dependency-present branch writes. It does not prove the hook runs on
every session, nor that the degraded branch is absent.

The verifier cannot parse shell as Python, so (a) below is a text match, not
an AST proof, and is deliberately narrow: it accepts exactly two shapes on a
single non-comment line —

* ``python3 -m <module>`` / ``python -m <module>``, where ``<module>`` is
  written out in full and matches the next hop's module exactly; or
* ``trw-mcp <verb>``, where ``<verb>`` is that module's last dotted component
  with ``_`` replaced by ``-`` (this repo's CLI verb-naming convention).

Neither shape is a general shell-command parser: a quoted or
variable-expanded invocation, or a verb spelled differently from its module's
dashed name, is not recognised. That under-recognition is deliberate — the
fail-closed direction (NFR02) is a false "isolated", never a false "wired".

(b), the real-path marker, is a field only the hook's real branch writes:
``{"hook_real_path": "<stem>"}`` in an event the run's evidence holds (see
:func:`trw_mcp.tools._deliver_capability_integration.step_capability_integration`
for where that evidence is gathered). A degraded/no-op branch (e.g. neither
``jq`` nor ``python3`` on ``PATH``) writes no such field.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HookHopResult:
    """The ``hook:<stem>`` -> next-hop edge's verdict.

    ``call_site`` is set only when the edge verifies; ``reason`` only when it
    does not.
    """

    call_site: str = ""
    reason: str = ""


def hooks_dir() -> Path:
    """The running ``trw_mcp`` package's bundled hooks directory.

    Derived from the running package (``Path(trw_mcp.__file__).parent / "data"
    / "hooks"``), never a repo-relative guess: this must resolve the same way
    in the monorepo (editable install) and in an installed project.
    """
    import trw_mcp

    return Path(trw_mcp.__file__).resolve().parent / "data" / "hooks"


_PYTHON = r"python3?"
#: What may follow the module or verb: whitespace, a shell separator, or the end of the line.
_COMMAND_END = r"(?=$|[\s;&|)])"
#: The start of a shell comment: ``#`` at the start of the line or after whitespace (``a#b`` is a word).
_INLINE_COMMENT = re.compile(r"(?:^|(?<=\s))#")
#: A bundled hook stem: a plain file name, nothing that can leave the hooks directory.
_STEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _invoking_line(text: str, module: str) -> int | None:
    """The 1-based line number of the first non-comment line in *text* invoking *module*.

    See the module docstring for exactly what is accepted. Returns ``None``
    when no line matches.
    """
    verb = module.rpartition(".")[2].replace("_", "-")
    # A whole command token on each side: a longer module (``pkg.leaf.extra``) or verb (``leaf-extra``)
    # is a different target, not this one (sol core-320-fr04 r1).
    python_re = re.compile(rf"(?:^|[\s;&|(]){_PYTHON}\s+-m\s+{re.escape(module)}{_COMMAND_END}")
    cli_re = re.compile(rf"(?:^|[\s;&|(])trw-mcp\s+{re.escape(verb)}{_COMMAND_END}")
    for lineno, line in enumerate(text.splitlines(), start=1):
        code = _INLINE_COMMENT.split(line, maxsplit=1)[0]  # nothing after an unquoted ``#`` runs
        if python_re.search(code) or cli_re.search(code):
            return lineno
    return None


def verify_hook_hop(repo_root: Path, stem: str, next_hop: str, hook_evidence: Collection[str]) -> HookHopResult:
    """Verify the ``hook:<stem>`` -> *next_hop* edge.

    *hook_evidence* is the set of hook stems whose real-path marker was
    observed in this run's evidence (gathered by the caller). Fails closed:
    a missing script, a script that never mentions *next_hop*'s module, or a
    stem absent from *hook_evidence* all resolve to an empty ``call_site``
    with a named reason. Never raises.
    """
    if not _STEM.fullmatch(stem):  # ``../x``, ``/tmp/x`` or ``a/b`` would leave the bundled hooks (sol r1)
        return HookHopResult(reason=f"hook stem {stem!r} is not a bundled hook name")
    script = hooks_dir() / f"{stem}.sh"
    try:
        text = script.read_text(encoding="utf-8")
    except OSError:
        return HookHopResult(reason=f"hook script not found: {stem}.sh")
    module = next_hop.rpartition(".")[0]
    lineno = _invoking_line(text, module)
    if lineno is None:
        return HookHopResult(reason=f"hook script does not invoke {module}")
    if stem not in hook_evidence:
        return HookHopResult(reason="hook real path not observed")
    try:
        relative = script.relative_to(repo_root)
    except ValueError:
        relative = script
    return HookHopResult(call_site=f"{relative}:{lineno}")


__all__ = ["HookHopResult", "hooks_dir", "verify_hook_hop"]
