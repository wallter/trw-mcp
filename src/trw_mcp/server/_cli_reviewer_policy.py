"""Deny-by-default CLI access policy for a bounded lane (PRD-CORE-305-FR05, sol round-1 P0 / round-2).

R6/round-1 enrolled individual verbs (``run adopt``, ``instructions sync``,
then ``local init``/``checkpoint``/``learn``) as ``state_changing`` in
``server/_cli_replacements.py`` -- an ALLOW-BY-DEFAULT design where a command
had to be explicitly named bad. That missed a wide reachable surface never
enrolled in either table: ``learn-drain``, ``memory migrate --apply``,
``sync pull --full``, and ``gc --no-dry-run`` all wrote protected state under
``TRW_SURFACE_ROLE=reviewer`` because nothing had ever listed them.

This module inverts the default. Under a bounded lane (reviewer role or a
dispatched child), a ``trw-mcp <verb...>`` invocation is refused UNLESS its
exact command path -- and, for a handful of dual-mode verbs, its actually
parsed args -- appear in :data:`_READ_ONLY_POLICY` below. Absence is a
refusal, not a pass: a brand-new verb, or a brand-new write-enabling flag on
an existing one, is denied the moment it exists, before anyone remembers to
classify it. ``tests/test_cli_reviewer_deny_by_default.py`` enumerates every
verb ``SUBCOMMAND_HANDLERS`` (``server/_subcommands.py``) actually dispatches
and fails closed if a name is missing from either this file's policy or that
test's own registered-elsewhere allowance -- so an unclassified verb fails a
test rather than silently working.

Each entry's predicate takes the parsed ``argparse.Namespace`` and returns
``True`` only when THIS invocation performs no write. Most predicates ignore
their argument (the command has no write-enabling flag at all); the handful
that read specific flags are commented with the exact dest examined, so a
reviewer changing that flag's default sees why the predicate exists.

ROUND-2 (sol round-2): round-1's allowlist was built from ``--help`` text and
docstrings, not the actual code path -- and two entries lied. ``hook-flags``
("publishes resolved switches") calls ``write_hook_flags``, which creates
``.trw/runtime/`` and writes a file every time. ``doctor`` ("read-only
diagnostics") opens the memory store during its backend check, which
checkpoints and rewrites the store's SQLite WAL, and makes a live network
request in its backend-connectivity check. ``memory migrate`` without
``--apply`` ("preview, writing nothing") still creates a temp directory and
copies the store's SQLite file into it. All three are removed. Every
surviving entry below is now backed by a code-path read cited in its comment
(function + effect), not a help string, AND by
``tests/test_cli_reviewer_write_freedom.py``, which actually RUNS every
allowlisted verb under the reviewer role inside a filesystem-snapshot +
network-trap fixture -- the evidence this module's claims must survive, not
prose asserting them.
"""

from __future__ import annotations

from collections.abc import Callable

ReviewerSafe = Callable[[object], bool]


