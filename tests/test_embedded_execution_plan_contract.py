"""Static default-policy contract checks, not proof an agent follows the embedded workflow."""

from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
SURFACE_PATHS = (
    PACKAGE_ROOT / "src/trw_mcp/data/skills",
    PACKAGE_ROOT / "src/trw_mcp/data/codex/skills",
    ROOT / ".claude/skills",
    ROOT / ".agents/skills",
)
SURFACES = (
    SURFACE_PATHS[0],
    SURFACE_PATHS[1],
    pytest.param(SURFACE_PATHS[2], marks=requires_monorepo),
    pytest.param(SURFACE_PATHS[3], marks=requires_monorepo),
)


@requires_monorepo
def test_combined_authoring_policy_is_in_compiled_aaref_not_only_mirrors() -> None:
    """Exercise the authoring-source compiler, not a hand-edited visible copy."""
    from trw_mcp.canons.registry import bundled_manifest_bytes, compile_registry_canon, load_registry

    registry = load_registry(bundled_manifest_bytes())
    canon = next(item for item in registry.compiled_canons if item.id == "aaref")
    compiled = compile_registry_canon(ROOT, canon)
    assert "groom requirements + plan" in compiled.core
    assert "new PRDs default to an `## Execution Plan` section in the same PRD" in compiled.reference
    assert "Existing reviewed PRDs and separate plans retain their authority" in compiled.reference
    assert "does not combine author and independent reviewer roles" in compiled.reference
    for path in (canon.combined, "AARE-F-FRAMEWORK.md", canon.runtime_combined):
        assert (ROOT / path).read_text() == compiled.combined


@pytest.mark.parametrize("surface", SURFACES)
def test_public_entry_resolves_default_without_changing_mcp_schema(surface: Path) -> None:
    text = (surface / "trw-prd-ready/SKILL.md").read_text()
    for fragment in (
        "exact standalone `--embedded-plan` option",
        "Require\nnonempty remaining input",
        "Never pass the option to `trw_prd_create`",
        "New feature descriptions default to embedded mode",
        "Existing PRD ID/path input without the option preserves its existing",
        "Explicit project/operator requirements for separate artifacts take precedence",
        "forward the resolved selected mode explicitly",
        "validation_partial: false",
        "quality_tier: approved",
        "independent review verdict is READY",
        "{prd_path}#execution-plan",
        "no extra sprint or separate plan is required",
    ):
        assert fragment in text, (surface, fragment)


@pytest.mark.parametrize("surface", SURFACES)
def test_embedded_contract_keeps_authority_proof_and_outcome_boundaries(surface: Path) -> None:
    text = (surface / "trw-exec-plan/SKILL.md").read_text()
    for fragment in (
        "resolved selected mode from `trw-prd-ready`",
        "competing authority paths",
        "Duplicate embedded sections",
        "Preserve accepted requirements",
        "whole line outside fenced code",
        "Re-read before writing",
        "return to grooming and\nreview",
        "applied/rejected/stale/unavailable",
        "never authority",
        "task-specific `trw_recall`",
        "work-to-outcome mismatch",
        "preserve the unresolved risk",
        "passing test count",
        "| Task / requirement | Outcome rationale | Owned paths/symbols | Consumer/interface |",
        "### Handoff",
        "Section existence is not verification",
        "not permission to run them automatically",
        "Re-run full PRD validation",
        "When the resolved mode is separate, write",
        "{prd_path}#execution-plan",
    ):
        assert fragment in text, (surface, fragment)


@pytest.mark.parametrize("skill", ("trw-exec-plan", "trw-prd-groom"))
def test_packaged_embedded_pilot_bodies_match(skill: str) -> None:
    bodies = [(surface / skill / "SKILL.md").read_text().split("\n# ", 1)[1] for surface in SURFACE_PATHS[:2]]
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize("skill", ("trw-exec-plan", "trw-prd-ready", "trw-prd-groom"))
@requires_monorepo
def test_embedded_pilot_bodies_match_across_clients(skill: str) -> None:
    # Client-specific frontmatter and adaptation guidance intentionally differ.
    bodies = [(surface / skill / "SKILL.md").read_text().split("\n# ", 1)[1] for surface in SURFACE_PATHS]
    if skill == "trw-prd-ready":
        # Codex installs internal phases as resources, unlike shared/Claude.
        for surface, body in zip(SURFACE_PATHS, bodies, strict=True):
            if surface in (SURFACE_PATHS[3], SURFACE_PATHS[1]):
                assert "trw-prd-review-contract.md" in body
        assert bodies[0] == bodies[2]
        assert bodies[1] == bodies[3]
    else:
        assert len(set(bodies)) == 1


