"""Tests for sprint orchestration tools — trw_sprint_start, trw_sprint_finish.

Covers: sprint doc parsing, track extraction, file overlap matrix,
PRD ref extraction, DoD parsing, kickoff prompt generation,
track commit verification, and build check integration.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from trw_mcp.exceptions import ValidationError
from trw_mcp.models.sprint import FileOverlapEntry, SprintDoc, SprintTrack
from trw_mcp.state.sprint_parser import (
    extract_dod_items,
    extract_file_overlap_matrix,
    extract_prd_refs,
    extract_tracks,
    get_track_by_name,
    parse_sprint_doc,
)
from trw_mcp.tools.sprint import register_sprint_tools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_SPRINT_DOC = """\
# Sprint 11: Consolidation

**Goal**: Close all remaining gaps from Sprints 4-7.

**Duration**: 1-2 sessions (3 parallel tracks)

---

## Track A: FRAMEWORK.md v20.0 Rewrite

**~8-12h | Content + structural rewrite**

### Items Consolidated

| Source Sprint | PRD | Open Item |
|--------------|-----|-----------|
| Sprint 6 | PRD-CORE-013 | Phase reversion guidance section |
| Sprint 7 | PRD-QUAL-004 | XML tag migration |
| Sprint 7 | PRD-QUAL-007 | Architecture section |

### Scope

1. XML tag migration
2. Add missing sections

### Files Modified
- `.trw/frameworks/FRAMEWORK.md` — full rewrite
- `.trw/frameworks/trw-core.md` — sync with new structure
- `.trw/frameworks/overlays/*.md` — sync phase overlays

### Validation
- [ ] 0 references to V1 validation
- [ ] XML tags parse correctly
- [x] MUST/CRITICAL/NEVER count < 15

---

## Track B: learning.py Slim-Down

**~3-4h | Python code**

### Items

Related to PRD-FIX-010 and PRD-QUAL-007.

### Files Modified
- `trw-mcp/src/trw_mcp/tools/learning.py`
- `trw-mcp/src/trw_mcp/state/claude_md.py`

### Validation
- [ ] learning.py < 500 lines
- [ ] All tests pass

---

## Track C: Drift Detection + Test Classification

**~3-4h | Python code**

Related to PRD-CORE-017 and PRD-QUAL-006.

### Files Modified
- `trw-mcp/src/trw_mcp/state/framework.py`
- `trw-mcp/src/trw_mcp/tools/testing.py`
- `trw-mcp/src/trw_mcp/models/testing.py`

### Validation
- [ ] state/framework.py implements vocabulary extraction
- [ ] trw_test_target classifies tests

---

## File Overlap Matrix

| File | Track A | Track B | Track C | Conflict? |
|------|---------|---------|---------|-----------|
| `.trw/frameworks/FRAMEWORK.md` | WRITE | -- | -- | NONE |
| `tools/learning.py` | -- | WRITE | -- | NONE |
| `state/framework.py` | -- | -- | WRITE | NONE |
| `server.py` | WRITE | WRITE | -- | YES |

**Merge order**: Any order (no dependencies between tracks).

---

## Definition of Done

### Track A
- [ ] FRAMEWORK.md v20.0 uses XML tags
- [x] Architecture section added

### Track B
- [ ] learning.py < 500 lines

