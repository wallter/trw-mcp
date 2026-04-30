"""Shared helpers for dedup tests."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.persistence import FileStateWriter

def mock_embed(text: str) -> list[float]:
    """Return a deterministic 384-dim vector based on text content.

    This creates vectors where similar texts produce similar hashes,
    and identical texts produce identical vectors.
    """
    import hashlib

    h = hashlib.sha256(text.encode()).digest()
    vec = [float(b) / 255.0 for b in h] * 12  # 32 * 12 = 384
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0.0:
        return [0.0] * 384
    return [v / norm for v in vec]

def write_entry(entries_dir: Path, writer: FileStateWriter, entry_id: str, summary: str, detail: str) -> Path:
    """Write a minimal learning entry YAML for testing."""
    path = entries_dir / f"{entry_id}.yaml"
    writer.write_yaml(
        path,
        {
            "id": entry_id,
            "summary": summary,
            "detail": detail,
            "tags": ["test"],
            "evidence": [],
            "impact": 0.5,
            "status": "active",
            "recurrence": 1,
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "merged_from": [],
        },
    )
    return path
