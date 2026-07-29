"""PRD-SEC-013 FR06: counters + blocks-only rolling false-block rate."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.security.intent_contract.telemetry import (
    dispose_last_block,
    false_block_rate,
    read_telemetry,
    record_firing,
    telemetry_path,
)


def test_telemetry_counts_and_rolling_false_block_rate(tmp_path: Path) -> None:
    """FR06 evidence artifact: 40 firings, 10 block-class, interleaved allows."""
    window = 10
    for index in range(40):
        if index % 4 == 0:  # 10 block-class firings, spaced among 30 allows
            record_firing(tmp_path, "blocked")
            if index in {0, 4}:  # exactly 2 dispositioned as false positives
                dispose_last_block(tmp_path, is_false_positive=True)
        else:
            record_firing(tmp_path, "allowed_no_match" if index % 2 else "allowed_match")

    state = read_telemetry(tmp_path)
    assert state["fires"] == 40
    assert state["false_blocks"] == 2
    assert state["break_glass"] == 0
    outcomes = state["recent_outcomes"]
    assert isinstance(outcomes, list)
    assert len(outcomes) == 40  # append-only: nothing evicted
    assert outcomes.count("false_block") == 2
    assert outcomes.count("blocked") == 8

    # Hand-computed: 2 false blocks out of the last 10 BLOCK-CLASS outcomes.
    # Over all 40 outcomes the naive rate would be 2/40 = 0.05 — the dilution
    # this denominator exists to prevent.
    assert false_block_rate(tmp_path, window=window) == 0.2


def test_rate_is_none_below_the_window_regardless_of_allow_volume(tmp_path: Path) -> None:
    for _ in range(100):
        record_firing(tmp_path, "allowed_no_match")
    for _ in range(9):
        record_firing(tmp_path, "blocked")
    assert false_block_rate(tmp_path, window=10) is None
    record_firing(tmp_path, "blocked")
    assert false_block_rate(tmp_path, window=10) == 0.0


def test_break_glass_outcomes_are_counted_but_are_not_block_class(tmp_path: Path) -> None:
    for _ in range(3):
        record_firing(tmp_path, "break_glass")
    assert read_telemetry(tmp_path)["break_glass"] == 3
    assert false_block_rate(tmp_path, window=1) is None


def test_disposition_is_explicit_and_never_inferred(tmp_path: Path) -> None:
    record_firing(tmp_path, "blocked")
    assert read_telemetry(tmp_path)["false_blocks"] == 0
    assert dispose_last_block(tmp_path, is_false_positive=False) is False
    assert read_telemetry(tmp_path)["false_blocks"] == 0
    assert dispose_last_block(tmp_path, is_false_positive=True) is True
    assert read_telemetry(tmp_path)["false_blocks"] == 1
    # Nothing pending left to disposition.
    assert dispose_last_block(tmp_path, is_false_positive=True) is False


def test_corrupt_telemetry_file_degrades_to_empty(tmp_path: Path) -> None:
    path = telemetry_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    record_firing(tmp_path, "allowed_match")
    assert read_telemetry(tmp_path)["fires"] == 1


def test_configured_path_is_honoured(tmp_path: Path) -> None:
    record_firing(tmp_path, "allowed_match", ".trw/context/custom-telemetry.json")
    assert (tmp_path / ".trw/context/custom-telemetry.json").exists()
