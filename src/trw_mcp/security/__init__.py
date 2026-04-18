"""MCP server authorization + capability scoping (PRD-INFRA-SEC-001).

This package implements the trust boundary around MCP servers exposed to
TRW-brokered agent sessions. It covers:

* Signed registry of allowlisted MCP servers (FR-1)
* Per-tool capability scoping filter (FR-2)
* Signature verification hooks (observe-mode stub in v1; FR-5)

The anomaly detector (FR-3) lives in ``anomaly_detector.py`` and is
implemented in a subsequent sprint wave.
"""
