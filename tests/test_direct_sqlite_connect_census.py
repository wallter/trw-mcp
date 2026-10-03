"""PRD-QUAL-147 FR09 -- every direct ``sqlite3.connect``/``connect_registered`` call in trw-mcp is accounted for.

trw-mcp has no single "checked helper" funnel the way trw-memory's
``storage._connection.connect`` is (PRD-SEC-016's own census,
``trw-memory/tests/test_direct_sqlite_connect_census.py``): trw-mcp opens SQLite
directly for four unrelated stores -- the code index, the comms mailbox, the
delivery journal, and the checkout-supplied project ``memory.db`` -- each with
its own opener. Charter rule Q2 ("class before site") says this census lands
BEFORE any of those sites are fixed: it starts green by listing every current
direct connect as an audited exception, tagged by the store class it belongs
to. Later adopter slices (QUAL-147 FR06/FR07, B71-73, ...) delete their own
entries as they route sites through a shared opener (trw-memory's
``probe_store`` for the checkout store, a deadline-bearing comms opener for
FR10, etc.) -- they do not touch this file's mechanism.

Three prior counts disagreed on trw-mcp/src's direct-connect surface: the
intake brief said 27, backlog row B80-27 said 21, and a fresh grep on this
worktree's HEAD (2026-09-26) said 22. This census does not hard-code any of
them: it is an AST walk, keyed by ``(relative path, enclosing function
qualname, 1-based ordinal within that function)`` -- exactly trw-memory's
shape -- so a call site surviving an unrelated edit above it does not make its
audited entry look stale, and a genuinely new, unlisted call site fails the
test by name. As of 2026-09-26 the walk finds 22 sites, enumerated below with
one-line, class-tagged reasons (see the class-tag legend in the
``_AUDITED_EXCEPTIONS`` docstring); this note is a dated observation, not an
assertion the tests encode -- an adopter slice correctly deleting an entry as
it migrates a site must not have to update a pinned count here.

**Aliases resolved per lexical scope.** ``import sqlite3 as sql`` / ``sql.connect(...)``
and ``from sqlite3 import connect`` / bare ``connect(...)`` both reach
``sqlite3.connect`` and were invisible to a receiver-name-only check (sol r1
finding: no site at HEAD uses either form, but a future one could go
uncounted). ``_scope_imports`` reads each module, function and class scope's
own ``Import``/``ImportFrom`` nodes, and ``_ordered_sites`` resolves a call's
names the way Python does (own scope, enclosing function scopes, module; a
class body's imports are not visible to its methods), so an alias bound in one
function no longer counts a same-named call in another function (QUAL-147
FR09 follow-up). ``_is_direct_connect`` then matches
``<bound-module-name>.connect(...)`` and a bare call through a bound
``connect`` name, alongside the literal ``sqlite3``/``dbapi``/``pysqlite3``/
``*_dbapi`` heuristic and ``connect_registered(...)``.

**Not shared with trw-memory's census.** The two AST helpers below
(``_is_direct_connect`` / ``_ordered_sites``) are structurally identical to
trw-memory's. They are duplicated rather than imported because there is no
shared test-utility package between the two: trw-mcp's test suite already
depends on the trw-memory *library* (via the pinned floor in
``trw-mcp/pyproject.toml``) but not on trw-memory's *test tree*, which is not
installed by either package's build and is free to change shape between
releases without a compatibility contract. Importing across test trees would
create exactly that undeclared contract. A future PRD could extract a tiny
``trw_testutil`` seam if a third consumer appears; one duplication does not
justify it yet.

**Tracer monkeypatches, deliberately not an entry.** ``tests/support/_delivery_io_tracer.py``
(moved out of ``src/`` by PRD-CORE-313 FR02, so it is no longer under this census's root)
monkeypatches ``sqlite3.connect`` itself (``sqlite3.connect = _wrap_connect(original_connect)``)
so it can attribute commits to a crash boundary for the FR05 durable-write
census; it is not a data open; it holds no database. This walker's
``_is_direct_connect`` only matches an explicit ``X.connect(...)`` call whose
receiver looks like a DB-API module, or a bare ``connect_registered(...)``
call -- an assignment to ``sqlite3.connect`` and a call through the local name
``original`` (not ``sqlite3.connect(...)``) are neither, so the walk finds no
site there and no entry is needed. Named here, not silently filtered: a
regression test below pins that this file adds no new AST-visible site.
"""

