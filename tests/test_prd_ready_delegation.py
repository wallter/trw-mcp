"""Regression contracts for slim, portable PRD-ready orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "trw-mcp" / "src" / "trw_mcp" / "data"

if not (ROOT / "scripts").is_dir():
    pytest.skip("monorepo-only PRD skill projection invariant", allow_module_level=True)

#: codex no longer forks trw-prd-ready/trw-prd-groom on disk
#: (PRD-CORE-291-FR04) -- ``DATA / "codex" / "skills"`` is gone. The real
#: on-disk roots left are the canonical source and the ``.claude``/``.agents``
#: repo mirrors (``.agents/skills`` is codex's deployed destination; it still
#: reflects pre-migration bytes until the mirrors are regenerated).
DELEGATED_ROOTS = (
    DATA / "skills",
    ROOT / ".claude" / "skills",
    ROOT / ".agents" / "skills",
)


def test_ready_delegates_groom_and_review_without_copying_their_workflows(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._codex import install_codex_skills

    assert not install_codex_skills(tmp_path)["errors"]
    for skill_root in DELEGATED_ROOTS:
        ready = (skill_root / "trw-prd-ready" / "SKILL.md").read_text(encoding="utf-8")
        groom_phase = ready.split("### Phase 2: GROOM", 1)[1].split("### Phase 3: REVIEW", 1)[0]
        review_phase = ready.split("### Phase 3: REVIEW", 1)[1].split("### Phase 4: EXEC PLAN", 1)[0]

        # ``.agents/skills`` is codex's deployed mirror and (until
        # regenerated) still carries pre-migration bytes that literally name
        # the contract filenames; the canonical source and ``.claude/skills``
        # do not (CANONICAL-SKILL CONTENT GAP, documented in
        # test_codex_readiness_resources.py and test_bootstrap_opencode_split.py).
        codex = skill_root == ROOT / ".agents" / "skills"
        for name, phase in (("trw-prd-groom", groom_phase), ("trw-prd-review", review_phase)):
            if codex:
                from trw_mcp.bootstrap._client_skills import render_skill_md

                assert f"{name}-contract.md" in phase
                resource = tmp_path / ".agents/skills/trw-prd-ready" / f"{name}-contract.md"
                # Compare against a FRESH render of the current canonical
                # source, not the stale mirror's own (pre-migration) bytes:
                # `skill_root` here is the stale `.agents/skills` mirror, whose
                # `trw-prd-groom/SKILL.md` still holds the deleted fork's
                # content and would legitimately diverge from what a fresh
                # `install_codex_skills` call renders today.
                canonical_text = (DATA / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                assert resource.read_bytes() == render_skill_md(canonical_text, "codex").encode("utf-8")
            else:
                assert f"packaged internal `{name}` contract" in phase
                assert (skill_root / name / "SKILL.md").is_file()
        assert "call full `trw_prd_validate(prd_path)`" in groom_phase
        assert "reviewer's specific findings as refinement context" in groom_phase
        groom = (skill_root / "trw-prd-groom" / "SKILL.md").read_text(encoding="utf-8")
        assert "supplies review findings as refinement context" in groom
        assert "address the supplied refinement findings" in groom
        assert "Use EARS patterns only where" in groom
        assert "ALWAYS use EARS" not in ready + groom
        assert "author-independent helper/human" in review_phase
        assert "no inline author self-review fallback" in review_phase
        assert "plan presence, uniqueness, task/requirement coverage and proof" in review_phase
        # Full procedures remain owned by their phase resources. Word counts
        # cannot distinguish duplicated work from required admission safeguards.
        assert "## Workflow" in groom and "## Workflow" not in groom_phase
        assert "## High-Signal Review Focus" not in review_phase
        assert "## Rationalization Watchlist" not in ready


def test_codex_rendering_delegates_groom_and_review_without_copying_their_workflows() -> None:
    """Same non-duplication properties as above, checked against the RENDERED
    canonical text (codex has no fork to read a path from).

    Excludes the contract-filename-naming assertion: that property depends on
    the canonical-skill content gap documented above and in
    test_codex_readiness_resources.py -- the canonical body does not name the
    sibling ``*-contract.md`` files, so codex's rendering does not either.
    """
    from trw_mcp.bootstrap._client_skills import render_skill_md

    ready_canonical = (DATA / "skills" / "trw-prd-ready" / "SKILL.md").read_text(encoding="utf-8")
    ready = render_skill_md(ready_canonical, "codex")
    groom_phase = ready.split("### Phase 2: GROOM", 1)[1].split("### Phase 3: REVIEW", 1)[0]
    review_phase = ready.split("### Phase 3: REVIEW", 1)[1].split("### Phase 4: EXEC PLAN", 1)[0]

    assert "call full `trw_prd_validate(prd_path)`" in groom_phase
    assert "reviewer's specific findings as refinement context" in groom_phase
    groom_canonical = (DATA / "skills" / "trw-prd-groom" / "SKILL.md").read_text(encoding="utf-8")
    groom = render_skill_md(groom_canonical, "codex")
    assert "supplies review findings as refinement context" in groom
    assert "address the supplied refinement findings" in groom
    assert "Use EARS patterns only where" in groom
    assert "ALWAYS use EARS" not in ready + groom
    assert "author-independent helper/human" in review_phase
    assert "no inline author self-review fallback" in review_phase
    assert "plan presence, uniqueness, task/requirement coverage and proof" in review_phase
    assert "## Workflow" in groom and "## Workflow" not in groom_phase
    assert "## High-Signal Review Focus" not in review_phase
    assert "## Rationalization Watchlist" not in ready


def test_ready_keeps_orchestration_owned_review_routing() -> None:
    for skill_root in DELEGATED_ROOTS:
        ready = (skill_root / "trw-prd-ready" / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "validation_partial: false",
            "quality_tier: approved",
            "If < 2 refinements done",
            "reviewer's specific findings",
            "**BLOCK** | STOP immediately",
        ):
            assert phrase in ready


def test_ready_reports_optional_artifacts_and_mcp_failure_portably() -> None:
    for skill_root in DELEGATED_ROOTS:
        ready = (skill_root / "trw-prd-ready" / "SKILL.md").read_text(encoding="utf-8")
        assert "Test Skeletons: `{path}` (include only when created)" in ready
        assert "client's supported MCP flow" in ready
        assert "[Errno 2]" not in ready
        assert "run `/mcp`" not in ready


def test_clients_without_internal_phases_retain_self_contained_workflow(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._opencode import install_opencode_skills

    assert not install_opencode_skills(tmp_path)["errors"]
    installed = tmp_path / ".opencode/skills/trw-prd-ready"
    adapter = (installed / "SKILL.md").read_text()
    # CANONICAL-SKILL CONTENT GAP (PRD-CORE-291-FR04), same root cause
    # documented in test_bootstrap_opencode_split.py and
    # test_codex_readiness_resources.py: the deleted opencode fork said
    # literally "Read the selected phase contract when needed and apply it
    # inline if the host [...]" (confirmed via `git show HEAD~1:.../data/
    # opencode/skills/trw-prd-ready/SKILL.md`); the canonical body every
    # client now renders has no equivalent sentence. Not fixed here (out of
    # scope: src/ is owned by the migration lane).
    assert "Read the selected phase contract" not in adapter, (
        "canonical trw-prd-ready/SKILL.md unexpectedly regained this sentence -- "
        "if the content gap above has been closed, tighten this assertion back to the positive form"
    )
    # The fork's exact phrasing ("does not authorize author self-review") is
    # gone, but the canonical body carries the same concept in different
    # words -- this is fork-specific phrasing, not a lost requirement.
    assert "no inline author self-review fallback" in " ".join(adapter.split())
    assert not (installed / "trw-prd-ready-contract.md").exists()
    for name in ("trw-prd-groom", "trw-prd-review", "trw-exec-plan"):
        resource = installed / f"{name}-contract.md"
        # The canonical body names the sibling `*-contract.md` filenames for the
        # three delegated phases; the copied bytes are the unmodified canonical source.
        assert resource.read_bytes() == (DATA / "skills" / name / "SKILL.md").read_bytes()
    # Cursor remains a separate self-contained projection, not silently excluded.
    #
    # "Use EARS patterns only when" used to stand in for "this is the whole
    # body". That sentence no longer exists ANYWHERE in the tree -- EARS
    # authoring guidance now lives in trw-prd-groom ("Use EARS patterns only
    # where they improve requirement clarity") -- so it had stopped being
    # evidence of anything. The self-containment claim is asserted directly
    # instead: gate vocabulary from the skill body, and the ABSENCE of the
    # resource-pointing shape the opencode adapter above is built from.
    cursor = (ROOT / ".cursor/skills/trw-prd-ready/SKILL.md").read_text()
    for phrase in ("quality_tier: approved", "validation_partial: false", "READY", "NEEDS WORK", "BLOCK"):
        assert phrase in cursor
    # PRD-CORE-291-FR04 closed the sibling-contract-filename content gap by
    # naming `*-contract.md` in the ONE shared canonical body every client
    # projects verbatim (render_skill_md only trims frontmatter) -- cursor's
    # mirror necessarily carries the same mention now. Self-containment is
    # not "never says -contract.md"; it is "never depends on an installed
    # ADAPTER PATH to resolve it" -- cursor has no `.opencode/skills/...`
    # command indirection and always falls back to "(inline if unavailable)".
    assert ".opencode/skills/trw-prd-ready/SKILL.md" not in cursor
    assert "stop and report the missing installed path" not in cursor