@pytest.mark.parametrize("surface", SURFACES)
def test_experimental_admission_does_not_require_score_padding(surface: Path) -> None:
    ready = (surface / "trw-prd-ready/SKILL.md").read_text()
    plan = (surface / "trw-exec-plan/SKILL.md").read_text()
    for fragment in (
        "**Legacy skip if:**",
        "**Embedded route:**",
        "Tier remains diagnostic",
        "**Embedded review:**",
        "**Legacy review:**",
        "**Embedded review routing:** do not invoke the legacy",
        "no inline author self-review fallback",
        "never reinterpret a legacy NEEDS WORK as slice approval",
        "BLOCK, review/tool failure, missing evidence, or exhausted cycles stops",
        "Policy approval alone is not admission",
        "Security, delivery and existing-input legacy gates are unchanged",
    ):
        assert fragment in ready, (surface, fragment)
    for fragment in (
        "document score/tier is diagnostic, not an approved-tier prerequisite",
        "**V1 (restricted route):** one named planning/artifact-mutation transition",
        "input SHA256, source root, validation receipt",
        "requirement IDs, accepted intent, allowed paths/actions and excluded actions",
        "load-bearing linked intent, policy/experiment recipe and skills",
        "author-independent reviewer identity/context",
        "same-input/link-digest READY",
        "In V1 also check the exact input",
        "Preserve pre/post bytes",
        "not production implementation",
        "relabel either as approved",
        "subsequent slices/resumes need renewed review",
        "an existing receipt cannot be upgraded retrospectively",
    ):
        assert fragment in plan, (surface, fragment)


@pytest.mark.parametrize("surface", SURFACES)
def test_reconciliation_scope_has_bounded_reuse_and_renewal_guards(surface: Path) -> None:
    """Instruction regression only; fresh-actor trials must establish adherence."""
    plan = (surface / "trw-exec-plan/SKILL.md").read_text()
    for fragment in (
        "Missing explicit reconciliation permission retains V1",
        "initial independent review explicitly permits",
        "frozen bytes outside Execution plan, existing tasks/owners/interfaces",
        "actions/exclusions, proof standards and the exact dependency digests",
        "do not rerun\n  substantive admission merely because",
        "inspect current PRD and supporting receipts",
        "Check exact reviewed dependency digests",
        "changed\nlinks stop reuse, not an actor judgment",
        "evidence, not new authority",
        "cumulative\nsection changes against the reviewed baseline",
        "unexplained\nedits or competing authority stop reuse",
        "It may not\nadd tasks, change owners/interfaces",
        "authorize a new command, revise implementation strategy or grant privileges",
        "Compression preserves material decisions, failed/missing proof and exclusions",
        "Renew substantive review for changed intent/acceptance/non-goals",
        "new\ncritical facts contradicting a material assumption",
        "Recording a newly found blocker is allowed; advancing past it is not",
        "Uncertainty stops reuse",
        "with this editor's starting snapshot; mismatch stops the write",
        "not atomic CAS",
        "Independent evidence assessment is required",
        "a passing command alone does not\ncertify the requirement",
        "invalid/partial output stops",
        "corrected under separately reviewed scope, never a silent\nsecond write",
    ):
        assert fragment in plan, (surface, fragment)


@pytest.mark.parametrize("surface", SURFACES)
def test_caller_routes_allow_only_explicit_reuse_without_conflicting_v1_rule(surface: Path) -> None:
    ready = (surface / "trw-prd-ready/SKILL.md").read_text()
    phases = ready.split("### Phase ")
    groom, review, execution = phases[3:6]
    assert "require valid/nonpartial" in groom
    assert "initial-draft branch NOW" in groom
    assert "only explicit reconciliation-only scope permits reuse" in review
    assert "without fresh substantive review" in review
    assert "V1 receipts stay one-transition; never upgrade retrospectively" in review
    assert "when required, use an author-independent" in review
    assert "selected admission" in execution
    assert "including its permitted reuse checks" in execution
    # The caller delegates mechanics; the owner still carries the substantive guard.
    owner = (surface / "trw-exec-plan/SKILL.md").read_text()
    assert "Independent evidence assessment is required" in owner
    for text in (ready, (surface / "trw-exec-plan/SKILL.md").read_text()):
        assert "before any subsequent slice/resume" not in text
        assert "an earlier receipt does not authorize reuse after the digest changes" not in text


@pytest.mark.parametrize("surface", SURFACES)
def test_installed_section_updater_consumer_keeps_permission_boundaries(surface: Path) -> None:
    text = (surface / "trw-exec-plan/SKILL.md").read_text()
    for fragment in (
        "trw_mcp.state.prd_sections.update_execution_plan",
        "expected_sha256",
        "helper does not perform",
        "sibling `.lock` and temporary-file writes",
        "target-only write restriction",
        "disclose the manual fallback",
    ):
        assert fragment in text, (surface, fragment)


