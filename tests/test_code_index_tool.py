from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trw_mcp.tools.code_index import trw_code_index_update


def test_trw_code_index_update_returns_stats_and_manifest_path_without_file_content(tmp_path: Path) -> None:
    secret_body = "do not leak this body\n"
    (tmp_path / "app.py").write_text(secret_body, encoding="utf-8")

    result = trw_code_index_update(repo_root=str(tmp_path), force=False, paths=None)

    assert result["status"] == "ok"
    assert result["manifest_path"].endswith(".trw/code-index/manifest.json")
    assert result["stats"]["added"] == 1
    assert "files" not in result
    assert secret_body not in str(result)


def test_trw_code_index_update_supports_force_and_path_limits(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    (tmp_path / "docs.md").write_text("docs\n", encoding="utf-8")
    first = trw_code_index_update(repo_root=str(tmp_path), force=False, paths=None)

    (tmp_path / "a.py").write_text("changed\n", encoding="utf-8")
    limited = trw_code_index_update(repo_root=str(tmp_path), force=False, paths=["a.py"])
    forced = trw_code_index_update(repo_root=str(tmp_path), force=True, paths=["a.py"])

    assert first["stats"]["added"] == 2
    assert limited["stats"]["modified"] == 1
    assert limited["stats"]["deleted"] == 0
    assert forced["stats"]["added"] == 1
    assert forced["stats"]["unchanged"] == 0


def test_trw_code_index_update_uses_configured_discovery_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "large.py").write_text("too large for configured cap\n", encoding="utf-8")
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: SimpleNamespace(
            code_index_max_file_bytes=1,
            code_index_exclude_dirs=[],
            code_index_include_extensions=[".py"],
        ),
    )

    result = trw_code_index_update(repo_root=str(tmp_path), force=False, paths=None)

    assert result["status"] == "ok"
    assert result["stats"]["added"] == 0
    assert result["stats"]["skipped"] == 1


def test_trw_code_index_update_returns_structured_failure_for_filesystem_errors(tmp_path: Path) -> None:
    repo_root = tmp_path / "not-a-directory"
    repo_root.write_text("file where repo dir should be\n", encoding="utf-8")

    result = trw_code_index_update(repo_root=str(repo_root), force=False, paths=None)

    assert result["status"] == "failed"
    assert result["error"]
