"""Misc CLI subcommand handlers — extracted from _subcommands.py for module-size compliance.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat
with test imports (``test_config_reference.py``).

Two handlers:
- ``_run_config_reference`` — print config env vars (markdown table)
- ``_run_local`` — offline ceremony fallback (PRD-FIX-073, extended by
  PRD-CORE-247-FR03 with ``recall`` and ``feedback``)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _type_text(annotation: object) -> str:
    """A field's type as an operator reads it: short class names, no module paths or ``<class '...'>``."""
    import re

    text = annotation.__name__ if isinstance(annotation, type) else str(annotation)
    return re.sub(r"(?:[A-Za-z_]\w*\.)+([A-Za-z_]\w*)", r"\1", text)


def _shorten(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _default_text(field_info: Any) -> str:
    """The default an operator would get: the factory's value, the literal, or ``(required)``; never ``PydanticUndefined``."""
    from pydantic_core import PydanticUndefined

    if field_info.default is not PydanticUndefined:
        return "" if field_info.default is None else str(field_info.default)
    if field_info.default_factory is not None:
        try:
            return str(field_info.get_default(call_default_factory=True))
        except (
            Exception
        ):  # justified: a factory that needs validated data cannot be shown; say so, don't crash the reference
            return "(computed)"
    return "(required)"


def _run_config_reference(args: argparse.Namespace) -> None:
    """Handle the ``config-reference`` subcommand -- print config env vars."""
    from trw_mcp.models.config._env_only_vars import ENV_ONLY_VARS
    from trw_mcp.models.config._main_fields import _TRWConfigFields

    print("# TRW Configuration Reference\n")
    print("All values can be set via environment variables with `TRW_` prefix.\n")
    print("| Environment Variable | Type | Default | Description |")
    print("|---------------------|------|---------|-------------|")

    for name, field_info in _TRWConfigFields.model_fields.items():
        env_var = f"TRW_{name.upper()}"
        field_type = _type_text(field_info.annotation)
        default = _default_text(field_info)
        # Truncate long defaults
        default_str = _shorten(str(default))
        desc = field_info.description or ""
        print(f"| `{env_var}` | {field_type} | `{default_str}` | {desc} |")

    # Env-only variables: read straight from os.environ, never declared as a
    # TRWConfig field, so the loop above cannot see them. ENV_ONLY_VARS is the
    # single explicit registry for this category (W35 item 4).
    for env_only in ENV_ONLY_VARS:
        print(f"| `{env_only.name}` | str | (none — env-only) | {env_only.description} |")

    # trw-memory's settings (the memory engine and daemon read these, not TRWConfig), so an operator sees every
    # variable that changes memory behaviour in one table (E2E-INC-051 (a)).
    from trw_memory.models.config import MemoryConfig

    prefix = str(MemoryConfig.model_config.get("env_prefix", "MEMORY_")).upper()
    for name, field_info in MemoryConfig.model_fields.items():
        print(
            f"| `{prefix}{name.upper()}` | {_type_text(field_info.annotation)} | `{_shorten(_default_text(field_info))}` "
            f"| {field_info.description or ''} |"
        )


def _local_status_machine(args: argparse.Namespace, fmt: str) -> None:
    """``local status --json`` / ``--format line`` (PRD-CORE-354 FR01/FR03/FR04).

    Session-keyed and pin-only: no pin is ``run.state == "none"`` (exit 0), not
    the plain-text refusal. Imports only the status modules, so the statusLine hot
    path never loads the service layer. Line mode never fails: any error prints
    the fallback line and exits 0.
    """
    import os

    try:
        from trw_mcp.services.status_snapshot import load_or_build_snapshot
        from trw_mcp.state._paths import resolve_trw_dir

        session_id = (
            getattr(args, "session_id", None)
            or os.environ.get("TRW_SESSION_ID")
            or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or None
        )
        run_path_str = getattr(args, "run_path", None)
        try:
            from trw_mcp.server._cli_replacements import _bounded_lane_active

            # A reviewer/dispatched-child lane runs `local status` as a read-only
            # verb; the opt-in cache is its only write, so the lane gets none.
            allow_cache_write = _bounded_lane_active() is None
        except Exception:  # justified: fail-open, an unknown lane state disables the cache write
            allow_cache_write = False
        snapshot = load_or_build_snapshot(
            resolve_trw_dir(),
            session_id,
            cache_ttl_s=getattr(args, "cache_ttl", None),
            allow_cache_write=allow_cache_write,
            run_path=Path(run_path_str) if run_path_str else None,
        )
        if fmt == "json":
            print(json.dumps(snapshot, indent=2))
            return
        from trw_mcp.services.status_line import render_status_line

        line = render_status_line(snapshot)
        print(line)
        ttl = getattr(args, "cache_ttl", None)
        # The line cache mirrors the JSON cache's guards: opt-in TTL, no explicit run path, lane may write.
        if allow_cache_write and ttl and ttl > 0 and not run_path_str:
            try:
                from trw_mcp.services.status_snapshot import write_cached_line

                write_cached_line(resolve_trw_dir(), session_id, line)
            except Exception:  # justified: fail-open, a failed cache write never fails the status call
                pass  # trw-fail-silent-allow: cache is an optimisation
    except Exception as exc:  # justified: boundary, a status surface must never break its caller
        if fmt == "line":
            print("TRW · status unavailable")
            return
        print(f"Error: status snapshot failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        sys.exit(1)


_TOPLEVEL_TIMEOUT_SECONDS = 5.0


def _enclosing_project(cwd: Path) -> Path | None:
    """The VCS toplevel of *cwd* when it differs from *cwd* and holds a ``.trw/`` dir, else ``None``.

    Bounded by a timeout; a missing binary, a non-repo cwd or any failure is ``None`` (keep the cwd).
    """
    import shutil
    import subprocess

    tool = shutil.which("git")
    if tool is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, resolved binary, no shell
            [tool, "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_TOPLEVEL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # trw-fail-silent-allow: no git or a hung call means "use the cwd"
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    top = Path(proc.stdout.strip()).resolve()
    if top == cwd.resolve() or not (top / ".trw").is_dir():
        return None
    return top


@contextmanager
def enclosing_project_bound() -> Iterator[None]:
    """Bind the project enclosing the cwd for one CLI verb (``local``, ``feedback``).

    With no install target and no ``TRW_PROJECT_ROOT``, a cwd inside a checkout whose toplevel has a
    ``.trw/`` resolves to that toplevel (via ``project_bound``, this command only). Every other caller of
    ``resolve_project_root`` is unchanged.
    """
    import os

    from trw_mcp.state._project_root_binding import install_target, project_bound

    top = None
    if install_target() is None and not os.environ.get("TRW_PROJECT_ROOT"):
        top = _enclosing_project(Path.cwd())
    if top is None:
        yield
        return
    with project_bound(top):
        yield


def _run_local(args: argparse.Namespace) -> None:
    """Handle ``local``: run the verb against the project that encloses the cwd."""
    with enclosing_project_bound():
        _run_local_verb(args)


def _run_local_verb(args: argparse.Namespace) -> None:
    """Offline ceremony fallback (PRD-FIX-073)."""
    if getattr(args, "local_command", None) == "status":
        fmt = "json" if getattr(args, "json", False) else str(getattr(args, "status_format", "text") or "text")
        if fmt in {"json", "line"}:
            _local_status_machine(args, fmt)
            return

    from trw_memory.exceptions import MemoryError as TrwMemoryError

    from trw_mcp.services.orchestration_service import (
        mark_local_delivered,
        read_local_status,
        scaffold_run_directory,
        write_checkpoint,
        write_local_learning,
    )

    local_cmd = getattr(args, "local_command", None)

    if local_cmd == "init":
        task_name = getattr(args, "task", None)
        if not task_name:
            print("Error: --task is required for 'local init'")
            sys.exit(1)
        init_result = scaffold_run_directory(task_name)
        print(f"Run initialized: {init_result['run_id']}")
        print(f"  Path: {init_result['run_path']}")
    elif local_cmd == "checkpoint":
        message = getattr(args, "message", "") or ""
        run_path_str = getattr(args, "run_path", None)
        run_path = Path(run_path_str) if run_path_str else None
        try:
            cp_result = write_checkpoint(message, run_path=run_path)
            print(f"Checkpoint created at {cp_result['timestamp']}")
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    elif local_cmd == "status":
        run_path_str = getattr(args, "run_path", None)
        run_path = Path(run_path_str) if run_path_str else None
        try:
            status = read_local_status(run_path=run_path)
            print(f"Run: {status['run_id']}")
            print(f"  Task: {status['task']}")
            print(f"  Status: {status['status']}")
            print(f"  Phase: {status['phase']}")
            print(f"  Checkpoints: {status['checkpoints']}")
            print(f"  Events: {status['events']}")
            print(f"  Path: {status['run_path']}")
        except FileNotFoundError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    elif local_cmd == "learn":
        from trw_mcp.state._store_selection import StoreUnavailableError

        tags = list(getattr(args, "tag", []) or [])
        evidence = list(getattr(args, "evidence", []) or []) or None
        try:
            result = write_local_learning(
                summary=str(getattr(args, "summary", "")),
                detail=str(getattr(args, "detail", "")),
                tags=tags,
                evidence=evidence,
                impact=float(getattr(args, "impact", 0.5) or 0.5),
                type=str(getattr(args, "type", "pattern") or "pattern"),
                confidence=str(getattr(args, "confidence", "unverified") or "unverified"),
                evidence_level=str(getattr(args, "evidence_level", "unknown") or "unknown"),
            )
            if result.get("status") == "rejected":
                print(f"Error: {result.get('message', result.get('reason', 'rejected'))}")
                sys.exit(1)
            print(f"Learning {result.get('status', 'saved')}: {result.get('id', result.get('learning_id', 'unknown'))}")
        except (OSError, ValueError, StoreUnavailableError) as exc:
            print(f"Error: {exc}")
            sys.exit(1)
        except TrwMemoryError as exc:
            # confidence="verified" without --evidence (and other write-time
            # schema/policy refusals) raise trw_memory.exceptions.MemoryError
            # (e.g. SchemaValidationError) here — the same gate the MCP
            # trw_learn tool enforces, surfaced as a clean CLI message instead
            # of a traceback.
            print(f"Error: {exc}")
            sys.exit(1)
    elif local_cmd == "recall":
        # PRD-CORE-247-FR03: marshalling only. Ranking happened in execute_recall.
        from trw_memory.daemon.client import DAEMON_START_COMMAND

        from trw_mcp.services.local_surface_service import format_local_recall, run_local_recall

        try:
            recall_result = run_local_recall(
                str(getattr(args, "query", "")),
                tags=list(getattr(args, "tag", []) or []) or None,
                max_results=getattr(args, "max_results", None),
            )
        except Exception as exc:
            # A raised exception (store open failed outright, before execute_recall
            # could even attempt the query) is reported the same way as the
            # in-band ``store_unavailable`` signal below: stderr, non-zero, with a
            # remedy — never printed as if it were an empty result.
            print(
                f"Error: cannot read the memory store ({type(exc).__name__}: {exc}). "
                f"Start the memory daemon ({DAEMON_START_COMMAND}) or run `trw-mcp doctor` to diagnose.",
                file=sys.stderr,
            )
            sys.exit(1)
        # PRD-CORE-247-FR03 / L-PbZW: an unopenable store is not an empty one.
        # ``execute_recall`` reports this in-band via ``store_unavailable`` rather
        # than raising (see trw_mcp.state._memory_recall.recall_learnings), so the
        # CLI must check for it explicitly — otherwise a daemon-down/store-refused
        # recall silently prints "No matching learnings." and exits 0, a false
        # success indistinguishable from a genuinely empty store.
        store_unavailable = recall_result.get("store_unavailable")
        if store_unavailable:
            print(
                f"Error: memory store unavailable ({store_unavailable}). "
                f"Start the memory daemon ({DAEMON_START_COMMAND}) or run `trw-mcp doctor` to diagnose.",
                file=sys.stderr,
            )
            sys.exit(1)
        for line in format_local_recall(recall_result):
            print(line)
    elif local_cmd == "feedback":
        from trw_mcp.services.local_surface_service import submit_local_feedback

        feedback_result = submit_local_feedback(
            category=str(getattr(args, "category", "")),
            subject=str(getattr(args, "subject", "")),
            message=str(getattr(args, "message", "")),
            contact_email=getattr(args, "contact_email", None),
            force=bool(getattr(args, "force", False)),
        )
        if feedback_result.get("success"):
            print(f"Feedback submitted: {feedback_result.get('submission_id') or 'accepted'}")
        else:
            # Same result shape the MCP tool returns; an unconfigured backend is
            # reported, not raised, so the operator sees what to set.
            # Nothing was sent, so a script looping over submissions must see a failure (exit 1, reason on
            # stderr). A record in the local outbox is a retry aid (`trw-mcp feedback flush`), not a delivery.
            reason = f"Feedback not submitted: {feedback_result.get('error', 'unknown error')}"
            outbox_id = feedback_result.get("outbox_id")
            if outbox_id:
                reason += f" (kept in the local outbox as {outbox_id}; retry with `trw-mcp feedback flush`)"
            print(reason, file=sys.stderr)
            sys.exit(1)
    elif local_cmd == "deliver":
        run_path_str = getattr(args, "run_path", None)
        run_path = Path(run_path_str) if run_path_str else None
        as_json = bool(getattr(args, "json", False))
        try:
            status = mark_local_delivered(str(getattr(args, "message", "") or "local delivery"), run_path=run_path)
        except FileNotFoundError as exc:
            if as_json:
                # PRD-CORE-300-FR02 slice S0: --json means exactly one parseable
                # document on stdout, success or failure alike.
                print(json.dumps({"error": str(exc)}))
            else:
                print(f"Error: {exc}")
            sys.exit(1)
        if as_json:
            print(
                json.dumps(
                    {
                        "run_id": status["run_id"],
                        "run_path": status["run_path"],
                        "gate_evaluated": False,
                    }
                )
            )
        else:
            print(f"Run delivered: {status['run_id']}")
            print(f"  Path: {status['run_path']}")
            # Said out loud, not only stamped in run.yaml. The operator reading
            # this line is the one who can still supply the evidence; a field in a
            # file they may never open is a record, not a notice.
            print("  NOTE: offline path — no deliver gate was evaluated (gate_evaluated: false).")
            print("        CONSTITUTION 1.a still binds: a passing build check, a durable")
            print("        acceptable-failure record, or a recorded override.")
    else:
        # PRD-CORE-247-FR03: this listing IS the discoverability fix. The
        # capability must be reachable without reading argparse source, so every
        # subcommand appears here with the flags it needs.
        print("Usage: trw-mcp local {init|checkpoint|status|learn|recall|feedback|deliver}")
        print()
        print("Commands:")
        print("  init        Create a run directory (--task NAME required)")
        print("  checkpoint  Save progress (--message MSG)")
        print("  status      Show active local run status")
        print("  learn       Persist a learning (--summary, --detail, --tag, --type,")
        print("              --confidence, --impact, --evidence)")
        print("  recall      Recall learnings (--query Q, --tag T, --max-results N)")
        print("  feedback    Submit feedback (--category C, --subject S, --message M)")
        print("  deliver     Mark active run delivered (--message MSG)")
        sys.exit(0)

    sys.exit(0)
