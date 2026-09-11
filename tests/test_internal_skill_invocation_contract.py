"""Internal skill bodies must not advertise unreachable direct invocations."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
#: Every live copy of the internal PRD-review skill (two formerly-vendored
#: mirrors were deleted wholesale in `a77650f238`; only these remain).
#: Deliberately NOT filtered with `.exists()`: a deleted surface must fail
#: loudly here, not silently drop out of the contract.
PRD_REVIEW_SURFACES = (
    PACKAGE_ROOT / "src/trw_mcp/data/skills/trw-prd-review/SKILL.md",
    PACKAGE_ROOT / "src/trw_mcp/data/codex/skills/trw-prd-review/SKILL.md",
    pytest.param(ROOT / ".claude/skills/trw-prd-review/SKILL.md", marks=requires_monorepo),
    pytest.param(ROOT / ".agents/skills/trw-prd-review/SKILL.md", marks=requires_monorepo),
)
PUBLIC_GUIDANCE_SURFACES = (
    ROOT
    / "platform/src/app/(marketing)/docs/skills/skills-page/data.tsx",  # trw-leak-allow: proprietary_path real monorepo file this test reads
    ROOT
    / "platform/src/app/(marketing)/docs/skills/skills-page/PromptMappingSection.tsx",  # trw-leak-allow: proprietary_path real monorepo file this test reads
    ROOT
    / "platform/src/app/(marketing)/docs/skills/page.tsx",  # trw-leak-allow: proprietary_path real monorepo file this test reads
    ROOT
    / "platform/src/app/(marketing)/docs/requirements/requirements-page/data.tsx",  # trw-leak-allow: proprietary_path real monorepo file this test reads
    ROOT / "docs/documentation/aare-f-overview.md",
    ROOT / "docs/documentation/prd-system.md",
    ROOT / "docs/documentation/requirements-tracking.md",
    ROOT / "docs/documentation/traceability-matrix.md",
)
INTERNAL_COMMAND = re.compile(
    r"(?<![\w./-])/(?:trw-prd-groom|trw-prd-review|trw-exec-plan)(?=$|[\s`\"',.;:!?()\[\]{}<>])"
)


@pytest.mark.parametrize("path", PRD_REVIEW_SURFACES)
def test_internal_prd_review_does_not_advertise_direct_invocation(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert INTERNAL_COMMAND.search(text) is None, path
    assert "invoked standalone" not in text, path


def test_prd_review_remains_internal_and_pipeline_owned() -> None:
    source = PRD_REVIEW_SURFACES[0].read_text(encoding="utf-8")
    ready = (PACKAGE_ROOT / "src/trw_mcp/data/skills/trw-prd-ready/SKILL.md").read_text(encoding="utf-8")

    assert "user-invocable: false" in source
    assert "Invoke the packaged internal `trw-prd-review` contract" in ready


@requires_monorepo
def test_public_guidance_does_not_advertise_internal_phase_commands() -> None:
    for path in PUBLIC_GUIDANCE_SURFACES:
        assert INTERNAL_COMMAND.search(path.read_text(encoding="utf-8")) is None, path


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Run /trw-prd-review", True),
        ("Use `/trw-prd-groom PRD-1`", True),
        ("Try (/trw-exec-plan).", True),
        ("`.agents/skills/trw-prd-review`", False),
        ("path/to/trw-exec-plan", False),
        ("trw-mcp/src/data/skills/trw-exec-plan.md", False),
    ],
)
def test_internal_command_detection_respects_token_boundaries(text: str, expected: bool) -> None:
    assert (INTERNAL_COMMAND.search(text) is not None) is expected
