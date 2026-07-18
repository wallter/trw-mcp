"""Runtime version-drift advisory tests (PRD-CORE-215-FR02).

Covers the four acceptance behaviors:
- drift -> advisory attached once per session (structured field + text block)
- no drift -> no advisory
- metadata lookup failure -> no advisory, no crash (fail-open)
- interval caching honored (injected fake clock; lookup recomputed at most once
  per version_check_interval_seconds)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.types import TextContent

from trw_mcp.middleware.version_drift import (
    VersionDriftChecker,
    VersionDriftMiddleware,
    build_advisory,
)


class _FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _CountingLookup:
    """Version lookup stub that records how many times it was invoked."""

    def __init__(self, value: tuple[str, str] | None) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> tuple[str, str] | None:
        self.calls += 1
        return self.value


@dataclass
class _FakeRequestContext:
    session_id: str = "sess-1"


@dataclass
class _FakeContext:
    request_context: _FakeRequestContext | None = None

    @property
    def session_id(self) -> str:
        if self.request_context is None:
            raise RuntimeError("no request context")
        return self.request_context.session_id


@dataclass
class _FakeMessage:
    name: str = "trw_status"


@dataclass
class _FakeMiddlewareContext:
    message: _FakeMessage
    fastmcp_context: _FakeContext | None = None


@dataclass
class _FakeToolResult:
    """ToolResult stub with the two surfaces the advisory annotates."""

    content: list[Any] = field(default_factory=list)
    structured_content: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# VersionDriftChecker unit behavior
# --------------------------------------------------------------------------- #


def test_drift_yields_advisory_once_per_session() -> None:
    """Drift surfaces exactly once for a given session + drift value."""
    checker = VersionDriftChecker(
        interval_seconds=300.0,
        clock=_FakeClock(),
        lookup=_CountingLookup(("0.55.0", "0.56.0")),
    )

    first = checker.advisory_for("sess-1")
    assert first == build_advisory("0.55.0", "0.56.0")
    assert first["booted_version"] == "0.55.0"
    assert first["installed_version"] == "0.56.0"
    # FR07: remediation text is stdio-accurate (no shared-server/proxy topology).
    assert "restart the trw-mcp server" in first["action"]

    # Same session, same drift value -> suppressed (RISK-003 noise control).
    assert checker.advisory_for("sess-1") is None

    # A different session still gets the advisory once.
    assert checker.advisory_for("sess-2") == build_advisory("0.55.0", "0.56.0")


def test_no_drift_yields_no_advisory() -> None:
    """Matching booted/installed versions produce no advisory."""
    checker = VersionDriftChecker(
        interval_seconds=300.0,
        clock=_FakeClock(),
        lookup=_CountingLookup(("0.55.0", "0.55.0")),
    )
    assert checker.advisory_for("sess-1") is None
    assert checker.advisory_for("sess-2") is None


def test_lookup_failure_yields_no_advisory() -> None:
    """A None lookup (metadata failure) fails open: no advisory, no raise."""
    checker = VersionDriftChecker(
        interval_seconds=300.0,
        clock=_FakeClock(),
        lookup=_CountingLookup(None),
    )
    assert checker.advisory_for("sess-1") is None


def test_lookup_exception_fails_open() -> None:
    """A raising lookup must not propagate out of advisory_for path via middleware."""

    def _boom() -> tuple[str, str] | None:
        raise RuntimeError("metadata blew up")

    checker = VersionDriftChecker(interval_seconds=300.0, clock=_FakeClock(), lookup=_boom)
    # The checker itself does not swallow — the middleware does. Assert the raise
    # is contained by the middleware layer (see test_middleware_fails_open below).
    with pytest.raises(RuntimeError):
        checker.advisory_for("sess-1")


def test_interval_caching_recomputes_at_most_once_per_interval() -> None:
    """The comparator is recomputed lazily at most once per interval."""
    clock = _FakeClock()
    lookup = _CountingLookup(("0.55.0", "0.56.0"))
    checker = VersionDriftChecker(interval_seconds=300.0, clock=clock, lookup=lookup)

    # Use distinct session ids so per-session dedup never suppresses, isolating
    # the caching behavior from the emission behavior.
    checker.advisory_for("a")
    assert lookup.calls == 1

    # Within the interval: no recompute.
    clock.advance(299.0)
    checker.advisory_for("b")
    assert lookup.calls == 1

    # Interval elapsed: one recompute.
    clock.advance(2.0)
    checker.advisory_for("c")
    assert lookup.calls == 2


def test_zero_interval_rechecks_every_call() -> None:
    """interval=0 recomputes on every call (documented knob behavior)."""
    clock = _FakeClock()
    lookup = _CountingLookup(("0.55.0", "0.56.0"))
    checker = VersionDriftChecker(interval_seconds=0.0, clock=clock, lookup=lookup)
    checker.advisory_for("a")
    checker.advisory_for("b")
    assert lookup.calls == 2


# --------------------------------------------------------------------------- #
# VersionDriftMiddleware integration behavior
# --------------------------------------------------------------------------- #


def _mw(value: tuple[str, str] | None) -> VersionDriftMiddleware:
    checker = VersionDriftChecker(
        interval_seconds=300.0,
        clock=_FakeClock(),
        lookup=_CountingLookup(value),
    )
    return VersionDriftMiddleware(checker=checker)


async def test_middleware_attaches_advisory_on_drift() -> None:
    """On drift, both structured_content and a text block carry the advisory."""
    result = _FakeToolResult(
        content=[TextContent(type="text", text="ok")],
        structured_content={"data": 1},
    )

    async def call_next(_ctx: Any) -> Any:
        return result

    ctx = _FakeMiddlewareContext(
        message=_FakeMessage(),
        fastmcp_context=_FakeContext(request_context=_FakeRequestContext()),
    )
    out = await _mw(("0.55.0", "0.56.0")).on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    assert out.structured_content is not None
    assert out.structured_content["version_drift"] == build_advisory("0.55.0", "0.56.0")
    # Original tool payload untouched.
    assert out.structured_content["data"] == 1
    # Text advisory appended (non-blocking, human readable).
    texts = [b.text for b in out.content if isinstance(b, TextContent)]
    assert any("version drift advisory" in t and "0.56.0" in t for t in texts)


async def test_middleware_no_advisory_when_versions_match() -> None:
    """No drift -> tool result is returned unchanged."""
    result = _FakeToolResult(
        content=[TextContent(type="text", text="ok")],
        structured_content={"data": 1},
    )

    async def call_next(_ctx: Any) -> Any:
        return result

    ctx = _FakeMiddlewareContext(
        message=_FakeMessage(),
        fastmcp_context=_FakeContext(request_context=_FakeRequestContext()),
    )
    out = await _mw(("0.55.0", "0.55.0")).on_call_tool(ctx, call_next)  # type: ignore[arg-type]

    assert out.structured_content == {"data": 1}
    assert len(out.content) == 1


async def test_middleware_passthrough_without_context() -> None:
    """No MCP session context (unit/direct calls) -> untouched pass-through."""
    result = _FakeToolResult(content=[TextContent(type="text", text="ok")])

    async def call_next(_ctx: Any) -> Any:
        return result

    ctx = _FakeMiddlewareContext(message=_FakeMessage(), fastmcp_context=None)
    out = await _mw(("0.55.0", "0.56.0")).on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    assert len(out.content) == 1
    assert out.structured_content is None


async def test_middleware_fails_open_on_lookup_exception() -> None:
    """A raising comparator must never break the tool call."""

    def _boom() -> tuple[str, str] | None:
        raise RuntimeError("metadata blew up")

    checker = VersionDriftChecker(interval_seconds=300.0, clock=_FakeClock(), lookup=_boom)
    middleware = VersionDriftMiddleware(checker=checker)

    result = _FakeToolResult(content=[TextContent(type="text", text="ok")])

    async def call_next(_ctx: Any) -> Any:
        return result

    ctx = _FakeMiddlewareContext(
        message=_FakeMessage(),
        fastmcp_context=_FakeContext(request_context=_FakeRequestContext()),
    )
    out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
    # Tool call succeeded, no advisory attached.
    assert len(out.content) == 1
    assert out.content[0].text == "ok"


def test_config_knob_default_is_typed_and_documented() -> None:
    """version_check_interval_seconds is a typed TRWConfig field (NFR03)."""
    from trw_mcp.models.config import TRWConfig

    cfg = TRWConfig()
    assert isinstance(cfg.version_check_interval_seconds, float)
    assert cfg.version_check_interval_seconds >= 0.0


# --------------------------------------------------------------------------- #
# PRD-CORE-215-FR07: retire stale proxy authority without erasing history
# --------------------------------------------------------------------------- #

from pathlib import Path

from trw_mcp.middleware.version_drift import (
    CURRENT_TRANSPORT_AUTHORITY,
    TransportAuthoritySurface,
    TransportInvariant,
    load_current_transport_authority,
    scan_text_for_proxy_claims,
    validate_transport_authority,
)

_ADVISORY_INV = TransportInvariant("advisory", "adv.py", "build_advisory", "advisory stays observable")
_RETRY_INV = TransportInvariant("retry_gap", "client.py", "record the gap", "retry-gap stays loud")
_FIX_INVARIANTS = (_ADVISORY_INV, _RETRY_INV)


def _surf(path: str, historical: bool = False) -> TransportAuthoritySurface:
    return TransportAuthoritySurface(name=path, path=path, role="fixture", historical=historical)


def _clean_surfaces() -> list[tuple[TransportAuthoritySurface, str]]:
    """Current-authority fixtures: stdio-only text + both live invariants present."""
    return [
        (_surf("adv.py"), "surfaces drift via build_advisory; stdio-only process."),
        (_surf("client.py"), "retry once then record the gap loudly; never lose a call."),
        # A denial/stdio-affirmation clause: retired topology named only to say it is gone.
        (_surf("_transport.py"), "stdio-only: there is no shared server, proxy, or HTTP transport."),
    ]


def test_prd_core_215_fr07() -> None:
    """Typed inventory: current surfaces are proxy-free; history + invariants honored."""
    # Current-authority fixtures pass with zero proxy claims.
    ok_report = validate_transport_authority(_clean_surfaces(), invariants=_FIX_INVARIANTS)
    assert ok_report.ok
    assert ok_report.proxy_violations == ()
    assert ok_report.missing_invariants == ()

    # A CURRENT surface with a proxy production claim fails typed.
    bad = [
        *_clean_surfaces(),
        (_surf("bad.py"), "All clients share the shared server over HTTP transport on port 8100."),
    ]
    bad_report = validate_transport_authority(bad, invariants=_FIX_INVARIANTS)
    assert not bad_report.ok
    assert any("bad.py" in v for v in bad_report.proxy_violations)

    # A HISTORICAL-labelled surface with the SAME claim is not flagged (preserved).
    hist = [
        *_clean_surfaces(),
        (_surf("archive/old-215.md", historical=True), "v1 routed via the shared server proxy on port 8100."),
    ]
    hist_report = validate_transport_authority(hist, invariants=_FIX_INVARIANTS)
    assert hist_report.ok
    assert hist_report.proxy_violations == ()

    # Deleting the version-drift advisory invariant fails (removal is loud).
    no_adv = [(_surf("adv.py"), "stdio-only; advisory deleted"), (_surf("client.py"), "record the gap")]
    rep_no_adv = validate_transport_authority(no_adv, invariants=_FIX_INVARIANTS)
    assert not rep_no_adv.ok
    assert "advisory" in rep_no_adv.missing_invariants

    # Deleting the retry-once-then-record-gap guidance fails.
    no_gap = [(_surf("adv.py"), "build_advisory"), (_surf("client.py"), "guidance removed")]
    rep_no_gap = validate_transport_authority(no_gap, invariants=_FIX_INVARIANTS)
    assert not rep_no_gap.ok
    assert "retry_gap" in rep_no_gap.missing_invariants


def test_prd_core_215_fr07_real_repo_surfaces_pass_today() -> None:
    """The real validator passes against the seven named current-authority files."""
    report = validate_transport_authority(load_current_transport_authority())
    assert report.ok, (report.proxy_violations, report.missing_invariants)
    assert report.proxy_violations == ()
    assert report.missing_invariants == ()

    assert len(CURRENT_TRANSPORT_AUTHORITY) == 8
    paths = {s.path for s in CURRENT_TRANSPORT_AUTHORITY}
    for expected in (
        "trw-mcp/src/trw_mcp/server/_transport.py",
        "trw-mcp/src/trw_mcp/tools/_ceremony_session_start_steps.py",
        "trw-mcp/src/trw_mcp/middleware/ceremony.py",
        "trw-mcp/src/trw_mcp/middleware/version_drift.py",
        "trw-mcp/src/trw_mcp/server/_tools.py",
        "trw-mcp/src/trw_mcp/bootstrap/_client_integrations.py",
        # PRD-CORE-215-FR07: the bundled agent retry-protocol fragment is a
        # current-authority surface — reintroducing a shared-server production
        # claim there is now caught by the validator.
        "trw-mcp/src/trw_mcp/data/agents/_shared/mcp-retry-protocol.md",
        "CLAUDE.md",
    ):
        assert expected in paths


def test_prd_core_215_fr07_transport_is_stdio_only() -> None:
    """stdio is the executable transport and server/_transport.py claims no proxy."""
    root = Path(__file__).resolve().parents[2]
    src = (root / "trw-mcp/src/trw_mcp/server/_transport.py").read_text(encoding="utf-8")
    # stdio is what actually runs.
    assert "mcp.run()" in src
    assert 'transport="stdio"' in src
    # Zero current proxy production claims (the negations are stdio affirmations).
    assert scan_text_for_proxy_claims(src) == ()
