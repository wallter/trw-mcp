"""Tests for the W38 (7.0.0 security P1) shared platform-egress trust gate.

Covers the ONE decision point every platform-egress call site
(``state/auto_upgrade.py``, ``sync/pull.py``, ``sync/push.py``,
``tools/submit_feedback.py``, ``telemetry/sender.py``,
``telemetry/publisher.py``, ``telemetry/pipeline.py``) uses:

  - ``trusted_platform_hosts`` sources ONLY the default host, USER-level
    ``~/.trw/config.yaml``, and env — never a project's tracked config.
  - ``bearer_allowed_for`` — the scheme/host floor.
  - ``platform_contact_enabled`` — the config-field switch
    that disables BOTH the update check and the team-sync pull loop, and the
    24h update-check throttle.
  - ``platform_auth_headers`` — the ONE function that builds an
    ``Authorization`` header (P1-C, pre-7.0.0-freeze release verify): a
    census test below fails if any other module under ``trw_mcp/src`` builds
    one without going through it.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state._platform_trust import (
    DEFAULT_TRUSTED_PLATFORM_HOST,
    bearer_allowed_for,
    platform_auth_headers,
    platform_contact_enabled,
    trusted_platform_hosts,
)

from ._telemetry_pipeline_support import pipeline_cls  # noqa: F401

# ---------------------------------------------------------------------------
# trusted_platform_hosts — allowlist sourcing
# ---------------------------------------------------------------------------


def test_default_host_always_trusted() -> None:
    assert DEFAULT_TRUSTED_PLATFORM_HOST in trusted_platform_hosts()


def test_project_tracked_platform_url_never_adds_trust(tmp_path: Path) -> None:
    """The core W38 invariant: a project's tracked .trw/config.yaml setting
    platform_url/platform_urls to an attacker host must NOT make that host
    trusted — trust never derives from the merged TRWConfig."""
    _reset_config(TRWConfig(platform_url="https://attacker.host", platform_urls=["https://also-attacker.example"]))
    hosts = trusted_platform_hosts()
    assert "attacker.host" not in hosts
    assert "also-attacker.example" not in hosts


def test_user_level_config_adds_trusted_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host declared in ~/.trw/config.yaml (machine layer) IS trusted."""
    home = Path(os.environ["HOME"])
    trw_dir = home / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text(
        'platform_trusted_hosts:\n  - "self-hosted.internal.example"\n', encoding="utf-8"
    )
    hosts = trusted_platform_hosts()
    assert "self-hosted.internal.example" in hosts


def test_malformed_user_config_fails_open_to_default_only(monkeypatch: pytest.MonkeyPatch) -> None:
    home = Path(os.environ["HOME"])
    trw_dir = home / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("not: [valid, yaml, :::", encoding="utf-8")
    hosts = trusted_platform_hosts()
    assert hosts == {DEFAULT_TRUSTED_PLATFORM_HOST}


def test_env_trusted_hosts_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_PLATFORM_TRUSTED_HOSTS", "op1.example, op2.example")
    hosts = trusted_platform_hosts()
    assert {"op1.example", "op2.example"} <= hosts


# ---------------------------------------------------------------------------
# bearer_allowed_for
# ---------------------------------------------------------------------------


