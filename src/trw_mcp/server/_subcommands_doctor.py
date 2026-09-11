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
from trw_mcp.server._doctor_instruction_gate import (
    _GATE_SCAN_EXCLUSIONS as _GATE_SCAN_EXCLUSIONS,
)
from trw_mcp.server._doctor_instruction_gate import (
    _instruction_surfaces as _instruction_surfaces,
)
from trw_mcp.server._doctor_instruction_gate import (
    classify_carrier_state as classify_carrier_state,
)

# FR-07: re-exported so callers/tests can assert doctor consumes the canonical
# deliver-gate phrase rather than a hardcoded copy — never duplicate the value.
from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE as DELIVER_GATE_PHRASE

logger = structlog.get_logger(__name__)

DoctorStatus = Literal["PASS", "WARN", "FAIL", "SKIP"]


@dataclass(frozen=True)
class CheckResult:
    """One diagnostic check outcome.

    *data* carries machine-readable detail for checks whose message is a
    summary of something structured — the ``agent_parity`` per-client installed
    and expected counts are the first case (PRD-CORE-252-FR05). It is omitted
    from the json payload when empty, so no existing row grows a key.
    """

    name: str
    status: DoctorStatus
    message: str
    data: tuple[object, ...] = ()


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
    """Report whether the TARGET's ``config.yaml`` is present, parseable, and schema-valid.

    CORE262-11: this used to validate a bare ``TRWConfig()`` -- field defaults
    plus whatever ``TRW_*`` env vars happen to be set in THIS process -- which
    has no dependency on the target file's content at all. A syntactically
    valid YAML mapping that Pydantic rejects (e.g. ``target_platforms: 5``)
    therefore always read PASS: the row validated a config that had nothing to
    do with the file it claimed to check. This now re-parses the same mapping
    ``_resolve_target_config`` builds a ``TRWConfig`` from, and validates that
    mapping directly, so a schema-invalid target genuinely fails this row.
    """
    config_path = target / ".trw" / "config.yaml"
    if not config_path.exists():
        return CheckResult(
            "config",
            "WARN",
            f"no {config_path} found — built-in defaults apply (run 'trw-mcp init-project .').",
        )
    try:
        from ruamel.yaml import YAML

        raw = YAML(typ="safe").load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CheckResult("config", "FAIL", f"config.yaml parse error: {exc}")

    from trw_mcp.models.config._loader import _normalize_meta_tune_overrides

    overrides = raw if isinstance(raw, dict) else {}
    overrides = {str(k): v for k, v in overrides.items() if v is not None}
    overrides.pop("platform_api_key", None)
    try:
        TRWConfig(**_normalize_meta_tune_overrides(overrides))  # type: ignore[arg-type]
    except Exception as exc:
        return CheckResult("config", "FAIL", f"config.yaml present but schema-invalid: {exc}")
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
    """Report the profile the project resolved, and WARN when it is not the one it asked for.

    PASS is reserved for agreement (PRD-CORE-262-FR05). The old predicate
    returned PASS whenever the REQUESTED identifier was merely a known profile,
    never comparing it to the resolved one — so a silent fallback (retired
    identifier, unknown platform, empty ``target_platforms``) read green while
    the row printed a client the project never selected.
    """
    requested = config.target_platforms[0] if config.target_platforms else "claude-code"
    resolved = config.client_profile.client_id
    if requested not in _PROFILES:
        return CheckResult(
            "profile",
            "WARN",
            f"profile: {resolved} (requested '{requested}' is not a supported profile; fell back).",
        )
    if requested != resolved:
        return CheckResult(
            "profile",
            "WARN",
            f"profile: {resolved} (requested '{requested}' resolved to '{resolved}').",
        )
    return CheckResult("profile", "PASS", f"profile: {resolved}")


# ── FR-07: instruction-file presence + deliver-gate statement ────────────────
# Heavy lifting lives in the ``_doctor_instruction_gate`` sibling (marker
# resolution, carrier-state classification, single-source-pointer detection);
# these are the thin CheckResult-wrapping callers the catalogue dispatches to.


