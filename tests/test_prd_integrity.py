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
    _check_repo_path_references,
    _extract_repo_path_refs,
    _normalize_repo_path,
    _path_exists,
    _resolve_bare_filename,
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


# --- PRD-QUAL-067: bare-filename bounded-rglob resolver -----------------------


def _mk_tree(root: Path, files: list[str]) -> None:
    """Materialize an in-memory tree of relative paths under ``root``."""
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")


def test_resolve_bare_filename_one_match_returns_true(tmp_path: Path) -> None:
    """FR-01 happy path: exactly one match → resolved=True, count=1."""
    _mk_tree(tmp_path, ["docs/eval/TRACE-SCHEMA.md"])
    resolved, count = _resolve_bare_filename(tmp_path, "TRACE-SCHEMA.md")
    assert resolved is True
    assert count == 1


def test_resolve_bare_filename_multi_match_returns_ambiguous(tmp_path: Path) -> None:
    """FR-01 ambiguous: multiple matches → resolved=False, count>1, short-circuits at 2."""
    _mk_tree(
        tmp_path,
        [
            "trw-eval/src/trw_eval/scoring/analysis/a/prompts.py",
            "trw-eval/src/trw_eval/scoring/analysis/b/prompts.py",
            "trw-eval/src/trw_eval/scoring/analysis/c/prompts.py",
        ],
    )
    resolved, count = _resolve_bare_filename(tmp_path, "prompts.py")
    assert resolved is False
    assert count >= 2  # short-circuits at >1; exact count not guaranteed


def test_resolve_bare_filename_zero_match_returns_unresolved(tmp_path: Path) -> None:
    """FR-01 + NFR-03: zero matches → resolved=False, count=0 (surfaces as warning)."""
    _mk_tree(tmp_path, ["docs/eval/something_else.md"])
    resolved, count = _resolve_bare_filename(tmp_path, "aggregate.json")
    assert resolved is False
    assert count == 0


def test_resolve_bare_filename_respects_exclude_dirs(tmp_path: Path) -> None:
    """NFR-02 + NFR-04: vendor and build trees are skipped by the resolver."""
    _mk_tree(
        tmp_path,
        [
            ".venv/lib/python3.12/site-packages/foo/score.json",
            "node_modules/pkg/score.json",
            ".git/hooks/score.json",
            "__pycache__/score.json",
            "dist/score.json",
        ],
    )
    resolved, count = _resolve_bare_filename(tmp_path, "score.json")
    assert resolved is False
    assert count == 0


def test_resolve_bare_filename_memoizes(tmp_path: Path) -> None:
    """FR-01 perf: cache dict short-circuits repeat lookups for the same token."""
    _mk_tree(tmp_path, ["docs/eval/TRACE-SCHEMA.md"])
    cache: dict[str, tuple[bool, int]] = {}
    r1 = _resolve_bare_filename(tmp_path, "TRACE-SCHEMA.md", cache=cache)
    assert "TRACE-SCHEMA.md" in cache
    # Delete the file: if memoization works, the cached result survives.
    (tmp_path / "docs/eval/TRACE-SCHEMA.md").unlink()
    r2 = _resolve_bare_filename(tmp_path, "TRACE-SCHEMA.md", cache=cache)
    assert r1 == r2 == (True, 1)


def test_bare_filename_unknown_extension_falls_through(tmp_path: Path) -> None:
    """Option C gate: extensions NOT in _KNOWN_SOURCE_SUFFIXES use legacy _path_exists.

    A bare `foo.html` token (not in known-source set) must NOT trigger the
    resolver — it falls through to the legacy repo-root-anchored check, which
    returns False for a file that only exists deep in the tree. The legacy
    path emits severity=error, preserving pre-QUAL-067 behavior for extensions
    this PRD intentionally didn't expand into.
    """
    _mk_tree(tmp_path, ["sub/deep/page.html"])
    content = "See `page.html` for details."
    failures = _check_repo_path_references(content, tmp_path)
    # legacy hard-error (not the new warning path)
    html_failures = [f for f in failures if "page.html" in f.message]
    assert len(html_failures) == 1
    assert html_failures[0].severity == "error"


