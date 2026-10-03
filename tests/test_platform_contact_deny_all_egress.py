"""B71-106/107 vertical slice: with platform contact off, no platform sender in
either package opens a connection.

``platform_contact_enabled`` (``trw_memory.platform_contact``) is the ONE
kill switch both packages resolve, layered env > project ``.trw/config.yaml``
> machine ``~/.trw/config.yaml`` > on. ``trw_mcp.state._platform_trust.
platform_contact_enabled`` delegates to it (a ``TRWConfig`` field may only
veto on top). Every platform-egress sender in both packages asks one of the
two gates (``platform_auth_headers``/``platform_contact_enabled`` in
trw-mcp, ``platform_contact_blocked`` in trw-memory) before it would open a
connection.

This file drives every one of the 10 known platform senders (a grep sweep of
both packages for HTTP clients, 2026-09-26) with arguments that WOULD send if contact
were on — sync/consent flags on, a real-looking platform URL, a fake API
key, a non-empty payload — while the switch is off, four different ways, and
proves nothing ever opens a socket. A positive control (switch ON) proves
the harness itself would have caught a leak.

    | # | Sender                              | Package   | Entry point                                    |
    |---|-------------------------------------|-----------|-------------------------------------------------|
    | 1 | Feedback submission                 | trw-mcp   | ``submit_feedback.submit_feedback_via_http``     |
    | 2 | Sync pull (intel state)             | trw-mcp   | ``sync.pull.SyncPuller.pull_intel_state``        |
    | 3 | Sync push (learnings)               | trw-mcp   | ``sync.push.SyncPusher.push_learnings``          |
    | 4 | Sync push (outcomes)                | trw-mcp   | ``sync.push.SyncPusher.push_outcomes``           |
    | 5 | Telemetry batch send (sender)       | trw-mcp   | ``telemetry.sender.BatchSender.send``            |
    | 6 | Telemetry batch publish (publisher) | trw-mcp   | ``telemetry.publisher.publish_learnings``        |
    | 7 | Telemetry batch send (pipeline)     | trw-mcp   | ``telemetry.pipeline.TelemetryPipeline.flush_now``|
    | 8 | Shared memory publish               | trw-memory| ``sync._remote_publish.publish_memory_result``   |
    | 9 | Retry-queue drain                   | trw-memory| ``sync._remote_publish.drain_retry_queue``       |
    |10a| Remote retire                       | trw-memory| ``sync._remote_publish.retire_remote_memory``    |
    |10b| Shared memory fetch                 | trw-memory| ``sync._remote_fetch.fetch_shared_memories``     |
    |10c| SSE subscriber                      | trw-memory| ``sync.subscriber.SSESubscriber.start``/``stop`` |

(10c and the retry-queue drain are both covered so the table is 12 rows for
the 10 inventoried senders — ``retire_remote_memory`` and the SSE subscriber
were bundled as "10" in the inventory's summary count but are driven and
asserted separately here.)
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest
from trw_memory.models.config import MemoryConfig
from trw_memory.models.memory import MemoryEntry
from trw_memory.platform_contact import platform_contact_enabled as memory_platform_contact_enabled
from trw_memory.sync._remote_fetch import fetch_shared_memories
from trw_memory.sync._remote_publish import drain_retry_queue, publish_memory_result, retire_remote_memory
from trw_memory.sync.retry_queue import RetryQueue
from trw_memory.sync.subscriber import SSESubscriber

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state._platform_trust import platform_contact_enabled as mcp_platform_contact_enabled
from trw_mcp.sync.pull import SyncPuller
from trw_mcp.sync.push import SyncPusher
from trw_mcp.telemetry import publisher as mcp_publisher
from trw_mcp.telemetry.sender import BatchSender, stamp_consent
from trw_mcp.tools.submit_feedback import submit_feedback_via_http

from ._telemetry_pipeline_support import pipeline_cls  # noqa: F401

#: A real-looking platform URL and a fake credential — everything a sender
#: needs to believe it should send, short of contact actually being allowed.
BACKEND_URL = "https://api.trwframework.com"
FAKE_API_KEY = "trw-fake-api-key-for-egress-test-only"


# ---------------------------------------------------------------------------
# Egress recorder — patches the LOWEST level so nothing can slip by.
# ---------------------------------------------------------------------------


@pytest.fixture
def egress_recorder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every attempt to resolve a host or open a socket, and refuse it.

    httpx's sync backend calls ``socket.create_connection``; its async
    backend (used by ``httpx.AsyncClient``) resolves through the event loop,
    which calls ``socket.getaddrinfo`` in a worker thread. ``socket.socket.
    connect`` is patched too so a raw/manually-built socket has nowhere to
    hide either.
    """
    attempts: list[str] = []

    def _blocked_getaddrinfo(host: object, port: object, *args: object, **kwargs: object) -> Any:
        attempts.append(f"getaddrinfo:{host}:{port}")
        raise OSError("egress refused in test")

    def _blocked_create_connection(address: object, *args: object, **kwargs: object) -> Any:
        attempts.append(f"create_connection:{address}")
        raise OSError("egress refused in test")

    def _blocked_connect(self: socket.socket, address: object, *args: object, **kwargs: object) -> Any:
        attempts.append(f"socket.connect:{address}")
        raise OSError("egress refused in test")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    return attempts


