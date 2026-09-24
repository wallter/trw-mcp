"""PRD-CORE-298 FR07: the daemon's security settings are daemon-wide, and a client that differs refuses.

One daemon serves every checkout and enforces the settings it resolved from its own
environment. A client that resolves another value for any of them would lose that
policy silently, so the first attach compares the two sets and refuses on any
difference, naming the key and both values.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from pydantic_core import to_jsonable_python
from trw_memory.models.config import DAEMON_WIDE_SECURITY_KEYS, MemoryConfig, daemon_wide_security

from tests._memory_daemon import running_daemon
from tests._memory_fixtures import DaemonCheckout, MemoryDaemon, attach_checkout
from tests._path_isolation import set_current_root
from trw_mcp.models.config import reload_config
from trw_mcp.state import _daemon_store
from trw_mcp.state._daemon_store import DaemonMemoryStore, _require_matching_security, daemon_store_for
from trw_mcp.state._store_selection import StoreUnavailableError

#: One non-default value per daemon-wide key, as its environment variable spells it.
_DIFFERING = {
    "rbac_enabled": "true",
    "default_role": "reader",
    "namespace_roles": '{"default": "reader"}',
    "enable_recall_filter": "false",
    "recall_filter_mode": "strict",
    "canary_fail_mode": "degrade",
    "poisoning_detection_mode": "enforce",
    "enable_trust_scoring": "false",
    "trust_scoring_mode": "enforce",
    "provenance_required": "false",
}


class _StatusOnly:
    """A client whose ``memory_status`` answers with a fixed payload, counting the calls."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.calls = 0

    async def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        assert (name, arguments["security_settings_only"]) == ("memory_status", True)
        self.calls += 1
        return self._payload


