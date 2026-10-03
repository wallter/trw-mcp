"""PRD-SEC-023 FR06 (chokepoint C4 and the census): session text does not leave while the mark is above ``team``, and no platform call site
escapes the label module unnoticed.

One session-text sink refuses while ``session_mark().level`` is above ``team``: feedback (``submit_feedback_via_http``, and nothing is queued
in the outbox either). The shared-recall query leg of ``trw_recall`` no longer exists (SHARED-RECALL-LOCAL); the library's
``merge_shared_results`` is the remaining shared-fetch caller and is covered below.

The census enumerates, from the AST of both packages' sources, every call of the two platform-header builders
(``trw_mcp.state._platform_trust.platform_auth_headers`` and ``trw_memory.sync._remote_common.build_platform_headers``). Each enclosing
function must be either

* GUARDED: the function itself, or every function in either tree that refers to it, calls the label module (one of ``GUARD_CALLS``), or
* EXCLUDED: listed with the reason it sends metadata only (or nothing off the host).

A call site that is neither fails by name, a listed entry that no longer exists fails as stale, and the mutation tests prove each guarded
entry fails by name once its label call is removed.
"""

from __future__ import annotations

import ast
import functools
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from trw_memory.labels import Level

from tests._layout import MONOREPO_ROOT, requires_monorepo
from trw_mcp.state._session_mark import session_mark

pytestmark = requires_monorepo
if MONOREPO_ROOT is None:
    # Reads monorepo-only files; the public package (and the release check's export of it) has none of them.
    pytest.skip("needs the monorepo checkout (public repo is the package alone)", allow_module_level=True)
_REPO = MONOREPO_ROOT
_ROOTS = (_REPO / "trw-mcp" / "src", _REPO / "trw-memory" / "src")
_BUILDERS = frozenset({"platform_auth_headers", "build_platform_headers"})

#: The label module's egress checks: ``LabelPolicy.admit`` (rows), ``session_egress_refusal`` (the session mark), and two thin ``admit``
#: wrappers with their own tests: ``split_by_label`` (the sync push) and ``labelled_row_count`` (every row of a store file, for the backup).
GUARD_CALLS = frozenset({"admit", "session_egress_refusal", "split_by_label", "labelled_row_count"})

Site = tuple[str, str]  # (path under a source root, dotted qualname of the enclosing function)


@dataclass(frozen=True)
class Guarded:
    """The label call lives in the site itself (``by`` empty) or in each function listed in ``by``, which must be every referrer."""

    by: tuple[Site, ...] = ()
    #: Referrers that send only rows a guarded caller already admitted, with the reason.
    admitted_upstream: Mapping[Site, str] | None = None


GUARDED: dict[Site, Guarded] = {
    ("trw_mcp/sync/push.py", "SyncPusher._post_learnings"): Guarded(
        by=(("trw_mcp/sync/push.py", "SyncPusher.push_learnings"),)
    ),
    ("trw_mcp/tools/submit_feedback.py", "submit_feedback_via_http"): Guarded(),
    ("trw_mcp/telemetry/publisher.py", "_post_learning"): Guarded(
        by=(("trw_mcp/telemetry/publisher.py", "publish_learnings"),)
    ),
    ("trw_mcp/sync/backup.py", "BackupUploader.upload"): Guarded(
        by=(("trw_mcp/server/_subcommands_backup.py", "_run_backup_create"),)
    ),
    ("trw_memory/sync/_remote_fetch.py", "fetch_shared_memories"): Guarded(
        by=(("trw_memory/_client_org_shared.py", "merge_shared_results"),)
    ),
    # The retry drain was listed as admitted upstream until SYNC-RETRY-LABEL-RECHECK: a row relabelled while its record waited was sent.
    # It now asks the label of the current row itself.
    ("trw_memory/sync/_remote_publish.py", "_publish_payload_result"): Guarded(
        by=(
            ("trw_memory/sync/_remote_publish.py", "publish_memory_result"),
            ("trw_memory/sync/_remote_publish.py", "_drain_retry_queue_with_ids.publish_payload"),
        ),
    ),
}

EXCLUDED: dict[Site, str] = {
    ("trw_mcp/sync/push.py", "SyncPusher.push_outcomes"): (
        "session-outcome metrics: run id, counts and learning ids (opaque), never a row's text; gated by platform_telemetry_enabled"
    ),
    (
        "trw_mcp/sync/pull.py",
        "SyncPuller.pull_intel_state",
    ): "inbound pull: sends the client id and the pull cursors only",
    (
        "trw_mcp/sync/backup.py",
        "BackupUploader.list_remote",
    ): "lists this client's own backups: sends the client id only",
    ("trw_mcp/sync/backup.py", "BackupUploader.download"): "downloads one of this client's backups: sends its key only",
    ("trw_mcp/telemetry/pipeline.py", "TelemetryPipeline._send_batch"): (
        "anonymous usage telemetry: tool names, timings and counts, gated by platform_telemetry_enabled"
    ),
    ("trw_mcp/telemetry/sender.py", "BatchSender._http_post"): (
        "anonymous usage telemetry batches, the same event shape as the pipeline"
    ),
    (
        "trw_memory/sync/_remote_publish.py",
        "retire_remote_memory",
    ): "marks a published row obsolete: sends its remote id only",
    (
        "trw_memory/sync/subscriber.py",
        "SSESubscriber._listen_loop",
    ): "inbound SSE stream: a GET with the last event id only",
    ("trw_memory/daemon/_direct.py", "post_tool"): "the local memory daemon on loopback, never off the host (FIX-157)",
}


