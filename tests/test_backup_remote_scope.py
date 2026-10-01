"""`backup create` never uploads a store that holds more than the invoking project's namespace (MEMORY-LEAK-REMOTE-BACKUP-WHOLE-STORE).

The archive is a VACUUM INTO of the whole served store, so it carries every project's rows and the ``user:*`` tier that
PRD-CORE-280 says never leaves the machine. The remote leg is therefore refused (fail closed) when the store holds any
namespace but the invoking project's, and always when it holds a ``user:*`` row. The local archive is still written.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("governing_project")]

_PROJECT = "project:demo-1a2b3c4d"


def _store(path: Path, rows: dict[str, str]) -> None:
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    backend = SQLiteBackend(path)
    for entry_id, namespace in rows.items():
        backend.store(MemoryEntry(id=entry_id, content=f"row {entry_id} SECRET-CONTENT", namespace=namespace))
    backend.close()


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: dict[str, str]) -> tuple[list[httpx.Request], str]:
    from trw_memory.daemon import DiscoveryAbsent

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _subcommands_backup

    db_path = tmp_path / "memory.db"
    _store(db_path, rows)
    monkeypatch.setattr("trw_memory.cli_client.read_live_discovery", lambda paths: DiscoveryAbsent(reason="none"))
    monkeypatch.setattr("trw_mcp.server._backup_remote_scope.invoking_namespace", lambda: _PROJECT)
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)
    monkeypatch.setattr(
        _subcommands_backup,
        "_load_config",
        lambda: TRWConfig(backend_url="https://api.trwframework.com", platform_api_key="k", backup_remote_enabled=True),
    )
    calls: list[httpx.Request] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/backup/presign":
            return httpx.Response(
                200, json={"url": "https://bucket.s3.amazonaws.com/k?sig=1", "key": "backups/k.db.gz"}
            )
        return httpx.Response(200)

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    _subcommands_backup.run_backup(argparse.Namespace(backup_command="create", namespace="default", db=str(db_path)))
    return calls, ""


def test_a_store_with_a_user_row_is_never_uploaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls, _ = _run(tmp_path, monkeypatch, {"M-p": _PROJECT, "M-u": "user:local"})

    out = capsys.readouterr().out
    assert calls == []  # zero HTTP requests
    assert "Created backup archive:" in out  # the local archive was still written
    assert "Remote upload refused" in out and "user:local (1 row)" in out
    assert "SECRET-CONTENT" not in out  # counts only, never content


def test_a_store_holding_another_project_is_never_uploaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls, _ = _run(tmp_path, monkeypatch, {"M-p": _PROJECT, "M-o": "project:other-99999999"})

    out = capsys.readouterr().out
    assert calls == [] and "Remote upload refused" in out and "project:other-99999999 (1 row)" in out


def test_a_store_with_only_the_invoking_projects_namespace_still_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls, _ = _run(tmp_path, monkeypatch, {"M-1": _PROJECT, "M-2": _PROJECT})

    assert len(calls) == 2  # presign + PUT
    assert "Uploaded to remote backup" in capsys.readouterr().out
