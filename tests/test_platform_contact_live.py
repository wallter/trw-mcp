"""B71-106: every trw-mcp request asks the platform contact switch, and a file value is never cached.

sol's review of the first cut found multi-request sends that asked once, before their loop, so a switch
turned off mid-push still let later batches (or telemetry retries) out; and a TRWConfig loaded while the
project file said ``false`` kept vetoing after the operator switched contact back on.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config

from ._telemetry_pipeline_support import pipeline_cls  # noqa: F401

#: The payload project's consent: a send's policy is read from the .trw its payload came from.
_CONSENT = "learning_sharing_enabled: true\nplatform_telemetry_enabled: true\nbackup_remote_enabled: true\n"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home, root = tmp_path / "home", tmp_path / "project"
    (home / ".trw").mkdir(parents=True)
    (root / ".trw").mkdir(parents=True)
    (root / ".trw" / "config.yaml").write_text(_CONSENT, encoding="utf-8")  # the payload project's consent
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    monkeypatch.delenv("TRW_PLATFORM_CONTACT_ENABLED", raising=False)
    _reset_config(TRWConfig())
    yield root
    _reset_config()


def _switch(project: Path, value: str) -> None:
    (project / ".trw" / "config.yaml").write_text(_CONSENT + f"platform_contact_enabled: {value}\n", encoding="utf-8")


class _Entry:
    sync_seq = 1

    def to_dict(self) -> dict[str, object]:
        return {"summary": "a shareable learning", "importance": 0.9, "tags": []}


def test_a_push_stops_at_the_next_batch_once_the_switch_turns_off(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    from trw_mcp.sync.push import SyncPusher

    posts: list[str] = []

    class _Client:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def post(self, url: str, **_k: object) -> httpx.Response:
            posts.append(url)
            _switch(project, "false")  # the operator switches contact off after the first batch
            return httpx.Response(200, json={"inserted": 1}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    pusher = SyncPusher(
        backend_url="https://api.trwframework.com",
        api_key="k",
        client_id="c",
        learning_sharing_enabled=True,
        batch_size=1,
        source_trw_dir=project / ".trw",
    )

    result = asyncio.run(pusher.push_learnings([_Entry(), _Entry(), _Entry()]))  # type: ignore[list-item]

    assert len(posts) == 1 and result.pushed == 1


def test_telemetry_retries_stop_once_the_switch_turns_off(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.telemetry.sender import BatchSender

    attempts: list[str] = []

    def _post(_self: object, url: str, _payload: object) -> bool:
        attempts.append(url)
        _switch(project, "false")
        return False  # the platform did not accept it: the sender would retry

    monkeypatch.setattr(BatchSender, "_http_post", _post)
    sender = BatchSender(
        platform_urls=["https://api.trwframework.com"],
        platform_api_key="k",
        input_path=project / ".trw" / "queue.jsonl",
        platform_telemetry_enabled=True,
        max_retries=5,
        backoff_base=0.0,
        source_trw_dir=project / ".trw",
    )

    assert sender._send_batch_to("https://api.trwframework.com", [{"event": "x"}]) is False
    assert len(attempts) == 1


def test_contact_switched_back_on_in_the_file_takes_effect_in_a_running_process(project: Path) -> None:
    """The file value is read live, never cached in TRWConfig, so it cannot veto a later re-enable."""
    from trw_mcp.state._platform_trust import platform_contact_enabled

    _switch(project, "false")
    _reset_config()  # the process loads its config while the file says false
    assert platform_contact_enabled(project / ".trw") is False

    _switch(project, "true")
    assert platform_contact_enabled(project / ".trw") is True


def test_a_config_built_in_code_still_vetoes(project: Path) -> None:
    from trw_mcp.state._platform_trust import platform_contact_enabled

    _reset_config(TRWConfig(platform_contact_enabled=False))

    assert platform_contact_enabled(project / ".trw") is False


class _FlipOnPost:
    """An httpx.Client whose POST fails retryably after the operator switches contact off."""

    posts: list[str] = []
    project: Path

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    def __enter__(self) -> _FlipOnPost:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def post(self, url: str, **_k: object) -> object:
        import httpx

        self.posts.append(url)
        _switch(self.project, "false")
        return httpx.Response(429, request=httpx.Request("POST", url))  # rate limited: the sender would retry


def test_the_learning_publisher_stops_retrying_once_the_switch_turns_off(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sol r3: every POST asks, including the 429 retries."""
    import httpx

    from trw_mcp.telemetry import publisher

    _FlipOnPost.posts, _FlipOnPost.project = [], project
    monkeypatch.setattr(httpx, "Client", _FlipOnPost)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    assert (
        publisher._post_learning("https://api.trwframework.com", {"summary": "x"}, source_trw_dir=project / ".trw")
        is False
    )  # type: ignore[arg-type]
    assert len(_FlipOnPost.posts) == 1


def test_the_telemetry_pipeline_stops_retrying_once_the_switch_turns_off(
    project: Path, monkeypatch: pytest.MonkeyPatch, pipeline_cls: type
) -> None:
    import httpx

    _FlipOnPost.posts, _FlipOnPost.project = [], project
    monkeypatch.setattr(httpx, "Client", _FlipOnPost)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    pipeline = pipeline_cls()

    sent = pipeline._send_batch(
        [{"tool": "x"}],
        ["https://api.trwframework.com", "https://api.trwframework.com/b"],
        "k",
        roots=(project / ".trw",),
    )

    assert sent is False and len(_FlipOnPost.posts) == 1


def test_a_switch_check_that_faults_between_retries_ends_the_publish_without_a_traceback(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lead's sol pass on 694596c50: a working directory removed between retries raised out of the
    publisher and the pipeline. The resolver now fails closed on any fault, so the loop just stops."""
    import httpx

    from trw_mcp.telemetry import publisher

    posts: list[str] = []

    class _RateLimited(_FlipOnPost):
        def post(self, url: str, **_k: object) -> object:
            posts.append(url)
            monkeypatch.setattr("trw_memory.platform_contact._resolve", lambda *_a: 1 / 0)  # next ask faults
            return httpx.Response(429, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "Client", _RateLimited)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    assert (
        publisher._post_learning("https://api.trwframework.com", {"summary": "x"}, source_trw_dir=project / ".trw")
        is False
    )  # type: ignore[arg-type]
    assert len(posts) == 1