def test_bare_filename_single_match_emits_no_failure(tmp_path: Path) -> None:
    """FR-01 happy path end-to-end: single-match bare filename produces zero failures."""
    _mk_tree(tmp_path, ["docs/eval/TRACE-SCHEMA.md"])
    content = "Per `TRACE-SCHEMA.md`, the trace has fields X and Y."
    failures = _check_repo_path_references(content, tmp_path)
    assert failures == []


def test_bare_filename_emits_warning_not_error_on_ambiguous(tmp_path: Path) -> None:
    """FR-05: multi-match emits severity=warning (not error); message hints disambiguation."""
    _mk_tree(
        tmp_path,
        [
            "trw-eval/a/prompts.py",
            "trw-eval/b/prompts.py",
        ],
    )
    content = "The helper `prompts.py` is shared."
    failures = _check_repo_path_references(content, tmp_path)
    assert len(failures) == 1
    f = failures[0]
    assert f.severity == "warning"
    assert f.rule == "repo_path_exists"
    assert "multiple matches" in f.message
    assert "disambiguate" in f.message


def test_bare_filename_emits_warning_not_error_on_zero_match(tmp_path: Path) -> None:
    """FR-05 + NFR-03: zero-match still visible — as severity=warning, not dropped."""
    _mk_tree(tmp_path, ["docs/other.md"])
    content = "This cites `aggregate.json` from the sprint run."
    failures = _check_repo_path_references(content, tmp_path)
    assert len(failures) == 1
    f = failures[0]
    assert f.severity == "warning"
    assert f.rule == "repo_path_exists"
    assert "no match" in f.message
    assert "disambiguate" in f.message


# --- FR-02/FR-03/FR-04 regressions (QUAL-066 preservation) --------------------


def test_qual066_ellipsis_still_skipped_under_qual067(tmp_path: Path) -> None:
    """FR-02 regression: `.../foo.py` token still dropped by _normalize_repo_path,
    and never reaches the bare-filename resolver."""
    _mk_tree(tmp_path, ["deep/foo.py"])
    content = "Token `.../foo.py` here."
    failures = _check_repo_path_references(content, tmp_path)
    # Ellipsis token never makes it into _extract_repo_path_refs, so zero failures.
    assert failures == []


def test_qual066_extra_categories_still_honored(_config_with_extras: None) -> None:
    """FR-02 regression: config-extended categories still accepted."""
    for cat in ("EVAL", "DIST", "INTENT"):
        assert _check_allowed_category({"category": cat}) == []


def test_directory_qualified_missing_still_hard_errors(tmp_path: Path) -> None:
    """FR-04 regression: `/`-qualified nonexistent path still severity=error."""
    content = "Reference to `this/does/not/exist.py`."
    failures = _check_repo_path_references(content, tmp_path)
    assert len(failures) == 1
    f = failures[0]
    assert f.severity == "error"
    assert f.rule == "repo_path_exists"
    assert "this/does/not/exist.py" in f.message


def test_directory_qualified_existing_resolves_silently(tmp_path: Path) -> None:
    """FR-04 regression (positive): `/`-qualified existing path → zero failures,
    and the bare-filename resolver is NOT invoked (no double-processing)."""
    _mk_tree(tmp_path, ["pkg/mod.py"])
    content = "See `pkg/mod.py`."
    failures = _check_repo_path_references(content, tmp_path)
    assert failures == []


def test_parent_dir_shorthand_still_rejected_under_qual067() -> None:
    """FR-03 regression: `..` still rejected by _normalize_repo_path."""
    assert _normalize_repo_path("../trw-memory/README.md") is None
    assert _normalize_repo_path("a/../b.py") is None