class TestBearerAllowedFor:
    def test_default_host_https_allowed(self) -> None:
        assert bearer_allowed_for(f"https://{DEFAULT_TRUSTED_PLATFORM_HOST}/v1/x")

    def test_untrusted_https_host_refused(self) -> None:
        assert not bearer_allowed_for("https://not-trusted.example/v1/x")

    def test_http_non_loopback_refused(self) -> None:
        assert not bearer_allowed_for("http://not-trusted.example/v1/x")

    @pytest.mark.parametrize(
        "url", ["http://localhost:5002/v1/x", "http://127.0.0.1:5002/v1/x", "http://[::1]:5002/v1/x"]
    )
    def test_http_loopback_allowed(self, url: str) -> None:
        assert bearer_allowed_for(url)

    def test_env_trusted_host_allowed_over_https(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PLATFORM_TRUSTED_HOSTS", "self-hosted.example")
        assert bearer_allowed_for("https://self-hosted.example/v1/x")
        # Same host over http (non-loopback) is still refused.
        assert not bearer_allowed_for("http://self-hosted.example/v1/x")


# ---------------------------------------------------------------------------
# platform_contact_enabled — the kill switch
# ---------------------------------------------------------------------------


class TestPlatformContactEnabled:
    def test_enabled_by_default(self) -> None:
        _reset_config(TRWConfig())
        assert platform_contact_enabled() is True

    def test_config_switch_disables(self) -> None:
        _reset_config(TRWConfig(platform_contact_enabled=False))
        assert platform_contact_enabled() is False

    def test_a_leftover_trw_offline_is_not_a_second_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PRD-CORE-302 W40: the config field is the one contact switch."""
        _reset_config(TRWConfig(platform_contact_enabled=True))
        monkeypatch.setenv("TRW_OFFLINE", "1")
        assert platform_contact_enabled() is True


# ---------------------------------------------------------------------------
# Wiring: the switch disables BOTH contacts, end to end
# ---------------------------------------------------------------------------


class TestSwitchDisablesBothContacts:
    def test_update_check_makes_no_request_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.auto_upgrade import check_for_update

        _reset_config(TRWConfig(platform_url="https://api.trwframework.com", platform_contact_enabled=False))
        with patch("httpx.Client") as mock_client_cls:
            result = check_for_update()
        mock_client_cls.assert_not_called()
        assert result["available"] is False

    async def test_pull_makes_no_request_when_disabled(self) -> None:
        from trw_mcp.sync.pull import SyncPuller

        _reset_config(TRWConfig(platform_contact_enabled=False))
        puller = SyncPuller(backend_url="https://api.trwframework.com", api_key="key", client_id="c")
        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await puller.pull_intel_state()
        mock_client_cls.assert_not_called()
        assert result is None


# ---------------------------------------------------------------------------
# The pull loop never sends the bearer to an untrusted host, http or https
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend_url", ["http://not-trusted.example", "https://not-trusted.example"])
async def test_pull_withholds_bearer_from_untrusted_host(backend_url: str) -> None:
    from trw_mcp.sync.pull import SyncPuller

    _reset_config(TRWConfig())
    puller = SyncPuller(backend_url=backend_url, api_key="secret-key", client_id="c")

    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {"etag": "e1", "sync_hints": {}, "team_learnings": []}

    mock_cls = MagicMock()
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", mock_cls), structlog.testing.capture_logs() as logs:
        result = await puller.pull_intel_state()

    assert result is not None
    _, kwargs = mock_client.get.call_args
    assert "Authorization" not in kwargs["headers"]
    assert any(e.get("event") == "credential_withheld_untrusted_host" for e in logs)


async def test_pull_attaches_bearer_to_trusted_https_host() -> None:
    from trw_mcp.sync.pull import SyncPuller

    _reset_config(TRWConfig())
    puller = SyncPuller(backend_url="https://api.trwframework.com", api_key="secret-key", client_id="c")

    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {"etag": "e1", "sync_hints": {}, "team_learnings": []}

    mock_cls = MagicMock()
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", mock_cls):
        await puller.pull_intel_state()

    _, kwargs = mock_client.get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret-key"


# ---------------------------------------------------------------------------
# 24h update-check throttle
# ---------------------------------------------------------------------------


class TestUpdateCheckThrottle:
    def test_second_call_within_window_serves_cached_result_without_a_new_request(self) -> None:
        from trw_mcp.state.auto_upgrade import check_for_update

        _reset_config(TRWConfig(platform_url="https://api.trwframework.com"))
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"version": "1.2.3"}
        client = MagicMock()
        client.get.return_value = response
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        with patch("httpx.Client", return_value=client) as mock_client_cls:
            first = check_for_update()
            second = check_for_update()

        assert mock_client_cls.call_count == 1
        assert first == second

    def test_call_after_window_expiry_makes_a_new_request(self) -> None:
        from trw_mcp.state import auto_upgrade
        from trw_mcp.state.auto_upgrade import check_for_update

        _reset_config(TRWConfig(platform_url="https://api.trwframework.com"))
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"version": "1.2.3"}
        client = MagicMock()
        client.get.return_value = response
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        with patch("httpx.Client", return_value=client) as mock_client_cls:
            check_for_update()
            # Simulate the throttle window having elapsed.
            auto_upgrade._last_check_monotonic -= auto_upgrade._VERSION_CACHE_HOURS * 3600 + 1
            check_for_update()

        assert mock_client_cls.call_count == 2


# ---------------------------------------------------------------------------
# platform_auth_headers — the ONE bearer-attach chokepoint (P1-C)
# ---------------------------------------------------------------------------


class TestPlatformAuthHeaders:
    def test_empty_api_key_yields_no_header(self) -> None:
        assert platform_auth_headers("https://api.trwframework.com", "") == {}

    def test_untrusted_host_yields_no_header(self) -> None:
        assert platform_auth_headers("https://evil.example", "secret") == {}

    def test_trusted_host_yields_bearer_header(self) -> None:
        assert platform_auth_headers("https://api.trwframework.com", "secret") == {"Authorization": "Bearer secret"}

    def test_contact_disabled_yields_no_header_even_for_trusted_host(self) -> None:
        _reset_config(TRWConfig(platform_contact_enabled=False))
        try:
            assert platform_auth_headers("https://api.trwframework.com", "secret") == {}
        finally:
            _reset_config(TRWConfig())


# ---------------------------------------------------------------------------
# Per-site regression tests (P1-C): push.py, submit_feedback.py, sender.py,
# publisher.py, pipeline.py all attached the bearer unconditionally before
# this fix — a project-tracked backend_url/platform_urls pointed at an
# attacker host received a real TRW_PLATFORM_API_KEY.
# ---------------------------------------------------------------------------


async def test_sync_push_withholds_bearer_from_untrusted_backend() -> None:
    from trw_mcp.sync.push import SyncPusher

    pusher = SyncPusher(
        backend_url="https://evil.example",
        api_key="secret-key",
        client_id="c",
        learning_sharing_enabled=True,
    )

    class _Entry:
        sync_seq = 1

        def to_dict(self) -> dict[str, object]:
            return {"summary": "s", "importance": 0.5, "tags": []}

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"inserted": 1, "updated": 0, "skipped": 0, "errors": 0}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_cls = MagicMock()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", mock_cls):
        await pusher.push_learnings([_Entry()])  # type: ignore[list-item]

    _, kwargs = mock_client.post.call_args
    assert "Authorization" not in kwargs["headers"]


def test_submit_feedback_withholds_bearer_from_untrusted_backend() -> None:
    from trw_mcp.tools.submit_feedback import submit_feedback_via_http

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"id": "sub-1"}
    mock_client = MagicMock()
    mock_client.post.return_value = response
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    with patch("httpx.Client", return_value=mock_client):
        submit_feedback_via_http(
            backend_url="https://evil.example",
            api_key="secret-key",
            payload={"category": "feedback", "subject": "s", "message": "message body here"},
        )

    _, kwargs = mock_client.post.call_args
    assert "Authorization" not in kwargs["headers"]


def test_telemetry_sender_withholds_bearer_from_untrusted_url() -> None:
    from trw_mcp.telemetry.sender import BatchSender

    sender = BatchSender(
        platform_urls=["https://evil.example"],
        platform_api_key="secret-key",
        input_path=Path("/dev/null"),
        platform_telemetry_enabled=True,
    )

    response = MagicMock()
    response.status_code = 200
    mock_client = MagicMock()
    mock_client.post.return_value = response
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    with patch("httpx.Client", return_value=mock_client):
        sender._http_post("https://evil.example/v1/telemetry", [{"event": "x"}])

    _, kwargs = mock_client.post.call_args
    assert "Authorization" not in kwargs["headers"]


# ---------------------------------------------------------------------------
# Census: no OTHER module may build a raw "Bearer"/"Authorization" header.
#
# 2026-09-25 sol re-review round 2 found the first version of this census
# trusted a call by name alone and missed several shapes. Four checks now:
#   (a) a literal "Bearer " string anywhere — an f-string segment, OR a plain
#       string constant (module/class/function docstrings excluded so a
#       prose mention like "Bearer token for..." isn't a false positive).
#   (b) an "Authorization" dict key, in BOTH `{"Authorization": ...}` and
#       `dict(Authorization=...)` form, or an `.update(Authorization=...)`
#       call, whose value is not a call to a sanctioned helper.
#   (c) an `Authorization` subscript assignment (`headers["Authorization"] =
#       ...`) whose RHS is not a call to a sanctioned helper.
#   (d) a PROVENANCE heuristic on every call to a sanctioned helper: flags an
#       argument whose own source text references TRWConfig/MemoryConfig
#       (get_config(), cfg., .platform_api_key, ...) — closes the specific
#       gap sol named (a call to the right function NAME with a
#       config-sourced key, which defeats operator_release_bearer_value's
#       documented "explicit operator input only" invariant). This is a
#       source-text heuristic, not real data-flow/taint analysis, so it can
#       still be evaded by indirection (e.g. aliasing get_config to another
#       name first) — treated as raising the bar, not a formal proof.
# ---------------------------------------------------------------------------

#: Functions allowed to appear as the value of an "Authorization" key/assignment.
_ALLOWED_AUTH_HELPERS = frozenset(
    {
        "platform_auth_headers",
        "build_platform_headers",
        "operator_release_bearer_value",
    }
)

#: Files exempt from the census, each with a documented reason.
_CENSUS_EXEMPTIONS = {
    # The two sanctioned chokepoints themselves.
    "trw-mcp/src/trw_mcp/state/_platform_trust.py",
    "trw-memory/src/trw_memory/sync/_remote_common.py",
    # jev's judge HTTP client authenticates to an operator-configured
    # OpenAI-compatible judge endpoint (self._base_url/self._api_key) -- a
    # distinct credential and threat model from TRW_PLATFORM_API_KEY /
    # MemoryConfig.platform_url, which is what this census guards.
    "trw-memory/src/trw_memory/decisions/_jev_http.py",
}


#: Substrings in a helper-call ARGUMENT's source that indicate the api_key
#: came from TRWConfig/MemoryConfig rather than explicit operator/CLI/env
#: input — a config-sourced key defeats operator_release_bearer_value's
#: documented invariant even though the call itself is to the allowed name.
_CONFIG_SOURCED_ARG_MARKERS = ("get_config(", "cfg.", "config.", ".platform_api_key", "MemoryConfig")


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return None


def _docstring_node_ids(tree: ast.Module) -> set[int]:
    """id() of every Constant node that is a module/class/function docstring."""
    ids: set[int] = set()
    candidates: list[ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            candidates.append(node)
    for owner in candidates:
        body = owner.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            ids.add(id(body[0].value))
    return ids


def _flag_config_sourced_helper_call(node: ast.Call, source: str, rel: str, offenders: list[str]) -> None:
    """Heuristic provenance check: an allowed helper called with a config-derived key.

    Not real taint analysis — just a source-text substring match on each
    argument's own span — but it catches the concrete misuse pattern (passing
    ``cfg.platform_api_key``/``get_config()...`` into
    ``operator_release_bearer_value``, which defeats its documented "explicit
    operator input only" invariant even though the call name is allowed).
    """
    for arg in (*node.args, *(kw.value for kw in node.keywords)):
        try:
            arg_src = ast.get_source_segment(source, arg) or ""
        except Exception:  # pragma: no cover — get_source_segment is best-effort
            arg_src = ""
        if any(marker in arg_src for marker in _CONFIG_SOURCED_ARG_MARKERS):
            offenders.append(f"{rel}:{node.lineno} (helper called with a config-sourced argument: {arg_src})")


def _census_scan(root: Path, *, rel_prefix: str) -> list[str]:
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel_path = path.relative_to(root)
        rel = f"{rel_prefix}/{rel_path.as_posix()}"
        if rel in _CENSUS_EXEMPTIONS:
            continue
        # Test files assert the SHAPE of a header against a fake server/curl,
        # they are not themselves a production request sink.
        if "tests" in rel_path.parts or rel_path.parts[0] == "tests":
            continue
        text = path.read_text(encoding="utf-8")
        if (
            "Bearer" not in text
            and "Authorization" not in text
            and not any(name in text for name in _ALLOWED_AUTH_HELPERS)
        ):
            continue
        tree = ast.parse(text, filename=str(path))
        docstring_ids = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            # (a) a literal "Bearer " string anywhere: f-string segment, or a
            # plain (non-docstring) string constant.
            if isinstance(node, ast.JoinedStr):
                for value in node.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str) and "Bearer " in value.value:
                        offenders.append(f"{rel}:{node.lineno} (literal Bearer string)")
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "Bearer " in node.value
                and id(node) not in docstring_ids
            ):
                offenders.append(f"{rel}:{node.lineno} (literal Bearer string)")
            # (b) an "Authorization" dict key (both {"Authorization": ...}
            # and dict(Authorization=...)) whose value is not a call to a
            # sanctioned helper.
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value == "Authorization":
                        if _call_name(value) not in _ALLOWED_AUTH_HELPERS:
                            offenders.append(f"{rel}:{node.lineno} (Authorization dict key, not helper-sourced)")
            if isinstance(node, ast.Call):
                is_dict_or_update_call = _call_name(node) in {"dict", "update"} or (
                    isinstance(node.func, ast.Attribute) and node.func.attr == "update"
                )
                if is_dict_or_update_call:
                    for kw in node.keywords:
                        if kw.arg == "Authorization" and _call_name(kw.value) not in _ALLOWED_AUTH_HELPERS:
                            offenders.append(f"{rel}:{node.lineno} (Authorization keyword arg, not helper-sourced)")
                # (d) provenance heuristic — ONLY for operator_release_bearer_value.
                # platform_auth_headers/build_platform_headers are SUPPOSED to be
                # called with a TRWConfig/MemoryConfig-sourced key (that is their
                # whole job: apply the trust-gate to a config-derived credential);
                # operator_release_bearer_value's documented invariant is the
                # opposite -- both inputs must be explicit operator/CLI/env
                # input, never config -- so only its calls are checked.
                if _call_name(node) == "operator_release_bearer_value":
                    _flag_config_sourced_helper_call(node, text, rel, offenders)
        for assign in ast.walk(tree):
            if isinstance(assign, ast.Assign) and len(assign.targets) == 1:
                target = assign.targets[0]
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "Authorization"
                    and _call_name(assign.value) not in _ALLOWED_AUTH_HELPERS
                ):
                    offenders.append(f"{rel}:{assign.lineno} (Authorization subscript assign, not helper-sourced)")
    return offenders


def test_no_other_module_builds_a_raw_bearer_header() -> None:
    """AST census across trw-mcp/src, trw-memory/src, and scripts/.

    Every "Authorization" header construction and every literal "Bearer "
    string must be either inside the sanctioned helper itself or built via a
    call to it (``platform_auth_headers`` / ``build_platform_headers`` /
    ``operator_release_bearer_value``).
    """
    repo_root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    offenders += _census_scan(repo_root / "trw-mcp" / "src" / "trw_mcp", rel_prefix="trw-mcp/src/trw_mcp")
    offenders += _census_scan(repo_root / "trw-memory" / "src" / "trw_memory", rel_prefix="trw-memory/src/trw_memory")
    offenders += _census_scan(repo_root / "scripts", rel_prefix="scripts")
    assert offenders == [], f"raw Authorization/Bearer construction outside the sanctioned helpers: {offenders}"


# ---------------------------------------------------------------------------
# P1-C round 2 (2026-09-25 sol re-review, finding #2): platform_contact_enabled
# must be a hard stop -- no request attempted -- at every AUTOMATIC egress
# sink, not just header suppression.
# ---------------------------------------------------------------------------


async def test_sync_push_learnings_makes_no_request_when_contact_disabled() -> None:
    from trw_mcp.sync.push import SyncPusher

    _reset_config(TRWConfig(platform_contact_enabled=False))
    pusher = SyncPusher(
        backend_url="https://api.trwframework.com",
        api_key="secret",
        client_id="c",
        learning_sharing_enabled=True,
    )

    class _Entry:
        sync_seq = 1

        def to_dict(self) -> dict[str, object]:
            return {"summary": "s", "importance": 0.5, "tags": []}

    with patch("httpx.AsyncClient") as mock_client_cls:
        result = await pusher.push_learnings([_Entry()])  # type: ignore[list-item]
    mock_client_cls.assert_not_called()
    assert result.pushed == 0


async def test_sync_push_outcomes_makes_no_request_when_contact_disabled() -> None:
    from trw_mcp.sync.push import SyncPusher

    _reset_config(TRWConfig(platform_contact_enabled=False))
    pusher = SyncPusher(
        backend_url="https://api.trwframework.com",
        api_key="secret",
        client_id="c",
        platform_telemetry_enabled=True,
    )
    with patch("httpx.AsyncClient") as mock_client_cls:
        result = await pusher.push_outcomes([{"outcome": "x"}])
    mock_client_cls.assert_not_called()
    assert result.pushed == 0


def test_telemetry_sender_makes_no_request_when_contact_disabled() -> None:
    from trw_mcp.telemetry.sender import BatchSender

    _reset_config(TRWConfig(platform_contact_enabled=False))
    sender = BatchSender(
        platform_urls=["https://api.trwframework.com"],
        platform_api_key="secret",
        input_path=Path("/dev/null"),
        platform_telemetry_enabled=True,
    )
    with patch("httpx.Client") as mock_client_cls:
        result = sender.send()
    mock_client_cls.assert_not_called()
    assert result["skipped_reason"] == "platform_contact_disabled"


def test_publish_learnings_makes_no_request_when_contact_disabled(tmp_path: Path) -> None:
    from trw_mcp.telemetry import publisher

    _reset_config(
        TRWConfig(
            platform_contact_enabled=False,
            platform_url="https://api.trwframework.com",
            learning_sharing_enabled=True,
        )
    )
    with patch("httpx.Client") as mock_client_cls:
        result = publisher.publish_learnings()
    mock_client_cls.assert_not_called()
    assert result["skipped_reason"] == "platform_contact_disabled"


def test_telemetry_pipeline_flush_makes_no_request_when_contact_disabled(pipeline_cls: object) -> None:
    from unittest.mock import MagicMock

    _reset_config(
        TRWConfig(
            platform_contact_enabled=False,
            platform_url="https://api.trwframework.com",
            platform_telemetry_enabled=True,
        )
    )
    pipeline = pipeline_cls()  # type: ignore[operator]
    pipeline._queue.append({"tool": "x"})
    pipeline._writer = MagicMock()
    with patch("httpx.Client") as mock_client_cls:
        result = pipeline.flush_now()
    mock_client_cls.assert_not_called()
    assert result.get("skipped_reason") == "platform_contact_disabled"


# ---------------------------------------------------------------------------
# rc10 C12 P1: remote recall honours the contact switch (the query never leaves)
# ---------------------------------------------------------------------------


def _remote_recall_fetches(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Run recall's remote leg with sharing on and the platform fetch mocked; the mock records any request."""
    from trw_mcp.tools._recall_impl import _augment_with_remote

    fetch = MagicMock(return_value=MagicMock(status="ok", results=[], fetched=0, refused=0))
    monkeypatch.setattr("trw_memory.sync.fetch_shared_memories", fetch)
    monkeypatch.setattr(
        "trw_mcp.state._store_selection.selected_store", lambda _trw_dir: (MagicMock(admit_shared=None), None)
    )
    rows, _status = _augment_with_remote("secret query text", [{"id": "L-local"}])
    assert rows[0]["id"] == "L-local"
    return fetch


def test_remote_recall_sends_nothing_when_contact_disabled_in_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = Path(os.environ["HOME"]) / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("platform_contact_enabled: false\n", encoding="utf-8")
    _reset_config()
    assert platform_contact_enabled() is False
    _remote_recall_fetches(monkeypatch).assert_not_called()


def test_remote_recall_sends_nothing_when_contact_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_PLATFORM_CONTACT_ENABLED", "false")
    _reset_config()
    assert platform_contact_enabled() is False
    _remote_recall_fetches(monkeypatch).assert_not_called()


def test_remote_recall_still_asks_the_platform_when_contact_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_config(TRWConfig(platform_contact_enabled=True))
    fetch = _remote_recall_fetches(monkeypatch)
    fetch.assert_called_once()
    assert fetch.call_args.args[0] == "secret query text"


def test_feedback_sends_nothing_when_contact_disabled() -> None:
    from trw_mcp.tools.submit_feedback import submit_feedback_via_http

    _reset_config(TRWConfig(platform_contact_enabled=False))
    payload = cast("Any", {"kind": "bug", "title": "t", "body": "b", "metadata": {}})
    with patch("httpx.Client") as mock_client_cls:
        result = submit_feedback_via_http(backend_url="https://api.trwframework.com", api_key="k", payload=payload)
    mock_client_cls.assert_not_called()
    assert not result.success
    assert "platform_contact_enabled" in result.error


# ---------------------------------------------------------------------------
# Census: every module that opens an outbound connection is gated by the
# contact switch or named here as an operator-typed exception. A new call
# site fails until someone decides which it is.
# ---------------------------------------------------------------------------

#: Modules whose outbound requests must be stopped by ``platform_contact_enabled()``.
_CONTACT_GATED = {
    "state/auto_upgrade.py",
    "sync/pull.py",
    "sync/push.py",
    "telemetry/pipeline.py",
    "telemetry/publisher.py",
    "telemetry/sender.py",
    "tools/_recall_impl.py",
    "tools/submit_feedback.py",
}
#: Modules outside the switch, each with the reason.
_CONTACT_EXEMPT = {
    "cli/auth.py": "`trw-mcp auth login`: the operator types it, with the URL to log in to",
    "clients/llm.py": "a local Ollama or the Anthropic SDK, not the TRW platform",
    "server/_doctor_backend_connectivity.py": "`trw-mcp doctor` probes an explicitly configured backend_url only",
    "server/_subcommands_release.py": "`trw-mcp release --push` with an operator-supplied --backend-url",
}
_OUTBOUND_ATTRS = {"Client", "AsyncClient", "get", "post", "put", "patch", "delete", "request", "stream"}
_OUTBOUND_NAMES = {"urlopen", "fetch_shared_memories"}


def _opens_a_connection(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _OUTBOUND_NAMES:
            return True
        if isinstance(func, ast.Attribute):
            if func.attr in _OUTBOUND_NAMES:
                return True
            if (
                isinstance(func.value, ast.Name)
                and func.value.id in {"httpx", "requests"}
                and func.attr in _OUTBOUND_ATTRS
            ):
                return True
    return False


def test_every_outbound_module_is_gated_by_the_contact_switch_or_named_exempt() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
    found = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "code_index" not in path.parts and _opens_a_connection(ast.parse(path.read_text(encoding="utf-8")))
    }
    unregistered = found - _CONTACT_GATED - set(_CONTACT_EXEMPT)
    assert not unregistered, (
        f"outbound call sites with no contact-switch decision (gate or exempt them): {unregistered}"
    )
    ungated = {rel for rel in _CONTACT_GATED if "platform_contact_enabled" not in (root / rel).read_text("utf-8")}
    assert not ungated, f"registered as gated but never check platform_contact_enabled(): {ungated}"
    assert not (_CONTACT_GATED | set(_CONTACT_EXEMPT)) - found, "a registered module no longer opens a connection"
