"""PRD-SEC-021 FR02: the installer's presigned wheel download validates https + an allowed origin.

Drives the served ``_download_proprietary_wheel`` function directly (the
proprietary-install entry point that calls it, ``_install_proprietary_packages``,
requires the full argparse/backend scaffolding this test does not need).
Reuses S1's ONE redirect policy, ``_origin_pinned_opener``
(``_backend_redirect_origins``'s sibling for the wheel download is
``_wheel_allowed_origins``) — no second redirect/origin policy is introduced.

Round-2 review: the entitlement payload is untyped JSON, so both an
``allowed_origins`` entry and ``allowed_origins`` itself can be any JSON
shape, not just str/list. Verified against the round-1-fixed (pre-round-2)
template (commit 5469fe822, via ``git archive``): an entry of ``42`` raised
bare ``AttributeError: 'int' object has no attribute 'decode'``, a ``dict``
or ``list`` entry raised bare ``TypeError: unhashable type: '...'``, and a
top-level ``allowed_origins=42`` raised bare ``TypeError: 'int' object is not
iterable`` — none of these were the named ``WheelOriginNotAllowedError``.
Explicit ``isinstance`` checks now refuse all of these (and a top-level
``None`` still gets ``MissingAllowedOriginsError``, the "old backend"
disposition; any other non-list top-level shape gets
``WheelOriginNotAllowedError``, a "malformed present field" disposition) —
see ``_wheel_allowed_origins`` and ``_download_proprietary_wheel`` docstrings.

Failing-first, BEHAVIOURAL proof (PRD-SEC-021 NFR02/Q3): a signature/import
error alone is not acceptance evidence, so the actual pre-fix vs post-fix
BEHAVIOUR was driven through the two functions both versions of the template
share (``_post_proprietary_entitlement`` then ``_download_proprietary_wheel``,
called with each version's own signature) against a real local HTTP server,
once for the pre-fix template (merge commit 4ebf6644a, loaded via
``git archive``) and once for this fix, for five scenarios: an off-list
presigned origin, an http-scheme presigned URL, an entitlement payload
omitting ``allowed_origins``, an allowlisted origin that 302-redirects to an
off-list one, and (round-1 review P1) an ``allowed_origins`` list carrying
BOTH an https and an http entry for the same host. Observed output (recorded
2026-09-26, script at ``scratchpad/sec021s2_behavioral_proof.py`` this
session):

* off-list origin — BASE: "SUCCEEDED -- wrote pkg.whl (43 bytes, sha OK)",
  offlist server requests ``[('GET', '/pkg.whl')]``; FIX: "RAISED
  WheelOriginNotAllowedError: origin not in the allowed list: https://...",
  offlist server requests ``[]``.
* http scheme — BASE: "SUCCEEDED -- wrote pkg.whl (43 bytes, sha OK)"; FIX:
  "RAISED WheelOriginNotAllowedError: origin not in the allowed list:
  http://...".
* missing ``allowed_origins`` — BASE: "SUCCEEDED -- wrote pkg.whl (43 bytes,
  sha OK)"; FIX: "RAISED MissingAllowedOriginsError: entitlement response
  predates the allowed-origins field".
* redirect to an off-list origin — BASE: "SUCCEEDED -- wrote pkg.whl (43
  bytes, sha OK)", offlist server requests ``[('GET', '/pkg.whl')]`` (the
  base actually reached and downloaded from the off-list target); FIX:
  "RAISED CrossOriginRedirectError: refused redirect to a different origin:
  https://...", offlist server requests ``[]`` — proving FR02 reuses FR01's
  ``_origin_pinned_opener`` for the redirect hop, not just the initial URL.
* round-1 P1, mixed-scheme allowed_origins — BASE: "SUCCEEDED -- wrote
  pkg.whl (43 bytes, sha OK)" (BASE has no allowed_origins concept at all, so
  the mixed list is simply irrelevant to it); FIX: "RAISED
  WheelOriginNotAllowedError: origin not in the allowed list: malformed
  allowed_origins entry: 'http://...'", wheel server requests ``[]`` — the
  http sibling entry is refused before any request, not silently admitted
  into the pinned set (which would otherwise let an https download be
  redirected to that same host's http entry, a cleartext downgrade).

In every scenario the base actually performed the download (server logged
the GET, bytes landed, SHA-256 verified) while the fix refused before any
request reached the disallowed target. The unit tests below are the
regression suite for the fixed behaviour above; the base-vs-fix comparison
is not re-run on every CI pass (it requires loading a historical commit) —
its output is the recorded failing-first proof.
"""

from __future__ import annotations

import hashlib
import importlib.util
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_wheel_origin_probe", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load()


class _WheelServer:
    """A local HTTP listener serving one fixed byte body, or a 302 redirect."""

    def __init__(self, body: bytes = b"", redirect_to: str | None = None) -> None:
        self.body = body
        self.redirect_to = redirect_to
        self.requests: list[str] = []
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server.requests.append(self.path)
                if server.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", server.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(server.body)))
                self.end_headers()
                self.wfile.write(server.body)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.base = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def wheel_server() -> Iterator[_WheelServer]:
    server = _WheelServer(body=b"not-a-real-wheel-just-bytes")
    try:
        yield server
    finally:
        server.close()


