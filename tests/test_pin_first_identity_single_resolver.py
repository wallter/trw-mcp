"""Every enforcement surface resolves "which run is this caller pinned to" the same way (ledger RC-010).

Three surfaces decide who is acting: the MCP comms path (``get_pinned_run``), the
commit-ownership git hook (``scripts/check_formation_ownership._resolve_caller_run``)
and the edit-time advisory (``_formation_hook_advisory._pinned_run``). They reached
the pin store by different call chains, and nothing pinned them to the same answer,
so they could drift silently — one admitting a commit the other would attribute to
a different member.

All three now go through ``run_path_for_pin`` (PRD-CORE-296-FR02). These tests pin
the agreement across a live, an absent and a stale pin, and the properties that make
it safe: pin-first-and-pin-only (a stale or unknown key resolves to nothing rather
than to the most recently touched run), and no sibling adoption outside the server.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests._layout import MONOREPO_ROOT, requires_monorepo

#: A pid no live process holds, so only the heartbeat decides whether the pin expired.
_DEAD_PID = 999_999


@pytest.fixture
def hook_module() -> Any:
    """Load the standalone script the git hook runs, by path, as git does."""
    assert MONOREPO_ROOT is not None
    spec = importlib.util.spec_from_file_location(
        "check_formation_ownership", MONOREPO_ROOT / "scripts" / "check_formation_ownership.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], Path]:
    """Write a pin store holding one entry for ``pin-x``; returns the run it names.

    A second, newer active run sits beside it, so a recency fallback would have
    something to borrow.
    """
    from trw_mcp.state._pin_store import invalidate_pin_store_cache

    run = tmp_path / ".trw" / "runs" / "task" / "r1"
    (run / "meta").mkdir(parents=True)
    newer = tmp_path / ".trw" / "runs" / "task" / "r2"
    (newer / "meta").mkdir(parents=True)
    (newer / "meta" / "run.yaml").write_text("status: active\n", encoding="utf-8")
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_SESSION_ID", "pin-x")

    def write(overrides: dict[str, Any]) -> Path:
        pins = tmp_path / ".trw" / "runtime" / "pins.json"
        pins.parent.mkdir(parents=True, exist_ok=True)
        entry = {"run_path": str(run), "pid": _DEAD_PID, "last_heartbeat_ts": "2099-01-01T00:00:00Z", **overrides}
        pins.write_text(json.dumps({"pin-x": entry}), encoding="utf-8")
        invalidate_pin_store_cache()
        return run

    return write


def _surfaces(hook_module: Any) -> dict[str, Path | None]:
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths_pin_mgmt import get_pinned_run
    from trw_mcp.tools._formation_hook_advisory import _pinned_run

    return {
        "mcp": get_pinned_run(context=build_call_context(None)),
        "guard": hook_module._resolve_caller_run(),
        "advisory": _pinned_run(),
    }


@requires_monorepo
def test_a_live_pin_resolves_to_its_run_on_every_surface(project: Any, hook_module: Any) -> None:
    run = project({})
    assert _surfaces(hook_module) == {"mcp": run, "guard": run, "advisory": run}


@requires_monorepo
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"last_heartbeat_ts": "2026-01-01T00:00:00Z"}, id="expired-heartbeat"),
        pytest.param({"run_path": "<gone>"}, id="dangling-run-dir"),
    ],
)
def test_a_stale_pin_resolves_to_nothing_on_every_surface(
    overrides: dict[str, Any], project: Any, hook_module: Any, tmp_path: Path
) -> None:
    """A stale pin is no identity: never the newer active run beside it."""
    if overrides.get("run_path") == "<gone>":
        overrides = {"run_path": str(tmp_path / ".trw" / "runs" / "task" / "gone")}
    project(overrides)
    assert _surfaces(hook_module) == {"mcp": None, "guard": None, "advisory": None}


@requires_monorepo
def test_an_unknown_pin_resolves_to_nothing_on_every_surface(
    monkeypatch: pytest.MonkeyPatch, project: Any, hook_module: Any
) -> None:
    """Pin-first and pin-only: no mtime scan, no borrowed identity."""
    project({})
    monkeypatch.setenv("TRW_SESSION_ID", "pin-never-written")
    assert _surfaces(hook_module) == {"mcp": None, "guard": None, "advisory": None}


@requires_monorepo
def test_the_out_of_server_surfaces_reach_the_store_through_the_shared_resolver(hook_module: Any) -> None:
    """A second call chain is the drift this pins against."""
    from trw_mcp.tools import _formation_hook_advisory

    for module in (hook_module, _formation_hook_advisory):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "run_path_for_pin" in source, module.__name__
        assert "get_pin_entry" not in source, f"{module.__name__} must not reimplement the store lookup"


@requires_monorepo
def test_the_out_of_server_surfaces_never_adopt_a_sibling_pin(
    monkeypatch: pytest.MonkeyPatch, project: Any, hook_module: Any
) -> None:
    """Reconnect adoption matches a pin whose client is our PARENT; a hook's parent is git."""
    import trw_mcp.state._paths_pin_mgmt as mgmt
    from trw_mcp.tools._formation_hook_advisory import _pinned_run

    project({})
    calls: list[bool] = []
    real = mgmt.run_path_for_pin

    def spy(pin_key: str, *, adopt_sibling: bool = False) -> Path | None:
        calls.append(adopt_sibling)
        return real(pin_key, adopt_sibling=adopt_sibling)

    monkeypatch.setattr(mgmt, "run_path_for_pin", spy)
    hook_module._resolve_caller_run()
    _pinned_run()
    assert calls == [False, False]