### Track C
- [ ] state/framework.py implements drift detection
- [ ] All tests pass
"""


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to a temp directory with required structure."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".trw" / "tracks").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trw" / "context").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs").mkdir(exist_ok=True)

    # Patch module-level configs
    from trw_mcp.models.config import TRWConfig
    import trw_mcp.tools.sprint as sprint_mod
    import trw_mcp.tools.tracks as tracks_mod

    monkeypatch.setattr(sprint_mod, "_config", TRWConfig())
    monkeypatch.setattr(tracks_mod, "_config", TRWConfig())
    return tmp_path


@pytest.fixture
def sprint_doc_path(tmp_path: Path) -> Path:
    """Write sample sprint doc to temp dir and return path."""
    doc_path = tmp_path / "sprint-11.md"
    doc_path.write_text(_SAMPLE_SPRINT_DOC, encoding="utf-8")
    return doc_path


def _get_tools() -> dict[str, object]:
    """Create a fresh server and register sprint tools, return tool map."""
    srv = FastMCP("test-sprint")
    register_sprint_tools(srv)
    tools = {t.name: t for t in srv._tool_manager._tools.values()}
    return tools


# ---------------------------------------------------------------------------
# TestSprintDocParser
# ---------------------------------------------------------------------------


class TestSprintDocParser:
    """Tests for sprint document parsing logic."""

    def test_parse_sprint_number(self) -> None:
        """Sprint number extracted from heading."""
        doc = parse_sprint_doc(_SAMPLE_SPRINT_DOC, "test.md")
        assert doc.sprint_number == 11

    def test_parse_sprint_title(self) -> None:
        """Sprint title extracted from heading."""
        doc = parse_sprint_doc(_SAMPLE_SPRINT_DOC, "test.md")
        assert doc.title == "Consolidation"

    def test_parse_sprint_goal(self) -> None:
        """Goal extracted from bold **Goal**: line."""
        doc = parse_sprint_doc(_SAMPLE_SPRINT_DOC, "test.md")
        assert "remaining gaps" in doc.goal

    def test_extract_tracks_count(self) -> None:
        """Correct number of tracks extracted."""
        tracks = extract_tracks(_SAMPLE_SPRINT_DOC)
        assert len(tracks) == 3

    def test_extract_track_names(self) -> None:
        """Track letters match A, B, C."""
        tracks = extract_tracks(_SAMPLE_SPRINT_DOC)
        names = [t.name for t in tracks]
        assert names == ["A", "B", "C"]

    def test_extract_track_titles(self) -> None:
        """Track titles extracted from headings."""
        tracks = extract_tracks(_SAMPLE_SPRINT_DOC)
        assert tracks[0].title == "FRAMEWORK.md v20.0 Rewrite"
        assert "Slim-Down" in tracks[1].title

    def test_extract_prd_refs(self) -> None:
        """PRD references extracted from text."""
        refs = extract_prd_refs("PRD-CORE-013 and PRD-QUAL-004 are related to PRD-CORE-013")
        assert refs == ["PRD-CORE-013", "PRD-QUAL-004"]

    def test_extract_prd_refs_empty(self) -> None:
        """No PRD refs in text returns empty list."""
        refs = extract_prd_refs("no prd references here")
        assert refs == []

    def test_extract_track_prd_scope(self) -> None:
        """PRD scope extracted from track section content."""
        tracks = extract_tracks(_SAMPLE_SPRINT_DOC)
        # Track A mentions PRD-CORE-013, PRD-QUAL-004, PRD-QUAL-007
        assert "PRD-CORE-013" in tracks[0].prd_scope
        assert "PRD-QUAL-004" in tracks[0].prd_scope

    def test_extract_track_files(self) -> None:
        """Files extracted from ### Files Modified section."""
        tracks = extract_tracks(_SAMPLE_SPRINT_DOC)
        # Track A has 3 files
        assert len(tracks[0].files) >= 2
        assert any("FRAMEWORK.md" in f for f in tracks[0].files)

    def test_extract_track_files_track_b(self) -> None:
        """Track B files extracted correctly."""
        tracks = extract_tracks(_SAMPLE_SPRINT_DOC)
        assert len(tracks[1].files) >= 2
        assert any("learning.py" in f for f in tracks[1].files)

    def test_extract_dod_items(self) -> None:
        """DoD checkbox items extracted."""
        items = extract_dod_items(
            "- [ ] Item one\n- [x] Item two\n- [ ] Item three\n"
        )
        assert len(items) == 3
        assert items[0] == "[ ] Item one"
        assert items[1] == "[x] Item two"

    def test_extract_file_overlap_matrix(self) -> None:
        """File overlap matrix parsed from markdown table."""
        entries = extract_file_overlap_matrix(_SAMPLE_SPRINT_DOC)
        assert len(entries) >= 3
        paths = [e.file_path for e in entries]
        assert any("FRAMEWORK.md" in p for p in paths)

    def test_file_overlap_conflict_detection(self) -> None:
        """Conflict column parsed — server.py has overlap."""
        entries = extract_file_overlap_matrix(_SAMPLE_SPRINT_DOC)
        conflict_files = [e.file_path for e in entries if e.has_conflict]
        assert any("server.py" in f for f in conflict_files)

    def test_extract_validation_criteria(self) -> None:
        """Validation criteria extracted as checkbox items."""
        tracks = extract_tracks(_SAMPLE_SPRINT_DOC)
        # Track A has 3 validation items
        assert len(tracks[0].validation_criteria) >= 2

    def test_parse_missing_sprint_number_raises(self) -> None:
        """Document without sprint number raises ValidationError."""
        with pytest.raises(ValidationError, match="sprint number"):
            parse_sprint_doc("# Some Random Doc\n\nNo sprint here.", "bad.md")

    def test_parse_empty_tracks(self) -> None:
        """Document with sprint number but no tracks returns empty list."""
        doc = parse_sprint_doc("# Sprint 99: Empty\n\nNo tracks here.", "empty.md")
        assert doc.sprint_number == 99
        assert doc.tracks == []

    def test_get_track_by_name_found(self) -> None:
        """Existing track returned by name lookup."""
        doc = parse_sprint_doc(_SAMPLE_SPRINT_DOC, "test.md")
        track = get_track_by_name(doc, "B")
        assert track.name == "B"

    def test_get_track_by_name_case_insensitive(self) -> None:
        """Track lookup is case-insensitive."""
        doc = parse_sprint_doc(_SAMPLE_SPRINT_DOC, "test.md")
        track = get_track_by_name(doc, "a")
        assert track.name == "A"

    def test_get_track_by_name_not_found(self) -> None:
        """Missing track raises ValidationError."""
        doc = parse_sprint_doc(_SAMPLE_SPRINT_DOC, "test.md")
        with pytest.raises(ValidationError, match="not found"):
            get_track_by_name(doc, "Z")

    def test_extract_merge_order(self) -> None:
        """Merge order text extracted."""
        doc = parse_sprint_doc(_SAMPLE_SPRINT_DOC, "test.md")
        assert "Any order" in doc.merge_order

    def test_dod_items_from_full_doc(self) -> None:
        """DoD items collected from Definition of Done section."""
        doc = parse_sprint_doc(_SAMPLE_SPRINT_DOC, "test.md")
        assert len(doc.dod_items) >= 3
        # Mix of checked and unchecked
        checked = [i for i in doc.dod_items if i.startswith("[x]")]
        unchecked = [i for i in doc.dod_items if i.startswith("[ ]")]
        assert len(checked) >= 1
        assert len(unchecked) >= 1