# ---------------------------------------------------------------------------
# Isolation: HOME, TRW_PROJECT_ROOT, and a clean TRWConfig singleton.
# ---------------------------------------------------------------------------


_CONSENT = "learning_sharing_enabled: true\nplatform_telemetry_enabled: true\nbackup_remote_enabled: true\n"


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".trw").mkdir(parents=True)
    (project / ".trw").mkdir(parents=True)
    # The payload project grants every consent, so only the switch under test can stop a send.
    (project / ".trw" / "config.yaml").write_text(_CONSENT, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    monkeypatch.delenv("TRW_PLATFORM_CONTACT_ENABLED", raising=False)
    # Default TRWConfig(): platform_contact_enabled=True, i.e. the trw-mcp
    # veto field does NOT fire — only the shared switch under test decides.
    _reset_config(TRWConfig())
    return project


def _apply_switch_off(mode: str, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = Path.home()
    if mode == "machine":
        (home / ".trw" / "config.yaml").write_text("platform_contact_enabled: false\n", encoding="utf-8")
    elif mode == "env":
        monkeypatch.setenv("TRW_PLATFORM_CONTACT_ENABLED", "false")
    elif mode == "project":
        (project / ".trw" / "config.yaml").write_text(_CONSENT + "platform_contact_enabled: false\n", encoding="utf-8")
    elif mode == "project-null-over-machine-false":
        (project / ".trw" / "config.yaml").write_text(_CONSENT + "platform_contact_enabled: null\n", encoding="utf-8")
        (home / ".trw" / "config.yaml").write_text("platform_contact_enabled: false\n", encoding="utf-8")
    else:  # pragma: no cover — parametrize guards this
        raise ValueError(f"unknown mode: {mode}")


# ---------------------------------------------------------------------------
# One thunk per inventoried sender. Each is called with arguments that WOULD
# send if platform contact were on: sync/consent flags true, the real host,
# a fake key, and a non-empty payload.
# ---------------------------------------------------------------------------


def _mcp_config_that_would_send() -> TRWConfig:
    return TRWConfig(
        platform_url=BACKEND_URL,
        platform_urls=[BACKEND_URL],
        learning_sharing_enabled=True,
        platform_telemetry_enabled=True,
        platform_api_key=FAKE_API_KEY,
    )


def _memory_config_that_would_send(project: Path) -> MemoryConfig:
    return MemoryConfig(
        sync_enabled=True,
        platform_url=BACKEND_URL,
        platform_api_key=FAKE_API_KEY,
        sync_min_importance=0.0,
        project_root=str(project),
    )


class _PushEntry:
    """Minimal stand-in for a ``MemoryEntry`` as ``SyncPusher.push_learnings`` needs it."""

    sync_seq = 1

    def to_dict(self) -> dict[str, object]:
        return {"summary": "a shareable learning", "importance": 0.9, "tags": []}


async def _run_submit_feedback(project: Path) -> None:
    payload: Any = {
        "category": "feedback",
        "subject": "egress test",
        "message": "a message body long enough to pass validation",
        "metadata": {},
    }
    submit_feedback_via_http(
        backend_url=BACKEND_URL, api_key=FAKE_API_KEY, payload=payload, source_trw_dir=project / ".trw"
    )


async def _run_sync_pull(project: Path) -> None:
    puller = SyncPuller(
        backend_url=BACKEND_URL, api_key=FAKE_API_KEY, client_id="egress-test-client", trw_dir=project / ".trw"
    )
    await puller.pull_intel_state()


async def _run_sync_push_learnings(project: Path) -> None:
    pusher = SyncPusher(
        backend_url=BACKEND_URL,
        api_key=FAKE_API_KEY,
        client_id="egress-test-client",
        learning_sharing_enabled=True,
        source_trw_dir=project / ".trw",
    )
    await pusher.push_learnings([_PushEntry()])  # type: ignore[list-item]


async def _run_sync_push_outcomes(project: Path) -> None:
    pusher = SyncPusher(
        backend_url=BACKEND_URL,
        api_key=FAKE_API_KEY,
        client_id="egress-test-client",
        platform_telemetry_enabled=True,
        source_trw_dir=project / ".trw",
    )
    await pusher.push_outcomes([{"outcome": "egress-test"}])


async def _run_telemetry_sender(project: Path) -> None:
    queue_path = project / ".trw" / "telemetry-queue.jsonl"
    record = stamp_consent({"event": "egress-test"}, consented=True)
    queue_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    sender = BatchSender(
        platform_urls=[BACKEND_URL],
        platform_api_key=FAKE_API_KEY,
        input_path=queue_path,
        platform_telemetry_enabled=True,
        max_retries=1,  # avoid the exponential-backoff sleep on repeated refusal
        source_trw_dir=project / ".trw",
    )
    sender.send()


async def _run_telemetry_publisher() -> None:
    mcp_publisher.publish_learnings()


async def _run_telemetry_pipeline(pipeline_factory: type) -> None:
    pipeline = pipeline_factory()
    pipeline._queue.append({"tool": "egress-test"})
    from unittest.mock import MagicMock

    pipeline._writer = MagicMock()
    pipeline.flush_now()


async def _run_memory_publish(cfg: MemoryConfig) -> None:
    entry = MemoryEntry(id="M-egress-test", content="a shareable learning", importance=0.95)
    publish_memory_result(entry, cfg)


async def _run_memory_retry_drain(cfg: MemoryConfig, tmp_path: Path) -> None:
    queue = RetryQueue(tmp_path / "retry-queue.jsonl")
    queue.enqueue("M-queued", {"source_learning_id": "M-queued", "summary": "queued content"})
    drain_retry_queue(queue, cfg)


async def _run_memory_retire(cfg: MemoryConfig) -> None:
    retire_remote_memory("R-egress-test", cfg)


async def _run_memory_fetch(cfg: MemoryConfig) -> None:
    fetch_shared_memories("some query text", cfg, admit=lambda _entry: True)


async def _run_memory_sse_subscriber(cfg: MemoryConfig) -> None:
    """The listener starts (only the live switch is off) and must wait, not connect, across reconnects."""
    import threading

    from trw_memory.sync import subscriber as subscriber_module

    delay, subscriber_module.RECONNECT_DELAY = subscriber_module.RECONNECT_DELAY, 0.02
    subscriber = SSESubscriber(cfg, on_event=lambda _data: None)
    subscriber.start()
    try:
        threading.Event().wait(0.2)  # ~10 reconnect periods
    finally:
        subscriber.stop()
        subscriber_module.RECONNECT_DELAY = delay


async def _run_jev_judge() -> None:
    from trw_memory.decisions._jev_http import JevHttpJudge
    from trw_memory.decisions._models import NoulQuestion

    judge = JevHttpJudge("fake-key")
    outcome = judge.decide("user content", {"q": NoulQuestion(instructions="Q?", criteria={"true": "t", "false": "f"})})
    assert getattr(outcome, "kind", None) == "disabled", f"a vetoed judge must say so, got {outcome!r}"


async def _run_remote_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.clients.llm import LLMClient

    monkeypatch.setenv("OLLAMA_HOST", "http://ollama.example.invalid:11434")
    assert await LLMClient()._ask_ollama("a prompt with user content") is None


async def _run_anthropic_client() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from trw_mcp.clients.llm import LLMClient

    client = LLMClient()
    sdk = MagicMock()
    sdk.messages.create = AsyncMock()
    client._async_client, client._available = sdk, True
    assert await client.ask("a prompt with user content") is None
    sdk.messages.create.assert_not_awaited()


async def _drive_all_senders(
    project: Path, tmp_path: Path, pipeline_factory: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Call every one of the 10 inventoried senders with arguments that would
    send if contact were on."""
    _reset_config(_mcp_config_that_would_send())
    try:
        await _run_submit_feedback(project)
        await _run_sync_pull(project)
        await _run_sync_push_learnings(project)
        await _run_sync_push_outcomes(project)
        await _run_telemetry_sender(project)
        await _run_telemetry_publisher()
        await _run_telemetry_pipeline(pipeline_factory)
    finally:
        _reset_config(TRWConfig())

    cfg = _memory_config_that_would_send(project)
    await _run_memory_publish(cfg)
    await _run_memory_retry_drain(cfg, tmp_path)
    await _run_memory_retire(cfg)
    await _run_memory_fetch(cfg)
    await _run_memory_sse_subscriber(cfg)
    await _run_jev_judge()
    await _run_remote_ollama(monkeypatch)
    await _run_anthropic_client()


# ---------------------------------------------------------------------------
# The vertical slice: switch off, four ways, every sender, zero egress.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["machine", "env", "project", "project-null-over-machine-false"])
async def test_no_sender_opens_a_connection_when_contact_is_off(
    mode: str,
    isolated_project: Path,
    egress_recorder: list[str],
    pipeline_cls: type,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = isolated_project
    _apply_switch_off(mode, project, monkeypatch)

    # Sanity: the switch really is off by both packages' own resolution,
    # before we even get to the senders.
    assert mcp_platform_contact_enabled(project / ".trw") is False
    assert memory_platform_contact_enabled(project_root=str(project)) is False

    await _drive_all_senders(project, tmp_path, pipeline_cls, monkeypatch)

    assert egress_recorder == [], f"a sender opened a connection with contact off ({mode}): {egress_recorder}"


# ---------------------------------------------------------------------------
# Positive control: with the switch ON, the harness itself must observe an
# attempt — otherwise the negative results above would be worthless.
# ---------------------------------------------------------------------------


async def test_control_senders_do_attempt_egress_when_contact_is_on(
    isolated_project: Path,
    egress_recorder: list[str],
) -> None:
    project = isolated_project
    assert mcp_platform_contact_enabled(project / ".trw") is True
    assert memory_platform_contact_enabled(project_root=str(project)) is True

    _reset_config(_mcp_config_that_would_send())
    try:
        await _run_submit_feedback(project)
    finally:
        _reset_config(TRWConfig())

    cfg = _memory_config_that_would_send(project)
    await _run_memory_publish(cfg)

    assert egress_recorder, "control failed: no sender attempted egress with contact ON"


async def test_control_remote_ollama_attempts_egress_when_contact_is_on_and_loopback_never_is_vetoed(
    isolated_project: Path,
    egress_recorder: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With contact ON the remote host is tried (the harness sees it); a loopback host is tried even with it OFF."""
    from trw_mcp.clients.llm import LLMClient

    # The client asks the switch of the project it runs in; the suite sandbox roots that at tmp_path.
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: isolated_project / ".trw")
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama.example.invalid:11434")
    await LLMClient()._ask_ollama("a prompt")
    assert egress_recorder, "control failed: a remote Ollama was not attempted with contact ON"

    egress_recorder.clear()
    monkeypatch.setenv("TRW_PLATFORM_CONTACT_ENABLED", "false")
    for host in ("http://localhost:11434", "127.0.0.1:11434", "http://[::1]:11434"):
        monkeypatch.setenv("OLLAMA_HOST", host)
        await LLMClient()._ask_ollama("a prompt")
    assert egress_recorder, "a loopback Ollama must not be vetoed by the contact switch"


async def test_control_anthropic_client_sends_when_contact_is_on(
    isolated_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from trw_mcp.clients.llm import LLMClient

    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: isolated_project / ".trw")
    client = LLMClient()
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=MagicMock(content=[], stop_reason="end_turn"))
    client._async_client, client._available = sdk, True
    await client.ask("a prompt")
    sdk.messages.create.assert_awaited_once()
