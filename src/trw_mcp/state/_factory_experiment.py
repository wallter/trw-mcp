"""The one experimental boundary of the software factory (PRD-CORE-340-FR11, FR12).

Every factory entry point (``trw-mcp factory status`` and the legacy script
adapter, ``receipt verify`` plus ``record_verification_receipt`` itself, the
factory checkpoint branch, the formation orchestrator-member addition) asks this
module and nothing else whether it may run, so the switch, the time-box and the
banner cannot drift apart.

States, in the order they are decided:

``config_error``  the switch could not be read strictly (unreadable or invalid
                  config, an experiment record that fails its own validation, a
                  naive clock). Refuses; NEVER falls back to enabled.
``disabled``      ``factory_enabled`` is false (the default). Wins over overdue.
``overdue``       enabled, but UTC time is at or after the day following
                  ``expiry`` (2026-12-28T00:00:00Z for the shipped record).
``enabled``       enabled and within the time-box.

Invariants: the experiment record is code, not config, so a caller cannot
override the expiry; extending or promoting the experiment is a reviewed edit to
:data:`EXPERIMENT`. ``check`` writes nothing. Real UTC is read only when ``now``
is omitted, through :func:`_utc_now` (tests inject ``now`` instead).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "BANNER",
    "EXPERIMENT",
    "LIMITATIONS",
    "OVERDUE_MARKER",
    "ExperimentRecord",
    "FactoryGate",
    "check",
    "exit_code_for",
    "is_factory_message",
]

BANNER: Final = (
    "EXPERIMENTAL (Alpha): trw-software-factory may change or be removed without notice; "
    "stable surfaces must not depend on it. Expires 2026-12-27."
)

LIMITATIONS: Final[tuple[str, ...]] = (
    "single host and clock",
    "one team, small N, self-selected work",
    "reporter-asserted execution",
    "content scope is not Git identity",
    "no acceptance authority",
    "no throughput claim",
    "VOID is unauthenticated (any journal writer); voided attempts leave ready_to_used",
    "verification outcomes are not signed; a same-user edit to a receipt can change them",
)

OVERDUE_MARKER: Final = "EXPERIMENT OVERDUE"

#: Refusal reason -> process exit code. One vocabulary for every entry point.
_EXIT: Final = {"factory_disabled": 2, "factory_config_error": 2, "experiment_overdue": 3}


@dataclass(frozen=True)
class ExperimentRecord:
    """Owner, tier, time-box and exit criterion of the factory experiment."""

    owner: str
    tier: str
    start: date
    expiry: date
    exit_criterion: str

    def problem(self) -> str | None:
        """Why this record is malformed, or ``None`` when it is well-formed."""
        if not (isinstance(self.owner, str) and self.owner.strip()):
            return "experiment record has no owner"
        if self.tier != "Alpha":
            return f"experiment tier {self.tier!r} is not Alpha"
        if not (isinstance(self.start, date) and isinstance(self.expiry, date)) or self.expiry < self.start:
            return "experiment start/expiry are not a valid date range"
        if not (isinstance(self.exit_criterion, str) and self.exit_criterion.strip()):
            return "experiment record has no exit criterion"
        return None

    @property
    def overdue_from(self) -> datetime:
        """The first UTC instant at which the experiment is overdue."""
        return datetime.combine(self.expiry + timedelta(days=1), time.min, tzinfo=timezone.utc)


EXPERIMENT: Final = ExperimentRecord(
    owner="operator",
    tier="Alpha",
    start=date(2026, 9, 28),
    expiry=date(2026, 12, 27),
    exit_criterion=(
        "One real self-build attempt with receiver-evidenced USED, zero unresolved references, "
        "passing native checks and operator review of utility and limits. It permits a promote "
        "decision, never automatic graduation; otherwise extend once (<=90 days, with rationale) "
        "or remove."
    ),
)


@dataclass(frozen=True)
class FactoryGate:
    """The outcome of :func:`check`."""

    state: str
    detail: str = ""

    @property
    def enabled(self) -> bool:
        return self.state == "enabled"

    @property
    def reason(self) -> str | None:
        """Stable machine key of the refusal, ``None`` when enabled."""
        return None if self.enabled else _REASON[self.state]

    @property
    def exit_code(self) -> int:
        return 0 if self.enabled else _EXIT[_REASON[self.state]]

    @property
    def message(self) -> str:
        """Human text for a refusal (contains :data:`OVERDUE_MARKER` when overdue)."""
        if self.enabled:
            return BANNER
        head = OVERDUE_MARKER if self.state == "overdue" else _REASON[self.state]
        return f"{head}: {self.detail}" if self.detail else head


_REASON: Final = {
    "disabled": "factory_disabled",
    "config_error": "factory_config_error",
    "overdue": "experiment_overdue",
}


def exit_code_for(reason: str) -> int:
    """Exit code for a refusal reason key; 1 for any other refusal."""
    return _EXIT.get(reason, 1)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_SWITCH_KEY = re.compile(r"^[\"']?factory_enabled[\"']?\s*:", re.IGNORECASE)
_SWITCH_LINE = re.compile(r"^factory_enabled:[ \t]*(true|false)[ \t]*(?:#.*)?$", re.IGNORECASE)
_ENV_TRUE = frozenset({"1", "true", "yes", "on"})
_ENV_FALSE = frozenset({"0", "false", "no", "off"})


class _SwitchUnreadable(ValueError):
    """The switch itself is ambiguous or malformed; the message is fixed text, never file content."""


def _switch_in_file(path: Path) -> bool | None:
    """``factory_enabled`` from one config file's top-level lines alone; ``None`` when the file does not name it."""
    try:
        text = path.read_text(encoding="utf-8")
    except (
        FileNotFoundError
    ):  # trw-fail-silent-allow: an absent config file is the ordinary state; None means "not named here"
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise _SwitchUnreadable(f"{path.name} is unreadable ({type(exc).__name__})") from None
    values: set[bool] = set()
    for line in text.splitlines():
        if not _SWITCH_KEY.match(line):
            continue
        plain = _SWITCH_LINE.match(line)
        if plain is None:
            raise _SwitchUnreadable("factory_enabled is not a plain true/false")
        values.add(plain.group(1).lower() == "true")
    if len(values) > 1:
        raise _SwitchUnreadable("factory_enabled is set to both true and false")
    return values.pop() if values else None


