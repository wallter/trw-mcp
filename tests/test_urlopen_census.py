"""AST census: no ``urllib.request.urlopen`` call outside a documented allowlist
(PRD-SEC-021 FR06), plus a parametrized https-or-loopback refusal check across the
three FR04/FR05 migrated call sites (``cli/auth.py::_post_json``,
``server/_doctor_backend_connectivity.py::probe_backend_url``,
``server/_subcommands_release.py::_push_release``).

Follows the ``_census_scan``/``_call_name`` AST pattern in
``tests/test_platform_trust.py`` (PRD-SEC-021 FR06 acceptance criterion).

Failing-first (NFR02): this file, run unmodified against the pre-fix code (``git
archive`` of the base commit, before this PRD's ``httpx`` migration landed, in a scratch
copy) failed 4 of 8 cases. ``test_no_urlopen_outside_allowlist`` found 3 offenders --
``trw_mcp/cli/auth.py:80``, ``trw_mcp/server/_doctor_backend_connectivity.py:45``,
``trw_mcp/server/_subcommands_release.py:306`` -- because all three sites still called
``urllib.request.urlopen`` directly. The three https-or-loopback refusal tests failed
because the pre-fix code had no scheme/host guard at all: the ``dns_tripwire`` fixture
(patched at ``socket.getaddrinfo``, so it catches the request attempt regardless of
which HTTP library makes it) recorded ``called=True`` for all three sites given a
non-loopback ``http://`` URL -- i.e. each site actually attempted to resolve and
connect to the disallowed host before failing, rather than refusing up front. See the
commit WHY for the exact archive/run reproduction.

Round-1 review (3 fixes, each with its own failing-first proof against the census
logic itself, using synthetic sources so no production file needs to carry a
deliberately-planted defect). Confirmed by loading the pre-round-1 module
(commit a0bcabc49) via ``importlib.util.spec_from_file_location`` and calling its own
``_is_urlopen_call``/``_census_scan`` directly against the synthetic sources below:
``_is_urlopen_call`` on ``from urllib.request import urlopen as u; u(...)`` returned 0
hits (should be 1); and setting the old whole-file-keyed ``_ALLOWLIST["synthetic/site.py"]``
made ``_census_scan`` return ``[]`` offenders for a file containing an allowlisted
``allowed()`` function AND a brand-new, never-reviewed ``other()`` function calling
``urlopen`` -- the old scheme silently exempted BOTH.

1. Aliased ``urlopen`` forms -- ``from urllib.request import urlopen as u`` then
   ``u(...)`` -- were undetected because the old check only matched a literal
   ``Name`` id of ``"urlopen"``. ``_urlopen_aliases`` now resolves
   ``ImportFrom`` nodes to catch any local bound name. ``urllib.request.build_opener(
   ...).open(...)`` is also detected (a ``.open`` call whose receiver expression is
   itself a direct call to something named ``build_opener``) -- cheaply, by AST shape
   only: a ``build_opener()`` result first ASSIGNED to a variable, then ``.open()``'d
   later, is a dataflow-tracking problem out of scope for this lint-level census.
2. The allowlist is now keyed ``"<rel path>:<qualname>"`` (``qualname`` = dotted
   enclosing function/class path, or ``<module>`` for module-level code), not by
   whole file, per the charter's "key allowlists by symbol, never file or line" rule
   -- a new offending call in an allowlisted FILE but a DIFFERENT function still
   fails the census.
"""

from __future__ import annotations

import ast
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

#: Empty after PRD-SEC-021 FR04: zero ``urllib.request.urlopen`` call sites remain in
#: ``trw-mcp/src/trw_mcp``. Add a ``"path/to/file.py:qualname"`` entry (``qualname`` is
#: the enclosing function's dotted name, or ``<module>`` for module-level code) with a
#: one-sentence reason if a future call site needs ``urlopen`` specifically instead of
#: ``httpx``. Keyed by symbol, never by whole file, so a new offending call added
#: elsewhere in an already-exempt file is still caught.
_ALLOWLIST: dict[str, str] = {}


