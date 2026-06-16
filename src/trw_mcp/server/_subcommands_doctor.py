"""``trw-mcp doctor`` — read-only first-run diagnostic subcommand (PRD-QUAL-106).

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat
and registered in ``SUBCOMMAND_HANDLERS`` for table-driven dispatch.

The doctor runs a fixed catalogue of read-only pre-flight checks grounded in the
PRD-QUAL-080 first-run-friction inventory and exits 0 on a clean run, 1 when any
check FAILs (WARN/SKIP never fail the run). Each check is fail-open isolated: an
unhandled exception in one check becomes a single FAIL row for that check and
never aborts the rest of the report (Risk R2).

It is STRICTLY diagnostic in v1 — no mutations. ``--fix`` prints suggested
remediation commands but applies nothing. The only permitted network call is the
optional probe of an explicitly-configured ``backend_url`` (FR-10); the default
empty config produces a fully-offline run (NFR-01).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._profiles import _PROFILES

# FR-07: consume the canonical deliver-gate phrase — never hardcode/duplicate it.
from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

logger = structlog.get_logger(__name__)

DoctorStatus = Literal["PASS", "WARN", "FAIL", "SKIP"]

# TRW-managed instruction surfaces scanned for the deliver-gate statement (FR-07).
_INSTRUCTION_FILES: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    ".codex/INSTRUCTIONS.md",
    ".opencode/INSTRUCTIONS.md",
    ".github/copilot-instructions.md",
)

# Whole-block markers that delimit a TRW auto-generated section.
_BLOCK_MARKERS: tuple[tuple[str, str], ...] = (
    ("<!-- trw:start -->", "<!-- trw:end -->"),
    ("<!-- trw:gemini:start -->", "<!-- trw:gemini:end -->"),
    ("<!-- trw:copilot:start -->", "<!-- trw:copilot:end -->"),
    ("<!-- trw:antigravity:start -->", "<!-- trw:antigravity:end -->"),
)


@dataclass(frozen=True)
class CheckResult:
    """One diagnostic check outcome."""

    name: str
    status: DoctorStatus
    message: str


def _overall_status(results: list[CheckResult]) -> Literal["pass", "warn", "fail"]:
    """Reduce check rows to an overall verdict: FAIL > WARN > pass; SKIP ignored."""
    statuses = {r.status for r in results}
    if "FAIL" in statuses:
        return "fail"
    if "WARN" in statuses:
        return "warn"
    return "pass"


# ── FR-01: Python + dependency version ───────────────────────────────────────


def _check_python_version(_target: Path, _config: TRWConfig) -> CheckResult:
    major, minor = sys.version_info[0], sys.version_info[1]
    if (major, minor) < (3, 10):
        return CheckResult(
            "python_version",
            "FAIL",
            f"Python 3.10+ required (found {major}.{minor}). Install a newer interpreter.",
        )

    parts = [f"python {major}.{minor}"]
    status: DoctorStatus = "PASS"
    for pkg, floor in (("trw-mcp", None), ("trw-memory", (0, 9, 5))):
        ver = _package_version(pkg)
        if ver is None:
            status = "WARN"
            parts.append(f"{pkg}=absent")
            continue
        parts.append(f"{pkg}={ver}")
        if floor is not None and _parse_version(ver) < floor:
            status = "WARN"
            parts.append(f"({pkg} below recommended {'.'.join(map(str, floor))})")
    return CheckResult("python_version", status, "; ".join(parts))


def _package_version(name: str) -> str | None:
    import importlib.metadata as _md

    try:
        return _md.version(name)
    except _md.PackageNotFoundError:
        return None


def _parse_version(raw: str) -> tuple[int, ...]:
    nums: list[int] = []
    for token in raw.split(".")[:3]:
        digits = "".join(ch for ch in token if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    return tuple(nums)


# ── FR-02: config presence + parseability ────────────────────────────────────


def _check_config(target: Path, _config: TRWConfig) -> CheckResult:
    config_path = target / ".trw" / "config.yaml"
    if not config_path.exists():
        return CheckResult(
            "config",
            "WARN",
            f"no {config_path} found — built-in defaults apply (run 'trw-mcp init-project .').",
        )
    try:
        from ruamel.yaml import YAML

        YAML(typ="safe").load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CheckResult("config", "FAIL", f"config.yaml parse error: {exc}")

    try:
        TRWConfig()
    except Exception as exc:
        return CheckResult("config", "FAIL", f"config.yaml present but TRWConfig() failed: {exc}")
    return CheckResult("config", "PASS", f"{config_path} found and valid.")


# ── FR-03: MCP server smoke (import only) ────────────────────────────────────


def _import_mcp_app() -> object:
    """Import the FastMCP app factory without starting a listener (patchable seam).

    Imports ``create_app`` (the factory) rather than the module-level ``mcp``
    singleton so the smoke check proves the server module loads without paying
    for app construction or any listener bind (FR-03 / Risk R1).
    """
    from trw_mcp.server._app import create_app

    return create_app


def _check_mcp_import(_target: Path, _config: TRWConfig) -> CheckResult:
    try:
        factory = _import_mcp_app()
    except Exception as exc:
        return CheckResult("mcp_import", "FAIL", f"FastMCP app import failed: {exc}")
    if not callable(factory):
        return CheckResult("mcp_import", "FAIL", "FastMCP app factory is not callable.")
    return CheckResult("mcp_import", "PASS", "FastMCP server module imports cleanly.")


# ── FR-04: client-profile detection ──────────────────────────────────────────


def _check_profile(_target: Path, config: TRWConfig) -> CheckResult:
    requested = config.target_platforms[0] if config.target_platforms else "claude-code"
    resolved = config.client_profile.client_id
    if requested in _PROFILES:
        return CheckResult("profile", "PASS", f"profile: {resolved}")
    return CheckResult(
        "profile",
        "WARN",
        f"profile: {resolved} (requested '{requested}' is not a supported profile; fell back).",
    )


# ── FR-07: instruction-file presence + deliver-gate statement ────────────────


def _extract_trw_block(content: str) -> str | None:
    for start_marker, end_marker in _BLOCK_MARKERS:
        start = content.find(start_marker)
        end = content.find(end_marker)
        if start != -1 and end != -1 and end > start:
            return content[start + len(start_marker) : end]
    return None


def _check_instruction_gate(target: Path, _config: TRWConfig) -> CheckResult:
    present: list[str] = []
    missing_gate: list[str] = []
    for rel in _INSTRUCTION_FILES:
        path = target / rel
        if not path.is_file():
            continue
        present.append(rel)
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            missing_gate.append(rel)
            continue
        block = _extract_trw_block(content)
        if block is None:
            continue  # no TRW-managed block in this surface — nothing to assert.
        if DELIVER_GATE_PHRASE not in block:
            missing_gate.append(rel)

    if not present:
        return CheckResult(
            "instruction_surface",
            "WARN",
            "no TRW instruction surface present yet (run 'trw-mcp init-project .').",
        )
    if missing_gate:
        return CheckResult(
            "instruction_surface",
            "FAIL",
            f"deliver-gate statement absent from TRW block in: {', '.join(missing_gate)}.",
        )
    return CheckResult(
        "instruction_surface",
        "PASS",
        f"deliver-gate statement present in {len(present)} surface(s): {', '.join(present)}.",
    )


# ── FR-08: .trw directory integrity ──────────────────────────────────────────


def _check_trw_dir(target: Path, _config: TRWConfig) -> CheckResult:
    trw = target / ".trw"
    if not trw.exists():
        return CheckResult(
            "trw_dir",
            "WARN",
            f"{trw} not initialised yet (run 'trw-mcp init-project .').",
        )
    if not trw.is_dir():
        return CheckResult("trw_dir", "FAIL", f"{trw} exists but is not a directory.")
    config_path = trw / "config.yaml"
    if config_path.exists():
        try:
            config_path.read_text(encoding="utf-8")
        except OSError as exc:
            return CheckResult("trw_dir", "FAIL", f"{config_path} is unreadable: {exc}")
    return CheckResult("trw_dir", "PASS", f"{trw} present and readable.")


# ── FR-09: memory backend health (read-only) ─────────────────────────────────


def _probe_memory_backend(db_path: Path) -> tuple[int, bool]:
    """Open the SQLite backend read-only and return ``(entry_count, vectors_ok)``.

    Patchable seam. Opens an EXISTING store only; the caller guards on file
    existence so this never creates a store.
    """
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    backend = SQLiteBackend(db_path, recovery_policy="empty_ok")
    try:
        count = backend.count()
        vectors_ok = bool(backend.vec_available)
    finally:
        close = getattr(backend, "close", None)
        if callable(close):
            close()
    return count, vectors_ok


def _check_memory_backend(target: Path, _config: TRWConfig) -> CheckResult:
    db_path = target / ".trw" / "memory" / "memory.db"
    if not db_path.exists():
        return CheckResult(
            "memory_backend",
            "WARN",
            "no memory store yet (created on first trw_session_start / trw_learn).",
        )
    count, vectors_ok = _probe_memory_backend(db_path)
    if not vectors_ok:
        return CheckResult(
            "memory_backend",
            "WARN",
            f"memory store healthy ({count} entries) but sqlite-vec unavailable — "
            "vector search degraded (install the [vectors] extra).",
        )
    return CheckResult("memory_backend", "PASS", f"memory store healthy ({count} entries, vectors ok).")


# ── FR-10: optional backend probe + installer-flag advisory ──────────────────


def _probe_backend_url(url: str) -> tuple[bool, str]:
    """Probe an owned/local ``backend_url`` (patchable seam). Never a prod host."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 — owned URL only
            return True, f"{resp.status} {getattr(resp, 'reason', '')}".strip()
    except urllib.error.URLError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def _check_backend_connectivity(_target: Path, config: TRWConfig) -> CheckResult:
    url = str(config.backend_url or "").strip()
    if not url:
        return CheckResult(
            "backend_connectivity",
            "SKIP",
            "no backend_url configured — fully offline (no network call made).",
        )
    ok, detail = _probe_backend_url(url)
    if ok:
        return CheckResult("backend_connectivity", "PASS", f"backend_url reachable: {detail}")
    return CheckResult("backend_connectivity", "FAIL", f"backend_url unreachable: {detail}")


