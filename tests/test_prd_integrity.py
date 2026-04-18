"""Regression tests for trw_prd_validate integrity checks (PRD-QUAL-066).

Pins two bugs that caused false-negative F grades on valid PRDs:

* Bug 1 — ``_check_allowed_category`` used the BUILTIN-only alias instead of
  the config-extended set (``allowed_prd_categories()``). This rejected every
  EVAL / INTENT / SCALE / THRASH / HPO / SEC / DIST PRD even when those
  categories were registered in ``.trw/config.yaml:extra_prd_categories``.
* Bug 2 — ``_normalize_repo_path`` guarded ``".."`` but not ``"..."``
  (three-dot ellipsis shorthand), leaking tokens like ``.../pre_analyzers.py``
  to ``_path_exists`` which then reported false ``repo_path_exists`` failures.

Test matrix intentionally covers the PRD's §7 acceptance criteria, including
FR-01, FR-02, FR-03 and NFR-01 (no regression), NFR-03 (debug log emission).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from trw_mcp.models.config import TRWConfig, reload_config
from trw_mcp.state.validation.prd_integrity import (
    BUILTIN_PRD_CATEGORIES,
    _check_allowed_category,
    _extract_repo_path_refs,
    _normalize_repo_path,
    _path_exists,
    allowed_prd_categories,
    run_prd_integrity_checks,
)


# --- Fixtures -----------------------------------------------------------------


@pytest.fixture
def _config_with_extras() -> Iterator[None]:
    """Install a TRWConfig whose ``extra_prd_categories`` contains all 7 TRW categories."""
    cfg = TRWConfig(extra_prd_categories=["EVAL", "DIST", "INTENT", "SCALE", "THRASH", "HPO", "SEC"])
    reload_config(cfg)
    yield
    reload_config(None)


@pytest.fixture
def _config_empty_extras() -> Iterator[None]:
    """Install a TRWConfig with no extra categories — BUILTIN-only."""
    cfg = TRWConfig(extra_prd_categories=[])
    reload_config(cfg)
    yield
    reload_config(None)


# --- FR-01: category allowlist ------------------------------------------------


@pytest.mark.parametrize("category", ["EVAL", "DIST", "INTENT", "SCALE", "THRASH", "HPO", "SEC"])
def test_check_allowed_category_accepts_config_extension(
    category: str, _config_with_extras: None
) -> None:
    """All 7 TRW-specific categories pass when registered in config."""
    failures = _check_allowed_category({"category": category})
    assert failures == []


@pytest.mark.parametrize("category", sorted(BUILTIN_PRD_CATEGORIES))
def test_builtin_categories_still_accepted(category: str, _config_with_extras: None) -> None:
    """NFR-01 regression: BUILTIN categories continue to pass alongside extras."""
    failures = _check_allowed_category({"category": category})
    assert failures == []


@pytest.mark.parametrize("category", sorted(BUILTIN_PRD_CATEGORIES))
def test_builtin_categories_accepted_with_no_extras(
    category: str, _config_empty_extras: None
) -> None:
    """Backward compat: empty extra_prd_categories preserves BUILTIN behavior."""
    failures = _check_allowed_category({"category": category})
    assert failures == []


def test_check_allowed_category_rejects_unknown(_config_with_extras: None) -> None:
    """Unknown category (not in BUILTIN, not in extras) is still rejected."""
    failures = _check_allowed_category({"category": "BOGUS"})
    assert len(failures) == 1
    f = failures[0]
    assert f.rule == "aaref_category_allowlist"
    assert f.severity == "error"
    # Message must enumerate the union (both BUILTIN + extras).
    assert "EVAL" in f.message
    assert "CORE" in f.message


def test_check_allowed_category_rejects_unknown_with_no_extras(
    _config_empty_extras: None,
) -> None:
    """Unknown category still rejected when no extras are configured."""
    failures = _check_allowed_category({"category": "EVAL"})  # EVAL not in BUILTIN
    assert len(failures) == 1
    assert failures[0].rule == "aaref_category_allowlist"


def test_check_allowed_category_empty_category_passes(_config_empty_extras: None) -> None:
    """Empty/missing category field does not emit a failure (handled by other checks)."""
    assert _check_allowed_category({"category": ""}) == []
    assert _check_allowed_category({}) == []


def test_allowed_prd_categories_unions_extras(_config_with_extras: None) -> None:
    """Helper returns BUILTIN ∪ extras (upper-cased)."""
    s = allowed_prd_categories()
    assert BUILTIN_PRD_CATEGORIES <= s
    for extra in ("EVAL", "DIST", "INTENT", "SCALE", "THRASH", "HPO", "SEC"):
        assert extra in s


# --- FR-02: ellipsis-path guard -----------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        ".../pre_analyzers.py",      # prefix ellipsis
        "a/.../b.py",                 # middle ellipsis
        "foo/bar/...",                # trailing ellipsis
        ".../nested/.../deep.py",    # multiple ellipses
    ],
)
def test_normalize_repo_path_rejects_ellipsis_prefix(candidate: str) -> None:
    """Ellipsis-shorthand tokens do not resolve to a path (return None)."""
    assert _normalize_repo_path(candidate) is None


def test_normalize_repo_path_accepts_full_path() -> None:
    """A real absolute-within-repo path normalizes through unchanged."""
    got = _normalize_repo_path("trw-mcp/src/trw_mcp/state/validation/prd_integrity.py")
    assert got == "trw-mcp/src/trw_mcp/state/validation/prd_integrity.py"


def test_normalize_repo_path_preserves_nonexistent_bare_path_failure(tmp_path: Path) -> None:
    """FR-02 non-regression: a non-ellipsis missing path still fails _path_exists."""
    # The normalizer returns it (syntactically valid), _path_exists reports False.
    normalized = _normalize_repo_path("this/does/not/exist.py")
    assert normalized == "this/does/not/exist.py"
    assert _path_exists(tmp_path, normalized) is False


def test_normalize_repo_path_still_rejects_parent_dot_dot() -> None:
    """Existing '..' guard MUST remain — the ellipsis fix must not widen it."""
    assert _normalize_repo_path("../evil.py") is None
    assert _normalize_repo_path("a/../b.py") is None


def test_ellipsis_skip_emits_debug_log() -> None:
    """NFR-03: ellipsis skip emits a structlog.debug event with the raw token."""
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),  # allow debug
    )
    with capture_logs() as logs:
        _normalize_repo_path(".../pre_analyzers.py")
    events = [e for e in logs if e.get("event") == "prd_integrity_ellipsis_skip"]
    assert len(events) == 1
    assert events[0]["raw"] == ".../pre_analyzers.py"


# --- FR-03: end-to-end fixture (minimized PRD-EVAL-037 facsimile) -------------


_EVAL_037_FACSIMILE = """---
prd:
  id: PRD-EVAL-037
  title: Mock EVAL PRD
  category: EVAL
  status: draft
  priority: P1
