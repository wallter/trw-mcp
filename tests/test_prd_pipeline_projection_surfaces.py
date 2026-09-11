"""Cross-client PRD review and execution-plan pipeline contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
DATA = PACKAGE_ROOT / "src" / "trw_mcp" / "data"


PIPELINE_PATHS = (
    DATA / "skills" / "trw-prd-ready" / "SKILL.md",
    DATA / "codex" / "skills" / "trw-prd-ready" / "SKILL.md",
    ROOT / ".claude" / "skills" / "trw-prd-ready" / "SKILL.md",
    ROOT / ".agents" / "skills" / "trw-prd-ready" / "SKILL.md",
    ROOT / ".cursor" / "skills" / "trw-prd-ready" / "SKILL.md",
)


@pytest.mark.parametrize("index", [0, 1, *[pytest.param(i, marks=requires_monorepo) for i in (2, 3, 4)]])
def test_ready_delegates_one_exec_plan_contract_without_inline_duplication(index: int) -> None:
    path = PIPELINE_PATHS[index]
    content = path.read_text(encoding="utf-8")
    phase = content.split("### Phase 4: EXEC PLAN", 1)[1].split("## Final Report", 1)[0]
    if index in (1, 3):
        assert "trw-exec-plan-contract.md" in phase
    else:
        assert "packaged internal `trw-exec-plan` contract" in phase
    # Delegation rather than a second task-planning procedure, not a word quota.
    assert "### Draft tasks" not in phase
    assert "## Rationalization Watchlist" not in phase
    assert "0.85" not in content
    assert "0.70" not in content


def test_opencode_ready_delivers_reviewed_execution_plan(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._opencode import install_opencode_commands, install_opencode_skills

    assert not install_opencode_commands(tmp_path)["errors"]
    assert not install_opencode_skills(tmp_path)["errors"]
    directory = tmp_path / ".opencode/skills/trw-prd-ready"
    command = (tmp_path / ".opencode/commands/trw-prd-ready.md").read_text()
    assert ".opencode/skills/trw-prd-ready/SKILL.md" in command
    skill = (directory / "SKILL.md").read_text()
    for phase in ("trw-prd-ready", "trw-prd-groom", "trw-prd-review", "trw-exec-plan"):
        resource = directory / f"{phase}-contract.md"
        assert resource.name in skill
        assert resource.read_bytes() == (DATA / "skills" / phase / "SKILL.md").read_bytes()
    owner = (directory / "trw-prd-ready-contract.md").read_text()
    for phrase in (
        "author-independent helper/human",
        "no inline author self-review fallback",
        "Legacy output: configured separate plan",
        "{prd_path}#execution-plan",
        "read-only handoff",
        "full-validation receipt",
    ):
        assert phrase in owner
    assert "migration/rollback" in (directory / "trw-exec-plan-contract.md").read_text()
    assert "stop and report" in skill and "stop and report" in command
    assert not (tmp_path / ".git").exists()


def test_exec_plan_is_evidence_sized_and_project_native() -> None:
    for path in (
        DATA / "skills" / "trw-exec-plan" / "SKILL.md",
        DATA / "codex" / "skills" / "trw-exec-plan" / "SKILL.md",
    ):
        content = path.read_text(encoding="utf-8")
        for phrase in (
            "validation_partial: false",
            "quality_tier: approved",
            "behavior/acceptance criterion",
            "project-native",
            "migration/rollback",
            "Do not split solely by line count",
        ):
            assert phrase in content
        assert "Est. Time" not in content
        assert "All tests SHOULD FAIL" not in content


# ---------------------------------------------------------------------------
# PRD-QUAL-120-FR01: consumer inventory and authority map
# ---------------------------------------------------------------------------


def test_prd_qual_120_fr01() -> None:
    """FR01 acceptance: Given the inventory fixture, When authority validation
    runs, Then each field has exactly one writer, every projection names its
    source, and AcceptanceManifest has no write path into PRD source, INDEX,
    or ROADMAP."""
    from trw_mcp.state.prd_utils import (
        REQUIREMENTS_AUTHORITY_MAP,
        AuthorityEntry,
        validate_requirements_authority,
    )

    # The shipped inventory satisfies the single-writer contract.
    assert validate_requirements_authority() == []
    assert validate_requirements_authority(REQUIREMENTS_AUTHORITY_MAP) == []
    # Every projection/derived surface names its source.
    for entry in REQUIREMENTS_AUTHORITY_MAP:
        if entry.kind in ("derived", "projection"):
            assert entry.source, entry.field_name

    # Negative: a second writer for one field fails.
    dup = (
        AuthorityEntry(field_name="x", writer="a", kind="source"),
        AuthorityEntry(field_name="x", writer="b", kind="source"),
    )
    failures = validate_requirements_authority(dup)
    assert any("multiple writers" in failure for failure in failures)

    # Negative: a projection without a source fails.
    unsourced = (AuthorityEntry(field_name="y", writer="w", kind="projection"),)
    assert any("must name its source" in f for f in validate_requirements_authority(unsourced))

    # Negative: a manifest write path into authored truth or projections fails.
    for bad in (
        "docs/requirements-aare-f/prds",
        "docs/requirements-aare-f/INDEX.md",
        "docs/requirements-aare-f/ROADMAP.md",
    ):
        offending = (
            AuthorityEntry(
                field_name="acceptance_state",
                writer="state/acceptance_manifest.py",
                kind="derived",
                source="raw_prd_bytes",
                write_paths=(bad,),
            ),
        )
        assert any("no write path" in f for f in validate_requirements_authority(offending)), bad


def test_prd_qual_120_fr03(tmp_path) -> None:
    """FR03 acceptance: Given a PRD, manifest, and registry projection, When
    manifest state changes, Then the read model and registry input change
    deterministically, PRD source bytes remain unchanged, and no digest
    feedback loop or second INDEX or ROADMAP writer exists."""
    from pathlib import Path

    from trw_mcp.state.acceptance_manifest import (
        ReceiptEvidence,
        RegistryAcceptanceView,
        derive_manifest,
        persist_manifest,
        registry_view,
    )

    prd = Path(tmp_path) / "PRD-CORE-060.md"
    prd.write_text(
        "---\nprd:\n  id: PRD-CORE-060\n  title: T\nverification:\n  mappings:\n"
        "  - requirement_id: PRD-CORE-060-FR01\n    acceptance_criteria: [c]\n"
        "    method: test\n    evidence_artifact: t.py::t\n    pass_condition: p\n---\n",
        encoding="utf-8",
    )
    source_before = prd.read_bytes()
    trw = Path(tmp_path) / ".trw"

    unknown = derive_manifest(prd, {})
    accepted = derive_manifest(prd, {"PRD-CORE-060-FR01": ReceiptEvidence("r", "sha256:" + "d" * 64)})
    persist_manifest(accepted, trw)

    # Manifest state change -> deterministic read-model change.
    view_unknown, view_accepted = registry_view(unknown), registry_view(accepted)
    assert view_unknown.unknown_count == 1 and view_accepted.accepted_count == 1
    assert view_unknown.manifest_digest != view_accepted.manifest_digest
    assert registry_view(accepted) == view_accepted  # deterministic

    # PRD source bytes untouched; the manifest lives out-of-band only.
    assert prd.read_bytes() == source_before

    # No second INDEX/ROADMAP writer: the adapter is a frozen value object with
    # no persistence surface, and the module exposes no catalogue writer.
    import trw_mcp.state.acceptance_manifest as manifest_module

    assert not hasattr(RegistryAcceptanceView, "persist")
    assert not any("index" in name.lower() or "roadmap" in name.lower() for name in dir(manifest_module))

    # No digest feedback: re-deriving after persistence is byte-identical.
    assert derive_manifest(prd, {}).canonical_digest() == unknown.canonical_digest()
