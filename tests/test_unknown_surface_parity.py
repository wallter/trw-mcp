"""PRD-CORE-246 FR05/FR06/FR08/FR09 — the declared surface tells the truth.

Measured 2026-09-03, before this change: ``resolve_tool_surface("unknown",
"standard")`` returned 9 tools with NEITHER verification tool, while the
middleware exposed 12 WITH both. The never-hide union was a second,
hand-maintained compensating layer in a different module, so every consumer that
read the declared authority alone — including
``TRWConfig.resolve_tool_surface_for_task`` — was told the session had no
verification tools. ``trw_submit_feedback`` was absent from all eight measured
surfaces, so a tooling-gap report was blocked by the gap it described.

FR08's contract test is the durable part: it computes the effective surface from
the REAL ``resolve_tool_surface`` and the REAL ``_ALWAYS_EXPOSED`` — no
monkeypatch of either, enforced by a module-inspection assertion — because the
defect class it guards is exactly a divergence between a mocked resolver and the
production one. Both known instances of that class were found by a human in a
live session, twice.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT
from trw_mcp.middleware.surface_authority import _ALWAYS_EXPOSED
from trw_mcp.models.surface_packs import CAPABILITY_PACKS, PACK_TOOLS, STANDARD_TASK_PACKS
from trw_mcp.server._surface_manifest_registry import (
    KERNEL_VERSION,
    KERNEL_VERSION_DIGESTS,
    TOOL_MANIFEST,
    kernel_digest,
    resolve_tool_surface,
)
from trw_mcp.tools._deliver_gate_dispatch import _GATE_TABLE

pytestmark = pytest.mark.integration

#: Every resolution case the runtime can actually produce: the seven mapped task
#: types, the unresolvable ``None`` (no run / unreadable run.yaml — measured at
#: 189 of 191 on-disk runs) and the unmapped sentinel ``"audit"``, which is the
#: tombstoned precedent at ``surface_packs.py``.
_RESOLUTION_CASES: tuple[str | None, ...] = (*STANDARD_TASK_PACKS.keys(), None, "audit")


def _effective(task_type: str | None) -> set[str]:
    """The surface a session ACTUALLY sees: declared packs ∪ the never-hide set."""
    return set(resolve_tool_surface(task_type, "standard").tools) | set(_ALWAYS_EXPOSED)


# ── FR05: the declared authority states what the runtime exposes ────────


def test_unknown_declares_the_verification_pack() -> None:
    """FR05 AC1-AC3: 12 declared tools for ``unknown``, ``None`` and an unmapped
    sentinel, with both verification tools declared and no duplicated id."""
    for case in ("unknown", None, "audit"):
        resolution = resolve_tool_surface(case, "standard")
        assert "trw_build_check" in resolution.tools, case
        assert "trw_review" in resolution.tools, case
        assert len(resolution.tools) == 12, (case, resolution.tools)
        assert len(set(resolution.tools)) == 12, f"{case}: duplicated tool id in {resolution.tools}"

    assert STANDARD_TASK_PACKS["unknown"] == ("verification",)
    assert resolve_tool_surface("unknown", "standard").packs == ("kernel", "verification")


def test_unmapped_decision_string_names_the_fallback() -> None:
    """FR05 boundary: the substitution is VISIBLE, not silent.

    The decision string moved from ``unmapped -> kernel only`` to a named-pack
    form. A reader of the resolution can still tell that ``audit`` was not a
    real key — the fallback is reported, not disguised as a mapping.
    """
    unmapped = resolve_tool_surface("audit", "standard")
    assert "unmapped" in unmapped.decision
    assert "unknown fallback" in unmapped.decision

    mapped = resolve_tool_surface("coding", "standard")
    assert "unmapped" not in mapped.decision


def test_declared_and_effective_no_longer_diverge_on_verification() -> None:
    """FR05's reason for existing: the declared authority was WRONG about the
    system it is the authority for.

    Every gate-remedy verification tool that the runtime exposes for a case must
    now also be DECLARED for it, so a consumer reading the table alone (e.g.
    ``TRWConfig.resolve_tool_surface_for_task``) is not told the session has no
    verification tools.
    """
    from trw_mcp.models.config import get_config

    for case in _RESOLUTION_CASES:
        declared = set(resolve_tool_surface(case, "standard").tools)
        assert "trw_build_check" in declared or case in {"research", "planning"}, case

    wired = get_config().resolve_tool_surface_for_task("unknown")
    assert "trw_build_check" in wired.tools
    assert "trw_review" in wired.tools


def test_versioned_kernel_and_manifest_preserve_feedback_contract() -> None:
    """CORE218 CA1 moves correction into kernel v2; CORE246 feedback ownership
    and registered public inventory remain unchanged."""
    assert kernel_digest() == KERNEL_VERSION_DIGESTS[KERNEL_VERSION]
    assert KERNEL_VERSION_DIGESTS[1] == "9997a48f81a04594b2bca455a92cdc38a2c9b7cfc9901e239c4152371d0becf7"
    assert len(TOOL_MANIFEST) == 48
    assert CAPABILITY_PACKS["feedback"] == ("trw_submit_feedback",)
    # ``trw_submit_feedback`` belongs to EXACTLY the feedback pack (FR06 AC3).
    owning = [pack for pack, tools in PACK_TOOLS.items() if "trw_submit_feedback" in tools]
    assert owning == ["feedback"]


# ── FR06: the feedback channel is reachable from every surface ──────────


def test_feedback_channel_reachable_for_every_task_type() -> None:
    """FR06 AC1: membership holds for all nine resolution cases.

    Measured before the change: absent from ALL of them. "Ironically the
    tooling-gap feedback is blocked by the same gap" (sub_6C05joB22NlijR0O,
    still live 46 days later).
    """
    for case in _RESOLUTION_CASES:
        assert "trw_submit_feedback" in _effective(case), f"feedback channel masked for task_type={case!r}"

    # It reaches sessions through the BOOTSTRAP never-hide set, not by joining a
    # pack, the kernel, or RIGID_TOOLS (each of which would have other effects).
    assert "trw_submit_feedback" in _ALWAYS_EXPOSED
    assert "trw_submit_feedback" not in set(resolve_tool_surface("unknown", "standard").tools)


def test_feedback_call_is_not_denied_on_the_unknown_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR06 AC2: a middleware ``on_call_tool`` for the feedback tool on the
    unresolvable surface returns the TOOL RESULT, not a ``tool_not_in_surface``
    payload. Driven through the real middleware."""
    from trw_mcp.middleware import surface_authority as sa

    monkeypatch.setattr(sa, "_resolve_mode", lambda: "standard")
    monkeypatch.setattr(sa, "resolve_task_type", lambda **_kw: None)
    sa.reset_surface_authority_state()

    sentinel = object()

    class _Msg:
        name = "trw_submit_feedback"

    class _Ctx:
        message = _Msg()
        fastmcp_context = None

    async def _call_next(_ctx: object) -> object:
        return sentinel

    result = asyncio.run(
        sa.SurfaceAuthorityMiddleware().on_call_tool(_Ctx(), _call_next)  # type: ignore[arg-type]
    )
    assert result is sentinel

    # Control: a tool that is genuinely outside the surface IS denied, so the
    # assertion above is not just "the middleware allows everything".
    class _OtherMsg:
        name = "trw_code_search"

    class _OtherCtx:
        message = _OtherMsg()
        fastmcp_context = None

    denied = asyncio.run(
        sa.SurfaceAuthorityMiddleware().on_call_tool(_OtherCtx(), _call_next)  # type: ignore[arg-type]
    )
    assert denied is not sentinel
    assert denied.structured_content["error_type"] == "tool_not_in_surface"  # type: ignore[union-attr,index]