def _urlopen_aliases(tree: ast.Module) -> set[str]:
    """Local names bound to ``urllib.request.urlopen`` via ``from ... import ... as ...``."""
    aliases = {"urlopen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"urllib.request", "request"}:
            for alias in node.names:
                if alias.name == "urlopen":
                    aliases.add(alias.asname or alias.name)
    return aliases


def _is_build_opener_expr(node: ast.AST) -> bool:
    """True if *node* is a call to something named (or ending in) ``build_opener``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "build_opener"
    if isinstance(func, ast.Attribute):
        return func.attr == "build_opener"
    return False


def _is_urlopen_call(node: ast.AST, aliases: set[str]) -> bool:
    """True for a call to ``urlopen`` (by any resolved alias), or to ``.open()`` on a
    ``build_opener(...)`` result (``OpenerDirector.open``, the redirect-following
    equivalent of ``urlopen``).

    Deliberately restricted to ``ast.Call``/``ast.Attribute``/``ast.Name`` nodes,
    never ``ast.Constant`` string values, so a string-literal marker such as
    ``meta_tune/sandbox.py``'s network-attempt-detection heuristic list never trips the
    census (FR06 acceptance criterion 3) -- it is excluded by construction, not via an
    allowlist entry.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in aliases
    if isinstance(func, ast.Attribute):
        if func.attr == "urlopen":
            return True
        if func.attr == "open" and _is_build_opener_expr(func.value):
            return True
    return False


def _iter_calls_with_qualname(tree: ast.AST) -> Iterator[tuple[ast.Call, str]]:
    """Yield every ``ast.Call`` node paired with its enclosing function/class qualname.

    ``qualname`` is the dotted path of enclosing ``FunctionDef``/``AsyncFunctionDef``/
    ``ClassDef`` names (e.g. ``"MyClass.method"``), or ``"<module>"`` for module-level
    code -- this is the key the allowlist is scoped to (item 2: symbol, not file).
    """

    def _walk(node: ast.AST, scope: list[str]) -> Iterator[tuple[ast.Call, str]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                yield child, ".".join(scope) if scope else "<module>"
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield from _walk(child, [*scope, child.name])
            else:
                yield from _walk(child, scope)

    yield from _walk(tree, [])


def _census_scan(root: Path, *, rel_prefix: str, allowlist: dict[str, str] | None = None) -> list[str]:
    active_allowlist = _ALLOWLIST if allowlist is None else allowlist
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = f"{rel_prefix}/{path.relative_to(root).as_posix()}"
        text = path.read_text(encoding="utf-8")
        if "urlopen" not in text and "open" not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        aliases = _urlopen_aliases(tree)
        for node, qualname in _iter_calls_with_qualname(tree):
            if not _is_urlopen_call(node, aliases):
                continue
            key = f"{rel}:{qualname}"
            if key in active_allowlist:
                continue
            offenders.append(f"{key}:{node.lineno}")
    return offenders


def test_no_urlopen_outside_allowlist() -> None:
    """Zero ``urllib.request.urlopen`` call sites in ``trw-mcp/src/trw_mcp`` outside the allowlist.

    Fails if any of the three FR04 sites is reverted to ``urllib.request.urlopen``, or if a
    new call site is added anywhere in ``trw-mcp/src/trw_mcp`` without an explicit
    ``_ALLOWLIST`` entry and reason.
    """
    repo_root = Path(__file__).resolve().parents[1]
    offenders = _census_scan(repo_root / "src" / "trw_mcp", rel_prefix="trw_mcp")
    assert offenders == [], f"urlopen call sites outside the documented allowlist: {offenders}"


def test_sandbox_urlopen_string_marker_does_not_trip_census() -> None:
    """``meta_tune/sandbox.py``'s ``"urlopen"`` string literal is a heuristic marker, not a call."""
    repo_root = Path(__file__).resolve().parents[1]
    text = (repo_root / "src" / "trw_mcp" / "meta_tune" / "sandbox.py").read_text(encoding="utf-8")
    assert '"urlopen"' in text, "fixture assumption stale: sandbox.py no longer has the marker"
    offenders = _census_scan(repo_root / "src" / "trw_mcp", rel_prefix="trw_mcp")
    assert not any("meta_tune/sandbox.py" in o for o in offenders)


# ---------------------------------------------------------------------------
# Round-1 review item 1: aliased-import detection (synthetic sources, no production
# file needs a planted defect to prove these forms are caught).
# ---------------------------------------------------------------------------


def _scan_source(tmp_path: Path, source: str) -> list[str]:
    src_dir = tmp_path / "synthetic_root"
    src_dir.mkdir()
    (src_dir / "site.py").write_text(source, encoding="utf-8")
    return _census_scan(src_dir, rel_prefix="synthetic", allowlist={})


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from urllib.request import urlopen as u\n\n\ndef f():\n    return u('http://x')\n",
            id="import-as-alias-name-call",
        ),
        pytest.param(
            "import urllib.request as r\n\n\ndef f():\n    return r.urlopen('http://x')\n",
            id="module-alias-attribute-call",
        ),
        pytest.param(
            "import urllib.request\n\n\ndef f():\n    return urllib.request.urlopen('http://x')\n",
            id="bare-import-dotted-attribute-call",
        ),
        pytest.param(
            "from urllib.request import build_opener\n\n\ndef f():\n    return build_opener().open('http://x')\n",
            id="build-opener-dot-open",
        ),
    ],
)
def test_census_detects_every_aliased_form(tmp_path: Path, source: str) -> None:
    offenders = _scan_source(tmp_path, source)
    assert offenders, f"aliased/opener form went undetected:\n{source}"


def test_census_ignores_unrelated_dot_open_call(tmp_path: Path) -> None:
    """A plain ``.open()`` NOT preceded by ``build_opener(...)`` is not a false positive."""
    source = "def f():\n    fh = open('local.txt')\n    return fh.read()\n"
    offenders = _scan_source(tmp_path, source)
    assert offenders == []


# ---------------------------------------------------------------------------
# Round-1 review item 2: allowlist is keyed by symbol (module:qualname), not file.
# ---------------------------------------------------------------------------


