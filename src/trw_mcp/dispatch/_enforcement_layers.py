"""FR08 enforcement-layer report: which MCP/permission layers a dispatch actually held.

Belongs to the ``trw_mcp.dispatch`` package (PRD-SEC-015-FR08). ``read_only_enforced``
on :class:`trw_mcp.dispatch._types.DispatchResult` reports one filesystem-write
fact and nothing about the child's MCP tool surface -- a single ``True`` that has
to cover four different mechanisms across the client matrix is exactly the
conflation this FR replaces. :func:`enforcement_report` is the ONE place that
decides the per-client layer tuple; nowhere else encodes the table.

**A layer name is a claim of verified enforcement.** Every layer emitted below is
backed by a named, passing probe (PRD-SEC-015-FR08's table); an unmeasured or
assumed layer is never invented -- the client/isolate combination instead gets an
empty tuple and a note explaining why nothing is claimed.
"""

from __future__ import annotations

from typing_extensions import TypedDict

from trw_mcp.dispatch._types import EnforcementLayer


def enforcement_report(
    client: str,
    *,
    read_only: bool,
    isolate: bool,
    posture_enforced: bool,
    mcp_injected: bool,
) -> tuple[tuple[EnforcementLayer, ...], str]:
    """Return the FR08 ``(enforcement_layers, mcp_role_note)`` pair for one dispatch.

    Encodes the FR08 table verbatim, one row per (client, isolate) pair the PRD's
    probes actually cover. Any pair the table does not list -- including EVERY
    row for a client outside {codex, claude, agy, grok, opencode} -- returns ``()``
    with a note naming it unmeasured, never a guessed layer.

    ``posture_enforced`` gating (codex, ``isolate=False`` only): ``mcp_allowlist``
    (the ``-c mcp_servers.trw.enabled_tools`` override) and ``mcp_role``
    (``TRW_SURFACE_ROLE=reviewer`` in the child's server env) are BOTH rendered
    from the SAME ``reviewer_argv_template`` (``_client_specs.py``), which only
    renders when the reviewer posture actually took (``posture_enforced=True``).
    Reporting either layer on a default-posture dispatch -- where no ``-c``
    override and no role env ever reached the child -- would be an unverified
    claim, so both are gated together. ``sandbox`` (``--sandbox read-only``) is
    NOT gated: codex emits that flag for every read-only dispatch regardless of
    posture, so it is reported whenever ``read_only`` is True.

    ``mcp_injected`` is True when a reviewer or ``with_trw`` template rendered an MCP server into the argv.
    ``build_command`` picks that transport BEFORE isolation, so ``isolate=True`` does not mean "no MCP": a
    child with an injected server never reports ``mcp_absent`` (sol r1 P2).

    When ``read_only`` is False nothing was denied, so the report is ``((), "")``
    for every client -- the PRD's table only enumerates the read-only posture.
    """
    if not read_only:
        return (), ""

    if client == "codex":
        if isolate and not mcp_injected:
            return ("sandbox", "mcp_absent"), "project trw MCP not loaded under --ignore-user-config"
        if isolate and not posture_enforced:
            return ("sandbox",), "a with_trw MCP server was injected under isolate; no reviewer role was rendered"
        if posture_enforced:
            return ("sandbox", "mcp_allowlist", "mcp_role"), ""
        return ("sandbox",), ""

    if client == "claude":
        # Claude's spec declares no sandbox and no read-only argv; headless -p denying edits is a permission
        # default, not a verified sandbox, so ``sandbox`` is never claimed for it (sol r1 P2).
        no_sandbox = "headless -p edit denial is a permission default, not a verified sandbox"
        if isolate and not mcp_injected:
            return ("mcp_absent",), f"no MCP servers under empty --mcp-config; {no_sandbox}"
        if isolate:
            return (), f"an MCP server was injected under isolate (reviewer or with_trw); {no_sandbox}"
        return (), "claude non-isolate enforcement layers unmeasured: not in the FR08 probe table"

    if client == "agy":
        # Re-measured 2026-09-26 on agy 1.2.11: headless agy DOES spawn its global `trw` server
        # (the 2026-09-16 "spawns no MCP server" row was stale). The read-only child carries
        # TRW_SURFACE_ROLE=reviewer (read_only_env), which that server inherits.
        return (
            ("mcp_role",),
            "agy's global trw MCP server inherits TRW_SURFACE_ROLE=reviewer from the read-only child "
            "(measured agy 1.2.11; holds while that mcp_config entry sets no TRW_SURFACE_ROLE); file "
            "writes are denied only by the host wrapper, reported per run in sandbox_verified",
        )

    if client == "grok":
        # Measured grok 1.0.34 2026-09-26: grok loads the project's .grok/config.toml trw server
        # (no isolation argv) and that server inherits the read-only child's TRW_SURFACE_ROLE.
        return (
            ("mcp_role",),
            "grok's project trw MCP server inherits TRW_SURFACE_ROLE=reviewer from the read-only child "
            "(measured grok 1.0.34; holds while .grok/config.toml sets no TRW_SURFACE_ROLE); file writes "
            "are denied by --permission-mode dontAsk, which admits only MCPTool(trw__*)",
        )

    if client == "opencode":
        return (), "opencode lane unmeasured: no reachable model to probe"

    return (), f"enforcement layers unmeasured for client {client!r}: not in the FR08 probe table"


class EnforcementFields(TypedDict):
    """``DispatchResult`` keyword arguments for the FR08 report."""

    enforcement_layers: tuple[EnforcementLayer, ...]
    mcp_role_note: str


#: The report for a dispatch that never launched a child: nothing was enforced.
NO_CHILD_ENFORCEMENT: EnforcementFields = {
    "enforcement_layers": (),
    "mcp_role_note": "no child was launched; nothing was enforced",
}


def enforcement_fields(
    client: str, *, read_only: bool, isolate: bool, posture_enforced: bool, mcp_injected: bool
) -> EnforcementFields:
    """:func:`enforcement_report` as ``DispatchResult`` keyword arguments."""
    layers, note = enforcement_report(
        client, read_only=read_only, isolate=isolate, posture_enforced=posture_enforced, mcp_injected=mcp_injected
    )
    return {"enforcement_layers": layers, "mcp_role_note": note}
