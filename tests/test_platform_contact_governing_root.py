"""A send's whole policy is read from the ``.trw`` its PAYLOAD was read from (CONTACT-SWITCH-ANCHOR).

The contact switch and the consent flags (``learning_sharing_enabled``, ``platform_telemetry_enabled``,
``backup_remote_enabled``) come from ``state._platform_trust.send_policy(source_trw_dir)``: the config
of the project the content came from, never the cached process config, the cwd or
``TRW_PROJECT_ROOT``. A payload with no ``.trw`` has no policy and is not sent. Review history:
r1 (a send from ``<project>/src`` read ``src``'s absent switch and defaulted to contact on), r2
(a config cached in project A authorized project B's learnings) and r3 (a payload file relocated
into B was gated on A: ``test_platform_contact_payload_path.py`` holds that rule and its census).
"""

from __future__ import annotations

import ast
import json
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests import _source_index as source_index
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.sync.backup import BackupUploader
from trw_mcp.sync.client import BackendSyncClient
from trw_mcp.telemetry.sender import BatchSender, stamp_consent
from trw_mcp.tools.submit_feedback import submit_feedback_via_http

from ._telemetry_pipeline_support import pipeline_cls  # noqa: F401

pytestmark = pytest.mark.repo_scan

BACKEND_URL = "https://api.trwframework.com"
FAKE_API_KEY = "trw-fake-api-key-for-governing-root-test"
_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
_CONSENT = "learning_sharing_enabled: true\nplatform_telemetry_enabled: true\nbackup_remote_enabled: true\n"
_NO_CONSENT = "learning_sharing_enabled: false\nplatform_telemetry_enabled: false\nbackup_remote_enabled: false\n"


# --- census 1: no argument-less contact-switch call anywhere in trw_mcp ------------------------


def _argless_switch_calls(tree: ast.AST) -> list[int]:
    """Lines calling ``platform_contact_enabled`` (by any imported alias or attribute) with no argument."""
    names = {"platform_contact_enabled"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {a.asname for a in node.names if a.name == "platform_contact_enabled" and a.asname}
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and not node.args
        and not node.keywords
        and (
            (isinstance(node.func, ast.Name) and node.func.id in names)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "platform_contact_enabled")
        )
    ]


def test_no_sender_asks_the_contact_switch_without_naming_its_payload_source() -> None:
    offenders = {
        f"{path.relative_to(_SRC).as_posix()}:{line}"
        for path in _SRC.rglob("*.py")
        for line in _argless_switch_calls(source_index.tree(path))
    }
    assert not offenders, f"platform_contact_enabled() re-finds its project; pass the payload's .trw: {offenders}"


def test_the_census_sees_an_aliased_argless_call() -> None:
    tree = ast.parse("from trw_memory.platform_contact import platform_contact_enabled as on\nok = on()\n")
    assert _argless_switch_calls(tree) == [2]


# --- census 2 (every gate root is derived from its payload's path, or a named exception) lives in
# test_platform_contact_payload_path.py, keyed by (module, qualname) with a reason per exception.


# --- shared harness ----------------------------------------------------------------------------


