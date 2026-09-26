"""Admission record for the W38 (7.0.0 security P1) platform-contact switch.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out for the same reason every other per-change admission table is: the
registry grows once per new public field and would otherwise drift past the
module-size gate each time one is admitted.

One field is admitted: ``platform_contact_enabled``, the operator kill
switch for both platform-egress contacts (the session-start update check and
the team-sync pull loop).

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "trw-mcp/README.md"
_TESTS = "trw-mcp/tests/test_platform_trust.py::test_platform_contact_enabled_switch_disables_both_contacts"

PLATFORM_EGRESS_ADMISSIONS: dict[str, ConfigAdmission] = {
    "platform_contact_enabled": ConfigAdmission(
        field_name="platform_contact_enabled",
        owner="W38-security-P1",
        consumer=(
            "trw_mcp.state._platform_trust.platform_contact_enabled, read directly (hard stop, no "
            "request attempted) by trw_mcp.state.auto_upgrade.check_for_update/_fetch_artifact_info, "
            "trw_mcp.sync.pull.SyncPuller.pull_intel_state, trw_mcp.sync.push.SyncPusher.push_learnings/"
            "push_outcomes, trw_mcp.telemetry.sender.BatchSender.send, "
            "trw_mcp.telemetry.publisher.publish_learnings, and trw_mcp.telemetry.pipeline."
            "TelemetryPipeline.flush_now; read indirectly, via platform_auth_headers "
            "(header-suppression only — the one remaining site whose request still proceeds "
            "unauthenticated when this is False), by trw_mcp.tools.submit_feedback."
            "submit_feedback_via_http, fired from an explicit agent-invoked MCP tool call rather "
            "than an automatic background contact (see interaction_analysis)"
        ),
        default_rationale=(
            "Defaults to True: byte-identical behavior for existing installs that already rely on "
            "the update check or team sync. TRW_PLATFORM_CONTACT_ENABLED=false gives the same "
            "result without editing config (PRD-CORE-302 W40 folded the old env-only offline switch into it)."
        ),
        interaction_analysis=(
            "A hard stop at every AUTOMATIC platform-egress call site (2026-09-25 sol re-review, "
            "P1-C round 2 closed the gap): auto_upgrade and pull read it at the START, before any "
            "URL/host resolution; push/sender/publisher/pipeline read it immediately after their "
            "own per-purpose consent gate (learning_sharing_enabled for push/publisher, "
            "platform_telemetry_enabled for sender/pipeline) and before any URL/client is touched — "
            "in every case False means no HTTP client is constructed and no request is attempted, "
            "queued data is left untouched for a future consented run. Agent-invoked sends are "
            "gated the same way (rc11): recall's remote augmentation and trw_status feedback "
            "(submit_feedback_via_http) send nothing while the switch is off. Operator-typed CLI "
            "commands with operator-supplied URLs (release push, doctor's backend probe, auth "
            "login, models fetch) are outside it. It does "
            "not affect trw_learn/trw_deliver platform TELEMETRY (platform_telemetry_enabled, a "
            "separate consent gate) or model downloads (install-time only)."
        ),
        deprecation_plan=(
            "Retain: it is the one switch for automatic platform contact. Uploads stay under "
            "platform_telemetry_enabled, and model downloads are install-time only."
        ),
        docs_pointer=_DOCS,
        test_pointer=_TESTS,
        budget_decision="admitted",
    ),
}

__all__ = ["PLATFORM_EGRESS_ADMISSIONS"]
