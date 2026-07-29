"""The published set of ``TRWConfig`` fields that no production code reads.

PRD-QUAL-131-FR05 / OQ-01. ``trw-eval`` needs to reject a variant overlay that
names a field trw-mcp does not read, but it consumes the *published wheel* and
the readership scan runs over monorepo source a wheel does not carry. So the
answer travels as **data**: ``scripts/check_config_field_consumers.py`` writes
``trw_mcp/data/config-unread-fields.json`` from the same measurement that drives
the ratchet, that file ships in the wheel, and this module is the reader.

Why the distinction matters. The previous cross-package guard derived its
allowlist from ``TRWConfig.model_fields`` -- an **existence** test. It is correct
for a field that was *deleted* and structurally blind to one that is merely
*unread*: the field is still in ``model_fields``, so the overlay passes
validation, the container writes it to ``.trw/config.yaml``, nothing reads it,
and the arm runs un-ablated under an ablated label. Existence is not behavior.
This module supplies the readership half.

Fail-closed on purpose: the data file ships in the same wheel as this function,
so its absence is a packaging defect rather than a runtime condition, and
silently returning an empty set would restore exactly the blindness the file
exists to remove.
"""

from __future__ import annotations

import functools
import json
from importlib.resources import files as _pkg_files

#: Bundled artifact name. Written by ``scripts/check_config_field_consumers.py``
#: (``--write-baseline`` regenerates both it and the ratchet baseline together,
#: and the gate fails on drift between them).
UNREAD_FIELDS_RESOURCE = "config-unread-fields.json"


@functools.cache
def unread_config_fields() -> frozenset[str]:
    """Return every admitted ``TRWConfig`` field with no production reader.

    A member of this set is settable in ``.trw/config.yaml`` and as a ``TRW_*``
    environment variable, is printed by ``trw-mcp config-reference``, and does
    nothing. Callers that gate on config -- notably eval harnesses building
    ablation arms -- must treat naming one as an error, not as a knob.

    Raises:
        RuntimeError: the bundled artifact is missing or malformed. Fail-closed;
            see the module docstring.
    """
    resource = _pkg_files("trw_mcp.data") / UNREAD_FIELDS_RESOURCE
    try:
        raw = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:  # pragma: no cover - packaging defect
        msg = (
            f"bundled {UNREAD_FIELDS_RESOURCE} is missing from trw_mcp.data. It ships in the same "
            "wheel as this function, so this is a packaging defect. Regenerate it with "
            "`python3 scripts/check_config_field_consumers.py --write-baseline`."
        )
        raise RuntimeError(msg) from exc

    payload = json.loads(raw)
    fields = payload.get("unread_fields") if isinstance(payload, dict) else None
    if not isinstance(fields, list) or not all(isinstance(name, str) for name in fields):
        msg = (
            f"bundled {UNREAD_FIELDS_RESOURCE} has no 'unread_fields' list of strings. "
            "Regenerate it with `python3 scripts/check_config_field_consumers.py --write-baseline`."
        )
        raise RuntimeError(msg)
    return frozenset(fields)
