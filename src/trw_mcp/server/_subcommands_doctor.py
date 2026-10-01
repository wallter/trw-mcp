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
from trw_mcp.server._doctor_checks_registry import CHECKS as _CHECKS

# The four noqa-F401 _check_* imports below are referenced only via globals()[...]
# in _doctor_core's dispatch loop (test-monkeypatch indirection), same as every
# _check_* function defined directly in this file -- they are imported instead, to
# keep the 350-eLOC gate, so the module-level binding is the only use ruff can't see.
from trw_mcp.server._doctor_hint_delivery import (
    hint_delivery_row as _hint_delivery_row,
)
from trw_mcp.server._doctor_hint_hub import (
    check_hint_hub as _check_hint_hub,  # noqa: F401
)
from trw_mcp.server._doctor_hook_channel import (
    check_hook_channel as _check_hook_channel,  # noqa: F401
)
from trw_mcp.server._doctor_hook_family import check_hook_family as _check_hook_family  # noqa: F401
from trw_mcp.server._doctor_instruction_gate import (
    _GATE_SCAN_EXCLUSIONS as _GATE_SCAN_EXCLUSIONS,
)
from trw_mcp.server._doctor_instruction_gate import (
    _instruction_surfaces as _instruction_surfaces,
)
from trw_mcp.server._doctor_launcher_divergence import (
    check_launcher_divergence as _check_launcher_divergence,  # noqa: F401
)
from trw_mcp.server._doctor_retired_artifacts import (
    check_retired_artifacts as _check_retired_artifacts,  # noqa: F401
)

# Split into a sibling for the 350-effective-LOC module-size gate
# (PRD-CORE-311-FR07 made room for the new ``sync_health`` row below); the
# facade re-export keeps `_run_doctor` and every test's existing import path
# (`from trw_mcp.server._subcommands_doctor import _resolve_target_config`)
# working unchanged.
from trw_mcp.server._doctor_target_config import resolve_target_config as _resolve_target_config
from trw_mcp.server._doctor_user_yaml import check_user_yaml as _check_user_yaml  # noqa: F401
from trw_mcp.shared_server._doctor import check_shared_mcp as _check_shared_mcp  # noqa: F401

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

    overrides = raw if isinstance(raw, dict) else {}
    overrides = {str(k): v for k, v in overrides.items() if v is not None}
    overrides.pop("platform_api_key", None)
    try:
        TRWConfig(**overrides)
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
# resolution, single-source-pointer detection);
# these are the thin CheckResult-wrapping callers the catalogue dispatches to.


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


def _check_memory_backend(target: Path, _config: TRWConfig) -> CheckResult:
    """PRD-CORE-280 FR05: the row resolves through ``selected_store``, never a raw backend.

    Delegates to the ``_doctor_memory_store`` sibling (kept out of this file for
    the module-size gate, the same reason ``_check_memory_wal`` and
    ``_check_memory_daemon`` do below).
    """
    from trw_mcp.server._doctor_memory_store import memory_backend_row

    status, message = memory_backend_row(target)
    return CheckResult("memory_backend", cast("DoctorStatus", status), message)


def _check_memory_ledger_sample(target: Path, config: TRWConfig) -> CheckResult:
    """PRD-CORE-334 FR04: advisory ``memory-ledger-sample`` row, the namespace's exact decision count."""
    from trw_mcp.server._doctor_memory_store import memory_ledger_row

    status, message = memory_ledger_row(target, config)
    return CheckResult("memory-ledger-sample", cast("DoctorStatus", status), message)


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


# ── learning L-LhQe: a leftover pre-fix per-namespace warm.db orphan ──────────


def _check_memory_warm_legacy(target: Path, config: TRWConfig) -> CheckResult:
    """Report a leftover legacy per-namespace ``warm.db``, never deleting it.

    Delegates to the ``_doctor_memory_warm_legacy`` sibling (same shape as
    ``_check_memory_wal``, kept out of this file for the module-size gate).
    """
    from trw_mcp.server._doctor_memory_warm_legacy import memory_warm_legacy_row

    status, message = memory_warm_legacy_row(target, config)
    return CheckResult("memory_warm_legacy", cast("DoctorStatus", status), message)


# ── PRD-CORE-253-FR03: loopback memory-daemon reachability ───────────────────


def _check_memory_daemon(target: Path, _config: TRWConfig) -> CheckResult:
    """Report the user-space memory daemon's reachability, pid, uptime and store.

    Delegates to the ``_doctor_memory_daemon`` sibling (kept out of this file
    for the module-size gate). It PROBES and never starts: a diagnostic that
    spawned a daemon would always report one.
    """
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    status, message = memory_daemon_row(target)
    return CheckResult("memory_daemon", cast("DoctorStatus", status), message)


