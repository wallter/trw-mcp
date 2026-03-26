"""Device authorization flow for TRW CLI (RFC 8628).

Provides ``device_auth_login``, ``device_auth_logout``, ``device_auth_status``,
and ``select_organization`` using ONLY Python stdlib (no requests/httpx).

Matches the installer UI patterns from ``install-trw.template.py``.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── ANSI colors (mirrors installer constants) ────────────────────────

_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

GREEN = "\033[0;32m" if _USE_COLOR else ""
YELLOW = "\033[0;33m" if _USE_COLOR else ""
RED = "\033[0;31m" if _USE_COLOR else ""
BOLD = "\033[1m" if _USE_COLOR else ""
DIM = "\033[2m" if _USE_COLOR else ""
NC = "\033[0m" if _USE_COLOR else ""

# ── Spinner (matches installer _Spinner) ─────────────────────────────

_SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"


class _Spinner:
    """Braille-character spinner in a daemon thread."""

    def __init__(self, message: str) -> None:
        self.message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        idx = 0
        while not self._stop.is_set():
            frame = _SPINNER_FRAMES[idx % len(_SPINNER_FRAMES)]
            sys.stdout.write(f"\r\033[K            {frame} {self.message}")
            sys.stdout.flush()
            idx += 1
            self._stop.wait(0.1)


# ── TTY input helper (matches installer _open_tty) ───────────────────


def _open_tty() -> TextIO | None:
    """Open /dev/tty for reading, or None if unavailable."""
    try:
        return open("/dev/tty")
    except OSError:
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

    # Step 2: Try browser, show fallback URL with code embedded
    if interactive:
        browser_opened = False
        with contextlib.suppress(Exception):
            browser_opened = webbrowser.open(verification_uri_complete)

        print()
        if browser_opened:
            print(f"  {GREEN}\u2713{NC} Browser opened — approve the request to continue")
            print(f"  {DIM}Verify this code matches: {BOLD}{user_code}{NC}")
        else:
            print(f"  {BOLD}Open this URL to authenticate:{NC}")
            print()
            print(f"    {GREEN}{verification_uri_complete}{NC}")
            print()

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
                pass

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
                    f"\n  {RED}Error:{NC} Unexpected response "
                    f"(HTTP {exc.code}): {error_code or 'unknown'}",
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


def select_organization(
    orgs: list[dict[str, object]],
    interactive: bool = True,
) -> dict[str, object] | None:
    """Select an organization from *orgs*.

    - If 1 org, auto-select and print confirmation.
    - If multiple, show numbered selector (installer style).
    - Reads from /dev/tty for piped-input compatibility.

    Returns the selected org dict or None on failure.
    """
    if not orgs:
        if interactive:
            print(f"\n  {YELLOW}No organizations found{NC}")
        return None

    if len(orgs) == 1:
        org = orgs[0]
        name = org.get("name") or org.get("slug") or "default"
        if interactive:
            print(f"  {GREEN}\u2713{NC} Organization: {BOLD}{name}{NC}")
        return org

    # Multiple orgs: numbered selector
    if not interactive:
        # Non-interactive: pick first
        return orgs[0]

    print()
    print(f"  {BOLD}Select organization:{NC}")
    print()
    for i, org in enumerate(orgs):
        name = org.get("name") or org.get("slug") or f"org-{i + 1}"
        marker = f"{BOLD}\u276f{NC}" if i == 0 else " "
        print(f"    {marker} {i + 1}. {name}")
    print()

    tty = _open_tty()
    if tty is None:
        return orgs[0]

    try:
        sys.stdout.write("  Choice [1]: ")
        sys.stdout.flush()
        raw = tty.readline().strip()
    finally:
        tty.close()

    if not raw:
        return orgs[0]

    try:
        choice = int(raw) - 1
        if 0 <= choice < len(orgs):
            selected = orgs[choice]
            name = selected.get("name") or selected.get("slug") or f"org-{choice + 1}"
            print(f"  {GREEN}\u2713{NC} Organization: {BOLD}{name}{NC}")
            return selected
    except ValueError:
        pass

    # Invalid input: default to first
    return orgs[0]


# ── Config helpers ────────────────────────────────────────────────────

_YAML_KEY_RE = re.compile(r"^(\s*)(platform_api_key)\s*:\s*(.*)$")


def _read_config_lines(config_path: Path) -> list[str] | None:
    """Read config file lines, or None if file doesn't exist."""
    try:
        return config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None


