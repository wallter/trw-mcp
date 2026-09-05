"""PRD-SEC-014-FR04: ``trw-mcp doctor`` reports embedding cache state + posture.

An operator reasoning about egress had two surfaces for consent flags and none
for embedding traffic, which is governed by the cache and the offline switches
instead. This pins the ``embedding_egress`` row in both the human and JSON
outputs, for each of the three postures.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.server import _subcommands_doctor as doctor
from trw_mcp.server._subcommands_doctor import CheckResult, _doctor_core, _run_doctor


def _config(target: Path) -> TRWConfig:
    return TRWConfig(target_platforms=["claude-code"], backend_url="")


def _row(results: list[CheckResult], name: str) -> CheckResult:
    for result in results:
        if result.name == name:
            return result
    raise AssertionError(f"no {name!r} row in {[r.name for r in results]}")


@pytest.fixture(autouse=True)
def _no_offline_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("TRW_OFFLINE", "HF_HUB_OFFLINE", "MEMORY_LOCAL_ONLY"):
        monkeypatch.delenv(var, raising=False)


def _stub_probe(monkeypatch: pytest.MonkeyPatch, state_value: str) -> None:
    """Force a cache state without depending on the developer's real HF cache."""
    from trw_memory.embeddings import _hf_cache

    monkeypatch.setattr(
        _hf_cache,
        "probe_model_cache",
        lambda model_name: _hf_cache.CacheProbe(_hf_cache.CacheState(state_value)),
    )


def test_doctor_reports_cache_state_and_posture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FR04: the JSON payload carries an embedding_egress row naming both facts."""
    _stub_probe(monkeypatch, "complete")
    with pytest.raises(SystemExit):
        _run_doctor(argparse.Namespace(target_dir=str(tmp_path), format="json", fix=False))

    payload = json.loads(capsys.readouterr().out)
    rows = {check["name"]: check for check in payload["checks"]}
    assert "embedding_egress" in rows
    message = rows["embedding_egress"]["message"]
    assert "cache complete" in message
    assert "posture cache-first" in message


def test_complete_cache_reports_cache_first_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_probe(monkeypatch, "complete")
    row = _row(_doctor_core(tmp_path, _config(tmp_path)), "embedding_egress")
    assert row.status == "PASS"
    assert "cache complete" in row.message
    assert "posture cache-first" in row.message


def test_absent_cache_without_switch_warns_network_capable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_probe(monkeypatch, "absent")
    row = _row(_doctor_core(tmp_path, _config(tmp_path)), "embedding_egress")
    assert row.status == "WARN"
    assert "cache absent" in row.message
    assert "posture network-capable" in row.message


def test_offline_switch_reports_offline_forced_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_probe(monkeypatch, "incomplete")
    monkeypatch.setenv("TRW_OFFLINE", "1")
    row = _row(_doctor_core(tmp_path, _config(tmp_path)), "embedding_egress")
    assert row.status == "PASS"
    assert "cache incomplete" in row.message
    assert "posture offline-forced" in row.message
    assert "TRW_OFFLINE" in row.message


def test_probe_failure_is_reported_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02: an unanswerable probe warns; the doctor run still completes."""
    from trw_memory.embeddings import _hf_cache

    def _boom(model_name: str) -> object:
        raise RuntimeError("cache layout changed")

    monkeypatch.setattr(_hf_cache, "probe_model_cache", _boom)
    results = _doctor_core(tmp_path, _config(tmp_path))
    row = _row(results, "embedding_egress")
    assert row.status == "WARN"
    assert "cache state unknown" in row.message
    assert len(results) > 1


def test_row_appears_in_human_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR04: the row renders in the human report too, not only in JSON."""
    _stub_probe(monkeypatch, "complete")
    with pytest.raises(SystemExit):
        _run_doctor(argparse.Namespace(target_dir=str(tmp_path), format="human", fix=False))
    human = capsys.readouterr().out
    assert "embedding_egress" in human
    assert "posture cache-first" in human


def test_check_is_registered_in_the_catalogue() -> None:
    """The row is dispatched by the catalogue, not merely defined."""
    assert ("embedding_egress", "_check_embedding_egress") in doctor._CHECKS


def test_embedder_wrapper_degrades_to_keyword_only_on_remote_code_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NFR02: RemoteCodeNotPermittedError reaches trw-mcp as "no embedder", not a crash.

    The wrapper's contract is that a loader failure degrades recall to
    keyword-only rather than erroring; the new fail-closed security refusal must
    travel that same path.
    """
    from trw_memory.exceptions import RemoteCodeNotPermittedError

    from trw_mcp.state import _memory_connection

    class _RefusingProvider:
        def __init__(self, model_name: str, dim: int) -> None:
            pass

        def available(self) -> bool:
            raise RemoteCodeNotPermittedError("embedding_trust_remote_code is False")

    monkeypatch.setattr(
        "trw_memory.embeddings.local.LocalEmbeddingProvider",
        _RefusingProvider,
    )
    _memory_connection.reset_embedder()
    try:
        assert _memory_connection.get_embedder() is None
    finally:
        _memory_connection.reset_embedder()