def test_allowlist_entry_does_not_exempt_a_different_function_in_the_same_file(tmp_path: Path) -> None:
    """A new offending call in an allowlisted FILE but a DIFFERENT function still fails."""
    source = (
        "import urllib.request\n\n\n"
        "def allowed():\n    return urllib.request.urlopen('http://x')\n\n\n"
        "def other():\n    return urllib.request.urlopen('http://y')\n"
    )
    src_dir = tmp_path / "synthetic_root"
    src_dir.mkdir()
    (src_dir / "site.py").write_text(source, encoding="utf-8")

    allowlist = {"synthetic/site.py:allowed": "reviewed false positive for this test"}
    offenders = _census_scan(src_dir, rel_prefix="synthetic", allowlist=allowlist)

    assert not any(":allowed:" in o for o in offenders), "the allowlisted symbol must stay exempt"
    assert any(":other:" in o for o in offenders), "a different function in the same file must still be caught"


def test_allowlist_entry_exempts_only_the_named_symbol(tmp_path: Path) -> None:
    """The allowlisted symbol itself produces zero offenders once named."""
    source = "import urllib.request\n\n\ndef allowed():\n    return urllib.request.urlopen('http://x')\n"
    src_dir = tmp_path / "synthetic_root"
    src_dir.mkdir()
    (src_dir / "site.py").write_text(source, encoding="utf-8")

    allowlist = {"synthetic/site.py:allowed": "reviewed false positive for this test"}
    offenders = _census_scan(src_dir, rel_prefix="synthetic", allowlist=allowlist)

    assert offenders == []


# ---------------------------------------------------------------------------
# FR05: https-only unless the resolved host is loopback, across all three sites.
# ---------------------------------------------------------------------------


class _OkHandler(http.server.BaseHTTPRequestHandler):
    """Minimal handler returning an empty JSON object for GET and POST."""

    def _reply(self) -> None:
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._reply()

    def do_POST(self) -> None:
        self._reply()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture()
def loopback_port() -> Iterator[int]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _OkHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


_NON_LOOPBACK_HTTP_URL = "http://example.test/path"


@pytest.fixture()
def dns_tripwire(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, bool]]:
    """Fail any name resolution attempt, regardless of which HTTP library makes it.

    Patched at ``socket.getaddrinfo`` — the one primitive every HTTP client (``urllib``
    or ``httpx``) ultimately calls to resolve a non-loopback hostname before connecting
    — so this tripwire proves "no request was attempted" independent of which library
    the call site happens to use, unlike patching a specific client class.
    """
    import socket

    attempted = {"called": False}

    def _tripwire(*args: object, **kwargs: object) -> object:
        attempted["called"] = True
        raise OSError("name resolution must never be attempted for a refused URL")

    monkeypatch.setattr(socket, "getaddrinfo", _tripwire)
    yield attempted


def test_post_json_refuses_non_loopback_http(dns_tripwire: dict[str, bool]) -> None:
    from trw_mcp.cli import auth

    with pytest.raises(ValueError, match="non-https"):
        auth._post_json(_NON_LOOPBACK_HTTP_URL, {"a": 1})
    assert dns_tripwire["called"] is False


def test_post_json_permits_loopback_http(loopback_port: int) -> None:
    from trw_mcp.cli import auth

    result = auth._post_json(f"http://127.0.0.1:{loopback_port}/x", {"a": 1})
    assert result == {}


def test_probe_backend_url_refuses_non_loopback_http(dns_tripwire: dict[str, bool]) -> None:
    from trw_mcp.server import _doctor_backend_connectivity as doctor_conn

    ok, detail = doctor_conn.probe_backend_url(_NON_LOOPBACK_HTTP_URL)
    assert ok is False
    assert "non-https" in detail
    assert dns_tripwire["called"] is False


def test_probe_backend_url_permits_loopback_http(loopback_port: int) -> None:
    from trw_mcp.server import _doctor_backend_connectivity as doctor_conn

    ok, detail = doctor_conn.probe_backend_url(f"http://127.0.0.1:{loopback_port}/x")
    assert ok is True
    assert "200" in detail


def test_push_release_refuses_non_loopback_http(dns_tripwire: dict[str, bool]) -> None:
    from trw_mcp.server import _subcommands_release as release

    with pytest.raises(SystemExit):
        release._push_release(
            {"version": "1.0.0", "path": "/tmp/x.whl", "checksum": "abc", "size_bytes": 1},
            "http://example.test",
            "trw_dk_test",
        )
    assert dns_tripwire["called"] is False


def test_push_release_permits_loopback_http(loopback_port: int) -> None:
    from trw_mcp.server import _subcommands_release as release

    # Succeeds through to the local server; no SystemExit means the scheme/host guard
    # let the request through (the release-not-found 200 empty-JSON reply is enough to
    # prove the request was attempted and completed).
    release._push_release(
        {"version": "1.0.0", "path": "/tmp/x.whl", "checksum": "abc", "size_bytes": 1},
        f"http://127.0.0.1:{loopback_port}",
        "trw_dk_test",
    )
