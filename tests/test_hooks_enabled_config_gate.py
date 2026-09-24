"""``hooks_enabled: false``, as ``TRWConfig`` resolves it, silences every bundled hook.

Before 2026-09-23 no hook read the config key: ``lib-trw.sh`` honoured only the
client-profile ``HOOKS_ENABLED`` in ``hook-env.sh`` (or ``TRW_HOOKS_ENABLED``),
and only four hooks checked even that. trw-eval ablates by writing the key into
the project config, so every ``hooks_enabled: false`` arm ran with hooks on.
The one read point is now ``lib-trw.sh``, which every hook sources; it reads the
value ``TRWConfig`` publishes to ``.trw/runtime/hook-flags``. The profile flag
only seeds the key at install.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from trw_mcp.bootstrap._file_ops import _write_hook_env_file
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.state._hook_flags import hook_flags_path, write_hook_flags

pytestmark = pytest.mark.unit

HOOKS_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks"
HOOKS = sorted(path for path in HOOKS_DIR.glob("*.sh") if not path.name.startswith("lib-"))
PAYLOAD = (
    b'{"source":"startup","session_id":"s","hook_event_name":"x","prompt":"database pool",'
    b'"tool_name":"mcp__trw__trw_deliver","tool_input":{"file_path":"x.py"},"tool_response":{}}'
)


@pytest.fixture(autouse=True)
def _no_machine_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "machine-home"))
    for key in ("TRW_HOOKS_ENABLED", "TRW_LEARNING_RECALL_ENABLED"):
        monkeypatch.delenv(key, raising=False)


def _project(root: Path, config: str) -> Path:
    """A project with one learning, the config written and published the way the server does."""
    (root / ".trw" / "learnings" / "entries").mkdir(parents=True)
    (root / ".trw" / "learnings" / "entries" / "L-1.yaml").write_text(
        "id: L-1\nsummary: Database pool exhausts under load\nimpact: 0.9\nstatus: active\n", encoding="utf-8"
    )
    (root / ".trw" / "config.yaml").write_text(config, encoding="utf-8")
    write_hook_flags(root / ".trw")
    return root


def _tree(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _run(hook: Path, root: Path) -> subprocess.CompletedProcess[bytes]:
    env = {key: value for key, value in os.environ.items() if "HOOKS_ENABLED" not in key}
    env |= {"CLAUDE_PROJECT_DIR": str(root), "HOME": str(root.parent / "home")}
    return subprocess.run(
        ["/bin/sh", str(hook)], input=PAYLOAD, env=env, cwd=str(root), capture_output=True, timeout=20, check=False
    )


def test_every_bundled_hook_is_covered() -> None:
    """Every non-library hook the glob finds gets parametrized below (PRD-INFRA-174:
    a fixed cardinality here would be a census literal on an externally-rooted
    population -- the coverage guarantee already comes from parametrizing
    ``test_hooks_enabled_false_means_no_output_and_no_side_effects`` over ``HOOKS``
    itself, so this is a vacuity floor, not a count claim)."""
    assert len(HOOKS) > 0, [path.name for path in HOOKS]


@pytest.mark.parametrize("hook", HOOKS, ids=lambda path: path.name)
def test_hooks_enabled_false_means_no_output_and_no_side_effects(hook: Path, tmp_path: Path) -> None:
    root = _project(tmp_path / "project", "hooks_enabled: false\n")
    before = _tree(root)

    proc = _run(hook, root)

    assert (proc.returncode, proc.stdout, proc.stderr) == (0, b"", b"")
    assert _tree(root) == before
    assert not (tmp_path / "home").exists()


def test_the_resolved_switch_outranks_the_profile_policy_file(tmp_path: Path) -> None:
    root = _project(tmp_path / "project", "hooks_enabled: false\n")
    (root / ".trw" / "runtime" / "hook-env.sh").write_text("export HOOKS_ENABLED=true\n", encoding="utf-8")

    assert _run(HOOKS_DIR / "session-start.sh", root).stdout == b""


def test_with_the_key_on_a_hook_still_speaks(tmp_path: Path) -> None:
    """Guards the silence test: a hook that says nothing either way would pass it vacuously."""
    root = _project(tmp_path / "project", "hooks_enabled: true\n")

    assert _run(HOOKS_DIR / "session-start.sh", root).stdout != b""


def test_hook_env_no_longer_carries_a_hooks_switch(tmp_path: Path) -> None:
    _write_hook_env_file(tmp_path / ".trw", resolve_client_profile("claude-code"))

    assert "HOOKS_ENABLED" not in (tmp_path / ".trw" / "runtime" / "hook-env.sh").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("client", "existing", "expected"),
    [
        ("opencode", "task_root: docs\n", {"task_root": "docs", "hooks_enabled": False}),
        ("opencode", "{task_root: docs}\n", {"task_root": "docs", "hooks_enabled": False}),
        ("opencode", "", {"hooks_enabled": False}),
        ("opencode", "hooks_enabled: true\n", {"hooks_enabled": True}),
        ("opencode", "{hooks_enabled: true}\n", {"hooks_enabled": True}),
        ("opencode", '"hooks_enabled": true\n', {"hooks_enabled": True}),
        ("claude-code", "task_root: docs\n", {"task_root": "docs"}),
    ],
    ids=[
        "hooks-off-profile-seeds-false",
        "seeds-into-a-flow-mapping",
        "seeds-an-empty-config",
        "operator-value-wins",
        "operator-value-wins-in-a-flow-mapping",
        "operator-value-wins-under-a-quoted-key",
        "hooks-on-profile-adds-nothing",
    ],
)
def test_the_profile_only_seeds_the_config_default(
    tmp_path: Path, client: str, existing: str, expected: dict[str, object]
) -> None:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "config.yaml").write_text(existing, encoding="utf-8")

    _write_hook_env_file(trw_dir, resolve_client_profile(client))

    assert YAML(typ="safe").load((trw_dir / "config.yaml").read_text(encoding="utf-8")) == expected
    published = hook_flags_path(trw_dir).read_text(encoding="utf-8")
    assert f"hooks_enabled={str(expected.get('hooks_enabled', True)).lower()}\n" in published
