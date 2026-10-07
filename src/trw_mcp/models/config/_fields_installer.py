"""The installer's remembered answers (``installer_answers``).

``install-trw.py`` asks a few questions once (AI extras, the proprietary install and upgrade, moving a checkout's
learnings, connecting to the platform, writing into a system Python) and records each answer here, yes or no, so a
re-run reuses it instead of asking again. A project choice lives in the project's ``.trw/config.yaml``; a machine
choice (the system-Python decision, keyed by interpreter) lives in ``~/.trw/config.yaml``. The installer writes it
through ``trw-mcp config set installer_answers.<key> <true|false>`` and reads it back itself; no TRW runtime path
reads it. ``--reconfigure`` asks everything once more.
"""

from __future__ import annotations

from pydantic import Field


class _InstallerFields:
    """Installer domain mixin — mixed into _TRWConfigFields via MI."""

    installer_answers: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Answers the installer remembers so it never asks twice: key -> true/false (for example "
            "ai_extras, proprietary_upgrade, migrate_learnings, platform_connect, system_python_<id>). "
            "Written by install-trw.py through `trw-mcp config set installer_answers.<key>`; "
            "`install-trw.py --reconfigure` asks every question again and records the new answers."
        ),
    )


__all__ = ["_InstallerFields"]
