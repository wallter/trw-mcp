"""``trw-mcp dispatch`` CLI handler.

Belongs to the ``trw_mcp.dispatch`` package. Lazy-imported from
``trw_mcp.server._subcommands`` so the heavy dispatch path is not loaded for
every CLI invocation.

Behavior: build a :class:`DispatchRequest` (applying any audit role to the
prompt), run it, then emit either the raw JSON result plus its requested-vs-applied
``policy`` (``--json`` / ``--output-file``) or the plain normalized answer. Exit 0 iff the result is
``ok``, else 1. An unresolvable or disabled ``--client`` exits 2. ``--variant-of BASE`` also writes
the answer as a named variant of BASE (PRD-CORE-299-FR04), a failed run too, marked ``ok: false``;
a base that is missing or sits in a discovery directory exits 2 before anything is dispatched.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from trw_mcp.dispatch._fallback import dispatch_with_fallback, host_dispatch_client
from trw_mcp.dispatch._private_io import write_private_atomic
from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request
from trw_mcp.dispatch._roles import ROLE_TABLE
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult
from trw_mcp.dispatch._usage import record_dispatch_policy
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.doc_variants import VariantLocationError, variant_dir, write_variant

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


def _variant_base(args: argparse.Namespace) -> Path | None:
    """The --variant-of base, checked before any model call; exits 2 when it cannot take a variant."""
    variant_of = getattr(args, "variant_of", None)
    if not variant_of:
        return None
    # Resolved once here and used for every check and for the write, so a symlink is judged by its target.
    base = Path(str(variant_of)).resolve()
    try:
        if not base.is_file():
            raise VariantLocationError(f"base {base} is not a file")
        variant_dir(base, resolve_project_root())
    except VariantLocationError as exc:
        print(f"--variant-of refused: {exc}", file=sys.stderr)
        sys.exit(2)
    return base


def _write_answer_variant(base: Path, role: str | None, result: DispatchResult, model: str | None) -> None:
    """Write *result* as the next round of *base*'s variant; a failed run is written too, marked failed."""
    spec = ROLE_TABLE.get(role or "")
    body = result.text if result.ok else f"Dispatch failed: {result.silence_reason or 'not ok'}.\n\n{result.text}"
    try:
        path = write_variant(
            base,
            resolve_project_root(),
            kind=spec.artifact_kind if spec else "notes",
            producer=result.client,
            body=body,
            ok=result.ok,
            role=role,
            model=model,
        )
    except (ValueError, OSError) as exc:  # VariantLocationError is a ValueError
        print(f"--variant-of write failed: {exc}", file=sys.stderr)
        sys.exit(2)
    print(f"variant: {path}", file=sys.stderr)


def _resolve_cli_posture(args: argparse.Namespace) -> str:
    """Derive the dispatch posture the CLI passes into :func:`resolve_dispatch_request`.

    An explicit ``--posture`` wins. Otherwise a ``--role`` that is a review/audit
    role (every entry in :data:`ROLE_TABLE` today: code-review, design-audit,
    architectural-audit, adversarial-audit) fail-closed derives
    ``posture="reviewer"`` -- a review role must never run unbounded just because
    the caller forgot a flag. ``--posture default`` overrides that derivation
    explicitly; since the override puts a reviewer-labeled child on an unbounded
    surface, a warning is printed to stderr rather than overriding silently. This
    is the one code path the MCP ``trw_dispatch`` tool's ``posture`` parameter
    also reaches (:func:`trw_mcp.dispatch._resolve.resolve_dispatch_request`), so
    CLI and MCP dispatch cannot diverge on what "reviewer" means.
    """
    explicit_raw = getattr(args, "posture", None)
    explicit = str(explicit_raw) if explicit_raw is not None else None
    role = getattr(args, "role", None)
    role_wants_reviewer = role in ROLE_TABLE
    if explicit is not None:
        if explicit == "default" and role_wants_reviewer:
            print(
                f"warning: --posture default overrides the posture=reviewer that --role {role!r} "
                "would otherwise derive; this child runs UNBOUNDED (no read-only TRW surface).",
                file=sys.stderr,
            )
        return explicit
    return "reviewer" if role_wants_reviewer else "default"


