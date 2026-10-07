"""Headless installer support: the run-log scrub and the one final JSON result document.

The shell installers (``install.sh``) call this through the interpreter they installed into, so none of
this logic lives in the size-capped installer template::

    python -m trw_mcp.bootstrap._headless_report redact-file LOG
    python -m trw_mcp.bootstrap._headless_report report --dir DIR --exit-code N --log LOG ...

The JSON document (``schema_version`` 1)::

    {"schema_version": 1, "ok": bool, "version": str|null, "interpreter": str, "install_path": str,
     "clients_configured": [str], "doctor": {"status": str, "summary": str},
     "warnings": [{"message": str, "remedy": str}], "files_changed": [str], "next_steps": [str],
     "log_path": str, "dispatch": {"enabled": bool, "decided_by": "flag|prior|prompt|default",
     "default_client": str|null, "clients": [per-client resolved defaults], "writes": [...], "warnings": [str]}}

``dispatch`` is the ``trw-mcp config dispatch --json`` result (``--dispatch-json FILE``), or, when the run
did not make a decision, the effective config's dispatch state with ``decided_by`` ``prior`` or ``default``.

``install_path`` is the project directory the install targeted; ``files_changed`` are absolute paths of
project files written since the run started. Everything that reaches the log or the document is passed
through :func:`trw_mcp.telemetry.anonymizer.redact_secrets`, plus the literal key values in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from trw_mcp.telemetry.anonymizer import redact_secrets

SCHEMA_VERSION = 1
_SECRET_ENV = ("TRW_API_KEY", "TRW_LICENSE_KEY")
_SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache"})
_MAX_FILES = 200
# The platform key shape (``trw_...`` / ``trw_dk_...``) is not in the shared credential detector.
_PLATFORM_KEY = re.compile(r"\btrw_(?:dk_)?[A-Za-z0-9_-]{8,}")
_REMEDY = re.compile(r"\((run [^)]+)\)|\b(?:Fix|Then run|Re-run with)\b:?\s*(.+)$", re.IGNORECASE)


def redact_text(text: str) -> str:
    """Scrub *text*: literal key values from the environment first, then the shared redactor."""
    for name in _SECRET_ENV:
        secret = os.environ.get(name, "").strip()
        if len(secret) >= 8:
            text = text.replace(secret, "<REDACTED>")
    return _PLATFORM_KEY.sub("<REDACTED>", redact_secrets(text))


def redact_dispatch(value: Any) -> Any:
    """*value* (a dispatch setup result) with every string passed through :func:`redact_text`."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {redact_text(str(k)): redact_dispatch(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_dispatch(v) for v in value]
    return value


