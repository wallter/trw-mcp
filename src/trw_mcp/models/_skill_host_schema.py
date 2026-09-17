"""Per-host accepted skill frontmatter — PRD-FIX-141-FR10.

Belongs to the ``skill_manifest.py`` facade. Re-exported there for back-compat,
and split out so that module stays under the 350 effective-LOC gate.
"""

from __future__ import annotations

#: Frontmatter keys each HOST defines for its own skill loader, which TRW's
#: manifest does not model and must not reject (PRD-FIX-141-FR10).
#:
#: Strict mode rejected 11 of the 25 bundled skills for carrying ``context``,
#: ``agent``, ``disable-model-invocation`` or ``category`` — keys Claude Code
#: itself defines and that TRW's own bundle ships (learning L-ODuU). A validator
#: that rejects the artifacts its own package installs is measuring the wrong
#: thing.
#:
#: Deliberately PER HOST, and deliberately not a union: accepting every key any
#: host ever defines would turn a genuine typo in a TRW field into a silent
#: no-op, which is the failure mode strict mode exists to catch. A key outside
#: the ACTIVE host's schema keeps today's behaviour exactly — warning in compat
#: mode, error in strict mode. An unknown host accepts nothing extra.
HOST_FRONTMATTER_FIELDS: dict[str, frozenset[str]] = {
    "claude-code": frozenset({"context", "agent", "disable-model-invocation", "category"}),
}

#: Canonical fields whose BLANK value a host legitimately writes, normalized to
#: ``None`` instead of rejected. Claude Code ships ``argument-hint:`` with no
#: value for skills that take no argument; compat mode already coerced that, so
#: strict mode rejecting it was the two modes disagreeing about the same bundle.
HOST_BLANK_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "claude-code": frozenset({"argument_hint"}),
}

#: The host assumed when a caller names none. Every bundled skill is authored
#: against it, and it is the only host with a schema today; an explicit
#: ``host=`` always wins.
DEFAULT_SKILL_HOST = "claude-code"


def host_frontmatter_fields(host: str | None) -> frozenset[str]:
    """Return the accepted-but-ignored frontmatter keys for *host*."""
    return HOST_FRONTMATTER_FIELDS.get(host or DEFAULT_SKILL_HOST, frozenset())


def host_blank_allowed_fields(host: str | None) -> frozenset[str]:
    """Return the canonical fields *host* may legitimately leave blank."""
    return HOST_BLANK_ALLOWED_FIELDS.get(host or DEFAULT_SKILL_HOST, frozenset())


__all__ = [
    "DEFAULT_SKILL_HOST",
    "HOST_BLANK_ALLOWED_FIELDS",
    "HOST_FRONTMATTER_FIELDS",
    "host_blank_allowed_fields",
    "host_frontmatter_fields",
]