def _check_installer_flag_advisory(target: Path, _config: TRWConfig) -> CheckResult:
    # PRD-QUAL-080 CLM-014: surface the installer-flag-surface divergence.
    # This is advisory-only documentation, not a detected defect. A blanket WARN
    # would make ``doctor`` unable to ever report overall PASS on a clean tree, so
    # the row SKIPs (advisory preserved in the message) unless a detectable
    # condition warrants attention — namely a project-local ``install.sh`` present
    # in the scanned tree, whose flag surface this note clarifies.
    advisory = (
        "the curl|bash shell installer accepts --allow-unauthenticated "
        "(not --skip-auth, which is an install-trw.py flag)."
    )
    if (target / "install.sh").is_file():
        return CheckResult(
            "installer_flag_advisory",
            "WARN",
            f"project-local install.sh present — CI note: {advisory}",
        )
    return CheckResult(
        "installer_flag_advisory",
        "SKIP",
        f"advisory only (no project-local install.sh detected): {advisory}",
    )


# ── stubs / NotImplementedError visibility (PRD residual) ────────────────────


def _check_stubs(target: Path, config: TRWConfig) -> CheckResult:
    """Advisory: surface stub/partial PRDs + source NotImplementedError sites.

    Delegates the scan to ``_doctor_stubs.build_stubs_message`` (kept in a
    sibling so this file stays under the eLOC gate). Fail-open: a project with
    no PRD catalogue and no NotImplementedError sites reports SKIP, and the
    heavy lifting is isolated so a scan error never aborts the doctor run
    (the catalogue dispatcher also wraps this).
    """
    from trw_mcp.server._doctor_stubs import build_stubs_message

    prds_relative_path = str(getattr(config, "prds_relative_path", "") or "")
    status, message = build_stubs_message(target, prds_relative_path)
    return CheckResult("stubs", cast("DoctorStatus", status), message)