def _fallback_clients(args: argparse.Namespace, dispatch_cfg: object) -> list[str]:
    """--fallback-clients (comma list; "" disables) or the dispatch_fallback_clients config default."""
    flag = getattr(args, "fallback_clients", None)
    if flag is None:
        return list(getattr(dispatch_cfg, "dispatch_fallback_clients", []))
    return [c.strip() for c in str(flag).split(",") if c.strip()]


def run_dispatch(args: argparse.Namespace) -> None:
    """Handle the ``dispatch`` subcommand.

    Delegates client/model/timeout/read-only resolution to the shared
    :func:`resolve_dispatch_request` so the CLI and the MCP tool path produce
    byte-identical requests. A :class:`DispatchResolutionError` (unresolved /
    disabled) is translated to a stderr message + ``sys.exit`` with the carried
    exit code (2), matching the CLI's historical behavior.
    """
    dispatch_cfg = get_config().dispatch

    # The prompt is read here (CLI surface) but applied to the request inside the
    # shared resolver. _read_prompt exits 2 directly on its own input errors.
    prompt = _read_prompt(args)
    base = _variant_base(args)
    cwd = Path(args.cwd) if getattr(args, "cwd", None) else None
    posture = _resolve_cli_posture(args)
    output_file = getattr(args, "output_file", None)
    if output_file and Path(output_file).is_symlink():
        print(f"--output-file {output_file} is a symlink; refusing to write through it", file=sys.stderr)
        sys.exit(2)

    def build(client: str | None, model: str | None) -> DispatchRequest:
        return resolve_dispatch_request(
            client=client,
            prompt=prompt,
            role=getattr(args, "role", None),
            model=model,
            posture=posture,
            effort=getattr(args, "effort", None),
            cwd=cwd,
            timeout_s=getattr(args, "timeout", None),
            # --allow-writes forces writes (read_only=False); otherwise leave
            # read_only unset (None) so the config default applies.
            read_only=(False if bool(getattr(args, "allow_writes", False)) else None),
            isolate=not bool(getattr(args, "no_isolate", False)),
            # None (neither --with-trw nor --no-with-trw) defers to the config
            # default; an explicit flag is authoritative, like --allow-writes.
            with_trw=getattr(args, "with_trw", None),
            use_pty=bool(getattr(args, "pty", False)),
            verify_sandbox=bool(getattr(args, "verify_sandbox", False)),
            dispatch_cfg=dispatch_cfg,
        )

    try:
        req = build(getattr(args, "client", None), getattr(args, "model", None))
    except DispatchResolutionError as err:
        print(str(err), file=sys.stderr)
        sys.exit(err.exit_code)

    ran: list[tuple[DispatchRequest, dict[str, dict[str, object]]]] = []

    def run(request: DispatchRequest) -> DispatchResult:
        ran.append((request, record_dispatch_policy(request, f"cli-{uuid.uuid4().hex}")))  # PRD-CORE-290-FR03
        return dispatch(request)

    # A fallback client gets its own configured model: --model names the primary's.
    result = dispatch_with_fallback(
        req,
        _fallback_clients(args, dispatch_cfg),
        lambda c: build(c, None),
        run=run,
        host_client=host_dispatch_client(),
    )
    last_req, policy = ran[-1]  # the request whose result this is
    if result.fallback_note:
        print(f"dispatch fallback: {result.fallback_note}", file=sys.stderr)
    payload = json.dumps({**result.model_dump(mode="json"), "policy": policy}, indent=2)

    if output_file:
        out_path = Path(output_file)
        # Create any missing parent dirs so a nested --output-file path does not
        # crash on write.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # A rename replaces a symlink swapped in since the check above; it never
        # writes through one.
        write_private_atomic(out_path, payload)
    if getattr(args, "json", False):
        print(payload)
    elif not output_file:
        print(result.text)
    if base is not None:
        _write_answer_variant(base, getattr(args, "role", None), result, last_req.model)

    sys.exit(0 if result.ok else 1)
