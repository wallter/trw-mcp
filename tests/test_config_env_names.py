"""Every TRWConfig setting is read from a ``TRW_``-prefixed env var, and not only from a doubled one (ENV-DOUBLE-PREFIX).

pydantic-settings reads an aliased field from each alias name verbatim (no ``env_prefix``), and an unaliased
field from ``env_prefix + name``, so a field named ``trw_x`` answers only to ``TRW_TRW_X``. trw-memory's
MemoryConfig carries the same census (trw-memory/tests/test_config_env_names.py).
"""

from __future__ import annotations

from pydantic import AliasChoices, AliasPath, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from trw_mcp.models.config import TRWConfig

#: field -> why it may break the census. Each entry names the backlog row that retires it.
_ALLOWED = {
    "trw_dir": (
        "TRW-DIR-ENV (8.1): reads only TRW_TRW_DIR. Honoring TRW_DIR instead would move .trw but not runs_root "
        "(default '.trw/runs'), and TRW_DIR already names trw-memory's SEC-001 anchor, so it needs a design "
        "that makes the three agree"
    ),
}


def _env_name(choice: object) -> str:
    """The env var one alias choice reads (a str verbatim, an AliasPath its first element); never skipped."""
    if isinstance(choice, str):
        return choice.upper()
    if isinstance(choice, AliasPath) and choice.path and isinstance(choice.path[0], str):
        return choice.path[0].upper()
    return f"<uncheckable alias {choice!r}>"


def census(settings: type[BaseSettings]) -> list[str]:
    """Every env name without the prefix, and every field readable ONLY under a doubled prefix."""
    prefix = str(settings.model_config.get("env_prefix", "")).upper()
    by_name = bool(settings.model_config.get("populate_by_name"))
    problems: list[str] = []
    for field, info in sorted(settings.model_fields.items()):
        alias = info.validation_alias
        if alias is None:
            names = [prefix + field.upper()]
        else:
            choices = alias.choices if isinstance(alias, AliasChoices) else [alias]
            names = [_env_name(choice) for choice in choices]
            names += [prefix + field.upper()] if by_name else []
        problems += [f"{field}: unprefixed {name}" for name in names if not name.startswith(prefix)]
        if names and all(name.startswith(prefix * 2) for name in names):
            problems.append(f"{field}: only {names}")
    return problems


def test_every_trw_setting_is_read_from_its_trw_prefixed_name() -> None:
    problems = [problem for problem in census(TRWConfig) if problem.split(":", 1)[0] not in _ALLOWED]

    assert problems == []


def test_every_allowlisted_field_still_breaks_the_census() -> None:
    """An entry whose field was fixed must be deleted, so the allowlist cannot outlive its reason."""
    flagged = {problem.split(":", 1)[0] for problem in census(TRWConfig)}

    assert set(_ALLOWED) <= flagged


class _Planted(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRW_")

    bare: int = Field(default=0, validation_alias=AliasChoices("bare"))
    bare_path: int = Field(default=0, validation_alias=AliasChoices(AliasPath("BARE", 0), "trw_bare_path"))
    trw_doubled: int = 0
    fine: int = 0


def test_the_census_reports_a_bare_alias_and_a_doubled_only_name() -> None:
    assert census(_Planted) == [
        "bare: unprefixed BARE",
        "bare_path: unprefixed BARE",
        "trw_doubled: only ['TRW_TRW_DOUBLED']",
    ]
