"""Fallback-pusher consent-inheritance lock for _client_push._push_to_target.

PRD-SEC-004 (sweep-4 finding 3): when ``_push_to_target`` is handed a target
that was NOT pre-built in ``BackendSyncClient.__init__`` (absent from both the
primary slot and ``pusher_map``), it lazily constructs a ``SyncPusher``. That
lazy pusher MUST inherit the resolved consent flags
(``learning_sharing_enabled`` / ``platform_telemetry_enabled``) — otherwise it
would default fail-closed and silently drop a CONSENTED push, OR (if it ever
regressed to defaulting True) leak content without consent.

This branch was previously untested. These tests use a REAL ``SyncPusher`` (not
a mock) so the assertions lock actual behavior:
- the lazily-built pusher carries the flags it was given,
- with sharing ON the real (gated) push path proceeds (reaches the network
  boundary, which we observe via a patched httpx call),
- with sharing OFF the real push path early-returns with zero off-machine POST.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trw_mcp.sync._client_push import _push_to_target
from trw_mcp.sync.push import SyncPusher


def _entry() -> Any:
    """A dict-shaped 'entry'.

    SyncPusher._serialize_entry does ``dict(entry)`` then ``.get(...)`` and
    ``max(e.sync_seq ...)`` reads the attribute, so the consented push path
    (which actually serializes) needs a dict carrying a ``sync_seq``. A dict
    subclass with a ``sync_seq`` attribute satisfies both accesses.
    """

    class _Entry(dict):  # type: ignore[type-arg]
        sync_seq = 1

    return _Entry(id="L-1", summary="hello", detail="world", impact=0.5, tags=[])


def _primary() -> SyncPusher:
    # A distinct primary pusher whose label will NOT match the target, forcing
    # the pusher_map lookup -> miss -> lazy construction branch.
    return SyncPusher(
        backend_url="http://primary.example",
        api_key="pk-primary",
        client_id="c1",
        learning_sharing_enabled=True,
        platform_telemetry_enabled=True,
    )


@pytest.mark.asyncio
async def test_lazy_fallback_pusher_inherits_consent_flags_true() -> None:
    """Target absent from pusher_map -> lazily-built pusher carries flags=True."""
    target = SimpleNamespace(label="extra", url="http://extra.example", api_key="pk-extra")
    pusher_map: dict[str, SyncPusher] = {}

    await _push_to_target(
        client_id="c1",
        target=target,
        primary_target_label="primary",  # != target.label, so primary not used
        primary_pusher=_primary(),
        pusher_map=pusher_map,
        batch_size=100,
        timeout=5.0,
        dirty=[_entry()],
        outcomes=[{"k": "v"}],
        learning_sharing_enabled=True,
        platform_telemetry_enabled=True,
    )

    # The lazy pusher was created and cached under the target label.
    assert "extra" in pusher_map
    built = pusher_map["extra"]
    assert isinstance(built, SyncPusher)
    # It inherited the resolved consent flags rather than fail-closed defaults.
    assert built._learning_sharing_enabled is True
    assert built._platform_telemetry_enabled is True


@pytest.mark.asyncio
async def test_lazy_fallback_pusher_inherits_consent_flags_false() -> None:
    """Flags=False propagate to the lazy pusher (fail-closed inheritance)."""
    target = SimpleNamespace(label="extra", url="http://extra.example", api_key="pk-extra")
    pusher_map: dict[str, SyncPusher] = {}

    await _push_to_target(
        client_id="c1",
        target=target,
        primary_target_label="primary",
        primary_pusher=_primary(),
        pusher_map=pusher_map,
        batch_size=100,
        timeout=5.0,
        dirty=[_entry()],
        outcomes=[{"k": "v"}],
        learning_sharing_enabled=False,
        platform_telemetry_enabled=False,
    )

    built = pusher_map["extra"]
    assert built._learning_sharing_enabled is False
    assert built._platform_telemetry_enabled is False


@pytest.mark.asyncio
async def test_lazy_fallback_push_suppressed_when_sharing_disabled() -> None:
    """With sharing OFF the lazy pusher's real push path makes zero HTTP POST."""
    target = SimpleNamespace(label="extra", url="http://extra.example", api_key="pk-extra")
    pusher_map: dict[str, SyncPusher] = {}

    # If the consent gate is honored, push_learnings/push_outcomes early-return
    # before importing/using httpx, so this AsyncClient must never be touched.
    fake_async_client = MagicMock()
    with patch("httpx.AsyncClient", return_value=fake_async_client) as async_client_ctor:
        result = await _push_to_target(
            client_id="c1",
            target=target,
            primary_target_label="primary",
            primary_pusher=_primary(),
            pusher_map=pusher_map,
            batch_size=100,
            timeout=5.0,
            dirty=[_entry()],
            outcomes=[{"k": "v"}],
            learning_sharing_enabled=False,
            platform_telemetry_enabled=False,
        )

    async_client_ctor.assert_not_called()
    assert result.pushed == 0
    assert result.failed == 0


@pytest.mark.asyncio
async def test_lazy_fallback_push_proceeds_when_sharing_enabled() -> None:
    """With sharing ON the lazy pusher's real push path reaches the HTTP boundary."""
    target = SimpleNamespace(label="extra", url="http://extra.example", api_key="pk-extra")
    pusher_map: dict[str, SyncPusher] = {}

    # Build an httpx.AsyncClient-shaped async-context-manager mock whose POST
    # returns a successful backend body so push_learnings reports a push.
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"inserted": 1, "updated": 0, "skipped": 0, "errors": 0}
    async_client = MagicMock()
    async_client.post = AsyncMock(return_value=resp)
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=async_client) as async_client_ctor:
        result = await _push_to_target(
            client_id="c1",
            target=target,
            primary_target_label="primary",
            primary_pusher=_primary(),
            pusher_map=pusher_map,
            batch_size=100,
            timeout=5.0,
            dirty=[_entry()],
            outcomes=[],
            learning_sharing_enabled=True,
            platform_telemetry_enabled=True,
        )

    # The consented path actually reached the network boundary and pushed.
    async_client_ctor.assert_called()
    async_client.post.assert_awaited()
    assert result.pushed == 1
