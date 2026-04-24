"""Auto-detect client profile and model ID from environment signals.

PRD-CORE-099: Learning source provenance. Pure functions — no network
calls, no subprocess spawning, no exceptions raised to callers.

Detection strategy:
- **Client**: Check for IDE-specific env vars or config files.
- **Model**: Check for model-override env vars or config file values.

Both functions return ``""`` when detection fails.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Client profile detection
# ---------------------------------------------------------------------------

# Env-var prefixes that identify each client.  Checked in priority order.
# Signal strength rationale:
#   - claude-code: CLAUDE_CODE_* is auto-injected by Claude Code — high confidence
#   - codex: CODEX_CLI_VERSION is set by Codex CLI — high confidence
#   - cursor-ide: CURSOR_TRACE_ID is auto-injected by Cursor IDE — medium confidence
#     (cursor-cli uses CURSOR_API_KEY which is user-set, not included here)
#   - aider: AIDER_MODEL is user-set (not auto-injected) — medium confidence
#   - opencode: OPENCODE_* is user-set (not auto-injected) — lowest confidence
_CLIENT_SIGNALS: list[tuple[str, list[str]]] = [
    ("claude-code", ["CLAUDE_CODE_VERSION", "CLAUDE_CODE_ENTRYPOINT"]),
    ("codex", ["CODEX_CLI_VERSION", "CODEX_SANDBOX_TYPE"]),
    ("cursor-ide", ["CURSOR_TRACE_ID", "CURSOR_SESSION_ID"]),
    ("aider", ["AIDER_MODEL", "AIDER_CHAT_HISTORY_FILE"]),
    ("opencode", ["OPENCODE_MODEL", "OPENCODE_CONFIG"]),
]


def detect_client_profile(*, cwd: str | Path | None = None) -> str:
    """Detect the IDE/client profile from environment signals.

    Checks env vars first (fast), then falls back to filesystem markers.
    Returns one of: ``"claude-code"``, ``"opencode"``, ``"cursor-ide"``,
    ``"codex"``, ``"aider"``, or ``""`` (unknown).

    Note: ``cursor-ide`` is returned when Cursor IDE env vars are detected.
    ``cursor-cli`` detection requires filesystem checks (see ``detect_ide``).
    """
    # Phase 1: env var check
    for client_id, env_keys in _CLIENT_SIGNALS:
        if any(os.environ.get(k) for k in env_keys):
            logger.debug("client_detected_env", client=client_id)
            return client_id

    # Phase 2: filesystem markers (slower, checked only when env is empty)
    try:
        base = Path(cwd) if cwd else Path.cwd()
        if (base / ".opencode" / "opencode.json").is_file() or (base / "opencode.json").is_file():
            logger.debug("client_detected_fs", client="opencode")
            return "opencode"
        if (base / ".aider.conf.yml").is_file():
            logger.debug("client_detected_fs", client="aider")
            return "aider"
    except OSError:  # justified: fail-open, filesystem errors don't break detection
        pass

    return ""


# ---------------------------------------------------------------------------
# Model ID detection
# ---------------------------------------------------------------------------

# Env vars checked in priority order per client.
_MODEL_ENV_VARS: list[str] = [
    "CLAUDE_MODEL",
    "ANTHROPIC_MODEL",
    "OPENCODE_MODEL",
    "AIDER_MODEL",
    "OPENAI_MODEL",
]


def _parse_opencode_model(cwd: str | Path | None = None) -> str:
    """Extract model from opencode.json config file.

    OpenCode uses ``"provider/model"`` format (e.g., ``"anthropic/claude-sonnet-4-6"``).
    We return only the model portion after the slash.
    """
    try:
        base = Path(cwd) if cwd else Path.cwd()
        for candidate in [base / ".opencode" / "opencode.json", base / "opencode.json"]:
            if candidate.is_file():
                raw_text = candidate.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                raw_model = data.get("model", "")
                if isinstance(raw_model, str) and raw_model:
                    # Strip provider prefix: "anthropic/claude-sonnet-4-6" -> "claude-sonnet-4-6"
                    return raw_model.split("/", 1)[-1] if "/" in raw_model else raw_model
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return ""


def detect_model_id(*, cwd: str | Path | None = None) -> str:
    """Detect the AI model ID from environment variables or config files.

    Returns a model identifier string (e.g., ``"claude-opus-4-7"``) or
    ``""`` when detection fails.
    """
    # Phase 1: env var check
    for env_key in _MODEL_ENV_VARS:
        val = os.environ.get(env_key, "").strip()
        if val:
            # Normalize provider-prefixed formats
            model = val.split("/", 1)[-1] if "/" in val else val
            logger.debug("model_detected_env", model=model, source=env_key)
            return model

    # Phase 2: config file fallback (opencode.json)
    model = _parse_opencode_model(cwd)
    if model:
        logger.debug("model_detected_config", model=model)
        return model

    return ""