# ── PRD-SEC-014-FR04: embedding cache state + egress posture ─────────────────


def _check_embedding_egress(target: Path, config: TRWConfig) -> CheckResult:
    """Report the daemon's embedding model cache state and the effective egress posture.

    Delegates to the ``_doctor_embedding_egress`` sibling (kept out of this file
    for the eLOC gate). Fail-open: an unanswerable cache probe becomes a WARN row
    with the conservative posture, never an aborted report.
    """
    from trw_mcp.server._doctor_embedding_egress import embedding_egress_report
    from trw_mcp.state._retrieval_capability import daemon_embedding_model

    enabled = bool(getattr(config, "embeddings_enabled", False))
    status, message = embedding_egress_report(
        daemon_embedding_model(), embeddings_enabled=enabled, trw_dir=target / ".trw"
    )
    return CheckResult("embedding_egress", cast("DoctorStatus", status), message)


# ── docs/sprint-mcp7/PLAN.md §3b item 3: retrieval capability ────────────────


def _check_retrieval(target: Path, config: TRWConfig) -> CheckResult:
    """Report vectors / embeddings / weights as active, degraded or off, with the fix."""
    from trw_mcp.state._retrieval_capability import daemon_embedding_model, probe_retrieval, retrieval_doctor_row

    status, message = retrieval_doctor_row(
        probe_retrieval(daemon_embedding_model(), embeddings_enabled=config.embeddings_enabled), target / ".trw"
    )
    return CheckResult("retrieval", cast("DoctorStatus", status), message)


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


def _check_dispatch_credentials(_target: Path, _config: TRWConfig) -> CheckResult:
    """Each OAuth dispatch client's login state, expiry and credential lock, as metadata (PRD-CORE-304-FR04)."""
    from trw_mcp.dispatch._credentials import credential_report

    status, message, rows = credential_report()
    return CheckResult("dispatch_credentials", cast("DoctorStatus", status), message, data=tuple(rows))


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


# ── PRD-INFRA-189: environment parity ────────────────────────────────────────


def _check_gnu_timeout(_target: Path, _config: TRWConfig) -> CheckResult:
    from trw_mcp.server._doctor_environment import gnu_timeout_row

    return CheckResult("gnu_timeout", *gnu_timeout_row())


def _check_foreign_client_paths(target: Path, _config: TRWConfig) -> CheckResult:
    from trw_mcp.server._doctor_environment import foreign_client_paths_row

    return CheckResult("foreign_client_paths", *foreign_client_paths_row(target))


def _check_claude_code_version(_target: Path, config: TRWConfig) -> CheckResult:
    from trw_mcp.server._doctor_environment import claude_code_version_row

    return CheckResult(
        "claude_code_version", *claude_code_version_row(int(config.dispatch.dispatch_version_probe_timeout_s))
    )


def _check_stray_servers(target: Path, _config: TRWConfig) -> CheckResult:
    from trw_mcp.server._doctor_environment import stray_servers_row

    return CheckResult("stray_servers", *stray_servers_row(target))


def _check_hook_python(target: Path, _config: TRWConfig) -> CheckResult:
    from trw_mcp.server._doctor_hook_python import hook_python_row

    return CheckResult("hook_python", *hook_python_row(target))


def _check_jev(target: Path, config: TRWConfig) -> CheckResult:
    from trw_mcp.server._doctor_jev import jev_row

    return CheckResult("jev", *jev_row(target, config))


# ── PRD-FIX-149-FR07: doctor cannot silently disagree with version-status ────


def _check_version_status_compatible(target: Path, _config: TRWConfig) -> CheckResult:
    """WARN when ``collect_version_status()`` reports ``compatible: false``.

    Delegates to the ``_doctor_version_status`` sibling (kept out of this file
    for the eLOC gate); it reads the same layer ``version-status`` itself does,
    so the two cannot disagree.
    """
    from trw_mcp.server._doctor_version_status import version_status_row

    return CheckResult("version_status", *version_status_row(target))


