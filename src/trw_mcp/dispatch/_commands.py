"""Per-client command (argv) builder for the dispatch layer.

Belongs to the ``trw_mcp.dispatch`` package. ``build_command`` is a *pure*
function (no I/O, no subprocess) that turns a :class:`DispatchRequest` into the
exact ``list[str]`` argv to execute. The prompt is always passed as a single
argv token — never shell-interpolated — so a malicious prompt body cannot break
out into shell metacharacters.

New clients are a small, data-driven addition: register a ``_ClientSpec`` in
``_CLIENT_SPECS``. The isolation / read-only / model flags verified live on this
box (2026-06-21) are encoded here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from trw_mcp.dispatch._types import SUPPORTED_CLIENTS, DispatchClient, DispatchRequest

# ``SUPPORTED_CLIENTS`` is defined in ``_types.py`` (next to the ``DispatchClient``
# Literal) so config defaults, the env allowlist, and this builder all derive from
# ONE source. Re-exported here for back-compat (``_commands.SUPPORTED_CLIENTS`` and
# the package facade import it from here).
__all__ = ["SUPPORTED_CLIENTS", "UnsupportedClientError", "build_command"]


class UnsupportedClientError(ValueError):
    """Raised for a client we deliberately do not support (e.g. gemini, EOL'd)."""


@dataclass(frozen=True)
class _ClientSpec:
    """Declarative recipe for building one client's argv.

    Each callable takes the request and returns the argv fragment to append, so
    a client's flag policy lives in one place and is trivially testable.
    """

    base: list[str]
    isolation_args: Callable[[DispatchRequest], list[str]] = field(default=lambda _r: [])
    read_only_args: Callable[[DispatchRequest], list[str]] = field(default=lambda _r: [])
    model_args: Callable[[DispatchRequest], list[str]] = field(default=lambda _r: [])
    # How the prompt is appended. Most clients take it as a trailing positional;
    # claude uses the ``-p`` flag.
    prompt_args: Callable[[str], list[str]] = field(default=lambda p: [p])
    # Fixed flags always present (e.g. codex --skip-git-repo-check).
    always_args: list[str] = field(default_factory=list)


_CLIENT_SPECS: dict[DispatchClient, _ClientSpec] = {
    # claude -p "<prompt>" --output-format json  → {.result: str}
    # Isolation keeps USER-level auth but drops this project's ceremony:
    #   --setting-sources user      → load only user settings (skip project/local
    #                                  CLAUDE.md, hooks, settings)
    #   --strict-mcp-config + empty --mcp-config → no MCP servers, so the child
    #                                  cannot recurse into the host trw MCP.
    # NB: `--bare` was rejected — it also drops user login ("Not logged in"),
    # verified live 2026-06-21.
    #
    # read_only enforcement (claude): in headless ``-p`` mode claude denies edits
    # by default — there is no approval prompt to satisfy — so read_only=True adds
    # nothing. read_only=False must EXPLICITLY opt in via --permission-mode
    # acceptEdits, otherwise --allow-writes would be a silent no-op.
    #
    # Isolation limitation: even with --setting-sources user + empty --mcp-config
    # the child still READS the project CLAUDE.md it is pointed at (that is
    # intentional — it must see the code it audits); MCP recursion into the host
    # trw MCP is what the empty --mcp-config blocks.
    "claude": _ClientSpec(
        base=["claude"],
        always_args=["--output-format", "json"],
        isolation_args=lambda r: (
            ["--setting-sources", "user", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
            if r.isolate
            else []
        ),
        read_only_args=lambda r: [] if r.read_only else ["--permission-mode", "acceptEdits"],
        model_args=lambda r: ["--model", r.model] if r.model else [],
        prompt_args=lambda p: ["-p", p],
    ),
    # codex exec "<prompt>"  — picks up host MCP/hooks unless isolated.
    # --ignore-user-config isolates. read_only=True → --sandbox read-only;
    # read_only=False → --sandbox workspace-write (writes actually enabled).
    "codex": _ClientSpec(
        base=["codex", "exec"],
        always_args=["--skip-git-repo-check"],
        isolation_args=lambda r: ["--ignore-user-config"] if r.isolate else [],
        read_only_args=lambda r: ["--sandbox", "read-only" if r.read_only else "workspace-write"],
        model_args=lambda r: ["--model", r.model] if r.model else [],
    ),
    # agy -p "<prompt>"  (Antigravity CLI). Raw stdout by default; PTY fallback
    # is applied by the runner (not here) since it wraps the whole argv.
    #
    # read_only enforcement (agy): read_only=True adds --sandbox (its read-only
    # mode); read_only=False adds --dangerously-skip-permissions to enable writes.
    # Isolation limitation: agy exposes no host-config/MCP isolation flag in this
    # version, so TRW MCP recursion is NOT mitigated for agy (documented gap).
    "agy": _ClientSpec(
        base=["agy"],
        read_only_args=lambda r: ["--sandbox"] if r.read_only else ["--dangerously-skip-permissions"],
        model_args=lambda r: ["--model", r.model] if r.model else [],
        prompt_args=lambda p: ["-p", p],
    ),
    # opencode run "<prompt>" --format json --dir <cwd>  → NDJSON events.
    #
    # read_only enforcement (opencode): denies writes by default without
    # --dangerously-skip-permissions, so read_only=True adds nothing and
    # read_only=False adds --dangerously-skip-permissions.
    # Isolation limitation: opencode reads .opencode/ config from --dir and this
    # version has no --ignore-config flag, so config-driven recursion is a
    # documented gap.
    "opencode": _ClientSpec(
        base=["opencode", "run"],
        always_args=["--format", "json"],
        read_only_args=lambda r: [] if r.read_only else ["--dangerously-skip-permissions"],
        model_args=lambda r: ["--model", r.model] if r.model else [],
    ),
}


def build_command(req: DispatchRequest) -> list[str]:
    """Build the exact argv for *req*.

    Pure function: no environment, no subprocess, no PTY wrapping (the runner
    owns PTY). The prompt is always a single argv token.

    Raises:
        UnsupportedClientError: if ``req.client`` has no registered spec.
    """
    spec = _CLIENT_SPECS.get(req.client)
    if spec is None:  # pragma: no cover - guarded by the Literal type upstream
        raise UnsupportedClientError(f"No command spec for client {req.client!r}")

    argv: list[str] = [*spec.base]
    argv += spec.always_args
    argv += spec.isolation_args(req)
    argv += spec.read_only_args(req)
    argv += spec.model_args(req)
    # opencode needs an explicit --dir for the working directory.
    if req.client == "opencode" and req.cwd is not None:
        argv += ["--dir", str(req.cwd)]
    argv += list(req.extra_args)
    argv += spec.prompt_args(req.prompt)
    return argv
