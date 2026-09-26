"""Red tests for confirmed learning-loss paths (REFACTOR-CATALOG X-01, REF-002, X-04).

X-01: PRD-CORE-042-FR03 says a merge appends the incoming detail under an audit
marker. The implementation appends it only when it is longer, and never keeps
the incoming summary.
REF-002 and X-04 (consolidation archiving protected rows, or tag-only clusters)
moved with consolidation to the memory daemon (PRD-CORE-302 FR03) and are pinned
in trw-memory (``test_consolidation_clustering.py``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.state.dedup import merge_into_survivor


def _today() -> str:
    return datetime.now(tz=timezone.utc).date().isoformat()


class TestMergeKeepsIncomingContent:
    def test_shorter_incoming_detail_is_appended_under_audit_marker(self, tmp_path: Path) -> None:
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path,
            existing_detail="a long existing detail that is certainly longer than the new one",
            new_detail="short but distinct fact",
        )
        merge_into_survivor(path, new_data, reader, writer)
        detail = str(reader.read_yaml(path)["detail"])
        assert detail.endswith(f"\n---\nMerged from L-new01 on {_today()}:\nshort but distinct fact")

    def test_incoming_summary_survives_the_merge(self, tmp_path: Path) -> None:
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(tmp_path)
        new_data["summary"] = "incoming summary that says something new"
        merge_into_survivor(path, new_data, reader, writer)
        merged = reader.read_yaml(path)
        assert merged["summary"] == "summary"  # the survivor's summary is unchanged
        assert str(merged["detail"]).endswith(
            f"\n---\nMerged from L-new01 on {_today()}: incoming summary that says something new\n"
            "longer new detail with more info"
        )

    def test_a_detail_already_present_verbatim_is_not_appended_again(self, tmp_path: Path) -> None:
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path, existing_detail="alpha. beta gamma.", new_detail="beta gamma."
        )
        new_data["summary"] = "summary"
        merge_into_survivor(path, new_data, reader, writer)
        assert reader.read_yaml(path)["detail"] == "alpha. beta gamma."

    def test_a_multiline_incoming_summary_stays_on_one_header_line(self, tmp_path: Path) -> None:
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(tmp_path, new_detail="body")
        new_data["summary"] = "first line\nsecond line"
        merge_into_survivor(path, new_data, reader, writer)
        detail = str(reader.read_yaml(path)["detail"])
        assert detail.endswith(f"\n---\nMerged from L-new01 on {_today()}: first line second line\nbody")

    def test_an_identical_incoming_summary_is_not_repeated(self, tmp_path: Path) -> None:
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(tmp_path)
        new_data["summary"] = "summary"
        merge_into_survivor(path, new_data, reader, writer)
        detail = str(reader.read_yaml(path)["detail"])
        assert detail.endswith(f"\n---\nMerged from L-new01 on {_today()}:\nlonger new detail with more info")
