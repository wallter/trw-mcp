from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path


def _make_trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw directory with context subdirectory."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    return trw_dir


def _write_build_status(
    trw_dir: Path,
    *,
    tests_passed: bool = True,
    mypy_clean: bool = True,
    coverage_pct: float = 90.0,
    scope: str = "full",
    age_secs: int = 0,
) -> Path:
    """Write a build-status.yaml with controlled content."""
    if age_secs > 0:
        ts = datetime.fromtimestamp(time.time() - age_secs, tz=timezone.utc)
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        ts_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cache_path = trw_dir / "context" / "build-status.yaml"
    content = (
        f"tests_passed: {'true' if tests_passed else 'false'}\n"
        f"mypy_clean: {'true' if mypy_clean else 'false'}\n"
        f"coverage_pct: {coverage_pct}\n"
        f"scope: {scope}\n"
        f'timestamp: "{ts_str}"\n'
    )
    cache_path.write_text(content, encoding="utf-8")
    return cache_path
