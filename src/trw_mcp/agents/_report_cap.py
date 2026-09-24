"""The final-report cap rendered into installed agents (PRD-CORE-290-FR04).

Every rendered agent asks for a concise final report (``agent_report_max_chars``,
default 800 characters) that links durable findings instead of restating them.
It is guidance to the model, not something a host enforces. The value resolves
through the ordinary config cascade (``TRW_AGENT_REPORT_MAX_CHARS`` >
``.trw/config.yaml`` > the field default; 0 renders no cap). ``init_project`` and
``update_project`` resolve it once for their target inside
:func:`project_report_cap`, so the installer and the update path's hash
comparison render the same text; any other caller gets the live ``TRWConfig``
value.

Review and security agents are exempt: their findings schema is structured YAML
that a character cap would cut (``TaskPolicy.report_capped``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from trw_mcp.agents.task_policy import AGENT_TASK_CLASS, TASK_POLICY

__all__ = ["project_report_cap", "report_block"]

#: ``None`` outside an install/update call: the live config applies.
_CAP: ContextVar[int | None] = ContextVar("agent_report_max_chars", default=None)
_NAME_RE = re.compile(r"\A---\n(?:(?!---\n).*\n)*?name:[ \t]*['\"]?([\w-]+)", re.MULTILINE)


def _default_cap() -> int:
    from trw_mcp.models.config import TRWConfig

    return int(TRWConfig.model_fields["agent_report_max_chars"].default)


def _project_cap(target_dir: Path) -> int:
    """The cap the config cascade resolves for *target_dir*, or the default when it cannot load."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._loader import resolve_config_overrides

    try:
        overrides = resolve_config_overrides(target_dir / ".trw" / "config.yaml")
        return TRWConfig(**overrides).agent_report_max_chars  # type: ignore[arg-type]
    except Exception:  # trw-fail-silent-allow: an unloadable config renders the documented default
        return _default_cap()


@contextmanager
def project_report_cap(target_dir: Path) -> Iterator[None]:
    """Render agents inside the block with the cap configured for *target_dir*.

    A context manager used in the body, not a decorator: a wrapper around the
    installer entry points hid them from ``inspect.getfile`` and from the
    instruction-write guard's AST check.
    """
    token = _CAP.set(_project_cap(target_dir))
    try:
        yield
    finally:
        _CAP.reset(token)


def _configured_cap() -> int:
    from trw_mcp.models.config import get_config

    return get_config().agent_report_max_chars


def report_block(chars: int | None = None, *, agent_text: str = "") -> str:
    """The final-report instruction for an agent body, or ``""`` when the cap is 0 or its class is exempt."""
    name = _NAME_RE.search(agent_text)
    task_class = AGENT_TASK_CLASS.get(name.group(1)) if name else None
    if task_class is not None and not TASK_POLICY[task_class].report_capped:
        return ""
    cap = chars if chars is not None else _CAP.get()
    if cap is None:
        cap = _configured_cap()
    if cap <= 0:
        return ""
    return (
        "\n## Final report\n\n"
        f"Keep your final report to at most {cap} characters: lead with the outcome, then link "
        "durable findings (file paths, run paths, learning ids) instead of restating them. If the "
        "findings do not fit, say so and point to where they are recorded; never drop one to fit.\n"
    )
