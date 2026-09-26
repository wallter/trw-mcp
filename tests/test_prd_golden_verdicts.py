"""Golden PRD fixtures keep their validation verdict through the tool cut (PRD-CORE-300-FR01/FR07).

``trw_prd_validate`` stays a tool while PRD creation and diffing move to the
`trw-mcp prd create` / `trw-mcp prd diff` CLI (S5), and every slice rewrites
instruction text around it. These
three frozen PRDs (approved, review, draft tier) and their recorded verdicts
make any change to what the validator decides visible at the slice that caused
it. The pure validator is used, so a verdict does not depend on which repo
paths exist on the machine running the test.

Re-record deliberately, after a validator change that is meant to move a verdict::

    python -m tests.test_prd_golden_verdicts   # from trw-mcp/
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "golden_prds"
_VERDICTS = _FIXTURES / "verdicts.json"


def verdict(text: str) -> dict[str, object]:
    from trw_mcp.models.config import get_config
    from trw_mcp.tools.requirements import validate_prd_quality_v2

    result = validate_prd_quality_v2(text, get_config(), include_dynamic_checks=False)
    return {
        "valid": result.valid,
        "quality_tier": str(result.quality_tier.value),
        "total_score": round(result.total_score, 2),
        "failure_rules": sorted({f"{f.field}:{f.rule}" for f in result.failures}),
    }


def _fixtures() -> list[Path]:
    return sorted(_FIXTURES.glob("*.md"))


def test_the_fixture_set_spans_three_tiers() -> None:
    recorded = json.loads(_VERDICTS.read_text(encoding="utf-8"))
    assert sorted(recorded) == [path.name for path in _fixtures()]
    assert {entry["quality_tier"] for entry in recorded.values()} == {"approved", "review", "draft"}


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda path: path.stem)
def test_each_golden_prd_keeps_its_recorded_verdict(fixture: Path) -> None:
    recorded = json.loads(_VERDICTS.read_text(encoding="utf-8"))[fixture.name]
    assert verdict(fixture.read_text(encoding="utf-8")) == recorded


if __name__ == "__main__":
    _VERDICTS.write_text(
        json.dumps({path.name: verdict(path.read_text(encoding="utf-8")) for path in _fixtures()}, indent=2) + "\n",
        encoding="utf-8",
    )