# ── the census ───────────────────────────────────────────────────────────────


def _sources() -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for root in _ROOTS
        for path in sorted(root.rglob("*.py"))
    }


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _functions(tree: ast.AST) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every function with its dotted qualname (classes and enclosing functions included)."""

    def walk(node: ast.AST, prefix: str) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield f"{prefix}{child.name}", child
                yield from walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def _own_nodes(func: ast.AST) -> Iterator[ast.AST]:
    """The nodes of *func*'s own body, not those of functions or classes nested in it."""
    for child in ast.iter_child_nodes(func):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from _own_nodes(child)


def _calls(func: ast.AST, names: frozenset[str]) -> bool:
    return any(isinstance(node, ast.Call) and _name(node.func) in names for node in _own_nodes(func))


@functools.lru_cache(maxsize=4096)
def _file_index(path: str, text: str) -> tuple[dict[Site, ast.AST], frozenset[Site], dict[str, frozenset[Site]]]:
    """One file's part of :func:`_index`, cached on its text (a mutation re-parses only the file it changed)."""
    functions: dict[Site, ast.AST] = {}
    builder_sites: set[Site] = set()
    referrers: dict[str, set[Site]] = {}
    for qualname, func in _functions(ast.parse(text)):
        site = (path, qualname)
        functions[site] = func
        for node in _own_nodes(func):
            if isinstance(node, ast.Call) and _name(node.func) in _BUILDERS:
                builder_sites.add(site)
            if (name := _name(node)) is not None and isinstance(node, (ast.Name, ast.Attribute)):
                referrers.setdefault(name, set()).add(site)
    return functions, frozenset(builder_sites), {name: frozenset(sites) for name, sites in referrers.items()}


def _index(sources: Mapping[str, str]) -> tuple[dict[Site, ast.AST], set[Site], dict[str, set[Site]]]:
    """``(functions by site, sites calling a header builder, sites referring to each name)``."""
    functions: dict[Site, ast.AST] = {}
    builder_sites: set[Site] = set()
    referrers: dict[str, set[Site]] = {}
    for path, text in sources.items():
        file_functions, file_builders, file_referrers = _file_index(path, text)
        functions.update(file_functions)
        builder_sites |= file_builders
        for name, sites in file_referrers.items():
            referrers.setdefault(name, set()).update(sites)
    return functions, builder_sites, referrers


def census_failures(sources: Mapping[str, str]) -> list[str]:
    """Every census violation, each naming the function it is about; empty when every call site is accounted for."""
    functions, builder_sites, referrers = _index(sources)
    failures: list[str] = []
    for site in sorted(builder_sites):
        if site not in GUARDED and site not in EXCLUDED:
            failures.append(
                f"{site[1]} ({site[0]}) builds platform headers without a label guard or an exclusion entry"
            )
    for site in sorted(set(GUARDED) | set(EXCLUDED)):
        if site not in builder_sites:
            failures.append(
                f"{site[1]} ({site[0]}) is listed but no longer builds platform headers: remove the stale entry"
            )
    for site, guard in sorted(GUARDED.items()):
        if site not in builder_sites:
            continue
        if not guard.by:
            if not _calls(functions[site], GUARD_CALLS):
                failures.append(f"{site[1]} ({site[0]}) is listed as guarded but calls no label check")
            continue
        upstream = dict(guard.admitted_upstream or {})
        for caller in guard.by:
            if caller not in functions or not _calls(functions[caller], GUARD_CALLS):
                failures.append(f"{site[1]} ({site[0]}): its guard {caller[1]} ({caller[0]}) calls no label check")
        short = site[1].rsplit(".", 1)[-1]
        for referrer in sorted(referrers.get(short, set()) - {site} - set(guard.by) - set(upstream)):
            failures.append(
                f"{site[1]} ({site[0]}) is reached from {referrer[1]} ({referrer[0]}), which has no label guard"
            )
    return failures


def test_every_platform_call_site_is_guarded_or_excluded_for_metadata_only() -> None:
    assert census_failures(_sources()) == []


def test_every_exclusion_says_why_it_is_metadata_only() -> None:
    assert all(len(reason.split()) >= 5 for reason in EXCLUDED.values())
    assert all(len(reason.split()) >= 5 for g in GUARDED.values() for reason in (g.admitted_upstream or {}).values())


def test_a_new_unguarded_call_site_fails_by_name() -> None:
    sources = _sources()
    sources["trw_mcp/sync/new_sender.py"] = (
        "from trw_mcp.state._platform_trust import platform_auth_headers\n\n\n"
        "def send_everything(url, key):\n    return platform_auth_headers(url, key, source_trw_dir=None)\n"
    )

    failures = census_failures(sources)

    assert len(failures) == 1 and "send_everything (trw_mcp/sync/new_sender.py)" in failures[0]


