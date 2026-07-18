"""Internal skill bodies must not advertise unreachable direct invocations."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRD_REVIEW_SURFACES = (
    ROOT / "trw-mcp/src/trw_mcp/data/skills/trw-prd-review/SKILL.md",
    ROOT / "trw-mcp/src/trw_mcp/data/codex/skills/trw-prd-review/SKILL.md",
    ROOT / ".claude/skills/trw-prd-review/SKILL.md",
    ROOT / ".agents/skills/trw-prd-review/SKILL.md",
    ROOT / "trw-eval/trw-mcp-local/src/trw_mcp/data/skills/trw-prd-review/SKILL.md",
    ROOT / "trw-eval/trw-mcp-local/src/trw_mcp/data/codex/skills/trw-prd-review/SKILL.md",
)
PUBLIC_GUIDANCE_SURFACES = (
    ROOT / "platform/src/app/(marketing)/docs/skills/skills-page/data.tsx",
    ROOT / "platform/src/app/(marketing)/docs/skills/skills-page/PromptMappingSection.tsx",
    ROOT / "platform/src/app/(marketing)/docs/skills/page.tsx",
    ROOT / "platform/src/app/(marketing)/docs/requirements/requirements-page/data.tsx",
    ROOT / "docs/documentation/aare-f-overview.md",
    ROOT / "docs/documentation/prd-system.md",
    ROOT / "docs/documentation/requirements-tracking.md",
    ROOT / "docs/documentation/traceability-matrix.md",
)
INTERNAL_COMMAND = re.compile(
    r"(?<![\w./-])/(?:trw-prd-groom|trw-prd-review|trw-exec-plan)(?=$|[\s`\"',.;:!?()\[\]{}<>])"
)


def test_internal_prd_review_does_not_advertise_direct_invocation() -> None:
    for path in PRD_REVIEW_SURFACES:
        text = path.read_text(encoding="utf-8")
        assert INTERNAL_COMMAND.search(text) is None, path
        assert "invoked standalone" not in text, path


def test_prd_review_remains_internal_and_pipeline_owned() -> None:
    source = PRD_REVIEW_SURFACES[0].read_text(encoding="utf-8")
    ready = (ROOT / "trw-mcp/src/trw_mcp/data/skills/trw-prd-ready/SKILL.md").read_text(encoding="utf-8")

    assert "user-invocable: false" in source
    assert "Invoke the packaged internal `trw-prd-review` contract" in ready


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
