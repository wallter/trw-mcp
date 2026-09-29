"""PRD-INFRA-199-FR05: an open PRD touching a daemon, checkout input, an executor or a control needs the checklist."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trw_mcp.state.validation._prd_arch_shift import ARCH_SHIFT_RULE, architectural_shift_failures
from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

_TEMPLATE = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "prd_template.md"
_CHECKLIST = re.search(
    r"^### Architectural-shift checklist.*?(?=^### )", _TEMPLATE.read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL
)
assert _CHECKLIST is not None
CHECKLIST = _CHECKLIST.group(0)


def _prd(body: str, *, status: str = "draft", extra: str = "") -> str:
    return (
        "---\nprd:\n  id: PRD-INFRA-999\n  title: fixture\n"
        f"  status: {status}\n  priority: P2\n{extra}---\n\n# Fixture\n\n## 6. Technical Approach\n\n{body}\n"
    )


def _rules(content: str, **frontmatter: object) -> list[str]:
    fm: dict[str, object] = {"status": "draft", **frontmatter}
    return [f.rule for f in architectural_shift_failures(fm, content)]


def test_a_daemon_prd_without_the_checklist_is_invalid_and_told_how_to_fix_it() -> None:
    result = validate_prd_quality_v2(_prd("The memory daemon gains a lane budget."), include_dynamic_checks=False)

    [finding] = [f for f in result.failures if f.rule == ARCH_SHIFT_RULE]
    assert result.valid is False and finding.severity == "error"
    assert (
        "no 'Architectural-shift checklist' section" in finding.message
        and "architectural_shift: false" in finding.message
    )


@pytest.mark.parametrize(
    "body",
    [
        "Reads checkout-supplied stores.",
        "Moves work onto the executor.",
        "Closes a privacy control gap.",
        "It defeats a documented control.",
    ],
)
def test_each_trigger_requires_the_checklist(body: str) -> None:
    assert _rules(body) == [ARCH_SHIFT_RULE]


def test_the_templates_checklist_satisfies_the_rule_and_a_missing_class_is_named() -> None:
    assert _rules(f"The daemon changes.\n\n{CHECKLIST}") == []

    without_d = "\n".join(line for line in CHECKLIST.splitlines() if not line.startswith("| D |"))
    [finding] = architectural_shift_failures({"status": "draft"}, f"The daemon changes.\n\n{without_d}")
    assert "no row for class(es) D" in finding.message


def test_frontmatter_overrides_the_heuristic_both_ways() -> None:
    assert _rules("The daemon changes.", architectural_shift=False) == []
    assert _rules("A docs-only change.", architectural_shift=True) == [ARCH_SHIFT_RULE]
    assert _rules("A docs-only change.") == []


@pytest.mark.parametrize("status", ["done", "implemented", "deprecated", "superseded"])
def test_a_settled_prd_is_not_reopened(status: str) -> None:
    assert _rules("The daemon changes.", status=status) == []
