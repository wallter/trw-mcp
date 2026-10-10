"""PRD-CORE-298 FR07: the daemon's security settings are daemon-wide, and a client that differs refuses.

One daemon serves every checkout and enforces the settings it resolved from its own
environment. A client that resolves a stricter value than the daemon for any of them
would lose that policy silently, so the first attach compares the two sets and refuses
a weaker or unrankable daemon value, naming the key and both values. A daemon that is
at least as strict is used with a warning: adopting its value never weakens filtering.
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
    "recall_filter_mode": "observe",
    "canary_fail_mode": "degrade",
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

    async def retire(self) -> None:
        """Holds no session; the store retires every client it cached when a test ends."""


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


#: The keys whose non-default value in :data:`_DIFFERING` is STRICTER than the default, so a
#: client resolving it is refused by a default daemon; the rest relax the default.
_STRICTER_THAN_DEFAULT = {"rbac_enabled", "default_role", "namespace_roles"}


@pytest.mark.parametrize("key", sorted(_STRICTER_THAN_DEFAULT))
def test_a_weaker_or_unordered_daemon_setting_refuses_naming_the_key_and_both_values(
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
    assert "Agents must not stop" in message
    assert fresh_clients == {}, "a refused client must not be cached for later calls"


@pytest.mark.parametrize("key", sorted(set(_DIFFERING) - _STRICTER_THAN_DEFAULT))
def test_a_stricter_daemon_setting_is_adopted_not_refused(
    key: str,
    daemon_checkout: DaemonCheckout,
    fresh_clients: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that relaxes a key still attaches to a daemon that enforces the stricter default."""
    monkeypatch.setenv(f"MEMORY_{key.upper()}", _DIFFERING[key])

    store = daemon_store_for(daemon_checkout.trw_dir, daemon_checkout.namespace)

    assert isinstance(store, DaemonMemoryStore)
    assert len(fresh_clients) == 1


def _settings_check(daemon_overrides: dict[str, object], local_overrides: dict[str, object]) -> tuple[int, str]:
    """Run the attach check against a daemon reporting the defaults plus *daemon_overrides*."""
    defaults = to_jsonable_python(daemon_wide_security(MemoryConfig()))
    reported = {**defaults, **daemon_overrides}
    local = {**defaults, **local_overrides}
    status = _StatusOnly({"security_settings": reported, "daemon": [7, "t"]})
    return _require_matching_security(status, "project:x", local)  # type: ignore[arg-type]


def test_an_older_daemons_retired_redact_mode_is_accepted_by_a_strict_client() -> None:
    """The reported 9.0.x failure: a daemon still running trw-memory 5.2.0 or older reports its
    then-default ``redact``, this client resolves today's default ``strict``. ``redact`` blocked a
    hash-drifted entry exactly as ``strict`` does, so the attach must not refuse learn/recall."""
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        assert _settings_check({"recall_filter_mode": "redact"}, {"recall_filter_mode": "strict"}) == (7, "t")

    assert any(
        log["event"] == "daemon_security_setting_stricter" and log["daemon_value"] == "redact" for log in logs
    ), logs


@pytest.mark.parametrize(
    ("key", "daemon_value", "local_value"),
    [
        ("recall_filter_mode", "observe", "strict"),
        ("recall_filter_mode", "lenient", "strict"),  # a value this client cannot rank
        ("enable_recall_filter", False, True),
        ("canary_fail_mode", "log-only", "degrade"),
        ("canary_fail_mode", "degrade", "halt"),
        ("provenance_required", False, True),
        ("rbac_enabled", False, True),
        ("default_role", "admin", "reader"),
        ("default_role", "reader", "admin"),  # roles have no safe order: any difference refuses
    ],
)
def test_a_weaker_or_unrankable_daemon_value_still_refuses(key: str, daemon_value: object, local_value: object) -> None:
    with pytest.raises(StoreUnavailableError, match=key):
        _settings_check({key: daemon_value}, {key: local_value})


