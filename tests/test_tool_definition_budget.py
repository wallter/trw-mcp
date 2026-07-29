"""Tool-DEFINITION token-budget tripwires (companion to test_response_token_budget).

WHY THIS EXISTS — a different cost model from tool responses:

A tool *response* is paid once per call. A tool *definition* — its description
plus its parameter JSON Schema — is paid unconditionally in the system prompt
of EVERY session of EVERY client that loads the surface, before the agent has
done anything at all. It is the floor of TRW's context cost, and unlike a
response it cannot be trimmed at runtime, budgeted, or deferred by the tool
itself.

The 2026-07-12 campaign governed responses (see ``test_response_token_budget``
and CHANGELOG 0.57.0) and left definitions unmeasured. The 2026-07-27 baseline
found the predictable drift:

* 50 registered tools cost ~15.7k tokens of definition — before tool search,
  that is the whole surface in every system prompt.
* The 12-tool core preset cost ~5.9k tokens.
* Docstrings had accreted internal result-TypedDict field inventories,
  compaction mechanics, partial-failure notes, and worked examples — none of
  which a caller can act on and all of which the schema or the response
  already states.
* Parameter descriptions documented rare advanced knobs at the same weight as
  the one or two arguments in real use (``trw_recall``'s parameter schema was
  4.4x its description; ``trw_learn_update``'s was 8.3x).

WHAT A GOOD DEFINITION CONTAINS
1. One line: what the tool does, in the caller's terms.
2. When to call it (the trigger) — a literal ``Use when`` clause.
3. What comes back, at the granularity that changes the caller's next action —
   not a field-by-field TypedDict inventory.
4. Parameter prose only where the parameter is genuinely used: full sentences
   for the 1-3 common arguments, one short clause for advanced knobs.

THIS BUDGET IS A CEILING, NOT A LICENCE TO DELETE STRUCTURE.
``test_tool_docstring_pattern.py`` (PRD-QUAL-074) is the companion FLOOR: it
requires a literal ``Use when`` in every tool docstring and an ``Output:``/
``Returns:`` line on the hot-path tools. The two are compatible by design — the
floor mandates the headers, the ceiling bounds what goes under them. A trim
campaign in 2026-07 broke the floor for 24 tools by deleting whole sections
instead of shortening them; shorten the content, keep the headers.

NOT in a definition: internal module names, result TypedDict names and their
field lists, resilience/mechanism notes, PRD ids, or examples that restate the
schema.

ON "See Also": PRD-FIX-065 FR04 REQUIRES it on trw_learn, trw_recall,
trw_session_start, trw_deliver and trw_prd_create, and that requirement wins.
This campaign briefly dropped those lines on the reasoning that a chain is
unfollowable; they were restored the same day, because ~60 chars per tool
against ~25k saved is not a trade worth retiring a delivered requirement for.
Do not re-litigate it in a trim — change the PRD or leave it alone.

These are SOFT ceilings with slack over the post-trim measurement, exactly like
the response budget. If your change trips one:

1. Ask whether the words earn their tokens on every session of every client.
2. Move mechanism/rationale to the module or function body, where maintainers
   read it and callers do not pay for it.
3. If the growth is genuinely load-bearing, raise the ceiling IN THE SAME
   change with the new measurement and why. Never raise it to make the test
   pass.
"""

from __future__ import annotations

import json
from typing import Final

import pytest

pytestmark = pytest.mark.unit

# Post-trim measurements (2026-07-27, real ``list_tools()`` over the production
# server object). Ceilings = measurement + ~15% slack, so ordinary wording edits
# do not trip the wire but a reverted trim or a new bloated tool does.
#
# ``ensure_ascii=False`` throughout: the MCP wire format is UTF-8, so an em dash
# is one character. With the json default of ensure_ascii=True it serializes as
# ``—`` and measures as six, which would penalise ordinary punctuation and
# push authors toward ASCII-only prose for a cost that does not exist.
#
# Final post-campaign measurement (2026-07-27): full surface 37,920 chars
# (~9.5k tok), core preset 15,261 (~3.8k tok). Baseline before the trim, for
# the record: 62,794 (~15.7k tok) and 23,628 (~5.9k tok) — a 40% / 35% cut.
#
# These figures are ~800 chars ABOVE the mid-campaign measurement (37,120 /
# 14,461) that this comment first recorded. That is not drift: an independent
# review found load-bearing content the trim had cut — a delivery-gate
# consequence, a parameter-semantics clause, three missing output contracts —
# and it was restored. Correctness over the number.
FULL_SURFACE_CEILING_CHARS: Final[int] = 38_500
CORE_PRESET_CEILING_CHARS: Final[int] = 15_200

