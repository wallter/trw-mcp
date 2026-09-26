"""``dispatch`` CLI subparser registration.

Belongs to the ``_cli_argparse.py`` builder, which calls
:func:`add_dispatch_subcommand` where the subcommand used to be defined inline.

Extracted for the 350 effective-LOC module gate: the dispatch subcommand carries
the widest flag set of any subcommand here (client, model, timeout, role, cwd,
prompt/prompt-file, isolation, writes, TRW access, pty, sandbox verification,
output), and adding ``--with-trw`` / ``--no-with-trw`` (PRD-CORE-281-FR03) took
``_cli_argparse.py`` from 350 effective LOC to 368. The two sibling registration
modules (``_cli_argparse_operational``, ``_cli_argparse_project``) are the same
pattern, so this is the established split rather than a new one.
"""

from __future__ import annotations

import argparse

from trw_mcp.dispatch._client_spec_types import EFFORT_LEVELS
from trw_mcp.dispatch._client_specs import SUPPORTED_CLIENTS
from trw_mcp.dispatch._roles import ROLE_TABLE

__all__ = ["add_dispatch_subcommand"]


def add_dispatch_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``dispatch`` subcommand (cross-client second-opinion audits)."""
    clients = " | ".join(SUPPORTED_CLIENTS)
    dispatch_parser = subparsers.add_parser(
        "dispatch",
        help=f"Run another coding-agent CLI ({'/'.join(SUPPORTED_CLIENTS)}) headlessly for a second opinion",
    )
    dispatch_parser.add_argument(
        "--client",
        default=None,
        help=(
            f"Target CLI: {clients}. "
            "Optional: defaults to dispatch.default_client (or a --role default) "
            "from .trw/config.yaml."
        ),
    )
    dispatch_parser.add_argument(
        "--fallback-clients",
        dest="fallback_clients",
        default=None,
        help=(
            "Comma list of clients to try in order when the previous one reports quota_exhausted, "
            "an unsupported flag or a launch failure (e.g. codex,claude). Default: "
            "dispatch_fallback_clients; '' disables. Only listed clients run, never the host's own implicitly; "
            "a client that cannot run the request's posture is skipped (posture_unsupported)."
        ),
    )
    dispatch_parser.add_argument(
        "--prompt",
        default=None,
        help="The prompt/instruction for the child agent (or use --prompt-file).",
    )
    dispatch_parser.add_argument(
        "--prompt-file",
        dest="prompt_file",
        default=None,
        help="Read the prompt body from a file instead of --prompt.",
    )
    dispatch_parser.add_argument(
        "--role",
        default=None,
        choices=sorted(ROLE_TABLE),
        help="Prepend a read-only second-opinion audit role preamble to the prompt.",
    )
    dispatch_parser.add_argument(
        "--posture",
        choices=("default", "reviewer"),
        default=None,
        help=(
            "Confinement posture for the child. A --role that is a review/audit role "
            "(e.g. adversarial-audit, code-review) auto-derives posture=reviewer, fail-closed, "
            "so a review role never runs unbounded by omission. Pass --posture default to "
            "override that derivation (a warning is printed, since the child then runs "
            "unbounded). Maps to the same posture machinery trw_dispatch uses."
        ),
    )
    dispatch_parser.add_argument(
        "--model",
        default=None,
        help="Optional model override for the child client.",
    )
    dispatch_parser.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default=None,
        help="Reasoning effort for the child; default: dispatch_default_effort, else the role's task-class row.",
    )
    dispatch_parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory for the child process (default: current directory).",
    )
    dispatch_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help=(
            "Hard wall-clock timeout in seconds. Defaults to "
            "dispatch.default_timeout_s from .trw/config.yaml (600 if unset)."
        ),
    )
    dispatch_parser.add_argument(
        "--output-file",
        dest="output_file",
        default=None,
        help="Write the full DispatchResult JSON to this file.",
    )
    dispatch_parser.add_argument(
        "--variant-of",
        dest="variant_of",
        default=None,
        help=(
            "Also write the answer next to this base document as BASE.KIND-CLIENT-rN.md "
            "(next free round, never overwritten; kind from --role), stamped with the base's sha256."
        ),
    )
    dispatch_parser.add_argument(
        "--no-isolate",
        dest="no_isolate",
        action="store_true",
        help="Do NOT isolate the child from host config/hooks/MCP (default: isolate).",
    )
    dispatch_parser.add_argument(
        "--allow-writes",
        dest="allow_writes",
        action="store_true",
        help="Allow the child agent to write/edit (default: read-only).",
    )
    dispatch_parser.add_argument(
        "--with-trw",
        dest="with_trw",
        action="store_true",
        default=None,
        help=(
            "Give the child its own stdio trw-mcp connection to this project (TRW's server only; "
            "host hooks and user/project client config stay isolated). Refused for a client with "
            "no argv channel for it. Default comes from dispatch_child_trw_access."
        ),
    )
    dispatch_parser.add_argument(
        "--no-with-trw",
        dest="with_trw",
        action="store_false",
        # ``default=None`` restated on the negative half deliberately: argparse
        # seeds a shared dest from whichever action it reaches FIRST, and
        # ``store_false`` defaults to True. Relying on declaration order would
        # mean that reordering these two blocks silently pins with_trw to a
        # value the caller never passed and makes dispatch_child_trw_access
        # unreachable from the CLI.
        default=None,
        help="Force NO TRW connection for the child, overriding dispatch_child_trw_access.",
    )
    dispatch_parser.add_argument(
        "--pty",
        action="store_true",
        help="Wrap the child in a pseudo-TTY (use if stdout comes back empty, e.g. agy bug #76).",
    )
    dispatch_parser.add_argument(
        "--verify-sandbox",
        dest="verify_sandbox",
        action="store_true",
        help=(
            "Run a live write-containment probe in a disposable fixture and report the verdict "
            "as sandbox_verified (true/false/unverified). Costs one extra model call; off by default."
        ),
    )
    dispatch_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full DispatchResult as JSON instead of just the answer text.",
    )
