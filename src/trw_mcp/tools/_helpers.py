"""Shared helpers for TRW tool modules.

Extracted to avoid duplication across ceremony.py and _deferred_delivery.py.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping


def _run_step(
    name: str,
    fn: Callable[[], Mapping[str, object] | None],
    results: dict[str, object],
    errors: list[str],
) -> None:
    """Execute a delivery step with fail-open error handling.

    If ``fn`` returns a dict/mapping, it is stored in ``results[name]``
    (converted to a plain ``dict``).
    If ``fn`` returns None, nothing is stored (used for conditional steps).
    Exceptions are appended to ``errors`` and a failure dict is stored.
    """
    try:
        step_result = fn()
        if step_result is not None:
            results[name] = dict(step_result)
    except Exception as exc:  # justified: fail-open, individual delivery step must not block others
        errors.append(f"{name}: {exc}")
        results[name] = {"status": "failed", "error": str(exc)}
