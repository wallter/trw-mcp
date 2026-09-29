"""Fixed exporter-authored wording for the evidence pack (PRD-CORE-323 FR05).

Standard library only, so the CLI parser can import the help lines without
loading the section builders.
"""

from __future__ import annotations

SCHEMA = "trw.evidence_pack.v1"

POSTURE_LINE = "Evidence pack for readiness review. Not a conformance, certification or compliance claim."
REDACTION_LIMIT_LINE = "Redaction is pattern-based; not exhaustive."
VERIFY_LIMIT_LINE = "Verification shows integrity since export; not authorship or truth."

#: The three fixed lines, in header order. The verb's help text carries them verbatim.
LIMIT_LINES: tuple[str, ...] = (POSTURE_LINE, REDACTION_LIMIT_LINE, VERIFY_LIMIT_LINE)

AS_OF_RUN_RECORD = "run_record"
AS_OF_EXPORT_SNAPSHOT = "export_snapshot"

LABEL_OBSERVED = "observed"
LABEL_REDACTED = "redacted"
LABEL_UNKNOWN = "unknown"
