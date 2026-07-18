"""State helpers for SAFE-001 candidate promotion."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import structlog

from trw_mcp._locking import _lock_ex, _lock_un

logger = structlog.get_logger(__name__)


def load_recent_history(audit_log_path: Path, *, max_rows: int = 20) -> list[dict[str, object]]:
    """Reconstruct the Goodhart lookback window from the durable audit log."""
    if not audit_log_path.exists():
        return []
    history: list[dict[str, object]] = []
    try:
        with audit_log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict) or obj.get("event") != "promoted":
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict):
                    continue
                delta = payload.get("declared_metric_delta")
                if isinstance(delta, (int, float)) and not isinstance(delta, bool):
                    history.append({"declared_metric_delta": float(delta)})
    except OSError as exc:
        logger.warning(
            "goodhart_history_load_failed",
            component="meta_tune.promote",
            op="load_recent_history",
            outcome="degraded",
            error=str(exc),
        )
        return []
    return history[-max_rows:]


@contextmanager
def target_write_lock(target_path: Path, state_dir: Path) -> Iterator[None]:
    """Exclusive advisory lock serializing a target's read-modify-write."""
    locks_dir = state_dir / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(target_path).encode("utf-8")).hexdigest()[:32]
    with (locks_dir / f"{digest}.lock").open("a", encoding="utf-8") as lock_fh:
        _lock_ex(lock_fh.fileno())
        try:
            yield
        finally:
            _lock_un(lock_fh.fileno())


__all__ = ["load_recent_history", "target_write_lock"]
