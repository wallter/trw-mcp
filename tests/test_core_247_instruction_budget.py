"""PRD-CORE-247-FR08/FR09: instruction budget, per profile, N=7.

The two requirements pull against each other on purpose. FR08 cuts the block for
clients that can enumerate the live surface; FR09 keeps a full gate statement in
every carrier an agent reads. A test that only measured size would happily accept
a block that got small by dropping the gate, so both are asserted over the same
seven profiles.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import resolve_client_profile
from trw_mcp.state.claude_md._catalogue import CEREMONY_POINTER
from trw_mcp.state.claude_md._renderer import ProtocolRenderer
from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

#: Measured on the pre-change tree at trw-mcp 1.0.5, all seven built-in profiles.
_BASELINE_BLOCK_CHARS = {
    "claude-code": 5244,
    "cursor-ide": 5244,
    "copilot": 5244,
    "antigravity-cli": 5244,
    "codex": 5048,
    "cursor-cli": 5048,
    "opencode": 5048,
}

#: Measured AFTER the PRD-CORE-247 review follow-up, which deleted the
#: ``ProtocolRenderer.render_closing_reminder`` method that shadowed the module
#: function and returned session boundaries only. Routing the block through the
#: single implementation added the deliver gate (this carrier previously stated
#: it ZERO times, an FR09 "never zero" breach) and the offline substitute table
#: (FR02's completion evidence: the block must reach a surface an agent reads).
#:
#: **Both of FR08's literal numeric criteria are superseded by that fix and the
#: reason is recorded here rather than absorbed.** "At least 40 percent smaller"
#: and "byte-identical for light mode" were written against a block that carried
#: neither piece of governance. The mechanism FR08 specifies — catalogue to
#: pointer — is fully implemented and is asserted below in isolation, where the
#: 40 percent threshold still applies and is still met.
#: Re-measured for PRD-CORE-252 OQ-3's wiring-defect fix (2026-09-04):
#: ``render_behavioral_protocol()`` now appends the delegation & orchestration
#: block (gated on ``include_delegation``, previously dead code for every
#: profile but codex's dedicated renderer). Full-mode profiles with the flag
#: True (claude-code, cursor-ide, copilot, antigravity-cli) gained the real
#: block; light-mode profiles gained one extra join separator from the
#: gate's now-empty-string return (codex's flag is also True, so it gained
#: the full block on top of that separator).
#: CORE269: truthful unfinished/no-work/acceptance routing replaces loss claims.
#: Full blocks grow 145 chars; light blocks grow 159 chars. This records
#: descriptive snapshots, not a byte-saving claim or relaxation of hard budgets.
_MEASURED_BLOCK_CHARS = {
    "claude-code": 6282,
    "cursor-ide": 6282,
    "copilot": 6282,
    "antigravity-cli": 6282,
    "codex": 8631,
    "cursor-cli": 7544,
    "opencode": 7544,
}
_FULL_MODE = ("claude-code", "cursor-ide", "copilot", "antigravity-cli")
_LIGHT_MODE = ("codex", "cursor-cli", "opencode")
_MIN_REDUCTION = 0.40

#: Full-mode profiles whose ``include_delegation`` is True (PRD-CORE-252 OQ-3
#: fix, 2026-09-04) — their block legitimately grew past the pre-FR08
#: baseline because a real content producer got wired to its gate.
_DELEGATION_WIRED = frozenset({"claude-code", "cursor-ide", "copilot", "antigravity-cli"})

#: The substring that appears ONLY in a full three-path statement of the gate.
_FULL_GATE_MARKER = DELIVER_GATE_PHRASE


def _block(client_id: str) -> str:
    return ProtocolRenderer(client_profile=resolve_client_profile(client_id)).render_behavioral_protocol()


def test_every_built_in_profile_is_covered() -> None:
    """The measurement is N=7 or it is not the measurement the PRD records."""
    assert set(_BASELINE_BLOCK_CHARS) == set(_FULL_MODE) | set(_LIGHT_MODE)
    assert len(_BASELINE_BLOCK_CHARS) == 7
    for client_id in _FULL_MODE:
        assert resolve_client_profile(client_id).ceremony_mode == "full"
    for client_id in _LIGHT_MODE:
        assert resolve_client_profile(client_id).ceremony_mode == "light"


@pytest.mark.parametrize("client_id", sorted(_BASELINE_BLOCK_CHARS))
def test_catalogue_becomes_a_pointer_for_full_mode_only(client_id: str) -> None:
    """FR08 acceptance: the catalogue is a pointer for full mode, verbatim for light.

    The size assertion is on the CATALOGUE SUBSTITUTION, not on the whole block.
    That is deliberate and is the only honest form left: the review follow-up
    routed the deliver gate and the offline substitutes into this same block, so
    a whole-block comparison against the pre-change baseline now compares two
    different artifacts. What FR08 changes is the catalogue, so that is what the
    40 percent threshold is applied to.
    """
    profile = resolve_client_profile(client_id)
    block = _block(client_id)
    renderer = ProtocolRenderer(client_profile=profile)
    verbatim_table = renderer.render_ceremony_table()

    if profile.ceremony_mode == "light":
        assert "| Phase | Tool | When to Use |" in block, (
            f"{client_id} is a light-ceremony profile: its generated file IS the protocol carrier "
            "and may be the only place it can learn the tool surface, so the verbatim catalogue "
            "must be retained whatever instruction_catalogue_mode says"
        )
        assert len(block) == _MEASURED_BLOCK_CHARS[client_id], (
            f"{client_id} block is {len(block)} characters against the recorded "
            f"{_MEASURED_BLOCK_CHARS[client_id]}. Light-mode blocks are pinned exactly: cut the "
            "growth or re-record the measurement in this same change with the reason."
        )
        return

    assert "| Phase | Tool | When to Use |" not in block, "the verbatim catalogue must be gone for full mode"
    assert "trw_skill_discovery" in block, "the pointer must name how to enumerate the live surface"
    assert "trw_status" in block

    saved = len(verbatim_table) - len(CEREMONY_POINTER)
    baseline = _BASELINE_BLOCK_CHARS[client_id]
    assert saved >= _MIN_REDUCTION * baseline, (
        f"{client_id}: the catalogue substitution saves {saved} characters against a {baseline} "
        f"block ({saved / baseline:.1%}), under the {_MIN_REDUCTION:.0%} threshold"
    )
    assert len(block) == _MEASURED_BLOCK_CHARS[client_id], (
        f"{client_id} block is {len(block)} characters against the recorded "
        f"{_MEASURED_BLOCK_CHARS[client_id]}; re-record with the measurement and the reason."
    )
    if client_id in _DELEGATION_WIRED:
        # PRD-CORE-252 OQ-3 (2026-09-04): this profile's include_delegation was
        # already True; the block only grew because a real wiring defect (the
        # gate had a producer and no consumer) got fixed, not because the
        # catalogue-to-pointer saving regressed. The saved-characters assertion
        # above still holds the FR08 mechanism to its threshold in isolation.
        return
    assert len(block) < baseline, "the block must not be larger than the block it replaced"


@pytest.mark.parametrize("client_id", sorted(_BASELINE_BLOCK_CHARS))
def test_the_generated_protocol_block_carries_the_gate_and_the_substitutes(client_id: str) -> None:
    """Review follow-up: the block session-start.sh cats must carry both.

    ``generate_behavioral_protocol_md()`` writes
    ``.trw/context/behavioral_protocol.md``, which ``session-start.sh``'s
    ``_emit_protocol`` cats VERBATIM into agent context on resume/compact/clear
    whenever no instruction file carries the protocol — the bare-harness and
    not-yet-synced population, exactly the one that most needs the offline path.
    A same-named method on ``ProtocolRenderer`` shadowed the module function and
    returned session boundaries only, so that population received neither the
    deliver gate nor the offline substitutes. Same shape as the PRD-FIX-073-FR03
    wiring defect this PRD exists to close.
    """
    block = _block(client_id)
    assert block.count(_FULL_GATE_MARKER) == 1, (
        f"{client_id}'s generated block states the deliver gate {block.count(_FULL_GATE_MARKER)} "
        "times; the invariant is exactly one per carrier, and ZERO is the dangerous direction"
    )
    for substitute in ("trw-mcp local status", "trw-mcp local recall --query", "trw-mcp local deliver --message"):
        assert substitute in block, f"{client_id}'s generated block omits {substitute}"
    assert "reports/" in block and "exit code" in block, "the build-check substitute must name its artifact"


def test_the_shadowing_closing_reminder_method_is_gone() -> None:
    """Review follow-up: one implementation, not two that can diverge.

    An AST check, because the defect was a NAME COLLISION: a delegating method
    of the same name would satisfy a behavioural assertion while preserving the
    ambiguity the PRD recorded as RISK-005.
    """
    import ast

    source = (
        Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "state" / "claude_md" / "_renderer.py"
    ).read_text(encoding="utf-8")
    methods = {
        node.name
        for cls in ast.walk(ast.parse(source))
        if isinstance(cls, ast.ClassDef) and cls.name == "ProtocolRenderer"
        for node in cls.body
        if isinstance(node, ast.FunctionDef)
    }
    assert "render_closing_reminder" not in methods, (
        "ProtocolRenderer.render_closing_reminder shadowed "
        "sections._tool_lifecycle.render_closing_reminder and returned session boundaries only"
    )


def test_generate_behavioral_protocol_md_is_the_production_writer() -> None:
    """Wiring assertion: the function that writes the file the hook reads.

    Asserted through ``generate_behavioral_protocol_md`` — the real entry point
    ``trw_instructions_sync`` calls — rather than through ``ProtocolRenderer``,
    because asserting a renderer's return value while no consumer received it is
    precisely how PRD-FIX-073-FR03 passed its own test and reached 0 of 6
    surfaces.
    """
    from trw_mcp.state.claude_md.sections._behavioral_protocol import generate_behavioral_protocol_md

    written = generate_behavioral_protocol_md()
    assert written.count(_FULL_GATE_MARKER) == 1
    assert "trw-mcp local recall --query" in written
    assert "trw_skill_discovery" in written


def test_light_mode_keeps_the_catalogue_even_with_the_pointer_mode_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR08: the PROFILE wins, not the config field.

    A light-ceremony client may not be able to make the discovery call at all, so
    no value of ``instruction_catalogue_mode`` may strip its catalogue. Driven by
    setting the real field rather than by asserting the branch exists.
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    monkeypatch.setattr(config, "instruction_catalogue_mode", "pointer", raising=False)
    for client_id in _LIGHT_MODE:
        assert "| Phase | Tool | When to Use |" in _block(client_id)


def test_verbatim_mode_restores_the_catalogue_for_full_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR08: ``instruction_catalogue_mode: verbatim`` is a real lever, not a dormant flag."""
    from trw_mcp.models.config import get_config

    monkeypatch.setattr(get_config(), "instruction_catalogue_mode", "verbatim", raising=False)
    for client_id in _FULL_MODE:
        block = _block(client_id)
        assert "| Phase | Tool | When to Use |" in block
        assert "trw_skill_discovery" not in block


