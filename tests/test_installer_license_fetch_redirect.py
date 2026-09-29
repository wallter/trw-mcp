"""PRD-SEC-021 FR01: the installer's license fetch never resends the bearer cross-origin.

A real local ``http.server`` pair on two ports (distinct origins) drives the
served installer functions: ``_fetch_proprietary_license`` (GET, bearer) and
``_post_proprietary_entitlement`` (POST, license in the body), both of which go
through the shared ``_call_backend_json_with_retry`` skeleton.

Failing-first (PRD-SEC-021 NFR02, recorded against the pre-fix template at
lead/int-700 9e0c3a469): ``test_cross_origin_redirect_is_refused_before_reaching_target[license]``
failed with ``AssertionError: the cross-origin target received [('GET',
'/me/proprietary-license', 'Bearer <placeholder>')]`` (urllib copied the
``headers=`` bearer onto the redirected request and the fetch returned the
target's license), the ``[entitlement]`` case failed because the target
received the redirected request, and ``test_same_origin_redirect_succeeds_without_resending_bearer``
failed because the redirected leg carried the ``Authorization`` header.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_PLACEHOLDER_KEY = "placeholder-not-a-real-key"  # NFR01: never a real credential
_LICENSE_PATH = "/me/proprietary-license"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_license_redirect_probe", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load()


class _Server:
    """A local HTTP listener whose routes return (status, headers, json-body)."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str | None]] = []
        self.routes: dict[str, Callable[[], tuple[int, dict[str, str], dict[str, Any]]]] = {}
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                server.requests.append((self.command, self.path, self.headers.get("Authorization")))
                status, headers, body = server.routes.get(self.path, lambda: (404, {}, {"error": "nf"}))()
                payload = json.dumps(body).encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _serve
            do_POST = _serve

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
def servers() -> Iterator[tuple[_Server, _Server]]:
    origin, other = _Server(), _Server()
    try:
        yield origin, other
    finally:
        origin.close()
        other.close()