#: Read-only regardless of args: no flag on the command can turn it into a
#: write. Grouped by the top-level verb family for reviewability.
_ALWAYS_SAFE_PATHS: frozenset[str] = frozenset(
    {
        # _run_config_reference (server/_subcommands_misc.py): iterates
        # TRWConfig field metadata + ENV_ONLY_VARS constants and prints;
        # touches no path, no store, no socket.
        "config-reference",
        # _check_instructions_core (server/_subcommands_check.py): reads
        # AGENTS.md/CLAUDE.md with Path.read_text if present and prints a
        # comparison; no write call anywhere in the function.
        "check-instructions",
        # run_tendencies (tools/_tendencies... via trw_mcp.tendencies.cli):
        # own module docstring "No artifact is mutated (NFR-04)"; builds and
        # prints a report over already-persisted corpus files, reads only.
        "tendencies",
        # read_local_status (services/orchestration_service.py): reads
        # run.yaml + counts checkpoints.jsonl/events.jsonl lines; no writer
        # import in the module at all.
        "local status",
        # run_local_recall -> execute_recall: a store lookup that raises
        # StoreUnavailableError before opening any socket when the checkout
        # has no project_namespace (measuring_only()-style refusal on config
        # alone); the ranking/read path itself calls no writer.
        "local recall",
        # compute_codebase_risk_report (tools/codebase_risk_report.py) via
        # resolve_current_sidecar: reads an existing distill sidecar file if
        # present, else returns a "missing"/"tier_required" result -- no
        # write branch in either function, and --repo-root skips the git
        # subprocess entirely (resolve_repo_root returns the arg verbatim).
        "code risk",
        # server/_subcommands_handoff.py: validate/digest/render read the named
        # file(s) and print; only `handoff seal` writes, so it stays unlisted.
        "handoff validate",
        "handoff digest",
        "handoff render",
        # _run_validate (cli/channel_doctor.py): reads manifest.yaml via
        # channels._manifest_loader.load if present, else prints an error;
        # no write call in the function.
        "channel-doctor validate",
        # _run_stats (cli/channel_doctor.py): compute_channel_stats reads a
        # jsonl log path (or reports absence); no write call in the function.
        "channel-doctor stats",
        # _run_scan (cli/channel_doctor.py): the `dry_run` local is used only
        # in the printed message -- the function has no unlink()/write call
        # at all, regardless of the flag's value.
        "channel-doctor scan",
        # _probe_budget (tools/_experiment_cli.py): module docstring
        # "probe budget reads that file and never creates it (FR-10)";
        # confirmed -- _save_state is called only from the run/propose paths.
        "probe budget",
        # tools/_telemetry_cli.py module docstring: "read-only queries over
        # already-persisted state ... none write" for all five S3a verbs.
        "telemetry events",
        "telemetry classify",
        "telemetry surface-diff",
        "telemetry security",
        "telemetry channel-stats",
        # step_pipeline_health (tools/_pipeline_health.py) module docstring:
        # "All probes are read-only. No writes to memory.db or state files";
        # store_health() runs under measuring_only(), which refuses an
        # unpinned checkout on config alone before any store/socket touch.
        "telemetry pipeline-health",
        # tools/_prd_cli.py has no write_text/mkdir/FileStateWriter call
        # anywhere in the module; a diff of two given files only.
        "prd diff",
        # tools/_profile_cli.py has no write_text/mkdir/FileStateWriter call
        # anywhere in the module; resolves + prints config layers.
        "profile explain",
        # run_auth_status -> device_auth_status (cli/auth.py): reads the
        # credentials file and config.yaml lines; makes no HTTP call (only
        # login/logout perform the device-auth network flow).
        "auth status",
        # collect_version_status (server/_subcommands_release.py): reads
        # pyproject.toml/package.json/VERSION.yaml; "live-server version" is
        # the imported __version__ constant, not a query to a running
        # server. No write, no socket.
        "version-status",
        # formation._views.status -> pause_roll_call -> read_pause/
        # read_manifest: file reads only; own docstring "Read-only member
        # roll-up" (also relied on by trw_status's "STRICTLY READ-ONLY"
        # formation block).
        "formation status",
        # list_outbox (tools/_feedback_cli.py): globs and reads .trw/feedback/{outbox,sent}; no write,
        # no socket. flush is the write + network path and stays denied.
        "feedback list",
    }
)

#: Read-only ONLY when the named dest holds the given value in the actually
#: parsed args -- the exact class of gap this module closes (a dual-mode verb
#: whose write path is a flag, not a different subcommand name).
_CONDITIONAL_SAFE_PATHS: dict[str, ReviewerSafe] = {
    # _run_gc (server/_subcommands_gc.py) passes dry_run straight to
    # sweep_stale_runs, whose own docstring: "dry_run: When True, compute the
    # report but do NOT mutate run.yaml or append events." The non-dry-run
    # candidate-ref cleanup branch is also gated on `if not dry_run`.
    "gc": lambda args: bool(getattr(args, "dry_run", True)),
    # _run_session_changelog (server/_subcommands.py) branches explicitly:
    # `if args.write: write_session_changelog(...) else: build_session_changelog(...)`
    # (no persistence call) -- read the branch, not just the --help text.
    "session-changelog": lambda args: not bool(getattr(args, "write", False)),
    # _run_clean (cli/channel_doctor.py): `if dry_run: removed.append(...)
    # else: candidate.unlink()` -- the unlink() call is unreachable when
    # dry_run is True.
    "channel-doctor clean": lambda args: bool(getattr(args, "dry_run", False)),
}


def is_reviewer_safe(command_path: str, args: object) -> bool:
    """True when *command_path*, given the actually parsed *args*, is a reviewed read.

    DENY BY DEFAULT: a command path absent from BOTH tables above is unsafe,
    full stop. A new CLI verb -- or a new write-enabling flag on an existing
    one -- is refused under a bounded lane until someone reviews it and adds
    an entry here. This is never derived from
    ``CLI_REPLACEMENTS.state_changing`` or any other per-migration registry:
    that PRD-CORE-300 contract covers only the tools it moved to the CLI, and
    ``learn-drain``/``memory migrate``/``sync pull``/``gc`` all predate it and
    were never in scope for it -- exactly how they went unclassified.
    """
    if command_path in _ALWAYS_SAFE_PATHS:
        return True
    predicate = _CONDITIONAL_SAFE_PATHS.get(command_path)
    if predicate is None:
        return False
    return bool(predicate(args))


def classified_command_paths() -> frozenset[str]:
    """Every command path this policy has an opinion on (safe or conditional)."""
    return _ALWAYS_SAFE_PATHS | frozenset(_CONDITIONAL_SAFE_PATHS)


__all__ = ["ReviewerSafe", "classified_command_paths", "is_reviewer_safe"]