def _check_instruction_carrier_state(target: Path, _config: TRWConfig) -> CheckResult:
    """Report per-surface carrier state so a stale install is visible (FR07)."""
    from trw_mcp.server._doctor_instruction_gate import carrier_state_report

    status, message = carrier_state_report(target)
    return CheckResult("instruction_carrier", cast("DoctorStatus", status), message)


def _check_instruction_gate(target: Path, _config: TRWConfig) -> CheckResult:
    """Report deliver-gate-phrase presence across every instruction surface (FR07)."""
    from trw_mcp.server._doctor_instruction_gate import instruction_gate_report

    status, message = instruction_gate_report(target)
    return CheckResult("instruction_surface", cast("DoctorStatus", status), message)


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


def _check_framework_integrity(target: Path, config: TRWConfig) -> CheckResult:
    """Verify effective version pins, deployed bodies, and deployment stamp."""
    from trw_mcp.server._doctor_framework_integrity import check_framework_integrity

    status, message = check_framework_integrity(
        target,
        framework_version=config.framework_version,
        aaref_version=config.aaref_version,
    )
    return CheckResult("framework_integrity", cast("DoctorStatus", status), message)


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
            "vector search degraded. The installer bundles this by default; if it "
            "is missing here, run: pip install 'trw-mcp[vectors]' (or 'trw-memory[vectors]' "
            "in a standalone trw-memory install), then reconnect the MCP client.",
        )
    return CheckResult("memory_backend", "PASS", f"memory store healthy ({count} entries, vectors ok).")


# ── PRD-CORE-248-FR06: WAL size, live writers, last-checkpoint age ───────────


def _check_memory_wal(target: Path, config: TRWConfig) -> CheckResult:
    """Report WAL size, live writer count, and last-checkpoint age.

    Delegates to the ``_doctor_memory_wal`` sibling (kept out of this file for
    the module-size gate). It opens NO SQLite connection — unlike
    ``_check_memory_backend`` above — because a diagnostic that adds a writer to
    a contended store is measuring the thing it just made worse.

    The resolved *config* is forwarded rather than discarded: the row used to
    build a bare ``TRWConfig()`` of its own, so an operator who raised
    ``wal_checkpoint_threshold_mb`` in the target project's ``.trw/config.yaml``
    got a row that silently ignored it (same defect class as PRD-CORE-262-FR05).
    """
    from trw_mcp.server._doctor_memory_wal import memory_wal_row

    status, message = memory_wal_row(target, config)
    return CheckResult("memory_wal", cast("DoctorStatus", status), message)


# ── PRD-CORE-253-FR03: loopback memory-daemon reachability ───────────────────


def _check_memory_daemon(_target: Path, _config: TRWConfig) -> CheckResult:
    """Report the user-space memory daemon's reachability, pid, uptime and store.

    Delegates to the ``_doctor_memory_daemon`` sibling (kept out of this file
    for the module-size gate). It PROBES and never starts: a diagnostic that
    spawned a daemon would always report one.
    """
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    status, message = memory_daemon_row()
    return CheckResult("memory_daemon", cast("DoctorStatus", status), message)


# ── PRD-FIX-131 follow-up: operator visibility for hot worker threads ────────


def _check_thread_hotspots(target: Path, config: TRWConfig) -> CheckResult:
    """Report the hottest thread's CPU share on each live trw-mcp server process.

    Delegates to the ``_doctor_thread_hotspots`` sibling (kept out of this file
    for the eLOC gate). Read-only ``/proc`` census; it never sends a signal
    itself — an operator runs the WARN's named ``kill -USR1 <pid>`` remedy.
    SKIPs (never PASSes) on non-Linux or when ``/proc`` is unreadable, since
    those platforms make the census genuinely unmeasured rather than clean.
    """
    from trw_mcp.server._doctor_thread_hotspots import thread_hotspot_row

    status, message = thread_hotspot_row(
        target,
        share_threshold=config.doctor_thread_hotspot_share,
        min_seconds=float(config.doctor_thread_hotspot_min_seconds),
        is_linux=sys.platform.startswith("linux"),
    )
    return CheckResult("thread_hotspots", cast("DoctorStatus", status), message)