@pytest.fixture
def fresh_clients(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    clients: dict[str, object] = {}
    monkeypatch.setattr(_daemon_store, "_clients", clients)
    return clients


def test_every_daemon_wide_key_has_a_differing_case() -> None:
    assert set(_DIFFERING) == set(DAEMON_WIDE_SECURITY_KEYS)


def test_matching_settings_open_the_store(daemon_checkout: DaemonCheckout, fresh_clients: dict[str, object]) -> None:
    store = daemon_store_for(daemon_checkout.trw_dir, daemon_checkout.namespace)

    assert isinstance(store, DaemonMemoryStore)
    assert len(fresh_clients) == 1


@pytest.mark.parametrize("key", sorted(_DIFFERING))
def test_a_differing_setting_refuses_naming_the_key_and_both_values(
    key: str,
    daemon_checkout: DaemonCheckout,
    fresh_clients: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon_value = daemon_wide_security(MemoryConfig())[key]  # the daemon started from this same environment
    monkeypatch.setenv(f"MEMORY_{key.upper()}", _DIFFERING[key])
    client_value = daemon_wide_security(MemoryConfig())[key]
    assert client_value != daemon_value

    with pytest.raises(StoreUnavailableError) as refused:
        daemon_store_for(daemon_checkout.trw_dir, daemon_checkout.namespace)

    message = str(refused.value)
    assert key in message
    assert repr(daemon_value) in message
    assert f"MEMORY_{key.upper()}" in message
    assert fresh_clients == {}, "a refused client must not be cached for later calls"


def test_a_daemon_that_does_not_report_its_settings_is_refused() -> None:
    with pytest.raises(StoreUnavailableError, match="did not report its security settings"):
        _require_matching_security(_StatusOnly({"total_entries": 0}), "project:x", {})  # type: ignore[arg-type]


def test_a_refused_status_call_is_refused_with_its_reason() -> None:
    with pytest.raises(StoreUnavailableError, match="namespace forbidden"):
        _require_matching_security(
            _StatusOnly({"error": "namespace forbidden", "status": "forbidden"}),  # type: ignore[arg-type]
            "project:x",
            {},
        )


def test_the_daemon_reports_the_settings_it_enforces(daemon_checkout: DaemonCheckout) -> None:
    status = asyncio.run(
        daemon_checkout.client.call_tool(
            "memory_status", {"namespace": daemon_checkout.namespace, "security_settings_only": True}
        )
    )

    assert set(status["security_settings"]) == set(DAEMON_WIDE_SECURITY_KEYS)


@pytest.mark.parametrize(
    "configured_checkout", [{"MEMORY_RBAC_ENABLED": "true", "MEMORY_DEFAULT_ROLE": "reader"}], indirect=True
)
def test_rbac_set_on_the_daemon_denies_a_write(configured_checkout: DaemonCheckout) -> None:
    from trw_mcp.state.memory_adapter import store_learning

    with pytest.raises(ToolError, match="'reader' does not have store permission"):
        store_learning(configured_checkout.trw_dir, "L-denied", "refused write", "detail")

    assert asyncio.run(configured_checkout.client.get("L-denied", configured_checkout.namespace)) == {
        "status": "not_found"
    }


@pytest.mark.parametrize(
    "configured_checkout",
    [{"MEMORY_POISONING_DETECTION_MODE": "enforce", "MEMORY_POISONING_Z_THRESHOLD": "1.0"}],
    indirect=True,
)
def test_poisoning_enforce_set_on_the_daemon_quarantines_an_anomaly(configured_checkout: DaemonCheckout) -> None:
    from trw_mcp.state.memory_adapter import store_learning

    for index in range(12):
        store_learning(configured_checkout.trw_dir, f"L-base{index:03d}", "short baseline summary", "short detail")

    outcome = store_learning(configured_checkout.trw_dir, "L-anomaly", "x" * 4000, "y" * 4000)

    assert outcome["status"] == "quarantined", outcome


@pytest.mark.parametrize(
    "configured_checkout",
    [
        {
            "MEMORY_ENABLE_TRUST_SCORING": "true",
            "MEMORY_TRUST_SCORING_MODE": "enforce",
            "MEMORY_PROVENANCE_REQUIRED": "true",
            "TRW_SESSION_ID": "env-session-123",
        }
    ],
    indirect=True,
)
def test_provenance_required_on_the_daemon_signs_the_stored_row(configured_checkout: DaemonCheckout) -> None:
    from trw_mcp.state.memory_adapter import store_learning

    store_learning(
        configured_checkout.trw_dir, "L-prov01", "Safe summary", "Safe detail", source_identity="audit-agent"
    )

    row = asyncio.run(configured_checkout.client.get("L-prov01", configured_checkout.namespace))["entry"]
    assert row["metadata"]["provenance_session_id"] == "env-session-123", row
    assert row["metadata"]["provenance_signature"]


def test_a_reported_int_never_matches_a_bool() -> None:
    local = to_jsonable_python(daemon_wide_security(MemoryConfig()))
    reported = {**local, "rbac_enabled": int(local["rbac_enabled"])}

    with pytest.raises(StoreUnavailableError, match="rbac_enabled"):
        _require_matching_security(_StatusOnly({"security_settings": reported}), "project:x", local)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "configured_checkout", [{"MEMORY_RBAC_ENABLED": "true", "MEMORY_DEFAULT_ROLE": "writer"}], indirect=True
)
def test_a_writer_without_read_attaches_when_the_settings_match(configured_checkout: DaemonCheckout) -> None:
    store = daemon_store_for(configured_checkout.trw_dir, configured_checkout.namespace)

    assert store.put("writer row", configured_checkout.namespace, {"entry_id": "L-w"})["status"] == "stored"


def test_a_daemon_restarted_under_other_settings_is_checked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_dir = tmp_path / "restart-home"
    trw_dir = tmp_path / "repo" / ".trw"
    monkeypatch.setenv("TRW_USER_DIR", str(user_dir))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(trw_dir.parent))
    monkeypatch.setenv("MEMORY_RECALL_FILTER_MODE", "redact")
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    monkeypatch.setattr(_daemon_store, "_clients", {})
    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", _no_autostart)
    with running_daemon(user_dir) as paths:
        namespace, _ = attach_checkout(trw_dir, MemoryDaemon(paths, user_dir))
        set_current_root(trw_dir.parent)
        reload_config()
        daemon_store_for(trw_dir, namespace)
    paths.discovery.unlink()  # the killed daemon's record, so the wait below sees the new one

    monkeypatch.setenv("MEMORY_RECALL_FILTER_MODE", "strict")
    with running_daemon(user_dir):
        monkeypatch.setenv("MEMORY_RECALL_FILTER_MODE", "redact")  # this client still resolves the old value
        with pytest.raises(StoreUnavailableError, match="recall_filter_mode"):
            daemon_store_for(trw_dir, namespace)


