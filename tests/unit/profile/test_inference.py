"""FR-6 / FR-7 — domain + task-type inference tests."""

from __future__ import annotations

from trw_mcp.profile import infer_domain, infer_task_type


def test_infer_domain_explicit_flag_wins() -> None:
    """FR-6 (a): an explicit domain beats path inference."""
    assert infer_domain(explicit="memory", prd_path="platform/app.tsx") == "memory"


def test_infer_domain_from_prd_path_prefix() -> None:
    """FR-6 (b): each path prefix maps to its domain."""
    assert infer_domain(prd_path="platform/src/app.tsx") == "frontend"
    assert infer_domain(prd_path="backend/api/routes.py") == "backend"
    assert infer_domain(prd_path="trw-eval/runner.py") == "eval"
    assert infer_domain(prd_path="trw-mcp/src/x.py") == "core"
    assert infer_domain(prd_path="trw-memory/client.py") == "memory"


def test_infer_domain_fallback_unknown() -> None:
    """FR-6 (c): an unrecognized path falls back to unknown."""
    assert infer_domain(prd_path="docs/notes.md") == "unknown"
    assert infer_domain() == "unknown"


def test_infer_task_type_bugfix_keyword() -> None:
    """FR-7: bug/fix keywords map to bugfix."""
    assert infer_task_type(task_name="fix the login bug") == "bugfix"
    assert infer_task_type(prd_category="FIX") == "bugfix"


def test_infer_task_type_refactor_keyword() -> None:
    """FR-7: refactor maps to refactor (and beats a trailing 'fix')."""
    assert infer_task_type(task_name="refactor the resolver") == "refactor"


def test_infer_task_type_feature_keyword() -> None:
    """FR-7: feat/feature maps to feature."""
    assert infer_task_type(task_name="add feature X") == "feature"


def test_infer_task_type_generic_fallback() -> None:
    """FR-7: no keyword falls back to generic."""
    assert infer_task_type(task_name="misc chore") == "generic"
    assert infer_task_type() == "generic"


def test_infer_task_type_explicit_wins() -> None:
    """FR-7: explicit task-type beats keyword inference."""
    assert infer_task_type(explicit="docs", task_name="fix a bug") == "docs"
