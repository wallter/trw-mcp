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

from pydantic import Field


class _TelemetryFields:
    """Telemetry domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Debug & telemetry --

    # THE portable log-verbosity toggle. No client bootstrap profile bakes
    # ``--debug`` into its generated MCP server entry (that divergence was
    # removed 2026-07-27), so this key — or ``TRW_DEBUG`` / ``TRW_LOG_LEVEL``
    # — is how an operator turns on DEBUG-level logging plus the
    # ``.trw/logs/trw-mcp-*.jsonl`` file sink, identically for every client.
    # Consumed by ``_logging.configure_logging`` (below --log-level /
    # TRW_LOG_LEVEL / --debug / -v / -q) and by ``server/_cli.py``.
    debug: bool = Field(
        default=False,
        description="Enable DEBUG-level logging and the .trw/logs/ file sink for every MCP client",
    )
    logs_dir: str = "logs"
    telemetry: bool = False
    telemetry_enabled: bool = True
    telemetry_file: str = "tool-telemetry.jsonl"
    # PRD-CORE-181-FR04 rotation knob. Size threshold at which
    # ``rotate_pipeline_telemetry_log`` seals the active pipeline-events.jsonl
    # into a new compressed segment. 10 MiB (10485760) matches the per-JSONL
    # rotation threshold used elsewhere in state/. Overridable without a code
    # edit via the ``TRW_TELEMETRY_LOG_MAX_BYTES`` env var (BaseSettings
    # env_prefix) or ``.trw/config.yaml``.
    telemetry_log_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        description="Pipeline telemetry-log rotation threshold in bytes (FR04)",
    )
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

    # -- Framework overhead --
    #
    # The seven velocity_* fields that used to sit here were removed 2026-07-28
    # (PRD-QUAL-131-FR01): no production reader, and no velocity-tracking
    # subsystem for them to configure. ``framework_overhead_threshold`` IS read
    # and stays; it was filed under the velocity heading but is not a velocity
    # field.
    framework_overhead_threshold: float = 0.30
