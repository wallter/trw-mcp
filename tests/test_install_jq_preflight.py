"""PRD-INFRA-192-FR11: the installer's jq preflight warns without blocking and tells the truth.

The hooks read JSON with jq, or python3 when jq is absent (lib-trw.sh ``_json_get``),
so the warning fires only when neither is on PATH. Without a parser, ``append_event`` still records tool/file/session_id: PRD-FIX-149 FR06 removed
its jq dependency. The warning must not say those fields are dropped. What does degrade:
hook-input JSON reads, which ``pre-compact.sh`` reports as ``jq_unavailable=1``, and the
Stop hook's pins lookup, which can fall back to newest-wins attribution, and the
PostToolUse hook, which records ``change_evidence_unknown`` so the deliver gate treats
change evidence as uncomputable (tests/hooks/test_no_shell_json_parser.py runs that hook).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

_PACKAGE = Path(__file__).resolve().parent.parent
_TEMPLATE = _PACKAGE / "scripts" / "install-trw.template.py"
_HOOKS = _PACKAGE / "src" / "trw_mcp" / "data" / "hooks"
_UV_ENV = dict(os.environ)  # read at import, before the autouse HOME isolation hides uv's offline cache


def _load(path: Path = _TEMPLATE) -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_jq_preflight", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingUI:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def step_warn(self, msg: str) -> None:
        self.warnings.append(msg)


def test_jq_present_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _load()
    monkeypatch.setattr(installer.shutil, "which", lambda name: f"/usr/bin/{name}")
    ui = _RecordingUI()
    installer.jq_preflight(ui)
    assert ui.warnings == []


def test_python3_alone_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hooks fall back to python3's json module, so jq alone missing degrades nothing."""
    installer = _load()
    monkeypatch.setattr(installer.shutil, "which", lambda name: None if name == "jq" else f"/usr/bin/{name}")
    ui = _RecordingUI()
    installer.jq_preflight(ui)
    assert ui.warnings == []


def test_jq_absent_warns_without_the_fixed_field_drop_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _load()
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    ui = _RecordingUI()
    installer.jq_preflight(ui)  # returns: a warning never blocks the install
    assert len(ui.warnings) == 1
    warning = ui.warnings[0]
    assert "dropped" not in warning and "session_id" not in warning
    assert "jq_unavailable=1" in warning and "newest-wins" in warning
    assert "change_evidence_unknown" in warning and "deliver gate" in warning


def test_every_hook_marker_the_warning_names_is_what_the_hook_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _load()
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    ui = _RecordingUI()
    installer.jq_preflight(ui)
    warning = ui.warnings[0]
    for hook, marker in (("pre-compact.sh", "jq_unavailable=1"), ("post-tool-event.sh", "change_evidence_unknown")):
        assert marker in warning
        assert marker in (_HOOKS / hook).read_text(encoding="utf-8"), f"{hook} no longer writes {marker}"


class _ReachedExtraction(Exception):
    pass


def test_the_generated_installer_continues_past_a_missing_jq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The built artifact, not the template: a missing jq warns and the install proceeds."""
    spec = importlib.util.spec_from_file_location("build_installer_jq", _PACKAGE / "scripts" / "build_installer.py")
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "trw_mcp-9.9.9-py3-none-any.whl").write_bytes(b"fake-mcp")
    (dist / "trw_memory-9.9.9-py3-none-any.whl").write_bytes(b"fake-memory")
    monkeypatch.setattr(builder, "DIST_DIR", dist)
    installer = _load(builder.build_installer())

    real_which = installer.shutil.which
    monkeypatch.setattr(
        installer.shutil, "which", lambda name, *a, **k: None if name in {"jq", "python3"} else real_which(name)
    )
    monkeypatch.setattr(installer, "check_python_version", lambda _ui: sys.executable)
    warnings: list[str] = []
    monkeypatch.setattr(installer.UI, "step_warn", lambda _self, msg: warnings.append(msg))

    def _stop(*_args: object, **_kwargs: object) -> None:
        raise _ReachedExtraction

    monkeypatch.setattr(installer, "phase_extract_wheels", _stop)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["install-trw.py", "--script", "--no-ai", "--no-telemetry", "--skip-auth"])

    with pytest.raises(_ReachedExtraction):
        installer.main()
    assert any("change_evidence_unknown" in w for w in warnings), warnings


def test_the_hook_the_generated_artifact_installs_records_unknown_change_evidence_without_jq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real trw-mcp wheel goes into the built installer; the hook it deploys runs without jq."""
    import json
    import shutil
    import subprocess
    import zipfile

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv builds the real trw-mcp wheel the artifact embeds")
    dist = tmp_path / "dist"
    subprocess.run([uv, "build", "--wheel", "--offline", "-q", "-o", str(dist), str(_PACKAGE)], env=_UV_ENV, check=True)
    (dist / "trw_memory-9.9.9-py3-none-any.whl").write_bytes(b"fake-memory")
    spec = importlib.util.spec_from_file_location("build_installer_hook", _PACKAGE / "scripts" / "build_installer.py")
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.DIST_DIR = dist
    artifact = builder.build_installer()
    monkeypatch.setattr(sys, "argv", [str(artifact)])  # the installer re-reads its own file for the wheels
    _memory_whl, mcp_whl = _load(artifact).extract_wheels(tmp_path)
    site = tmp_path / "site"
    zipfile.ZipFile(mcp_whl).extractall(site)

    root = tmp_path / "project"
    (root / ".git").mkdir(parents=True)
    deploy = (
        "import sys, trw_mcp; from pathlib import Path; from trw_mcp.bootstrap import init_project;"
        f"assert Path(trw_mcp.__file__).is_relative_to({str(site)!r}), trw_mcp.__file__;"
        f"init_project(Path({str(root)!r}), ide='claude-code')"
    )
    memory_src = str(_PACKAGE.parent / "trw-memory" / "src")
    subprocess.run([sys.executable, "-c", deploy], env={"PYTHONPATH": f"{site}:{memory_src}"}, check=True)
    hook = root / ".claude" / "hooks" / "post-tool-event.sh"
    bin_dir = tmp_path / "bin-nojq"
    bin_dir.mkdir()
    for tool in ("sh", "cat", "date", "dirname", "pwd", "printf", "mkdir", "tr", "wc", "cut", "sed", "grep", "head"):
        if (resolved := shutil.which(tool)) is not None:
            (bin_dir / tool).symlink_to(resolved)
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(root / "a.py")}, "session_id": "h"})
    completed = subprocess.run(
        ["sh", str(hook)],
        input=payload,
        capture_output=True,
        text=True,
        env={"PATH": str(bin_dir), "CLAUDE_PROJECT_DIR": str(root), "TRW_SESSION_ID": "sess-1"},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    events = (root / ".trw" / "context" / "session-events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event"] for line in events][-1] == "change_evidence_unknown"
