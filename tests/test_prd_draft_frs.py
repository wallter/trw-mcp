"""Tests for the trw_prd_draft_frs tool (PRD-CORE-133)."""

from __future__ import annotations

import json
from typing import Callable

import pytest

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.tools._prd_draft_frs import (
    DraftFRsResult,
    FRBlock,
    _extract_paths_from_text,
    _extract_recommendations_from_markdown,
    _extract_symbols_from_text,
    draft_frs_from_research,
)


@pytest.fixture
def draft_frs_fn() -> Callable[..., dict[str, object]]:
    """Get the trw_prd_draft_frs tool function."""
    server = make_test_server("requirements")
    return extract_tool_fn(server, "trw_prd_draft_frs")


# ---------------------------------------------------------------------------
# FR01: Tool produces AARE-F compliant FR blocks
# ---------------------------------------------------------------------------


def test_draft_frs_from_markdown_research(draft_frs_fn) -> None:
    """FR01: Tool accepts markdown research and produces FR blocks."""
    research = """\
# Research Report: Add Rate Limiting

## Summary
The API currently has no rate limiting. We need to add token-bucket
rate limiting to protect against abuse.

## Key Findings
- `backend/src/api/middleware.py` handles all request middleware
- `backend/src/api/config.py` has the settings model
- Current throughput: ~500 req/s per instance

## Recommendations
1. Add a `RateLimitMiddleware` class
2. Configure limits per-route via settings
3. Return 429 with Retry-After header
"""
    result = draft_frs_fn(research_report=research)
    assert "functional_requirements" in result
    frs = result["functional_requirements"]
    assert len(frs) >= 1

    # Each FR should have AARE-F fields
    fr = frs[0]
    assert "id" in fr
    assert "priority" in fr
    assert "status" in fr
    assert "description" in fr
    assert "acceptance" in fr


def test_draft_frs_from_json_research(draft_frs_fn) -> None:
    """FR01: Tool accepts JSON research report."""
    research_json = json.dumps({
        "title": "Auth Middleware Rewrite",
        "summary": "Replace legacy auth with JWT-based middleware",
        "key_symbols": ["AuthMiddleware", "verify_token", "SessionStore"],
        "relevant_locations": [
            "backend/src/auth/middleware.py",
            "backend/src/auth/jwt.py",
        ],
        "recommendations": [
            "Replace session-based auth with stateless JWT",
            "Add refresh token rotation",
        ],
    })
    result = draft_frs_fn(research_report=research_json)
    frs = result["functional_requirements"]
    assert len(frs) >= 1
    # Should have backtick-wrapped symbols in acceptance
    combined = " ".join(fr["acceptance"] for fr in frs)
    assert "`AuthMiddleware`" in combined or "`verify_token`" in combined


def test_draft_frs_with_extra_context(draft_frs_fn) -> None:
    """FR01: Extra context is incorporated into FR descriptions."""
    research = "## Summary\nAdd caching layer for expensive queries."
    extra = "Must support Redis and in-memory backends. TTL configurable per route."
    result = draft_frs_fn(research_report=research, extra_context=extra)
    frs = result["functional_requirements"]
    # Extra context should influence the FRs
    combined = " ".join(fr["description"] + " " + fr["acceptance"] for fr in frs)
    assert "Redis" in combined or "TTL" in combined or "caching" in combined.lower()


# ---------------------------------------------------------------------------
# FR02: KeySymbols and RelevantLocations extraction
# ---------------------------------------------------------------------------


def test_draft_frs_extracts_key_symbols(draft_frs_fn) -> None:
    """FR02: Extracted symbols appear backtick-wrapped in acceptance sections."""
    research = """\
## Key Findings
The `UserService` class in `backend/src/services/user.py` handles registration.
The `EmailClient.send()` method needs retry logic.
Located at `backend/src/integrations/email.py`.
"""
    result = draft_frs_fn(research_report=research)
    assert "key_symbols" in result
    symbols = result["key_symbols"]
    assert "UserService" in symbols or "EmailClient.send()" in symbols or "EmailClient" in symbols


def test_draft_frs_extracts_relevant_locations(draft_frs_fn) -> None:
    """FR02: File paths are extracted from research."""
    research = """\
## Analysis
Changes needed in:
- backend/src/services/user.py (UserService)
- backend/src/integrations/email.py (EmailClient)
- backend/tests/test_user.py (existing tests)
"""
    result = draft_frs_fn(research_report=research)
    assert "relevant_locations" in result
    locations = result["relevant_locations"]
    assert any("backend/src/services/user.py" in loc for loc in locations)


