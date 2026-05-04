"""Braille-character spinner for the device-auth login flow.

Extracted from :mod:`trw_mcp.cli.auth` (PRD-DIST-243 Phase 1, cycle 22)
to keep ``auth.py`` under the 350-effective-LOC operator threshold.
The spinner runs in a daemon thread so it doesn't block the polling
loop or shutdown.
"""

from __future__ import annotations

import sys
import threading

__all__ = ["_SPINNER_FRAMES", "_Spinner"]


_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class _Spinner:
    """Braille-character spinner in a daemon thread."""

    def __init__(self, message: str) -> None:
        self.message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        idx = 0
        while not self._stop.is_set():
            frame = _SPINNER_FRAMES[idx % len(_SPINNER_FRAMES)]
            sys.stdout.write(f"\r\033[K            {frame} {self.message}")
            sys.stdout.flush()
            idx += 1
            self._stop.wait(0.1)
