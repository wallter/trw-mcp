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
    trust_walk_sample_rate: float = 0.3
    trust_security_tags: tuple[str, ...] = ("auth", "secrets", "permissions", "encryption", "oauth", "jwt")
    trust_locked: bool = False

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
