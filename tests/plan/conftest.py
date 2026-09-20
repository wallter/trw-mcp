"""A real two-member formation, built through the public facade."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


@dataclass
class Scene:
    root: Path
    manifest: Any
    runs: dict[str, Path]

    @property
    def project_root(self) -> Path:
        return self.root


@pytest.fixture
def scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Scene:
    from tests import _path_isolation
    from trw_mcp import formation
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths import pin_active_run

    root = tmp_path / "project"
    (root / ".trw").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _path_isolation.set_current_root(root)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    home = tmp_path / "home"
    os.makedirs(home, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    trw = root / ".trw"
    runs = {m: trw / "runs" / m for m in ("alpha", "beta")}
    for run in runs.values():
        (run / "meta").mkdir(parents=True)
        (run / "meta" / "run.yaml").write_text("status: active\n", encoding="utf-8")
    manifest = formation.create(
        runs["alpha"],
        {
            "formation_id": "plan-scene",
            "members": [
                {"member_id": "alpha", "client": "claude-code", "owned_paths": ["src/a.py"], "open_join": True},
                {"member_id": "beta", "client": "codex", "owned_paths": ["src/b/**"], "open_join": True},
            ],
        },
        trw_dir=trw,
        prds_dir=root / "prds",
    )
    for member in ("alpha", "beta"):
        monkeypatch.setenv("TRW_SESSION_ID", f"pin-{member}")
        context = build_call_context(None)
        formation.join("plan-scene", member, runs[member], pin_key=context.session_id, trw_dir=trw)
        pin_active_run(runs[member], context=context)
    monkeypatch.setenv("TRW_SESSION_ID", "pin-alpha")
    return Scene(root=root, manifest=manifest, runs=runs)
