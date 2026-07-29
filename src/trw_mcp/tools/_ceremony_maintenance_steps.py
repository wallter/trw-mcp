"""Fail-open maintenance sub-steps for session_start auto-maintenance.

Belongs to the ``_ceremony_helpers.py`` facade. Re-exported there for
back-compat. ``run_auto_maintenance`` keeps the orchestration and calls each
sub-step defined here, so the parent stays under the 350 effective-LOC module
gate — the same split already used by ``_ceremony_embeddings_maintenance.py``
for the embeddings sub-step.

Every helper is fail-open: an individual failure is logged and swallowed so it
can never block ``trw_session_start``.

Logging goes through :func:`_facade_logger` rather than a module-level logger.
Tests patch ``trw_mcp.tools._ceremony_helpers.logger`` and assert on warnings
emitted from these sub-steps, so the name is resolved through the parent module
at call time to keep those monkeypatches effective.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import AutoMaintenanceDict


def _facade_logger() -> Any:
    """Return the ``_ceremony_helpers`` logger, resolved at call time.

    Late lookup through the parent module (not an import-time binding) so a
    test monkeypatch on ``_ceremony_helpers.logger`` is observed here.
    """
    from trw_mcp.tools import _ceremony_helpers

    return _ceremony_helpers.logger


def _check_version_sentinel(
    trw_dir: Path,
    maintenance: AutoMaintenanceDict,
) -> None:
    """Detect if the installer wrote a newer version since this process started.

    The installer writes ``.trw/installed-version.json`` after upgrading.
    If the on-disk version is newer than the running version, inject an
    ``update_advisory`` telling the user to run ``/mcp`` to reload.
    """
    sentinel = trw_dir / "installed-version.json"
    if not sentinel.is_file():
        return

    try:
        data = json.loads(sentinel.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    installed_version = str(data.get("version", ""))
    if not installed_version:
        return

    # Compare with running version
    try:
        from importlib.metadata import version as pkg_version

        running_version = pkg_version("trw-mcp")
    except Exception:  # justified: importlib.metadata may fail in edge cases
        return

    # Potemkin defect D (sub_zAfRqZYYq2KtF72d): fire ONLY when the on-disk
    # installed version is genuinely NEWER than the running process — a real
    # pending upgrade that a ``/mcp`` reload would apply. The previous bare
    # ``!=`` check also fired when on-disk was OLDER than (or differently
    # formatted from) the running version, e.g. a stale sentinel left by a
    # downgrade or a server that out-lived the on-disk install. That produced
    # the confusing "vOLD was installed but still running vNEW — reload"
    # advisory the operator reported (reloading would DOWN-grade, not update).
    # Reuse the canonical semver comparator so the direction logic lives in one
    # place; it fails closed (no advisory) on any unparseable version.
    from trw_mcp.state.auto_upgrade import _compare_versions

    if _compare_versions(running_version, installed_version) and "update_advisory" not in maintenance:
        maintenance["update_advisory"] = (
            f"TRW v{installed_version} is installed on disk but this MCP server is still "
            f"running v{running_version}. Run /mcp to reload."
        )


def _run_learn_journal_drain(
    trw_dir: Path,
    config: TRWConfig,
    maintenance: AutoMaintenanceDict,
    *,
    defer_memory_heavy: bool,
    defer_reason: str,
    writer_pids: list[int],
) -> None:
    """Replay any learnings journaled-but-not-stored by an interrupted session.

    This is the REAL CONSUMER that closes the durability loop: a write-ahead
    record with no drain is itself the same defect class it guards against. Runs
    on every session_start; a no-op (and zero payload cost) when nothing is
    pending.

    Writer pressure THROTTLES this sweep, it no longer cancels it
    (PRD-INFRA-171-FR06). Cancelling was measured to be permanent — one peer MCP
    instance is enough to trip the pressure gate, so across 122 log files 42
    records were journaled and not one sweep ever ran. Under pressure the sweep
    now replays a bounded budget (see
    :func:`~trw_mcp.state.learn_journal.pressure_drain_budget`): a small
    minimum-progress floor plus any record past the age bound. That keeps the
    deferral's intent — recovery must not fight a live writer for the memory
    backend — as a SMALLER sweep rather than as no sweep, and the deferral
    advisory is still emitted for whatever the budget could not take.
    """
    if not config.learn_journal_enabled:
        return
    try:
        from trw_mcp.state import learn_journal
        from trw_mcp.tools._learn_journal_wiring import replay_journaled_learn

        limit = config.learn_journal_drain_limit
        if defer_memory_heavy:
            if learn_journal.pending_count(trw_dir, learnings_dir=config.learnings_dir) == 0:
                return
            limit = learn_journal.pressure_drain_budget(
                trw_dir,
                drain_limit=config.learn_journal_drain_limit,
                min_batch=config.learn_journal_drain_min_batch,
                max_age_seconds=config.learn_journal_pending_max_age_hours * 3600.0,
                learnings_dir=config.learnings_dir,
            )
            if limit <= 0:
                _defer_learn_journal_drain(config, maintenance, defer_reason, writer_pids)
                return

        drain_result = learn_journal.drain_pending(
            trw_dir,
            lambda lid, payload: replay_journaled_learn(trw_dir, config, lid, payload),
            limit=limit,
            learnings_dir=config.learnings_dir,
            max_attempts=config.learn_journal_max_replay_attempts,
        )
        if drain_result:
            maintenance["pending_learns_replayed"] = dict(drain_result)
            # FR06 (d): make sweep liveness observable from the ceremony layer.
            # Before this, only FAILURE was visible here — success lived solely in
            # the response payload, so the journaled-to-drained ratio could not be
            # measured from logs at all.
            _facade_logger().info(
                "learn_journal_drain_completed",
                replayed=int(drain_result.get("replayed", 0)),
                recovered=int(drain_result.get("recovered", 0)),
                dead_lettered=int(drain_result.get("dead_lettered", 0)),
                retained=int(drain_result.get("retained", 0)),
                deferred=int(drain_result.get("deferred", 0)),
                under_pressure=defer_memory_heavy,
            )
        if defer_memory_heavy and int(drain_result.get("deferred", 0)) > 0:
            _defer_learn_journal_drain(config, maintenance, defer_reason, writer_pids)
    except Exception:  # justified: fail-open, journal recovery must never block session start
        _facade_logger().warning("maintenance_learn_journal_drain_failed", exc_info=True)


def _defer_learn_journal_drain(
    config: TRWConfig,
    maintenance: AutoMaintenanceDict,
    defer_reason: str,
    writer_pids: list[int],
) -> None:
    """Record that pressure held records back — retained, not replaced, by FR06."""
    maintenance["pending_learns_deferred"] = _writer_pressure_details(config, defer_reason, writer_pids)
    _facade_logger().warning(
        "learn_journal_drain_deferred",
        reason=defer_reason,
        writer_count=len(writer_pids),
    )


def _writer_pressure_details(
    config: TRWConfig,
    defer_reason: str,
    writer_pids: list[int],
    *,
    retain_legacy_reason: bool = False,
) -> dict[str, object]:
    from trw_mcp.state.memory_pressure import writer_pressure_details

    return writer_pressure_details(
        defer_reason,
        writer_pids,
        threshold=config.session_start_writer_pressure_threshold,
        retain_legacy_reason=retain_legacy_reason,
    )


def _run_wal_maintenance(
    trw_dir: Path,
    config: TRWConfig,
    maintenance: AutoMaintenanceDict,
    *,
    defer_memory_heavy: bool,
    defer_reason: str,
    writer_pids: list[int],
) -> None:
    """Run or defer the WAL checkpoint without coupling its failures to other maintenance."""
    try:
        if defer_memory_heavy:
            maintenance["wal_checkpoint_deferred"] = _writer_pressure_details(config, defer_reason, writer_pids)
            _facade_logger().warning(
                "wal_checkpoint_deferred",
                reason=defer_reason,
                writer_pids=writer_pids,
                writer_count=len(writer_pids),
                threshold=config.session_start_writer_pressure_threshold,
            )
        else:
            from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

            wal_result = maybe_checkpoint_wal(trw_dir)
            if wal_result.get("checkpointed"):
                maintenance["wal_checkpoint"] = wal_result
    except Exception:  # justified: fail-open, WAL checkpoint must not block session start
        _facade_logger().warning("maintenance_wal_checkpoint_failed", exc_info=True)


__all__ = [
    "_check_version_sentinel",
    "_defer_learn_journal_drain",
    "_run_learn_journal_drain",
    "_run_wal_maintenance",
    "_writer_pressure_details",
]