# ---------------------------------------------------------------------------
# FR09 — one full gate statement per carrier, never zero
# ---------------------------------------------------------------------------


def test_deliver_gate_stated_once_per_carrier() -> None:
    """FR09 acceptance: exactly one full statement per rendered carrier.

    The failure this guards is symmetric. Two statements is the duplication the
    submitter reported; ZERO is the governance regression that collapsing them
    could cause, and it is the more dangerous of the two.
    """
    from trw_mcp.state.claude_md.sections._tool_lifecycle import render_closing_reminder

    # Carrier 1: the CLAUDE.md-family section (closing reminder).
    closing = render_closing_reminder()
    assert closing.count(_FULL_GATE_MARKER) == 1, (
        f"the closing-reminder carrier states the gate {closing.count(_FULL_GATE_MARKER)} times; "
        "the invariant is exactly one, never zero"
    )

    # Carrier 2: the light-mode minimal protocol — must retain the FULL wording
    # unconditionally, because that file is the only carrier those clients read.
    for client_id in _LIGHT_MODE:
        minimal = ProtocolRenderer(client_profile=resolve_client_profile(client_id)).render_minimal_protocol()
        assert minimal.count(_FULL_GATE_MARKER) == 1, f"{client_id} minimal protocol must state the gate exactly once"