from __future__ import annotations

import ast
from pathlib import Path

import trw_mcp

#: Class tags for the reasons below (PRD-QUAL-147 FR09 brief):
#:   checkout memory.db -- the pre-6.0 checkout-supplied project store; QUAL-147 FR06/FR07 routes
#:     these through trw-memory's probe_store instead of a raw read.
#:   comms.sqlite3 -- the per-formation comms mailbox (checkout-seedable); B71-73 (QUAL-147 FR10)
#:     adds a progress-handler deadline to its schema check.
#:   code index -- the repository code-search index the checkout's own build publishes and re-reads;
#:     not checkout-supplied content, already schema- and deadline-bounded (_bounded/_require_schema).
#:   delivery journal -- the local delivery-evidence journal trw-mcp itself writes and reads; not
#:     checkout-supplied content.
#:   migrate _exclusive/_snapshot -- locking and backup copies inside memory migrate, explicitly a
#:     Non-Goal of QUAL-147 ("these are locking and backup, not schema reads").
#:
#: (relative path from trw-mcp/src/trw_mcp, enclosing function qualname, 1-based ordinal of the call
#: within that function) -> one-line reason, class-tagged. Every entry here is a REPORTED residual at
#: the class-before-site stage, not a verified-safe exception; a later adopter slice deletes its own
#: entry as it routes that site through a shared opener.
_AUDITED_EXCEPTIONS: dict[tuple[str, str, int], str] = {
    ("code_index/store.py", "_expected_schema", 1): (
        "code index: an ephemeral :memory: db used only to derive the expected DDL string for "
        "_require_schema's comparison; never touches the checkout's on-disk index."
    ),
    ("code_index/store.py", "build_chunk_store", 1): (
        "code index: creates the index db trw-mcp's own build writes at a pid/uuid-scoped temp path, "
        "then publishes it atomically with os.replace; content is chunked from the checkout's source "
        "tree, not read back from an existing store."
    ),
    ("code_index/store.py", "open_store", 1): (
        "code index: opens the published index read-only (mode=ro); every read runs under _bounded's "
        "deadline and length limit and _require_schema's allowlist before the first data read (rc5 C12)."
    ),
    ("server/_backup_remote_scope.py", "namespace_census", 1): (
        "served store: a read-only COUNT(*) GROUP BY namespace on the store `backup create` just archived (daemon "
        "stopped), through connect_registered; counts only, decides whether the remote upload is refused."
    ),
    ("server/_backup_remote_scope.py", "labelled_row_count", 1): (
        "served store: a read-only (mode=ro) scan of namespace, tags and metadata on the store `backup create` just "
        "archived (daemon stopped), through connect_registered, in 1000-row batches; returns a count of rows labelled "
        "above team (PRD-SEC-023 FR05), never a row."
    ),
    ("comms/_bootstrap.py", "_lead_pending", 1): (
        "comms.sqlite3: B71-73/FR10 -- a read-only pending-count query against the checkout-seedable "
        "mailbox, with no deadline on the surrounding schema check today."
    ),
    ("comms/_hint.py", "pending_hint", 1): (
        "comms.sqlite3: B71-73/FR10 -- a read-only poller hint query against the mailbox; same "
        "deadline gap as _lead_pending."
    ),
    ("comms/_schema.py", "_expected_ddl", 1): (
        "comms.sqlite3: an ephemeral :memory: db used only to derive the expected DDL for verify()'s "
        "comparison; not a read of the checkout-supplied mailbox itself, but lives in the module B71-73/"
        "FR10 adds the deadline to."
    ),
    ("comms/_store.py", "_publish_new", 1): (
        "comms.sqlite3: the mailbox's own first-publication path (creates a fresh staging file, never "
        "an existing checkout-supplied one); B71-73/FR10's deadline lands in this same module."
    ),
    ("comms/_store.py", "connect", 1): (
        "comms.sqlite3: the canonical read-write opener every comms tool call goes through "
        "(trw_send/trw_inbox); B71-73/FR10 adds the progress-handler deadline here before "
        "verify()'s first read."
    ),
    ("comms/_upgrade.py", "_exclusive", 1): (
        "comms.sqlite3: the schema-upgrade path's exclusive-lock connection on the checkout-supplied "
        "mailbox; same store class as the B71-73/FR10 deadline gap."
    ),
    ("comms/_upgrade.py", "_verified_backup", 1): (
        "comms.sqlite3: opens the just-created backup COPY read-only to verify it, not the live "
        "checkout-supplied mailbox (the live connection is held by the caller's _exclusive block)."
    ),
    ("comms/_upgrade.py", "rollback", 1): (
        "comms.sqlite3: opens the pre-upgrade backup read-only to restore it during rollback; the "
        "backup was fsynced and sha256-verified by this same module before being trusted."
    ),
    ("comms/_watch.py", "observe", 1): (
        "comms.sqlite3: B71-73/FR10 -- a read-only tailer poll against the checkout-seedable mailbox; "
        "same deadline gap as _lead_pending."
    ),
    ("formation/_stall.py", "stall_scan", 1): (
        "comms.sqlite3: B71-73/FR10 -- a read-only unread-mail scan against the checkout-seedable "
        "mailbox, same deadline gap."
    ),
    ("state/_store_migration.py", "_exclusive", 1): (
        "migrate _exclusive/_snapshot: locking/backup, non-goal -- takes the project store's exclusive "
        "lock for migrate/restore; reads no memories rows."
    ),
    ("state/_store_migration.py", "_snapshot", 1): (
        "migrate _exclusive/_snapshot: locking/backup, non-goal -- opens a fresh backup COPY target "
        "that does not exist yet; conn.backup() writes into it, it is never read as untrusted content."
    ),
    ("state/_store_migration.py", "preview_migration", 1): (
        "checkout memory.db: QUAL-147 FR07 -- opens the live checkout-supplied project store "
        "read-only (a Path.as_uri URI, no immutable=1: it is live) only to back it up into a private "
        "scratch copy; _source_rows probes that copy with probe_store before any backend reads it."
    ),
    ("state/_store_migration.py", "apply_migration", 1): (
        "migrate _exclusive/_snapshot: locking/backup, non-goal -- an empty :memory: db backed into "
        "the held project-store connection to empty it after a successful migration; not a read of "
        "checkout content."
    ),
    ("state/_store_migration.py", "_stamp_origin_project", 1): (
        "checkout memory.db: UF-PRD-22 -- opens the private working COPY that _source_rows already probed with "
        "probe_store (never the live checkout store, never the user store) to stamp origin_project on the rows "
        "being migrated; the daemon, not this open, reads the result."
    ),
    ("state/_store_migration.py", "_swap", 1): (
        "migrate _exclusive/_snapshot: locking/backup, non-goal -- CLI restore's swap step opens the "
        "restore source (a file trw-mcp itself produced) to back it into the held project store."
    ),
    ("tools/_delivery_journal_store.py", "JournalStore.connect", 1): (
        "delivery journal: trw-mcp's own local delivery-evidence journal, not checkout-supplied "
        "content; opens (creating if absent) the read-write store with 0600 perms."
    ),
    ("tools/_delivery_journal_store.py", "JournalStore.connect_ro", 1): (
        "delivery journal: read-only open of the same local, trw-mcp-owned journal file."
    ),
}


