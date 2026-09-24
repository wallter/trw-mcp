"""What the learning-engagement report reads from a tool call (PRD-CORE-294 FR05).

The report needs token counts for trw_recall and trw_session_start, and the
transition-selector state of every session, on the tool_call rows it reads.
The wrapper recorded ``input_tokens``/``output_tokens`` as zero placeholders for
every tool; these tests drive the real wrapper and read the real projection.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest


def _call(tmp_path: Path, tool: str, **kwargs: object) -> dict[str, object]:
    """Run a tool named ``tool`` through the real wrapper; return its tool_call payload."""
    from trw_mcp.telemetry.tool_call_timing import wrap_tool

    def fn(**_kwargs: object) -> dict[str, object]:
        return {"learnings": [{"id": "L-1", "claim": "pool exhausts under load " * 10}]}

    fn.__name__ = tool
    events = tmp_path / "events"
    wrapped = wrap_tool(
        fn, session_id_resolver=lambda: "s1", run_dir_resolver=lambda: None, fallback_dir_resolver=lambda: events
    )
    wrapped(**kwargs)
    rows = [json.loads(line) for line in (events / "tool_call_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["payload"]["tool"] for row in rows] == [tool]
    payload = rows[0]["payload"]
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize("tool", ["trw_recall", "trw_session_start"])
def test_recall_and_session_start_calls_record_their_token_counts(tmp_path: Path, tool: str) -> None:
    payload = _call(tmp_path, tool, query="database pool exhaustion")

    assert payload["token_method"] == "utf8_bytes_div_4"
    assert payload["output_tokens"] == math.ceil(int(payload["response_bytes"]) / 4) > 0
    assert payload["input_tokens"] == math.ceil(len(b'{"query": "database pool exhaustion"}') / 4)


def test_other_tools_keep_their_zero_placeholders_and_no_token_method(tmp_path: Path) -> None:
    payload = _call(tmp_path, "trw_status")

    assert (payload["input_tokens"], payload["output_tokens"]) == (0, 0)
    assert "token_method" not in payload


@pytest.mark.parametrize(("nudges", "jev"), [(True, False), (True, True), (False, False)])
def test_session_start_records_which_transition_selector_ran_and_whether_it_was_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nudges: bool, jev: bool
) -> None:
    from trw_mcp.tools import _assess_enablement

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "config.yaml").write_text(f"transition_nudges_enabled: {str(nudges).lower()}\n", encoding="utf-8")
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(_assess_enablement, "backend_enablement", lambda _root, env=None: (jev, "test"))

    payload = _call(tmp_path, "trw_session_start")

    assert payload["nudge_selector"] == {"name": "transition", "enabled": nudges, "jev": jev and nudges}
    assert "nudge_selector" not in _call(tmp_path / "other", "trw_recall")


@pytest.mark.parametrize(
    ("config", "delivered"),
    [
        ("", True),
        ("transition_nudges_enabled: false\n", False),
        # The codex and opencode profiles default nudge_enabled to false; the selector must not follow it.
        ("nudge_enabled: false\n", True),
    ],
)
def test_transition_nudges_enabled_is_the_one_gate_over_transition_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: str, delivered: bool
) -> None:
    from trw_mcp.state._ceremony_nudge_selectors import select_transition_line
    from trw_mcp.tools._ceremony_status_context import maybe_attach_edit_hint_transition_nudge
    from trw_mcp.tools._learnings_collector import LearningSummary

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "config.yaml").write_text(config, encoding="utf-8")
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    response: dict[str, object] = {}

    maybe_attach_edit_hint_transition_nudge(
        response, trw_dir, learnings=[LearningSummary(id="L-1", summary="Pool exhausts under load")]
    )
    line = select_transition_line(trw_dir, session_key="other", candidates=[("jev:build_failed", "Ask trw_assess.")])

    assert ("transition_nudge" in response) is delivered
    assert (line is not None) is delivered