# A tool definition has two independently-governed halves, and conflating them
# produces an untunable test:
#
#   PROSE     = the description + every parameter ``description``. Entirely
#               author-controlled. This is where drift happens and where a
#               trim campaign has leverage.
#   SIGNATURE = the parameter schema with all descriptions stripped. Forced by
#               the function signature: each ``x: str | None = None`` costs
#               ~78 chars of JSON Schema no matter what the docstring says.
#               No amount of editing moves it.
#
# Measured 2026-07-27: trw_learn had a 1,821-char signature floor from its 24
# parameters — it exceeded any reasonable combined per-tool ceiling with a
# COMPLETELY EMPTY docstring. A combined ceiling would therefore have demanded
# either an impossible edit or a meaningless exemption list. Two ceilings state
# the truth instead: prose bloat is a writing defect, signature bloat is an API
# design defect, and they have different fixes.
#
# The fix for a tripped SIGNATURE ceiling is an API change — collapsing
# rarely-set arguments into one structured parameter — not more editing.
#
# RATCHETED 2026-07-28 (PRD-CORE-234-FR08). The structured-argument collapse
# landed: trw_learn 24 -> 10 parameters, trw_learn_update 20 -> 10, trw_init
# 13 -> 7, trw_review 10 -> 7. Aggregate signature floor 20,324 -> 17,481
# chars. Ceilings move DOWN to hold that saving, because a ceiling left at the
# pre-collapse value permits silent regrowth all the way back — the saving was
# banked but ungoverned until this line changed.
#
# Measured maxima at the ratchet: signature 978 (trw_recall), 932
# (trw_build_check), 861 (trw_learn_update); prose 1,116 (trw_learn), 1,104
# (trw_learn_update). Both ceilings sit deliberately close to their maxima —
# they are ratchets, and adding two optional arguments to trw_recall SHOULD
# trip the signature one and force the structured-argument conversation rather
# than pass quietly.
#
# Do NOT raise either to make a test pass. Raising is legitimate only with the
# new measurement and a justification for why the growth is load-bearing, in
# the same change that causes it.
PER_TOOL_PROSE_CEILING_CHARS: Final[int] = 1_150
PER_TOOL_SIGNATURE_CEILING_CHARS: Final[int] = 1_000

# Aggregate signature floor across the whole registered surface — the fourth
# ceiling PRD-CORE-234-FR08 asks for, added 2026-07-28.
#
# FR08 specified 17,300. Measured delivered state is 17,481, so the specified
# number would FAIL on the commit that introduced it — the same defect an
# earlier draft of FR08 had when it proposed a per-tool ceiling of 900 against
# a 978 maximum. The specified figure came from a predicted post-collapse
# residual of 16,438; the delivered implementation landed 634 chars above that
# prediction, partly because trw_init shipped `dict | str | None = None` (the
# +106 form) where FR03 mandated `Field(default_factory=dict)` (+58).
#
# Set to 17,800 — just above measured. That is what a ratchet is: it prevents
# regrowth without asserting a target the code does not meet. Closing the
# 634-char gap is real remaining work, tracked in the PRD, not papered over by
# a ceiling that would red-light the branch.
AGGREGATE_SIGNATURE_CEILING_CHARS: Final[int] = 17_800

# Parameter PROSE far larger than the tool's own description means the tool
# documents knobs nobody turns. Applied only to tools whose description is
# non-trivial, so a terse-by-design tool is not penalised for having a small
# description. Measured against prose, never the raw schema — see the test.
MAX_PARAM_PROSE_TO_DESC_RATIO: Final[float] = 3.0

_BLOAT_GUIDANCE: Final[str] = (
    "Tool-definition token budget exceeded — a definition is paid in the "
    "system prompt of EVERY session of EVERY client, before the agent acts. "
    "Cut internal field inventories, mechanism prose, PRD ids, and examples "
    "that restate the schema; keep what the caller needs to decide to call "
    "the tool. If the tokens are genuinely load-bearing, raise the ceiling in "
    "this same change with the new measurement and justification. See this "
    "file's docstring and .claude/rules/trw-mcp-python.md "
    "§Tool Definition Token Budget."
)


