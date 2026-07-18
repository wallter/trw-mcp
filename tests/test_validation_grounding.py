from pathlib import Path

import pytest
from structlog.testing import capture_logs

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation import _prd_scoring_grounding as grounding
from trw_mcp.state.validation._prd_scoring import compute_grounding_penalty


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


def test_line_suffix_on_existing_file_not_penalized(tmp_path: Path):
    """A path-qualified ref with a :line suffix to an EXISTING file is clean
    (feedback sub_5qbmT6WPNoP58rlv item 7 gap 1)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src/foo.py").write_text("")
    content = "See `src/foo.py:42` and `src/foo.py:42:5` for the change."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert hallucinated == []
    assert penalty == 1.0


def test_line_suffix_on_missing_file_still_penalized(tmp_path: Path):
    """Regression: a :line-suffixed ref to a MISSING file is still hallucinated
    (the suffix strip must not exempt genuinely-absent files)."""
    (tmp_path / "src").mkdir()
    content = "See `src/missing.py:42` for the change."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert hallucinated == ["src/missing.py"]
    assert penalty == pytest.approx(0.9)


def test_greenfield_annotation_outside_backticks_not_penalized(tmp_path: Path):
    """A missing file annotated as greenfield OUTSIDE the backticks is exempt
    (feedback item 7 gap 2 — the real TRW convention)."""
    (tmp_path / "src").mkdir()
    content = "Create `src/new_module.py` (new) and `src/planned.py` (planned) and `src/future.py` (future)."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert hallucinated == []
    assert penalty == 1.0


def test_hallucinated_ref_without_annotation_still_penalized(tmp_path: Path):
    """Regression: an unannotated missing ref is still penalized even when a
    sibling ref on the same content IS greenfield-annotated."""
    (tmp_path / "src").mkdir()
    content = "Create `src/new_module.py` (new) but also touch `src/ghost.py`."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert hallucinated == ["src/ghost.py"]
    assert penalty == pytest.approx(0.9)


def test_extra_roots_satisfy_sibling_repo_reference(tmp_path: Path):
    """A ref that exists only in a sibling repo root is not hallucinated when
    that root is supplied (feedback item 7 gap 3 — DRY additional_repo_roots)."""
    main_root = tmp_path / "main"
    sibling = tmp_path / "sibling"
    (main_root / "src").mkdir(parents=True)
    (sibling / "lib").mkdir(parents=True)
    (sibling / "lib/helper.py").write_text("")
    content = "Reuse `lib/helper.py` from the sibling repo."
    # Without the sibling root it is hallucinated.
    penalty_off, hall_off = compute_grounding_penalty(content, main_root)
    assert hall_off == ["lib/helper.py"]
    assert penalty_off == pytest.approx(0.9)
    # With the sibling root supplied it resolves.
    penalty_on, hall_on = compute_grounding_penalty(content, main_root, extra_roots=[sibling])
    assert hall_on == []
    assert penalty_on == 1.0


# --- P1-8: two-colon (file:line:col) reference extraction ---------------------
# The impl-ref extractor previously dropped `file.py:line:col` tokens ENTIRELY
# (the suffix class excluded ':'), so a hallucinated two-colon reference was
# never even a candidate for the grounding penalty — it scored as clean. The
# only prior test (test_line_suffix_on_existing_file_not_penalized) exercised
# only the existing-file direction and therefore passed vacuously whether the
# token was correctly handled or silently never checked.


@pytest.mark.parametrize("suffix", [":42", ":42:5"])
def test_missing_file_with_line_or_col_suffix_is_penalized(tmp_path: Path, suffix: str):
    """Regression (P1-8): a MISSING file cited with `:line` OR `:line:col`
    earns a nonzero penalty. Before the fix, `:line:col` was invisible to the
    extractor and returned penalty=1.0 (unpenalized)."""
    (tmp_path / "src").mkdir()
    content = f"See `src/missing.py{suffix}` for the change."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert hallucinated == ["src/missing.py"]
    assert penalty == pytest.approx(0.9)


@pytest.mark.parametrize("suffix", [":42", ":42:5"])
def test_existing_file_with_line_or_col_suffix_not_penalized(tmp_path: Path, suffix: str):
    """An EXISTING file cited with `:line` OR `:line:col` stays clean — the
    anchor is stripped before the existence probe (no false positive)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src/foo.py").write_text("")
    content = f"See `src/foo.py{suffix}` for the change."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert hallucinated == []
    assert penalty == 1.0


# --- P2-5: Windows drive-letter references through the full pipeline ----------


