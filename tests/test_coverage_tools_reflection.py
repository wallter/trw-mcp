from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.reflection import ReflectionInputs, create_reflection_record, persist_reflection


class TestReflectionPersistWithRunPath:
    """Lines 251-260: persist_reflection with run_path that has a valid meta/ dir."""

    def test_persist_reflection_logs_event_to_run(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        (trw_dir / "reflections").mkdir(parents=True)
        run_path = tmp_path / "run"
        meta = run_path / "meta"
        meta.mkdir(parents=True)
        events_file = meta / "events.jsonl"
        events_file.write_text("")

        inputs = ReflectionInputs(
            events=[],
            run_id="test-run-01",
            error_events=[],
            phase_transitions=[],
            repeated_ops=[],
            success_patterns=[],
            tool_sequences=[],
            validated_learnings=[],
        )
        reflection = create_reflection_record(inputs, [], "session")

        with patch("trw_mcp.state.reflection.get_config") as mock_get_cfg:
            mock_get_cfg.return_value.reflections_dir = "reflections"
            persist_reflection(
                trw_dir=trw_dir,
                reflection=reflection,
                run_path=str(run_path),
                scope="session",
                learnings_count=0,
            )

        assert len(list((trw_dir / "reflections").glob("*.yaml"))) == 1

        import json

        logged = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
        reflection_events = [e for e in logged if e.get("event") == "reflection_complete"]
        assert len(reflection_events) == 1
        assert reflection_events[0]["scope"] == "session"

    def test_persist_reflection_no_run_path_skips_event(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        (trw_dir / "reflections").mkdir(parents=True)

        inputs = ReflectionInputs(
            events=[],
            run_id=None,
            error_events=[],
            phase_transitions=[],
            repeated_ops=[],
            success_patterns=[],
            tool_sequences=[],
            validated_learnings=[],
        )
        reflection = create_reflection_record(inputs, [], "session")

        with patch("trw_mcp.state.reflection.get_config") as mock_get_cfg:
            mock_get_cfg.return_value.reflections_dir = "reflections"
            persist_reflection(
                trw_dir=trw_dir,
                reflection=reflection,
                run_path=None,
                scope="session",
                learnings_count=2,
            )

        assert len(list((trw_dir / "reflections").glob("*.yaml"))) == 1

    def test_persist_reflection_run_path_missing_meta_skips_event(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        (trw_dir / "reflections").mkdir(parents=True)
        run_path = tmp_path / "run_no_meta"
        run_path.mkdir()

        inputs = ReflectionInputs(
            events=[],
            run_id="test-run-02",
            error_events=[],
            phase_transitions=[],
            repeated_ops=[],
            success_patterns=[],
            tool_sequences=[],
            validated_learnings=[],
        )
        reflection = create_reflection_record(inputs, [], "run")

        with patch("trw_mcp.state.reflection.get_config") as mock_get_cfg:
            mock_get_cfg.return_value.reflections_dir = "reflections"
            persist_reflection(
                trw_dir=trw_dir,
                reflection=reflection,
                run_path=str(run_path),
                scope="run",
                learnings_count=1,
            )

        assert len(list((trw_dir / "reflections").glob("*.yaml"))) == 1
        assert not (run_path / "meta" / "events.jsonl").exists()
