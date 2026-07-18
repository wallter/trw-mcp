"""Trusted, versioned model-capability catalog for the effort adapter edge.

PRD-CORE-209. This table is ADAPTER-EDGE ONLY: concrete provider model IDs
never leak into portable surfaces (agent frontmatter, framework docs), which
speak capability tiers. The catalog exists so that, when a caller supplies a
trusted active-model identity, the effort adapter can stop clamping values
the model actually declares (xhigh/max) — and can refuse values the model
does not accept at all (Haiku's effort parameter errors upstream).

TRW still never auto-selects xhigh/max; recommendation happens upstream in
task-profile resolution. The catalog only changes the mapping decision, and
every decision remains advice — never a claim of harness application.

Provenance: Anthropic model docs verified 2026-07-09; see
docs/research/providers/claude-code/CLAUDE-5-INTEGRATION-PLAN-2026-07-09.md §1/§7.
"""

from __future__ import annotations

from trw_mcp.models.task_profile_types import ExecutionEffort

# Bump when entries change so adapter decision identities change with it.
ANTHROPIC_MODEL_CATALOG_VERSION = "anthropic-models-2026-07"

_FULL_EFFORT: frozenset[ExecutionEffort] = frozenset({"low", "medium", "high", "xhigh", "max"})
_NO_XHIGH: frozenset[ExecutionEffort] = frozenset({"low", "medium", "high", "max"})

# Keyed by model-family prefix. A lookup matches a key exactly or at a
# `-` / `[` / `@` boundary (date-suffixed IDs, [1m] variants, Vertex @-pins).
# frozenset() means the model declares NO effort support (adapter returns
# `unsupported`, never a clamp); an absent model means unknown (safe base).
#
# trw:intentional `max` without `xhigh` on the 4.6 generation is correct, not
# a typo: `max` predates `xhigh`, which Anthropic inserted between `high` and
# `max` with Opus 4.7. The official effort doc's per-level support lists
# (fetched 2026-07-09) include Opus 4.6 + Sonnet 4.6 under `max` but exclude
# both from `xhigh`; Opus 4.5 supports neither. See
# docs/documentation/prompting/claude-5-sources/RAW-AGENT-REPORTS-2026-07-09.md.
_ANTHROPIC_EFFORT_CAPABILITIES: dict[str, frozenset[ExecutionEffort]] = {
    "claude-fable-5": _FULL_EFFORT,
    "claude-mythos-5": _FULL_EFFORT,
    "claude-opus-4-8": _FULL_EFFORT,
    "claude-opus-4-7": _FULL_EFFORT,
    "claude-sonnet-5": _FULL_EFFORT,
    "claude-opus-4-6": _NO_XHIGH,
    "claude-sonnet-4-6": _NO_XHIGH,
    "claude-opus-4-5": frozenset({"low", "medium", "high"}),
    "claude-haiku-4-5": frozenset(),
}

_BOUNDARY_CHARS = ("-", "[", "@")


def _normalize_model_id(model_id: str) -> str:
    normalized = model_id.strip().lower()
    # Provider-prefixed IDs: Bedrock "anthropic.claude-…" and the region-
    # prefixed inference-profile form "us./eu./apac. anthropic.claude-…".
    marker = "anthropic.claude"
    idx = normalized.find(marker)
    if idx != -1 and (idx == 0 or normalized[idx - 1] == "."):
        normalized = normalized[idx + len("anthropic.") :]
    return normalized


def lookup_model_effort_capabilities(model_id: str) -> frozenset[ExecutionEffort] | None:
    """Return the declared effort set for a trusted model identity.

    Returns ``None`` for unknown models (callers keep their safe default) and
    an empty frozenset for models that declare no effort support at all.
    """
    normalized = _normalize_model_id(model_id)
    if not normalized:
        return None
    # Longest key first so a longer family never loses to a shorter prefix.
    for key in sorted(_ANTHROPIC_EFFORT_CAPABILITIES, key=len, reverse=True):
        if normalized == key or (normalized.startswith(key) and normalized[len(key) : len(key) + 1] in _BOUNDARY_CHARS):
            return _ANTHROPIC_EFFORT_CAPABILITIES[key]
    return None
