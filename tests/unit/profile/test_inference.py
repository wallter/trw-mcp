"""FR-6 / FR-7 — domain + task-type inference tests."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._defaults import DEFAULT_DOMAIN_PATH_MAP
from trw_mcp.profile import infer_domain, infer_task_type
from trw_mcp.profile.session_resolve import resolve_session_profile


def test_infer_domain_explicit_flag_wins() -> None:
    """FR-6 (a): an explicit domain beats path inference."""
    assert infer_domain(explicit="memory", prd_path="web/app.tsx") == "memory"


def test_infer_domain_from_path_prefix_uses_generic_defaults() -> None:
    """FR-6 (b): the shipped table names LAYOUT CONVENTIONS, not one repo's tree.

    Replaces the previous assertions, which pinned TRW's own monorepo
    directory names (the frontend, backend, and eval packages) as the shipped
    defaults of a public package.
    """
    assert infer_domain(prd_path="frontend/src/app.tsx") == "frontend"
    assert infer_domain(prd_path="web/src/app.tsx") == "frontend"
    assert infer_domain(prd_path="ui/button.tsx") == "frontend"
    assert infer_domain(prd_path="api/routes.py") == "backend"
    assert infer_domain(prd_path="server/main.py") == "backend"
    assert infer_domain(prd_path="eval/runner.py") == "eval"
    assert infer_domain(prd_path="evals/runner.py") == "eval"


def test_infer_domain_defaults_carry_no_project_specific_directory() -> None:
    """FR-6: no shipped default may name a private package directory."""
    # fmt: off
    assert not {"platform/", "backend/", "trw-eval/", "trw-mcp/", "trw-memory/"} & set(DEFAULT_DOMAIN_PATH_MAP)  # trw-leak-allow: proprietary_path negative assertion: proves these are absent from the shipped defaults
    # fmt: on


def test_infer_domain_uses_config_supplied_map() -> None:
    """FR-6: a project states its own layout; the config table replaces defaults."""
    project_map = {"mobile/": "frontend", "batch/": "eval"}
    assert infer_domain(prd_path="mobile/src/app.tsx", path_domain_map=project_map) == "frontend"
    assert infer_domain(prd_path="batch/runner.py", path_domain_map=project_map) == "eval"
    # The generic defaults are REPLACED, not merged.
    assert infer_domain(prd_path="api/routes.py", path_domain_map=project_map) == "unknown"


def test_infer_domain_longest_prefix_wins_regardless_of_order() -> None:
    """FR-6: a config mapping resolves independently of YAML key order."""
    coarse_first = {"src/": "core", "src/payments/": "payments"}
    fine_first = {"src/payments/": "payments", "src/": "core"}
    assert infer_domain(prd_path="src/payments/x.py", path_domain_map=coarse_first) == "payments"
    assert infer_domain(prd_path="src/payments/x.py", path_domain_map=fine_first) == "payments"


def test_infer_domain_accepts_prefix_without_trailing_slash() -> None:
    """FR-6: an operator-written key need not carry the trailing slash."""
    assert infer_domain(prd_path="api/routes.py", path_domain_map={"api": "backend"}) == "backend"


def test_resolve_session_profile_reads_the_config_domain_map(tmp_path: Path) -> None:
    """FR-6 wiring: the live config table selects the on-disk domain layer.

    Real path — no stub of ``infer_domain``. ``acme-svc/`` is not in any shipped
    default, so the ``domain-payments`` layer loads only if the config-supplied
    mapping reached the inference call.
    """
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "domain-payments.yaml").write_text("ceremony_tier: MINIMAL\n", encoding="utf-8")

    # model_validate is the shape the config loader itself uses for .trw/config.yaml.
    config = TRWConfig.model_validate({"profile_domain_path_map": {"acme-svc/": "payments"}})
    resolved = resolve_session_profile(config, prd_path="acme-svc/handler.py", trw_dir=tmp_path)
    assert "domain" in resolved.layers_applied

    unmapped = resolve_session_profile(config, prd_path="other/handler.py", trw_dir=tmp_path)
    assert "domain" not in unmapped.layers_applied


def test_infer_domain_fallback_unknown() -> None:
    """FR-6 (c): an unrecognized path falls back to unknown."""
    assert infer_domain(prd_path="notes/readme.md") == "unknown"
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
