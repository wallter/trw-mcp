from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateWriter


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw structure for adapter tests."""
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir()
    return d


@pytest.fixture
def trw_dir_with_entries(trw_dir: Path) -> Path:
    """Create a .trw structure with sample YAML learning entries."""
    entries_dir = trw_dir / "learnings" / "entries"
    writer = FileStateWriter()
    writer.write_yaml(
        entries_dir / "2026-01-01-test-learning.yaml",
        {
            "id": "L-test0001",
            "summary": "Test learning about Python",
            "detail": "Python is a great language",
            "tags": ["python", "testing"],
            "evidence": [],
            "impact": 0.8,
            "status": "active",
            "source_type": "agent",
            "source_identity": "test",
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "access_count": 0,
            "q_value": 0.5,
            "q_observations": 0,
            "recurrence": 1,
        },
    )
    writer.write_yaml(
        entries_dir / "2026-01-02-second-learning.yaml",
        {
            "id": "L-test0002",
            "summary": "Testing gotcha with mocking",
            "detail": "Always patch at the import site",
            "tags": ["testing", "gotcha"],
            "evidence": ["test_foo.py"],
            "impact": 0.6,
            "status": "active",
            "source_type": "human",
            "source_identity": "Tyler",
            "created": "2026-01-02",
            "updated": "2026-01-02",
            "access_count": 3,
            "q_value": 0.7,
            "q_observations": 2,
            "recurrence": 2,
        },
    )
    writer.write_yaml(
        entries_dir / "2026-01-03-obsolete-entry.yaml",
        {
            "id": "L-test0003",
            "summary": "Obsolete learning",
            "detail": "No longer relevant",
            "tags": ["old"],
            "evidence": [],
            "impact": 0.4,
            "status": "obsolete",
            "source_type": "agent",
            "source_identity": "",
            "created": "2026-01-03",
            "updated": "2026-01-03",
            "access_count": 0,
            "q_value": 0.3,
            "q_observations": 0,
            "recurrence": 1,
        },
    )
    return trw_dir
