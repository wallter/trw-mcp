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

Provenance: see the dated ``trw:intentional`` blocks below and
ANTHROPIC_MODEL_CATALOG_VERSION -- one place to check, not two. Originally
verified 2026-07-09, re-verified and
extended 2026-07-26 (Claude Opus 5 + Sonnet 4.5 entries). Each entry records what
the vendor's published API accepts, so a change is a re-read of those model docs —
not a tuning decision.
"""

from __future__ import annotations

from collections.abc import Iterable

from trw_mcp.models.task_profile_types import ExecutionEffort

# Bump when entries change so adapter decision identities change with it.
# Date-precise (not month-precise): two entry changes inside one calendar
# month must still produce two distinct decision identities.
ANTHROPIC_MODEL_CATALOG_VERSION = "anthropic-models-2026-09-10"

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
#
# trw:intentional `claude-fable-5-1` and `claude-mythos-5-1` are declared
# EXPLICITLY even though the `-`-boundary match above would resolve them onto
# `claude-fable-5` / `claude-mythos-5` and happen to give the right answer.
# Prefix inheritance is silent: a point release that NARROWED its effort set
# would keep declaring the predecessor's wider one, and this catalog is
# load-bearing precisely because an unsupported effort value *errors* rather
# than being ignored. Declare each point release deliberately.
# Verified 2026-09-10: both support the full low..max ladder, default `high`.
# Source: platform.claude.com/docs/en/build-with-claude/effort
_ANTHROPIC_EFFORT_CAPABILITIES: dict[str, frozenset[ExecutionEffort]] = {
    "claude-fable-5-1": _FULL_EFFORT,
    "claude-mythos-5-1": _FULL_EFFORT,
    "claude-fable-5": _FULL_EFFORT,
    "claude-mythos-5": _FULL_EFFORT,
    "claude-opus-5": _FULL_EFFORT,
    "claude-opus-4-8": _FULL_EFFORT,
    "claude-opus-4-7": _FULL_EFFORT,
    "claude-sonnet-5": _FULL_EFFORT,
    "claude-opus-4-6": _NO_XHIGH,
    "claude-sonnet-4-6": _NO_XHIGH,
    "claude-opus-4-5": frozenset({"low", "medium", "high"}),
    # trw:intentional empty frozenset, not absence. Sonnet 4.5 and Haiku 4.5
    # *error* on the effort parameter rather than ignoring it, so the adapter
    # must return `unsupported` (write nothing) instead of clamping to a value
    # the API would reject. Omitting them would fall through to the safe base
    # and wrongly report low/medium/high as mapped.
    "claude-sonnet-4-5": frozenset(),
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


def match_model_family(model_id: str, keys: Iterable[str]) -> str | None:
    """Return the entry of *keys* naming *model_id*'s family, or ``None``.

    The single implementation of "which table row describes this model id".
    One concrete model reaches TRW under several spellings — a bare alias
    (``claude-opus-5``), a dated snapshot (``claude-haiku-4-5-20251001``), a
    Claude Code long-context rendering (``claude-opus-5[1m]``), a Vertex pin
    (``claude-opus-4-5@20251101``), and Bedrock's plain or region-prefixed
    provider forms (``anthropic.claude-opus-5``, ``us.anthropic.claude-opus-5``).
    A table keyed on bare aliases and read with an exact dict lookup silently
    misses every other spelling, which is how a model can appear to cost
    nothing at all.

    Matching is longest-key-first so a longer family never loses to a shorter
    prefix, and a match must land on a ``-``/``[``/``@`` boundary so
    ``claude-opus-4-80`` never inherits ``claude-opus-4-8``.
    """
    normalized = _normalize_model_id(model_id)
    if not normalized:
        return None
    # `keys` may come from a user-supplied YAML table (TRWConfig.pricing_table_path),
    # where a bare `2026:` or `on:` key parses as int/bool. `len()` and `startswith`
    # would raise on those, and the caller's fail-open would then drop the entire
    # telemetry event rather than just the price — a worse outcome than the $0.00
    # this matcher exists to prevent.
    str_keys = [k for k in keys if isinstance(k, str)]
    for key in sorted(str_keys, key=len, reverse=True):
        if normalized == key or (normalized.startswith(key) and normalized[len(key) : len(key) + 1] in _BOUNDARY_CHARS):
            return key
    return None


def lookup_model_effort_capabilities(model_id: str) -> frozenset[ExecutionEffort] | None:
    """Return the declared effort set for a trusted model identity.

    Returns ``None`` for unknown models (callers keep their safe default) and
    an empty frozenset for models that declare no effort support at all.
    """
    key = match_model_family(model_id, _ANTHROPIC_EFFORT_CAPABILITIES)
    return _ANTHROPIC_EFFORT_CAPABILITIES[key] if key is not None else None
