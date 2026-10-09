"""External cross-model findings must be scored, never silently replaced."""

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader


@pytest.mark.parametrize(
    "findings",
    [
        [],
        [
            {"category": "correctness", "severity": "medium", "description": "Wrong result"},
            {"category": "security", "severity": "critical", "description": "Low confidence", "confidence": 0.1},
            {"severity": "bogus"},
        ],
    ],
)
def test_external_findings_use_auto_scoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, findings: list[dict[str, object]]
) -> None:
    tools = make_ceremony_server(monkeypatch, tmp_path)
    _reset_config(TRWConfig(cross_model_review_enabled=False, evidence_receipt_mode="observe"))
    run = tmp_path / ".trw/runs/test/run"
    (run / "meta").mkdir(parents=True)
    (run / "meta/run.yaml").write_text("run_id: run\nowner_session_id: session\n")
    with (
        patch("trw_mcp.tools.review.find_active_run", return_value=run),
        patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff"),
        patch("trw_mcp.tools._review_helpers._invoke_cross_model_review") as provider,
    ):
        auto = tools["trw_review"].fn(mode="auto", reviewer_findings=findings)
        result = tools["trw_review"].fn(
            mode="cross_model",
            reviewer_findings=findings,
            reviewer_identity={"reviewer_source": "cross_model", "reviewer_receipt_id": "unverified-token"},
        )
    assert "cross_model_skipped" not in result
    for key in (
        "verdict",
        "total_findings_count",
        "surfaced_findings_count",
        "rejected_findings",
        "suppressed_findings",
    ):
        assert result.get(key) == auto.get(key), key
    provider.assert_not_called()
    data = FileStateReader().read_yaml(run / "meta/review.yaml")
    assert data["verdict"] == auto["verdict"]
    assert "cross_model_skipped" not in data
    assert "cross_model_skipped" not in (run / "meta/events.jsonl").read_text()
    assert data.get("review_family_coverage") != "cross_family"


@pytest.mark.parametrize("findings", [[], [{"category": "correctness", "severity": "medium", "description": "Wrong"}]])
def test_unreceipted_external_findings_error_without_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, findings: list[dict[str, object]]
) -> None:
    tools = make_ceremony_server(monkeypatch, tmp_path)
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    with (
        patch("trw_mcp.tools.review.find_active_run", return_value=run),
        patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
    ):
        result = tools["trw_review"].fn(mode="cross_model", reviewer_findings=findings)
    assert "error" in result
    assert "auto" in str(result["error"])
    assert "verdict" not in result
    assert not (run / "meta/review.yaml").exists()


def test_disabled_provider_without_findings_has_no_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = make_ceremony_server(monkeypatch, tmp_path)
    _reset_config(TRWConfig(cross_model_review_enabled=False))
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    with (
        patch("trw_mcp.tools.review.find_active_run", return_value=run),
        patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
    ):
        result = tools["trw_review"].fn(mode="cross_model")
    assert "error" in result
    assert "verdict" not in result
    assert not (run / "meta/review.yaml").exists()


@pytest.mark.parametrize("valid", [False, True])
def test_external_cross_model_family_requires_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    import hashlib

    from trw_mcp.tools._review_reviewer_family import resolve_reviewer_fields

    tools = make_ceremony_server(monkeypatch, tmp_path)
    _reset_config(TRWConfig(cross_model_review_enabled=False, evidence_receipt_mode="observe"))
    run = tmp_path / ".trw/runs/task/run"
    (run / "meta").mkdir(parents=True)
    receipt = tmp_path / "external-review.txt"
    receipt.write_text("External audit: warning in the error path")
    digest = hashlib.sha256(receipt.read_bytes()).hexdigest() if valid else "invalid"
    with (
        patch("trw_mcp.tools.review.find_active_run", return_value=run),
        patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff"),
    ):
        result = tools["trw_review"].fn(
            mode="cross_model",
            reviewer_findings=[{"category": "correctness", "severity": "medium", "description": "Wrong result"}],
            reviewer_identity={"reviewer_source": "cross_model", "reviewer_receipt_id": digest},
            options={"external_receipt_path": str(receipt)},
        )
    data = FileStateReader().read_yaml(run / "meta/review.yaml")
    fields = resolve_reviewer_fields(data, str(data["mode"]), tmp_path)
    assert result["verdict"] == "warn"
    assert "cross_model_skipped" not in result
    assert "cross_model_skipped" not in data
    assert "cross_model_skipped" not in (run / "meta/events.jsonl").read_text()
    assert fields.verified_cross_model is valid
    if not valid:
        assert fields.family != "cross_model"
        assert fields.family_downgraded_reason == "external_receipt_digest_mismatch"


@pytest.mark.parametrize("mode", [None, "cross_model", "auto", "manual", "reconcile"])
def test_conflicting_findings_cannot_drop_external_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str | None
) -> None:
    tools = make_ceremony_server(monkeypatch, tmp_path)
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    result = tools["trw_review"].fn(
        mode=mode,
        options={"run_path": str(run)},
        findings=[],
        reviewer_findings=[{"category": "correctness", "severity": "critical", "description": "Wrong result"}],
    )
    assert "error" in result
    assert "verdict" not in result
    assert 'mode="cross_model"' in result["error"]
    assert 'mode="auto"' in result["error"]
    assert list((run / "meta").iterdir()) == []


@pytest.mark.parametrize("valid", [False, True])
def test_external_cross_model_typed_receipt_preserves_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    from tests.test_review_receipts import _external_artifact, _manual_project
    from trw_mcp.tools._review_receipt_writer import load_latest_review_evidence

    tools = make_ceremony_server(monkeypatch, tmp_path)
    _reset_config(TRWConfig(cross_model_review_enabled=False))
    run = _manual_project(tmp_path, monkeypatch)
    artifact, digest = _external_artifact(tmp_path)
    with (
        patch("trw_mcp.tools.review.find_active_run", return_value=run),
        patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff"),
        patch("trw_mcp.tools._review_helpers._invoke_cross_model_review") as provider,
    ):
        result = tools["trw_review"].fn(
            mode="cross_model",
            reviewer_findings=[
                {
                    "category": "security",
                    "severity": "critical",
                    "description": "Unbounded read",
                    "reviewer_role": "security",
                }
            ],
            reviewer_identity={"reviewer_source": "cross_model", "reviewer_receipt_id": digest if valid else "invalid"},
            options={"external_receipt_path": str(artifact), "prd_ids": ["PRD-CORE-255"]},
        )
    assert result["typed_receipt_state"] == "written"
    _, receipt = load_latest_review_evidence(run, tmp_path)
    assert receipt is not None
    assert receipt.verdict.value == "block"
    assert receipt.findings[0].description == "Unbounded read"
    assert (receipt.reviewer_family == "cross_model") is valid
    assert receipt.external_receipt_digest == (digest if valid else "")
    assert "cross_model_skipped" not in result
    provider.assert_not_called()
