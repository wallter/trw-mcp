"""PRD-CORE-302 W40: ``trw-mcp models fetch`` is the one explicit model download."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest


def _run(*, as_json: bool = True) -> int:
    from trw_mcp.server._subcommands_models import run_models

    with pytest.raises(SystemExit) as exit_info:
        run_models(argparse.Namespace(models_command="fetch", as_json=as_json))
    return int(exit_info.value.code or 0)


def _record_fetches(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    import trw_memory.embeddings as embeddings

    asked: list[str | None] = []

    def _fetch(*, embedding_model: str | None = None, rerank_model: str | None = None) -> dict[str, str]:
        asked.append(embedding_model)
        return {str(embedding_model): "main", "cross-encoder/x": "main"}

    monkeypatch.setattr(embeddings, "fetch_models", _fetch)
    return asked


def test_fetch_downloads_the_daemons_model(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "org/custom-model")
    asked = _record_fetches(monkeypatch)

    assert _run() == 0
    assert asked == ["org/custom-model"]
    assert json.loads(capsys.readouterr().out) == {
        "status": "fetched",
        "models": {"org/custom-model": "main", "cross-encoder/x": "main"},
    }


def test_a_config_yaml_model_key_does_not_change_the_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The retired ``retrieval_embedding_model`` key must not pick a model the daemon never loads."""
    from trw_memory._model_pin import DEFAULT_EMBEDDING_MODEL

    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("retrieval_embedding_model: org/config-model\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEMORY_EMBEDDING_MODEL", raising=False)
    asked = _record_fetches(monkeypatch)

    assert _run() == 0
    assert asked == [DEFAULT_EMBEDDING_MODEL]


def test_a_failed_download_is_reported_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import trw_memory.embeddings as embeddings

    def _fetch(**_: object) -> dict[str, str]:
        raise OSError("hub unreachable")

    monkeypatch.setattr(embeddings, "fetch_models", _fetch)
    assert _run(as_json=False) == 1
    assert "failed: OSError: hub unreachable" in capsys.readouterr().err


def test_the_verb_is_wired_into_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    args = _build_arg_parser().parse_args(["models", "fetch", "--json"])
    assert (args.command, args.models_command, args.as_json) == ("models", "fetch", True)