@pytest.fixture
def egress(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record and refuse every host lookup or connection (sync httpx, async httpx, raw sockets)."""
    attempts: list[str] = []

    def refuse(*args: object, **_kwargs: object) -> Any:
        attempts.append(repr(args[:2]))
        raise OSError("egress refused in test")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    return attempts


def _project(root: Path, contact: str, consent: str = _CONSENT) -> Path:
    (root / ".trw").mkdir(parents=True, exist_ok=True)
    (root / ".trw" / "config.yaml").write_text(consent + f"platform_contact_enabled: {contact}\n", encoding="utf-8")
    return root


class _PushEntry:
    sync_seq = 1

    def to_dict(self) -> dict[str, object]:
        return {"summary": "a shareable learning", "importance": 0.9, "tags": []}


# --- a sync client or an explicit-source sender obeys its payload's project, not TRW_PROJECT_ROOT


@pytest.fixture
def governing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[Path]:
    """The payload project says ``request.param``; the project TRW_PROJECT_ROOT names says the opposite."""
    contact = request.param
    (tmp_path / "home" / ".trw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PLATFORM_CONTACT_ENABLED", raising=False)
    elsewhere = _project(tmp_path / "elsewhere", "true" if contact == "false" else "false")
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(elsewhere))
    _reset_config(
        TRWConfig(
            platform_url=BACKEND_URL,
            platform_urls=[BACKEND_URL],
            learning_sharing_enabled=True,
            platform_telemetry_enabled=True,
            platform_api_key=FAKE_API_KEY,
        )
    )
    yield _project(tmp_path / "governing", contact)
    _reset_config(TRWConfig())


async def _drive_sync_client(project: Path) -> None:
    from trw_mcp.models.config import get_config

    client = BackendSyncClient(config=get_config(), trw_dir=project / ".trw")
    await client._pusher.push_learnings([_PushEntry()])  # type: ignore[list-item]
    await client._pusher.push_outcomes([{"outcome": "governing-root-test"}])
    await client._puller.pull_intel_state()


@pytest.mark.parametrize(("governing", "attempted"), [("false", False), ("true", True)], indirect=["governing"])
async def test_sync_client_obeys_its_trw_dirs_switch_not_trw_project_root(
    governing: Path, attempted: bool, egress: list[str]
) -> None:
    await _drive_sync_client(governing)

    assert bool(egress) is attempted, egress


async def _feedback(project: Path) -> None:
    payload: Any = {"category": "feedback", "subject": "s", "message": "a message body", "metadata": {}}
    submit_feedback_via_http(
        backend_url=BACKEND_URL, api_key=FAKE_API_KEY, payload=payload, source_trw_dir=project / ".trw"
    )


async def _telemetry(project: Path) -> None:
    queue = project / ".trw" / "telemetry-queue.jsonl"
    queue.write_text(json.dumps(stamp_consent({"event": "e"}, consented=True)) + "\n", encoding="utf-8")
    BatchSender(
        platform_urls=[BACKEND_URL],
        platform_api_key=FAKE_API_KEY,
        input_path=queue,
        platform_telemetry_enabled=True,
        max_retries=1,
        source_trw_dir=project / ".trw",
    ).send()


async def _backup(project: Path) -> None:
    archive = project / "backup.tar.zst"
    archive.write_bytes(b"archive bytes")
    uploader = BackupUploader(
        BACKEND_URL, FAKE_API_KEY, "c1", backup_remote_enabled=True, source_trw_dir=project / ".trw"
    )
    await uploader.upload(archive)
    await uploader.list_remote()


@pytest.mark.parametrize(("governing", "attempted"), [("false", False), ("true", True)], indirect=["governing"])
@pytest.mark.parametrize("sender", [_feedback, _telemetry, _backup], ids=["feedback", "telemetry", "backup"])
async def test_an_explicit_source_sender_obeys_that_projects_switch(
    sender: Any, governing: Path, attempted: bool, egress: list[str]
) -> None:
    await sender(governing)

    assert bool(egress) is attempted, egress


# --- production entry points, machine-level credentials, TRW_PROJECT_ROOT unset -----------------

_MACHINE_CREDENTIALS = f"platform_urls: [{BACKEND_URL}]\n" + _CONSENT


def _run_from(cwd: Path, monkeypatch: pytest.MonkeyPatch, *, reload: bool = True) -> None:
    """Run as if from *cwd*; ``reload=False`` keeps the config already cached (the r2 shape)."""
    from tests import _path_isolation

    monkeypatch.chdir(cwd)
    _path_isolation.set_current_root(cwd)  # resolve_project_root()/resolve_trw_dir() see the cwd, as in production
    if reload:
        _reset_config()  # get_config() rebuilds from the files, under this root


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home"
    (home / ".trw").mkdir(parents=True)
    (home / ".trw" / "config.yaml").write_text(_MACHINE_CREDENTIALS, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRW_PLATFORM_API_KEY", FAKE_API_KEY)  # the machine-level credential (PRD-SEC-005)
    for name in ("TRW_PROJECT_ROOT", "TRW_PLATFORM_CONTACT_ENABLED", "TRW_BACKEND_URL", "TRW_BACKEND_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path
    _reset_config(TRWConfig())


def _seed(root: Path) -> None:
    """A learning under the project's own ``.trw``, for the publisher."""
    from tests._test_telemetry_publisher_support import _make_learning, _write_learning

    if (root / ".trw").is_dir():
        _write_learning(root / ".trw" / "learnings" / "entries", "l.yaml", _make_learning(impact=0.9))


async def _entry_feedback(_tmp: Path, _pipeline: Any, _egress: list[str]) -> None:
    from trw_mcp.tools.submit_feedback import submit_feedback

    submit_feedback(category="feedback", subject="subdir", message="a message body long enough to pass")


async def _entry_sync_push(_tmp: Path, _pipeline: Any, _egress: list[str]) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_trw_dir

    client = BackendSyncClient(config=get_config(), trw_dir=resolve_trw_dir())  # as _boot_deferred builds it
    await client._pusher.push_learnings([_PushEntry()])  # type: ignore[list-item]
    await client._pusher.push_outcomes([{"outcome": "subdir"}])


async def _entry_sync_pull(_tmp: Path, _pipeline: Any, _egress: list[str]) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_trw_dir

    await BackendSyncClient(config=get_config(), trw_dir=resolve_trw_dir())._puller.pull_intel_state()


async def _entry_backup(tmp: Path, _pipeline: Any, _egress: list[str]) -> None:
    import argparse

    from trw_memory.exceptions import StorageRootUnresolvableError

    from trw_mcp.models.config import get_config
    from trw_mcp.server._subcommands_backup import _build_uploader, _resolve_base_and_db
    from trw_mcp.state._platform_trust import payload_trw_dir

    archive = tmp / "backup.tar.zst"
    archive.write_bytes(b"archive bytes")
    try:  # as the CLI resolves it: the policy root is the .trw that owns the store's db file
        source = payload_trw_dir(_resolve_base_and_db(argparse.Namespace(db=None, namespace="default"))[1])
    except StorageRootUnresolvableError:  # no project: the CLI refuses before any contact
        source = None
    uploader = _build_uploader(get_config(), client_id="c1", source_trw_dir=source)
    await uploader.upload(archive)
    await uploader.list_remote()


async def _entry_telemetry_sender(_tmp: Path, _pipeline: Any, _egress: list[str]) -> None:
    sender = BatchSender.from_config()
    queue = sender._input_path
    if queue.parent.parent.is_dir():  # only inside a real .trw: seeding must never create a project
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text(json.dumps(stamp_consent({"event": "e"}, consented=True)) + "\n", encoding="utf-8")
    sender._max_retries = 1
    sender.send()


async def _entry_publisher(_tmp: Path, _pipeline: Any, _egress: list[str]) -> None:
    from trw_mcp.telemetry.publisher import publish_learnings

    publish_learnings(force=True)


async def _entry_pipeline(_tmp: Path, pipeline: Any, _egress: list[str]) -> None:
    from unittest.mock import MagicMock

    instance = pipeline()
    instance._writer = MagicMock()
    instance.enqueue({"tool": "subdir"})
    instance.flush_now()


_ENTRY_POINTS = {
    "tools/submit_feedback": _entry_feedback,
    "sync/push": _entry_sync_push,
    "sync/pull": _entry_sync_pull,
    "sync/backup": _entry_backup,
    "telemetry/sender": _entry_telemetry_sender,
    "telemetry/publisher": _entry_publisher,
    "telemetry/pipeline": _entry_pipeline,
}


def test_every_contact_gated_module_has_a_production_entry_point_here() -> None:
    from tests.test_platform_trust import _CONTACT_GATED

    assert {m.removesuffix(".py") for m in _CONTACT_GATED} == set(_ENTRY_POINTS)


def _backup_store_in(project: Path, module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Place the backup store in *project*, so that project governs it (E2E-BACKUP-DAEMON-LAYOUT).

    The unconfigured default is now the daemon's shared store, which belongs to no project and so never
    uploads (lead ruling on W3's known issue 1). A backup case that must be governed by *project* says
    where the store is, as an operator relocating it would: through the single-store path the daemon and
    backup both honour (E2E-BACKUP-DAEMON-STORE-ONE-RESOLVER; MEMORY_STORAGE_PATH no longer relocates it).
    """
    if module == "sync/backup":
        monkeypatch.setenv("MEMORY_SINGLE_STORE_PATH", str(project / ".memory" / "default" / "memory.db"))


@pytest.mark.parametrize("module", sorted(_ENTRY_POINTS))
@pytest.mark.parametrize("where", ["project_subdir", "no_project"])
async def test_r1_no_payload_project_means_zero_contact(
    where: str, module: str, machine: Path, egress: list[str], pipeline_cls: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """r1: from ``<project>/src`` (enclosing project says false) or from no project, nothing is sent."""
    project = _project(machine / "project", "false")
    (project / "src").mkdir()
    cwd = project / "src" if where == "project_subdir" else machine / "bare"
    cwd.mkdir(exist_ok=True)
    _seed(project)
    if where == "project_subdir":
        _backup_store_in(project, module, monkeypatch)  # the zero then comes from the project saying no
    _run_from(cwd, monkeypatch)

    await _ENTRY_POINTS[module](machine, pipeline_cls, egress)

    assert egress == [], f"{module} contacted the platform from {where}: {egress}"


@pytest.mark.parametrize("module", sorted(_ENTRY_POINTS))
async def test_control_the_same_sender_from_a_contact_on_project_root_does_attempt(
    module: str, machine: Path, egress: list[str], pipeline_cls: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(machine / "project", "true")
    _seed(project)
    _backup_store_in(project, module, monkeypatch)
    _run_from(project, monkeypatch)

    await _ENTRY_POINTS[module](machine, pipeline_cls, egress)

    assert egress, f"control: {module} made no attempt with contact on, so the zero above proves nothing"


# --- r2: a config cached in project A never authorizes project B's payload --------------------

#: B's config, per arm: its switch off (consent on), or its consent off (switch on).
_B_POLICY = {
    "b_switch_off": ("false", _CONSENT),
    "b_consent_off": ("true", _NO_CONSENT),
}


def _a_cached_then_b(machine: Path, arm: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Load get_config() in A (contact and consent on), then run from B without refreshing it."""
    from trw_mcp.models.config import get_config

    a = _project(machine / "a", "true")
    contact, consent = _B_POLICY[arm]
    b = _project(machine / "b", contact, consent)
    _seed(b)
    _run_from(a, monkeypatch)
    assert get_config().learning_sharing_enabled is True  # A's config is the cached one
    _run_from(b, monkeypatch, reload=False)
    return a, b


@pytest.mark.parametrize("arm", sorted(_B_POLICY))
async def test_r2_publish_learnings_from_b_under_as_cached_config_sends_nothing(
    arm: str, machine: Path, egress: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The r2 repro: A's cached permission published B's learning. Now B's own policy decides."""
    from trw_mcp.telemetry.publisher import publish_learnings

    _a_cached_then_b(machine, arm, monkeypatch)

    publish_learnings(force=True)

    assert egress == [], f"B's learning left under A's cached {arm.removeprefix('b_')}: {egress}"


async def _b_push(_a: Path, b: Path, _pipeline: Any) -> None:
    from trw_mcp.models.config import get_config

    client = BackendSyncClient(config=get_config(), trw_dir=b / ".trw")  # A's config, B's payload store
    await client._pusher.push_learnings([_PushEntry()])  # type: ignore[list-item]
    await client._pusher.push_outcomes([{"outcome": "b"}])


async def _b_backup(a: Path, b: Path, _pipeline: Any) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.server._subcommands_backup import _build_uploader

    archive = a / "backup.tar.zst"
    archive.write_bytes(b"archive bytes")
    await _build_uploader(get_config(), client_id="c1", source_trw_dir=b / ".trw").upload(archive)


async def _b_pipeline(a: Path, b: Path, pipeline: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    instance = pipeline()
    instance._writer = MagicMock()
    instance.enqueue({"tool": "produced in b"})  # stamped with B's .trw
    _run_from(a, monkeypatch, reload=False)  # the flush runs later, back in A
    instance.flush_now()


_B_PAYLOAD_SENDERS = {
    "sync/push": _b_push,
    "sync/backup": _b_backup,
    "telemetry/sender": lambda _a, _b, _p: _entry_telemetry_sender(_a, _p, []),
    "telemetry/publisher": lambda _a, _b, _p: _entry_publisher(_a, _p, []),
}


@pytest.mark.parametrize("arm", sorted(_B_POLICY))
@pytest.mark.parametrize("sender", [*sorted(_B_PAYLOAD_SENDERS), "telemetry/pipeline"])
async def test_config_from_a_payload_from_b_obeys_bs_switch_and_consent(
    sender: str, arm: str, machine: Path, egress: list[str], pipeline_cls: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _a_cached_then_b(machine, arm, monkeypatch)

    if sender == "telemetry/pipeline":
        await _b_pipeline(a, b, pipeline_cls, monkeypatch)
    else:
        await _B_PAYLOAD_SENDERS[sender](a, b, pipeline_cls)

    assert egress == [], f"{sender}: B's payload left under A's config ({arm}): {egress}"


async def test_control_config_from_a_payload_from_b_sends_when_b_allows(
    machine: Path, egress: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without it the zeros above could come from a broken harness rather than B's policy."""
    from trw_mcp.telemetry.publisher import publish_learnings

    a = _project(machine / "a", "true")
    b = _project(machine / "b", "true")
    _seed(b)
    _run_from(a, monkeypatch)
    _run_from(b, monkeypatch, reload=False)

    publish_learnings(force=True)

    assert egress, "control: B allows, so B's learning should have been attempted"


# --- the pipeline: each event goes out under its own project's policy, or not at all ------------


def test_a_mixed_pipeline_batch_sends_each_source_under_its_own_policy_and_drops_the_unstamped(
    machine: Path, pipeline_cls: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    a = _project(machine / "a", "true")
    b = _project(machine / "b", "false")
    _run_from(a, monkeypatch)
    sent: list[tuple[Path, list[object]]] = []

    def record(_self: Any, events: list[dict[str, object]], _urls: Any, _key: Any, *, roots: tuple[Path, ...]) -> bool:
        sent.append((roots[0], [e["tool"] for e in events]))  # the origin stamp leads; the buffer's owner follows
        return True

    monkeypatch.setattr(pipeline_cls, "_send_batch", record)
    instance = pipeline_cls()
    instance._writer = MagicMock()
    instance.enqueue({"tool": "a-1"})
    _run_from(b, monkeypatch, reload=False)
    instance.enqueue({"tool": "b-1"})
    _run_from(a, monkeypatch, reload=False)
    instance.enqueue({"tool": "a-2"})
    instance._queue.append({"tool": "unstamped"})  # no source: never sent under anyone's policy

    result = instance.flush_now()

    assert sent == [(a / ".trw", ["a-1", "a-2"])]
    assert result["sent"] == 2 and result["failed"] == 1  # the unstamped one is dropped, never sent
    buffered = [call.args[1] for call in instance._writer.append_jsonl.call_args_list]
    assert buffered and all("_trw_source_trw_dir" not in event for event in buffered)  # the stamp never leaves


# --- a fresh install makes no platform contact -------------------------------------------------


@pytest.mark.parametrize("module", sorted(_ENTRY_POINTS))
async def test_a_fresh_install_makes_zero_platform_contacts(
    module: str, tmp_path: Path, egress: list[str], pipeline_cls: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shipped defaults: no platform URL, no key, every consent flag off -- nothing leaves the box."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("TRW_PROJECT_ROOT", "TRW_PLATFORM_CONTACT_ENABLED", "TRW_PLATFORM_API_KEY", "TRW_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    project = tmp_path / "fresh"
    (project / ".trw").mkdir(parents=True)
    _seed(project)
    _run_from(project, monkeypatch)

    entry = _ENTRY_POINTS[module]
    try:
        await entry(tmp_path, pipeline_cls, egress)
    finally:
        _reset_config(TRWConfig())

    assert egress == [], f"a fresh install's {module} contacted the platform: {egress}"


def test_the_shipped_defaults_grant_no_platform_contact() -> None:
    """Default-off, asserted on the declared defaults of both packages and the written config template."""
    from trw_memory.models.config import MemoryConfig

    from trw_mcp.bootstrap._config_templates import _default_config

    mcp = TRWConfig.model_fields
    assert {k: mcp[k].default for k in ("learning_sharing_enabled", "platform_telemetry_enabled")} == dict.fromkeys(
        ("learning_sharing_enabled", "platform_telemetry_enabled"), False
    )
    assert (mcp["backup_remote_enabled"].default, mcp["team_sync_enabled"].default) == (False, False)
    assert (mcp["platform_url"].default, mcp["backend_url"].default, mcp["backend_api_key"].default) == ("", "", "")
    assert mcp["platform_urls"].default_factory() == []  # type: ignore[call-arg,misc]
    assert mcp["platform_api_key"].default.get_secret_value() == ""
    memory = MemoryConfig.model_fields
    assert (memory["sync_enabled"].default, memory["platform_url"].default, memory["platform_api_key"].default) == (
        False,
        "",
        "",
    )
    _reset_config(TRWConfig())
    written = _default_config()
    assert not [
        line
        for line in written.splitlines()
        if line.split(":")[0].strip() in {"learning_sharing_enabled", "platform_telemetry_enabled", "platform_urls"}
    ], "the installed config.yaml opts in"
