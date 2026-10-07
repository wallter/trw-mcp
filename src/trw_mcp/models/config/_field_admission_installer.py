"""Admission record for ``installer_answers`` (the installer's remembered answers).

Its own table module, merged by ``_field_admission_registry.py`` (the pattern PRD-FIX-123 established).
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

INSTALLER_ADMISSIONS: dict[str, ConfigAdmission] = {
    "installer_answers": ConfigAdmission(
        field_name="installer_answers",
        owner="installer: ask once, then remember",
        consumer=(
            "scripts/install-trw.template.py AnswerStore / ask_once: reads the map from the project and machine "
            "config.yaml before each interactive question and writes it with `trw-mcp config set "
            "installer_answers.<key>`; no TRW runtime path reads it"
        ),
        default_rationale=(
            "empty. An empty map means no question has been answered, so the installer asks each one the first "
            "time it matters; a headless or --json run never asks and never records."
        ),
        interaction_analysis=(
            "Independent of every behavioural flag. An explicit installer flag (--ai, --no-ai, --with-proprietary, "
            "--allow-system-python, ...) wins over a recorded answer, and --reconfigure ignores the record for "
            "one run. The map is a plain key -> bool, so a project config can carry its own choices while the "
            "machine config holds the per-interpreter system-Python decision."
        ),
        deprecation_plan=(
            "Retire if the installer stops asking interactive questions, or when its answers move into the "
            "dedicated fields they stand for."
        ),
        docs_pointer="docs/CLIENT-PROFILES.md",
        test_pointer="trw-mcp/tests/test_installer_answers.py",
        budget_decision="admitted",
    ),
}

__all__ = ["INSTALLER_ADMISSIONS"]
