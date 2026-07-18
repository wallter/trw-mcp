"""Runtime version-drift advisory middleware (PRD-CORE-215-FR02).

A long-lived MCP server process keeps serving the *booted* tool code even
after the installed ``trw-mcp`` package is upgraded on disk — silent drift that
has surfaced as phantom stale-tool behavior in four reflection ledgers. This
middleware surfaces that drift as a structured, **non-blocking** advisory on
tool results (the same annotation pattern as ``ceremony_hint``), so an operator
can schedule a deliberate restart instead of debugging phantom behavior.

Design (per PRD dual-review):
- **Narrow comparator**: booted ``trw_mcp.__version__`` (resolved once at import
  time = when the server booted) vs ``importlib.metadata.version("trw-mcp")``
  (the currently installed distribution). Deliberately NOT ``collect_version_status``
  — its pyproject-absent fallback can make booted and installed appear falsely
  identical in installed projects.
- **Cached**: the comparator is recomputed at most once per
  ``version_check_interval_seconds`` (typed config knob) so it never adds
  measurable hot-path latency.
- **Fail-open**: any metadata-lookup failure yields no advisory and a debug log —
  a diagnostic must never break a tool call (NFR02).
- **Low noise**: emitted once per drift value per session (RISK-003).
"""

from __future__ import annotations

__all__ = [
    "CURRENT_TRANSPORT_AUTHORITY",
    "CURRENT_TRANSPORT_INVARIANTS",
    "FORBIDDEN_PROXY_PATTERNS",
    "TransportAuthorityReport",
    "TransportAuthoritySurface",
    "TransportInvariant",
    "VersionDriftChecker",
    "VersionDriftMiddleware",
    "build_advisory",
    "load_current_transport_authority",
    "validate_transport_authority",
]

import importlib.metadata
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import structlog
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools import ToolResult
from mcp.types import CallToolRequestParams, TextContent

logger = structlog.get_logger(__name__)

_PACKAGE_NAME = "trw-mcp"

# Key added to a tool result's structured_content when drift is detected.
_ADVISORY_KEY = "version_drift"

# Operator remediation text. Restart stays an operator action because each
# stdio client runs its own server process and an unprompted restart would drop
# an in-flight session. Wording is stdio-accurate (PRD-CORE-215-FR07): it names
# only this client's own server process.
_ACTION_TEXT = "restart the trw-mcp server process when convenient; reconnect this client with /mcp after restart"

VersionLookup = Callable[[], "tuple[str, str] | None"]
Clock = Callable[[], float]


def _default_lookup() -> tuple[str, str] | None:
    """Return ``(booted, installed)`` trw-mcp versions, or None on failure.

    Fail-open: an ``importlib.metadata`` lookup failure (package not installed,
    corrupted metadata) yields None + a debug log rather than raising — a drift
    diagnostic must never break a tool call.
    """
    try:
        from trw_mcp import __version__ as booted

        installed = importlib.metadata.version(_PACKAGE_NAME)
    except Exception:  # justified: fail-open -- version diagnostic must never break a tool call
        logger.debug(
            "version_drift_lookup_failed",
            component="version_drift",
            op="lookup_versions",
            outcome="fail_open",
            exc_info=True,
        )
        return None
    if not isinstance(booted, str) or not isinstance(installed, str):
        return None
    return booted, installed


def build_advisory(booted: str, installed: str) -> dict[str, str]:
    """Build the structured drift advisory payload."""
    return {
        "booted_version": booted,
        "installed_version": installed,
        "action": _ACTION_TEXT,
    }