@pytest.fixture
def sleeps(installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record retry back-off sleeps instead of waiting; a refusal must never retry."""
    recorded: list[float] = []
    monkeypatch.setattr(installer.time, "sleep", recorded.append)
    return recorded


def _redirect(to: str, code: int = 302) -> Callable[[], tuple[int, dict[str, str], dict[str, Any]]]:
    return lambda: (code, {"Location": to}, {})


def _ok(body: dict[str, Any]) -> Callable[[], tuple[int, dict[str, str], dict[str, Any]]]:
    return lambda: (200, {}, body)


def _call_license(installer: ModuleType, base: str) -> object:
    return installer._fetch_proprietary_license(base, _PLACEHOLDER_KEY, timeout=5)


def _call_entitlement(installer: ModuleType, base: str) -> object:
    return installer._post_proprietary_entitlement(base, _PLACEHOLDER_KEY, "trw-demo", "1.0.0", timeout=5)


_ENTITLEMENT_BODY = {"url": "https://example.invalid/w.whl", "sha256": "0" * 64, "version": "1.0.0"}


@pytest.mark.parametrize(
    ("call", "path", "code", "target_body"),
    [
        pytest.param(_call_license, _LICENSE_PATH, 302, {"license_key": "from-target"}, id="license"),
        pytest.param(_call_license, _LICENSE_PATH, 307, {"license_key": "from-target"}, id="license-307"),
        pytest.param(_call_entitlement, "/proprietary/entitlement", 302, _ENTITLEMENT_BODY, id="entitlement"),
    ],
)
def test_cross_origin_redirect_is_refused_before_reaching_target(
    installer: ModuleType,
    servers: tuple[_Server, _Server],
    sleeps: list[float],
    call: Callable[[ModuleType, str], object],
    path: str,
    code: int,
    target_body: dict[str, Any],
) -> None:
    origin, other = servers
    origin.routes[path] = _redirect(f"{other.base}{path}", code)
    other.routes[path] = _ok(target_body)

    raised: BaseException | None = None
    try:
        call(installer, origin.base)
    except Exception as exc:
        raised = exc

    # Behavioural core first, through the API both versions share: nothing
    # reached the other origin — no bearer, no body, no request at all.
    assert other.requests == [], f"the cross-origin target received {other.requests}"
    # The refusal is typed and permanent: one request to the backend, no retries.
    assert type(raised).__name__ == "CrossOriginRedirectError", repr(raised)
    assert isinstance(raised, RuntimeError)  # every installer caller downgrades on RuntimeError
    assert not isinstance(raised, OSError)  # the retry loop treats OSError/URLError as transient
    assert origin.requests == [origin.requests[0]] and sleeps == []
    assert other.base.rsplit(":", 1)[1] in str(raised)
    assert _PLACEHOLDER_KEY not in str(raised)


def test_same_origin_redirect_succeeds_without_resending_bearer(
    installer: ModuleType, servers: tuple[_Server, _Server], sleeps: list[float]
) -> None:
    origin, _other = servers
    origin.routes[_LICENSE_PATH] = _redirect("/v2/me/proprietary-license")
    origin.routes["/v2/me/proprietary-license"] = _ok({"license_key": "lic-same-origin"})

    assert _call_license(installer, origin.base) == "lic-same-origin"
    first, second = origin.requests
    assert first == ("GET", _LICENSE_PATH, f"Bearer {_PLACEHOLDER_KEY}")  # the backend itself gets the bearer
    assert second == ("GET", "/v2/me/proprietary-license", None)  # add_unredirected_header: never re-sent
    assert sleeps == []


@pytest.mark.parametrize("location", ["file:///etc/hosts", "data:,x", "ftp://127.0.0.1/x"])
def test_unsupported_scheme_redirect_is_a_typed_permanent_refusal(
    installer: ModuleType, servers: tuple[_Server, _Server], sleeps: list[float], location: str
) -> None:
    """Review r1 (sec021-s1): urllib's own scheme refusal raised an HTTPError 3xx that the loop retried.

    Failing-first against 3eeca48e7 (file: and data:; ftp: was already refused): a plain RuntimeError "auto-license
    network failure after retries: HTTPError" after three requests and sleeps [1.0, 2.0].
    """
    origin, _other = servers
    origin.routes[_LICENSE_PATH] = _redirect(location)

    with pytest.raises(RuntimeError) as info:
        _call_license(installer, origin.base)
    assert type(info.value).__name__ == "CrossOriginRedirectError", repr(info.value)
    assert len(origin.requests) == 1 and sleeps == []


def test_retryable_failure_still_retries_then_raises_plain_runtime_error(
    installer: ModuleType, servers: tuple[_Server, _Server], sleeps: list[float]
) -> None:
    """Regression: the refusal did not change the transient (5xx) exit path."""
    origin, _other = servers
    origin.routes[_LICENSE_PATH] = lambda: (503, {}, {"error": "busy"})

    with pytest.raises(RuntimeError, match="auto-license network failure after retries") as info:
        _call_license(installer, origin.base)
    assert type(info.value) is RuntimeError
    assert len(origin.requests) == 3 and sleeps == [1.0, 2.0]


def test_resolve_downgrades_with_a_redirect_specific_hint(
    installer: ModuleType, servers: tuple[_Server, _Server], sleeps: list[float], tmp_path: Path
) -> None:
    """The served caller keeps the public install going and names the real cause."""
    origin, other = servers
    origin.routes[_LICENSE_PATH] = _redirect(f"{other.base}{_LICENSE_PATH}")
    warnings: list[str] = []

    class _UI:
        def step_warn(self, message: str) -> None:
            warnings.append(message)

    result = installer._resolve_proprietary_license(
        with_proprietary=True,
        explicit_license_key="",
        backend_url=origin.base,
        prior_config={"api_key": _PLACEHOLDER_KEY},
        ui=_UI(),
    )
    assert result == ("", False)
    assert other.requests == []
    assert len(warnings) == 1 and "different origin" in warnings[0]
    assert "once the backend is reachable" not in warnings[0]


@pytest.mark.parametrize(
    ("start", "target", "allowed"),
    [
        ("http://h.test:8080/a", "http://h.test:8080/b", True),
        ("https://h.test/a", "https://H.TEST:443/b", True),  # case + explicit default port
        ("http://h.test/a", "https://h.test/b", True),  # same-host upgrade to https (PRD §8)
        ("https://h.test/a", "http://h.test/b", False),  # downgrade
        ("http://h.test:8080/a", "https://h.test/b", False),  # upgrade only from the default port
        ("http://h.test/a", "http://h.test:81/b", False),  # port change
        ("http://h.test/a", "http://evil.test/b", False),  # host change
        ("http://h.test/a", "http://h.test.evil.test/b", False),  # suffix trick
        ("http://h.test/a", "http://h.test:99999/b", False),  # unparseable port fails closed
        ("http://h.test/a", "http://h.test:0/b", False),  # explicit port 0 is not the default port
        ("http://h.test/a", "ftp://h.test/b", False),
    ],
)
def test_backend_redirect_policy(installer: ModuleType, start: str, target: str, allowed: bool) -> None:
    """Unit table for the one redirect policy (FR02 reuses ``_origin_pinned_opener``)."""
    import urllib.request

    opener = installer._origin_pinned_opener(installer._backend_redirect_origins(start))
    handler = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
    req = urllib.request.Request(start)
    if allowed:
        assert handler.redirect_request(req, None, 302, "Found", {}, target).full_url == target
    else:
        with pytest.raises(installer.CrossOriginRedirectError):
            handler.redirect_request(req, None, 302, "Found", {}, target)
