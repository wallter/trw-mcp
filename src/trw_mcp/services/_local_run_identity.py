"""Run identity for the offline ``trw-mcp local`` CLI (PRD-FIX-132).

The MCP surface answers "which run is mine" from a per-connection pin keyed on
the caller's session identity, and refuses when there is no pin (PRD-CORE-141
FR05). The shell hooks answer it the same way, through ``trw_pin_key`` /
``resolve_owned_run`` against the same ``.trw/runtime/pins.json``. The offline
CLI answered it with ``max(st_mtime)`` over every ``run.yaml`` under
``.trw/runs/``.

mtime carries no ownership information. Under concurrency -- the condition the
offline path exists for -- the most recently touched run belongs to whichever
*other* agent checkpointed last, so a pinless agent following the degraded-mode
protocol appended its checkpoints, and its ungated ``delivered`` stamp, to a
stranger's audit trail. The resulting run directory reads exactly like an honest
one, which is what makes it a truthfulness defect rather than an inconvenience.

Resolution here is therefore explicit-or-refuse, with no third path:

1. ``--run-path`` -- the caller states the answer.
2. The session pin, read through :func:`resolve_pin_key` and
   :func:`get_pinned_run`. The precedence chain is NOT re-implemented here; a
   second definition of identity is how the divergence arose in the first place.
3. Refuse. Candidate runs are printed as advisory text; nothing selects one and
   nothing is written.
"""

from __future__ import annotations

from pathlib import Path

import structlog

_logger = structlog.get_logger(__name__)

#: Upper bound on the advisory candidate list. A refusal message must not grow
#: with the run tree -- this repository carries ~200 runs.
MAX_ADVISORY_CANDIDATES = 5


class LocalRunIdentityError(FileNotFoundError):
    """The offline CLI could not establish which run the caller owns.

    Subclasses ``FileNotFoundError`` deliberately. The CLI dispatcher
    (``server/_subcommands_misc._run_local``) already catches that type for
    ``checkpoint``, ``status`` and ``deliver`` and turns it into a printed error
    plus exit 1. Raising a type it does not catch would surface a traceback
    instead of the remedy, and the whole point of the refusal is that the caller
    can act on it.

    Attributes:
        candidates: The advisory run directories named in the message. Carried
            structurally so a programmatic caller does not have to parse prose.
    """

    def __init__(self, message: str, *, candidates: tuple[Path, ...] = ()) -> None:
        super().__init__(message)
        self.candidates = candidates


def resolve_session_pin_key() -> str | None:
    """Return this process's pin key when it is externally anchored, else ``None``.

    ``resolve_pin_key`` always returns *something*: its last layer mints a
    per-process UUID. That UUID is unknowable to any other process, so a pin
    keyed on it can never be looked up again -- it is the "identity unknown"
    state wearing the shape of an answer. Comparing the resolved key against
    :func:`get_session_id` detects exactly that case without duplicating a
    single layer of the precedence chain (explicit > ``TRW_SESSION_ID`` >
    client session var > FastMCP ctx > process UUID).

    The ``ctx_isolation_enabled=False`` kill switch also short-circuits to the
    process UUID, and is therefore reported as unanchored too. That is the safe
    reading: with isolation disabled there is no per-session identity to honour.
    """
    from trw_mcp.state._paths import get_session_id, resolve_pin_key

    key = resolve_pin_key(ctx=None, explicit=None)
    if key == get_session_id():
        return None
    return key


def pinned_run_for_session() -> Path | None:
    """Return the run directory pinned for this session, or ``None``.

    A pin whose target has been deleted resolves to ``None`` rather than to a
    path: a dangling pin is not ownership of anything.
    """
    key = resolve_session_pin_key()
    if key is None:
        return None

    from trw_mcp.state._paths import get_pinned_run

    run_dir = get_pinned_run(session_id=key)
    if run_dir is None:
        return None
    if not (run_dir / "meta" / "run.yaml").is_file():
        _logger.warning("local_run_pin_dangling", pin_key=key, run_path=str(run_dir))
        return None
    return run_dir


def candidate_runs(limit: int = MAX_ADVISORY_CANDIDATES) -> tuple[Path, ...]:
    """Return up to *limit* non-terminal runs, newest run id first -- advisory only.

    This exists so a refusal can tell an operator what is available. It is
    deliberately NOT wired into resolution: naming candidates is help, choosing
    one is the defect.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.models.run import is_terminal_status
    from trw_mcp.state._paths import iter_run_dirs, resolve_project_root
    from trw_mcp.state.persistence import FileStateReader

    try:
        runs_root = resolve_project_root() / str(get_config().runs_root)
        reader = FileStateReader()
        live: list[Path] = []
        for run_dir, run_yaml in iter_run_dirs(runs_root):
            try:
                status = str(reader.read_yaml(run_yaml).get("status", "active"))
            except Exception:  # justified: an unreadable run.yaml is still a candidate to name
                status = "active"
            if not is_terminal_status(status):
                live.append(run_dir)
    except (OSError, ValueError):
        return ()

    live.sort(key=lambda p: p.name, reverse=True)
    return tuple(live[:limit])


def _refusal_message(candidates: tuple[Path, ...]) -> str:
    lines = [
        "Refusing to select a run: no --run-path was given and no run is pinned for this session.",
        "Picking the most recently modified run would write into whichever run another agent",
        "touched last, so this command writes nothing until identity is stated.",
        "Remedy (any one):",
        "  - pass --run-path <run directory>",
        "  - export TRW_SESSION_ID=<this session's id> so the .trw/runtime/pins.json entry resolves",
        "  - run 'trw-mcp local init --task NAME' and pass the path it prints as --run-path",
    ]
    if candidates:
        lines.append(f"Candidate runs (advisory only, none was selected; showing {len(candidates)}):")
        lines.extend(f"  - {path}" for path in candidates)
    else:
        lines.append("Candidate runs (advisory only): none found under the runs root.")
    return "\n".join(lines)


def resolve_owned_run_path(run_path: Path | None) -> Path:
    """Resolve the run directory this caller owns, or raise.

    Args:
        run_path: Explicit run directory from ``--run-path``, or ``None``.

    Returns:
        The run directory to operate on.

    Raises:
        FileNotFoundError: When an explicit *run_path* does not exist.
        LocalRunIdentityError: When no explicit path was given and no pin
            resolves. Nothing has been written when this is raised.
    """
    if run_path is not None:
        explicit = Path(run_path)
        if not explicit.exists():
            raise FileNotFoundError(f"Run path does not exist: {explicit}")
        return explicit

    pinned = pinned_run_for_session()
    if pinned is not None:
        _logger.info("local_run_resolved_from_pin", run_path=str(pinned))
        return pinned

    candidates = candidate_runs()
    _logger.warning("local_run_identity_unresolved", candidate_count=len(candidates))
    raise LocalRunIdentityError(_refusal_message(candidates), candidates=candidates)