@pytest.mark.parametrize(("live", "checks"), [((1, "a"), 1), ((2, "b"), 2)])
def test_a_daemon_other_than_the_one_that_answered_is_checked_again(
    live: tuple[int, str],
    checks: int,
    tmp_path: Path,
    fresh_clients: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Daemon (1, "a") answers the check; if (2, "b") is live by the next attach, it was never checked."""
    local = to_jsonable_python(daemon_wide_security(MemoryConfig()))
    client = _StatusOnly({"security_settings": local, "daemon": [1, "a"]})
    monkeypatch.setattr("trw_memory.daemon.client.DaemonClient", lambda _token, instance=None: client)
    monkeypatch.setattr("trw_memory.daemon.read_checkout_grant", lambda _root: "grant")
    monkeypatch.setattr(_daemon_store, "_daemon_instance", lambda: live)

    for _ in range(2):
        daemon_store_for(tmp_path / ".trw", "project:x")

    assert client.calls == checks


def test_a_local_setting_changed_after_attach_is_checked_again(
    daemon_checkout: DaemonCheckout, fresh_clients: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon_store_for(daemon_checkout.trw_dir, daemon_checkout.namespace)
    monkeypatch.setenv("MEMORY_RECALL_FILTER_MODE", "strict")  # the daemon keeps running on its own value

    with pytest.raises(StoreUnavailableError, match="recall_filter_mode"):
        daemon_store_for(daemon_checkout.trw_dir, daemon_checkout.namespace)


def test_the_settings_reply_names_the_daemon_that_answered(
    daemon_checkout: DaemonCheckout, memory_daemon: MemoryDaemon
) -> None:
    from trw_memory.daemon._discovery import DaemonInfo, read_live_discovery

    status = asyncio.run(
        daemon_checkout.client.call_tool(
            "memory_status", {"namespace": daemon_checkout.namespace, "security_settings_only": True}
        )
    )
    live = read_live_discovery(memory_daemon.paths)

    assert isinstance(live, DaemonInfo)
    assert status["daemon"] == [live.pid, live.started_at]


def test_a_daemon_that_does_not_say_who_answered_is_refused() -> None:
    local = to_jsonable_python(daemon_wide_security(MemoryConfig()))

    with pytest.raises(StoreUnavailableError, match="which daemon answered"):
        _require_matching_security(_StatusOnly({"security_settings": local}), "project:x", local)  # type: ignore[arg-type]


def test_a_daemon_restarted_after_the_check_refuses_every_operation_until_checked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_dir = tmp_path / "restart-home"
    trw_dir = tmp_path / "repo" / ".trw"
    monkeypatch.setenv("TRW_USER_DIR", str(user_dir))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(trw_dir.parent))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    monkeypatch.setattr(_daemon_store, "_clients", {})
    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", _no_autostart)
    with running_daemon(user_dir) as paths:
        namespace, _ = attach_checkout(trw_dir, MemoryDaemon(paths, user_dir))
        set_current_root(trw_dir.parent)
        reload_config()
        checked = daemon_store_for(trw_dir, namespace)
    paths.discovery.unlink()  # the killed daemon's record, so the wait below sees the new one

    with running_daemon(user_dir):
        with pytest.raises(StoreUnavailableError, match="replaced"):
            checked.get("L-any")
        assert daemon_store_for(trw_dir, namespace).get("L-any") is None  # attached again: checked, then served


def _no_autostart(_paths: object) -> None:
    raise AssertionError("the test tried to start a memory daemon")
