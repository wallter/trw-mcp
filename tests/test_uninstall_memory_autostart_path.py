"""`uninstall --delete-memory` reaches the daemon through the shared-env launcher too (DAEMON-AUTOSTART-RESIDUAL-CLI).

Clients now start a missing daemon through the interpreter an env record names (`shared_server._daemon_launch`), so a
stale venv cannot win the respawn race. `_uninstall_memory.delete_checkout_memory` built its `DaemonClient` directly and
would start the daemon from its own interpreter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.server import _uninstall_memory


def test_delete_checkout_memory_builds_its_client_through_the_shared_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, Path, dict[str, Any]]] = []
    sentinel = object()

    def fake_daemon_client(token: str, trw_dir: Path, **kwargs: Any) -> object:
        seen.append((token, trw_dir, kwargs))
        return sentinel

    async def fake_forget_all(client: Any, namespace: str) -> int:
        assert client is sentinel
        return 3

    monkeypatch.setattr("trw_mcp.shared_server._daemon_launch.daemon_client", fake_daemon_client)
    monkeypatch.setattr(_uninstall_memory, "_forget_all", fake_forget_all)
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    deleted = _uninstall_memory.delete_checkout_memory(_uninstall_memory.CheckoutMemory("project:demo", "tok"), trw_dir)

    assert deleted == 3
    [(token, used_dir, kwargs)] = seen
    assert (token, used_dir) == ("tok", trw_dir) and "paths" in kwargs
