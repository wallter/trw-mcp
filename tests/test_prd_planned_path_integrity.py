"""Regression coverage for planned PRD path markers across validation layers."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.validation._prd_integrity_paths import _check_repo_path_references
from trw_mcp.state.validation._prd_scoring import compute_grounding_penalty


@pytest.mark.parametrize("marker", ["(new)", "(planned)", "(future)", "(PLANNED)"])
def test_planned_marker_exempts_missing_path_in_both_validators(tmp_path: Path, marker: str) -> None:
    """A supported trailing marker makes one missing reference explicitly planned."""
    content = f"Create `src/planned_module.py` {marker}."

    assert _check_repo_path_references(content, tmp_path) == []
    assert compute_grounding_penalty(content, tmp_path) == (1.0, [])


def test_mixed_marked_and_unmarked_occurrences_still_require_existence(tmp_path: Path) -> None:
    """One planned occurrence must not exempt a separate unmarked occurrence."""
    content = "Create `src/missing_module.py` (planned), then modify `src/missing_module.py`."

    failures = _check_repo_path_references(content, tmp_path)
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)

    assert [failure.rule for failure in failures] == ["repo_path_exists"]
    assert penalty == pytest.approx(0.9)
    assert hallucinated == ["src/missing_module.py"]


@pytest.mark.parametrize(
    ("content", "exempt"),
    [
        ("Create `src/boundary.py`" + " " * 7 + "(planned).", True),
        ("Create `src/outside.py`" + " " * 8 + "(planned).", False),
        ("(planned) then modify `src/unrelated.py`.", False),
    ],
)
def test_only_near_trailing_marker_exempts_its_path(tmp_path: Path, content: str, exempt: bool) -> None:
    """Window boundaries and unrelated prose do not weaken missing-path checks."""
    failures = _check_repo_path_references(content, tmp_path)
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)

    assert (failures == []) is exempt
    assert (hallucinated == []) is exempt
    assert penalty == pytest.approx(1.0 if exempt else 0.9)


def test_existing_unmarked_path_stays_valid(tmp_path: Path) -> None:
    """Existing paths remain accepted without requiring a planned marker."""
    path = tmp_path / "src" / "existing_module.py"
    path.parent.mkdir()
    path.write_text("", encoding="utf-8")
    content = "Modify `src/existing_module.py`."

    assert _check_repo_path_references(content, tmp_path) == []
    assert compute_grounding_penalty(content, tmp_path) == (1.0, [])


def test_marker_detection_is_load_bearing_in_both_validators(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NEGATIVE CONTROL: disable only marker detection and both exemptions vanish.

    Without this, every assertion above is also satisfied by a build that never
    collected the path at all, rather than by one that collected it and exempted
    it. The two consumers are patched SEPARATELY on purpose: each does
    ``from ... import has_trailing_planned_marker``, so the name is bound in the
    consumer module and patching the defining module alone would be a no-op --
    the classic import-time-binding trap.
    """
    from trw_mcp.state.validation import _prd_integrity_paths, _prd_scoring_grounding

    content = "Create `src/planned_module.py` (planned)."
    assert _check_repo_path_references(content, tmp_path) == []
    assert compute_grounding_penalty(content, tmp_path) == (1.0, [])

    for module in (_prd_integrity_paths, _prd_scoring_grounding):
        monkeypatch.setattr(module, "has_trailing_planned_marker", lambda *_a, **_k: False)

    assert [failure.rule for failure in _check_repo_path_references(content, tmp_path)] == ["repo_path_exists"]
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert penalty == pytest.approx(0.9)
    assert hallucinated == ["src/planned_module.py"]


@pytest.mark.parametrize("separator", [" `x` ", " prose ", "\n", "\r\n", " | ", "\v", "\f"])
def test_unattached_marker_does_not_exempt_missing_path(tmp_path: Path, separator: str) -> None:
    """A nearby annotation for another token, line or cell is not path evidence."""
    content = f"Modify `src/missing.py`{separator}(new)."

    failures = _check_repo_path_references(content, tmp_path)
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)

    assert [failure.rule for failure in failures] == ["repo_path_exists"]
    assert penalty == pytest.approx(0.9)
    assert hallucinated == ["src/missing.py"]


@pytest.mark.parametrize("separator", ["", " ", "\t", " \t "])
def test_attached_marker_accepts_horizontal_spacing(tmp_path: Path, separator: str) -> None:
    """Valid adjacent annotations still exempt planned files in both consumers."""
    content = f"Create `src/planned.py`{separator}(PLANNED)."

    assert _check_repo_path_references(content, tmp_path) == []
    assert compute_grounding_penalty(content, tmp_path) == (1.0, [])
