"""A dispatched CLI's OAuth login, read as metadata only, and the host lock around its refresh (PRD-CORE-304).

Every dispatched codex or claude child inherits the real HOME, so concurrent children share one login
whose refresh token is single-use and rotates: the first child to refresh spends it and the others fail
(openai/codex#15502, anthropics/claude-code#56339). The CLI refreshes inside its own process, at start or
mid-run, so a lock released at launch would not help: a run that could see its token expire holds the
per-client lock until the child exits, retries included. A run far from expiry takes no lock and stays
parallel. The lock is advisory: a CLI process trw did not launch is outside it.

Nothing here reads, logs or returns a credential: only the account id's digest, the access token's
``exp`` claim (decoded in memory) and file presence. The macOS keychain is never queried, because
reading it can raise a system prompt: a keychain login reports ``keychain`` and no expiry.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, TypeVar

from trw_mcp._locking import _lock_ex_nb, _lock_un
from trw_mcp.dispatch._client_specs import CLIENT_SPECS, UnknownClientError, client_spec_for

__all__ = [
    "CredentialLockTimeout",
    "CredentialState",
    "credential_lock",
    "credential_report",
    "credential_state",
    "run_guarded",
]

T = TypeVar("T")

LoginState = Literal["logged_in", "absent", "keychain", "unreadable", "not_oauth"]
#: Extra time past a run's own timeout that still counts as "could expire during it".
_MARGIN = timedelta(minutes=5)
_POLL_SECONDS = 0.5
#: The longest a dispatch waits for another one's near-expiry run to finish.
_MAX_WAIT_SECONDS = 1800.0


class CredentialLockTimeout(TimeoutError):
    """Another dispatch held the client's credential lock for longer than this one would wait."""


@dataclass(frozen=True)
class CredentialState:
    """What can be said about a client's login without reading a secret."""

    client: str
    state: LoginState
    expires_at: datetime | None = None
    identity: str | None = None  # the first 8 hex chars of the account id's SHA-256

    def could_expire_within(self, seconds: float, now: datetime | None = None) -> bool:
        """True for an OAuth login whose expiry is unknown or falls within *seconds* (plus a margin)."""
        if self.state not in ("logged_in", "keychain", "unreadable"):
            return False
        now = now or datetime.now(timezone.utc)
        return self.expires_at is None or self.expires_at <= now + timedelta(seconds=seconds) + _MARGIN


def _jwt_expiry(token: object) -> datetime | None:
    """The ``exp`` claim of a JWT, decoded in memory; ``None`` for anything else. The token never leaves."""
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    payload = token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:  # trw-fail-silent-allow: an unreadable token has no known expiry, which locks
        return None
    return _from_epoch(claims.get("exp") if isinstance(claims, dict) else None)


def _from_epoch(value: object, *, per_second: int = 1) -> datetime | None:
    """A UTC time from an epoch *value* in 1/*per_second* units; ``None`` (unknown, which locks) for a bool,
    a non-number, or anything out of range."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value / per_second, timezone.utc)
    except (OverflowError, OSError, ValueError):  # trw-fail-silent-allow: an impossible expiry is an unknown one
        return None


def credential_state(client: str, home: Path) -> CredentialState:
    """*client*'s login under *home*, as metadata; where it lives comes from its ClientSpec."""
    try:
        spec = client_spec_for(client)
    except UnknownClientError:  # trw-fail-silent-allow: an unregistered client has no login file to race over
        return CredentialState(client, "not_oauth")
    login = spec.oauth_login
    if login is None:
        return CredentialState(client, "not_oauth")
    path = home / login.file  # the child gets HOME and no override of it (_env), so this is the login it uses
    if not path.is_file():
        # a claude_ai_oauth login lives in the macOS keychain when this file is absent; it is never read.
        return CredentialState(client, "keychain" if login.shape == "claude_ai_oauth" else "absent")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # trw-fail-silent-allow: reported as unreadable, which locks
        return CredentialState(client, "unreadable")
    section = (
        data.get("tokens" if login.shape == "openai_tokens" else "claudeAiOauth") if isinstance(data, dict) else None
    )
    section = section if isinstance(section, dict) else {}
    if login.shape == "openai_tokens":
        account, expires_at = section.get("account_id"), _jwt_expiry(section.get("access_token"))
    else:
        account, millis = section.get("accountUuid"), section.get("expiresAt")
        expires_at = _from_epoch(millis, per_second=1000)
    identity = hashlib.sha256(account.encode()).hexdigest()[:8] if isinstance(account, str) and account else None
    return CredentialState(client, "logged_in", expires_at, identity)