# ---------------------------------------------------------------------------
# TestSprintStart
# ---------------------------------------------------------------------------


class TestSprintStart:
    """Tests for trw_sprint_start tool."""

    async def test_start_returns_sprint_info(self, sprint_doc_path: Path) -> None:
        """Basic start returns sprint metadata."""
        tools = _get_tools()
        result = await tools["trw_sprint_start"].fn(
            sprint_doc_path=str(sprint_doc_path),
            track="A",
        )
        assert result["sprint_number"] == 11
        assert result["track"] == "A"
        assert "FRAMEWORK.md" in result["track_title"]

    async def test_start_creates_run_directory(self, sprint_doc_path: Path, tmp_path: Path) -> None:
        """Run directory created with meta/run.yaml."""
        tools = _get_tools()
        result = await tools["trw_sprint_start"].fn(
            sprint_doc_path=str(sprint_doc_path),
            track="B",
        )
        run_path = Path(str(result["run_path"]))
        assert run_path.exists()
        assert (run_path / "meta" / "run.yaml").exists()
        assert (run_path / "meta" / "events.jsonl").exists()

    async def test_start_registers_track(self, sprint_doc_path: Path, tmp_path: Path) -> None:
        """Track registered in .trw/tracks/ registry."""
        tools = _get_tools()
        await tools["trw_sprint_start"].fn(
            sprint_doc_path=str(sprint_doc_path),
            track="A",
        )
        registry = tmp_path / ".trw" / "tracks" / "sprint-11.yaml"
        assert registry.exists()

    async def test_start_generates_kickoff_prompt(self, sprint_doc_path: Path) -> None:
        """Kickoff prompt contains essential context."""
        tools = _get_tools()
        result = await tools["trw_sprint_start"].fn(
            sprint_doc_path=str(sprint_doc_path),
            track="A",
        )
        prompt = str(result["kickoff_prompt"])
        assert "Sprint" in prompt
        assert "Track" in prompt
        assert "A" in prompt
        assert "PRD" in prompt or "Files" in prompt
        assert "feat(sprint11)" in prompt

    async def test_start_includes_file_list(self, sprint_doc_path: Path) -> None:
        """Result includes parsed file list."""
        tools = _get_tools()
        result = await tools["trw_sprint_start"].fn(
            sprint_doc_path=str(sprint_doc_path),
            track="A",
        )
        assert result["file_count"] >= 2
        assert isinstance(result["files"], list)

    async def test_start_includes_prd_scope(self, sprint_doc_path: Path) -> None:
        """Result includes PRD scope from track."""
        tools = _get_tools()
        result = await tools["trw_sprint_start"].fn(
            sprint_doc_path=str(sprint_doc_path),
            track="A",
        )
        assert isinstance(result["prd_scope"], list)
        assert len(result["prd_scope"]) >= 1

    async def test_start_invalid_track_raises(self, sprint_doc_path: Path) -> None:
        """Starting a non-existent track raises error."""
        tools = _get_tools()
        with pytest.raises(ValidationError, match="not found"):
            await tools["trw_sprint_start"].fn(
                sprint_doc_path=str(sprint_doc_path),
                track="Z",
            )

    async def test_start_missing_doc_raises(self, tmp_path: Path) -> None:
        """Starting with non-existent doc path raises error."""
        tools = _get_tools()
        with pytest.raises(ValidationError, match="not found"):
            await tools["trw_sprint_start"].fn(
                sprint_doc_path=str(tmp_path / "nonexistent.md"),
                track="A",
            )

    async def test_start_relative_path(self, tmp_path: Path) -> None:
        """Relative path resolved against project root."""
        doc_path = tmp_path / "sprints" / "sprint-11.md"
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(_SAMPLE_SPRINT_DOC, encoding="utf-8")

        tools = _get_tools()
        result = await tools["trw_sprint_start"].fn(
            sprint_doc_path="sprints/sprint-11.md",
            track="C",
        )
        assert result["sprint_number"] == 11
        assert result["track"] == "C"


