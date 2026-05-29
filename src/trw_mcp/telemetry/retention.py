"""Local telemetry JSONL retention and compression helpers."""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path


def rotate_telemetry_log(path: Path, *, max_bytes: int, compress: bool = True) -> dict[str, object]:
    """Rotate a telemetry JSONL file when it exceeds ``max_bytes``."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if not path.exists() or path.stat().st_size <= max_bytes:
        return {"rotated": False, "path": str(path)}
    rotated = path.with_suffix(path.suffix + ".1")
    if rotated.exists():
        rotated.unlink()
    path.rename(rotated)
    path.touch()
    output = rotated
    if compress:
        compressed = rotated.with_suffix(rotated.suffix + ".gz")
        with rotated.open("rb") as src, gzip.open(compressed, "wb") as dst:
            shutil.copyfileobj(src, dst)
        rotated.unlink()
        output = compressed
    return {"rotated": True, "path": str(path), "archive_path": str(output), "compressed": compress}
