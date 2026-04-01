"""Shared default constants for TRWConfig and domain sub-configs.

Single source of truth for values that appear in both _main.py (TRWConfig)
and _sub_models.py (domain sub-configs).  Both modules import from here
instead of hardcoding their own copies.
"""

# -- Build / mutation --
DEFAULT_BUILD_CHECK_TIMEOUT_SECS: int = 300
DEFAULT_MUTATION_TIMEOUT_SECS: int = 300

# -- Learning storage --
DEFAULT_LEARNING_MAX_ENTRIES: int = 500
DEFAULT_RECALL_RECEIPT_MAX_ENTRIES: int = 1000
DEFAULT_RECALL_MAX_RESULTS: int = 25

# -- Orchestration --
DEFAULT_PARALLELISM_MAX: int = 10

# -- Scoring --
DEFAULT_SCORING_DEFAULT_DAYS_UNUSED: int = 30

# -- Ceremony adaptation (CORE-084) --
LIGHT_MODE_RECALL_CAP: int = 10

# -- Compact mode limits --
COMPACT_TAGS_CAP: int = 10  # Max tags per learning in compact mode
