"""Shared constants for state modules.

Centralizes magic numbers and string prefixes that were previously
hardcoded across multiple state modules.
"""

from __future__ import annotations

# Effective "unlimited" cap for list/search operations across the memory adapter,
# knowledge topology, and report modules.
DEFAULT_LIST_LIMIT: int = 10_000

# Default namespace for memory backend operations.
DEFAULT_NAMESPACE: str = "default"

# Valid source provenance values accepted by ``_validate_source_type``.
#
# PRD-CORE-247-FR04 corrected this comment: it claimed alignment with
# ``trw_memory.models.memory.MemoryEntry``, but that model carries a sixth value,
# ``company_sync``, which this set does not. The two lists live in separately
# versioned public packages and have already drifted, so this is a SUBSET of the
# storage-layer whitelist, not a mirror of it. Extending both to mark a new write
# path is disproportionate; use ``source_identity`` (a plain string with no
# whitelist validator) for provenance that must survive the write.
VALID_SOURCES: frozenset[str] = frozenset({"human", "agent", "tool", "consolidated", "team_sync"})

# PRD-CORE-247-FR04: the two markers an offline (``trw-mcp local``) learning write
# carries, with deliberately different lifetimes.
#
# ``LOCAL_CLI_SOURCE_IDENTITY`` is DURABLE provenance — it answers "where did this
# come from" permanently and is never cleared. ``RECONCILE_PENDING_TAG`` is a
# TRANSIENT queue entry — it answers "has anyone been told about this yet", and
# PRD-CORE-247-FR05 removes it from exactly the rows the next successful
# ``trw_session_start`` reports.
LOCAL_CLI_SOURCE_IDENTITY: str = "local_cli"
RECONCILE_PENDING_TAG: str = "trw-reconcile-pending"