@pytest.mark.parametrize(
    ("key", "daemon_value", "local_value"),
    [
        ("recall_filter_mode", "strict", "observe"),
        ("canary_fail_mode", "halt", "log-only"),
        ("enable_recall_filter", True, False),
        ("provenance_required", True, False),
        ("rbac_enabled", True, False),
    ],
)
def test_a_stricter_daemon_value_is_adopted(key: str, daemon_value: object, local_value: object) -> None:
    assert _settings_check({key: daemon_value}, {key: local_value}) == (7, "t")


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
    [
        {
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
    monkeypatch.setenv("MEMORY_RECALL_FILTER_MODE", "strict")
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    monkeypatch.setattr(_daemon_store, "_clients", {})
    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", _no_autostart)
    with running_daemon(user_dir) as paths:
        namespace, _ = attach_checkout(trw_dir, MemoryDaemon(paths, user_dir))
        set_current_root(trw_dir.parent)
        reload_config()
        daemon_store_for(trw_dir, namespace)
    paths.discovery.unlink()  # the killed daemon's record, so the wait below sees the new one

    monkeypatch.setenv("MEMORY_RECALL_FILTER_MODE", "observe")
    with running_daemon(user_dir):
        monkeypatch.setenv("MEMORY_RECALL_FILTER_MODE", "strict")  # this client still resolves the old value
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
    monkeypatch.setattr(
        "trw_memory.daemon.client.DaemonClient", lambda _token, instance=None, keep_session=False: client
    )
    monkeypatch.setattr("trw_memory.daemon.read_checkout_grant", lambda _root: "grant")
    monkeypatch.setattr(_daemon_store, "_daemon_instance", lambda: live)

    for _ in range(2):
        daemon_store_for(tmp_path / ".trw", "project:x")

    assert client.calls == checks


@pytest.mark.parametrize("configured_checkout", [{"MEMORY_RECALL_FILTER_MODE": "observe"}], indirect=True)
def test_a_local_setting_changed_after_attach_is_checked_again(
    configured_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon_store_for(configured_checkout.trw_dir, configured_checkout.namespace)
    monkeypatch.setenv("MEMORY_RECALL_FILTER_MODE", "strict")  # the daemon keeps running on its own value

    with pytest.raises(StoreUnavailableError, match="recall_filter_mode"):
        daemon_store_for(configured_checkout.trw_dir, configured_checkout.namespace)


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


class _Retirable:
    """A cached client that records when its owner retires it."""

    def __init__(self) -> None:
        self.retired = 0

    async def retire(self) -> None:
        self.retired += 1


def test_a_replaced_client_is_retired_and_the_current_one_is_not(
    tmp_path: Path, fresh_clients: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Release-verify RES-01: a daemon restart replaces the cached client and retires the old one's session."""
    built: list[_Retirable] = []

    def _build(_token: str, instance: object = None, keep_session: bool = False) -> _Retirable:
        built.append(_Retirable())
        return built[-1]

    live = [(1, "a")]
    monkeypatch.setattr("trw_memory.daemon.client.DaemonClient", _build)
    monkeypatch.setattr("trw_memory.daemon.read_checkout_grant", lambda _root: "grant")
    monkeypatch.setattr(_daemon_store, "_daemon_instance", lambda: live[0])
    monkeypatch.setattr(_daemon_store, "_require_matching_security", lambda _client, _ns, _local: live[0])

    daemon_store_for(tmp_path / ".trw", "project:x")
    daemon_store_for(tmp_path / ".trw", "project:x")  # cached: nothing replaced
    live[0] = (2, "b")
    daemon_store_for(tmp_path / ".trw", "project:x")
    _daemon_store._run(asyncio.sleep(0))  # the retire was queued on the daemon-call loop first

    cached = [client for client in built if client is fresh_clients["grant"][0]]
    retired = [client.retired for client in built if client not in cached]
    # Each attach builds a checker client and a cached one; only the first cached client was replaced.
    assert [client.retired for client in cached] == [0]
    assert retired.count(1) == 1 and set(retired) <= {0, 1}