def _read_switch_alone() -> bool:
    """``factory_enabled`` and nothing else, in the cascade's own order: env, then project, then machine, else off.

    The general loader validates EVERY key, so an unrelated bad key (a duplicate, a wrong type) used to
    disable the factory. This reads only the one switch and refuses, with fixed text, when the switch itself
    is ambiguous or malformed.
    """
    from trw_mcp.state._paths import resolve_project_root

    env = os.environ.get("TRW_FACTORY_ENABLED")
    if env is not None:
        text = env.strip().lower()
        if text in _ENV_TRUE:
            return True
        if text in _ENV_FALSE:
            return False
        raise _SwitchUnreadable("TRW_FACTORY_ENABLED is not a boolean")
    value = False
    for path in (Path.home() / ".trw" / "config.yaml", resolve_project_root() / ".trw" / "config.yaml"):
        found = _switch_in_file(path)
        if found is not None:
            value = found  # the project file, read last, wins
    return value


def _read_switch() -> bool:
    """Read ``factory_enabled`` through the one config cascade, strictly.

    Unlike ``get_config`` (which reverts a malformed config to defaults), a switch that cannot be read raises:
    the caller reports ``config_error`` and refuses. If the cascade fails only because of some OTHER key, the
    switch is read on its own (:func:`_read_switch_alone`) so an unrelated key cannot disable the factory; that
    is logged, never silent. The raise names both the cascade failure and why the isolated read also failed.
    """
    from trw_mcp.models.config._loader import exclude_env_shadowed_keys, resolve_config_overrides
    from trw_mcp.models.config._main import TRWConfig
    from trw_mcp.state._paths import resolve_project_root

    try:
        merged = resolve_config_overrides(resolve_project_root() / ".trw" / "config.yaml", apply_env_exclusion=False)
        value = TRWConfig(**exclude_env_shadowed_keys(merged)).factory_enabled  # type: ignore[arg-type]
        if not isinstance(value, bool):
            raise TypeError("factory_enabled is not a bool")
    except Exception as strict:
        cause = strict.__cause__ or strict.__context__
        named = f"{type(strict).__name__}: {type(cause).__name__}" if cause else type(strict).__name__
        try:
            isolated = _read_switch_alone()
        except Exception as alone:
            reason = str(alone) if isinstance(alone, _SwitchUnreadable) else type(alone).__name__
            raise _SwitchUnreadable(f"{named}; the switch alone: {reason}") from None
        logger.warning("factory_switch_read_alone", cascade_failure=named, enabled=isolated)
        return isolated
    return value


def check(now: datetime | None = None, *, record: ExperimentRecord = EXPERIMENT) -> FactoryGate:
    """Decide whether factory work may run at *now* (real UTC when omitted)."""
    try:
        enabled = _read_switch()
    except Exception as exc:  # trw-fail-silent-allow: reported as config_error, refuses
        detail = str(exc) if isinstance(exc, _SwitchUnreadable) else type(exc).__name__
        return FactoryGate("config_error", f"factory_enabled could not be read strictly ({detail})")
    if not enabled:
        return FactoryGate("disabled", "set factory_enabled: true (or TRW_FACTORY_ENABLED=1) to opt in")
    problem = record.problem()
    if problem:
        return FactoryGate("config_error", problem)
    when = now if now is not None else _utc_now()
    if when.tzinfo is None:
        return FactoryGate("config_error", "the clock is not timezone-aware")
    if when >= record.overdue_from:
        return FactoryGate("overdue", f"the experiment expired {record.expiry.isoformat()} (UTC); owner {record.owner}")
    return FactoryGate("enabled")


def is_factory_message(message: str) -> bool:
    """True for a checkpoint message that is a JSON object with ``factory == 1``."""
    if not message.lstrip().startswith("{"):
        return False
    try:
        payload = json.loads(message)
    except ValueError:  # trw-fail-silent-allow: prose that starts with a brace is an ordinary checkpoint
        return False
    return isinstance(payload, dict) and payload.get("factory") == 1
