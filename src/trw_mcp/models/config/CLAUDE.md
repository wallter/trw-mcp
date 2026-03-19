# Config Models

This package contains `TRWConfig` (the root settings model) and its sub-models.

## Client Profile System

`_client_profile.py` defines frozen Pydantic models: `CeremonyWeights`, `ScoringDimensionWeights`, `WriteTargets`, `ClientProfile`. `_profiles.py` holds the built-in registry (`_PROFILES`) and `resolve_client_profile()`.

**Key invariants**:
- All models are `frozen=True` — use `model_copy(update=...)` for overrides
- `CeremonyWeights` fields must be `ge=0` and sum to 100
- `ScoringDimensionWeights` fields must be `ge=0` and sum to ~1.0 (tolerance 0.01)
- `mandatory_phases` are normalized to lowercase by the validator
- `TRWConfig.client_profile` is `@property` (not `@cached_property`) — BaseSettings is not frozen

**When adding a new profile**: add to `_PROFILES` dict in `_profiles.py`, write a wiring test (see `.claude/rules/testing.md` "Wiring Verification Tests"), and verify via `test_client_profile.py`.

Full reference: [`docs/CLIENT-PROFILES.md`](../../../../../docs/CLIENT-PROFILES.md)
