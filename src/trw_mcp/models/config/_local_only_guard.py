"""F5 follow-up (2026-09-25 sol re-review, P1): 'local_only' fails closed in trw-mcp too.

trw-memory's F5 fix (local_only raises ConfigError instead of warn-and-ignore)
only covers trw-memory's own ``MemoryConfig`` construction. trw-mcp reads the
SAME shared ``.trw/config.yaml`` into its own ``TRWConfig``, which never had a
``local_only`` field, so ``extra="ignore"`` silently dropped a leftover key as
one more generic "unrecognised config key" warning rather than refusing — a
trw-mcp-only process (e.g. a CLI subcommand that never constructs
``MemoryConfig``) never hit trw-memory's fail-closed floor at all.

Two distinct surfaces, checked at two distinct points because pydantic-settings'
``EnvSettingsSource`` never surfaces an environment variable that does not match
a declared field name into the values dict a model validator sees (verified
empirically: ``TRWConfig`` with a ``model_validator(mode="before")`` probe saw
an empty dict under ``TRW_LOCAL_ONLY=true``), so an env-var leak can only be
caught by scanning ``os.environ`` directly, the same way
``warn_retired_env_vars`` already does for retired keys.

- ``reject_local_only_mapping`` — call from a ``model_validator(mode="before")``
  on ``TRWConfig``. Catches BOTH the YAML cascade (project + machine merge,
  passed as constructor kwargs) and a direct ``TRWConfig(local_only=...)`` call,
  since both go through the same pydantic validation entrypoint.
- ``reject_local_only_env`` — call directly from the config-loading entrypoint
  (``_loader.py``), reading raw ``os.environ``, before precedence/filtering can
  hide it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from trw_mcp.exceptions import ConfigError

#: Case-insensitive key spellings this guard rejects.
_LOCAL_ONLY_KEYS = frozenset({"local_only", "memory_local_only"})

#: Case-insensitive env var names this guard rejects.
_LOCAL_ONLY_ENV_VARS = frozenset({"TRW_LOCAL_ONLY", "TRW_MEMORY_LOCAL_ONLY"})

_REMEDY = (
    "'local_only' was removed in trw-memory 4.0.0 / trw-mcp 7.0.0; remove it. "
    "To keep sync off, set sync_enabled: false (and learning_sharing_enabled: false) explicitly."
)


def reject_local_only_mapping(data: Mapping[str, object], *, source: str) -> None:
    """Raise ``ConfigError`` if *data* (a constructor-kwargs or merged-YAML dict) sets local_only."""
    for key in data:
        if isinstance(key, str) and key.lower() in _LOCAL_ONLY_KEYS:
            raise ConfigError(f"{key} in {source}: {_REMEDY}")


def reject_local_only_env(environ: Mapping[str, str] | None = None) -> None:
    """Raise ``ConfigError`` if the environment sets a local_only alias.

    Reads raw ``environ`` (default ``os.environ``) directly rather than going
    through pydantic-settings, which never surfaces an undeclared field's env
    var into the values a model validator sees.
    """
    env = environ if environ is not None else os.environ
    for name in env:
        if name.upper() in _LOCAL_ONLY_ENV_VARS:
            raise ConfigError(f"{name} in environment: {_REMEDY}")
