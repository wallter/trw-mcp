"""Boot-path and storage-maintenance policy fields (PRD-CORE-248).

Its own domain mixin rather than more lines in ``_fields_memory.py`` and
``_fields_paths.py``: both were within a couple of lines of the 200 raw-line
ceiling the PRD-CORE-089 architecture guard enforces
(``tests/test_config_fields.py::test_domain_mixin_files_under_200_lines``), and
these four knobs are one coherent policy — when a checkpoint is due, how often
the question is asked, and how long the post-``initialize`` boot step may take
before it is abandoned to the first-tool-call fallback.

``wal_checkpoint_threshold_mb`` moves here unchanged in default and name; FR04
only converts it from the bare annotated integer ``wal_checkpoint_threshold_mb:
int = 10`` into a bounded, described ``Field``.
"""

from __future__ import annotations

from pydantic import Field


class _BootMaintenanceFields:
    """Boot + storage-maintenance domain mixin — mixed into _TRWConfigFields via MI."""

    # The checkpoint used to fire on size ALONE, from session-start ALONE, and
    # was cancelled outright whenever two writers were live -- which is the
    # steady state on a machine running two editors. Measured consequence in
    # this repository: a 25.0 MiB memory.db-wal against this 10 MB threshold.
    # These four knobs are magnitude tunables only; none of them switches the
    # fix off.
    wal_checkpoint_threshold_mb: int = Field(
        default=10,
        ge=1,
        le=1024,
        description=(
            "WAL size (MiB) at or above which a checkpoint is due. One of the two independent "
            "triggers; see wal_checkpoint_max_age_seconds for the other. This is a TRIGGER, not a "
            "cap -- and neither is trw-memory's journal_size_limit, which is a truncation target "
            "applied when the WAL resets rather than a ceiling enforced on an active WAL "
            "(storage/_connection.py::WAL_JOURNAL_SIZE_LIMIT_BYTES, 64 MiB, 6.4x this default). On "
            "a SQLite engine below 3.51.3 only PASSIVE may run, and PASSIVE never truncates, so the "
            "file climbs toward that target and stays there while this trigger keeps firing to no "
            "visible effect -- which is why the doctor's memory_wal row tracks an EFFECTIVE "
            "checkpoint clock separately and names the engine upgrade as the remedy."
        ),
    )
    wal_checkpoint_max_age_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description=(
            "Seconds since the last SUCCESSFUL checkpoint after which one is due regardless of WAL "
            "size. A store with no recorded timestamp is treated as due, so a fresh checkout "
            "checkpoints once early rather than waiting for the size trigger."
        ),
    )
    wal_checkpoint_idle_interval_seconds: int = Field(
        default=60,
        ge=5,
        le=3600,
        description=(
            "Interval (seconds) between idle-sweep evaluations on the 'trw-wal-checkpoint' daemon "
            "thread. An evaluation with no trigger satisfied costs one stat call and opens no SQLite "
            "connection, so a server that is idle stays idle."
        ),
    )
    # -- Deferred boot work (PRD-CORE-248 FR01) --
    boot_deferred_work_budget_ms: int = Field(
        default=5000,
        ge=100,
        le=120000,
        description=(
            "Budget (ms) for the post-'initialize' backend-sync resolution scheduled by "
            "PRD-CORE-248 FR01. Exceeding it abandons the scheduled attempt with a "
            "boot_deferred_work_budget_exceeded warning; the first tool call then re-runs the "
            "resolution inline, so no tool can observe an unresolved sync configuration. Sized "
            "well above the measured 12.6 ms of that block so a non-zero rate means the work "
            "acquired new I/O, not that the budget is tight."
        ),
    )
