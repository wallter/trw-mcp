"""TRW Framework MCP Server — orchestration, requirements, and self-learning tools.

Error Handling Policy (PRD-FIX-043)
------------------------------------
Every ``except Exception`` block in this package MUST be annotated with a  # justified: fail-open
``# justified: <category>, <rationale>`` comment. Recognised categories:

- **fail-open** — telemetry, logging, or analytics that must never block
  the primary tool flow. Swallowing is acceptable; logging at ``debug``
  or ``warning`` with ``exc_info=True`` is preferred.
- **boundary** — external system calls (Anthropic API, subprocess, network)
  where the full exception surface is unpredictable. Always log with
  ``exc_info=True`` at ``warning`` level.
- **cleanup** — resource release (file locks, temp files, connections)
  where failure during cleanup must not mask the original result.
- **import-guard** — optional dependency checks (``try: import X``).
  Log at ``warning`` with install instructions.
- **scan-resilience** — iterating over user-generated data (YAML entries,
  JSONL lines) where a single malformed record must not abort the scan.
  Log at ``debug`` per-item, summarise at ``warning`` if any skipped.

Bare ``except Exception: pass`` without logging is prohibited.  # justified: fail-open
New ``except Exception`` blocks require both the ``# justified:`` comment
and a corresponding log call.
"""

from importlib.metadata import version as _pkg_version

__version__: str = _pkg_version("trw-mcp")

__all__ = ["__version__"]
