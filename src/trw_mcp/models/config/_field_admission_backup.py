"""Admission record for the PRD-CORE-311 remote-backup consent gate.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out for the same reason every other per-change admission table is: the
registry grows once per new public field and would otherwise drift past the
module-size gate each time one is admitted.

One field is admitted: ``backup_remote_enabled``, the consent gate for
``trw_mcp.sync.backup.BackupUploader`` — the off-machine leg of PRD-CORE-311's
full-store backup archive. It follows the same fail-closed pattern as
``learning_sharing_enabled`` (``_fields_telemetry.py``), but a backup archive
is the RAW local store (not an anonymized/redacted payload like learning
sync), so it is a distinct, more conservative gate rather than a reuse of
that flag.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "trw-mcp/README.md"
_TESTS = "trw-mcp/tests/test_sync_backup_uploader.py::test_upload_disabled_by_default_makes_no_request"

BACKUP_ADMISSIONS: dict[str, ConfigAdmission] = {
    "backup_remote_enabled": ConfigAdmission(
        field_name="backup_remote_enabled",
        owner="PRD-CORE-311-FR03",
        consumer="trw_mcp.sync.backup.BackupUploader.upload, checked before any presign POST is attempted",
        default_rationale=(
            "Defaults to False (fail-closed), mirroring learning_sharing_enabled. A gzip backup "
            "archive is the raw local store — vectors, graph edges, and run-state history included — "
            "not the redacted/anonymized summary+detail payload learning sync sends, so it carries "
            "materially more sensitive content off-machine and does not reuse that flag's default-on "
            "risk profile even implicitly."
        ),
        interaction_analysis=(
            "Read at the START of BackupUploader.upload, before any HTTP client is constructed: False "
            "means zero requests (no presign POST, no PUT) and the caller's local archive is left "
            "in place for a future consented upload. Even when True, platform_contact_enabled() (the "
            "operator's global egress kill switch) is checked immediately after — and re-checked "
            "before the PUT — so this field alone cannot cause egress while the global switch is off. "
            "Does not affect the LOCAL-only backup leg (trw-memory's create_backup_archive), which "
            "always runs regardless of this flag."
        ),
        deprecation_plan=(
            "Retain: it is the sole consent gate for a full-store off-machine backup upload, "
            "distinct from learning_sharing_enabled and platform_telemetry_enabled."
        ),
        docs_pointer=_DOCS,
        test_pointer=_TESTS,
        budget_decision="admitted",
    ),
}

__all__ = ["BACKUP_ADMISSIONS"]