class VersionDriftChecker:
    """Cached booted-vs-installed comparator with per-session drift dedup.

    Separated from the middleware so it is unit-testable with an injected clock
    and version lookup (no MCP transport required).
    """

    def __init__(
        self,
        *,
        interval_seconds: float,
        clock: Clock = time.monotonic,
        lookup: VersionLookup = _default_lookup,
    ) -> None:
        self._interval = interval_seconds
        self._clock = clock
        self._lookup = lookup
        self._last_check: float | None = None
        # Cached advisory (drift present) or None (no drift / lookup failed).
        self._cached_advisory: dict[str, str] | None = None
        # (session_id, drift_value) pairs already surfaced this process.
        self._emitted: set[tuple[str, str]] = set()

    def _refresh(self) -> None:
        """Recompute the cached drift verdict if the interval has elapsed."""
        now = self._clock()
        if self._last_check is not None and (now - self._last_check) < self._interval:
            return
        self._last_check = now
        versions = self._lookup()
        if versions is None:
            self._cached_advisory = None
            return
        booted, installed = versions
        self._cached_advisory = None if booted == installed else build_advisory(booted, installed)

    def advisory_for(self, session_id: str) -> dict[str, str] | None:
        """Return the drift advisory for this session, or None.

        Returns the advisory at most once per (session, drift-value) pair so a
        drift that persists for days does not re-nag the same session.
        """
        self._refresh()
        advisory = self._cached_advisory
        if advisory is None:
            return None
        drift_value = f"{advisory['booted_version']}->{advisory['installed_version']}"
        key = (session_id, drift_value)
        if key in self._emitted:
            return None
        self._emitted.add(key)
        return advisory


def _attach_advisory(result: ToolResult, advisory: dict[str, str]) -> None:
    """Attach the advisory to a tool result (structured field + text block).

    Mirrors the ``ceremony_hint`` pattern: a structured field on
    ``structured_content`` for programmatic clients plus a human-readable
    TextContent block (non-JSON, so ResponseOptimizerMiddleware leaves it
    untouched) for clients that only read text.
    """
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and _ADVISORY_KEY not in structured:
        structured[_ADVISORY_KEY] = advisory

    content = getattr(result, "content", None)
    if isinstance(content, list):
        text = (
            f"version drift advisory: booted trw-mcp {advisory['booted_version']} "
            f"!= installed {advisory['installed_version']} — {advisory['action']}"
        )
        content.append(TextContent(type="text", text=text))


def _resolve_interval() -> float:
    """Resolve ``version_check_interval_seconds`` from config (fail-open to default)."""
    try:
        from trw_mcp.models.config import get_config

        return float(get_config().version_check_interval_seconds)
    except Exception:  # justified: fail-open -- fall back to the typed model default
        from trw_mcp.models.config import TRWConfig

        return float(TRWConfig().version_check_interval_seconds)