@pytest.mark.parametrize("surface", SURFACES)
def test_initial_authoring_requires_live_creation_and_bounded_reviewed_repairs(surface: Path) -> None:
    """Static ownership guards, not actor-level proof of provenance or consumption."""
    ready = (surface / "trw-prd-ready/SKILL.md").read_text()
    groom = (surface / "trw-prd-groom/SKILL.md").read_text()
    plan = (surface / "trw-exec-plan/SKILL.md").read_text()
    for fragment in (
        "successful creation result and its exact output",
        "path as this invocation's initial-authoring provenance",
        "Never infer permission",
        "from draft status, a missing plan, an existing ID/path, or failed/uncertain creation",
        "up to two NEEDS WORK repair cycles",
        "within the original user scope",
        "For newly created input only: Every revision invalidates prior review reuse",
        "embedded creation with successful Phase 1",
        "read-only; invalid/partial existing input",
        "owner alone handles research",
        "Only for legacy or authorized embedded authoring, invoke",
        "never substitute a local loop",
    ):
        assert fragment in ready, (surface, fragment)
    for fragment in (
        "one selected readiness predicate throughout baseline, early exit",
        "**default target** requires full nonpartial validation",
        "risk-scaled `quality_tier: approved`",
        "**initial-authoring target** applies only",
        "successful creation result bound to the exact output path",
        "retained initial-authoring provenance",
        "no unresolved substantive blockers; tier is diagnostic",
        "failed/uncertain creation do not select this target",
        "up to two NEEDS WORK repair cycles",
        "fresh author-independent review",
        "Existing-input repair still requires separately authorized scope",
        "without it, stop read-only",
        "Initial authoring preserves lifecycle/status and version unless separately authorized",
        "not a new public option, tool argument, code execution permission",
        "Exit only when the selected readiness predicate passes",
        "**Validation loop** (max 3 iterations)",
        "no evidence-backed progress toward validity, not five-point score movement",
        "Success requires the same selected readiness predicate",
    ):
        assert fragment in groom, (surface, fragment)
    for fragment in (
        "Before planning mutation",
        "No prior planning review is required on this branch.",
        "original authoring scope",
        "requires separately authorized repair scope",
        "No code execution permission",
    ):
        assert fragment in plan, (surface, fragment)


@pytest.mark.parametrize("surface", SURFACES)
def test_new_embedded_drafts_whole_artifact_before_one_review(surface: Path) -> None:
    ready = (surface / "trw-prd-ready/SKILL.md").read_text()
    groom = (surface / "trw-prd-groom/SKILL.md").read_text()
    plan = (surface / "trw-exec-plan/SKILL.md").read_text()
    authoring = ready.split("### Phase 2:", 1)[1].split("### Phase 3:", 1)[0]
    review = ready.split("### Phase 3:", 1)[1].split("### Phase 4:", 1)[0]
    handoff = ready.split("### Phase 4:", 1)[1].split("## Final Report", 1)[0]
    assert "initial-draft branch NOW" in authoring
    assert "validate the whole requirements+plan artifact before Phase 3" in authoring
    assert review.index("Every revision invalidates prior review reuse") < review.index(
        "ONE combined review boundary per candidate"
    )
    assert "intent coverage, tasks, ownership, interfaces and proof, not score" in review
    assert "exact current PRD path/SHA256, full-validation receipt" in review
    assert "load-bearing intent/dependency digests" in review
    assert "Recheck reviewed bytes and dependency" in handoff
    assert "digests; STOP if changed" in handoff
    assert "read-only handoff: no postreview" in handoff
    assert "Return the reviewed artifact unchanged" in handoff
    assert "do not add a redundant implementation ceremony" in handoff
    assert "Planning-only requests never imply" in handoff
    assert "first substantively assess original intent coverage" in groom
    assert "even if the skeleton is already valid" in groom
    assert "do not require pointless rewriting" in groom
    initial = plan.split("### Initial draft", 1)[1].split("### Existing input", 1)[0]
    assert "No prior planning review is required" in initial
    assert "new user-scope uncertainty stops" in initial
    assert "After READY this branch is read-only" in initial
    assert "Do not create a placeholder" in plan
    assert "replacement-only helper for initial append" in plan
    assert "For an existing section, update in place" in plan


