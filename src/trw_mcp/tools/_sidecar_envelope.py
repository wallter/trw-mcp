"""Sidecar envelope contract and validation, shared by exact and ancestor readers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from trw_mcp.tools._sidecar_pair import pair_from_two_builds, pair_stale_action

SCHEMA_VERSION_ACCEPTED: str = "risk-report-sidecar/v0"
DEFAULT_CACHE_DIR_REL: str = ".trw/distill/map-cache"
#: The only artifact an ancestor may answer for: it carries one hint per target.
ANCESTOR_ARTIFACT: str = "before-edit-batch"


SidecarEnvelopeStatus = Literal[
    "ok",
    "sidecar_missing",
    "sidecar_malformed",
    "schema_mismatch",
    "stale_sha",
    "tier_required",
]


@dataclass(frozen=True)
class SidecarLoadResult:
    """Outcome of a sidecar load + envelope validation."""

    payload: Any | None
    status: SidecarEnvelopeStatus
    action: str | None
    sidecar_path: str | None
    sidecar_sha: str | None
    #: The envelope's ``dirty_paths`` exactly as read (unvalidated), set only on ``ok``.
    dirty_paths: object = None


_NO_PRODUCER_ACTION = "No producer exists for this sidecar yet, so it cannot be generated (see DEFECT-LEDGER UF-011)."


def load_envelope(sidecar_path: Path) -> dict[str, Any] | None:
    """Read + JSON-parse sidecar; return None on missing/malformed."""
    if not sidecar_path.exists():
        return None
    try:
        parsed = json.loads(sidecar_path.read_text(encoding="utf-8"))
    # trw-fail-silent-allow: None is the documented "missing or malformed" result; the caller reports that status
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def load_sidecar_with_sha_check(
    sidecar_path: Path,
    *,
    expected_sha: str,
    cli_remediation: str | None,
    file_path_hint: str | None = None,
) -> SidecarLoadResult:
    """Load sidecar envelope + validate schema_version + SHA match.

    Returns ``SidecarLoadResult`` with payload populated only when all
    checks pass. NEVER raises.

    Args:
        sidecar_path: Path to the sidecar JSON.
        expected_sha: Current git HEAD SHA (caller verifies it matched).
        cli_remediation: Exact CLI to run to regenerate the sidecar, or None
            when no producer exists for this artifact. None is not a
            convenience default. `trw_entity_risk_map` (since REMOVED, UF-011) advertised a
            `trw-distill self-improve` subcommand that has never been
            registered, so every caller at every tier was told to run something
            that could not work (DEFECT-LEDGER UF-011). This parameter has to be
            able to say "there is nothing to run", or the only way to satisfy
            its type is to invent a command.

            Note the deliberate omission above: the missing subcommand is NOT
            named here. `wiring/checks/existence.py::_check_sidecar` decides a
            producer exists if any non-consumer source file under the search
            roots contains the contract's `producer_token`, so writing that
            token into this docstring silently cleared the CONSUMER_ORPHAN
            finding for it — prose disabling a truthfulness gate, which is the
            exact defect class the gate exists to catch.
        file_path_hint: Deprecated compatibility keyword; no longer needed for remediation.
    """
    _ = file_path_hint  # PRD-DIST-1988 compatibility; callers may still supply it.
    sidecar_path_str = str(sidecar_path)
    envelope = load_envelope(sidecar_path)
    if envelope is None:
        return SidecarLoadResult(
            payload=None,
            status="sidecar_missing",
            action=(f"Run: {cli_remediation}" if cli_remediation else _NO_PRODUCER_ACTION),
            sidecar_path=sidecar_path_str,
            sidecar_sha=expected_sha,
        )
    schema = envelope.get("schema_version")
    if schema != SCHEMA_VERSION_ACCEPTED:
        return SidecarLoadResult(
            payload=None,
            status="schema_mismatch",
            action=(
                f"Sidecar schema_version={schema!r}; expected "
                f"{SCHEMA_VERSION_ACCEPTED!r} — upgrade trw-distill or trw-mcp"
            ),
            sidecar_path=sidecar_path_str,
            sidecar_sha=expected_sha,
        )
    sidecar_sha = envelope.get("sha")
    stale: str | None = None
    if not isinstance(sidecar_sha, str) or sidecar_sha != expected_sha:
        stale = f"Sidecar SHA={sidecar_sha!r}; HEAD={expected_sha} — re-run with --persist-sidecar"
    elif pair_from_two_builds(sidecar_path, envelope, load_envelope):
        stale = pair_stale_action(expected_sha, cli_remediation, _NO_PRODUCER_ACTION)
    if stale is not None:
        return SidecarLoadResult(
            payload=None,
            status="stale_sha",
            action=stale,
            sidecar_path=sidecar_path_str,
            sidecar_sha=expected_sha,
        )
    payload = envelope.get("payload")
    if payload is None:
        return SidecarLoadResult(
            payload=None,
            status="sidecar_malformed",
            action=(
                f"Sidecar payload missing; re-run: {cli_remediation}"
                if cli_remediation
                else f"Sidecar payload missing. {_NO_PRODUCER_ACTION}"
            ),
            sidecar_path=sidecar_path_str,
            sidecar_sha=expected_sha,
        )
    return SidecarLoadResult(
        payload=payload,
        status="ok",
        action=None,
        sidecar_path=sidecar_path_str,
        sidecar_sha=expected_sha,
        dirty_paths=envelope.get("dirty_paths"),
    )
