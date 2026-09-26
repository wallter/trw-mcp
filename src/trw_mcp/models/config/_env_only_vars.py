"""Registry of ``TRW_*`` environment variables read directly from ``os.environ``.

``trw-mcp config-reference`` (``server/_subcommands_misc.py``) built its table
from ``_TRWConfigFields.model_fields`` alone, so a variable read straight from
the environment -- never declared as a ``TRWConfig`` field -- was invisible to
the one command whose entire job is listing every env var this package reads
(W35 item 4; the README's config-reference paragraph claimed exactly the
opposite, that env-only variables are *not* listed).

This is the single source of truth for that second category. Keep it current
with every new ``os.environ``/``os.getenv`` read of a ``TRW_`` name that is
not also a ``TRWConfig`` field:
``tests/test_config_reference_env_only.py`` AST-scans ``src/`` for such reads
and fails when one is missing here (or when an entry here is stale and no
longer read anywhere -- see that test for both directions).
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["ENV_ONLY_VARS", "EnvOnlyVar"]


class EnvOnlyVar(NamedTuple):
    """One environment-only variable: never a ``TRWConfig`` field."""

    name: str
    description: str


ENV_ONLY_VARS: tuple[EnvOnlyVar, ...] = (
    EnvOnlyVar(
        "TRW_AGENT_ID",
        "Overrides the derived agent identity used in trust/escalation logging and telemetry spans.",
    ),
    EnvOnlyVar(
        "TRW_ALLOW_DIRTY_BUNDLE",
        "Set to 1 to let `update-project` proceed against a dirty bundled-data checkout.",
    ),
    EnvOnlyVar(
        "TRW_CHAIN_ID",
        "Fallback source-run identity for chain-evaluation runs when TRW_RUN_ID is unset.",
    ),
    EnvOnlyVar(
        "TRW_COMPACTION_GATE_MAX_BLOCKS",
        "Maximum PreCompact ceremony-gate blocks before the gate stops re-arming (default 2).",
    ),
    EnvOnlyVar(
        "TRW_CONFIG_STRICT",
        "Truthy value makes an invalid .trw/config.yaml raise instead of falling back to defaults.",
    ),
    EnvOnlyVar(
        "TRW_ENTITLEMENT_KEY",
        "HMAC key used to verify entitlement tokens; unset falls back to a derived default.",
    ),
    EnvOnlyVar(
        "TRW_HOT_PATH_STRICT",
        "Set to 1 to raise instead of falling back when a run path resolution hits the legacy mtime scan.",
    ),
    EnvOnlyVar(
        "TRW_LOG_LEVEL",
        "Overrides the structlog level (falls back to LOG_LEVEL, then config.debug/-v/-q).",
    ),
    EnvOnlyVar(
        "TRW_MODEL_FAMILY_HINT",
        "Overrides the inferred calling-model family used for capability-tier resolution.",
    ),
    EnvOnlyVar(
        "TRW_PROBE_BUDGET_OVERRIDE",
        "Overrides the experiment-probe budget guard for one `trw-mcp probe run` invocation.",
    ),
    EnvOnlyVar(
        "TRW_PROBE_ENABLED",
        "Gates `trw-mcp probe run` (sandboxed empirical probes); unset or falsy refuses.",
    ),
    EnvOnlyVar(
        "TRW_PROJECT_ROOT",
        "Overrides the resolved project root; used by audit/export/bootstrap/sync to scope a run.",
    ),
    EnvOnlyVar(
        "TRW_REPO_ROOT",
        "Overrides the resolved repo root used to locate the telemetry log path and channel stats.",
    ),
    EnvOnlyVar(
        "TRW_RUN_ID",
        "Preferred source-run identity for chain-evaluation runs (see also TRW_CHAIN_ID).",
    ),
    EnvOnlyVar(
        "TRW_SESSION_ID",
        "Stable per-connection identity for pin isolation, ceremony session resolution and telemetry.",
    ),
)
