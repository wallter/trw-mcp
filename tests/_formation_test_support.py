"""Shared fixtures for the PRD-CORE-265 formation tests.

One builder, used by every FR/NFR test, so the manifest shape a test asserts
against is the shape the facade actually writes — no test constructs a manifest
by hand-writing YAML, which is how a test suite drifts from its production
writer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

__all__ = ["FormationFixture", "formation_env", "make_run_dir", "write_pin"]


@dataclass(frozen=True)
class FormationFixture:
    """An isolated project root holding one orchestrator run and N member runs."""

    project_root: Path
    trw_dir: Path
    orchestrator_run: Path
    member_runs: dict[str, Path]

    def manifest_path(self) -> Path:
        return self.orchestrator_run / "formation.yaml"

    def payload(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "formation_id": "release-train",
            "shared_rules_ref": "docs/rules.md",
            "members": [
                {
                    "member_id": "impl-1",
                    "client": "claude-code",
                    "role": "implementer",
                    "owned_paths": ["src/alpha"],
                    "test_owned_paths": ["tests/test_alpha.py"],
                    "prd_ids": ["PRD-CORE-900"],
                },
                {
                    "member_id": "impl-2",
                    "client": "codex",
                    "role": "implementer",
                    "owned_paths": ["src/beta"],
                    "test_owned_paths": ["tests/test_beta.py"],
                    "prd_ids": ["PRD-CORE-901"],
                },
            ],
        }
        base.update(overrides)
        return base


def make_run_dir(root: Path, name: str, *, status: str = "active", phase: str = "implement") -> Path:
    """Create a minimal run directory with the meta/ files the roll-up reads."""
    run_dir = root / name
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: {name}\ntask: {name}\nstatus: {status}\nphase: {phase}\n", encoding="utf-8"
    )
    (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return run_dir


@pytest.fixture
def formation_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FormationFixture:
    """An isolated project root with the formation path resolvers redirected."""
    project_root = tmp_path / "repo"
    trw_dir = project_root / ".trw"
    runs_root = trw_dir / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    (project_root / "docs").mkdir(parents=True, exist_ok=True)
    (project_root / "docs" / "rules.md").write_text("Shared rules: commit only what you own.\n", encoding="utf-8")

    orchestrator = make_run_dir(runs_root, "orchestrator")
    members = {name: make_run_dir(runs_root, name) for name in ("impl-1", "impl-2")}

    from trw_mcp.state import _paths

    monkeypatch.setattr(_paths, "resolve_project_root", lambda: project_root)
    monkeypatch.setattr(_paths, "resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr(_paths, "pin_store_path", lambda: trw_dir / "runtime" / "pins.json", raising=False)

    from trw_mcp.state import _pin_store

    monkeypatch.setattr(_pin_store, "pin_store_path", lambda: trw_dir / "runtime" / "pins.json")

    return FormationFixture(project_root, trw_dir, orchestrator, members)


def write_pin(fixture: FormationFixture, pin_key: str, run_path: Path, *, age_hours: float = 0.0) -> None:
    """Write one pin-store entry with a heartbeat *age_hours* in the past."""
    pins_path = fixture.trw_dir / "runtime" / "pins.json"
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    store: dict[str, Any] = json.loads(pins_path.read_text()) if pins_path.is_file() else {}
    heartbeat = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    store[pin_key] = {
        "run_path": str(run_path),
        "pid": 999_999,
        "last_heartbeat_ts": heartbeat.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
    }
    pins_path.write_text(json.dumps(store), encoding="utf-8")