def lock_path(client: str) -> Path:
    """The per-client lock file every trw process on this host shares."""
    return Path.home() / ".trw" / "runtime" / f"cred-lock-{client}.lock"


@contextlib.contextmanager
def credential_lock(client: str, *, wait_seconds: float) -> Iterator[Callable[[], None]]:
    """Hold *client*'s host-wide credential lock, yielding a ``release`` that may end the hold early (from any
    thread); :class:`CredentialLockTimeout` after *wait_seconds*."""
    path = lock_path(client)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                _lock_ex_nb(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise CredentialLockTimeout(
                        f"{client}'s credential lock was held past {wait_seconds:.0f}s"
                    ) from None
                time.sleep(_POLL_SECONDS)
        guard, held = threading.Lock(), [True]

        def release() -> None:
            with guard:
                if held[0]:
                    held[0] = False
                    _lock_un(fd)

        try:
            yield release
        finally:
            release()
    finally:
        os.close(fd)


def run_guarded(client: str, timeout_s: float, run: Callable[[], T], refuse: Callable[[str], T]) -> T:
    """*run*, holding *client*'s credential lock through it if its login could expire within *timeout_s*.

    A run far from expiry takes no lock. A wait past the bound returns ``refuse(reason)`` instead of hanging.
    """
    if not credential_state(client, Path.home()).could_expire_within(timeout_s):
        return run()
    try:
        with credential_lock(
            client, wait_seconds=min(timeout_s / 2, _MAX_WAIT_SECONDS)
        ) as release:  # run keeps the rest
            refreshed = threading.Event()
            watcher = threading.Thread(
                target=_release_once_fresh, args=(client, timeout_s, release, refreshed), daemon=True
            )
            watcher.start()  # a login refreshed by this child, or by the holder this one waited on, frees the others
            try:
                return run()
            finally:
                refreshed.set()
    except CredentialLockTimeout as exc:
        return refuse(str(exc))


def _release_once_fresh(client: str, timeout_s: float, release: Callable[[], None], done: threading.Event) -> None:
    """Release the credential lock as soon as the login no longer expires within *timeout_s* (sol r2)."""
    while not done.is_set():
        if not credential_state(client, Path.home()).could_expire_within(timeout_s):
            release()
            return
        done.wait(_POLL_SECONDS)


def _lock_held(client: str) -> bool:
    """Whether another process holds *client*'s credential lock now; never creates the lock file."""
    try:
        fd = os.open(lock_path(client), os.O_RDWR)
    except OSError:  # trw-fail-silent-allow: no lock file means nobody has taken it
        return False
    try:
        _lock_ex_nb(fd)
    except OSError:
        return True
    else:
        _lock_un(fd)
        return False
    finally:
        os.close(fd)


def _expiry_text(minutes: object) -> str:
    """ ", expires in N min", or ", expired N min ago" for a token already past its expiry (never "expires in -300 min")."""
    if not isinstance(minutes, int):
        return ""
    return f", expires in {minutes} min" if minutes >= 0 else f", expired {-minutes} min ago"


def credential_report(home: Path | None = None) -> tuple[str, str, list[dict[str, object]]]:
    """``(status, message, rows)`` for each OAuth dispatch client (PRD-CORE-304-FR04): metadata only, never a secret.

    WARN when a readable login expires within 10 minutes or a login file is unreadable; a keychain login
    is reported as not read, never as signed in.
    """
    now = datetime.now(timezone.utc)
    rows: list[dict[str, object]] = []
    for spec in CLIENT_SPECS.values():
        if spec.oauth_login is None:
            continue
        state = credential_state(spec.client_id, home or Path.home())
        minutes = round((state.expires_at - now).total_seconds() / 60) if state.expires_at else None
        rows.append(
            {
                "client": spec.client_id,
                "state": state.state,
                "expires_in_min": minutes,
                "identity": state.identity,
                "lock": "held" if _lock_held(spec.client_id) else "free",
            }
        )
    soon = [row for row in rows if isinstance(row["expires_in_min"], int) and row["expires_in_min"] < 10]
    status = "WARN" if soon or any(row["state"] == "unreadable" for row in rows) else "PASS"
    labels = {"keychain": "keychain (not read)", "logged_in": "logged in"}
    message = "; ".join(
        f"{row['client']}: {labels.get(str(row['state']), row['state'])}"
        + _expiry_text(row["expires_in_min"])
        + (", lock held" if row["lock"] == "held" else "")
        for row in rows
    )
    return status, message, rows
