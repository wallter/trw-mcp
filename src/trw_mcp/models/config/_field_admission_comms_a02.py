"""Admission records for PRD-CORE-274 Amendment 02 comms fields.

A sibling of ``_field_admission_comms.py`` (which sits near the 350 effective-LOC
gate) and merged into ``COMMS_ADMISSIONS`` there, so the registry import is
unchanged. Defaults are conservative policy choices, not measured optima.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_PRD = "docs/requirements-aare-f/prds/PRD-CORE-274-cross-harness-peer-messaging-slice-1.md"

AMENDMENT_02_ADMISSIONS: dict[str, ConfigAdmission] = {
    "comms_message_ttl_seconds": ConfigAdmission(
        field_name="comms_message_ttl_seconds",
        owner="PRD-CORE-274-FR13",
        consumer="trw_mcp.comms admission expiry (expires_at) and the FR16 upgrade backfill",
        default_rationale=(
            "86400 seconds (one day), bounded 300..604800. A policy choice, not a measured optimum: "
            "long enough for a member that is busy or reconnecting to still receive work, short "
            "enough that a stale request does not surface days later as if current."
        ),
        interaction_analysis=(
            "Stamped on each row at admission, so changing it affects only later admissions; an "
            "upgraded v3 row gets admitted_at plus the value in force at upgrade. Together with "
            "comms_retry_grace_seconds it defines the tombstone horizon (FR15)."
        ),
        deprecation_plan="Retain; durable delivery needs a finite horizon.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_storage_contract.py",
        budget_decision="admitted",
    ),
    "comms_candidate_ttl_seconds": ConfigAdmission(
        field_name="comms_candidate_ttl_seconds",
        owner="PRD-CORE-274-FR18",
        consumer="trw_mcp.comms bootstrap announce (candidate expires_at)",
        default_rationale=(
            "3600 seconds (one hour), bounded 60..86400. A policy choice, not a measurement: long "
            "enough for an orchestrator to discover and admit a client that announced first, short "
            "enough that an abandoned announcement stops being admissible the same working session."
        ),
        interaction_analysis=(
            "Stamped at announce; a re-announce by the same pin and run refreshes it. Expired records "
            "free their share of the 64-record registry cap and can no longer be admitted or picked up."
        ),
        deprecation_plan="Retain; an unbounded candidate would be admissible indefinitely.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_candidate_admission.py",
        budget_decision="admitted",
    ),
    "comms_group_body_budget_bytes": ConfigAdmission(
        field_name="comms_group_body_budget_bytes",
        owner="PRD-CORE-274-FR15",
        consumer="trw_mcp.comms._policy admission check (snapshotted per group as groups.body_budget)",
        default_rationale=(
            "16 MiB, bounded 64 KiB..16 MiB. With the 4096-row limit this is the NFR08 envelope: "
            "whole-mailbox verification measured 84 ms maximum at 4096 rows and 16 MiB of bodies "
            "on the development Mac (an environment observation, not a portable bound)."
        ),
        interaction_analysis=(
            "Snapshotted at group birth like the other admission policy, so a config reload never "
            "replenishes it. Tombstoning after the retry horizon frees bytes; exceeding it refuses "
            "group_storage_budget and evicts nothing."
        ),
        deprecation_plan="Retain until verification is incremental; then re-derive with new measurements.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_policy.py",
        budget_decision="admitted",
    ),
    "comms_retry_grace_seconds": ConfigAdmission(
        field_name="comms_retry_grace_seconds",
        owner="PRD-CORE-274-FR15",
        consumer="trw_mcp.comms._messages tombstone sweep",
        default_rationale=(
            "3600 seconds, bounded 0..86400. How long a terminal row keeps its body past its deadline, "
            "so a late exact retry still sees the original receipt. Policy choice, not a measurement."
        ),
        interaction_analysis=(
            "Only affects bodies; the row, receipt and canonical digest survive tombstoning, so exact-retry "
            "detection is unchanged at any value."
        ),
        deprecation_plan="Retain with tombstoning.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_policy.py",
        budget_decision="admitted",
    ),
}

__all__ = ["AMENDMENT_02_ADMISSIONS"]
