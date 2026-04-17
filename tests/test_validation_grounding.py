from pathlib import Path

import pytest

from trw_mcp.state.validation._prd_scoring import compute_grounding_penalty, get_project_files


def test_get_project_files(tmp_path: Path):
    (tmp_path / "valid_file.py").write_text("print('hello')")
    (tmp_path / "test_file.py").write_text("print('hello')")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("cfg")

    files = get_project_files(tmp_path)
    assert "valid_file.py" in files
    assert "test_file.py" in files
    assert "config" not in files
    assert ".git/config" not in files


def test_compute_grounding_penalty(tmp_path: Path):
    (tmp_path / "valid.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_valid.py").write_text("")

    content = """
    We will modify `valid.py` and test it in `tests/test_valid.py`.
    We will also touch `hallucinated.py` and `tests/test_hallucinated.py`.
    And create a new file `new: new_file.py` and `another_new.py (new)`.
    """

    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)

    assert len(hallucinated) == 2
    assert "hallucinated.py" in hallucinated
    assert "tests/test_hallucinated.py" in hallucinated

    assert penalty == pytest.approx(0.9**2)


def test_compute_grounding_penalty_no_hallucinations(tmp_path: Path):
    (tmp_path / "valid.py").write_text("")
    content = "Update `valid.py`."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert penalty == 1.0
    assert len(hallucinated) == 0


def test_compute_grounding_penalty_no_project_root():
    content = "Update `valid.py`."
    penalty, hallucinated = compute_grounding_penalty(content, None)
    assert penalty == 1.0
    assert len(hallucinated) == 0