# ── advisory cross-reference to `trw-mcp tendencies` (PRD-QUAL-109 FR-03) ─────


def _check_tendencies_xref(_target: Path, _config: TRWConfig) -> CheckResult:
    """Advisory pointer to the sibling ``trw-mcp tendencies`` corpus-audit report.

    The doctor stays install-pre-condition-focused; AI-development tendency
    analysis (PRD-count uniformity, stub-closure chains, benchmark saturation,
    status-flip-only PRDs) lives in its own subcommand. This row always SKIPs so
    it never affects the doctor's overall verdict.
    """
    return CheckResult(
        "tendencies_xref",
        "SKIP",
        "for AI-development tendency analysis over historical handoff/PRD corpora, "
        "run 'trw-mcp tendencies' (advisory, exit-0).",
    )


# ── Catalogue + orchestration ────────────────────────────────────────────────

_CheckFn = Callable[[Path, TRWConfig], CheckResult]

# (check row name, module-level function name). The function is resolved from the
# module globals at run time so test monkeypatches on the named check functions
# take effect (test-monkeypatch indirection).
_CHECKS: tuple[tuple[str, str], ...] = (
    ("python_version", "_check_python_version"),
    ("config", "_check_config"),
    ("mcp_import", "_check_mcp_import"),
    ("profile", "_check_profile"),
    ("instruction_surface", "_check_instruction_gate"),
    ("trw_dir", "_check_trw_dir"),
    ("memory_backend", "_check_memory_backend"),
    ("backend_connectivity", "_check_backend_connectivity"),
    ("installer_flag_advisory", "_check_installer_flag_advisory"),
    ("stubs", "_check_stubs"),
    ("tendencies_xref", "_check_tendencies_xref"),
)