def test_repo_claude_md_states_the_gate_once() -> None:
    """FR09 acceptance for this repository's own carrier.

    ``CLAUDE.md`` stated the three-path gate twice: once in repo-owned prose and
    once inside the generated block. The prose copy is now a pointer.

    The CARRIER is the file plus whatever it imports, not the file's own bytes.
    PRD-CORE-240/243 externalized the generated block to a single
    ``@.trw/INSTRUCTIONS.md`` line, so counting only CLAUDE.md's bytes would read
    zero and call a correctly-carried gate a governance regression. The import is
    resolved the same way the instruction-surface lint resolves it: one level,
    one line.
    """
    from pathlib import Path as _Path

    repo_root = _Path(__file__).resolve().parents[2]
    repo_claude_md = repo_root / "CLAUDE.md"
    if not repo_claude_md.is_file():
        pytest.skip("monorepo-only: repo-root CLAUDE.md absent in the standalone mirror")

    content = repo_claude_md.read_text(encoding="utf-8")
    resolved = [content]
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("@") and not stripped.startswith("@@"):
            imported = repo_root / stripped[1:]
            if imported.is_file():
                resolved.append(imported.read_text(encoding="utf-8"))

    total = sum(part.count(_FULL_GATE_MARKER) for part in resolved)
    assert total == 1, (
        f"the CLAUDE.md carrier (file + resolved imports) states the gate {total} times; the "
        "invariant is one full statement per carrier, pointers elsewhere — and never zero"
    )
    assert content.count(_FULL_GATE_MARKER) == 0, (
        "the repo-owned prose copy must stay a pointer now that the generated block is imported"
    )