# ── PRD-SEC-014-FR04: embedding cache state + egress posture ─────────────────


def _check_embedding_egress(_target: Path, config: TRWConfig) -> CheckResult:
    """Report the configured model's cache state and the effective egress posture.

    Delegates to the ``_doctor_embedding_egress`` sibling (kept out of this file
    for the eLOC gate). Fail-open: an unanswerable cache probe becomes a WARN row
    with the conservative posture, never an aborted report.
    """
    from trw_mcp.server._doctor_embedding_egress import embedding_egress_report

    status, message = embedding_egress_report(
        str(getattr(config, "retrieval_embedding_model", "") or ""),
        embeddings_enabled=bool(getattr(config, "embeddings_enabled", False)),
    )
    return CheckResult("embedding_egress", cast("DoctorStatus", status), message)


# ── FR-10: optional backend probe + installer-flag advisory ──────────────────


def _check_backend_connectivity(_target: Path, config: TRWConfig) -> CheckResult:
    """Report the resolved egress posture and the probe decision separately.

    Delegates to the ``_doctor_backend_connectivity`` sibling; that module's
    docstring carries the distinction this row must not re-collapse.
    """
    from trw_mcp.server._doctor_backend_connectivity import backend_connectivity_row

    status, message = backend_connectivity_row(config)
    return CheckResult("backend_connectivity", cast("DoctorStatus", status), message)


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


# ── FR-12 (PRD-CORE-252-FR05): bundled-agent parity per selected client ──────


def _check_agent_parity(target: Path, _config: TRWConfig) -> CheckResult:
    """Report whether each selected client holds the full bundled agent set."""
    from trw_mcp.server._doctor_agent_parity import agent_parity_report

    status, message, rows = agent_parity_report(target)
    return CheckResult("agent_parity", cast("DoctorStatus", status), message, data=tuple(rows))


def _check_formation_readiness(_target: Path, config: TRWConfig) -> CheckResult:
    """Report per-client dispatch readiness: verification, binary, live version."""
    from trw_mcp.server._doctor_formation_readiness import formation_readiness_report

    status, message, rows = formation_readiness_report(config)
    return CheckResult("formation_readiness", cast("DoctorStatus", status), message, data=tuple(rows))


# ── PRD-FIX-133: Antigravity CLI live MCP registration ───────────────────────


def _check_antigravity_mcp(target: Path, _config: TRWConfig) -> CheckResult:
    """Report whether ``agy`` actually sees the ``trw`` MCP server registered.

    Delegates to the ``_doctor_antigravity_mcp`` sibling (kept out of this file
    for the eLOC gate). Probes the live client via ``agy mcp list`` rather than
    re-reading the config file the installer just wrote, so a write that
    silently fails to reach the running client is caught. SKIPs — never
    PASSes — when the ``agy`` binary is absent, since an absent binary makes
    registration unknown, not confirmed.
    """
    from trw_mcp.server._doctor_antigravity_mcp import antigravity_mcp_row

    status, message = antigravity_mcp_row(target)
    return CheckResult("antigravity_mcp", cast("DoctorStatus", status), message)


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
    ("instruction_carrier", "_check_instruction_carrier_state"),
    ("trw_dir", "_check_trw_dir"),
    ("framework_integrity", "_check_framework_integrity"),
    # PRD-CORE-248 FR06: memory_wal runs BEFORE memory_backend. The backend
    # check opens the store, and opening a SQLite store checkpoints and rewrites
    # its WAL — so a WAL row placed after it would report the state the
    # diagnostic itself produced, not the state the operator came to see.
    ("memory_wal", "_check_memory_wal"),
    ("memory_backend", "_check_memory_backend"),
    ("memory_daemon", "_check_memory_daemon"),
    ("thread_hotspots", "_check_thread_hotspots"),
    ("embedding_egress", "_check_embedding_egress"),
    ("backend_connectivity", "_check_backend_connectivity"),
    ("installer_flag_advisory", "_check_installer_flag_advisory"),
    ("stubs", "_check_stubs"),
    ("agent_parity", "_check_agent_parity"),
    ("antigravity_mcp", "_check_antigravity_mcp"),
    ("tendencies_xref", "_check_tendencies_xref"),
    # PRD-CORE-266-FR06: appended LAST so every pre-existing row keeps its
    # position — the doctor's row order is asserted by its own tests and relied
    # on by operator habit; it is the later of the two subprocess-spawning checks.
    ("formation_readiness", "_check_formation_readiness"),
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