def test_draft_frs_symbols_in_acceptance(draft_frs_fn) -> None:
    """FR02: Backtick-wrapped technical grounding in FR acceptance."""
    research_json = json.dumps({
        "title": "Fix Import Cycle",
        "summary": "Resolve circular import between models and services",
        "key_symbols": ["ModelBase", "ServiceRegistry"],
        "relevant_locations": ["src/models/base.py", "src/services/registry.py"],
        "recommendations": ["Extract shared types into a types.py module"],
    })
    result = draft_frs_fn(research_report=research_json)
    frs = result["functional_requirements"]
    combined_acceptance = " ".join(fr["acceptance"] for fr in frs)
    assert "`ModelBase`" in combined_acceptance or "`ServiceRegistry`" in combined_acceptance


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_draft_frs_empty_research_returns_minimal(draft_frs_fn) -> None:
    """Edge case: minimal research still produces at least one FR."""
    result = draft_frs_fn(research_report="Add feature X")
    frs = result["functional_requirements"]
    assert len(frs) >= 1


def test_draft_frs_result_has_metadata(draft_frs_fn: Callable[..., dict[str, object]]) -> None:
    """Result includes fr_count and functional_requirements."""
    research = "## Summary\nAdd logging to auth module."
    result = draft_frs_fn(research_report=research)
    assert "fr_count" in result
    assert "functional_requirements" in result
    assert isinstance(result["fr_count"], int)
    assert result["fr_count"] >= 1


# ---------------------------------------------------------------------------
# Unit tests: extraction functions (no MCP server needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("`UserService`", ["UserService"]),
        ("`EmailClient.send()`", ["EmailClient.send()"]),
        ("The `RateLimiter` class handles...", ["RateLimiter"]),
        ("plain text without symbols", []),
    ],
)
def test_extract_symbols_backtick(text: str, expected: list[str]) -> None:
    """FR02: Backtick-wrapped symbols are extracted."""
    result = _extract_symbols_from_text(text)
    for sym in expected:
        assert sym in result


@pytest.mark.parametrize(
    "text,expected",
    [
        ("backend/src/api/middleware.py", ["backend/src/api/middleware.py"]),
        ("see `src/models/base.py` for details", ["src/models/base.py"]),
        ("no paths here", []),
    ],
)
def test_extract_paths(text: str, expected: list[str]) -> None:
    """FR02: File paths are extracted correctly."""
    result = _extract_paths_from_text(text)
    for path in expected:
        assert path in result


def test_extract_recommendations_from_markdown() -> None:
    """FR01: Recommendations are extracted from markdown sections."""
    text = """\
# Report

## Recommendations
1. Add rate limiting middleware
2. Configure per-route limits
3. Return 429 headers

## Other Section
Not a recommendation.
"""
    recs = _extract_recommendations_from_markdown(text)
    assert len(recs) >= 2
    assert any("rate limiting" in r.lower() for r in recs)


def test_draft_frs_returns_typed_result() -> None:
    """Return type is DraftFRsResult with properly typed FRBlock entries."""
    result = draft_frs_from_research("## Summary\nAdd caching layer.")
    # Verify structure matches TypedDict
    assert isinstance(result["functional_requirements"], list)
    assert isinstance(result["key_symbols"], list)
    assert isinstance(result["relevant_locations"], list)
    assert isinstance(result["fr_count"], int)

    if result["functional_requirements"]:
        fr = result["functional_requirements"][0]
        assert "id" in fr
        assert "priority" in fr
        assert "status" in fr
        assert "description" in fr
        assert "acceptance" in fr
        assert "confidence" in fr


def test_draft_frs_priority_ordering() -> None:
    """First 2 recommendations get 'Must Have', rest get 'Should Have'."""
    research = json.dumps({
        "title": "Test",
        "recommendations": [
            "First change",
            "Second change",
            "Third change",
            "Fourth change",
        ],
    })
    result = draft_frs_from_research(research)
    frs = result["functional_requirements"]
    assert frs[0]["priority"] == "Must Have"
    assert frs[1]["priority"] == "Must Have"
    assert frs[2]["priority"] == "Should Have"
    assert frs[3]["priority"] == "Should Have"


def test_draft_frs_common_words_excluded() -> None:
    """Common English PascalCase words are not treated as symbols."""
    research = "The Summary describes the Background Context."
    result = draft_frs_from_research(research)
    symbols = result["key_symbols"]
    # "Summary", "Background", "Context" should be filtered out
    for word in ("Summary", "Background", "Context"):
        assert word not in symbols
