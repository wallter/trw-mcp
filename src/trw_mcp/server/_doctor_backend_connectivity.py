"""The ``backend_connectivity`` doctor row: resolved egress posture vs probe scope.

Belongs to the ``_subcommands_doctor.py`` facade, which re-exports
``_probe_backend_url`` and ``backend_connectivity_row`` so the check table and
the tests that monkeypatch the probe keep one import point. Split out for the
module-size gate, following ``_doctor_memory_wal.py``.

The whole reason this row needs its own module-level docstring is that it once
asserted the opposite of the truth, and the distinction it now keeps is easy to
collapse again by accident:

* **Probe scope** — this check deliberately probes ONLY an explicitly configured
  ``backend_url``, which is an owned or local host. It must never reach a prod
  host, because a diagnostic that phones home is not a diagnostic. That scope is
  correct and is not what was wrong.
* **Resolved posture** — what the process will actually talk to. Every real
  consumer (``submit_feedback``, sync push and pull) reads
  ``resolved_backend_url``, which also resolves ``platform_urls`` ×
  ``platform_api_key``. On the shipped configuration ``backend_url`` is empty
  while ``resolved_backend_url`` points at prod.

Collapsing the two produced "no backend_url configured — fully offline (no
network call made)": a true statement about the CHECK read as a false statement
about the PROCESS, on the one row an operator reads to audit egress
(``sub_alqafW7Pst40zAIK``, 2026-09-07).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

__all__ = ["backend_connectivity_row", "probe_backend_url"]


def probe_backend_url(url: str) -> tuple[bool, str]:
    """Probe an owned/local ``backend_url`` (patchable seam). Never a prod host."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 — owned URL only
            return True, f"{resp.status} {getattr(resp, 'reason', '')}".strip()
    except urllib.error.URLError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def backend_connectivity_row(config: TRWConfig) -> tuple[str, str]:
    """Return ``(status, message)`` for the ``backend_connectivity`` row.

    States the resolved posture and the probe decision as two separate facts —
    see the module docstring for why they must not be merged again.
    """
    url = str(config.backend_url or "").strip()
    if url:
        ok, detail = probe_backend_url(url)
        if ok:
            return "PASS", f"backend_url reachable: {detail}"
        return "FAIL", f"backend_url unreachable: {detail}"

    resolved = str(getattr(config, "resolved_backend_url", "") or "").strip()
    if resolved:
        host = urlsplit(resolved).netloc or resolved
        return (
            "SKIP",
            f"platform backend configured ({host}, key present) — submit_feedback and sync "
            "will reach it. Connectivity NOT probed here: this check probes only an "
            "explicitly configured local/owned backend_url, never a prod host, so this row "
            "says nothing about whether that host is up.",
        )
    return (
        "SKIP",
        "no backend_url and no resolved platform target — no egress path configured (no network call made).",
    )