def _check_mcp_security(target: Path, _config: TRWConfig) -> CheckResult:
    """PRD-CORE-300 slice S3a: the same status ``trw-mcp telemetry security`` reports.

    WARN when a recent shadow anomaly or an active quarantine is recorded;
    PASS otherwise. Reads *target*/.trw/context directly (like ``_check_trw_dir``)
    rather than through ``resolve_trw_dir()``'s config/cwd resolution, so this
    check honors the doctor's own ``target_dir`` argument rather than the
    process's cwd. Read-only; never raises (NFR-8 fail-open) — a resolution
    error becomes a FAIL row for this check alone, per the fail-open-isolated
    contract every doctor check keeps.
    """
    from trw_mcp.tools.mcp_security_status import compute_security_status

    try:
        status = compute_security_status(events_dir=target / ".trw" / "context").model_dump()
    except Exception as exc:  # justified: doctor checks are fail-open isolated
        return CheckResult("mcp_security", "FAIL", f"check raised: {exc}")
    from trw_mcp.server._doctor_mcp_security import security_row

    return CheckResult("mcp_security", *security_row(status, target))


def _check_pipeline_health(target: Path, config: TRWConfig) -> CheckResult:
    """WARN when the compounding-pipeline health surface reports a degraded signal."""
    from trw_mcp.server._doctor_pipeline_health import pipeline_health_row

    return CheckResult("pipeline_health", *pipeline_health_row(target, config))


def _check_distill(target: Path, config: TRWConfig) -> CheckResult:
    """trw-distill install state; never FAILs (optional, proprietary; 2026-09-26 audit)."""
    from trw_mcp.server._doctor_distill import distill_row

    return CheckResult("distill", *distill_row(target, config))


def _check_distill_ingest(target: Path, config: TRWConfig) -> CheckResult:
    """Last incremental-ingest preflight outcome; never FAILs (2026-09-27 audit)."""
    from trw_mcp.server._doctor_distill_ingest import distill_ingest_row

    return CheckResult("distill_ingest", *distill_ingest_row(target, config))


def _check_trw_trash(target: Path, config: TRWConfig) -> CheckResult:
    """Size of ``.trw/trash`` (backups TRW never deletes itself); read-only."""
    from trw_mcp.server._doctor_trash import trash_row

    return CheckResult("trw_trash", *trash_row(target, config))


def _check_codex_observation(_target: Path, _config: TRWConfig) -> CheckResult:
    """Whether codex's own run record is readable (CODEX-P0-A S3). Never FAILs."""
    from trw_mcp.server._doctor_codex_observation import codex_observation_row

    return CheckResult("codex_observation", *codex_observation_row())


def _check_hint_delivery(target: Path, config: TRWConfig) -> CheckResult:
    """RECORDED pre-edit hint tiers/fallback share; HINT-DELIVERY-CANARY. Never FAILs."""
    return CheckResult("hint_delivery", *_hint_delivery_row(target, config))


def _check_sync_health(target: Path, config: TRWConfig) -> CheckResult:
    """WARN on a degraded sync push, SKIP when unreadable/absent, else PASS.

    Delegates to the ``_doctor_sync_health`` sibling, which wraps the EXISTING
    ``step_sync_health`` read unchanged (PRD-CORE-311-FR07) -- no new
    detection, only a new surface.
    """
    from trw_mcp.server._doctor_sync_health import sync_health_row

    # step_sync_health reads <trw_dir>/sync-state.json, so it gets the .trw directory, not the project root.
    status, message = sync_health_row(target / ".trw", config)
    return CheckResult("sync_health", cast("DoctorStatus", status), message)


# ── Catalogue + orchestration ────────────────────────────────────────────────

_CheckFn = Callable[[Path, TRWConfig], CheckResult]

# _CHECKS itself is imported above from _doctor_checks_registry.py (pure data,
# extracted to keep this file under its 350 effective-LOC gate); the named
# functions still resolve against THIS module's globals at run time
# (test-monkeypatch indirection is unaffected by where the tuple lives).


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


# ── PRD-CORE-316 P3: checkout-access race-loser fd count ─────────────────────


def _check_checkout_access(_target: Path, _config: TRWConfig) -> CheckResult:
    """Report the checkout-access pinned-fd cache's race-loser descriptor count.

    Delegates to the ``_doctor_checkout_access`` sibling (kept out of this file for the
    module-size gate). Always PASS: the count is diagnostic, not a defect signal.
    """
    from trw_mcp.server._doctor_checkout_access import checkout_access_row

    status, message = checkout_access_row()
    return CheckResult("checkout_access", cast("DoctorStatus", status), message)


def _format_human(results: list[CheckResult], overall: str) -> str:
    glyph = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL", "SKIP": "SKIP"}
    lines = [f"[{glyph[r.status]}] {r.name}: {r.message}" for r in results]
    lines.append("")
    lines.append(f"doctor: {overall.upper()} ({len(results)} checks)")
    return "\n".join(lines)


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
