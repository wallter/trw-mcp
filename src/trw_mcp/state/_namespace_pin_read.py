"""A checkout's ``project_namespace`` pin, read without importing ``TRWConfig`` (PRD-CORE-333 S3c).

``selected_store`` used to build a whole ``TRWConfig`` for this one field. That import
cost ~110 ms of every UserPromptSubmit recall. :func:`pinned_namespace` resolves the pin
to the value ``TRWConfig(**resolve_config_overrides(trw_dir / "config.yaml"))`` gives it.
In the plain case it reads the sources itself, in the same order:

1. ``TRW_PROJECT_NAMESPACE`` in the environment. ``resolve_config_overrides`` drops the
   file key when that variable is set, so the variable wins, verbatim, even when empty
   -- once both layers have been read, since an unreadable one fails the cascade first.
2. The project's ``.trw/config.yaml``, then the machine's ``~/.trw/config.yaml``: the
   project layer overrides per key, and a ``null`` value counts as absent.
3. ``""``: unpinned.

The plain case is the one proven equal. A layer that holds a settings-source control
key (any ``_``-prefixed key: pydantic-settings reads ``_env_prefix``, ``_case_sensitive``,
``_env_file`` and the rest as init arguments that change where every field comes from)
or spells the pin's key in another case is resolved by the full ``TRWConfig`` instead.

What is refused, never guessed: a layer that does not parse, is not a mapping or cannot
be opened raises the reader's ``StateError``, as the cascade does. :class:`PinUnreadableError`
covers a pin that is not a string, the variable spelled in another case, and a full
resolution that fails.

What is NOT claimed: the plain read validates the pin and nothing else. A layer whose
OTHER keys ``TRWConfig`` would reject (``hooks_enabled: not-a-bool``, a leftover
``local_only``) still yields the pin, so an unrelated bad key no longer blocks the
store read. Validating every key would need the ``TRWConfig`` import back on every
prompt. ``tests/test_namespace_pin_read.py`` holds the read to ``TRWConfig`` over a
matrix of these sources and pins this limit.
"""

from __future__ import annotations

import os
from pathlib import Path

_KEY = "project_namespace"
_VARIABLE = "TRW_PROJECT_NAMESPACE"


class PinUnreadableError(ValueError):
    """The pin cannot be read unambiguously; no namespace is guessed."""


def read_config_layer(config_path: object) -> dict[str, object]:
    """One ``config.yaml`` layer as a string-keyed dict: ``{}`` when absent, ``null`` values dropped.

    Raises ``StateError`` (from ``FileStateReader.read_yaml``) when the file does not
    parse or is not a mapping. ``models.config._loader`` reads every layer through this.
    """
    from trw_mcp.state.persistence import FileStateReader

    path = config_path if isinstance(config_path, Path) else Path(str(config_path))
    if not path.exists():
        return {}
    overrides = FileStateReader().read_yaml(path, tolerate_identical_duplicates=True)
    if not isinstance(overrides, dict):
        return {}
    return {str(k): v for k, v in overrides.items() if v is not None}


def pinned_namespace(trw_dir: Path) -> str:
    """*trw_dir*'s ``project_namespace`` as ``TRWConfig`` resolves it; ``""`` when unpinned.

    Raises ``StateError`` for an unreadable layer and :class:`PinUnreadableError` for an
    ambiguous or ill-typed pin.
    """
    # Both layers are read first, as the cascade reads them: an unreadable one refuses
    # even when the variable would win, because TRWConfig refuses there too.
    layers = [read_config_layer(Path.home() / ".trw" / "config.yaml"), read_config_layer(trw_dir / "config.yaml")]
    if any(key.startswith("_") or (key.lower() == _KEY and key != _KEY) for layer in layers for key in layer):
        return _full_resolution(trw_dir)
    spellings = [name for name in os.environ if name.upper() == _VARIABLE]
    if spellings and spellings != [_VARIABLE]:
        raise PinUnreadableError(f"{_VARIABLE} is set as {sorted(spellings)}; set it once, in upper case")
    if spellings:
        return os.environ[_VARIABLE]
    value = next((layer[_KEY] for layer in reversed(layers) if _KEY in layer), None)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PinUnreadableError(f"{_KEY} is a {type(value).__name__}, not a string")
    return value


def _full_resolution(trw_dir: Path) -> str:
    """The pin from a whole ``TRWConfig``: for layers that change how its sources resolve.

    A store read never parses the process's command line: a layer carrying a ``_cli_*``
    control is refused, and ``_cli_parse_args=False`` is passed regardless. Anything the
    construction raises -- ``SystemExit`` from a CLI source included, ``KeyboardInterrupt``
    excepted -- is a refusal, never the end of the calling process (review r3).
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._loader import resolve_config_overrides

    try:
        overrides = resolve_config_overrides(trw_dir / "config.yaml")
        if any(key.startswith("_cli_") for key in overrides):
            raise PinUnreadableError("the config asks pydantic-settings to parse the command line")
        return TRWConfig(**overrides, _cli_parse_args=False).project_namespace  # type: ignore[arg-type]
    except (KeyboardInterrupt, PinUnreadableError):
        raise
    except BaseException as exc:  # justified: boundary, a config TRWConfig refuses refuses the pin, re-raised typed
        raise PinUnreadableError(
            f"the config does not resolve ({type(exc).__name__}); no namespace is guessed"
        ) from exc