# --- FR-06: observability ----------------------------------------------------


def _enable_debug_logs() -> None:
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),  # allow debug
    )


def test_bare_filename_resolved_emits_debug_event(tmp_path: Path) -> None:
    """FR-06: single-match → `prd_integrity_bare_filename_resolved` at debug."""
    _enable_debug_logs()
    _mk_tree(tmp_path, ["docs/eval/TRACE-SCHEMA.md"])
    with capture_logs() as logs:
        _check_repo_path_references("See `TRACE-SCHEMA.md`.", tmp_path)
    events = [e for e in logs if e.get("event") == "prd_integrity_bare_filename_resolved"]
    assert len(events) == 1
    assert events[0]["raw"] == "TRACE-SCHEMA.md"
    assert events[0]["match_count"] == 1


def test_bare_filename_ambiguous_emits_debug_event(tmp_path: Path) -> None:
    """FR-06: multi-match → `prd_integrity_bare_filename_ambiguous` at debug."""
    _enable_debug_logs()
    _mk_tree(tmp_path, ["a/prompts.py", "b/prompts.py"])
    with capture_logs() as logs:
        _check_repo_path_references("See `prompts.py`.", tmp_path)
    events = [e for e in logs if e.get("event") == "prd_integrity_bare_filename_ambiguous"]
    assert len(events) == 1
    assert events[0]["raw"] == "prompts.py"
    assert events[0]["match_count"] >= 2


def test_bare_filename_unresolved_emits_debug_event(tmp_path: Path) -> None:
    """FR-06: zero-match → `prd_integrity_bare_filename_unresolved` at debug."""
    _enable_debug_logs()
    with capture_logs() as logs:
        _check_repo_path_references("Mention of `aggregate.json`.", tmp_path)
    events = [e for e in logs if e.get("event") == "prd_integrity_bare_filename_unresolved"]
    assert len(events) == 1
    assert events[0]["raw"] == "aggregate.json"
    assert events[0]["match_count"] == 0


# --- NFR-01: batch re-validation — no `valid=true` → `valid=false` flips ----


def test_batch_revalidation_zero_flips(tmp_path: Path) -> None:
    """NFR-01: bare-filename entries across a sample of real PRDs NEVER emit
    severity=error — they're always severity=warning, which keeps existing
    `valid=true` PRDs passing (no flips to `valid=false`).

    Uses a synthetic repo tree to stay under the per-test 120s timeout (rglob
    on the real 100k-file monorepo is too slow for a sample loop). The
    semantically-equivalent contract is: given any bare-filename citation with
    any of {0, 1, many} matches, the resulting ValidationFailure (if any) has
    severity=warning — not error.
    """
    # Build a synthetic tree that exercises all three cases.
    _mk_tree(
        tmp_path,
        [
            "docs/eval/TRACE-SCHEMA.md",        # unambiguous
            "a/prompts.py",                      # ambiguous
            "b/prompts.py",                      # ambiguous
        ],
    )
    # Exercise the full 25-bare-filename shape from PRD-EVAL-037 plus a known-
    # missing token (aggregate.json). All should emit warning, never error.
    tokens = [
        "TRACE-SCHEMA.md",         # 1 match
        "prompts.py",              # many
        "aggregate.json",          # 0 matches
        "test_parser_examples.py", # 0 matches (should warn, not error)
        "score.json",              # 0 matches
    ]
    content = " ".join(f"`{t}`" for t in tokens)
    failures = _check_repo_path_references(content, tmp_path)
    for f in failures:
        assert f.severity == "warning", (
            f"QUAL-067 NFR-01: bare-filename entry must be warning, "
            f"got {f.severity!r}: {f.message}"
        )
    # At least one warning emitted (prompts.py + the 3 zero-match tokens).
    assert sum(1 for f in failures if f.severity == "warning") >= 4