# ── FR08: the contract test over the real resolver ──────────────────────


def test_gate_demanded_tools_subset_of_every_surface() -> None:
    """FR08 AC1: zero unreachable ``(gate_key, tool, task_type)`` triples.

    A gate that names a tool as its remedy is asserting that tool is reachable.
    Before this test, that assertion was checked by nobody — and was false twice.
    """
    unreachable: list[tuple[str, str, str | None]] = []
    for descriptor in _GATE_TABLE:
        for tool in descriptor.remedy_tools:
            for case in _RESOLUTION_CASES:
                if tool not in _effective(case):
                    unreachable.append((descriptor.key, tool, case))

    assert not unreachable, f"gates naming unreachable remedy tools: {unreachable}"
    # Non-vacuity: the table must actually name some tools.
    assert sum(len(d.remedy_tools) for d in _GATE_TABLE) >= 4


def test_a_gate_naming_an_unreachable_tool_is_detected() -> None:
    """FR08 AC2: the check FAILS and names the gate and the tool.

    Injects a descriptor whose remedy is in no pack and no never-hide set and
    re-runs the same predicate. Without this, a green FR08 test could mean
    "no gate names a tool" rather than "every named tool is reachable".
    """
    from trw_mcp.tools._deliver_gate_dispatch import GateDescriptor, OverridePolicy

    rogue = GateDescriptor(
        "rogue_gate", OverridePolicy.STRUCTURED, "rogue_gate", "rogue_gate", remedy_tools=("trw_probe",)
    )
    assert "trw_probe" not in _effective("unknown"), "pick a tool that is genuinely masked"

    unreachable = [
        (rogue.key, tool, case)
        for tool in rogue.remedy_tools
        for case in _RESOLUTION_CASES
        if tool not in _effective(case)
    ]
    assert unreachable
    assert unreachable[0][0] == "rogue_gate"
    assert unreachable[0][1] == "trw_probe"