def redact_file(path: Path) -> None:
    """Rewrite *path* in place with secrets scrubbed, keeping it owner-only (0600)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    path.write_text(redact_text(text), encoding="utf-8")
    path.chmod(0o600)


def _split_remedy(text: str) -> dict[str, str]:
    """``{"message", "remedy"}``: the first line is the message; a hint inside it, or later lines, the remedy."""
    first, _, rest = text.strip().partition("\n")
    remedy = " ".join(rest.split())
    match = _REMEDY.search(first)
    if not remedy and match:
        remedy = (match.group(1) or match.group(2) or "").strip()
    return {"message": redact_text(first.strip()), "remedy": redact_text(remedy)}


def files_changed(project: Path, since: float) -> list[str]:
    """Absolute paths of files under *project* modified at or after *since* (epoch seconds), capped."""
    found: list[str] = []
    for root, dirs, names in os.walk(project):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(names):
            path = Path(root) / name
            try:
                if path.stat().st_mtime >= since:
                    found.append(str(path))
            except OSError:
                continue  # trw-fail-silent-allow: a file that vanished mid-walk is simply not listed
            if len(found) >= _MAX_FILES:
                return found
    return found


def run_doctor(project: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    """``trw-mcp doctor`` as ``({"status", "summary"}, warnings)``; ``unknown`` when it cannot run."""
    cmd = [sys.executable, "-m", "trw_mcp.server", "doctor", str(project), "--format", "json"]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv (this interpreter, the doctor verb), no shell
            cmd, capture_output=True, text=True, timeout=90, check=False, stdin=subprocess.DEVNULL
        )
        payload = json.loads(proc.stdout)
        checks = [c for c in payload.get("checks", []) if isinstance(c, dict)]
        overall = str(payload.get("overall", "unknown")).lower()
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError) as exc:
        return {"status": "unknown", "summary": f"doctor could not run: {type(exc).__name__}"}, []
    counts = {s: sum(1 for c in checks if c.get("status") == s) for s in ("PASS", "WARN", "FAIL")}
    summary = f"{counts['PASS']} pass, {counts['WARN']} warn, {counts['FAIL']} fail"
    flagged = [
        {
            "message": redact_text(
                f"doctor {c.get('status', '')}: {c.get('name', '?')}: {c.get('message', '')}".strip()
            ),
            "remedy": _split_remedy(str(c.get("message", "")))["remedy"] or f"trw-mcp doctor {project}",
        }
        for c in checks
        if c.get("status") in ("WARN", "FAIL")
    ]
    return {"status": overall, "summary": summary}, flagged


def _version() -> str | None:
    from importlib import metadata

    try:
        return metadata.version("trw-mcp")
    except metadata.PackageNotFoundError:
        return None  # trw-fail-silent-allow: version is optional in the report; null says it is unknown


def _clients(project: Path) -> list[str]:
    try:
        from trw_mcp.bootstrap._utils import detect_ide

        return list(detect_ide(project))
    except Exception:  # trw:intentional the report must still print when client detection breaks; best-effort
        return []  # trw-fail-silent-allow: the client list is best-effort; the report must still print


def _dispatch_state(project: Path) -> dict[str, object]:
    """The dispatch setup result for a run that made no decision: the effective config, read-only."""
    from trw_mcp.dispatch._setup import describe_dispatch
    from trw_mcp.models.config._loader import config_for_trw_dir

    cfg = config_for_trw_dir(project / ".trw").dispatch
    return describe_dispatch(cfg, "prior" if "dispatch_tools_exposed" in cfg.operator_set else "default")


def _next_steps(ok: bool, doctor_status: str, auth_skipped: bool, project: Path) -> list[str]:
    if not ok:
        return [f"Read the run log, fix the cause named in warnings, and re-run the installer in {project}"]
    steps = []
    if auth_skipped:
        steps.append(
            "Set TRW_API_KEY (create a free key at https://trwframework.com/signup) and re-run for the full framework"
        )
    if doctor_status not in ("pass", "unknown"):
        steps.append(f"Run 'trw-mcp doctor {project}' and follow the rows it flags")
    steps.append("Restart your MCP client so it picks up the 'trw' server, then call trw_session_start()")
    return steps


def build_report(
    project: Path,
    exit_code: int,
    log_path: str,
    since: float,
    shell_warnings: list[str],
    error: str = "",
    auth_skipped: bool = False,
    dispatch: dict[str, object] | None = None,
) -> dict[str, object]:
    """Assemble the schema-1 document for a run that ended with *exit_code*."""
    ok = exit_code == 0
    warnings = [_split_remedy(error)] if error else []
    warnings += [_split_remedy(w) for w in shell_warnings if w.strip()]
    if auth_skipped:
        warnings.append(
            {
                "message": "No API key: installed the package only (no hooks, agents or skills)",
                "remedy": "Set TRW_API_KEY and re-run",
            }
        )
    if project.joinpath(".trw").is_dir():
        doctor, flagged = run_doctor(project)
    else:
        doctor, flagged = {"status": "unknown", "summary": "no .trw directory to check"}, []
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "version": _version(),
        "interpreter": sys.executable,
        "install_path": str(project),
        "clients_configured": _clients(project) if ok else [],
        "doctor": doctor,
        "warnings": warnings + flagged,
        "files_changed": [p for p in files_changed(project, since) if p != log_path],
        "next_steps": _next_steps(ok, doctor["status"], auth_skipped, project),
        "log_path": log_path,
        "dispatch": redact_dispatch(dispatch if dispatch is not None else _dispatch_state(project)),
    }


def _read_step(path: str) -> dict[str, object] | None:
    """The step's JSON result from *path*; ``None`` (compute from config) when absent or unreadable."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):  # trw-fail-silent-allow: a missing step result falls back to the live config state
        return None
    return value if isinstance(value, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="_headless_report")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("redact-file").add_argument("path")
    rep = sub.add_parser("report")
    rep.add_argument("--dir", default=".")
    rep.add_argument("--exit-code", type=int, default=0)
    rep.add_argument("--log", default="")
    rep.add_argument("--since", type=float, default=time.time())
    rep.add_argument("--warnings", default="", help="file with one shell warning per line")
    rep.add_argument("--error", default="")
    rep.add_argument("--auth-skipped", action="store_true")
    rep.add_argument("--dispatch-json", default="", help="file holding `trw-mcp config dispatch --json` output")
    args = parser.parse_args(argv)
    if args.cmd == "redact-file":
        redact_file(Path(args.path))
        return 0
    lines = Path(args.warnings).read_text(encoding="utf-8", errors="replace").splitlines() if args.warnings else []
    step = _read_step(args.dispatch_json)
    doc = build_report(
        Path(args.dir).resolve(), args.exit_code, args.log, args.since, lines, args.error, args.auth_skipped, step
    )
    sys.stdout.write(json.dumps(doc) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
