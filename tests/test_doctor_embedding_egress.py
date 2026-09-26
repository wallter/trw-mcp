"""PRD-SEC-014-FR04: ``trw-mcp doctor`` reports embedding cache state + posture.

An operator reasoning about egress had two surfaces for consent flags and none
for embedding traffic, which is governed by the cache and the offline switches
instead. Runtime loads are cache-only (PRD-CORE-302 W40), so the row reports
the cache state and, when the model is missing, the fetch command.
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
    assert "no huggingface.co request" in message


def test_complete_cache_reports_cache_first_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_probe(monkeypatch, "complete")
    row = _row(_doctor_core(tmp_path, _config(tmp_path)), "embedding_egress")
    assert row.status == "PASS"
    assert "cache complete" in row.message
    assert "fetch" not in row.message


@pytest.mark.parametrize("state", ["absent", "incomplete"])
def test_uncached_model_warns_with_the_fetch_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    _stub_probe(monkeypatch, state)
    row = _row(_doctor_core(tmp_path, _config(tmp_path)), "embedding_egress")
    assert row.status == "WARN"
    assert f"cache {state}" in row.message
    assert "runtime never downloads" in row.message
    assert "fix: trw-mcp models fetch" in row.message


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
    assert "cache complete" in human


def test_check_is_registered_in_the_catalogue() -> None:
    """The row is dispatched by the catalogue, not merely defined."""
    assert ("embedding_egress", "_check_embedding_egress") in doctor._CHECKS


def test_row_probes_the_daemons_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The model is the daemon's MEMORY_EMBEDDING_MODEL, the one it actually loads."""
    from trw_memory.embeddings import _hf_cache

    probed: list[str] = []

    def _probe(model_name: str) -> object:
        probed.append(model_name)
        return _hf_cache.CacheProbe(_hf_cache.CacheState.ABSENT)

    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "org/daemon-model")
    monkeypatch.setattr(_hf_cache, "probe_model_cache", _probe)
    row = doctor._check_embedding_egress(tmp_path, _config(tmp_path))
    assert probed == ["org/daemon-model"]
    assert "org/daemon-model" in row.message
