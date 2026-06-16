"""Telemetry, debug, OTEL, and velocity tracking fields.

Covers sections 26, 36, 51 (OTEL) of the original _main_fields.py:
  - Debug & telemetry
  - OTEL
  - Velocity tracking

MEAS-001 note:
  - ``pricing_table_path`` is the only field in this mixin currently consumed
    by the live H1 unified-telemetry path (``tool_call_timing`` /
    ``boot_audit``).
  - The remaining fields continue to govern legacy platform telemetry,
    OTEL, or usage logging and are not claimed as MEAS-001 config-E2E fields.
"""

from __future__ import annotations

from typing import Literal


class _TelemetryFields:
    """Telemetry domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Debug & telemetry --

    debug: bool = False
    logs_dir: str = "logs"
    telemetry: bool = False
    telemetry_enabled: bool = True
    telemetry_file: str = "tool-telemetry.jsonl"
    # PRD-SEC-004-FR05: separate consent for publishing learning CONTENT
    # (summary + detail) to the platform, distinct from anonymous usage
    # telemetry (platform_telemetry_enabled). Default off (privacy-forward):
    # publish_learnings() requires this True even when telemetry is enabled.
    # The Open-Question decision (PRD §12) is default-false with NO silent
    # auto-migration for existing platform_telemetry_enabled=true installs —
    # the tightening is intentional and disclosed in the CHANGELOG.
    learning_sharing_enabled: bool = False
    pricing_table_path: str = ""
    llm_usage_log_enabled: bool = True
    llm_usage_log_file: str = "llm_usage.jsonl"

    # -- OTEL --

    otel_enabled: bool = False
    otel_endpoint: str = ""
    # PRD-INFRA-145: span/attribute vocabulary. 'legacy' (default) keeps the
    # current tool.*/trw.* shape byte-identical so existing dashboards never
    # break; 'gen_ai' emits OpenTelemetry GenAI semantic-convention spans.
    # Default stays 'legacy' because the GenAI conventions are still
    # Development/Experimental upstream (forward-compat, non-breaking).
    otel_semconv: Literal["legacy", "gen_ai"] = "legacy"
    # PRD-INFRA-145-FR07: opt-in emission of gen_ai.input/output.messages
    # attributes. Default OFF (privacy-forward) — PII message bodies are never
    # attached unless an operator explicitly enables this AND otel_semconv is
    # 'gen_ai'; when on, every value passes through telemetry/anonymizer.py.
    otel_capture_messages: bool = False

    # -- Velocity tracking --

    velocity_alert_min_runs: int = 5
    velocity_alert_r_squared_min: float = 0.4
    framework_overhead_threshold: float = 0.30
    velocity_history_max_entries: int = 200
    velocity_stable_threshold: float = 0.05
    velocity_effective_q_threshold: float = 0.5
    velocity_sign_test_alpha: float = 0.1
    velocity_confounder_jump_ratio: float = 1.5
