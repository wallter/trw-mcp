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

import os
from typing import Literal

from pydantic import Field, field_validator


def _detect_model_family_from_env() -> str:
    """Best-effort detection of model family from well-known environment variables.

    Checks common env vars used by Claude Code, OpenAI, and other clients.
    Returns "generic" when no recognisable family is found — a stable non-empty
    fallback that avoids silent empty-string tagging (P1-A fix).
    """
    # Claude / Anthropic
    for var in ("CLAUDE_MODEL", "ANTHROPIC_MODEL", "CLAUDE_CODE_MODEL"):
        val = os.environ.get(var, "").strip()
        if val:
            return val.split("-")[0] if "-" in val else val
    # OpenAI / Codex
    for var in ("OPENAI_MODEL_NAME", "OPENAI_MODEL", "CODEX_MODEL"):
        val = os.environ.get(var, "").strip()
        if val:
            return val.split("-")[0] if "-" in val else val
    # Generic TRW override
    val = os.environ.get("TRW_MODEL_FAMILY_HINT", "").strip()
    if val:
        return val
    return "generic"


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
    # otel_endpoint removed under PRD-CORE-291 (slice 2): no production
    # reader, only a test pinning the default.
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
    # subsystem for them to configure. ``framework_overhead_threshold`` was kept
    # then on the claim that it "IS read" -- which the corrected consumer scan
    # (PRD-QUAL-139) shows was never true in any corpus, Python, shell or alias.
    # It followed the velocity_* fields out on 2026-09-16 (PRD-QUAL-139-FR05):
    # no consumer, no originating PRD, no test. The key is listed in
    # trw_mcp/data/config-retired-keys.json.

    # -- C-5 Model Generation Preparedness (PRD-CORE-105-FR01) --

    model_family: str = Field(
        default="",
        description=(
            "Model family tag for surface telemetry and nudge logs (C-5). "
            "When empty, best-effort auto-detection is applied and 'generic' is used "
            "as a stable fallback so production logs never carry empty model_family. "
            "Set via env var TRW_MODEL_FAMILY or config.yaml key model_family."
        ),
    )

    @field_validator("model_family", mode="after")
    @classmethod
    def _resolve_model_family(cls, v: str) -> str:
        """Resolve model_family to a non-empty string (P1-A fix).

        If the configured value is empty, attempt environment-based detection
        and fall back to 'generic'. This ensures surface telemetry and nudge
        logs always carry a meaningful model_family tag.
        """
        if v and v.strip():
            return v.strip()
        return _detect_model_family_from_env()
