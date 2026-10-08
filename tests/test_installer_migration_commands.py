"""Migration recovery guidance must survive shell parsing."""

import shlex
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from tests.test_installer_consent_regressions import installer  # noqa: F401

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_migration_commands_quote_paths(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int
) -> None:
    project = tmp_path / "a b; $(echo injected)"
    db = project / ".trw/memory/memory.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"x")
    manifest = str(project / "migration's manifest.json")
    monkeypatch.setattr(installer, "_run_python_output", lambda *_a, **_kw: (1 if code == 3 else 0, ""))
    monkeypatch.setattr(
        installer,
        "_run_migrate_command",
        lambda *_a, **_kw: (code, installer._MIGRATED_PREFIX + manifest, ""),
    )
    ui = Mock()
    installer.phase_migrate_store(ui, "python", project, migrate=True, interactive=False)
    lines = [str(c.args[0]) for c in ui.method_calls if c.args]
    command = next(line[line.index("trw-mcp memory migrate") :] for line in lines if "trw-mcp memory migrate" in line)
    expected = ["trw-mcp", "memory", "migrate", "--to", "user"]
    expected += ["--rollback", manifest] if code == 0 else ["--apply"]
    assert shlex.split(command) == [*expected, "--target-dir", str(project)]


def test_success_without_manifest_never_prints_a_broken_undo(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / ".trw/memory/memory.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"x")
    monkeypatch.setattr(installer, "_run_python_output", lambda *_a, **_kw: (0, ""))
    monkeypatch.setattr(installer, "_run_migrate_command", lambda *_a, **_kw: (0, "", ""))
    ui = Mock()
    assert installer.phase_migrate_store(ui, "python", tmp_path, migrate=True, interactive=False)
    assert not any("--rollback" in str(call) for call in ui.method_calls)
    assert any("manifest" in str(call).lower() for call in ui.step_warn.call_args_list)