# ---------------------------------------------------------------------------
# TestSprintFinish
# ---------------------------------------------------------------------------


class TestSprintFinish:
    """Tests for trw_sprint_finish tool."""

    async def test_finish_missing_tracks(self, sprint_doc_path: Path) -> None:
        """Finish with no committed tracks reports all missing."""
        tools = _get_tools()
        with patch("trw_mcp.tools.sprint.subprocess") as mock_sub:
            mock_sub.run.return_value = type("R", (), {
                "returncode": 0, "stdout": "", "stderr": "",
            })()
            mock_sub.TimeoutExpired = TimeoutError

            result = await tools["trw_sprint_finish"].fn(
                sprint_doc_path=str(sprint_doc_path),
            )

        assert result["all_tracks_committed"] is False
        assert len(result["missing_tracks"]) == 3
        assert "A" in result["missing_tracks"]

    async def test_finish_all_committed(self, sprint_doc_path: Path) -> None:
        """All tracks committed triggers build check."""
        tools = _get_tools()

        def mock_run(cmd: list[str], **kwargs: object) -> object:
            # git log returns commits for all tracks
            if "git" in cmd and "log" in cmd:
                return type("R", (), {
                    "returncode": 0, "stdout": "abc1234 feat(sprint11): Track A\n",
                    "stderr": "",
                })()
            # git diff returns no files
            if "git" in cmd and "diff" in cmd:
                return type("R", (), {
                    "returncode": 0, "stdout": "",
                    "stderr": "",
                })()
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch("trw_mcp.tools.sprint.subprocess") as mock_sub:
            mock_sub.run.side_effect = mock_run
            mock_sub.TimeoutExpired = TimeoutError
            # Also mock the build check to avoid actually running pytest/mypy
            with patch("trw_mcp.tools.build.run_build_check") as mock_build:
                from trw_mcp.models.build import BuildStatus

                mock_build.return_value = BuildStatus(
                    tests_passed=True,
                    mypy_clean=True,
                    coverage_pct=89.0,
                    test_count=1626,
                    failure_count=0,
                    failures=[],
                    timestamp="2026-02-11T12:00:00Z",
                    scope="full",
                    duration_secs=120.0,
                )

                result = await tools["trw_sprint_finish"].fn(
                    sprint_doc_path=str(sprint_doc_path),
                )

        assert result["all_tracks_committed"] is True
        assert len(result["missing_tracks"]) == 0
        assert result["build"]["status"] == "complete"
        assert result["build"]["tests_passed"] is True

    async def test_finish_dod_status(self, sprint_doc_path: Path) -> None:
        """DoD items parsed with checked/unchecked counts."""
        tools = _get_tools()
        with patch("trw_mcp.tools.sprint.subprocess") as mock_sub:
            mock_sub.run.return_value = type("R", (), {
                "returncode": 0, "stdout": "", "stderr": "",
            })()
            mock_sub.TimeoutExpired = TimeoutError

            result = await tools["trw_sprint_finish"].fn(
                sprint_doc_path=str(sprint_doc_path),
            )

        dod = result["dod_status"]
        assert dod["total"] >= 3
        assert dod["checked"] >= 1
        assert dod["unchecked"] >= 1

    async def test_finish_simplifier_waves(self, sprint_doc_path: Path) -> None:
        """Changed .py files grouped into simplifier waves."""
        tools = _get_tools()

        def mock_run(cmd: list[str], **kwargs: object) -> object:
            if "diff" in cmd:
                return type("R", (), {
                    "returncode": 0,
                    "stdout": "\n".join(f"src/file_{i}.py" for i in range(25)),
                    "stderr": "",
                })()
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with patch("trw_mcp.tools.sprint.subprocess") as mock_sub:
            mock_sub.run.side_effect = mock_run
            mock_sub.TimeoutExpired = TimeoutError

            result = await tools["trw_sprint_finish"].fn(
                sprint_doc_path=str(sprint_doc_path),
            )

        waves = result["simplifier_waves"]
        assert waves["total_files"] == 25
        assert waves["wave_count"] == 3  # 25 files / 10 per wave = 3 waves
        assert waves["wave_size"] == 10

    async def test_finish_next_steps_missing_tracks(self, sprint_doc_path: Path) -> None:
        """Next steps include track commit instructions when tracks missing."""
        tools = _get_tools()
        with patch("trw_mcp.tools.sprint.subprocess") as mock_sub:
            mock_sub.run.return_value = type("R", (), {
                "returncode": 0, "stdout": "", "stderr": "",
            })()
            mock_sub.TimeoutExpired = TimeoutError

            result = await tools["trw_sprint_finish"].fn(
                sprint_doc_path=str(sprint_doc_path),
            )

        assert any("Commit" in step or "commit" in step.lower()
                    for step in result["next_steps"])

    async def test_finish_missing_doc_raises(self, tmp_path: Path) -> None:
        """Finish with non-existent doc raises error."""
        tools = _get_tools()
        with pytest.raises(ValidationError, match="not found"):
            await tools["trw_sprint_finish"].fn(
                sprint_doc_path=str(tmp_path / "nonexistent.md"),
            )

    async def test_finish_ready_flag(self, sprint_doc_path: Path) -> None:
        """Ready flag reflects whether sprint is fully complete."""
        tools = _get_tools()
        with patch("trw_mcp.tools.sprint.subprocess") as mock_sub:
            mock_sub.run.return_value = type("R", (), {
                "returncode": 0, "stdout": "", "stderr": "",
            })()
            mock_sub.TimeoutExpired = TimeoutError

            result = await tools["trw_sprint_finish"].fn(
                sprint_doc_path=str(sprint_doc_path),
            )

        # Not ready because tracks aren't committed and DoD items unchecked
        assert result["ready"] is False