_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _scope_imports(scope: ast.AST) -> dict[str, str | None]:
    """The names *scope*'s own import statements bind, excluding any nested function/class/lambda scope.

    Each name maps to ``"module"`` (``import sqlite3`` / ``import sqlite3 as sql``), ``"connect"``
    (``from sqlite3 import connect`` / ``... as c``), or ``None`` for any other import, which shadows an
    outer scope's sqlite3 binding of the same name. An import nested in an ``if``/``try`` block still
    binds in the enclosing scope, so the walk descends into everything except a new scope.
    """
    bound: dict[str, str | None] = {}
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, _SCOPE_NODES):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.partition(".")[0]
                bound[alias.asname or top] = "module" if top == "sqlite3" else None
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                is_connect = node.module == "sqlite3" and alias.name == "connect"
                bound[alias.asname or alias.name] = "connect" if is_connect else None
        pending.extend(ast.iter_child_nodes(node))
    return bound


def _is_direct_connect(node: ast.AST, visible: dict[str, str | None]) -> bool:
    """A ``connect_registered(...)`` call, a bound-``connect`` bare call, or ``X.connect(...)`` on a DB-API name.

    *visible* is the import bindings the call's own lexical scope sees (see :func:`_ordered_sites`); the
    ``sqlite3``/``dbapi``/``pysqlite3``/``*_dbapi`` names are a heuristic fallback for a receiver those
    imports do not explain (e.g. an attribute of an unrelated object that merely looks like a DB-API
    module).
    """
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id == "connect_registered" or visible.get(node.func.id) == "connect"
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "connect"):
        return False
    receiver = node.func.value
    receiver_name = receiver.id if isinstance(receiver, ast.Name) else None
    if receiver_name is None:
        return False
    return (
        visible.get(receiver_name) == "module"
        or receiver_name in {"sqlite3", "dbapi", "pysqlite3"}
        or receiver_name.endswith("_dbapi")
    )


