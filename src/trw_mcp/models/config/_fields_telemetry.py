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


class _TelemetryFields:
    """Telemetry domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Debug & telemetry --

    debug: bool = False
    logs_dir: str = "logs"
    telemetry: bool = False
    telemetry_enabled: bool = True
    telemetry_file: str = "tool-telemetry.jsonl"
    pricing_table_path: str = ""
    llm_usage_log_enabled: bool = True
    llm_usage_log_file: str = "llm_usage.jsonl"

    # -- OTEL --

    otel_enabled: bool = False
    otel_endpoint: str = ""

    # -- Velocity tracking --

    velocity_alert_min_runs: int = 5
    velocity_alert_r_squared_min: float = 0.4
    framework_overhead_threshold: float = 0.30
    velocity_history_max_entries: int = 200
    velocity_stable_threshold: float = 0.05
    velocity_effective_q_threshold: float = 0.5
    velocity_sign_test_alpha: float = 0.1
    velocity_confounder_jump_ratio: float = 1.5
