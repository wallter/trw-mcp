"""Peer plan proposal and review (PRD-CORE-275).

THE ONE DESIGN DECISION: this package never touches transport. It is a pure
function over local files — it reads the formation manifest and the working
tree, and returns a body. The agent sends that body through its OWN existing
MCP session with ``trw_send``.

That keeps the MCP tool surface fixed, which matters because a tool definition
is paid in the system prompt of every session of every client while a CLI verb
costs nothing until invoked. It also keeps ONE member to ONE connection: a CLI
that sent messages itself would need its own MCP session, and enrolling from it
would create a second endpoint for one member. (Stated carefully: not every
sender-side connection must enroll, so this is a design preference with a real
benefit, not an impossibility proof.)

Nothing here can grant permission, record completion, or block a write. A plan
is a statement of intent and a review is advisory feedback; both are untrusted
peer data on arrival, and neither is authority.
"""

from __future__ import annotations

from trw_mcp.plan._body import (
    PROPOSAL_KEYS as PROPOSAL_KEYS,
)
from trw_mcp.plan._body import (
    REVIEW_KEYS as REVIEW_KEYS,
)
from trw_mcp.plan._body import (
    SCHEMA as SCHEMA,
)
from trw_mcp.plan._body import (
    build_proposal as build_proposal,
)
from trw_mcp.plan._body import (
    build_review as build_review,
)
from trw_mcp.plan._body import (
    canonical_bytes as canonical_bytes,
)
from trw_mcp.plan._body import (
    digest_of as digest_of,
)
from trw_mcp.plan._body import (
    encode as encode,
)
from trw_mcp.plan._body import (
    normalize_paths as normalize_paths,
)
from trw_mcp.plan._precheck import (
    ADVISORY_NOTE as ADVISORY_NOTE,
)
from trw_mcp.plan._precheck import (
    PrecheckRow as PrecheckRow,
)
from trw_mcp.plan._precheck import (
    precheck as precheck,
)
from trw_mcp.plan._schema import (
    PlanError as PlanError,
)
from trw_mcp.plan._schema import (
    PlanRefusal as PlanRefusal,
)
from trw_mcp.plan._schema import (
    check_path as check_path,
)
from trw_mcp.plan._schema import (
    parse_proposal as parse_proposal,
)
from trw_mcp.plan._schema import (
    parse_review as parse_review,
)
from trw_mcp.plan._staleness import Currency as Currency
from trw_mcp.plan._staleness import classify as classify
from trw_mcp.plan._staleness import render as render

__all__ = [
    "ADVISORY_NOTE",
    "PROPOSAL_KEYS",
    "REVIEW_KEYS",
    "SCHEMA",
    "Currency",
    "PlanError",
    "PlanRefusal",
    "PrecheckRow",
    "build_proposal",
    "build_review",
    "canonical_bytes",
    "check_path",
    "classify",
    "digest_of",
    "encode",
    "normalize_paths",
    "parse_proposal",
    "parse_review",
    "precheck",
    "render",
]