def _ordered_sites(tree: ast.Module, relative: str) -> list[tuple[str, str, int]]:
    """(path, qualname, ordinal) for every direct connect in ``tree``, numbered in source order.

    Import bindings resolve per lexical scope, the way Python does: code sees its own scope's imports,
    then its enclosing function scopes', then the module's. A class body's imports are visible to
    statements directly in that body but not to methods nested in it, so a class scope does not pass its
    own bindings down.
    """
    located: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, scope: str, visible: dict[str, str | None], inherited: dict[str, str | None]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call) and _is_direct_connect(child, visible):
                located.append((scope, child.lineno, child.col_offset))
            if isinstance(child, _SCOPE_NODES):
                child_scope = scope
                if not isinstance(child, ast.Lambda):
                    child_scope = child.name if scope == "<module>" else f"{scope}.{child.name}"
                child_visible = {**inherited, **_scope_imports(child)}
                passed_down = inherited if isinstance(child, ast.ClassDef) else child_visible
                visit(child, child_scope, child_visible, passed_down)
            else:
                visit(child, scope, visible, inherited)

    module_bindings = _scope_imports(tree)
    visit(tree, "<module>", module_bindings, module_bindings)
    ordinals: dict[str, int] = {}
    ordered: list[tuple[str, str, int]] = []
    for scope, _line, _col in sorted(located, key=lambda item: (item[1], item[2])):
        ordinals[scope] = ordinals.get(scope, 0) + 1
        ordered.append((relative, scope, ordinals[scope]))
    return ordered


