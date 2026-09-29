"""Trust model and complexity classification fields.

Covers sections 39, 51 (trust) of the original _main_fields.py:
  - Trust boundaries
  - Complexity classification (CORE-060)
"""

from __future__ import annotations


class _TrustFields:
    """Trust domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Trust boundaries --

    trust_crawl_boundary: int = 50
    trust_walk_boundary: int = 200
    # trust_walk_sample_rate, trust_security_tags and trust_locked were removed in trw-mcp 8.0.0 (PRD-CORE-313-FR06): no
    # production reader. Keys are listed in trw_mcp/data/config-retired-keys.json.

    # -- Complexity classification (CORE-060) --

    complexity_tier_minimal: int = 1
    complexity_tier_comprehensive: int = 6
    complexity_weight_novel_patterns: int = 3
    complexity_weight_cross_cutting: int = 2
    complexity_weight_architecture_change: int = 3
    complexity_weight_external_integration: int = 2
    complexity_weight_large_refactoring: int = 1
    complexity_weight_files_affected_max: int = 5
    complexity_hard_override_threshold: int = 2
