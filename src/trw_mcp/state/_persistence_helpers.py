"""Persistence utility helpers — extracted from persistence.py for module-size compliance.

Belongs to the ``persistence.py`` facade. Re-exported there for backward
compatibility with callers that import via the parent module.

Pure utility functions:
- ``_safe_yaml`` / ``_roundtrip_yaml`` / ``_new_yaml`` — YAML factory wrappers
- ``json_serializer`` — datetime/date-aware JSON encoder hook
- ``model_to_dict`` — Pydantic model → plain dict via JSON round-trip
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import cast

from pydantic import BaseModel
from ruamel.yaml import YAML


def _safe_yaml() -> YAML:
    """Safe YAML loader for reading untrusted content.

    Uses ruamel.yaml's safe loader (typ="safe") which rejects !!python/object
    and other constructor tags that would enable RCE. Use for all read paths
    where the YAML source may be user-editable (e.g. config.yaml, run.yaml).

    ruamel.yaml's YAML class maintains internal state that is NOT thread-safe.
    Creating a fresh instance per operation prevents concurrent read corruption
    (PRD-CORE-014 FR03).
    """
    return YAML(typ="safe")


def _roundtrip_yaml() -> YAML:
    """Round-trip YAML for write operations that preserve formatting.

    Uses the default round-trip loader/dumper so that comments and key ordering
    are preserved when serializing framework-generated data. Only use this for
    write paths — never for parsing user-supplied YAML content.

    ruamel.yaml's YAML class maintains internal emitter state that is
    NOT thread-safe.  Creating a fresh instance per operation prevents
    concurrent write corruption (PRD-CORE-014 FR03).
    """
    yml = YAML()
    yml.default_flow_style = False
    yml.preserve_quotes = True
    return yml


def _new_yaml() -> YAML:
    """Deprecated alias kept for any call sites not yet migrated.

    New code should use _safe_yaml() for reads and _roundtrip_yaml() for writes.
    """
    return _roundtrip_yaml()


def json_serializer(obj: object) -> str:
    """JSON serializer for objects not serializable by default json code.

    Args:
        obj: Object to serialize.

    Returns:
        JSON-compatible string representation.

    Raises:
        TypeError: If object type is not supported.
    """
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    msg = f"Object of type {type(obj).__name__} is not JSON serializable"
    raise TypeError(msg)


def model_to_dict(model: BaseModel) -> dict[str, object]:
    """Convert a Pydantic model to a plain dict suitable for YAML serialization.

    Converts enums to their values and dates to ISO strings.

    Args:
        model: Pydantic model instance.

    Returns:
        Plain dictionary with JSON-compatible values.
    """
    return cast("dict[str, object]", json.loads(model.model_dump_json()))