def test_parity_module_monkeypatches_neither_resolver_nor_never_hide_set() -> None:
    """FR08 AC3 + ``grep_absent``: the contract tests read PRODUCTION values.

    Only the middleware-dispatch tests in this module may monkeypatch, and never
    ``resolve_tool_surface`` or ``_ALWAYS_EXPOSED`` — mocking either would
    reproduce the exact divergence the check exists to catch.
    """
    source = Path(inspect.getfile(test_gate_demanded_tools_subset_of_every_surface)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden = ("resolve_tool_surface", "_ALWAYS_EXPOSED")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in {"setattr", "setitem", "delattr"}:
            continue
        rendered = ast.unparse(node)
        for target in forbidden:
            assert target not in rendered, f"the parity contract must not patch {target}: {rendered}"

    # And the two pure-contract tests must not take the monkeypatch fixture at all.
    for fn in (test_gate_demanded_tools_subset_of_every_surface, test_unknown_declares_the_verification_pack):
        assert "monkeypatch" not in inspect.signature(fn).parameters


# ── FR09: no surface still claims an unclassified task stays advisory ───


def test_no_source_claims_unknown_is_advisory() -> None:
    """FR09 AC1: zero matches of the advisory-unknown phrase across the
    enumerated surfaces, plus the evidence-rule sentence is PRESENT in the
    authoring source (a grep-absent check alone would pass on a deleted file)."""
    surfaces = (
        "trw-mcp/src/trw_mcp/data/framework.source.md",
        "trw-mcp/src/trw_mcp/data/framework.md",
        "trw-mcp/src/trw_mcp/data/framework-core.md",
        "trw-mcp/src/trw_mcp/data/surfaces/tool-lifecycle.md",
        "trw-mcp/src/trw_mcp/state/claude_md/sections/_tool_lifecycle.py",
        "trw-mcp/src/trw_mcp/state/claude_md/renderers/_review_and_opencode.py",
        "trw-mcp/src/trw_mcp/tools/_deliver_gate_dispatch.py",
        "trw-mcp/src/trw_mcp/tools/_orchestration_gate_scan.py",
        "trw-mcp/src/trw_mcp/models/config/_fields_build.py",
        "trw-mcp/src/trw_mcp/tools/_deliver_gate_mode.py",
        # PRD-CORE-246 §1 C6 (post-landing correction): a twelfth surface, found
        # alongside the original nine plus _deliver_gate_mode. Its
        # ``_gate_mode_blocks_task`` docstring used to call itself "scoped
        # identically to the build gate", which FR03 falsified; the docstring
        # was corrected in the same change (:437-450) but this test never
        # enumerated the file, so nothing would catch the stale claim
        # recurring.
        "trw-mcp/src/trw_mcp/tools/_prd_transition_gate.py",
    )
    phrases = (
        "unknown remain advisory",
        "unknown types remain advisory",
        "unknown never block",
        "docs/research/planning/unknown deliver successfully",  # trw-leak-allow: internal_docs verbatim historical regression string (PRD-CORE-246), not a live doc reference
        "``unknown`` is conservative",
    )

    offenders: list[tuple[str, str]] = []
    for rel in surfaces:
        path = PACKAGE_ROOT / rel.removeprefix("trw-mcp/")
        assert path.is_file(), f"FR09 surface disappeared: {rel}"
        text = path.read_text(encoding="utf-8")
        offenders += [(rel, phrase) for phrase in phrases if phrase in text]

    if MONOREPO_ROOT is not None:
        rel = "docs/documentation/tool-lifecycle.md"
        text = (MONOREPO_ROOT / rel).read_text(encoding="utf-8")
        offenders += [(rel, phrase) for phrase in phrases if phrase in text]

    assert not offenders, f"surfaces still asserting the superseded advisory-unknown rule: {offenders}"

    # The positive half: the evidence rule is actually stated in the hand-editable
    # canon source, so FR09 is a rewrite and not a deletion.
    source = (PACKAGE_ROOT / "src/trw_mcp/data/framework.source.md").read_text(encoding="utf-8")
    assert "an unclassified run that changed code still blocks" in source


def test_generated_canon_views_match_their_source() -> None:
    """FR09 AC2: the compiled views were REGENERATED, not hand-edited.

    ``framework.md`` / ``framework-core.md`` are compiler output; editing them
    directly is the recurring mistake this asserts against.
    """
    for rel in ("trw-mcp/src/trw_mcp/data/framework.md", "trw-mcp/src/trw_mcp/data/framework-core.md"):
        text = (PACKAGE_ROOT / rel.removeprefix("trw-mcp/")).read_text(encoding="utf-8")
        assert "an unclassified run that changed code still blocks" in text, rel