@pytest.mark.parametrize(
    "ref",
    [
        r"C:\Users\x\f.py:42",
        "C:/Users/x/f.py:42",
    ],
)
def test_windows_drive_letter_reference_is_not_flagged(tmp_path: Path, ref: str):
    """A drive-lettered absolute path is not a repo-relative reference, so it
    passes through the grounding pipeline without producing a false
    hallucination penalty (and the `:42` anchor never mangles the drive
    colon)."""
    (tmp_path / "src").mkdir()
    penalty, hallucinated = compute_grounding_penalty(f"See `{ref}`.", tmp_path)
    assert hallucinated == []
    assert penalty == 1.0


# --- P1-5: production path (extra_roots=None -> config.additional_repo_roots) --


def test_extra_roots_none_resolves_additional_repo_roots_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The PRODUCTION branch: with extra_roots omitted, sibling roots are read
    from the SAME ``additional_repo_roots`` config knob (absolute entry)."""
    main_root = tmp_path / "main"
    sibling = tmp_path / "sibling"
    (main_root / "src").mkdir(parents=True)
    (sibling / "lib").mkdir(parents=True)
    (sibling / "lib/helper.py").write_text("")
    cfg = TRWConfig(trw_dir=str(tmp_path / ".trw"), additional_repo_roots=[str(sibling)])
    monkeypatch.setattr(grounding, "get_config", lambda: cfg)

    content = "Reuse `lib/helper.py` from the sibling repo."
    penalty, hallucinated = compute_grounding_penalty(content, main_root)
    assert hallucinated == []
    assert penalty == 1.0


def test_extra_roots_none_resolves_relative_config_root_against_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A RELATIVE ``additional_repo_roots`` entry is resolved against
    project_root (exercises the ``not is_absolute()`` branch)."""
    main_root = tmp_path / "main"
    sibling = tmp_path / "sibling"
    (main_root / "src").mkdir(parents=True)
    (sibling / "lib").mkdir(parents=True)
    (sibling / "lib/helper.py").write_text("")
    cfg = TRWConfig(trw_dir=str(tmp_path / ".trw"), additional_repo_roots=["../sibling"])
    monkeypatch.setattr(grounding, "get_config", lambda: cfg)

    content = "Reuse `lib/helper.py` from the sibling repo."
    penalty, hallucinated = compute_grounding_penalty(content, main_root)
    assert hallucinated == []
    assert penalty == 1.0


def test_extra_roots_config_error_logs_warning_and_degrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When config resolution raises, the scorer degrades to project-root-only
    (the ref is still hallucinated) AND emits an observable warning instead of
    silently swallowing the exception (P1-5)."""

    def _boom() -> TRWConfig:
        raise RuntimeError("config backend unavailable")

    monkeypatch.setattr(grounding, "get_config", _boom)
    (tmp_path / "src").mkdir()
    content = "Reuse `lib/helper.py` from a sibling repo that is not configured."
    with capture_logs() as logs:
        penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert hallucinated == ["lib/helper.py"]
    assert penalty == pytest.approx(0.9)
    assert any(e.get("event") == "grounding_extra_roots_config_unavailable" for e in logs)


# --- P2-6: greenfield-window boundary cases ----------------------------------
# The greenfield exemption scans _GREENFIELD_WINDOW_CHARS (16) chars AFTER a
# backticked token for a to-be-created marker. Boundary behavior must be exact:
# a marker fully inside the window exempts; one that starts past it does not;
# and a marker belonging to the NEXT token must not exempt the previous one.


def test_greenfield_marker_ending_exactly_at_window_boundary_exempts(tmp_path: Path):
    """A `(new)` marker whose LAST char lands on the final in-window position
    (offset 11..15 for a 16-char window) is fully inside the window and
    exempts the missing reference."""
    (tmp_path / "src").mkdir()
    content = "See `src/a.py`" + " " * 11 + "(new) will be created."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert hallucinated == []
    assert penalty == 1.0


def test_greenfield_marker_starting_past_window_does_not_exempt(tmp_path: Path):
    """A `(new)` marker that begins at/after the window edge (offset 16) is out
    of range, so the missing reference is still penalized."""
    (tmp_path / "src").mkdir()
    content = "See `src/b.py`" + " " * 16 + "(new)."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    assert hallucinated == ["src/b.py"]
    assert penalty == pytest.approx(0.9)


def test_greenfield_marker_of_next_token_does_not_exempt_previous(tmp_path: Path):
    """A `(new)` marker that annotates a LATER backticked token must not leak
    its exemption backwards onto an earlier, unannotated reference."""
    (tmp_path / "src").mkdir()
    content = "Touch `src/c.py` then create `src/d.py` (new)."
    penalty, hallucinated = compute_grounding_penalty(content, tmp_path)
    # src/d.py is greenfield-exempt; src/c.py is NOT (its window has no marker).
    assert hallucinated == ["src/c.py"]
    assert penalty == pytest.approx(0.9)