def _direct_connect_call_sites(root: Path | None = None) -> list[tuple[str, str, int]]:
    """Every direct connect call under *root* (default ``trw_mcp/src/trw_mcp``), keyed by where it lives.

    AST-based, not a grep: matches ``connect_registered(...)`` and a bare ``.connect(`` call whose
    receiver name suggests a DB-API module (``sqlite3``, ``dbapi``, ``pysqlite3``, or anything ending
    ``_dbapi``) -- narrow enough to skip unrelated ``.connect(`` calls (network clients, signal/slot
    connections) without needing a type checker.

    Each site is keyed by (path, enclosing function qualname, 1-based ordinal within that function)
    rather than by line number, so an unrelated edit above a call does not make an audited entry look
    stale.
    """
    package_root = root if root is not None else Path(trw_mcp.__file__).parent
    sites: list[tuple[str, str, int]] = []
    for path in sorted(package_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # trw-fail-silent-allow: no source file under trw_mcp/src is expected to be unparseable; a genuinely broken file would fail mypy/ruff long before this census runs, so skipping it here does not hide a connect() call the rest of the pipeline missed
            continue
        sites.extend(_ordered_sites(tree, str(path.relative_to(package_root))))
    return sites


def test_every_direct_sqlite_connect_is_accounted_for() -> None:
    """PRD-QUAL-147 FR09: a new, unaudited direct connect call fails this test by name."""
    found = set(_direct_connect_call_sites())
    known = set(_AUDITED_EXCEPTIONS)

    unaudited = found - known
    assert unaudited == set(), (
        f"new direct sqlite3/connect_registered call(s) not in _AUDITED_EXCEPTIONS: {sorted(unaudited)} -- "
        "add a reasoned, class-tagged entry (see the class-tag legend above _AUDITED_EXCEPTIONS)."
    )


def test_every_audited_exception_still_exists_at_its_recorded_site() -> None:
    """The allowlist must track the source, not fossilize: a moved/removed call site is stale, not safe."""
    found = set(_direct_connect_call_sites())
    stale = set(_AUDITED_EXCEPTIONS) - found
    assert stale == set(), (
        f"_AUDITED_EXCEPTIONS entries no longer match any call site (moved or removed): {sorted(stale)}"
    )


def test_walker_catches_an_injected_unaudited_connect(tmp_path: Path) -> None:
    """Guard-the-guard: the walker must fail on a call site not in any exception list, via a real file on disk.

    Exercises the same AST function the two tests above use, against a throwaway source tree (not the
    real trw-mcp source), so this test is a permanent proof that an unlisted connect site is caught --
    it is not itself part of the audited baseline.
    """
    package = tmp_path / "throwaway_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_new_module.py").write_text(
        "import sqlite3\n\n\ndef read_something():\n    conn = sqlite3.connect('does-not-exist.db')\n    return conn\n"
    )

    sites = _direct_connect_call_sites(root=package)

    assert ("_new_module.py", "read_something", 1) in sites
    # This is exactly the failure test_every_direct_sqlite_connect_is_accounted_for guards against: an
    # unlisted site is present in the walk and would fail that test by name if it were real trw-mcp source.
    assert ("_new_module.py", "read_something", 1) not in _AUDITED_EXCEPTIONS


def test_walker_catches_an_injected_connect_registered_call(tmp_path: Path) -> None:
    """Guard-the-guard: ``connect_registered(...)`` (no receiver object) is caught the same way."""
    package = tmp_path / "throwaway_pkg2"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_new_module.py").write_text(
        "def read_something():\n    return connect_registered('does-not-exist.db')\n"
    )

    sites = _direct_connect_call_sites(root=package)

    assert ("_new_module.py", "read_something", 1) in sites


def test_walker_catches_an_injected_aliased_module_connect(tmp_path: Path) -> None:
    """Guard-the-guard (sol r1 P2 #1): ``import sqlite3 as sql`` + ``sql.connect(...)`` must be caught.

    ``sql`` is not one of the static heuristic receiver names (``sqlite3``/``dbapi``/``pysqlite3``/
    ``*_dbapi``); only resolving this file's own ``import ... as`` binding makes it visible.
    """
    package = tmp_path / "throwaway_pkg_alias"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_new_module.py").write_text(
        "import sqlite3 as sql\n\n\ndef read_something():\n    conn = sql.connect('does-not-exist.db')\n    return conn\n"
    )

    sites = _direct_connect_call_sites(root=package)

    assert ("_new_module.py", "read_something", 1) in sites
    assert ("_new_module.py", "read_something", 1) not in _AUDITED_EXCEPTIONS


