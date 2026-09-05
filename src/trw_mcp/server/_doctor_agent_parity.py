"""``agent_parity`` doctor check (PRD-CORE-252-FR05).

Belongs to the ``_subcommands_doctor.py`` catalogue. Registered there in
``_CHECKS``; this module owns the comparison so the catalogue module stays
under the size gate.

Why the check exists
--------------------
TRW ships a set of specialist agents and installs them per client. Before this
they reached one harness, and nothing anywhere reported the shortfall: a user on
Cursor or Codex saw a clean ``doctor`` run while seven of the specialists the
documentation promises were simply absent. Capability gaps that produce no
signal do not get fixed, they get rediscovered.

The expected set is the bundled agents directory, read at call time. No
per-client count is stated here — a literal would be census data that drifts the
moment a twelfth agent lands (PRD-CORE-252-FR06).

Verdict semantics: PASS when every agent-capable selected client holds the full
set; WARN, naming the missing agents and their client, when any is short; SKIP
when no selected client has an agent surface. Never FAIL — a missing agent
degrades capability but does not break the install, and the doctor exit code is
reserved for FAIL.

PASS is non-vacuous by construction. Both inputs — the bundled roster and the
selected clients — can be UNAVAILABLE rather than empty, and each returns a WARN
that names the reason. Without that split a missing bundle produced
a vacuous ``PASS`` over an empty roster and an unreadable project config
substituted the default client, so doctor passed a surface it had never
measured. A check that cannot see its subject reports that it cannot see it.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["AgentParityRow", "agent_parity_report"]


class AgentParityRow(dict[str, object]):
    """One client's agent parity, as a JSON-serializable row.

    A dict subclass rather than a model because the doctor's ``--format json``
    payload is built with ``json.dumps`` and every other check row is plain
    data. Keys: ``client``, ``installed``, ``expected``, ``missing``,
    ``supported``, and ``destination``.
    """


def _selected_clients(target: Path) -> tuple[list[str], str | None]:
    """Clients this project installed for, plus the reason they are unknown.

    Read from the project's own ``target_platforms`` rather than from raw
    detection: detection reports claude-code for any tree containing
    ``.claude/``, which TRW creates for every client.

    Returns ``(clients, unreadable_reason)``. A config that EXISTS but cannot be
    read yields ``([], "<ErrorClass>: <path>")``: substituting the default
    ``target_platforms`` there measured whatever client the default names and
    reported the verdict as though it described the one the user actually
    selected. A project with no config at all is not a failure — it has not
    chosen, so the packaged default genuinely applies.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader

    config_path = target / ".trw" / "config.yaml"
    if config_path.is_file():
        try:
            data = FileStateReader().read_yaml(config_path)
        except Exception as exc:  # justified: reported as not-measured, never substituted
            logger.warning("agent_parity_config_unreadable", path=str(config_path), exc_info=True)
            return [], f"{type(exc).__name__} reading {config_path}"
        if isinstance(data, dict):
            platforms = data.get("target_platforms")
            if isinstance(platforms, list) and platforms:
                return [str(item) for item in platforms], None
    return list(TRWConfig().target_platforms) or ["claude-code"], None


def _bundled_agent_stems() -> list[str] | None:
    """Stems of the agents that ship in the wheel, or ``None`` when the bundle is gone.

    ``None`` and ``[]`` are different facts and used to be the same value: with
    the directory missing, ``expected`` was empty, every client trivially held
    all of it, and doctor reported a vacuous ``PASS`` over an empty roster — a
    broken install certified as complete. Only a readable directory can produce
    a roster, and an EMPTY readable directory is not a roster worth comparing
    against either, so both collapse to "not measured" at the caller.
    """
    from trw_mcp.bootstrap._utils import _DATA_DIR

    source = _DATA_DIR / "agents"
    if not source.is_dir():
        return None
    stems = sorted(path.stem for path in source.glob("*.md"))
    return stems or None


def agent_parity_report(target: Path) -> tuple[str, str, list[AgentParityRow]]:
    """Compare installed agents against the bundle for every selected client.

    Args:
        target: Root of the project being diagnosed.

    Returns:
        ``(status, message, rows)`` where status is ``PASS``, ``WARN`` or
        ``SKIP`` and *rows* carries the per-client installed and expected counts
        for the machine-readable output.
    """
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.exceptions import AgentFormatError

    expected = _bundled_agent_stems()
    if expected is None:
        from trw_mcp.bootstrap._utils import _DATA_DIR

        return (
            "WARN",
            f"agent parity NOT MEASURED: no bundled agents found at {_DATA_DIR / 'agents'}; "
            "reinstall trw-mcp. A missing bundle cannot certify any client as complete.",
            [AgentParityRow(client="*", supported=False, installed=0, expected=0, reason="bundle_unavailable")],
        )

    clients, unreadable_reason = _selected_clients(target)
    if unreadable_reason is not None:
        return (
            "WARN",
            f"agent parity NOT MEASURED: {unreadable_reason} — the selected clients are unknown, "
            "so no client's agent surface was checked.",
            [
                AgentParityRow(
                    client="*", supported=False, installed=0, expected=len(expected), reason="config_unreadable"
                )
            ],
        )

    rows: list[AgentParityRow] = []
    shortfalls: list[str] = []
    unsupported: list[str] = []

    for client in dict.fromkeys(clients):
        try:
            fmt = agent_format_for(client)
        except AgentFormatError:
            # Not a registered client id. Recorded, not silently dropped, so a
            # stale target_platforms entry is visible rather than invisible.
            rows.append(AgentParityRow(client=client, supported=False, installed=0, expected=len(expected)))
            unsupported.append(client)
            continue
        if not fmt.supports_agents or fmt.destination_dir is None:
            rows.append(
                AgentParityRow(
                    client=client,
                    supported=False,
                    installed=0,
                    expected=len(expected),
                    reason=fmt.unsupported_reason,
                )
            )
            unsupported.append(client)
            continue

        dest = target / fmt.destination_dir
        # A user-authored agent in the destination is ignored rather than
        # reported as surplus: the check answers "is anything TRW ships
        # missing", not "is anything here unfamiliar".
        missing = [stem for stem in expected if not (dest / f"{stem}{fmt.filename_suffix}").is_file()]
        rows.append(
            AgentParityRow(
                client=client,
                supported=True,
                destination=fmt.destination_dir,
                installed=len(expected) - len(missing),
                expected=len(expected),
                missing=missing,
            )
        )
        if missing:
            shortfalls.append(f"{client} is missing {', '.join(missing)} from {fmt.destination_dir}/")

    if not any(row.get("supported") for row in rows):
        named = ", ".join(unsupported) or "no client"
        return "SKIP", f"{named} has no agent surface; no agents are expected on disk.", rows
    if shortfalls:
        return "WARN", "; ".join(shortfalls) + ". Run 'trw-mcp update-project' to reinstall them.", rows
    installed_clients = ", ".join(str(row["client"]) for row in rows if row.get("supported"))
    return "PASS", f"all {len(expected)} bundled agents present for {installed_clients}.", rows
