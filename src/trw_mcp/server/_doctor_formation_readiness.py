"""``formation_readiness`` doctor check (PRD-CORE-266-FR06).

Belongs to the ``_subcommands_doctor.py`` catalogue. Registered there in
``_CHECKS``; this module owns the record construction and the bounded version
probe so the catalogue module stays under the size gate.

Why the check exists
--------------------
An operator assembling a multi-harness formation had no way to ask TRW which of
the clients it can dispatch to are actually installed, which of them TRW has
genuinely verified, and which of them it merely believes things about. The
dispatch layer emitted no doctor row at all and contained no version probe
anywhere. Capability gaps that produce no signal do not get fixed; they get
rediscovered — the same reasoning recorded in ``_doctor_agent_parity.py``.

Everything here is DERIVED. The capability columns come from the client-spec
registry, so a row cannot disagree with the data the argv builder uses; only
``binary_resolved`` and ``version`` are measured live, and both are measured for
this run rather than remembered.

Verdict discipline
------------------
Precedence, highest first:

``unverified``   the registry entry's verification method is ``unverified``.
                 This wins even when the binary is present and answers: an
                 available binary is evidence the CLI exists, never evidence
                 that TRW understands its sandbox or output semantics.
``binary_absent`` no documented spelling of the executable resolves on PATH.
``not_measured``  the probe raised, timed out, or exited non-zero. The reason is
                 recorded verbatim; no version is claimed.
``ready``         a verified entry whose binary answered its own version probe.

There is no fifth verdict and no default: every path above assigns one
explicitly, so a client can never fall through to ``ready`` because a branch was
missed. The check status is PASS, WARN or SKIP and never FAIL — a missing client
degrades capability without breaking the install, and the doctor's exit code is
reserved for FAIL.

Credential hygiene: the probe runs with the base environment allowlist only
(:func:`build_probe_env`), never the per-client credential set. Printing a
version banner needs no API key, and the record carries the probe argv and the
probe's own output — never an environment value.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import structlog

from trw_mcp.dispatch._client_specs import ClientSpec, UnknownClientError, client_spec_for
from trw_mcp.dispatch._env import build_probe_env

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

__all__ = ["ReadinessRow", "formation_readiness_report"]

#: Upper bound on the version banner copied into a record. A CLI that prints its
#: whole help text on ``--version`` must not push kilobytes into the doctor's
#: json payload; the first line past this length is truncated, and truncation is
#: visible rather than silent.
_MAX_VERSION_CHARS = 200


class ReadinessRow(dict[str, object]):
    """One client's formation readiness, as a JSON-serializable row.

    A ``dict`` subclass rather than a model because the doctor's ``--format
    json`` payload is built with ``json.dumps`` and every other check row is
    plain data — the same choice ``AgentParityRow`` makes.
    """


def _resolve_binary(spec: ClientSpec) -> tuple[str | None, str | None]:
    """Return ``(name_that_answered, absolute_path)`` for the first spelling on PATH.

    Tries ``binary`` then each alias in order and reports WHICH one resolved, so
    an ambiguous vendor rename (cursor-cli's ``agent`` vs ``cursor-agent``) is
    settled by observation in the row rather than guessed in the registry.
    """
    for name in spec.binary_names:
        path = shutil.which(name)
        if path is not None:
            return name, path
    return None, None


def _probe_version(binary: str, spec: ClientSpec, timeout_s: int) -> tuple[str | None, str | None]:
    """Run the entry's own version argv. Returns ``(version, failure_reason)``.

    Exactly one of the two is non-None. Every failure mode — binary vanished
    between the PATH lookup and the call, timeout, non-zero exit, empty output —
    returns a NAMED reason rather than an empty version that a reader could
    mistake for "no version needed".
    """
    argv = [binary, *spec.version_argv]
    try:
        completed = subprocess.run(  # noqa: S603 - argv is registry data, never caller input
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=build_probe_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"probe exceeded the configured timeout of {timeout_s}s"
    except OSError as exc:
        return None, f"probe could not be executed: {exc}"
    if completed.returncode != 0:
        return None, f"probe exited {completed.returncode}"
    banner = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
    if not banner:
        banner = next((line.strip() for line in completed.stderr.splitlines() if line.strip()), "")
    if not banner:
        return None, "probe exited 0 but printed nothing"
    if len(banner) > _MAX_VERSION_CHARS:
        banner = banner[:_MAX_VERSION_CHARS] + "... (truncated)"
    return banner, None


def _readiness_row(client: str, timeout_s: int) -> ReadinessRow:
    """Build one client's record, assigning exactly one verdict."""
    try:
        spec = client_spec_for(client)
    except UnknownClientError:
        # An enabled client id with no registry entry. Reported, never skipped:
        # a config naming a client TRW cannot describe is exactly the state an
        # operator needs to see.
        return ReadinessRow(client=client, verdict="not_registered", reason="no registry entry for this client id")

    row = ReadinessRow(
        client=client,
        verification=spec.verification.method,
        verified_at=spec.verification.verified_at.isoformat(),
        sub_agents=spec.sub_agents,
        sandbox=spec.sandbox,
        headless_json=spec.headless_json,
        agent_surface=spec.agent_surface,
        instruction_files=list(spec.instruction_files),
        binary_candidates=list(spec.binary_names),
    )
    if spec.verification.method == "unverified":
        # Deliberately BEFORE the PATH lookup: an unverified entry's readiness
        # question is not "is it installed" but "does TRW know how to drive it",
        # and probing would invite a reader to treat a version string as an
        # answer to the second question.
        row["verdict"] = "unverified"
        row["reason"] = spec.verification.outstanding
        return row

    resolved, path = _resolve_binary(spec)
    if resolved is None:
        row["verdict"] = "binary_absent"
        row["reason"] = f"none of {list(spec.binary_names)} resolves on PATH"
        return row

    row["binary_resolved"] = resolved
    row["binary_path"] = path
    row["probe_argv"] = [resolved, *spec.version_argv]
    version, failure = _probe_version(resolved, spec, timeout_s)
    if failure is not None:
        row["verdict"] = "not_measured"
        row["reason"] = failure
        return row
    row["version"] = version
    row["verdict"] = "ready"
    return row


def formation_readiness_report(config: TRWConfig) -> tuple[str, str, list[ReadinessRow]]:
    """Report per-client dispatch readiness for every enabled client.

    Args:
        config: Live configuration; supplies the enabled-client list and the
            per-probe timeout.

    Returns:
        ``(status, message, rows)`` where status is ``PASS`` (at least one
        enabled client is ready), ``WARN`` (none is) or ``SKIP`` (none is
        enabled) — never ``FAIL``.
    """
    dispatch_cfg = config.dispatch
    enabled = list(dispatch_cfg.dispatch_enabled_clients)
    if not enabled:
        return (
            "SKIP",
            "formation readiness NOT MEASURED: dispatch.enabled_clients is empty, so no client was checked.",
            [],
        )

    timeout_s = int(dispatch_cfg.dispatch_version_probe_timeout_s)
    rows = [_readiness_row(client, timeout_s) for client in enabled]
    ready = [row for row in rows if row.get("verdict") == "ready"]
    logger.info(
        "doctor_formation_readiness",
        outcome="measured",
        clients=len(rows),
        ready=len(ready),
        timeout_s=timeout_s,
    )
    # Group the shortfall by verdict so the human line names WHAT is short, not
    # merely how many rows fell short. It is reported on PASS too — a per-client
    # gap stays visible even when the capability as a whole works.
    detail = ", ".join(f"{row['client']}={row.get('verdict')}" for row in rows if row.get("verdict") != "ready")
    summary = f"{len(ready)} of {len(rows)} dispatch clients ready"
    if detail:
        summary = f"{summary} ({detail})"

    # PASS means the CAPABILITY works: at least one enabled client can be
    # dispatched to. WARN is reserved for zero — the state in which no dispatch
    # is possible at all.
    #
    # Warning on any shortfall was the obvious design and is wrong here. Almost
    # nobody installs every supported CLI, and the registry deliberately holds a
    # permanently `unverified` entry, so "WARN unless all seven are ready" would
    # make PASS unreachable for every real install. A row that always warns is a
    # row operators learn to ignore, which destroys the signal the check exists
    # to carry — the per-client verdicts below stay exact either way.
    if ready:
        return ("PASS", summary, rows)
    return ("WARN", f"{summary}: no dispatch target is usable", rows)