def test_walker_catches_an_injected_from_import_connect(tmp_path: Path) -> None:
    """Guard-the-guard (sol r1 P2 #1): ``from sqlite3 import connect`` + bare ``connect(...)`` must be caught.

    A bare-name call has no receiver at all; only resolving this file's own ``from sqlite3 import``
    binding distinguishes it from an unrelated function named ``connect``.
    """
    package = tmp_path / "throwaway_pkg_fromimport"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_new_module.py").write_text(
        "from sqlite3 import connect\n\n\ndef read_something():\n    conn = connect('does-not-exist.db')\n    return conn\n"
    )

    sites = _direct_connect_call_sites(root=package)

    assert ("_new_module.py", "read_something", 1) in sites
    assert ("_new_module.py", "read_something", 1) not in _AUDITED_EXCEPTIONS


def test_walker_ignores_unrelated_dot_connect_calls(tmp_path: Path) -> None:
    """A ``.connect(`` on something that is not a DB-API-looking name must not false-positive.

    Also covers a bare ``connect(...)`` call to a locally defined function of that name that was
    never bound to ``sqlite3.connect`` -- the alias resolution must not treat every ``connect`` name
    as sqlite3's.
    """
    package = tmp_path / "throwaway_pkg3"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_new_module.py").write_text(
        "def connect(handler):\n    return handler\n\n\n"
        "def wire_signal(bus, handler):\n    bus.connect(handler)\n    return connect(handler)\n"
    )

    assert _direct_connect_call_sites(root=package) == []


def test_walker_resolves_a_same_name_alias_per_function_scope(tmp_path: Path) -> None:
    """QUAL-147 FR09 follow-up: an alias bound in one function is not bound in its sibling.

    ``reader`` binds ``connect`` (and ``db``) to sqlite3 in its own scope; ``wire`` binds the same two
    names to an unrelated module. Per-file resolution counted ``wire``'s calls too (a false failure);
    per-scope resolution counts only ``reader``'s, and still sees an enclosing function's import from a
    nested function.
    """
    package = tmp_path / "throwaway_pkg_scopes"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_new_module.py").write_text(
        "def reader():\n"
        "    from sqlite3 import connect\n"
        "    import sqlite3 as db\n"
        "    def nested():\n"
        "        return db.connect('b.db')\n"
        "    return connect('a.db'), nested\n\n\n"
        "def wire(bus):\n"
        "    from signals import connect\n"
        "    import eventbus as db\n"
        "    return connect(bus), db.connect(bus)\n"
    )

    assert sorted(_direct_connect_call_sites(root=package)) == [
        ("_new_module.py", "reader", 1),
        ("_new_module.py", "reader.nested", 1),
    ]


def test_walker_hides_a_class_body_import_from_its_methods(tmp_path: Path) -> None:
    """Python scoping: a class body's import is visible in that body, not inside its methods."""
    package = tmp_path / "throwaway_pkg_class_scope"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_new_module.py").write_text(
        "class Store:\n"
        "    from sqlite3 import connect\n"
        "    handle = connect(':memory:')\n\n"
        "    def wire(self, bus):\n"
        "        return connect(bus)\n"
    )

    assert _direct_connect_call_sites(root=package) == [("_new_module.py", "Store", 1)]


def test_delivery_io_tracer_has_no_ast_visible_connect_call() -> None:
    """Regression trap for the module docstring's claim: the tracer's monkeypatch is not a call site.

    ``sqlite3.connect = _wrap_connect(original_connect)`` is an assignment, and the wrapped
    replacement calls ``original(...)`` (a bare Name, not ``sqlite3.connect(...)``), so neither is
    matched by ``_is_direct_connect``. If a future edit turns either into a real ``sqlite3.connect(``
    call, this census (and this test) must see it.
    """
    support_root = Path(__file__).resolve().parent / "support"
    tracer_relative = "_delivery_io_tracer.py"
    assert (support_root / tracer_relative).is_file()
    sites = [site for site in _direct_connect_call_sites(root=support_root) if site[0] == tracer_relative]
    assert sites == []
    # PRD-CORE-313 FR02: the tracer is test instrumentation and no longer lives in the package.
    assert not (Path(trw_mcp.__file__).parent / "tools" / tracer_relative).exists()
