"""REF-001: the grounding penalty is applied to two dimensions by specification
(PRD-QUAL-063-FR04), but one validation now scans the filesystem ONCE for both."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import get_config
from trw_mcp.state.validation import _prd_scoring_grounding as grounding
from trw_mcp.state.validation import validate_prd_quality_v2

_PRD = """---
prd:
  id: PRD-FIX-999
  title: grounding probe
  version: '1.0'
  status: draft
  priority: P2
  category: FIX
---
# PRD-FIX-999: grounding probe

## 1. Problem Statement
The change touches `src/real_module.py` and `src/missing_module.py`.

## 4. Functional Requirements
### PRD-FIX-999-FR01: Do it
Implementation in `src/real_module.py`; test in `tests/test_missing.py`.

## 6. Traceability Matrix
| Requirement | Source | Test |
|---|---|---|
| FR01 | `src/missing_module.py` | `tests/test_missing.py` |
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real_module.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = grounding.compute_grounding_penalty

    def counted(content: str, project_root: Path | None, **kwargs: object) -> tuple[float, list[str]]:
        calls.append(str(project_root))
        return real(content, project_root, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(grounding, "compute_grounding_penalty", counted)
    return calls


def test_one_validation_scans_once_and_both_dimensions_keep_the_penalty(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _counting(monkeypatch)
    result = validate_prd_quality_v2(
        _PRD, get_config(), risk_level="medium", project_root=project, include_dynamic_checks=True
    )
    assert calls == [str(project)], "one filesystem scan per validation, refresh included"
    penalised = {d.name: d.details.get("hallucinated_paths") for d in result.dimensions}
    assert penalised["traceability"] and penalised["implementation_readiness"], (
        "PRD-QUAL-063-FR04 applies the penalty to BOTH dimensions; only the scan is shared"
    )


def test_a_static_validation_does_not_scan(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _counting(monkeypatch)
    validate_prd_quality_v2(_PRD, get_config(), risk_level="medium", project_root=project, include_dynamic_checks=False)
    assert calls == []


def test_nothing_is_cached_across_validations(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _counting(monkeypatch)
    for _ in range(2):
        validate_prd_quality_v2(
            _PRD, get_config(), risk_level="medium", project_root=project, include_dynamic_checks=True
        )
    assert len(calls) == 2
    assert grounding._SCOPE_MEMO.get() is None


def test_the_shared_result_is_the_computed_result(project: Path) -> None:
    direct = grounding.compute_grounding_penalty(_PRD, project)
    with grounding.grounding_scope():
        first = grounding.grounding_penalty_once(_PRD, project)
        second = grounding.grounding_penalty_once(_PRD, project)
    assert first == second == direct
    assert direct[1], "the probe PRD names missing paths"