def test_the_offline_substitute_table_reaches_an_instruction_surface() -> None:
    """FR02's instruction-surface arm, and the fix for PRD-FIX-073-FR03's wiring defect.

    That defect was a renderer whose output a test asserted and which reached
    zero consumers. This asserts the CONSUMER's text (``render_closing_reminder``,
    which the CLAUDE.md template's ``closing_reminder`` slot renders), and that it
    names a substitute for every obligation rather than the two it used to.
    """
    from trw_mcp.state.claude_md._static_sections import render_closing_reminder

    content = render_closing_reminder()
    for substitute in (
        "trw-mcp local status",
        "trw-mcp local init --task",
        "trw-mcp local checkpoint --message",
        "trw-mcp local learn --summary",
        "trw-mcp local recall --query",
        "trw-mcp local deliver --message",
        "trw-mcp local feedback --category",
    ):
        assert substitute in content, f"{substitute} missing from the instruction-surface offline table"
    assert "reports/" in content and "exit code" in content, "the build-check substitute must name its artifact"
    assert "gate_evaluated: false" in content
    assert "trw-reconcile-pending" in content


@pytest.mark.parametrize("client_id", sorted(_BASELINE_BLOCK_CHARS))
def test_session_boundary_preservation_is_not_completed_delivery(client_id: str) -> None:
    """CORE269: full/light renderers retain the distinction alongside the gate."""
    text = ProtocolRenderer(client_profile=resolve_client_profile(client_id)).render_behavioral_protocol()
    assert "unfinished" in text
    assert "handoff" in text
    assert "next-read" in text
    assert "nothing material to preserve" in text.lower()
    assert "completed-work acceptance" in text
    assert "instead of being lost" not in text
    assert DELIVER_GATE_PHRASE in text