def test_non_https_url_is_refused_before_any_request(
    installer: ModuleType, wheel_server: _WheelServer, tmp_path: Path
) -> None:
    url = f"{wheel_server.base}/pkg-1.0.0-py3-none-any.whl"  # plain http
    with pytest.raises(installer.WheelOriginNotAllowedError) as info:
        installer._download_proprietary_wheel(
            url, "0" * 64, tmp_path, [wheel_server.base.replace("http://", "https://", 1)]
        )
    assert wheel_server.requests == []  # refused before any connection was attempted
    assert "http" in str(info.value)


def test_origin_not_in_allowed_list_is_refused_before_any_request(installer: ModuleType, tmp_path: Path) -> None:
    url = "https://attacker.example.invalid/pkg-1.0.0-py3-none-any.whl"
    with pytest.raises(installer.WheelOriginNotAllowedError) as info:
        installer._download_proprietary_wheel(url, "0" * 64, tmp_path, ["https://good.example.invalid"])
    assert "attacker.example.invalid" in str(info.value)


def test_empty_allowed_origins_refuses_every_origin(installer: ModuleType, tmp_path: Path) -> None:
    url = "https://anything.example.invalid/pkg-1.0.0-py3-none-any.whl"
    with pytest.raises(installer.WheelOriginNotAllowedError):
        installer._download_proprietary_wheel(url, "0" * 64, tmp_path, [])


def test_mixed_scheme_allowed_origins_is_refused_up_front(
    installer: ModuleType, wheel_server: _WheelServer, tmp_path: Path
) -> None:
    """Round-1 review P1: a non-https sibling entry for the SAME host must not be silently admitted.

    ``_wheel_allowed_origins(["https://host", "http://host"])`` used to admit
    both into the pinned frozenset (``_origin_pinned_opener`` only checks
    membership, not scheme), so an https download for that host could be
    redirected to the http entry — a cleartext downgrade. The entry list
    itself is now validated: any non-https member refuses the WHOLE download
    before any request is attempted, not just the mismatched hop.
    """
    https_origin = wheel_server.base.replace("http://", "https://", 1)
    http_origin = wheel_server.base  # same host, http — the dangerous sibling entry
    url = f"{wheel_server.base}/pkg-1.0.0-py3-none-any.whl"

    with pytest.raises(installer.WheelOriginNotAllowedError) as info:
        installer._download_proprietary_wheel(url, "0" * 64, tmp_path, [https_origin, http_origin])

    assert wheel_server.requests == []  # refused before any connection, including to the https entry
    assert "malformed" in str(info.value) and http_origin in str(info.value)


@pytest.mark.parametrize(
    "malformed_entry",
    [
        "http://good.example.invalid",  # non-https sibling for an otherwise-valid host
        "https://good.example.invalid/some/path",  # carries a path, not a bare origin
        "https://good.example.invalid?x=1",  # carries a query
        "https://good.example.invalid#frag",  # carries a fragment
        "https://user:pass@good.example.invalid",  # carries userinfo
        "https://",  # no host at all
        "not-a-url-at-all",  # unparseable as an origin (empty scheme/host)
    ],
)
def test_malformed_allowed_origins_entry_refuses_before_any_request(
    installer: ModuleType, malformed_entry: str
) -> None:
    with pytest.raises(installer.WheelOriginNotAllowedError):
        installer._wheel_allowed_origins(["https://good.example.invalid", malformed_entry])


@pytest.mark.parametrize(
    "non_string_entry",
    [None, 42, 3.14, True, ["https://good.example.invalid"], {"origin": "https://good.example.invalid"}],
    ids=["none", "int", "float", "bool", "list", "dict"],
)
def test_non_string_allowed_origins_entry_refuses_before_any_request(
    installer: ModuleType, non_string_entry: object
) -> None:
    """Round-2 review: an untyped JSON entry (None/number/list/dict), not just a wrong-shaped string."""
    with pytest.raises(installer.WheelOriginNotAllowedError) as info:
        installer._wheel_allowed_origins(["https://good.example.invalid", non_string_entry])
    assert "not a string" in str(info.value)


@pytest.mark.parametrize(
    ("bad_value", "expected_error"),
    [
        pytest.param(None, "MissingAllowedOriginsError", id="none-is-missing-field-semantics"),
        pytest.param("https://good.example.invalid", "WheelOriginNotAllowedError", id="bare-string-not-a-list"),
        pytest.param(42, "WheelOriginNotAllowedError", id="int-not-a-list"),
        pytest.param({"a": "https://good.example.invalid"}, "WheelOriginNotAllowedError", id="dict-not-a-list"),
    ],
)
def test_non_list_allowed_origins_value_refuses_before_any_request(
    installer: ModuleType, wheel_server: _WheelServer, tmp_path: Path, bad_value: object, expected_error: str
) -> None:
    """Round-2 review: allowed_origins itself may be any JSON shape, not just list-or-None.

    Only ``None`` (the key was absent, or JSON ``null``) gets the "old
    backend" MissingAllowedOriginsError; any other non-list shape is a
    malformed PRESENT field (WheelOriginNotAllowedError) — a distinct
    disposition, documented on ``_download_proprietary_wheel``.
    """
    url = f"{wheel_server.base}/pkg-1.0.0-py3-none-any.whl"
    error_cls = getattr(installer, expected_error)
    with pytest.raises(error_cls):
        installer._download_proprietary_wheel(url, "0" * 64, tmp_path, bad_value)
    assert wheel_server.requests == []


