# Parent facade: tools/_delivery_helpers.py
"""Reviewer-provenance classification + receipt helpers (PRD-CORE-213 FR01-FR03).

Sibling of ``_delivery_review_gate.py``; re-exported through
``_delivery_helpers.py`` so callers/tests keep a single import point. Pure file
reads only (``meta/review.yaml`` + ``meta/run.yaml``) — no network, no LLM.

Responsibilities:
- FR01 provenance stamp construction (``build_reviewer_block`` /
  ``derive_reviewer_source``) — a ``reviewer`` block recording who reviewed,
  derived honestly from the invoking mode (``manual -> self``, ``auto ->
  subagent``, ``cross_model -> cross_model``; ``operator`` only ever explicit).
- FR02 authoring-session detection (``classify_review_independence``) using only
  already-persisted ``run_id`` / ``owner_session_id`` identity.
- FR03 P0/P1 self-review cap (``review_receipt_satisfied``).

Fail-open contract: absent/empty identity fields classify as ``unknown`` — which
FR03 treats as NOT-satisfying the P0/P1 receipt requirement, but the DELIVER gate
(``_prd_transition_gate``) only WARNS on ``unknown`` and never hard-blocks on a
resolution failure (NFR02). This module raises only on the explicit
``operator``-without-receipt validation (FR01).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import structlog

from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)

Independence = Literal["independent", "asserted_independent", "self_same_session", "unknown"]

# Closed reviewer-source vocabulary (FR01).
ALLOWED_REVIEWER_SOURCES: tuple[str, ...] = ("self", "subagent", "cross_model", "operator")
# Sources that constitute an INDEPENDENT reviewer receipt (FR03).
INDEPENDENT_REVIEWER_SOURCES: frozenset[str] = frozenset({"subagent", "cross_model", "operator"})
# Priorities subject to the same-session self-review advisory cap (FR03).
CAPPED_PRIORITIES: frozenset[str] = frozenset({"P0", "P1"})


@dataclass(frozen=True)
class RunIdentity:
    """Already-persisted identity of a run (from ``run.yaml``). No new primitive."""

    run_id: str = ""
    session_id: str = ""


# ---------------------------------------------------------------------------
# FR01 — provenance stamp construction
# ---------------------------------------------------------------------------


def derive_reviewer_source(mode: str | None, explicit: str | None) -> str:
    """Resolve the reviewer ``source`` honestly from the effective review mode.

    ``explicit`` (a caller-supplied ``reviewer_source``) always wins and is
    validated against :data:`ALLOWED_REVIEWER_SOURCES`. When omitted, the source
    is derived from the mode: ``auto -> subagent``, ``cross_model -> cross_model``,
    everything else (``manual`` / reconcile / unknown) ``-> self``. ``operator`` is
    NEVER inferred — it must be passed explicitly (FR01).
    """
    if explicit is not None:
        normalized = str(explicit).strip().lower()
        if normalized not in ALLOWED_REVIEWER_SOURCES:
            raise ValueError(f"invalid reviewer_source {explicit!r}; allowed: {', '.join(ALLOWED_REVIEWER_SOURCES)}")
        return normalized
    effective = (mode or "manual").strip().lower()
    if effective == "auto":
        return "subagent"
    if effective == "cross_model":
        return "cross_model"
    return "self"


def read_run_identity(resolved_run: Path | None, reader: FileStateReader | None = None) -> RunIdentity:
    """Read ``run_id`` / ``owner_session_id`` from a run's ``meta/run.yaml``.

    Returns empty identity fields when there is no active run or the file is
    absent/unreadable — the honest signal that identity could not be resolved.
    """
    if resolved_run is None:
        return RunIdentity()
    reader = reader or FileStateReader()
    run_yaml = resolved_run / "meta" / "run.yaml"
    if not run_yaml.exists():
        return RunIdentity()
    try:
        data = reader.read_yaml(run_yaml)
    except Exception:  # justified: unreadable run.yaml -> empty identity (fail-open, NFR02)
        logger.debug("reviewer_run_identity_unreadable", run=str(resolved_run), exc_info=True)
        return RunIdentity()
    if not isinstance(data, dict):
        return RunIdentity()
    return RunIdentity(
        run_id=str(data.get("run_id", "") or ""),
        session_id=str(data.get("owner_session_id", "") or ""),
    )


# Back-compat / gate alias — the delivering run's identity is read the same way.
load_delivering_run_identity = read_run_identity


def resolve_verified_reviewer_identity(
    claimed_run_id: str | None,
    claimed_session_id: str | None,
    delivering: RunIdentity,
    *,
    runs_root: Path | None,
    pins_path: Path | None,
    reader: FileStateReader | None = None,
) -> RunIdentity | None:
    """Verify a caller-claimed reviewer identity against framework-recorded state (OQ-001).

    A caller-supplied identity alone would be a self-mintable receipt — the exact
    trap this module exists to prevent — so a claim only becomes a verified
    identity when it is anchored to state the framework already recorded:

    - ``claimed_run_id`` must name an existing run under *runs_root* whose
      persisted ``meta/run.yaml`` ``run_id`` matches the claim AND differs from
      the delivering run's ``run_id``.
    - ``claimed_session_id`` must be a registered pin key in *pins_path*
      (``.trw/runtime/pins.json``) that differs from the delivering run's
      ``owner_session_id``; a pin whose run resolves back to the delivering run
      is rejected (a second session attached to the same run is not independent).
    - When both are claimed, both must verify and must be mutually consistent
      with the recorded run.yaml.

    Returns the framework-recorded :class:`RunIdentity` on success, or ``None``
    when the claim cannot be verified — the caller then falls back to stamping
    the delivering run's identity, so an unverifiable claim classifies no better
    than ``asserted_independent``. Fail-closed: any read error returns ``None``.
    """
    run_claim = str(claimed_run_id or "").strip()
    session_claim = str(claimed_session_id or "").strip()
    if not run_claim and not session_claim:
        return None
    reader = reader or FileStateReader()

    try:
        if run_claim:
            # trw:intentional path-shaped run_id claims are rejected before any glob (traversal guard)
            if any(sep in run_claim for sep in ("/", "\\", "..")):
                return None
            if runs_root is None or not runs_root.is_dir():
                return None
            recorded: RunIdentity | None = None
            for run_yaml in sorted(runs_root.glob(f"**/{run_claim}/meta/run.yaml")):
                candidate = read_run_identity(run_yaml.parent.parent, reader)
                if candidate.run_id == run_claim:
                    recorded = candidate
                    break
            if recorded is None:
                return None
            if not delivering.run_id or recorded.run_id == delivering.run_id:
                return None
            if session_claim:
                if recorded.session_id and recorded.session_id != session_claim:
                    return None
                if not recorded.session_id and not _pin_registered(pins_path, session_claim):
                    return None
            return RunIdentity(run_id=recorded.run_id, session_id=recorded.session_id or session_claim)

        # Session-only claim: anchor to the pin store.
        if not _pin_registered(pins_path, session_claim):
            return None
        if not delivering.session_id or session_claim == delivering.session_id:
            return None
        pinned_run = _pinned_run_identity(pins_path, session_claim, reader)
        if pinned_run is not None and delivering.run_id and pinned_run.run_id == delivering.run_id:
            return None
        return RunIdentity(
            run_id=pinned_run.run_id if pinned_run is not None else "",
            session_id=session_claim,
        )
    except Exception:  # justified: verification must fail closed, never invent an identity
        logger.debug("reviewer_identity_verification_failed", exc_info=True)
        return None


def _load_pins(pins_path: Path | None) -> dict[str, object]:
    if pins_path is None or not pins_path.is_file():
        return {}
    try:
        data = json.loads(pins_path.read_text(encoding="utf-8"))
    except Exception:  # justified: unreadable pin store -> no verification possible
        logger.debug("reviewer_identity_pins_unreadable", pins=str(pins_path), exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def _pin_registered(pins_path: Path | None, session_id: str) -> bool:
    return session_id in _load_pins(pins_path)


def _pinned_run_identity(pins_path: Path | None, session_id: str, reader: FileStateReader) -> RunIdentity | None:
    entry = _load_pins(pins_path).get(session_id)
    if not isinstance(entry, dict):
        return None
    run_path = str(entry.get("run_path", "") or "")
    if not run_path:
        return None
    identity = read_run_identity(Path(run_path), reader)
    return identity if identity.run_id else None


def build_reviewer_block(
    resolved_run: Path | None,
    reader: FileStateReader | None,
    *,
    source: str,
    receipt_id: str | None = None,
    ts: str | None = None,
    verified_identity: RunIdentity | None = None,
) -> dict[str, object]:
    """Build the ``reviewer`` provenance block persisted onto ``review.yaml`` (FR01).

    ``run_id`` / ``session_id`` come from ``run.yaml`` (no invented primitive),
    unless a *verified_identity* is supplied — an identity that
    :func:`resolve_verified_reviewer_identity` already anchored to
    framework-recorded state (OQ-001). Only that resolver may produce one;
    the block then carries ``identity_verified: true`` so downstream readers can
    distinguish a framework-verified reviewer identity from the default
    delivering-run stamp. A ``receipt_id`` token is present only for independent
    sources; an explicit ``operator`` source REQUIRES a non-empty ``receipt_id``
    or this raises ``ValueError`` (FR01 — the only raising path in this module).
    Records no secrets / diff bodies (NFR04).
    """
    if source == "operator" and not (receipt_id and str(receipt_id).strip()):
        raise ValueError("reviewer_source='operator' requires a non-empty receipt_id (operator sign-off token)")
    identity = verified_identity if verified_identity is not None else read_run_identity(resolved_run, reader)
    stamp = ts or datetime.now(timezone.utc).isoformat()
    block: dict[str, object] = {
        "source": source,
        "run_id": identity.run_id,
        "session_id": identity.session_id,
        "ts": stamp,
    }
    if verified_identity is not None:
        block["identity_verified"] = True
    if source in INDEPENDENT_REVIEWER_SOURCES:
        block["receipt_id"] = (
            str(receipt_id).strip() if (receipt_id and str(receipt_id).strip()) else secrets.token_hex(8)
        )
    return block


def ensure_reviewer_block(
    review_data: dict[str, object],
    resolved_run: Path | None,
    reader: FileStateReader | None = None,
    verified_identity: RunIdentity | None = None,
) -> None:
    """Inject a mode-derived ``reviewer`` block into ``review_data`` if absent.

    Central stamping point so EVERY review mode (manual/auto/cross_model) gets a
    provenance stamp without each handler needing to build one. Manual mode stamps
    its own block (with any explicit ``reviewer_source``) before persisting, so
    this is a no-op there. *verified_identity* (OQ-001) must come from
    :func:`resolve_verified_reviewer_identity`. Fail-open: any construction error
    leaves the payload unstamped rather than blocking the review write.
    """
    if "reviewer" in review_data:
        return
    try:
        mode = str(review_data.get("mode", "")) or "manual"
        source = derive_reviewer_source(mode, None)
        ts = str(review_data.get("timestamp", "")) or None
        review_data["reviewer"] = build_reviewer_block(
            resolved_run, reader, source=source, ts=ts, verified_identity=verified_identity
        )
    except Exception:  # justified: provenance stamping must never block the review artifact write
        logger.warning("reviewer_block_injection_failed", exc_info=True)


# ---------------------------------------------------------------------------
# FR02 — authoring-session detection
# ---------------------------------------------------------------------------


def _reviewer_block(review_data: dict[str, object]) -> dict[str, object] | None:
    reviewer = review_data.get("reviewer") if isinstance(review_data, dict) else None
    return reviewer if isinstance(reviewer, dict) else None


def _identity_differs(reviewer: dict[str, object], delivering_run: RunIdentity) -> bool | None:
    """Compare recorded review identity to the delivering run.

    Returns True/False when a comparison is possible on either the ``run_id`` or
    (``run_id`` empty) the ``session_id`` axis; None when neither axis is present
    on both sides so no honest comparison can be made.
    """
    review_run = str(reviewer.get("run_id", "") or "")
    review_session = str(reviewer.get("session_id", "") or "")
    if review_run and delivering_run.run_id:
        return review_run != delivering_run.run_id
    if review_session and delivering_run.session_id:
        return review_session != delivering_run.session_id
    return None


def classify_review_independence(
    review_data: dict[str, object],
    delivering_run: RunIdentity,
) -> Independence:
    """Classify a review's independence relative to the delivering run (FR02, OQ-001).

    The receipt must not be self-mintable (the exact incident this PRD stops): a
    caller-supplied ``reviewer.source`` alone is NOT proof of independence, so the
    classifier compares recorded identity against the delivering run.

    - ``independent`` (VERIFIABLE): ``subagent``/``cross_model`` whose recorded
      identity is present AND differs from the delivering run; OR ``operator``
      carrying a ``receipt_id``; OR a ``self`` review from a demonstrably
      different run.
    - ``asserted_independent`` (TRUST-BASED): ``subagent``/``cross_model`` that
      lacks a distinct verifiable identity (same run_id as the deliverer, or no
      comparable identity). Accepted only under warn mode; block mode rejects it.
    - ``self_same_session``: ``source == 'self'`` AND identity matches the
      delivering run — the self-certification incident class.
    - ``unknown``: no reviewer block, or ``self``/``operator`` with no way to
      resolve identity (operator without a receipt). Warns, never hard-blocks.
    """
    reviewer = _reviewer_block(review_data)
    if reviewer is None:
        return "unknown"
    source = str(reviewer.get("source", "")).strip().lower()

    if source == "operator":
        receipt = str(reviewer.get("receipt_id", "") or "").strip()
        return "independent" if receipt else "unknown"

    if source in {"subagent", "cross_model"}:
        differs = _identity_differs(reviewer, delivering_run)
        # Distinct, verifiable identity -> genuinely independent. Same identity or
        # no comparable identity -> trust-based assertion only (OQ-001 subagents
        # share the parent run_id, so 'source=subagent' is not self-evidently
        # independent).
        return "independent" if differs is True else "asserted_independent"

    if source == "self":
        differs = _identity_differs(reviewer, delivering_run)
        if differs is None:
            return "unknown"
        return "independent" if differs else "self_same_session"

    return "unknown"


# ---------------------------------------------------------------------------
# FR03 — P0/P1 self-review cap; independent receipt requirement
# ---------------------------------------------------------------------------


def review_receipt_satisfied(
    prd_priority: str,
    review_data: dict[str, object],
    delivering_run: RunIdentity,
    *,
    gate_mode: str = "block",
) -> bool:
    """Is the independent-reviewer-receipt requirement satisfied for a transition?

    For non-P0/P1 PRDs the cap does not apply -> always True. For P0/P1 PRDs:
      - ``independent`` (verifiable) always satisfies.
      - ``asserted_independent`` (trust-based) satisfies ONLY under warn mode; a
        ``block``-mode certification requires verifiable independence (OQ-001
        resolution — block=verifiable, warn=asserted).
      - ``self_same_session`` and ``unknown`` never satisfy (the gate treats an
        ``unknown``-only shortfall as advisory, but as a receipt predicate it is
        not satisfied).
    """
    priority = str(prd_priority).strip().upper()
    if priority not in CAPPED_PRIORITIES:
        return True
    classification = classify_review_independence(review_data, delivering_run)
    if classification == "independent":
        return True
    if classification == "asserted_independent":
        return gate_mode != "block"
    return False
