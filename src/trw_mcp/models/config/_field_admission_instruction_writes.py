"""Admission records for the PRD-FIX-123 instruction-write guard fields.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`FIELD_ADMISSIONS`. Split out for the same reason the
registry itself was split from ``_field_admission.py``: the table grows once per
new public field, and four admissions landing at once would push the registry
past the module-size gate.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "docs/requirements-aare-f/prds/PRD-FIX-123-instruction-sync-must-not-destroy-user-content.md"
_GUARD = "trw_mcp.state.claude_md._write_guard.guarded_instruction_write"

#: The four guard tunables. Every one is read on the write path; none is an
#: on/off switch for the fix itself (PRD-FIX-123-FR02 forbids a config flag that
#: could re-enable destroying user content — ``force`` is a call argument only).
INSTRUCTION_WRITE_ADMISSIONS: dict[str, ConfigAdmission] = {
    "instruction_write_max_total_shrink_fraction": ConfigAdmission(
        field_name="instruction_write_max_total_shrink_fraction",
        owner="PRD-FIX-123-FR02",
        consumer=_GUARD,
        default_rationale=(
            "Defaults to 0.25: a candidate that removes more than a quarter of an instruction "
            "file's total bytes is refused without force. Chosen as the secondary floor only — "
            "the incident it backstops GREW the file, so the primary floor is measured on the "
            "non-generated region and this one catches wholesale replacement."
        ),
        interaction_analysis=(
            "Evaluated by the guard AFTER the non-generated floor, so a write that destroys user "
            "content is always reported as `non_generated_shrink` rather than masked by a total "
            "measurement. Bypassed only by the `force` call argument, never by config."
        ),
        deprecation_plan="Retain; the sole total-shrink threshold. Removing it reintroduces a magic number.",
        docs_pointer=_DOCS,
        test_pointer=(
            "trw-mcp/tests/test_instruction_write_guard.py::TestShrinkFloor::"
            "test_total_shrink_floor_uses_the_configured_fraction"
        ),
        budget_decision="admitted",
    ),
    "instruction_backup_retention": ConfigAdmission(
        field_name="instruction_backup_retention",
        owner="PRD-FIX-123-FR04",
        consumer="trw_mcp.state.claude_md._write_backup.backup_instruction_file",
        default_rationale=(
            "Defaults to 10 pre-write copies per instruction filename — enough history to recover "
            "from an unnoticed sync several sessions later, bounded so a chatty bootstrap loop "
            "cannot fill the project directory."
        ),
        interaction_analysis=(
            "Read only by the retention prune that runs immediately after each backup copy lands, "
            "scoped to one filename's siblings in instruction_backup_dir. Independent of the "
            "shrink floors, which decide whether a write happens at all."
        ),
        deprecation_plan="Retain while guarded writes take backups; removal would make retention unbounded.",
        docs_pointer=_DOCS,
        test_pointer=(
            "trw-mcp/tests/test_instruction_write_guard.py::TestBackup::test_backup_precedes_write_and_retention_prunes"
        ),
        budget_decision="admitted",
    ),
    "instruction_backup_dir": ConfigAdmission(
        field_name="instruction_backup_dir",
        owner="PRD-FIX-123-FR04",
        consumer="trw_mcp.state.claude_md._write_backup.resolve_backup_dir",
        default_rationale=(
            "Defaults to '.trw/backups/instructions' so backups live beside the other TRW runtime "
            "artifacts and are covered by the bundled .trw/.gitignore rule this PRD adds."
        ),
        interaction_analysis=(
            "Resolved relative to the project root and containment-checked with is_path_within "
            "before any copy; an escaping value REFUSES the write (fail-closed) rather than "
            "degrading, unlike instruction_external_filename which falls back to inline."
        ),
        deprecation_plan="Retain; operators relocating .trw need the backup directory to follow.",
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/test_instruction_write_guard.py::TestSecurity::test_backup_path_escape_is_refused",
        budget_decision="admitted",
    ),
    "instruction_write_block_delta_tolerance_bytes": ConfigAdmission(
        field_name="instruction_write_block_delta_tolerance_bytes",
        owner="PRD-FIX-123-FR02",
        consumer="trw_mcp.state.claude_md._write_measure._shrink_is_trw_accounted",
        default_rationale=(
            "Defaults to 64 bytes — comfortably above the separator churn the merge itself "
            "produces (a handful of newlines) and comfortably below a lost line of prose, so "
            "a shrink the TRW block's own size change does not explain still reaches the floor."
        ),
        interaction_analysis=(
            "Read only when the total floor is about to fire, to decide whether the drop is "
            "attributable to TRW's own marker span. Raising it widens the exemption and weakens "
            "the secondary floor; it has no effect on the primary non-generated floor, which is "
            "an exact comparison and is evaluated first."
        ),
        deprecation_plan=(
            "Retain; without it the attribution test would carry a magic number, and setting it "
            "to 0 would refuse correct writes whose separators the merge normalised."
        ),
        docs_pointer=_DOCS,
        test_pointer=(
            "trw-mcp/tests/test_instruction_write_guard.py::TestShrinkFloor::"
            "test_marker_bearing_candidate_whose_block_delta_does_not_explain_the_drop_is_refused"
        ),
        budget_decision="admitted",
    ),
    "instruction_dry_run_diff_max_lines": ConfigAdmission(
        field_name="instruction_dry_run_diff_max_lines",
        owner="PRD-FIX-123-FR03",
        consumer=_GUARD,
        default_rationale=(
            "Defaults to 400 diff lines — larger than any realistic TRW block change, small enough "
            "that a caller cannot force an unbounded payload through the MCP transport (NFR03)."
        ),
        interaction_analysis=(
            "Read only on the dry_run path. Exceeding it truncates the DIFF and sets "
            "diff_truncated=True in the payload; it never bounds a file, and it has no effect on "
            "the write path or on either shrink floor."
        ),
        deprecation_plan="Retain while dry_run returns diffs; removal would uncap the payload.",
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/test_instruction_write_guard.py::TestDryRun::test_diff_payload_is_bounded",
        budget_decision="admitted",
    ),
}

__all__ = ["INSTRUCTION_WRITE_ADMISSIONS"]
