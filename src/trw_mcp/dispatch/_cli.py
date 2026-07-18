"""``trw-mcp dispatch`` CLI handler.

Belongs to the ``trw_mcp.dispatch`` package. Lazy-imported from
``trw_mcp.server._subcommands`` so the heavy dispatch path is not loaded for
every CLI invocation.

Behavior: build a :class:`DispatchRequest` (applying any audit role to the
prompt), run it, then emit either the raw JSON result (``--json`` /
``--output-file``) or the plain normalized answer. Exit 0 iff the result is
``ok``, else 1. ``--client gemini`` exits 2 with a redirect to ``agy``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.models.config import get_config

# Reject an oversized --prompt-file before reading it into memory: a 1 MB ceiling
# is generous for an audit instruction and stops a runaway/hostile file from
# being slurped wholesale.
_MAX_PROMPT_FILE_BYTES = 1_000_000


def _read_prompt(args: argparse.Namespace) -> str:
    """Resolve the prompt from --prompt or --prompt-file (exactly one)."""
    prompt = getattr(args, "prompt", None)
    prompt_file = getattr(args, "prompt_file", None)
    if prompt and prompt_file:
        print("Provide only one of --prompt / --prompt-file.", file=sys.stderr)
        sys.exit(2)
    if prompt_file:
        path = Path(str(prompt_file))
        try:
            size = path.stat().st_size
        except OSError as exc:
            print(f"Cannot read --prompt-file {prompt_file!r}: {exc}", file=sys.stderr)
            sys.exit(2)
        if size > _MAX_PROMPT_FILE_BYTES:
            print(
                f"--prompt-file is too large ({size} bytes; max {_MAX_PROMPT_FILE_BYTES}).",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Cannot read --prompt-file {prompt_file!r}: {exc}", file=sys.stderr)
            sys.exit(2)
    if prompt:
        return str(prompt)
    print("A prompt is required: pass --prompt or --prompt-file.", file=sys.stderr)
    sys.exit(2)


def run_dispatch(args: argparse.Namespace) -> None:
    """Handle the ``dispatch`` subcommand.

    Delegates client/model/timeout/read-only resolution to the shared
    :func:`resolve_dispatch_request` so the CLI and the MCP tool path produce
    byte-identical requests. A :class:`DispatchResolutionError` (unresolved /
    disabled / gemini-EOL) is translated to a stderr message + ``sys.exit`` with
    the carried exit code (2), matching the CLI's historical behavior.
    """
    dispatch_cfg = get_config().dispatch

    # The prompt is read here (CLI surface) but applied to the request inside the
    # shared resolver. _read_prompt exits 2 directly on its own input errors.
    prompt = _read_prompt(args)
    cwd = Path(args.cwd) if getattr(args, "cwd", None) else None

    try:
        req = resolve_dispatch_request(
            client=getattr(args, "client", None),
            prompt=prompt,
            role=getattr(args, "role", None),
            model=getattr(args, "model", None),
            cwd=cwd,
            timeout_s=getattr(args, "timeout", None),
            # --allow-writes forces writes (read_only=False); otherwise leave
            # read_only unset (None) so the config default applies.
            read_only=(False if bool(getattr(args, "allow_writes", False)) else None),
            isolate=not bool(getattr(args, "no_isolate", False)),
            use_pty=bool(getattr(args, "pty", False)),
            dispatch_cfg=dispatch_cfg,
        )
    except DispatchResolutionError as err:
        print(str(err), file=sys.stderr)
        sys.exit(err.exit_code)

    result = dispatch(req)

    output_file = getattr(args, "output_file", None)
    if output_file:
        out_path = Path(output_file)
        # Create any missing parent dirs so a nested --output-file path does not
        # crash on write.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    if getattr(args, "json", False):
        print(result.model_dump_json(indent=2))
    elif not output_file:
        print(result.text)

    sys.exit(0 if result.ok else 1)