def _guard_functions() -> list[tuple[Site, Site]]:
    """(call site, the function holding its label check) for every guarded entry."""
    return [(site, caller) for site, guard in GUARDED.items() for caller in (guard.by or (site,))]


@pytest.mark.parametrize(
    "site,holder", _guard_functions(), ids=lambda value: value[1] if isinstance(value, tuple) else str(value)
)
def test_removing_a_label_check_fails_the_census_naming_the_call_site(site: Site, holder: Site) -> None:
    """The mutation check: rename every label call inside the guard function, and the census must name the call site."""
    sources = _sources()
    func = dict(_index({holder[0]: sources[holder[0]]})[0])[holder]
    lines = sources[holder[0]].splitlines(keepends=True)
    start, end = func.lineno - 1, func.end_lineno or func.lineno  # type: ignore[attr-defined]
    body = "".join(lines[start:end])
    mutated = re.sub(r"\b(" + "|".join(sorted(GUARD_CALLS)) + r")\(", r"_unguarded_\1(", body)
    assert mutated != body, f"{holder[1]} holds no label call to remove"
    sources[holder[0]] = "".join(lines[:start]) + mutated + "".join(lines[end:])

    failures = census_failures(sources)

    assert failures and all(site[1] in failure for failure in failures), failures


# ── C4: session egress refuses while the mark is above team ─────────────────


@pytest.fixture
def raised_mark() -> None:
    session_mark().raise_to(Level.PERSONAL)


def _feedback_payload() -> dict[str, object]:
    return {"category": "bugfix", "subject": "s", "message": "the session's own words", "metadata": {}}


def test_feedback_is_sent_while_the_mark_is_team(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The control for the refusal below."""
    import httpx

    from trw_mcp.tools.submit_feedback import submit_feedback_via_http

    calls: list[httpx.Request] = []
    real = httpx.Client
    monkeypatch.setattr("trw_mcp.tools.submit_feedback.platform_contact_enabled", lambda _root: True)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **k: real(
            *a, **{**k, "transport": httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={}))}
        ),
    )

    result = submit_feedback_via_http(
        backend_url="https://api.trwframework.com", api_key="k", payload=_feedback_payload(), source_trw_dir=tmp_path
    )  # type: ignore[arg-type]

    assert result.success is True and len(calls) == 1


@pytest.mark.usefixtures("raised_mark")
def test_feedback_is_refused_with_the_label_and_the_remedy_while_the_mark_is_personal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    from trw_mcp.tools.submit_feedback import submit_feedback_via_http

    calls: list[httpx.Request] = []
    real = httpx.Client
    monkeypatch.setattr("trw_mcp.tools.submit_feedback.platform_contact_enabled", lambda _root: True)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **k: real(
            *a, **{**k, "transport": httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={}))}
        ),
    )

    result = submit_feedback_via_http(
        backend_url="https://api.trwframework.com", api_key="k", payload=_feedback_payload(), source_trw_dir=tmp_path
    )  # type: ignore[arg-type]

    assert calls == []
    assert result.success is False and "personal" in (result.error or "") and "new session" in (result.error or "")


@pytest.mark.usefixtures("raised_mark")
def test_feedback_written_while_the_mark_is_personal_is_not_even_queued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from trw_mcp.tools import submit_feedback as module

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    monkeypatch.setattr(module, "_backend", lambda: ("https://api.trwframework.com", "k"))
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    sent: list[object] = []
    monkeypatch.setattr(module, "submit_feedback_via_http", lambda **kw: sent.append(kw))

    result = module.submit_feedback(category="bugfix", subject="s", message="the session's own words")

    assert sent == [] and result.success is False
    assert not any(p.is_file() for p in trw_dir.rglob("*")), "no outbox record holds the session's text"


async def test_a_library_shared_recall_whose_local_hits_are_labelled_sends_no_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``MemoryClient`` has no trw-mcp session mark; the recall that surfaced a row above team does not send its query."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from trw_memory import _client_org_shared
    from trw_memory import client as client_module
    from trw_memory.models.memory import MemoryEntry

    asked: list[str] = []
    monkeypatch.setattr(
        client_module,
        "fetch_shared_memories",
        lambda query, *_a, **_k: asked.append(query) or SimpleNamespace(status="ok", fetched=0, refused=0, results=[]),
    )
    monkeypatch.setattr(_client_org_shared, "snapshot_cached_shared_results", lambda _c, _q: [])
    monkeypatch.setattr(_client_org_shared, "store_gate", lambda *_a: None)
    client = MagicMock()
    client._apply_pending_remote_retirements = AsyncMock()
    client._get_embedder.return_value = None

    team = [MemoryEntry(id="L-t", content="t", namespace="default")]
    personal = [MemoryEntry(id="L-p", content="p", namespace="user:alice")]
    await _client_org_shared.merge_shared_results(client, "team query", [], 5, local_entries=team)
    await _client_org_shared.merge_shared_results(client, "personal query", [], 5, local_entries=personal)

    assert asked == ["team query"]