def _resolve_target_config(target: Path) -> TRWConfig:
    """Build the config the TARGET project records, not this process's defaults.

    ``TRWConfig()`` is the bare constructor: field defaults only, never the
    target's ``.trw/config.yaml``. Every row reading ``target_platforms`` or
    ``client_profile`` off it therefore printed ``claude-code`` for a codex
    project — the identical answer it would print for a project that recorded
    nothing at all, which makes the row unable to be wrong and unable to be
    right (PRD-CORE-262-FR05).

    Detection never enters this: ``target_platforms`` is the durable record the
    init path itself wrote, and directory presence cannot distinguish TRW's own
    scaffold from the user's.

    The cascade is not re-implemented here. It is
    :func:`~trw_mcp.models.config._loader.resolve_config_overrides`, the same
    function production builds from, because a hand-rolled copy had already
    drifted: this one dropped ``platform_api_key`` (correctly) but never
    re-resolved it from ``credentials.yaml``, and it omitted both the
    ``~/.trw/config.yaml`` layer and the ``TRW_*`` exclusion that preserves
    ``env > file`` precedence. So the doctor resolved a ``config.yaml`` value
    wherever an env var shadowed it and the live server resolved the env value
    — a row measuring against a setting the operator did not choose, which is
    the PRD-CORE-262-FR05 defect this docstring cites as its own justification.

    CORE262-10: ``_read_yaml_overrides`` documents "never raises" but does not
    honor that contract -- ``FileStateReader.read_yaml`` raises ``StateError``
    on malformed YAML (or a non-mapping top level), and nothing here caught
    it. ``_run_doctor`` calls this function BEFORE ``_doctor_core``'s per-check
    exception isolation even starts, so a malformed target ``config.yaml``
    used to abort the whole ``doctor`` invocation before a single row printed
    -- not even the ``config: FAIL`` row this exact input is supposed to
    produce. Falling back to ``TRWConfig()`` here is safe: ``_check_config``
    re-parses the same file independently and reports the parse failure as
    its own FAIL row.
    """
    from trw_mcp.exceptions import StateError
    from trw_mcp.models.config._loader import resolve_config_overrides

    try:
        overrides = resolve_config_overrides(target / ".trw" / "config.yaml")
    except StateError:
        logger.warning("doctor_target_config_unreadable", path=str(target / ".trw" / "config.yaml"), exc_info=True)
        return TRWConfig()
    if not overrides:
        return TRWConfig()
    try:
        return TRWConfig(**overrides)  # type: ignore[arg-type]
    except Exception:  # justified: an invalid config.yaml is _check_config's verdict, not this row's
        logger.warning("doctor_target_config_invalid", path=str(target / ".trw" / "config.yaml"))
        return TRWConfig()


def _run_doctor(args: argparse.Namespace) -> None:
    """Handle the ``doctor`` subcommand. Exits 1 iff overall verdict is fail."""
    target = Path(getattr(args, "target_dir", ".")).resolve()
    config = _resolve_target_config(target)
    results = _doctor_core(target, config)
    overall = _overall_status(results)

    if getattr(args, "format", "human") == "json":
        payload = {
            "checks": [
                {"name": r.name, "status": r.status, "message": r.message} | ({"data": list(r.data)} if r.data else {})
                for r in results
            ],
            "overall": overall,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(_format_human(results, overall))
        if getattr(args, "fix", False):
            print("\n--fix is suggest-only in v1: review the WARN/FAIL hints above and act manually.")

    sys.exit(1 if overall == "fail" else 0)
