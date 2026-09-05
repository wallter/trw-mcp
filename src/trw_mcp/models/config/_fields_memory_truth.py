"""Memory-truth invariants: decay wiring, tier protection, validity windows.

PRD-CORE-244 domain mixin. Split from ``_fields_memory.py`` because that file
sits against the 200-line domain-mixin ceiling
(``tests/test_config_fields.py::test_domain_mixin_files_under_200_lines``) and
these five knobs arrive together: each one turns a field that *described* a
guarantee into a field that *enforces* one.
"""

from __future__ import annotations

from pydantic import Field, field_validator


class _MemoryTruthFields:
    """PRD-CORE-244 memory-truth mixin — mixed into _TRWConfigFields via MI."""

    # -- Importance decay (PRD-CORE-244 FR09) --
    # ``memory_decay_pass`` was a hardened, locked, batched sweep with ZERO
    # production callers, so importance could rise (via apply_importance_boost)
    # and structurally never fell — a one-directional ratchet on the field both
    # compute_utility_score and prune-candidate selection key on. It now runs as
    # a deferred-delivery step, over these typed knobs instead of the literal
    # parameter defaults it used to carry.
    memory_decay_cutoff_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        description="Days an entry must go unused before importance decay may lower its importance.",
    )
    memory_decay_batch_size: int = Field(
        default=1000,
        ge=1,
        le=10_000,
        description="Maximum rows one importance-decay pass rewrites, bounding the writer-lock hold.",
    )

    # -- Automatic-removal protection (PRD-CORE-244 FR10) --
    # ``protection_tier`` was advertised by trw_learn, validated, stamped and
    # updatable, and every DESTRUCTIVE path ignored it: a learning marked
    # ``permanent`` was nominated for pruning on exactly the same schedule as a
    # ``normal`` one. This table multiplies the utility threshold a candidate
    # must fall BELOW before it is nominated, so a ``critical`` entry at 0.25
    # must be four times less useful than a ``normal`` one. ``protected`` and
    # ``permanent`` are absent because they are exempt outright, not discounted.
    # Mirrors MemoryConfig.protection_tier_prune_discount.
    protection_tier_prune_discount: dict[str, float] = Field(
        default_factory=lambda: {"critical": 0.25, "high": 0.5, "normal": 1.0, "low": 1.5},
        description=(
            "Multiplier on the utility threshold an entry must fall below before an automatic "
            "prune, tier demotion or purge may nominate it (PRD-CORE-244 FR10)."
        ),
    )

    # -- State-assertion validity windows (PRD-CORE-244 FR05) --
    # The window trw_learn PROPOSES (never sets) for a learning whose text
    # asserts current state. Types absent from this table record invariants and
    # are never offered a window — that is why ``convention`` and ``pattern`` do
    # not appear, and removing a key here is how an operator opts a type out.
    state_learning_default_ttl_days: dict[str, int] = Field(
        default_factory=lambda: {"incident": 90, "hypothesis": 30, "workaround": 180},
        description=(
            "Default validity window in days, per learning type, proposed for a state-asserting "
            "learning (PRD-CORE-244 FR05). Advisory only — nothing writes expires from it."
        ),
    )

    @field_validator("protection_tier_prune_discount")
    @classmethod
    def _bound_protection_discounts(cls, value: dict[str, float]) -> dict[str, float]:
        for tier, discount in value.items():
            if not 0.0 <= discount <= 4.0:
                raise ValueError(f"protection_tier_prune_discount[{tier!r}]={discount} is outside [0.0, 4.0]")
        return value

    @field_validator("state_learning_default_ttl_days")
    @classmethod
    def _bound_state_ttls(cls, value: dict[str, int]) -> dict[str, int]:
        for learning_type, days in value.items():
            if not 1 <= days <= 3650:
                raise ValueError(f"state_learning_default_ttl_days[{learning_type!r}]={days} is outside [1, 3650]")
        return value
