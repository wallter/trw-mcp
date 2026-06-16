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

## Platform Credential Storage (PRD-SEC-005)

The `platform_api_key` bearer credential is a **secret** and is stored OUT of
the git-tracked `config.yaml`. The loader (`_loader.py`) resolves it through
`_credentials.py` with the following precedence (highest wins):

1. **`TRW_PLATFORM_API_KEY`** environment variable — the enterprise path
   (inject from a secret manager; no on-disk key required).
2. **`.trw/credentials.yaml`** — ignored by the bundled `gitignore.txt`,
   written mode `0600` (owner read/write only; best-effort on non-POSIX).
3. **`.trw/config.yaml`** `platform_api_key` — DEPRECATED backward-compat
   fallback. When the key is sourced here, the loader emits exactly one
   deprecation warning per process advising migration via
   `trw-mcp update-project .` (which moves the key into `credentials.yaml`
   and blanks the tracked field, idempotently).

Writers (`cli/auth.py` login, the installer template) target `credentials.yaml`
only — never `config.yaml`. Rotate any key already committed to git history;
the migration cannot rewrite history.