---

# PRD-EVAL-037

Reference to `.../pre_analyzers.py` as shorthand.
Also mentions `a/.../b.py` and `foo/bar/...`.
Full path present once: `trw-mcp/src/trw_mcp/state/validation/prd_integrity.py`.
"""


def test_prd_eval_037_fixture_validates_clean(
    tmp_path: Path, _config_with_extras: None
) -> None:
    """End-to-end: the fixture produces ZERO category or path-exists failures."""
    import yaml

    parts = _EVAL_037_FACSIMILE.split("---", 2)
    frontmatter = yaml.safe_load(parts[1]).get("prd", {})
    body = parts[2]

    # Point project_root at the real monorepo root so the full path resolves.
    repo_root = Path(__file__).resolve().parents[2]
    failures, _warnings = run_prd_integrity_checks(
        body,
        frontmatter,
        project_root=repo_root,
        prds_relative_path="docs/requirements-aare-f/prds",
    )
    categories = [f.rule for f in failures]
    assert "aaref_category_allowlist" not in categories
    assert "repo_path_exists" not in categories


def test_ellipsis_tokens_do_not_produce_path_failures(tmp_path: Path) -> None:
    """FR-02 scoped: `_extract_repo_path_refs` drops ellipsis tokens entirely."""
    content = (
        "See `.../pre_analyzers.py` and `a/.../b.py` and `foo/bar/...`. "
        "Also `trw-mcp/src/trw_mcp/state/validation/prd_integrity.py`."
    )
    refs = _extract_repo_path_refs(content)
    for r in refs:
        assert "..." not in r