class VersionDriftMiddleware(Middleware):
    """FastMCP middleware that annotates tool results with a version-drift advisory.

    Advisory only — never blocks, never mutates the tool's own payload beyond
    adding the ``version_drift`` field. Runs its check after the wrapped tool
    executes so a comparator failure can never prevent the call.
    """

    def __init__(self, checker: VersionDriftChecker | None = None) -> None:
        self._checker = checker if checker is not None else VersionDriftChecker(interval_seconds=_resolve_interval())

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Execute the tool, then attach a drift advisory when versions diverge."""
        result = await call_next(context)

        ctx = context.fastmcp_context
        if ctx is None or ctx.request_context is None:
            return result

        try:
            advisory = self._checker.advisory_for(ctx.session_id)
            if advisory is not None:
                _attach_advisory(result, advisory)
                logger.debug(
                    "version_drift_advisory_attached",
                    component="version_drift",
                    op="attach_advisory",
                    session_id=ctx.session_id,
                    booted=advisory["booted_version"],
                    installed=advisory["installed_version"],
                )
        except Exception:  # justified: fail-open -- advisory must never break a tool call
            logger.debug(
                "version_drift_advisory_failed",
                component="version_drift",
                op="attach_advisory",
                outcome="fail_open",
                exc_info=True,
            )

        return result


# ---------------------------------------------------------------------------
# Current transport-authority inventory + stale-topology validator
# (PRD-CORE-215-FR07)
#
# A bounded inventory of the current-authority transport surfaces. The
# validator scans them for retired-topology production claims (stdio is the
# only executable transport) while EXEMPTING labelled-history records, and it
# asserts the live safeguards (version-drift advisory, retry-once-then-record-
# gap client guidance) remain PRESENT so retirement can never silently delete
# them.
# ---------------------------------------------------------------------------

# Repo root: .../trw-mcp/src/trw_mcp/middleware/version_drift.py -> parents[4].
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Line-level opt-out. version_drift.py is itself a current-authority surface, so
# the forbidden-pattern definitions below would self-match. Lines carrying this
# marker are dropped before scanning.
_SCAN_EXEMPT_MARKER = "trw:transport-scan-exempt"


@dataclass(frozen=True, slots=True)
class TransportAuthoritySurface:
    """A named current-authority transport surface (``path`` is repo-relative).

    ``historical`` marks a surface that lives in the labelled-history record
    (retired-topology PRDs/research). Historical surfaces are out of the current
    inventory and are never scanned, so their evidence is preserved verbatim.
    """

    name: str
    path: str
    role: str
    historical: bool = False


@dataclass(frozen=True, slots=True)
class TransportInvariant:
    """A live safeguard that must stay PRESENT in a current surface.

    Removing the marked behavior fails validation, so FR07 cannot silently drop
    the version-drift advisory or the retry-once-then-record-gap guidance.
    """

    name: str
    surface_path: str
    marker: str
    rationale: str


@dataclass(frozen=True, slots=True)
class _ProxyClaimPattern:
    """A forbidden retired-topology production-claim pattern."""

    name: str
    pattern: re.Pattern[str]


@dataclass(frozen=True, slots=True)
class TransportAuthorityReport:
    """Outcome of scanning the current transport-authority inventory."""

    ok: bool
    proxy_violations: tuple[str, ...]
    missing_invariants: tuple[str, ...]


CURRENT_TRANSPORT_AUTHORITY: tuple[TransportAuthoritySurface, ...] = (
    TransportAuthoritySurface(
        "server transport startup", "trw-mcp/src/trw_mcp/server/_transport.py", "boots the stdio transport"
    ),
    TransportAuthoritySurface(
        "session-start finalization",
        "trw-mcp/src/trw_mcp/tools/_ceremony_session_start_steps.py",
        "emits the stdio connection fingerprint",
    ),
    TransportAuthoritySurface(
        "ceremony middleware", "trw-mcp/src/trw_mcp/middleware/ceremony.py", "annotates ceremony tool results"
    ),
    TransportAuthoritySurface(
        "version-drift middleware",
        "trw-mcp/src/trw_mcp/middleware/version_drift.py",
        "surfaces booted-vs-installed drift",
    ),
    TransportAuthoritySurface(
        "tool registration", "trw-mcp/src/trw_mcp/server/_tools.py", "registers the MCP tool surface"
    ),
    TransportAuthoritySurface(
        "generated client integrations",
        "trw-mcp/src/trw_mcp/bootstrap/_client_integrations.py",
        "renders client integration + transport-loss guidance",
    ),
    TransportAuthoritySurface(
        "bundled agent retry-protocol fragment",
        "trw-mcp/src/trw_mcp/data/agents/_shared/mcp-retry-protocol.md",
        "single-source client retry-once-then-record-gap protocol injected into every bundled agent",
    ),
    TransportAuthoritySurface(
        "current operator documentation", "CLAUDE.md", "documents the stdio-only transport architecture"
    ),
)


CURRENT_TRANSPORT_INVARIANTS: tuple[TransportInvariant, ...] = (
    TransportInvariant(
        name="version_drift_advisory",
        surface_path="trw-mcp/src/trw_mcp/middleware/version_drift.py",
        marker="build_advisory",
        rationale="the live version-drift advisory stays observable",
    ),
    TransportInvariant(
        name="retry_once_record_gap",
        surface_path="trw-mcp/src/trw_mcp/bootstrap/_client_integrations.py",
        marker="record the gap",
        rationale="the retry-once-then-record-gap client guidance stays loud",
    ),
)


FORBIDDEN_PROXY_PATTERNS: tuple[_ProxyClaimPattern, ...] = (
    _ProxyClaimPattern("shared_server", re.compile(r"shared[-\s]server", re.IGNORECASE)),  # trw:transport-scan-exempt
    _ProxyClaimPattern("http_transport", re.compile(r"HTTP\s+transport", re.IGNORECASE)),  # trw:transport-scan-exempt
    _ProxyClaimPattern("port_8100", re.compile(r"port\s*8100|:8100\b")),  # trw:transport-scan-exempt
    _ProxyClaimPattern("mcp_transport_config", re.compile(r"mcp_transport")),  # trw:transport-scan-exempt
    _ProxyClaimPattern("proxy_reference", re.compile(r"\bprox(?:y|ies)\b", re.IGNORECASE)),  # trw:transport-scan-exempt
)


# Negation/retirement cues: a forbidden match preceded by one of these in the
# same clause is a denial or stdio affirmation (a clause that says the retired
# topology does not exist), not a current production claim.
_NEGATION_CUES: tuple[str, ...] = (
    "no ",
    "no-",
    "not ",
    "never",
    "without",
    "zero ",
    "there is no",
    "does not",
    "do not",
    "don't",
    "cannot",
    "can't",
    "deprecated",
    "removed",
    "retired",
    "no longer",
    "rather than",
    "instead of",
)

_SENTENCE_SPLIT = re.compile(r"[.;!?\n]")


def _strip_exempt_lines(text: str) -> str:
    """Drop validator-owned definition lines so scanning never self-matches."""
    return "\n".join(line for line in text.splitlines() if _SCAN_EXEMPT_MARKER not in line)


def _is_denial(clause: str, match_start: int) -> bool:
    """True when a negation/retirement cue precedes the match in the clause."""
    prefix = clause[:match_start].lower()
    return any(cue in prefix for cue in _NEGATION_CUES)


def scan_text_for_proxy_claims(text: str) -> tuple[str, ...]:
    """Return the names of forbidden patterns *asserted* (not denied) in text.

    A match inside a clause that also carries a negation/retirement cue before
    it is a stdio affirmation, not a production claim, and is not reported.
    """
    scannable = _strip_exempt_lines(text)
    found: list[str] = []
    for clause in _SENTENCE_SPLIT.split(scannable):
        for claim in FORBIDDEN_PROXY_PATTERNS:
            match = claim.pattern.search(clause)
            if match is not None and not _is_denial(clause, match.start()):
                found.append(claim.name)
    return tuple(dict.fromkeys(found))


def validate_transport_authority(
    surface_texts: Iterable[tuple[TransportAuthoritySurface, str]],
    *,
    invariants: tuple[TransportInvariant, ...] = CURRENT_TRANSPORT_INVARIANTS,
) -> TransportAuthorityReport:
    """Validate current-authority surfaces (FR07).

    Scans every *current* (non-historical) surface for retired-topology
    production claims and asserts each named live invariant remains present.
    Historical surfaces are skipped entirely — their evidence is preserved.
    """
    proxy_violations: list[str] = []
    present_text: dict[str, str] = {}
    for surface, text in surface_texts:
        if surface.historical:
            continue
        present_text[surface.path] = text
        proxy_violations.extend(f"{surface.path}: {claim}" for claim in scan_text_for_proxy_claims(text))

    missing: list[str] = []
    for inv in invariants:
        inv_text = present_text.get(inv.surface_path)
        if inv_text is None or inv.marker not in inv_text:
            missing.append(inv.name)

    return TransportAuthorityReport(
        ok=not proxy_violations and not missing,
        proxy_violations=tuple(proxy_violations),
        missing_invariants=tuple(missing),
    )


def load_current_transport_authority(
    surfaces: tuple[TransportAuthoritySurface, ...] = CURRENT_TRANSPORT_AUTHORITY,
    *,
    root: Path | None = None,
) -> list[tuple[TransportAuthoritySurface, str]]:
    """Read each inventory surface's text from disk (repo-root relative)."""
    base = root if root is not None else _REPO_ROOT
    return [(surface, (base / surface.path).read_text(encoding="utf-8")) for surface in surfaces]
