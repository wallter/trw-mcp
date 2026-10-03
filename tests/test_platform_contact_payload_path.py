"""A send's policy root is derived from its payload's own resolved file path (MRD S2/S3 review r3).

Review r3 found two sends whose payload file and policy root were resolved separately: an absolute
``telemetry_file`` put the queue in project B while ``BatchSender.from_config`` gated it on the
current project A, and ``MEMORY_STORAGE_PATH`` put the backed-up store in B while ``backup create``
gated it on ``MemoryConfig().source_trw_dir`` (A). The class fix: every file-payload sender takes its
policy root from ``payload_trw_dir(<the payload file>)``, carried with the payload. The census below
holds every gate root in ``trw_mcp`` to that rule, or to a named, reasoned allowlist.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.telemetry.sender import BatchSender, stamp_consent

from ._telemetry_pipeline_support import pipeline_cls  # noqa: F401

BACKEND_URL = "https://api.trwframework.com"
_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
_CONSENT = "learning_sharing_enabled: true\nplatform_telemetry_enabled: true\nbackup_remote_enabled: true\n"


def _project(root: Path, contact: bool) -> Path:
    (root / ".trw").mkdir(parents=True, exist_ok=True)
    (root / ".trw" / "config.yaml").write_text(
        _CONSENT + f"platform_contact_enabled: {str(contact).lower()}\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def requests_sent(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Every request any httpx client (sync or async) makes, answered by a mock presign/PUT/telemetry backend."""
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path == "/v1/backup/presign":
            return httpx.Response(200, json={"url": "https://bucket.s3.amazonaws.com/k?sig=1", "key": "k"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handle)
    real_sync, real_async = httpx.Client, httpx.AsyncClient

    def sync_client(*args: object, **kwargs: object) -> httpx.Client:
        return real_sync(*args, **{**kwargs, "transport": transport})  # type: ignore[arg-type]

    def async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return real_async(*args, **{**kwargs, "transport": transport})  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "Client", sync_client)
    monkeypatch.setattr(httpx, "AsyncClient", async_client)
    return sent