def _doctor_core(target: Path, config: TRWConfig) -> list[CheckResult]:
    """Run every check fail-open isolated and return the accumulated results."""
    results: list[CheckResult] = []
    for name, fn_name in _CHECKS:
        fn = cast("_CheckFn", globals()[fn_name])
        try:
            results.append(fn(target, config))
        except Exception as exc:
            logger.warning("doctor_check_failed", check=name, outcome="error", exc_info=True)
            results.append(CheckResult(name, "FAIL", f"check raised: {exc}"))
    logger.info(
        "doctor_complete",
        outcome=_overall_status(results),
        checks=len(results),
        fails=sum(1 for r in results if r.status == "FAIL"),
    )
    return results


def _format_human(results: list[CheckResult], overall: str) -> str:
    glyph = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL", "SKIP": "SKIP"}
    lines = [f"[{glyph[r.status]}] {r.name}: {r.message}" for r in results]
    lines.append("")
    lines.append(f"doctor: {overall.upper()} ({len(results)} checks)")
    return "\n".join(lines)


def _run_doctor(args: argparse.Namespace) -> None:
    """Handle the ``doctor`` subcommand. Exits 1 iff overall verdict is fail."""
    target = Path(getattr(args, "target_dir", ".")).resolve()
    config = TRWConfig()
    results = _doctor_core(target, config)
    overall = _overall_status(results)

    if getattr(args, "format", "human") == "json":
        payload = {
            "checks": [{"name": r.name, "status": r.status, "message": r.message} for r in results],
            "overall": overall,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(_format_human(results, overall))
        if getattr(args, "fix", False):
            print("\n--fix is suggest-only in v1: review the WARN/FAIL hints above and act manually.")

    sys.exit(1 if overall == "fail" else 0)
