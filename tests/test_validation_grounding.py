from pathlib import Path

import pytest

from trw_mcp.state.validation._prd_scoring import compute_grounding_penalty, get_project_files


def test_get_project_files(tmp_path: Path):
    (tmp_path / "valid_file.py").write_text("print('hello')")
    (tmp_path / "test_file.py").write_text("print('hello')")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("cfg")
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "generated.py").write_text("ignore")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.js").write_text("ignore")

    files = get_project_files(tmp_path)
    assert "valid_file.py" in files
    assert "test_file.py" in files
    assert "config" not in files
    assert ".git/config" not in files
    assert ".trw/generated.py" not in files
    assert "node_modules/package.js" not in files


def test_compute_grounding_penalty(tmp_path: Path):
    (tmp_path / "valid.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_valid.py").write_text("")

    content = """
    We will modify `src/valid.py` and test it in `tests/test_valid.py`.
    We will also touch `src/hallucinated.py` and `tests/test_hallucinated.py`.
    And create a new file `new: src/new_file.py` and `src/another_new.py (new)`.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src/valid.py").write_text("")

    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)

    assert len(hallucinated) == 2
    assert "src/hallucinated.py" in hallucinated
    assert "tests/test_hallucinated.py" in hallucinated

    assert penalty == pytest.approx(0.9**2)


def test_compute_grounding_penalty_no_hallucinations(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/valid.py").write_text("")
    content = "Update `src/valid.py`."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert penalty == 1.0
    assert len(hallucinated) == 0


def test_compute_grounding_penalty_skips_bare_filenames_without_repo_walk(tmp_path: Path) -> None:
    content = "Mention `bare_missing.py`; PRD integrity handles bare filename resolution."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert penalty == 1.0
    assert hallucinated == []


def test_compute_grounding_penalty_no_project_root():
    content = "Update `valid.py`."
    penalty, hallucinated = compute_grounding_penalty(content, None)
    assert penalty == 1.0
    assert len(hallucinated) == 0