@pytest.fixture
def two_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A clean machine (empty HOME, no contact/project env) under *tmp_path*."""
    (tmp_path / "home" / ".trw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("TRW_PROJECT_ROOT", "TRW_PLATFORM_CONTACT_ENABLED", "MEMORY_STORAGE_PATH", "TRW_PLATFORM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path
    _reset_config(TRWConfig())


def _run_from(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests import _path_isolation

    monkeypatch.chdir(cwd)
    _path_isolation.set_current_root(cwd)


# --- the helper --------------------------------------------------------------------------------


def test_payload_trw_dir_finds_the_owning_trw_inside_beside_and_through_links(tmp_path: Path) -> None:
    from trw_mcp.state._platform_trust import payload_trw_dir

    a = _project(tmp_path / "a", True)
    b = _project(tmp_path / "b", True)
    (a / ".trw" / "logs").mkdir()
    (b / ".trw" / "logs").mkdir()
    (a / ".trw" / "logs" / "linked.jsonl").symlink_to(b / ".trw" / "logs" / "q.jsonl")

    assert payload_trw_dir(a / ".trw" / "logs" / "tool-telemetry.jsonl") == (a / ".trw").resolve()  # a queue inside
    assert payload_trw_dir(a / ".memory" / "default" / "memory.db") == (a / ".trw").resolve()  # MRD S1's store beside
    assert payload_trw_dir(a / ".trw" / "logs" / "linked.jsonl") == (b / ".trw").resolve()  # the link's target owns it
    assert payload_trw_dir(a / ".trw" / "logs" / ".." / ".." / ".." / "b" / "x.db") == (b / ".trw").resolve()


def test_payload_trw_dir_is_none_outside_any_project_and_for_the_machine_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state._platform_trust import payload_trw_dir

    home = tmp_path / "home"
    (home / ".trw" / "logs").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / "loose").mkdir()

    assert payload_trw_dir(tmp_path / "loose" / "q.jsonl") is None
    assert payload_trw_dir(home / ".trw" / "logs" / "q.jsonl") is None  # HOME's .trw is the machine tier
    assert payload_trw_dir(home / "notes" / "memory.db") is None


# --- r3 P0 #1: an absolute telemetry_file puts the queue in B; B's policy governs it -------------


def _queue_in(project: Path, name: str = "q.jsonl") -> Path:
    queue = project / ".trw" / "logs" / name
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(json.dumps(stamp_consent({"event": "e"}, consented=True)) + "\n", encoding="utf-8")
    return queue


def _send_from_a(two_projects: Path, monkeypatch: pytest.MonkeyPatch, telemetry_file: str) -> dict[str, object]:
    _run_from(two_projects / "a", monkeypatch)
    _reset_config(
        TRWConfig(platform_urls=[BACKEND_URL], platform_telemetry_enabled=True, telemetry_file=telemetry_file)
    )
    sender = BatchSender.from_config()
    sender._max_retries = 1
    return dict(sender.send())


@pytest.mark.parametrize(("b_contact", "posts"), [(False, 0), (True, 1)], ids=["b_denies", "control_b_allows"])
def test_an_absolute_telemetry_file_in_b_is_sent_under_bs_policy_not_the_cwds(
    b_contact: bool, posts: int, two_projects: Path, requests_sent: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(two_projects / "a", True)
    queue = _queue_in(_project(two_projects / "b", b_contact))

    result = _send_from_a(two_projects, monkeypatch, str(queue))

    assert len(requests_sent) == posts, result
    if not b_contact:
        assert result["skipped_reason"] == "platform_contact_disabled"


def test_a_queue_reached_through_a_link_from_a_is_sent_under_its_targets_policy(
    two_projects: Path, requests_sent: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(two_projects / "a", True)
    target = _queue_in(_project(two_projects / "b", False))
    link = two_projects / "a" / ".trw" / "logs" / "linked.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)

    result = _send_from_a(two_projects, monkeypatch, "linked.jsonl")  # relative: A/.trw/logs/linked.jsonl

    assert requests_sent == [], result


def test_a_queue_outside_any_trw_is_never_sent(
    two_projects: Path, requests_sent: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(two_projects / "a", True)
    queue = two_projects / "loose" / "q.jsonl"
    queue.parent.mkdir()
    queue.write_text(json.dumps(stamp_consent({"event": "e"}, consented=True)) + "\n", encoding="utf-8")

    result = _send_from_a(two_projects, monkeypatch, str(queue))

    assert requests_sent == []
    assert result["skipped_reason"] == "no_source_project"


# --- r3 P0 #2: the store is in B (--db names it; MEMORY_STORAGE_PATH no longer relocates it, see
# E2E-BACKUP-DAEMON-STORE-ONE-RESOLVER); the archived db's .trw governs it ----


def _store_in(project: Path) -> Path:
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    db = project / ".memory" / "default" / "memory.db"
    db.parent.mkdir(parents=True)
    SQLiteBackend(db).close()
    return db


@pytest.mark.parametrize(
    ("a_contact", "b_contact", "posts"),
    [(True, False, 0), (False, True, 0), (True, True, 2)],
    ids=["a_allows_b_denies", "a_denies_b_allows", "control_both_allow"],
)
def test_backup_create_of_a_relocated_store_is_governed_by_the_stores_project(
    a_contact: bool,
    b_contact: bool,
    posts: int,
    two_projects: Path,
    requests_sent: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The r3 repro: from A, ``--db`` names B's store. Both A (the invoking project) and B
    (the store's owner) must allow it: a relocation can only restrict, never substitute (classfix r1)."""
    from trw_memory.daemon import DiscoveryAbsent

    from trw_mcp.server import _subcommands_backup

    a = _project(two_projects / "a", a_contact)
    b = _project(two_projects / "b", b_contact)
    store = _store_in(b)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(b))
    monkeypatch.setattr("trw_memory.cli_client.read_live_discovery", lambda _p: DiscoveryAbsent(reason="no record"))
    config = TRWConfig(backend_url=BACKEND_URL, platform_api_key="k", backup_remote_enabled=True)
    monkeypatch.setattr(_subcommands_backup, "_load_config", lambda: config)
    _run_from(a, monkeypatch)

    _subcommands_backup.run_backup(argparse.Namespace(backup_command="create", namespace="default", db=str(store)))

    assert len(requests_sent) == posts, capsys.readouterr()