def _strip_descriptions(node: object) -> object:
    """Return ``node`` with every ``description`` key removed, recursively."""
    if isinstance(node, dict):
        return {k: _strip_descriptions(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [_strip_descriptions(item) for item in node]
    return node


def _definition_chars(tool: object) -> tuple[int, int]:
    """Return ``(description_chars, parameter_schema_chars)`` for one tool.

    Measures the serialized surface the client actually receives, not a
    fixture, so the tripwire cannot drift from what agents are billed for.
    """
    dumped = tool.model_dump(exclude_none=True)  # type: ignore[attr-defined]
    description = str(dumped.get("description") or "")
    parameters = json.dumps(dumped.get("parameters") or {}, default=str, sort_keys=True, ensure_ascii=False)
    return len(description), len(parameters)


def _prose_and_signature_chars(tool: object) -> tuple[int, int]:
    """Split one tool's definition into author-controlled prose vs schema floor.

    ``prose`` is the description plus every parameter ``description`` (measured
    as the difference between the full schema and the description-stripped
    schema, so nested and ``anyOf`` shapes are counted correctly). ``signature``
    is what the parameter list costs with no prose at all.
    """
    dumped = tool.model_dump(exclude_none=True)  # type: ignore[attr-defined]
    parameters = dumped.get("parameters") or {}
    full = len(json.dumps(parameters, default=str, sort_keys=True, ensure_ascii=False))
    floor = len(json.dumps(_strip_descriptions(parameters), default=str, sort_keys=True, ensure_ascii=False))
    prose = len(str(dumped.get("description") or "")) + (full - floor)
    return prose, floor


async def _measure(names: frozenset[str] | None = None) -> dict[str, tuple[int, int]]:
    """Measure every registered tool, optionally restricted to ``names``."""
    from trw_mcp.server._app import mcp

    tools = await mcp._list_tools()
    measured: dict[str, tuple[int, int]] = {}
    for tool in tools:
        name = str(tool.model_dump(exclude_none=True).get("name") or "")
        if names is not None and name not in names:
            continue
        measured[name] = _definition_chars(tool)
    return measured


def _total(measured: dict[str, tuple[int, int]]) -> int:
    return sum(desc + params for desc, params in measured.values())


def _report(measured: dict[str, tuple[int, int]], limit: int = 10) -> str:
    ranked = sorted(measured.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
    lines = [f"{d + p:6d} ({d:5d} desc + {p:5d} params)  {n}" for n, (d, p) in ranked[:limit]]
    return "\n".join(lines)


# The tools exposed to a default coding client. Kept explicit rather than
# derived so a preset change that widens the default surface is a visible diff
# here, not a silent budget increase.
CORE_PRESET: Final[frozenset[str]] = frozenset(
    {
        "trw_session_start",
        "trw_init",
        "trw_status",
        "trw_checkpoint",
        "trw_learn",
        "trw_recall",
        "trw_build_check",
        "trw_review",
        "trw_deliver",
        "trw_profile_explain",
        "trw_skill_discovery",
        "trw_request_tool_access",
    }
)


async def test_full_tool_surface_within_definition_budget() -> None:
    """Every registered tool definition, summed, stays under the surface ceiling."""
    measured = await _measure()
    total = _total(measured)
    assert total <= FULL_SURFACE_CEILING_CHARS, (
        f"Full tool surface = {total} chars (~{total // 4} tok) across "
        f"{len(measured)} tools, ceiling {FULL_SURFACE_CEILING_CHARS}.\n"
        f"Largest definitions:\n{_report(measured)}\n\n{_BLOAT_GUIDANCE}"
    )


async def test_core_preset_within_definition_budget() -> None:
    """The default client-facing preset stays under its (tighter) ceiling."""
    measured = await _measure(CORE_PRESET)
    missing = CORE_PRESET - set(measured)
    assert not missing, f"core-preset tools are not registered: {sorted(missing)}"
    total = _total(measured)
    assert total <= CORE_PRESET_CEILING_CHARS, (
        f"Core preset = {total} chars (~{total // 4} tok) across "
        f"{len(measured)} tools, ceiling {CORE_PRESET_CEILING_CHARS}.\n"
        f"Largest definitions:\n{_report(measured)}\n\n{_BLOAT_GUIDANCE}"
    )


async def _measure_split() -> dict[str, tuple[int, int]]:
    """Measure ``(prose, signature)`` chars for every registered tool."""
    from trw_mcp.server._app import mcp

    out: dict[str, tuple[int, int]] = {}
    for tool in await mcp._list_tools():
        name = str(tool.model_dump(exclude_none=True).get("name") or "")
        out[name] = _prose_and_signature_chars(tool)
    return out


async def test_no_tool_carries_excessive_prose() -> None:
    """Author-controlled prose stays under the per-tool ceiling.

    Prose is description + every parameter description. Unlike the signature
    floor below, this is fully fixable by editing.
    """
    over = {
        name: prose
        for name, (prose, _signature) in (await _measure_split()).items()
        if prose > PER_TOOL_PROSE_CEILING_CHARS
    }
    assert not over, (
        f"Tool prose over the per-tool ceiling of {PER_TOOL_PROSE_CEILING_CHARS} chars: "
        + ", ".join(f"{n}={c}" for n, c in sorted(over.items(), key=lambda kv: -kv[1]))
        + f"\n\n{_BLOAT_GUIDANCE}"
    )


async def test_no_tool_carries_an_excessive_parameter_signature() -> None:
    """The description-stripped parameter schema stays under its ceiling.

    A tripped signature ceiling is an API-design signal, not a writing one: the
    tool takes too many arguments. The fix is to collapse rarely-set arguments
    into one structured parameter, which shrinks the schema for every caller in
    every session — editing the docstring cannot help here.
    """
    over = {
        name: signature
        for name, (_prose, signature) in (await _measure_split()).items()
        if signature > PER_TOOL_SIGNATURE_CEILING_CHARS
    }
    assert not over, (
        "Parameter signatures over the per-tool ceiling of "
        f"{PER_TOOL_SIGNATURE_CEILING_CHARS} chars: "
        + ", ".join(f"{n}={c}" for n, c in sorted(over.items(), key=lambda kv: -kv[1]))
        + "\nEach optional parameter costs ~78 chars of schema in every system "
        "prompt regardless of its docstring. Collapse rarely-set arguments into "
        "a structured parameter rather than raising this ceiling.\n\n" + _BLOAT_GUIDANCE
    )


async def test_parameter_prose_does_not_dwarf_the_description() -> None:
    """Per-parameter prose must not outweigh the tool's own description.

    This is the "documents knobs nobody turns" check. It deliberately measures
    parameter *prose* against the description, NOT the whole parameter schema:
    the schema includes the signature floor, so a tool with many arguments and
    a tight description would look like an offender when it is in fact the
    well-written case. Ratios computed against the raw schema flagged
    trw_dispatch and trw_meta_tune_propose purely for having many arguments.
    """
    offenders: dict[str, tuple[int, int]] = {}
    from trw_mcp.server._app import mcp

    for tool in await mcp._list_tools():
        dumped = tool.model_dump(exclude_none=True)
        name = str(dumped.get("name") or "")
        description = len(str(dumped.get("description") or ""))
        prose, _signature = _prose_and_signature_chars(tool)
        param_prose = prose - description
        if description >= 200 and param_prose > description * MAX_PARAM_PROSE_TO_DESC_RATIO:
            offenders[name] = (description, param_prose)
    assert not offenders, (
        f"Parameter prose exceeds {MAX_PARAM_PROSE_TO_DESC_RATIO}x the tool description: "
        + ", ".join(
            f"{n}(desc={d}, param_prose={p})" for n, (d, p) in sorted(offenders.items(), key=lambda kv: -kv[1][1])
        )
        + "\nTrim advanced-knob prose to a short clause, or split the tool.\n\n"
        + _BLOAT_GUIDANCE
    )


async def test_definitions_omit_internal_implementation_vocabulary() -> None:
    """Definitions must not ship maintainer vocabulary the caller cannot act on.

    Result TypedDict names and PRD identifiers are the two recurring leaks: both
    are maintainer references that cost every caller tokens and tell them
    nothing about whether to call the tool.
    """
    import re

    measured_names: dict[str, str] = {}
    from trw_mcp.server._app import mcp

    for tool in await mcp._list_tools():
        dumped = tool.model_dump(exclude_none=True)
        # Parameter descriptions are billed to every caller exactly like the
        # tool description, so they are scanned too. Scanning only the
        # description let a PRD id inside an Args: entry through.
        params = json.dumps(dumped.get("parameters") or {}, default=str, ensure_ascii=False)
        measured_names[str(dumped.get("name") or "")] = f"{dumped.get('description') or ''}\n{params}"

    typed_dict = re.compile(r"\b\w+ResultDict\b")
    prd_id = re.compile(r"\bPRD-[A-Z]+-\d+\b|\b(?:FR|NFR|OQ|UF)-?\d{2,}\b")
    leaks: dict[str, list[str]] = {}
    for name, description in measured_names.items():
        found = sorted({*typed_dict.findall(description), *prd_id.findall(description)})
        if found:
            leaks[name] = found
    assert not leaks, (
        "Tool descriptions leak internal vocabulary (result TypedDict names / "
        "PRD ids) that callers cannot act on: "
        + "; ".join(f"{n}: {', '.join(v)}" for n, v in sorted(leaks.items()))
        + f"\n\n{_BLOAT_GUIDANCE}"
    )
