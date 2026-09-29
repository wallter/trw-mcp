"""PRD-CORE-304-FR02: concurrent dispatches near a login's expiry refresh it once, behind a host-wide lock."""

from __future__ import annotations

import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests._dispatch_host import install_stub
from trw_mcp.dispatch import dispatch
from trw_mcp.dispatch._credentials import credential_lock, credential_state
from trw_mcp.dispatch._types import DispatchRequest

_SECRET = "access-token-canary-must-never-surface"


def _jwt(expires: datetime) -> str:
    def part(obj: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': int(expires.timestamp()), 'sub': _SECRET})}.sig"


def _codex_login(home: Path, expires: datetime) -> None:
    path = home / ".codex" / "auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tokens = {"access_token": _jwt(expires), "refresh_token": _SECRET, "account_id": "acct-1"}
    path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": tokens}))


def _claude_login(home: Path, expires: datetime) -> None:
    path = home / ".claude" / ".credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    oauth = {"accessToken": _SECRET, "expiresAt": int(expires.timestamp() * 1000), "accountUuid": "acct-1"}
    path.write_text(json.dumps({"claudeAiOauth": oauth}))


def test_a_login_is_read_as_metadata_only(tmp_path: Path) -> None:
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    _codex_login(tmp_path, expires)

    state = credential_state("codex", tmp_path)

    assert (state.state, state.identity, int(state.expires_at.timestamp())) == (  # type: ignore[union-attr]
        "logged_in",
        state.identity,
        int(expires.timestamp()),
    )
    assert state.identity is not None and len(state.identity) == 8
    assert _SECRET not in repr(state) and "acct-1" not in repr(state)


def test_only_a_login_that_could_expire_during_the_run_locks(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    _codex_login(tmp_path, now + timedelta(hours=3))
    assert not credential_state("codex", tmp_path).could_expire_within(600)
    _codex_login(tmp_path, now + timedelta(minutes=12))
    assert credential_state("codex", tmp_path).could_expire_within(600)
    assert not credential_state("codex", tmp_path / "nobody").could_expire_within(600)  # no login: nothing to race
    assert credential_state("claude", tmp_path / "nobody").state == "keychain"  # never queried; expiry unknown
    assert credential_state("claude", tmp_path / "nobody").could_expire_within(600)
    assert not credential_state("grok", tmp_path).could_expire_within(600)


def _racy_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A fake claude whose expired token is refreshed by whichever launch sees it first; racy without a lock."""
    expired, refreshes = tmp_path / "token-expired", tmp_path / "refreshes"
    expired.write_text("1")
    refresh = f'if [ -f "{expired}" ]; then sleep 0.3; echo x >> "{refreshes}"; rm -f "{expired}"; fi\n'
    install_stub(tmp_path, monkeypatch, refresh + """echo '{"result": "Verdict: PASS"}'\n""")
    return expired, refreshes


def test_ten_concurrent_dispatches_near_expiry_refresh_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _claude_login(Path.home(), datetime.now(timezone.utc) + timedelta(minutes=2))
    _expired, refreshes = _racy_stub(tmp_path, monkeypatch)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _i: dispatch(DispatchRequest(client="claude", prompt="review")), range(10)))

    assert all(result.ok for result in results)
    assert refreshes.read_text().count("x") == 1


def test_a_login_far_from_expiry_takes_no_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _claude_login(Path.home(), datetime.now(timezone.utc) + timedelta(hours=6))
    _racy_stub(tmp_path, monkeypatch)
    with credential_lock("claude", wait_seconds=1):  # another dispatch holds it
        started = time.monotonic()
        result = dispatch(DispatchRequest(client="claude", prompt="review"))
    assert result.ok and time.monotonic() - started < 5


def test_a_lock_wait_past_its_bound_is_a_named_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _claude_login(Path.home(), datetime.now(timezone.utc) + timedelta(minutes=2))
    _racy_stub(tmp_path, monkeypatch)
    monkeypatch.setattr("trw_mcp.dispatch._credentials._MAX_WAIT_SECONDS", 0.5)
    holder_ready, release = threading.Event(), threading.Event()

    def hold() -> None:
        with credential_lock("claude", wait_seconds=1):
            holder_ready.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    holder_ready.wait(5)
    try:
        result = dispatch(DispatchRequest(client="claude", prompt="review"))
    finally:
        release.set()
        holder.join()

    assert (result.ok, result.error_class) == (False, "credential_refresh_conflict")
    assert "credential lock" in (result.last_error or "")
    assert result.read_only_enforced is False, "no child ran, so nothing was confined (B71-26 D6)"


# ── PRD-CORE-304-FR04: credential health, as metadata ────────────────────────


def test_doctor_shows_each_oauth_clients_login_and_expiry_and_no_secret(tmp_path: Path) -> None:
    from trw_mcp.server._subcommands_doctor import _check_dispatch_credentials

    _codex_login(Path.home(), datetime.now(timezone.utc) + timedelta(minutes=45))

    row = _check_dispatch_credentials(tmp_path, None)  # type: ignore[arg-type]

    by_client = {entry["client"]: entry for entry in row.data}  # type: ignore[index]
    assert by_client["codex"]["state"] == "logged_in"
    assert 43 <= by_client["codex"]["expires_in_min"] <= 45  # type: ignore[operator]
    assert by_client["claude"]["state"] == "keychain"
    assert "keychain (not read)" in row.message and row.status == "PASS"
    rendered = json.dumps({"message": row.message, "data": row.data}, default=str)
    assert _SECRET not in rendered and "acct-1" not in rendered


def test_a_login_about_to_expire_warns_and_a_held_lock_is_shown(tmp_path: Path) -> None:
    from trw_mcp.dispatch._credentials import credential_report

    _codex_login(Path.home(), datetime.now(timezone.utc) + timedelta(minutes=3))
    with credential_lock("codex", wait_seconds=1):
        status, message, rows = credential_report()
    assert status == "WARN"
    assert "codex: logged in, expires in" in message and "lock held" in message
    assert next(row for row in rows if row["client"] == "codex")["lock"] == "held"


def test_dispatch_status_with_no_job_id_reports_credential_health() -> None:
    from trw_mcp.tools.dispatch import _credential_status

    _codex_login(Path.home(), datetime.now(timezone.utc) + timedelta(hours=2))
    answer = _credential_status()
    assert answer["status"] == "pass"
    assert {row["client"] for row in answer["credentials"]} >= {"codex", "claude"}  # type: ignore[union-attr]
    assert _SECRET not in json.dumps(answer, default=str)


@pytest.mark.parametrize("exp", [10**20, -(10**20), float("inf"), True])
def test_an_impossible_expiry_is_an_unknown_one_which_locks(exp: object) -> None:
    """sol r1: an out-of-range exp or expiresAt must not raise inside dispatch or doctor."""
    home = Path.home()
    token = _jwt(datetime.now(timezone.utc)).split(".")
    claims = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    path = home / ".codex" / "auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tokens = {"access_token": f"{token[0]}.{claims}.sig", "account_id": "acct-1"}
    path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": tokens}))
    claude = home / ".claude" / ".credentials.json"
    claude.parent.mkdir(parents=True, exist_ok=True)
    claude.write_text(json.dumps({"claudeAiOauth": {"expiresAt": exp}}))

    for client in ("codex", "claude"):
        state = credential_state(client, home)
        assert (state.state, state.expires_at) == ("logged_in", None)
        assert state.could_expire_within(600)


def test_a_long_first_run_frees_the_others_once_it_has_refreshed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sol r2: waiters must not queue behind the refreshing child's whole run once the login is fresh."""
    login = Path.home() / ".claude" / ".credentials.json"
    _claude_login(Path.home(), datetime.now(timezone.utc) + timedelta(minutes=2))
    fresh = json.dumps({"claudeAiOauth": {"expiresAt": int((time.time() + 6 * 3600) * 1000), "accountUuid": "acct-1"}})
    expired, refreshes = tmp_path / "token-expired", tmp_path / "refreshes"
    expired.write_text("1")
    refresh = (
        f'if [ -f "{expired}" ]; then rm -f "{expired}"; echo x >> "{refreshes}"; '
        f"echo '{fresh}' > \"{login}\"; sleep 3; fi\n"
    )
    install_stub(tmp_path, monkeypatch, refresh + """echo '{"result": "Verdict: PASS"}'\n""")

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(lambda _i: dispatch(DispatchRequest(client="claude", prompt="r", timeout_s=4)), range(3))
        )

    assert [r.ok for r in results] == [True, True, True]  # none timed out waiting 2 s behind a 3 s run
    assert refreshes.read_text().count("x") == 1
    assert time.monotonic() - started < 6


def test_the_lock_reads_the_login_the_child_uses_not_the_parents_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sol r2: the child gets HOME and no CODEX_HOME (_env), so a parent override must not hide its login."""
    now = datetime.now(timezone.utc)
    _codex_login(Path.home(), now + timedelta(minutes=5))
    elsewhere = tmp_path / "parent-codex-home"
    _codex_login(elsewhere, now + timedelta(hours=8))
    monkeypatch.setenv("CODEX_HOME", str(elsewhere / ".codex"))

    assert credential_state("codex", Path.home()).could_expire_within(600)