@pytest.mark.parametrize("surface", SURFACES)
def test_default_is_resolved_before_creation_and_not_reclassified(surface: Path) -> None:
    """Static policy guards only, not an executed argument parser or actor trial."""
    ready = (surface / "trw-prd-ready/SKILL.md").read_text()
    detection = ready.split("## Input Detection", 1)[1].split("## Pipeline Phases", 1)[0]
    assert "classify the original remaining input once" in detection
    assert "Do not reclassify a newly created path as existing input" in detection
    assert "--embedded-plan` remains a compatibility option" in detection
    assert "missing plan or draft status does not authorize migration" in detection
    assert "not a new MCP parameter" in detection
    assert "ordinary tasks do not require a PRD or sprint" in ready
    initial = ready.split("### Phase 2:", 1)[1].split("### Phase 3:", 1)[0]
    assert "resolved selected mode and creation provenance" in initial


@pytest.mark.parametrize("surface", SURFACES)
def test_input_identity_is_not_a_reference_inside_feature_prose(surface: Path) -> None:
    """Text-only classifier contract; this does not execute a parser or model."""
    ready = (surface / "trw-prd-ready/SKILL.md").read_text()
    detection = ready.split("## Input Detection", 1)[1].split("## Pipeline Phases", 1)[0]
    for fragment in (
        "entire remaining argument identifies one PRD ID",
        "entire remaining argument is an explicit document path",
        "Mentioning an ID or path inside a feature description does not select existing input",
        "Add validation to scripts/check_exec_plan_paths.py",
        "Add export support compatible with PRD-CORE-020",
        "an explicitly selected existing path is missing, stop and report it",
        "do not infer new-creation authority",
    ):
        assert fragment in detection, (surface, fragment)
    assert "contains `/` or `.md`" not in detection


@pytest.mark.parametrize("surface", SURFACES)
def test_new_creation_has_bounded_finding_directed_repair(surface: Path) -> None:
    """Static policy regressions, not evidence an actor obeys the repair loop."""
    ready = (surface / "trw-prd-ready/SKILL.md").read_text()
    for fragment in (
        "up to two NEEDS WORK repair cycles",
        "within the original user scope",
        "Creation provenance is retained, not consumed as a one-use editing permission",
        "For newly created input only: Every revision invalidates prior review reuse",
        "full validation plus fresh author-independent review of exact revised bytes",
        "ONE combined review boundary per candidate",
        "BLOCK, review/tool failure, missing evidence, or exhausted cycles stops",
        "plan presence, uniqueness, task/requirement coverage and proof",
        "not an automated plan-coverage guarantee",
    ):
        assert fragment in ready, (surface, fragment)
    for skill in ("trw-prd-groom", "trw-exec-plan"):
        body = (surface / skill / "SKILL.md").read_text()
        assert "up to two NEEDS WORK repair cycles" in body
        assert "within the original user scope" in body
        assert "fresh author-independent review" in body


@pytest.mark.parametrize("surface", SURFACES)
def test_existing_recall_precedes_preflight_decisions_and_is_reused(surface: Path) -> None:
    """Instruction ordering only; not proof of actor adherence or useful memory."""
    text = (surface / "trw-prd-ready/SKILL.md").read_text()
    preflight = text.split("### Phase 0: PREFLIGHT", 1)[1].split("### Phase 1: CREATE", 1)[0]
    create = text.split("### Phase 1: CREATE", 1)[1].split("### Phase 2: GROOM", 1)[0]
    assert preflight.index("Before framing questions or assumptions") < preflight.index("Ask unresolved questions")
    assert "task-specific `trw_recall`" in preflight
    assert "not authority" in preflight
    assert "existing decision tree" in preflight
    assert "Reuse the inspected preflight evidence" in create
    assert "If preflight was skipped" in create
    assert "not merely because the phase changed" in create


@pytest.mark.parametrize("surface", SURFACES)
def test_progress_recording_does_not_reopen_readiness(surface: Path) -> None:
    """Policy wiring, not proof of actor adherence or machine authorization."""
    plan = (surface / "trw-exec-plan/SKILL.md").read_text()
    progress = plan.split("## Recording progress is not planning admission", 1)[1].split("## Readiness gate", 1)[0]
    for fragment in (
        "already authorized work",
        "failed or missing proof",
        "Do not invoke readiness",
        "solely to preserve these observations",
        "never expands write authority",
        "historical one-transition receipt is not upgraded",
        "cumulative changes",
        "reviewed substantive baseline",
        "Renew independent review before advancing",
        "Recording a blocker is allowed",
        "existing authorized handoff",
        "byte-preserving update procedure",
        "does not\nextend an old approval",
        "independent evidence assessment",
        "actual delivery\ngates remain required",
    ):
        assert fragment in progress, (surface, fragment)
    ready = (surface / "trw-prd-ready/SKILL.md").read_text()
    routing = ready.split("## Progress-only requests", 1)[1].split("## Input Detection", 1)[0]
    assert "without running CREATE/GROOM/REVIEW again" in routing
    assert "substantive changes still use the readiness route" in routing
