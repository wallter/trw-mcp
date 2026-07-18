"""Device authorization flow for TRW CLI (RFC 8628).

Provides ``device_auth_login``, ``device_auth_logout``, ``device_auth_status``,
using ONLY Python stdlib (no requests/httpx).

Matches the installer UI patterns from ``install-trw.template.py``.
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trw_mcp.models.config._credentials import (
    credentials_path_for,
    read_key_from_file,
    write_credentials_key,
)

# Config-file helpers extracted to a sibling (PRD-SEC-005, 350-eLOC gate);
# re-exported here so existing callers/tests keep importing from ``auth``.
from ._auth_config import (
    _read_config_lines as _read_config_lines,
)
from ._auth_config import (
    _save_config_field as _save_config_field,
)
from ._auth_config import (
    device_auth_logout as device_auth_logout,
)

# Spinner extracted to ``_auth_spinner.py`` (PRD-DIST-243 Phase 1,
# cycle 22) to keep this module under the 350-effective-LOC threshold.
from ._auth_spinner import _Spinner

# ── ANSI colors (mirrors installer constants) ────────────────────────

_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

GREEN = "\033[0;32m" if _USE_COLOR else ""
YELLOW = "\033[0;33m" if _USE_COLOR else ""
RED = "\033[0;31m" if _USE_COLOR else ""
BOLD = "\033[1m" if _USE_COLOR else ""
DIM = "\033[2m" if _USE_COLOR else ""
NC = "\033[0m" if _USE_COLOR else ""

# ── TTY input helper (matches installer _open_tty) ───────────────────


def _open_tty() -> TextIO | None:
    """Open /dev/tty for reading, or None if unavailable."""
    with contextlib.suppress(OSError):
        return open("/dev/tty")
    return None


# ── HTTP helpers (stdlib only) ────────────────────────────────────────


def _post_json(url: str, payload: dict[str, object], timeout: int = 10) -> dict[str, object]:
    """POST JSON to *url* and return parsed response.

    Raises ``HTTPError`` on HTTP errors and ``URLError`` on network errors.
    """
    body = json.dumps(payload).encode("utf-8")
    req = Request(  # noqa: S310 — URL comes from operator-supplied api_url
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return dict(json.loads(resp.read().decode("utf-8")))


# ── Device auth flow (RFC 8628) ──────────────────────────────────────


def _format_countdown(seconds: int) -> str:
    """Format *seconds* as ``MM:SS``."""
    m, s = divmod(max(0, seconds), 60)
    return f"{m}:{s:02d}"


def device_auth_login(api_url: str, interactive: bool = True) -> dict[str, object] | None:
    """Perform RFC 8628 device authorization flow.

    1. Request device code from ``{api_url}/v1/auth/device/code``.
    2. Display verification URI and user code.
    3. Poll ``{api_url}/v1/auth/device/token`` until authorized or expired.

    Returns the token response dict on success, or ``None`` on failure.
    Only uses Python stdlib (``urllib.request``, ``webbrowser``).
    """
    api_url = api_url.rstrip("/")

    # Step 1: Request device code
    try:
        code_resp = _post_json(
            f"{api_url}/v1/auth/device/code",
            {"client_id": "trw-cli"},
        )
    except (HTTPError, URLError, OSError) as exc:
        if interactive:
            print(f"\n  {RED}Error:{NC} Could not reach {api_url}", file=sys.stderr)
            print(f"  {DIM}{exc}{NC}", file=sys.stderr)
        return None

    device_code = str(code_resp.get("device_code", ""))
    user_code = str(code_resp.get("user_code", ""))
    verification_uri = str(code_resp.get("verification_uri", ""))
    verification_uri_complete = str(code_resp.get("verification_uri_complete", verification_uri))
    interval = int(str(code_resp.get("interval", 5)))
    expires_in = int(str(code_resp.get("expires_in", 900)))

    if not device_code or not user_code:
        if interactive:
            print(f"\n  {RED}Error:{NC} Invalid device code response", file=sys.stderr)
        return None

    # Step 2: Display URL immediately, then try to open browser in background
    if interactive:
        print()
        print(f"  {BOLD}Open this URL to authenticate:{NC}")
        print()
        print(f"    {GREEN}{verification_uri_complete}{NC}")
        print()

        # Open browser in a daemon thread to avoid blocking on Linux.
        # webbrowser.open() can hang for seconds (or indefinitely) when
        # the BROWSER env var is set or xdg-open waits for the process.
        # See: https://github.com/python/cpython/issues/39357
        #      https://github.com/ipython/ipython/pull/936
        def _open_browser() -> None:
            with contextlib.suppress(Exception):
                webbrowser.open(verification_uri_complete)

        browser_thread = threading.Thread(target=_open_browser, daemon=True)
        browser_thread.start()

        # Give the browser a moment to open, then report status
        browser_thread.join(timeout=2.0)
        if not browser_thread.is_alive():
            print(f"  {DIM}(Browser opened — verify the code matches){NC}")
        else:
            print(f"  {DIM}(Opening browser...){NC}")

    # Step 3: Poll for token
    deadline = time.monotonic() + expires_in
    spinner: _Spinner | None = None

    if interactive:
        remaining = int(deadline - time.monotonic())
        spinner = _Spinner(f"Waiting for authorization (expires in {_format_countdown(remaining)})...")
        spinner.start()

    try:
        result = _poll_for_token(
            api_url=api_url,
            device_code=device_code,
            interval=interval,
            deadline=deadline,
            interactive=interactive,
            spinner=spinner,
        )
    finally:
        if spinner is not None:
            spinner.stop()
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    if result is not None and interactive:
        email = result.get("user_email", "")
        if email:
            print(f"  {GREEN}\u2713{NC} Authenticated as {BOLD}{email}{NC}")
        else:
            print(f"  {GREEN}\u2713{NC} Authentication successful")

    return result


def _poll_for_token(
    *,
    api_url: str,
    device_code: str,
    interval: int,
    deadline: float,
    interactive: bool,
    spinner: _Spinner | None,
) -> dict[str, object] | None:
    """Poll the token endpoint until success, expiry, or denial.

    Handles RFC 8628 error codes:
    - ``authorization_pending`` -- keep polling
    - ``slow_down`` -- permanently increase interval by 5s
    - ``expired_token`` -- stop, return None
    - ``access_denied`` -- stop, return None

    On network errors, applies exponential backoff (double interval, max 30s).
    """
    poll_interval = interval

    while time.monotonic() < deadline:
        time.sleep(poll_interval)

        # Update spinner countdown
        if spinner is not None:
            remaining = int(deadline - time.monotonic())
            spinner.message = f"Waiting for authorization (expires in {_format_countdown(remaining)})..."

        try:
            token_resp = _post_json(
                f"{api_url}/v1/auth/device/token",
                {
                    "client_id": "trw-cli",
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            # Success
            return token_resp

        except HTTPError as exc:
            # Parse error response body
            error_code = ""
            try:
                err_body = json.loads(exc.read().decode("utf-8"))
                error_code = str(err_body.get("error", ""))
            except (json.JSONDecodeError, OSError):
                pass  # Error body unparsable — error_code stays "" and falls through to unknown handler

            if error_code == "authorization_pending":
                continue
            if error_code == "slow_down":
                poll_interval += 5
                continue
            if error_code == "expired_token":
                if interactive:
                    print(f"\n  {RED}Error:{NC} Authorization expired. Please try again.")
                return None
            if error_code == "access_denied":
                if interactive:
                    print(f"\n  {RED}Error:{NC} Authorization denied.")
                return None
            # Unknown HTTP error
            if interactive:
                print(
                    f"\n  {RED}Error:{NC} Unexpected response (HTTP {exc.code}): {error_code or 'unknown'}",
                )
            return None

        except (URLError, OSError):
            # Network error: exponential backoff
            poll_interval = min(poll_interval * 2, 30)
            continue

    # Deadline reached
    if interactive:
        print(f"\n  {RED}Error:{NC} Authorization expired. Please try again.")
    return None


# ── Organization selector ─────────────────────────────────────────────


def device_auth_status(config_path: Path, api_url: str) -> dict[str, object]:
    """Check authentication status from config file.

    Returns ``{"authenticated": True, "key_prefix": "trw_dk_...", "org_name": ..., "user_email": ...}``
    if a platform API key is configured, otherwise ``{"authenticated": False}``.
    """
    # PRD-SEC-005: the bearer credential now lives in the ignored
    # credentials.yaml; fall back to config.yaml for legacy installs.
    key_value = read_key_from_file(credentials_path_for(config_path))
    if not key_value:
        key_value = read_key_from_file(config_path)

    lines = _read_config_lines(config_path)
    if lines is None and not key_value:
        return {"authenticated": False}

    key_prefix = ""
    if key_value:
        key_prefix = key_value[:10] + "..." if len(key_value) > 10 else key_value

    org_name = ""
    user_email = ""

    for line in lines or []:
        stripped = line.rstrip("\n").lstrip()
        if stripped.startswith("platform_org_name:"):
            org_name = stripped.partition(":")[2].strip().strip('"').strip("'")
        elif stripped.startswith("platform_user_email:"):
            user_email = stripped.partition(":")[2].strip().strip('"').strip("'")

    if not key_prefix:
        return {"authenticated": False}

    result: dict[str, object] = {
        "authenticated": True,
        "key_prefix": key_prefix,
        "config_path": str(config_path),
        "api_url": api_url,
    }
    if org_name:
        result["org_name"] = org_name
    if user_email:
        result["user_email"] = user_email
    return result


# ── CLI entry points (called from server subcommand dispatch) ─────────


def run_auth_login(api_url: str, config_path: Path) -> int:
    """CLI handler for ``trw-mcp auth login``. Returns exit code."""
    result = device_auth_login(api_url, interactive=True)
    if result is None:
        return 1

    # Report the organization the key was ACTUALLY issued for. The key's
    # org_id is fixed server-side at /auth/device/approve time — an interactive
    # picker here was a no-op that could display an org the key is NOT scoped
    # to (removed 2026-07-11; the browser approve page is where org is chosen).
    org_name_display = str(result.get("org_name", ""))
    if org_name_display:
        print(f"  {GREEN}\u2713{NC} Organization: {BOLD}{org_name_display}{NC}")

    # Save API key + metadata. The bearer credential goes to the ignored,
    # 0600 credentials.yaml (PRD-SEC-005-FR01) \u2014 never to the git-tracked
    # config.yaml. Non-secret metadata (org_name, user_email) stays in
    # config.yaml so `auth status` keeps working.
    api_key = result.get("api_key", "")
    if api_key and isinstance(api_key, str):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        credentials_path = credentials_path_for(config_path)
        write_credentials_key(credentials_path, api_key)
        # Also save org_name and user_email for `auth status`
        org_name = str(result.get("org_name", ""))
        user_email = str(result.get("user_email", ""))
        if org_name:
            _save_config_field(config_path, "platform_org_name", org_name)
        if user_email:
            _save_config_field(config_path, "platform_user_email", user_email)
        print(f"\n  {GREEN}\u2713{NC} API key saved to {credentials_path} (mode 0600)")
    else:
        print(f"\n  {YELLOW}No API key in response{NC}")

    return 0


def run_auth_logout(config_path: Path) -> int:
    """CLI handler for ``trw-mcp auth logout``. Returns exit code."""
    removed = device_auth_logout(config_path)
    creds_path = credentials_path_for(config_path)
    if removed:
        print(f"  {GREEN}\u2713{NC} API key removed ({creds_path} + {config_path})")
        return 0
    print(f"  {YELLOW}No API key found ({creds_path} or {config_path}){NC}")
    return 0


def run_auth_status(config_path: Path, api_url: str) -> int:
    """CLI handler for ``trw-mcp auth status``. Returns exit code."""
    status = device_auth_status(config_path, api_url)
    if status.get("authenticated"):
        print(f"  {GREEN}\u2713{NC} Authenticated")
        print(f"    Key:   {status.get('key_prefix', '?')}")
        org = status.get("org_name")
        email = status.get("user_email")
        if org:
            print(f"    Org:   {org}")
        if email:
            print(f"    Email: {email}")
        print(f"    Config: {config_path}")
    else:
        print(f"  {YELLOW}Not authenticated{NC}")
        print("    Run: trw-mcp auth login")
    return 0