def test_backup_create_of_a_db_outside_any_project_stays_local(
    two_projects: Path,
    requests_sent: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from trw_memory.daemon import DiscoveryAbsent
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    from trw_mcp.server import _subcommands_backup

    a = _project(two_projects / "a", True)
    db = two_projects / "loose" / "memory.db"
    db.parent.mkdir()
    SQLiteBackend(db).close()
    monkeypatch.setattr("trw_memory.cli_client.read_live_discovery", lambda _p: DiscoveryAbsent(reason="no record"))
    config = TRWConfig(backend_url=BACKEND_URL, platform_api_key="k", backup_remote_enabled=True)
    monkeypatch.setattr(_subcommands_backup, "_load_config", lambda: config)
    _run_from(a, monkeypatch)

    _subcommands_backup.run_backup(argparse.Namespace(backup_command="create", namespace="default", db=str(db)))

    assert requests_sent == []
    assert "must both allow contact and remote backup" in capsys.readouterr().out


def test_backup_restore_latest_into_a_db_outside_any_project_fetches_nothing(
    two_projects: Path, requests_sent: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.server import _subcommands_backup

    _run_from(_project(two_projects / "a", True), monkeypatch)
    config = TRWConfig(backend_url=BACKEND_URL, platform_api_key="k", backup_remote_enabled=True)
    monkeypatch.setattr(_subcommands_backup, "_load_config", lambda: config)
    db = two_projects / "loose" / "memory.db"
    args = argparse.Namespace(backup_command="restore", namespace="default", db=str(db), restore_from="latest")

    with pytest.raises(SystemExit) as exited:
        _subcommands_backup.run_backup(args)

    assert (exited.value.code, requests_sent) == (1, [])


# --- classfix r1: a payload tied to two projects needs BOTH to allow it (AND over all roots) ------


def _link_logs(a: Path, b: Path) -> None:
    """A's ``.trw/logs`` is a link to B's: A's buffered events land in a file B owns."""
    (b / ".trw" / "logs").mkdir(parents=True, exist_ok=True)
    (a / ".trw" / "logs").symlink_to(b / ".trw" / "logs", target_is_directory=True)


@pytest.mark.parametrize(
    ("a_contact", "b_contact", "linked", "posts"),
    [(False, True, True, 0), (True, False, True, 0), (True, True, False, 1), (True, True, True, 1)],
    ids=["a_denies_b_allows_linked", "a_allows_b_denies_linked", "control_a_allows_unlinked", "control_both_linked"],
)
def test_a_pipeline_event_needs_its_origin_and_its_buffers_owner_to_allow_it(
    a_contact: bool,
    b_contact: bool,
    linked: bool,
    posts: int,
    two_projects: Path,
    requests_sent: list[httpx.Request],
    pipeline_cls: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The classfix r1 repro: the event's stamp (A) was REPLACED by its buffer's owner (B)."""
    a = _project(two_projects / "a", a_contact)
    b = _project(two_projects / "b", b_contact)
    if linked:
        _link_logs(a, b)
    _run_from(a, monkeypatch)
    _reset_config(TRWConfig(platform_urls=[BACKEND_URL], platform_telemetry_enabled=True))
    instance = pipeline_cls()
    instance._max_retries = 1

    instance.enqueue({"tool": "emitted in a"})
    result = instance.flush_now()

    assert len(requests_sent) == posts, result


@pytest.mark.parametrize(("a_contact", "b_contact"), [(False, True), (True, False)], ids=["a_denies", "b_denies"])
def test_published_learnings_need_the_project_and_the_entries_owner_to_allow_them(
    a_contact: bool,
    b_contact: bool,
    two_projects: Path,
    requests_sent: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests._test_telemetry_publisher_support import _make_learning, _write_learning
    from trw_mcp.telemetry.publisher import publish_learnings

    a = _project(two_projects / "a", a_contact)
    b = _project(two_projects / "b", b_contact)
    _write_learning(b / ".trw" / "learnings" / "entries", "l.yaml", _make_learning(impact=0.9))
    (a / ".trw" / "learnings").symlink_to(b / ".trw" / "learnings", target_is_directory=True)
    _run_from(a, monkeypatch)
    _reset_config(TRWConfig(platform_urls=[BACKEND_URL], learning_sharing_enabled=True))

    result = publish_learnings(force=True)

    assert requests_sent == [], result


def test_control_published_learnings_through_a_link_send_when_both_allow(
    two_projects: Path, requests_sent: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests._test_telemetry_publisher_support import _make_learning, _write_learning
    from trw_mcp.telemetry.publisher import publish_learnings

    a = _project(two_projects / "a", True)
    b = _project(two_projects / "b", True)
    _write_learning(b / ".trw" / "learnings" / "entries", "l.yaml", _make_learning(impact=0.9))
    (a / ".trw" / "learnings").symlink_to(b / ".trw" / "learnings", target_is_directory=True)
    _run_from(a, monkeypatch)
    _reset_config(TRWConfig(platform_urls=[BACKEND_URL], learning_sharing_enabled=True))

    result = publish_learnings(force=True)

    assert len(requests_sent) == 1, result


_FLAGS = ("contact", "learning_sharing", "platform_telemetry", "backup_remote")


@settings(max_examples=200, deadline=None)
@given(st.lists(st.one_of(st.none(), st.tuples(*[st.booleans()] * len(_FLAGS))), max_size=5))
def test_send_policy_all_is_never_more_permissive_than_any_single_root(roots: list[tuple[bool, ...] | None]) -> None:
    """Q3c: the AND over roots grants a flag only if EVERY root grants it; none, or an empty set, grants nothing."""
    from unittest.mock import patch

    from trw_mcp.state import _platform_trust as trust

    policies = {Path(f"/root-{i}"): trust.SendPolicy(*flags) for i, flags in enumerate(roots) if flags is not None}
    paths = [Path(f"/root-{i}") if flags is not None else None for i, flags in enumerate(roots)]
    with patch.object(
        trust, "send_policy", lambda root: policies.get(root, trust.SendPolicy()) if root else trust.SendPolicy()
    ):
        combined = trust.send_policy_all(paths)

    for flag in _FLAGS:
        each = [getattr(policies[p], flag) if p is not None else False for p in paths]
        assert getattr(combined, flag) == (bool(each) and all(each))


# --- census: every gate root is derived from its payload's path, or named here with its reason ---
#
# Three rules, each keyed by (module, qualname): (1) every gate root and sender ``source_trw_dir=``/
# ``trw_dir=`` is ``payload_trw_dir(...)`` or carried from one (a parameter, an attribute, an assigned
# name, a tuple/subscript of those); (2) a function that RECEIVES a root (a ``source_trw_dir``/``trw_dir``
# parameter) never gates on a root without it -- a carried origin may be intersected, never replaced
# (classfix r1); (3) roots of more than one origin are combined only through ``send_policy_all``, where
# an extra, non-payload root is allowed because an AND can only restrict.

_GATES = {"platform_contact_enabled", "send_policy", "send_policy_all"}
_AND = "send_policy_all"
#: Sender constructors whose payload root arrives as ``trw_dir=`` rather than ``source_trw_dir=``.
_TRW_DIR_SENDERS = {"SyncPuller", "BackendSyncClient"}
#: A parameter that carries a payload's root into a function (rule 2).
_CARRIED = {"source_trw_dir", "trw_dir"}
#: Calls that pass a root through unchanged: ``Path(root)``, ``str(root)``.
_CARRIERS = {"Path", "str"}

#: (module, qualname) -> why this root is not a payload file's ``.trw``. A new entry is a reviewed diff.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("server/_boot_deferred.py", "_resolve_backend_sync"): (
        "sync: the pushed rows are selected from the user store BY this trw_dir's namespace pin "
        "(state._store_selection.selected_store(trw_dir)); the selector and the policy are one value, and "
        "the store file itself lives in the user tier, not in any project"
    ),
    ("server/_subcommands_sync.py", "_client"): (
        "sync push/pull/status: the same rows-selected-by-trw_dir payload as the sync loop (INC-145)"
    ),
    ("clients/llm.py", "_contact_allowed"): (
        "LLM prompt: the payload is agent-typed text with no source file, not a project's rows; the veto asks "
        "the switch of the project the client runs in, and only for a non-loopback host (EGRESS-STRAGGLER-CENSUS)"
    ),
    ("tools/submit_feedback.py", "_send_recorded"): (
        "feedback: the payload is agent-typed text (a fresh submit, or its own project's outbox record on "
        "flush), not a file; the caller's project is resolved once"
    ),
}


def _callee(node: ast.Call) -> str:
    return node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")


def _gate_aliases(tree: ast.AST) -> set[str]:
    names = set(_GATES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {a.asname for a in node.names if a.name in _GATES and a.asname}
    return names


def _own_nodes(func: ast.AST) -> Iterator[ast.AST]:
    """*func*'s nodes, not descending into nested function or class definitions (they are visited on their own)."""
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


def _roots(func: ast.AST, gates: set[str]) -> list[tuple[bool, ast.expr]]:
    """``(anded, root)`` for every gate call's root and every sender ``source_trw_dir=``/``trw_dir=`` in *func*."""
    roots: list[tuple[bool, ast.expr]] = []
    for node in _own_nodes(func):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node)
        if name in gates and node.args:
            roots.append((name == _AND, node.args[0]))
        wanted = {"source_trw_dir", "trw_dir"} if name in _TRW_DIR_SENDERS else {"source_trw_dir"}
        roots.extend((False, k.value) for k in node.keywords if k.arg in wanted)
    return roots


class _Func:
    """One function's parameters and local bindings, and the origin of any expression in it."""

    def __init__(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        a = func.args
        self.params = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
        self.assigned: dict[str, list[ast.expr]] = {}
        for node in _own_nodes(func):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        self.assigned.setdefault(target.id, []).append(node.value)
            elif isinstance(node, ast.NamedExpr):
                self.assigned.setdefault(node.target.id, []).append(node.value)
            elif isinstance(node, ast.comprehension) and isinstance(node.target, ast.Name):
                self.assigned.setdefault(node.target.id, []).append(node.iter)

    def origins(self, expr: ast.expr, seen: frozenset[str] = frozenset()) -> set[str]:
        """Where *expr*'s root comes from: parameter names, ``payload_trw_dir``, ``None``, or ``?<call>``."""
        if isinstance(expr, ast.Call):
            name = _callee(expr)
            if name == "payload_trw_dir":
                return {"payload_trw_dir"}
            if name in _CARRIERS and len(expr.args) == 1 and not expr.keywords:
                return self.origins(expr.args[0], seen)
            return {f"?{name or ast.unparse(expr.func)}"}
        if isinstance(expr, ast.Constant) and expr.value is None:
            return {"None"}
        if isinstance(expr, ast.Name):
            if expr.id in self.assigned and expr.id not in seen:
                return set().union(*(self.origins(v, seen | {expr.id}) for v in self.assigned[expr.id]))
            return {expr.id} if expr.id in self.params else {f"?{expr.id}"}
        if isinstance(expr, (ast.Attribute, ast.Subscript, ast.NamedExpr)):
            return self.origins(expr.value, seen)
        if isinstance(expr, (ast.Tuple, ast.List)):
            return set().union(*(self.origins(e, seen) for e in expr.elts))
        return {f"?{ast.unparse(expr)}"}


def _violations(tree: ast.AST, module: str, allowlist: dict[tuple[str, str], str]) -> list[str]:
    gates = _gate_aliases(tree)
    found: list[str] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                if (module, qualname) not in allowlist:
                    found.extend(f"{module}::{qualname} {why}" for why in check(child))
                visit(child, f"{qualname}.")
            else:
                visit(child, prefix)

    def check(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        scope = _Func(func)
        carried = scope.params & _CARRIED
        problems: list[str] = []
        single: set[frozenset[str]] = set()
        for anded, root in _roots(func, gates):
            origins = scope.origins(root)
            unknown = {o for o in origins if o.startswith("?")}
            # rule 1; inside the AND an extra root only restricts, but one element must be the payload's own
            if unknown and not (anded and origins - unknown - {"None"}):
                problems.append(f"roots a send in {ast.unparse(root)}")
            if carried and not carried & origins:  # rule 2
                problems.append(f"replaces its carried root {sorted(carried)} with {ast.unparse(root)}")
            if not anded:
                single.add(frozenset(origins))
        if len(single) > 1 and not any(anded for anded, _ in _roots(func, gates)):  # rule 3
            problems.append(f"combines roots {sorted(map(sorted, single))} outside {_AND}")
        return problems[:1]

    visit(tree, "")
    return found


def test_every_send_root_is_its_payloads_trw_or_a_named_exception() -> None:
    offenders = [
        v
        for path in sorted(_SRC.rglob("*.py"))
        for v in _violations(ast.parse(path.read_text(encoding="utf-8")), path.relative_to(_SRC).as_posix(), ALLOWLIST)
    ]
    assert not offenders, offenders


def test_every_allowlist_entry_still_names_a_real_exception() -> None:
    """A stale entry would silently pre-authorize a future violation at that qualname."""
    stale = []
    for module, qualname in ALLOWLIST:
        tree = ast.parse((_SRC / module).read_text(encoding="utf-8"))
        if not any(v.startswith(f"{module}::{qualname} ") for v in _violations(tree, module, {})):
            stale.append((module, qualname))
    assert not stale, stale


@pytest.mark.parametrize(
    "planted",
    [
        "def f():\n    platform_contact_enabled(resolve_trw_dir())\n",
        "def f():\n    root = resolve_project_root()\n    send_policy(root)\n",
        "def f():\n    Sender(source_trw_dir=get_config().trw_dir)\n",
        "def f():\n    platform_contact_enabled(Path.cwd() / '.trw')\n",
        "def f(args):\n    Up(source_trw_dir=MemoryConfig().source_trw_dir)\n",
        "def f():\n    BackendSyncClient(config=c, trw_dir=resolve_trw_dir())\n",
        "from x import send_policy as p\ndef f():\n    p(resolve_trw_dir())\n",
        "class C:\n    def m(self):\n        q = self.x\n        q = resolve_trw_dir()\n        send_policy(q)\n",
        "def f():\n    if (root := resolve_trw_dir()) is None:\n        return\n    send_policy(root)\n",
        # classfix r1: a carried stamp REPLACED by the buffer's owner (the 633ed4d2c _flush_group shape)
        "def f(source_trw_dir):\n    owner = payload_trw_dir(buf(source_trw_dir))\n    send_policy(owner)\n",
        # two origins gated one by one instead of through the AND
        "def f(source_trw_dir, q):\n    send_policy(source_trw_dir)\n    platform_contact_enabled(payload_trw_dir(q))\n",
        # an AND with no payload-derived element at all
        "def f():\n    send_policy_all((resolve_trw_dir(), None))\n",
    ],
)
def test_the_census_catches_a_planted_root(planted: str) -> None:
    assert _violations(ast.parse(planted), "planted.py", {})


@pytest.mark.parametrize(
    "clean",
    [
        "def f(path):\n    send_policy(payload_trw_dir(path))\n",
        "def f(self):\n    platform_contact_enabled(self._source_trw_dir)\n",
        "def f(queue):\n    root = payload_trw_dir(queue)\n    Sender(source_trw_dir=root)\n",
        "def f(source_trw_dir):\n    on(Path(source_trw_dir).parent)\n",
        "def f(q):\n    if (root := payload_trw_dir(q)) is None:\n        return\n    send_policy(root)\n",
        "def f(source_trw_dir, q):\n    roots = (source_trw_dir, payload_trw_dir(q))\n    send_policy_all(roots)\n",
        "def f(q):\n    send_policy_all((resolve_trw_dir(), payload_trw_dir(q)))\n",
        "def f(roots):\n    return all(platform_contact_enabled(r) for r in roots)\n",
    ],
)
def test_the_census_accepts_a_derived_or_carried_root(clean: str) -> None:
    assert _violations(ast.parse(clean), "clean.py", {}) == []
