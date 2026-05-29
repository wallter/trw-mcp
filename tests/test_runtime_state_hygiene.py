from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_checker() -> object:
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_trw_runtime_state.py"
    spec = importlib.util.spec_from_file_location("check_trw_runtime_state", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trw_runtime_classifier_documents_path_tiers() -> None:
    checker = _load_checker()

    assert checker.classify_trw_path(".trw/frameworks/VERSION.yaml") == "canonical"
    assert checker.classify_trw_path(".trw/config.yaml") == "canonical"
    assert checker.classify_trw_path(".trw/compliance/reviews/2026/05/review.yaml") == "audit"
    assert checker.classify_trw_path(".trw/runtime/pins.json") == "ephemeral"
    assert checker.classify_trw_path(".trw/context/session-events.jsonl") == "ephemeral"
    assert checker.classify_trw_path(".trw/security/rate_limits.yaml") == "ephemeral"
    assert checker.classify_trw_path("trw-mcp/src/trw_mcp/state/_paths.py") == "outside_trw"


def test_precommit_check_rejects_ephemeral_trw_paths(capsys: object) -> None:
    checker = _load_checker()

    status = checker.main([".trw/runtime/pins.json", ".trw/frameworks/VERSION.yaml"])

    assert status == 1
    captured = capsys.readouterr()
    assert ".trw/runtime/pins.json" in captured.err
    assert ".trw/frameworks/VERSION.yaml" not in captured.err


def test_precommit_check_accepts_canonical_and_audit_paths() -> None:
    checker = _load_checker()

    assert checker.main([".trw/frameworks/VERSION.yaml", ".trw/compliance/reviews/2026/05/review.yaml"]) == 0
