"""Framework portability regression guards (PRD-CORE-161; v26.1 refresh 2026-07-09)."""

import copy
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Public-mirror guard: this file asserts a MONOREPO invariant (repo-root
# .trw/frameworks/FRAMEWORK.md parity) absent from the standalone trw-mcp
# PyPI/GitHub mirror. Skip cleanly there; the monorepo CI still enforces it.
if not (_REPO_ROOT / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

_FRAMEWORK_ROOT = _REPO_ROOT / ".trw" / "frameworks" / "FRAMEWORK.md"
_FRAMEWORK_BUNDLED = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "framework.md"


def _compiled():
    from trw_mcp.canons.registry import bundled_manifest_bytes, clear_cache, load_registry

    clear_cache()
    return load_registry(bundled_manifest_bytes()).compiled_canons


# --------------------------------------------------------------------------- #
# PRD-CORE-207 — compact-core self-sufficiency, size, portability, comprehension #
# --------------------------------------------------------------------------- #


def test_compact_cores_are_self_sufficient_and_normatively_complete() -> None:
    """FR03/NFR01: every normative obligation resolves from the core alone."""
    from trw_mcp.canons.registry import (
        all_families,
        compile_canon,
        covered_families,
        missing_core_anchors,
        scenario_failures,
    )

    for compiled in _compiled():
        source = (_REPO_ROOT / compiled.authoring_source).read_text(encoding="utf-8")
        result = compile_canon(compiled.id, source, source_basename="x.md")
        # 100% required-obligation and required-family coverage in the core.
        assert missing_core_anchors(compiled.id, result.core) == ()
        assert covered_families(compiled.id, result.core) == all_families(compiled.id)
        # No normative obligation is reference-only (compile guards; assert here too).
        for span in result.spans:
            if span.cls.value == "normative":
                assert span.dest.value in {"core", "both", "core_stub"}
        # Every critical mandatory decision is answerable from the core with the
        # reference file absent (deterministic self-sufficiency proxy).
        assert scenario_failures(compiled.id, result.core) == ()


def test_compact_canon_byte_budget_and_instruction_targets() -> None:
    """NFR04: each compact core is <=70% of the frozen baseline bytes."""
    from trw_mcp.canons.registry import compile_canon, core_byte_ratio

    report: list[tuple[str, int, int, float]] = []
    for compiled in _compiled():
        source = (_REPO_ROOT / compiled.authoring_source).read_text(encoding="utf-8")
        result = compile_canon(compiled.id, source, source_basename="x.md")
        baseline = len((_REPO_ROOT / compiled.combined).read_bytes())
        ratio = core_byte_ratio(result, baseline)
        report.append((compiled.id, baseline, len(result.core.encode("utf-8")), ratio))
        assert ratio <= compiled.max_core_ratio <= 0.70, f"{compiled.id} core ratio {ratio:.3f}"
        # A compact runtime path is declared for the future instruction-pointer
        # flip (the flip itself is the FR09 promotion release, not shadow mode).
        assert compiled.compact_core.endswith("-core.md")
    assert {r[0] for r in report} == {"framework", "aaref"}


def test_compact_cores_remain_provider_client_and_language_neutral() -> None:
    """NFR05: no provider-model / fixed-context / beta-coordination token in a core."""
    from trw_mcp.canons.registry import compile_canon, scan_forbidden

    for compiled in _compiled():
        source = (_REPO_ROOT / compiled.authoring_source).read_text(encoding="utf-8")
        core = compile_canon(compiled.id, source, source_basename="x.md").core
        assert scan_forbidden(core) == ()
        for retired in ("Opus 4.7", "Opus 4.6", "Agent " + "Teams", "Team" + "Create"):
            assert retired not in core


def test_compact_canon_comprehension_receipt_contract() -> None:
    """FR08 (automated portion): the comprehension-receipt contract is enforced."""
    from trw_mcp.canons.registry import REQUIRED_RECEIPT_FIELDS, validate_comprehension_receipt

    valid = dict.fromkeys(REQUIRED_RECEIPT_FIELDS, "x")
    valid["critical_accuracy_by_arm"] = {"compact": 1.0, "combined": 1.0}
    valid["critical_accuracy_by_stratum"] = {
        "balanced:claude-sonnet:compact": 1.0,
        "balanced:claude-sonnet:combined": 1.0,
        "frontier:codex-default:compact": 1.0,
        "frontier:codex-default:combined": 1.0,
    }
    valid["strata"] = {
        "adapters": ["claude-sonnet", "codex-default"],
        "profiles": {"claude-sonnet": "balanced", "codex-default": "frontier"},
        "repeats": 3,
    }
    valid["raw_outcomes"] = [
        {
            "canon": canon,
            "arm": arm,
            "adapter": adapter,
            "repeat": repeat,
            "correct": True,
        }
        for canon in ("framework", "aaref")
        for arm in ("compact", "combined")
        for adapter in ("claude-sonnet", "codex-default")
        for repeat in range(3)
    ]
    valid["negative_control_outcomes"] = [{"correct": False}]
    valid["negative_control_accuracy"] = 0.0
    valid["parser_failures"] = []
    valid["operational_cost_by_arm"] = {
        arm: {
            "request_count": 12,
            "mean_latency_seconds": 1.0,
            "mean_context_bytes": 1000,
            "token_usage": "not exposed by adapter",
        }
        for arm in ("compact", "combined")
    }
    valid["independent_reviewer"] = "Independent audit PASS: docs/evidence/v26.1-independent-audit.md"
    valid["bootstrap_ci_lower"] = -0.02
    assert validate_comprehension_receipt(valid) == []

    # A critical-accuracy miss in any arm blocks.
    miss = dict(valid)
    miss["critical_accuracy_by_arm"] = {"frontier": 1.0, "balanced": 0.9}
    assert any("balanced" in e for e in validate_comprehension_receipt(miss))

    # A non-inferiority lower bound below the -0.05 floor blocks.
    ni = dict(valid)
    ni["bootstrap_ci_lower"] = -0.08
    assert any("non-inferiority" in e for e in validate_comprehension_receipt(ni))

    shallow = dict(valid)
    shallow["strata"] = {**valid["strata"], "repeats": 2}
    assert any("at least 3" in e for e in validate_comprehension_receipt(shallow))

    pending = dict(valid)
    pending["independent_reviewer"] = "pending independent audit"
    assert any("completed independent audit" in e for e in validate_comprehension_receipt(pending))

    weak_control = dict(valid)
    weak_control["negative_control_accuracy"] = 0.5
    assert any("negative_control_accuracy" in e for e in validate_comprehension_receipt(weak_control))

    invalid_cases = [
        ("critical_accuracy_by_arm", "bad", "must be a mapping"),
        ("critical_accuracy_by_stratum", {}, "critical_accuracy_by_stratum is empty"),
        ("critical_accuracy_by_stratum", "bad", "must be a mapping"),
        ("strata", {"adapters": ["only-one"], "profiles": {"only-one": "balanced"}, "repeats": 3}, "two independent"),
        (
            "strata",
            {
                "adapters": ["claude-sonnet", "codex-default"],
                "profiles": {"claude-sonnet": "balanced", "wrong": "frontier"},
                "repeats": 3,
            },
            "keys must exactly match",
        ),
        ("strata", {"adapters": "bad", "profiles": {}, "repeats": "bad"}, "string list"),
        ("strata", "bad", "strata must be a mapping"),
        ("raw_outcomes", [], "non-empty list"),
        ("raw_outcomes", [None], "entries must be mappings"),
        ("raw_outcomes", [{"canon": "", "arm": "compact", "adapter": "a", "repeat": 0}], "invalid canon"),
        (
            "raw_outcomes",
            [{"canon": "framework", "arm": "compact", "adapter": "claude-sonnet", "repeat": 0}],
            "correct must be boolean",
        ),
        ("negative_control_outcomes", [], "non-empty list"),
        ("negative_control_accuracy", "bad", "must be a number"),
        ("parser_failures", [{"error": "bad-json"}], "must be empty"),
        ("parser_failures", "bad", "must be a list"),
        ("operational_cost_by_arm", {"compact": {}}, "compact and combined"),
        ("operational_cost_by_arm", {"compact": "bad", "combined": "bad"}, "must be a mapping"),
        (
            "operational_cost_by_arm",
            {
                "compact": {"request_count": 0, "mean_latency_seconds": 0, "mean_context_bytes": 0, "token_usage": ""},
                "combined": {"request_count": 0, "mean_latency_seconds": 0, "mean_context_bytes": 0, "token_usage": ""},
            },
            "must be positive",
        ),
        ("operational_cost_by_arm", "bad", "must be a mapping"),
        ("independent_reviewer", 7, "must be a string"),
        ("bootstrap_ci_lower", "bad", "must be a number"),
    ]
    for field, value, expected_error in invalid_cases:
        invalid = copy.deepcopy(valid)
        invalid[field] = value
        assert any(expected_error in error for error in validate_comprehension_receipt(invalid))

    # Missing required fields block (absence never passes).
    assert validate_comprehension_receipt({}) != []


def test_checked_in_v261_comprehension_receipt_passes_machine_gate() -> None:
    """FR08: promotion consumes the real checked-in receipt, not only fixtures."""
    from trw_mcp.canons.registry import validate_comprehension_receipt

    receipt_path = _REPO_ROOT / "docs" / "evidence" / "canon-comprehension-v26.1.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert validate_comprehension_receipt(receipt) == []


def _framework_content() -> str:
    if not _FRAMEWORK_ROOT.exists():
        pytest.skip("FRAMEWORK.md not found at expected path")
    return _FRAMEWORK_ROOT.read_text(encoding="utf-8")


class TestFrameworkPortability:
    """PRD-CORE-161: canonical framework is portable and evidence-led."""

    def test_header_declares_model_agnostic_policy(self) -> None:
        content = _framework_content()
        assert "v26.1_TRW" in content
        assert "MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK" in content
        assert "Model policy: capability-based" in content

    def test_framework_removes_v24_provider_and_beta_claims(self) -> None:
        content = _framework_content()
        forbidden = ("Opus 4.7", "Opus 4.6", "Agent " + "Teams", "Team" + "Create", "Send" + "Message")
        for token in forbidden:
            assert token not in content, f"FRAMEWORK.md still contains retired v24/beta token: {token}"

    def test_framework_md_bundled_copy_matches_root(self) -> None:
        if not _FRAMEWORK_BUNDLED.exists():
            pytest.skip("bundled framework.md not found")
        assert _FRAMEWORK_ROOT.read_bytes() == _FRAMEWORK_BUNDLED.read_bytes(), (
            "bundled framework.md has drifted from .trw/frameworks/FRAMEWORK.md"
        )

    def test_eval_transfer_discipline_is_present(self) -> None:
        content = _framework_content()
        assert "eval and transfer discipline" in content.lower()
        assert "stratified" in content.lower()
        assert "harness" in content.lower()
        assert "uncertainty" in content.lower()

    def test_language_agnostic_validation_policy_is_present(self) -> None:
        content = _framework_content()
        assert "LANGUAGE-AGNOSTIC VALIDATION" in content
        assert "project-native" in content
        assert "do not invent universal percentages or single-language gates" in content

    def test_nudge_policy_is_present(self) -> None:
        content = _framework_content()
        assert "NUDGES AND ADAPTIVE GUIDANCE" in content
        assert "Nudges MUST be client-, model-, and language-neutral" in content
        assert "workflow" in content and "learnings" in content and "ceremony" in content and "context" in content

    def test_shared_worktree_commit_policy(self) -> None:
        content = _framework_content()
        assert "Commit each coherent, focused, green milestone promptly" in content
        assert "not a commit-count target" in content
        assert "never requires broken, cosmetic, or invented commits" in content

    def test_shared_worktree_git_decision_matrix(self) -> None:
        content = _framework_content()
        assert "Commit each coherent, focused, green milestone promptly" in content
        assert "not a commit-count target" in content
        assert "commits the complete current version" in content
        assert "never use a plain commit" in content
        assert "command-specific operator authorization and exclusive ownership" in content
        for forbidden in ("`git add -A`", "`git add .`", "`git add -u`", "`git commit -a`"):
            assert forbidden in content
        for hazardous in ("`reset`", "`clean`", "`stash`", "`rebase`", "`commit --amend`"):
            assert hazardous in content

    def test_version_control_policy_is_adapter_scoped(self) -> None:
        """PRD-CORE-206-NFR03: VCS-neutral policy, Git as adapter, no auto-mutation."""
        content = _framework_content()
        # The normative invariants apply to any VCS; Git commands are adapter examples.
        assert "VERSION CONTROL (GIT ADAPTER)" in content
        assert "Use the project's native version-control workflow" in content
        assert "with the active VCS" in content
        # Production code contains NO automatic commit/reset/clean/history rewrite.
        import re

        src_root = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp"
        mutating = re.compile(
            r"""["']git["']\s*,\s*["']"""
            r"(commit|reset|clean|checkout|switch|restore|rebase|merge|cherry-pick|revert|stash|push)"
            r"""["']"""
        )
        offenders = [
            str(path.relative_to(_REPO_ROOT))
            for path in src_root.rglob("*.py")
            if mutating.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], f"production code invokes a mutating git command: {offenders}"


class TestFrameworkMdEnforcement:
    """Core RFC 2119 and process guardrails remain present."""

    def test_rigid_review_must(self) -> None:
        content = _framework_content()
        assert "trw_review()" in content and "before DELIVER" in content

    def test_reversion_must_revert(self) -> None:
        content = _framework_content()
        assert "SHOULD revert" in content or "MUST revert" in content

    def test_phase_must_not_advance(self) -> None:
        content = _framework_content()
        assert "MUST NOT advance" in content

    def test_watchlist_review_entry(self) -> None:
        content = _framework_content()
        assert "RATIONALIZATION WATCHLIST" in content
        content_lower = content.lower()
        assert "too simple" in content_lower or "skip" in content_lower

    def test_phase_reversion_quality_signal(self) -> None:
        content = _framework_content()
        assert "PHASE REVERSION" in content
        assert "revert" in content.lower() and ("structural" in content.lower() or "redesign" in content.lower())
