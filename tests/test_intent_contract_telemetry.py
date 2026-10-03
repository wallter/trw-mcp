"""PRD-SEC-013 FR06: bounded hook-firing telemetry (counters per outcome plus the last outcome)."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.security.intent_contract.telemetry import read_telemetry, record_firing, telemetry_path


def test_telemetry_counts_each_outcome_and_keeps_the_last(tmp_path: Path) -> None:
    for index in range(40):
        record_firing(tmp_path, "blocked" if index % 4 == 0 else "allowed_no_match" if index % 2 else "allowed_match")
    record_firing(tmp_path, "break_glass")

    state = read_telemetry(tmp_path)
    assert state["fires"] == 41
    assert state["break_glass"] == 1
    assert state["outcomes"] == {"blocked": 10, "allowed_match": 10, "allowed_no_match": 20, "break_glass": 1}
    assert state["last_outcome"] == "break_glass"


def test_corrupt_telemetry_file_degrades_to_empty(tmp_path: Path) -> None:
    path = telemetry_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    record_firing(tmp_path, "allowed_match")
    assert read_telemetry(tmp_path)["fires"] == 1


def test_configured_path_is_honoured(tmp_path: Path) -> None:
    record_firing(tmp_path, "allowed_match", ".trw/context/custom-telemetry.json")
    assert (tmp_path / ".trw/context/custom-telemetry.json").exists()


def test_the_telemetry_file_stays_bounded_however_many_hooks_fire(tmp_path: Path) -> None:
    """UF-MCP-03: every Write/Edit hook fire rewrote a file holding one entry per fire ever (360 KB after 15,023
    fires, 2.9 ms a write and growing); the file must not grow with the number of fires."""
    for _ in range(50):
        record_firing(tmp_path, "allowed_no_match")
    size_after_50 = telemetry_path(tmp_path).stat().st_size
    for _ in range(950):
        record_firing(tmp_path, "allowed_no_match")
    assert read_telemetry(tmp_path)["fires"] == 1000
    assert telemetry_path(tmp_path).stat().st_size <= size_after_50 + 8  # only the counter's digits may grow


def test_a_legacy_outcome_log_is_folded_into_counts_and_shrunk_on_the_next_fire(tmp_path: Path) -> None:
    """UF-MCP-03: an existing install's file holds one entry per past fire; the next fire rewrites it as counts,
    so it stops paying for the log on every write, and no count is lost."""
    path = telemetry_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "fires": 15000,
        "false_blocks": 0,
        "break_glass": 2,
        "recent_outcomes": ["allowed_no_match"] * 14990 + ["blocked"] * 8 + ["break_glass"] * 2,
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    big = path.stat().st_size

    record_firing(tmp_path, "allowed_match")

    state = json.loads(path.read_text(encoding="utf-8"))
    assert "recent_outcomes" not in state
    assert path.stat().st_size < big // 100
    assert state["fires"] == 15001
    assert state["break_glass"] == 2
    assert state["outcomes"] == {"allowed_no_match": 14990, "blocked": 8, "break_glass": 2, "allowed_match": 1}
    assert state["last_outcome"] == "allowed_match"


def test_a_corrupt_counter_never_escapes_the_hook_telemetry_boundary(tmp_path: Path) -> None:
    """codex r1 KI on UF-MCP-03: a counter past Python's int-to-str digit limit made serialization raise ValueError
    out of the hook's telemetry call; telemetry must never change an allow/block decision."""
    from trw_mcp.security.intent_contract._hook_common import telemetry

    path = telemetry_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"fires": 1, "outcomes": {"allowed_match": ' + "9" * 4300 + "}}", encoding="utf-8")

    telemetry(tmp_path, "allowed_match", None)  # must not raise

    # The failure left the telemetry path usable: a healthy counter file is advanced by the same call.
    path.write_text('{"fires": 1, "outcomes": {"allowed_match": 1}}', encoding="utf-8")
    telemetry(tmp_path, "allowed_match", None)
    assert read_telemetry(tmp_path)["fires"] == 2
