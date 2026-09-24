"""What the learning-engagement report reads from a tool call besides its timing (PRD-CORE-294 FR05).

Belongs to the ``tool_call_timing`` wrapper: :class:`_CallTelemetry` calls
:func:`call_measures` once per successful call and the emitter puts the result
on the ``tool_call`` event. Every other tool keeps the zero token placeholders,
and a row without ``token_method`` is never read as a measured zero.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field

#: The tools whose response cost the report measures per call.
TOKEN_COUNTED_TOOLS = frozenset({"trw_recall", "trw_session_start"})
#: How the counts are estimated: UTF-8 bytes of the JSON over four, the budget rule tool responses use.
ESTIMATE_METHOD = "utf8_bytes_div_4"


@dataclass(frozen=True)
class CallMeasures:
    input_tokens: int = 0
    output_tokens: int = 0
    payload: dict[str, object] = field(default_factory=dict)


def call_measures(tool: str, kwargs: Mapping[str, object], response_bytes: int | None) -> CallMeasures:
    """Token counts for a counted tool, and the transition-selector state on ``trw_session_start``.

    ``response_bytes`` is the wrapper's own measurement of the response; ``None``
    (unmeasurable) leaves the call uncounted rather than counted as zero.
    """
    payload: dict[str, object] = {}
    if tool == "trw_session_start":
        from trw_mcp.tools._ceremony_status_context import transition_selector_state

        payload["nudge_selector"] = transition_selector_state()
    if tool not in TOKEN_COUNTED_TOOLS or response_bytes is None:
        return CallMeasures(payload=payload)
    arguments = {key: value for key, value in kwargs.items() if key != "ctx"}
    input_bytes = len(json.dumps(arguments, default=str).encode("utf-8"))
    payload["token_method"] = ESTIMATE_METHOD
    return CallMeasures(math.ceil(input_bytes / 4), math.ceil(response_bytes / 4), payload)