def device_auth_logout(config_path: Path) -> bool:
    """Remove ``platform_api_key`` from ``.trw/config.yaml``.

    Returns True if a key was found and removed.
    """
    lines = _read_config_lines(config_path)
    if lines is None:
        return False

    new_lines: list[str] = []
    removed = False
    for line in lines:
        m = _YAML_KEY_RE.match(line.rstrip("\n"))
        if m:
            value = m.group(3).strip().strip('"').strip("'")
            if value:
                # Non-empty key present: clear it
                indent = m.group(1)
                new_lines.append(f'{indent}platform_api_key: ""\n')
                removed = True
            else:
                # Already empty: keep as-is
                new_lines.append(line if line.endswith("\n") else line + "\n")
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")

    if removed:
        config_path.write_text("".join(new_lines), encoding="utf-8")

    return removed


def device_auth_status(config_path: Path, api_url: str) -> dict[str, object]:
    """Check authentication status from config file.

    Returns ``{"authenticated": True, "key_prefix": "trw_dk_...", "org_name": ..., "user_email": ...}``
    if a platform API key is configured, otherwise ``{"authenticated": False}``.
    """
    lines = _read_config_lines(config_path)
    if lines is None:
        return {"authenticated": False}

    key_prefix = ""
    org_name = ""
    user_email = ""

    for line in lines:
        stripped = line.rstrip("\n").lstrip()
        if stripped.startswith("platform_api_key:"):
            value = stripped.partition(":")[2].strip().strip('"').strip("'")
            if value and value != '""':
                key_prefix = value[:10] + "..." if len(value) > 10 else value
        elif stripped.startswith("platform_org_name:"):
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

    # If orgs returned, let user pick
    orgs = result.get("organizations")
    if isinstance(orgs, list) and orgs:
        selected = select_organization(orgs, interactive=True)
        if selected:
            print(f"  {DIM}Organization ID: {selected.get('id', 'unknown')}{NC}")

    # Save API key + metadata to config
    api_key = result.get("api_key", "")
    if api_key and isinstance(api_key, str):
        config_path.parent.mkdir(parents=True, exist_ok=True)
        _save_api_key(config_path, api_key)
        # Also save org_name and user_email for `auth status`
        org_name = str(result.get("org_name", ""))
        user_email = str(result.get("user_email", ""))
        if org_name:
            _save_config_field(config_path, "platform_org_name", org_name)
        if user_email:
            _save_config_field(config_path, "platform_user_email", user_email)
        print(f"\n  {GREEN}\u2713{NC} API key saved to {config_path}")
    else:
        print(f"\n  {YELLOW}No API key in response{NC}")

    return 0


def run_auth_logout(config_path: Path) -> int:
    """CLI handler for ``trw-mcp auth logout``. Returns exit code."""
    removed = device_auth_logout(config_path)
    if removed:
        print(f"  {GREEN}\u2713{NC} API key removed from {config_path}")
        return 0
    print(f"  {YELLOW}No API key found in {config_path}{NC}")
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


def _save_config_field(config_path: Path, key: str, value: str) -> None:
    """Write or update a single field in config YAML."""
    field_re = re.compile(rf"^(\s*)({re.escape(key)})\s*:\s*(.*)$")
    lines = _read_config_lines(config_path)
    if lines is None:
        config_path.write_text(f'{key}: "{value}"\n', encoding="utf-8")
        return

    new_lines: list[str] = []
    replaced = False
    for line in lines:
        m = field_re.match(line.rstrip("\n"))
        if m:
            indent = m.group(1)
            new_lines.append(f'{indent}{key}: "{value}"\n')
            replaced = True
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")

    if not replaced:
        new_lines.append(f'{key}: "{value}"\n')

    config_path.write_text("".join(new_lines), encoding="utf-8")


def _save_api_key(config_path: Path, api_key: str) -> None:
    """Write or update ``platform_api_key`` in config YAML."""
    lines = _read_config_lines(config_path)
    if lines is None:
        # Create minimal config
        config_path.write_text(
            f'platform_api_key: "{api_key}"\n',
            encoding="utf-8",
        )
        return

    new_lines: list[str] = []
    replaced = False
    for line in lines:
        m = _YAML_KEY_RE.match(line.rstrip("\n"))
        if m:
            indent = m.group(1)
            new_lines.append(f'{indent}platform_api_key: "{api_key}"\n')
            replaced = True
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")

    if not replaced:
        new_lines.append(f'platform_api_key: "{api_key}"\n')

    config_path.write_text("".join(new_lines), encoding="utf-8")