def test_pinned_opener_never_follows_an_https_origin_to_its_http_sibling(installer: ModuleType) -> None:
    """_origin_pinned_opener itself must refuse a same-host scheme downgrade when only https is pinned.

    Belt-and-suspenders for the round-1 fix: even if a caller somehow built a
    pinned set from an https-only entry, the opener's redirect handler still
    refuses a redirect Location that reuses the same host over http, because
    the tuple ("http", host, port) was never a member of an https-only set.
    """
    import urllib.request

    pinned = installer._wheel_allowed_origins(["https://good.example.invalid"])
    assert ("http", "good.example.invalid", 80) not in pinned  # the https-only set carries no http sibling
    opener = installer._origin_pinned_opener(pinned)
    handler = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
    req = urllib.request.Request("https://good.example.invalid/a")

    with pytest.raises(installer.CrossOriginRedirectError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://good.example.invalid/b")


def test_missing_allowed_origins_raises_a_distinct_error(installer: ModuleType, tmp_path: Path) -> None:
    """OQ-003: a payload predating FR03 must raise a DIFFERENT class than an origin mismatch."""
    url = "https://anything.example.invalid/pkg-1.0.0-py3-none-any.whl"
    with pytest.raises(installer.MissingAllowedOriginsError) as info:
        installer._download_proprietary_wheel(url, "0" * 64, tmp_path, None)
    assert "predates" in str(info.value)
    assert not issubclass(installer.MissingAllowedOriginsError, installer.WheelOriginNotAllowedError)
    assert not issubclass(installer.WheelOriginNotAllowedError, installer.MissingAllowedOriginsError)


def test_allowlisted_https_origin_downloads_and_verifies_sha256(
    installer: ModuleType, wheel_server: _WheelServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive path: validation passes, the download proceeds, SHA-256 still verifies.

    A full TLS test harness is out of scope for this PRD (verification note,
    FR02) — this exercises the real local-socket transport while treating the
    server's origin as https, so both the scheme check and the allowed-origin
    check are exercised for real against the SAME origin comparison the
    negative tests use (``_url_origin``), without requiring a certificate.
    """
    real_url_origin = installer._url_origin
    monkeypatch.setattr(installer, "_url_origin", lambda u: ("https", *real_url_origin(u)[1:]))
    url = f"{wheel_server.base}/pkg-1.0.0-py3-none-any.whl"
    https_origin = wheel_server.base.replace("http://", "https://", 1)

    dest = installer._download_proprietary_wheel(
        url, hashlib.sha256(wheel_server.body).hexdigest(), tmp_path, [https_origin]
    )

    assert dest.read_bytes() == wheel_server.body
    assert wheel_server.requests == ["/pkg-1.0.0-py3-none-any.whl"]


def test_redirect_to_an_off_list_origin_is_refused(
    installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves FR02 reuses S1's opener: a redirect hop is independently re-validated, not just the initial URL."""
    target = _WheelServer(body=b"should-never-be-served")
    origin_server = _WheelServer(redirect_to=f"{target.base}/pkg-1.0.0-py3-none-any.whl")
    try:
        real_url_origin = installer._url_origin
        monkeypatch.setattr(installer, "_url_origin", lambda u: ("https", *real_url_origin(u)[1:]))
        url = f"{origin_server.base}/pkg-1.0.0-py3-none-any.whl"
        allowed_https_origin = origin_server.base.replace("http://", "https://", 1)

        with pytest.raises(installer.CrossOriginRedirectError):
            installer._download_proprietary_wheel(url, "0" * 64, tmp_path, [allowed_https_origin])

        assert target.requests == []  # the off-list redirect target never received a request
    finally:
        origin_server.close()
        target.close()


def test_redirect_policy_unit_table_reuses_origin_pinned_opener(installer: ModuleType) -> None:
    """Unit table mirroring S1's test_backend_redirect_policy: FR02 introduces NO second opener/policy."""
    import urllib.request

    pinned = installer._wheel_allowed_origins(["https://good.example.invalid"])
    opener = installer._origin_pinned_opener(pinned)
    handler = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
    req = urllib.request.Request("https://good.example.invalid/a")

    assert (
        handler.redirect_request(req, None, 302, "Found", {}, "https://good.example.invalid/b").full_url
        == "https://good.example.invalid/b"
    )
    with pytest.raises(installer.CrossOriginRedirectError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example.invalid/b")
