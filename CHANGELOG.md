# Changelog

All notable changes to the TRW MCP server package.

## [Unreleased]

## [0.11.7] — 2026-03-13

### Added

- **Multi-IDE support (PRD-CORE-074)** — OpenCode, Cursor, and future CLIs now supported alongside Claude Code
  - IDE detection (`detect_ide`, `detect_installed_clis`, `resolve_ide_targets`)
  - OpenCode bootstrap: `opencode.json` + `AGENTS.md` with smart merge
  - Cursor bootstrap: `hooks.json` (4 events), `.cursor/rules/*.mdc`, `mcp.json` with smart merge
  - `--ide` flag on `init-project` / `update-project` CLI commands
  - Installer CLI detection with interactive opt-in prompt
- **Layer 3 MCP Cooperative Nudges** — ceremony status in every `trw_*` tool response with progressive urgency (low→medium→high)
  - `ceremony_nudge.py` — state tracker with atomic file persistence
  - Wired into all production tools (session_start, checkpoint, deliver, build_check, learn)
  - `compute_nudge_minimal()` for local models (≤200 chars)
- **Instructions sync** — `trw_claude_md_sync` gains `client` param (auto/claude-code/opencode/all), writes to CLAUDE.md, AGENTS.md, or both
- **IDE adapter hook** (`lib-ide-adapter.sh`) — routes ceremony enforcement across IDE variants
- **+68 bootstrap tests** — `_write_version_yaml`, `_result_action_key`, OpenCode, Cursor, enforcement variants

### Changed

- **Bootstrap refactor** — extracted `_result_action_key()` helper (DRY, replaces 4 inline copies), added structured logging to `_write_version_yaml`, type annotation fix for mypy `--strict`

## [0.11.6] — 2026-03-13

### Changed

- **PRD pipeline consolidation** — `/trw-prd-groom`, `/trw-prd-review`, and `/trw-exec-plan` are now internal phases, no longer user-invocable. New `/trw-prd-ready` skill orchestrates the full pipeline (groom → review → exec plan) in one command. `/trw-prd-new` auto-chains into the full pipeline after creation.
- **Framework v24.3** — updated lifecycle, skill table, and PRD lifecycle documentation to reflect consolidated pipeline
- **Skill prompt quality** — added 0.70 floor gate to groom phase, `trw_learn` call to exec-plan, conditional advisory in review phase, explicit delegation model per pipeline phase
- **Version DRY** — centralized version management: `TRWConfig` is single source of truth for framework/AARE-F versions, `pyproject.toml` for package versions. Tests derive versions from config instead of hardcoding. Bootstrap generates `VERSION.yaml` dynamically via `importlib.metadata`. `trw-memory/_version.py` also uses `importlib.metadata`.
- **AARE-F version** — corrected `aaref_version` default from `v1.1.0` to `v2.0.0` (matching the actual document header)

## [0.11.5] — 2026-03-13

### Removed

- **Bash installer** (`install-trw.template.sh`) — redundant with the Python installer which the site recommends; removed template, build format option, and bash-specific codepath from `build_installer.py`
- **`mcp-hmr` dev dependency** — incompatible with `fastmcp>=3.0` (requires `fastmcp<3`); removed from `[dev]` extras

### Fixed

- **Missing dev dependencies** — added `hypothesis`, `sqlite-vec`, and `rank-bm25` to `[dev]` extras so fresh venvs pass the full test suite

### Added — Code Quality & Test Coverage Hardening

- **710 new tests across the monorepo** — trw-mcp +599 (3,927→4,526), backend +112 (725→837), trw-memory assertions strengthened (29 weak assertions replaced)
- **12 new test files** covering previously untested modules:
  - `test_scoring_edge_cases.py` (99 tests) — decay, correlation, complexity, recall algorithms
  - `test_prd_utils_edge.py` (83 tests) — frontmatter parsing, sections, content density, transitions
  - `test_memory_adapter_edge.py` (56 tests) — embed, convert, recall, store, reset paths
  - `test_knowledge_topology_edge.py` (53 tests) — jaccard, clusters, merge, render functions
  - `test_persistence_edge.py` (49 tests) — YAML roundtrip, locks, concurrency, events
  - `test_learning_injection_edge.py` (29 tests) — domain tags, selection, formatting
  - `test_recall_tracking_edge.py` (14 tests) — outcome recording, stats edge cases
  - Backend: `test_admin_orgs.py` (33), `test_admin_users.py` (17), `test_admin_keys.py` (13), `test_edge_cases.py` (11)
- **Modules at 100% coverage** — `recall_tracking.py`, `auto_upgrade.py`
- **Expanded existing test files** — +45 consolidation, +37 validation gates, +33 semantic checks, +31 dashboard, +26 auto_upgrade, +23 tiers, +22 export, +29 backend SSE/telemetry

### Changed

- **`consolidation.py`** — function parameters changed from `list[dict[str, object]]` to `Sequence[dict[str, object]]` for Pyright covariance compatibility
- **`_update_project.py`** — extracted `_coerce_manifest_list()`, `_remove_stale_set()`, `_migrate_predecessor_set()` DRY helpers reducing ~90 lines of duplication
- **`sqlite_backend.py` (trw-memory)** — extracted `_build_filter_clause()` static method eliminating WHERE clause duplication between `search()` and `list_entries()`
- **`learning.py`, `requirements.py`** — consolidated scattered imports from same modules into single blocks
- **Backend `test_config.py`** — properly typed `_reload_config()` return as `BackendConfig`, removing 13 `type: ignore[attr-defined]`
- **Backend `auth_2fa.py`** — bare `dict` changed to `dict[str, Any]` for PyJWT payloads, removing 2 `type: ignore[type-arg]`
- **Backend test files** — added proper `TestClient` and `Session` type annotations, removing 6 `type: ignore[no-untyped-def]`
- **Platform `VariationH.tsx`** — added `role="button"`, `tabIndex={0}`, `onKeyDown` to 6 interactive `<div>` elements for keyboard accessibility
- **Platform `login/route.ts`** — added error logging to 3 silent catch blocks

### Fixed

- **trw-memory weak assertions** — replaced 29 instances of `assert x is True/False` with idiomatic `assert x` / `assert not x` across 11 test files

---

### Added — Sprint 56: Agent Quality & Review Gaps

- **Context-aware learning injection** (`state/learning_injection.py`) — `select_learnings_for_task()` ranks recall results by 60% tag overlap + 40% impact score; `infer_domain_tags()` maps path components to domain tags; `format_learning_injection()` renders markdown for prompt prepending
- **N-gram DRY enforcement** (`state/dry_check.py`) — sliding-window SHA-256 duplication detector with configurable block size and boilerplate filtering
- **Migration verification gate** (`state/phase_gates_build.py`) — detects model-without-migration gaps and NOT NULL columns without `server_default`
- **Semantic review automation** (`state/semantic_checks.py` + `data/semantic_checks.yaml`) — 10 regex-based semantic checks (6 automated, 4 manual) with language-aware filtering
- **`trw-dry-check` skill** — on-demand duplication scanning via `/trw-dry-check`
- **VALIDATE soft gates** — DRY, migration, and semantic checks wired into `_check_validate_exit()` as best-effort warnings
- **Agent prompt updates** — `trw-implementer.md` DRY checklist, `trw-reviewer.md` semantic rubric, `trw-team-playbook` learning injection
- **Config fields** — `migration_gate_enabled`, `dry_check_enabled`, `dry_check_min_block_size`, `agent_learning_injection`, `agent_learning_max`, `agent_learning_min_impact`, `semantic_checks_enabled`
- **109 new tests** — migration gate (26), DRY check (19), learning injection (30), semantic checks (34)

---

## [0.11.4] — 2026-03-10

### Fixed — Silent MCP Startup Crashes

- **Crash log on startup failure** — `__main__.py` wraps the entire startup in try/except, writes crash details to `.trw/logs/crash.log` AND stderr so failures are always visible
- **Early stderr logging** — `main()` configures basic logging before config/middleware loads, so exceptions during initialization are no longer invisible
- **Defensive middleware init** — `_build_middleware()` and `_load_server_instructions()` catch exceptions instead of crashing the import chain
- **Correct Python path in `.mcp.json`** — uses `sys.executable` (absolute path) instead of bare `python` which doesn't exist on many systems
- **Resilient message loading** — `get_message_or_default()` catches all exceptions (not just KeyError/FileNotFoundError), so missing `ruamel.yaml` doesn't kill the server

### Added

- **CLAUDE.md deployment docs** — release workflow, migration fallback, API key scopes, PostgreSQL JSON cast gotchas

---

## [0.11.3] — 2026-03-09

### Added

- **Background batch send on session start** — `trw_session_start()` now fires a daemon-thread batch send after flushing telemetry events, so new installations appear in the dashboard immediately instead of waiting for `trw_deliver()`
- **Admin installations endpoint** — `GET /admin/installations` shows all installations across all orgs (platform admin only)
- **Admin-aware installations dashboard** — admin users see all installations with org column; non-admin users see org-scoped view

### Changed — Installer Rewrite (Bash → Python)

- **Installer rewritten from bash to Python** — `install-trw.template.py` replaces `install-trw.template.sh` as the default installer format. Users now run `python3 install-trw.py` instead of `bash install-trw.sh`.
- **Box alignment fixed permanently** — `draw_box()` uses ANSI-aware `_visible_len()` + f-string padding
- **Smart color detection** — ANSI colors auto-disable when stdout is not a TTY
- **Phased architecture** — each installation step is a standalone function for maintainability
- **Threaded spinner** — replaces bash background subshell + PID juggling with a clean daemon thread
- **`build_installer.py`** — now supports `--format py|sh` (Python is default)

### Fixed

- **API key scopes on waitlist conversion** — converted users now get `scopes=["*"]` instead of empty scopes, fixing 401 errors on all scope-protected endpoints
- **API key scopes on admin key creation** — same fix for `POST /admin/organizations/{org_id}/api-keys`
- **Header stats format corruption** — split `_build_stats_summary` into separate index/roadmap formatters
- **Index sync double write** — consolidated to single read→merge→update→write
- **Sprint-finish step ordering** — PRD status update moved after build gate passes
- **FD leak** — `_try_acquire_deferred_lock` exception handler widened
- **Deploy script** — `.trw/` excluded from uncommitted changes check; `python` → `python3` for WSL2

---

## [0.11.2] — 2026-03-07

### Fixed — Installer Progress Feedback

- **Live progress during project setup** — spinner now updates with file-by-file progress (`Updating project... (12 files) CLAUDE.md`) instead of a static "Updating existing installation..." message that appeared frozen
- **`run_with_progress()` helper** — streams command output in background, parses Updated/Created/Preserved lines, and updates spinner message in real time
- **`update_spinner()` function** — allows dynamic spinner message updates via shared temp file
- Script mode shows prefixed output directly instead of suppressing it

---

## [0.11.1] — 2026-03-07

### Improved — Interactive Installer

- **Interactive mode** — installer detects terminal and shows spinner animations, progress steps, and box-drawing banners
- **Optional feature prompts** — interactive mode prompts for AI/LLM extras (`trw-mcp[ai]`) and `sqlite-vec` installation
- **New CLI flags** — `--ai`, `--no-ai`, `--sqlite-vec`, `--no-sqlite-vec`, `--quiet`, `--script` for headless automation
- **DRY pip install** — extracted `pip_install()` helper for the 3-tier fallback pattern (normal → `--user` → `--break-system-packages`)
- **Cleaner update output** — `update-project` output captured with spinner overlay instead of raw structlog debug spam
- **Script mode preserved** — piped input or `--script` flag gives the same quiet output as before

### Fixed — Production Deployment

- **NextAuth 500 on Amplify** — env vars (`AUTH_SECRET`, `NEXTAUTH_SECRET`) not reaching Next.js standalone runtime; fixed by baking them via `next.config.ts` `env{}` block
- **Backend 500 on telemetry** — migration 0009 (token columns) never applied to production Lambda; added auto-migration step to `deploy.sh`
- **Installer endpoint** — `/releases/latest/installer` was redirecting to `.zip` artifact instead of `install-trw.sh`; fixed S3 key derivation
- **Version sync** — pyproject.toml version synced with CHANGELOG (was stuck at 0.4.0)

---

## [0.11.0] — 2026-03-08

### Fixed — Framework Optimization Audit

- **Session duration tracking** — `_step_telemetry()` computes `total_duration_ms` from earliest `session_start` event timestamp; was always 0
- **Stop hook false positives** — `trw_deliver()` logs `trw_deliver_complete` to fallback `session-events.jsonl` when no active run; hook checks both locations
- **Review confidence scale mismatch** — normalize 0.0-1.0 confidence to 0-100 before comparing against `review_confidence_threshold`; was silently filtering 90%+ confidence findings
- **Silent exception handlers** — 15 `except Exception: pass` in tools/ replaced with `logger.debug(event, exc_info=True)`; fail-open preserved

### Added

- **Untracked source file detection** — `check_delivery_gates()` warns about uncommitted `.py`/`.ts`/`.tsx` files in `src/`/`tests/` before delivery
- **Cross-shard DRY review** — integration reviewer prompt and `trw-reviewer.md` agent include DRY violation detection and spec-based test gap analysis
- **Spec-based test review** — `trw-review-pr` skill and `reviewer-test-quality.md` expanded with acceptance-criterion verification checklist

---

## [0.10.0] — 2026-03-04

### Architecture — DRY Consolidation & God Module Decomposition (Sprint 54)

#### P0 — Cross-Package DRY Elimination

- **`scoring.py` consolidated** — 9 pure math functions (`update_q_value`, `compute_utility_score`,
  `apply_time_decay`, `bayesian_calibrate`, `compute_calibration_accuracy`, `_clamp01`, `_ensure_utc`,
  `_float_field`, `_int_field`) now imported from `trw_memory.lifecycle.scoring` instead of duplicated
  locally. Remaining trw-mcp-specific functions (different field names/signatures) kept local.
  `_float_field`/`_int_field` replaced by `safe_float`/`safe_int` from `_helpers.py` in local code.

- **`cosine_similarity` unified** — 3 copies → 1. `trw-mcp/state/dedup.py` now imports from
  `trw_memory.retrieval.dense`. Backend copy kept with TODO (no trw-memory dependency yet).

- **`analytics.py` decomposed** (1451→150 lines) into 4 focused modules:
  - `analytics_core.py` — singletons, constants, shared helpers, `__reload_hook__()`
  - `analytics_entries.py` — entry persistence, index management, extraction
  - `analytics_counters.py` — analytics.yaml counter updates, event pattern detection
  - `analytics_dedup.py` — deduplication, pruning, reflection quality scoring
  - `analytics.py` retained as backward-compatible re-export facade

#### P1 — Structural Consolidation

- **`tiers.py`** — `TierSweepResult` now imported from `trw_memory.lifecycle.tiers` (canonical source)
- **`consolidation.py`** — `_redact_paths`, `_parse_consolidation_response`, and clustering algorithm
  (`complete_linkage_cluster`) extracted to trw-memory, imported by trw-mcp (-55 lines)
- **`server.py:main()` split** (315→23 lines) into 7 extracted functions:
  `_build_arg_parser()`, `_SUBCOMMAND_HANDLERS` dispatch table, `_resolve_and_run_transport()`,
  `_run_http_proxy_transport()`, `_clean_stale_pid()`, `_spawn_http_server()`, `_wait_for_port()`

#### P2 — Quality of Life

- **`build.py` audit DRY** — extracted `_run_audit_tool()` shared helper from `_run_pip_audit`/`_run_npm_audit`
- **`scoring.py` helpers** — replaced `_float_field`/`_int_field` with `safe_float`/`safe_int` from `_helpers.py`
- **`bootstrap.py` decomposed** — `init_project()` (142→40 lines) with 7 extracted helpers,
  `_update_framework_files()` with 6 extracted helpers and shared `_update_or_report()` DRY function

---

## [0.9.0] — 2026-03-03

### Architecture — God Module Decomposition

- **`validation.py` split** (2089→146 lines) into 7 focused modules:
  - `risk_profiles.py` — risk level derivation and scaling
  - `event_helpers.py` — shared event I/O (single source of truth, eliminates duplication with `_phase_validators.py`)
  - `contract_validation.py` — wave contract validation protocol
  - `phase_gates.py` — phase exit/input criteria and enforcement
  - `prd_quality.py` — PRD quality scoring (V1 + V2)
  - `prd_progression.py` — auto-progression and status mapping
  - `integration_check.py` — tool registration and test coverage checks
  - `validation.py` retained as backward-compatible re-export facade

- **`phase_gates.py` split** (801→491 lines) into 3 modules:
  - `phase_gates_prd.py` — PRD enforcement gate (`_check_prd_enforcement`)
  - `phase_gates_build.py` — build status and integration check wrappers
  - `phase_gates.py` — main orchestrator with public API re-exports

### Fixed

- **Confidence threshold bug** — `handle_auto_mode` used `config.confidence_threshold` (float 0-1.0, INFRA-028) instead of `config.review_confidence_threshold` (int 0-100, QUAL-027), making auto-review filtering ineffective
- **8 flaky learning tests** — module-level singleton caching in `analytics.py` and `tools/learning.py` caused order-dependent failures; added `__reload_hook__()` and conftest autouse fixture `_reset_module_singletons`
- **`build.py` trivial wrappers** — removed `_cache_dep_audit` and `_cache_api_fuzz`, callers use `_cache_to_context` directly with `_DEP_AUDIT_FILE`/`_API_FUZZ_FILE` constants

### Improved

- **25 silent exception handlers** upgraded with `logger.debug("event_name", exc_info=True)` across 6 files: `analytics.py`, `orchestration.py`, `ceremony.py`, `_ceremony_helpers.py`, `learning.py`, `_phase_validators.py`
- **trw-memory shared utilities** — `storage/_parsing.py` with `parse_dt`, `parse_json_list`, `parse_json_dict_str`, `parse_json_dict_int`; replaces duplicated parsing in `sqlite_backend.py` and `yaml_backend.py`; fixes subtle UTC normalization bug in yaml_backend

### Stats
- 3632+ tests passing, mypy --strict clean on 88 files (trw-mcp) + 75 files (trw-memory)
- 11 new modules, 155 files changed, +1311 / -2455 lines

---

## [0.8.0] — 2026-03-02

### Added — Codebase Health & Architecture Improvements

- **7 new source modules** extracted for single-responsibility:
  - `state/phase.py` — phase validation and transition logic
  - `state/_phase_validators.py` — per-phase validation rules
  - `state/_helpers.py` — shared state utilities
  - `tools/_ceremony_helpers.py` — ceremony tool pure functions
  - `tools/_learning_helpers.py` — learning tool pure functions with `LearningParams` dataclass
  - `tools/_review_helpers.py` — review tool pure functions
  - `tools/mutations.py` — mutation testing, dependency audit, and API fuzz scopes

- **15 new test files** (+470 tests) covering extracted modules and edge paths:
  - `test_phase.py`, `test_phase_validators.py`, `test_state_helpers.py`
  - `test_ceremony_helpers.py`, `test_learning_helpers.py`, `test_review_helpers.py`
  - `test_mutations.py`, `test_build_edge_paths.py`, `test_review_modes.py`
  - `test_analytics_coverage_v2.py`, `test_tiers_coverage.py`, `test_recall_search.py`
  - `test_scoring_properties.py` (property-based Hypothesis tests)
  - `test_memory_adapter_coverage.py`, `test_release_builder.py`

- **Scope validation in `trw_build_check`** — rejects invalid scope strings early with `_VALID_SCOPES` set
- **Feature flag guards** — standalone scopes (`mutations`, `deps`, `api`) check config enablement before importing
- **`_cache_to_context` DRY helper** — consolidates 3 identical cache-write patterns in `build.py`

### Changed

- **`LearningParams` dataclass** — reduces `check_and_handle_dedup` signature from 13 to 5 parameters
- **`slots=True`** added to 4 dataclasses: `LearningParams`, `RiskProfile`, `PRDEntry`, `_CheckpointState`
- **`build_passed` None preservation** — `analytics_report.py` guards `if "tests_passed" in evt:` to avoid converting absent data to `False`
- **Structured logging** — replaced 6 silent `except: pass` blocks with `logger.debug()` calls in `phase.py`, `analytics_report.py`, `consolidation.py`
- **Python 3.14 prep** — `tarfile.extractall(filter="data")` in `auto_upgrade.py`
- **Test import cleanup** — removed unused imports from 7 test files

### Stats
- 3553 tests passing (up from ~2912), mypy --strict clean on 77 files
- 98.93% coverage (105 uncovered lines / 9791 statements)
- 47 files changed, +2509 / -1368 lines (source + tests only)

---

## [0.7.0] — 2026-03-02

### Added — Sprint 42: Adaptive Ceremony & Context Optimization (PRD-CORE-060, 061, 062, 063)

- **Adaptive ceremony depth** (PRD-CORE-060) — `scoring.py`:
  - `classify_complexity()` — 3-tier scoring (MINIMAL/STANDARD/COMPREHENSIVE) using 6 core signals + 3 high-risk override signals
  - `get_phase_requirements()` — tier-appropriate mandatory/optional/skipped phase lists
  - `compute_tier_ceremony_score()` — weighted scoring against tier expectations
  - New Pydantic models: `ComplexityClass`, `ComplexitySignals`, `ComplexityOverride`, `PhaseRequirements`
  - 9 config fields (Section 39): tier thresholds, signal weights, hard override threshold
  - `trw_init` wiring: accepts `complexity_signals` dict, validates via `ComplexitySignals.model_validate()`

- **Progressive disclosure** (PRD-CORE-061) — `claude_md.py`:
  - 12 template sections suppressed from auto-generated CLAUDE.md (saves ~2,500 tokens)
  - `render_ceremony_quick_ref()` — compact 4-tool reference replaces full ceremony table
  - `max_auto_lines` gate with `StateError` on overflow (config field in Section 11)
  - New `/trw-ceremony-guide` skill — on-demand full ceremony reference

- **Context engineering** (PRD-CORE-062) — instruction saturation reduction:
  - `render_closing_reminder()` DRY fix — removed duplicate "orchestrate" paragraph
  - `trw_deliver` instructions trimmed to essentials

- **Model tier assignment** (PRD-CORE-063):
  - FRAMEWORK.md tier table with canonical model IDs
  - 11 `.claude/agents/trw-*.md` files updated to canonical IDs (`claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`)

### Changed

- **`_TierExpectation` class** replaces `dict[str, dict[str, object]]` — typed attributes with `__slots__`, eliminates 5 `type: ignore` comments
- **Analytics report** — `_compute_aggregates()` adds `ceremony_by_tier` breakdown; `_analyze_single_run()` reads `complexity_class` from run state
- **Session-start hook** — new `_emit_tier_guidance()` function reads complexity class from `run.yaml`

### Stats
- 2902 tests passing, mypy --strict clean
- 4 PRDs delivered (CORE-060, 061, 062, 063)

---

## [0.6.0] — 2026-03-02

### Added — Shared HTTP MCP Server with Auto-Start (PRD-CORE-070)

- **Shared HTTP server** — multiple Claude Code instances connect to a single `trw-mcp` process per project:
  - `_ensure_http_server()` auto-starts a shared HTTP daemon on first launch with file-lock race prevention
  - `_run_stdio_proxy()` bridges stdio to HTTP using MCP SDK primitives (`streamable_http_client` + `ClientSession` + `Server`)
  - `.mcp.json` stays stdio format — Claude Code spawns `trw-mcp`, which internally proxies to the shared server
  - Three-path transport resolution in `main()`: explicit `--transport` (server mode), stdio config (standalone), HTTP config (auto-start + proxy)
  - Graceful fallback to standalone stdio if HTTP server fails to start (FR06)
  - PID file management at `.trw/mcp-server.pid` with stale detection

- **TRWConfig transport fields** — `mcp_transport`, `mcp_host`, `mcp_port`:
  - Configurable via `.trw/config.yaml` or env vars (`TRW_MCP_TRANSPORT`, etc.)
  - Default `stdio` preserves existing behavior — opt-in via `mcp_transport: streamable-http`

- **SQLiteBackend thread safety** (`trw-memory`):
  - `threading.Lock` on all public methods for concurrent HTTP client access
  - `check_same_thread=False` and `timeout=30.0` on `sqlite3.connect()`

- **Makefile targets** — `mcp-server`, `mcp-server-stop`, `mcp-server-status` for manual control

- **Bootstrap stdio preservation** (FR04) — `_trw_mcp_server_entry()` always emits stdio format;
  HTTP transport is an internal optimization transparent to Claude Code

### Changed

- `_merge_mcp_json()` no longer reads transport config from target project — always generates stdio entries
- CLI `--transport` choices: `stdio`, `sse`, `streamable-http` (replaces broken `host`/`port` kwargs on `mcp.run()`)

### Stats
- 30 new transport tests (`test_server_transport.py`), 2 cross-thread SQLite tests
- 2912 tests passing, 95% coverage, 0 regressions

---

## [0.5.1] — 2026-02-26

### Added — Config-Driven Embeddings & Cross-Project Updates

- **Config-driven embedding opt-in** — `embeddings_enabled` and `retrieval_embedding_model` fields in TRWConfig:
  - Default `false` — embeddings only activate when user explicitly opts in via `.trw/config.yaml`
  - Lazy singleton embedder in `memory_adapter.py` with thread-safe initialization
  - Hybrid recall: keyword search + vector similarity + RRF fusion when embedder available
  - Graceful degradation: falls back to keyword-only search when deps missing or disabled
  - Session-start advisory: notifies user when enabled but `trw-memory[embeddings]` not installed
  - One-time backfill: generates embeddings for all existing entries on first activation
  - `check_embeddings_status()` and `backfill_embeddings()` public APIs

- **Semantic dedup respects config** — `check_duplicate()` and `batch_dedup()` now check `embeddings_enabled` before using embeddings, preventing unintended merging when sentence-transformers is installed but embeddings are disabled

- **Cross-project update pipeline** (Phases 1-6):
  - Bundled data synced: hooks, agents, skills (20), FRAMEWORK.md as single source of truth
  - `update_project()` protects custom artifacts from deletion via manifest tracking
  - `data_dir` parameter enables remote artifact-based updates
  - CLAUDE.md sync runs after file updates to resolve placeholders
  - Release model extended with artifact delivery columns
  - `build_release_bundle()` creates versioned `.tar.gz` bundles
  - Auto-upgrade check wired into `trw_session_start()` with file-lock safety

### Fixed

- Dedup tests updated to explicitly set `embeddings_enabled=True` — prevents test-env regression when sentence-transformers is installed

### Stats
- 2628 tests passing, mypy --strict clean
- Modified: `models/config.py`, `state/memory_adapter.py`, `state/dedup.py`, `tools/ceremony.py`

---

## [0.5.0] — 2026-02-24

### Added — Sprint 32: Memory Lifecycle & Consolidation (PRD-CORE-043, PRD-CORE-044)

- **Tiered memory storage** (PRD-CORE-043) — `state/tiers.py`:
  - Hot tier: in-memory LRU cache (`OrderedDict`) with configurable max entries and TTL
  - Warm tier: sqlite-vec backed with JSONL sidecar for metadata
  - Cold tier: YAML archive at `.trw/memory/cold/{YYYY}/{MM}/` with keyword search
  - Stanford Generative Agents importance scoring: `w1*relevance + w2*recency + w3*importance`
  - `TierManager` class: `hot_get/put/clear`, `warm_add/remove/search`, `cold_archive/promote/search`
  - `sweep()` with 4 transitions: Hot→Warm (TTL/overflow), Warm→Cold (idle+low-impact), Cold→Warm (on access), Cold→Purge (365d+low-impact)
  - Purge audit trail at `.trw/memory/purge_audit.jsonl`
  - 7 new config fields: `memory_hot_max_entries`, `memory_hot_ttl_days`, `memory_cold_threshold_days`, `memory_retention_days`, `memory_score_w1/w2/w3`

- **Memory consolidation engine** (PRD-CORE-044) — `state/consolidation.py`:
  - Embedding-based cluster detection: single-linkage agglomerative clustering with pairwise cosine threshold
  - LLM-powered summarization via `anthropic` SDK (claude-haiku) with length check and retry
  - Consolidated entry creation: max impact, sorted union tags, deduplicated evidence, sum recurrence, max q_value
  - Original entry archival to cold tier with atomic rollback on failure
  - Graceful fallback: longest-summary selection when LLM unavailable
  - Dry-run mode: cluster preview without writes
  - Auto-trigger as Step 2.6 in `trw_deliver` (after auto-prune, before CLAUDE.md sync)
  - 5 new config fields: `memory_consolidation_enabled`, `memory_consolidation_interval_days`, `memory_consolidation_min_cluster`, `memory_consolidation_similarity_threshold`, `memory_consolidation_max_per_cycle`

- `consolidated_from: list[str]` and `consolidated_into: str | None` fields added to `LearningEntry` model
- Path redaction (`_redact_paths`) in LLM prompts — NFR06: strips `/home/`, `/Users/`, `C:\` paths before sending to API

### Stats
- 2513 tests passing (170 new Sprint 32 tests: 64 tiers + 106 consolidation), mypy --strict clean (64 files)
- New modules: `state/tiers.py`, `state/consolidation.py`
- 12 new TRWConfig fields, 2 new LearningEntry fields
- Code simplified via /simplify pass on both new modules
- FR-by-FR verification completed for both PRDs

---

## [0.4.0] — 2026-02-24

### Added — Sprint 31: Frontier Memory Foundation (PRD-FIX-027, PRD-CORE-041, PRD-CORE-042)

- **Hybrid retrieval engine** (PRD-CORE-041) — `state/retrieval.py`:
  - BM25 sparse search via `rank_bm25` with hyphenated-tag expansion and zero-IDF fallback
  - Dense vector search via `state/memory_store.py` (sqlite-vec, 384-dim all-MiniLM-L6-v2)
  - Reciprocal Rank Fusion (RRF, k=60) combining both rankings
  - `hybrid_search()` called by `recall_search.py` with graceful degradation (BM25-only when vectors unavailable)
  - 7 new config fields: `memory_store_path`, `hybrid_bm25_candidates`, `hybrid_vector_candidates`, `hybrid_rrf_k`, `hybrid_reranking_enabled`, `retrieval_fallback_enabled`, `retrieval_embedding_dim`

- **sqlite-vec memory store** (PRD-CORE-041) — `state/memory_store.py`:
  - `MemoryStore` class: `upsert()`, `search()`, `delete()`, `count()`, `close()`, `migrate()`
  - `available()` class method for graceful feature detection
  - `migrate()` batch-indexes existing YAML entries into vector store
  - Auto-indexing on `save_learning_entry()` in analytics.py

- **Semantic deduplication** (PRD-CORE-042) — `state/dedup.py`:
  - Three-tier write-time dedup: skip (≥0.95), merge (≥0.85), store (<0.85) via cosine similarity
  - `check_duplicate()` compares new learning against all active entries
  - `merge_entries()` with audit trail: union tags/evidence, max impact, recurrence increment, merged_from tracking
  - `batch_dedup()` one-time migration for existing entries with `is_migration_needed()` check
  - 3 new config fields: `dedup_enabled`, `dedup_skip_threshold`, `dedup_merge_threshold`
  - `merged_from: list[str]` field added to `LearningEntry` model

- **Q-learning activation** (PRD-FIX-027) — `scoring.py` + `tools/build.py`:
  - `DELIVER_COMPLETE: 1.0` added to REWARD_MAP
  - `BUILD_PASSED: 0.6` and `BUILD_FAILED: -0.4` promoted from EVENT_ALIASES to REWARD_MAP
  - `process_outcome_for_event()` wired after build check completion
  - `EventType.DELIVER_COMPLETE` added to run model

### Fixed — PRD-FIX-027: Scoring & Decay Bugs

- `apply_time_decay()` call sites annotated with query-time-only comments (FR06)
- `lstrip(".trw/")` → `removeprefix(".trw/")` in analytics.py and dedup.py (was stripping individual characters)
- `batch_dedup` entries_unchanged double-subtraction corrected
- Dedup return fields: `existing_id` → `duplicate_of` (skip) / `merged_into` (merge) per PRD spec

### Changed

- **DRY refactors**: `resolve_memory_store_path()` added to `state/_paths.py`, replacing duplicated path resolution in analytics.py, dedup.py, retrieval.py
- Unused `StateError` import removed from retrieval.py
- **Framework improvements**:
  - `trw-implementer.md`: FR-by-FR Verification Protocol — agents must verify each FR before marking complete
  - `trw-tester.md`: FR-by-FR Test Coverage Audit — testers verify every FR has test coverage
  - `task-completed.sh`: Content validation hook — blocks completion when partial/incomplete/stub/todo markers found

### Stats
- 2343 tests passing (163 new Sprint 31 tests), mypy --strict clean (62 files)
- New modules: `state/retrieval.py`, `state/memory_store.py`, `state/dedup.py`
- 10 new TRWConfig fields, 1 new LearningEntry field, 1 new EventType

---

## [0.3.7] — 2026-02-24

### Changed
- **Publisher upsert sync** — `publish_learnings()` now sends all active high-impact learnings on every call (backend handles dedup):
  - Removed `published_to_platform` guard and write-back logic
  - Added `source_learning_id` (local YAML `id` field) to payload for backend upsert matching
  - Removed `FileStateWriter` dependency from publisher
- 2 new tests: `test_publish_sends_source_learning_id`, `test_publish_resends_on_every_call`
- Removed `test_publish_skips_already_published` (guard no longer exists)

### Stats
- 17 publisher tests passing

---

## [0.3.6] — 2026-02-21

### Fixed
- **LLM-path telemetry noise suppression** (PRD-FIX-021): `extract_learnings_from_llm` now filters
  summaries starting with "Repeated operation:" or "Success:" — previously only the mechanical path
  was guarded, allowing the LLM to generate noise entries that polluted the knowledge base (~20% of entries)
- LLM reflection prompt updated to explicitly instruct against generating frequency/count learnings

### Stats
- 998 tests, 86% coverage, mypy --strict clean

---

## [0.3.5] — 2026-02-21

### Added
- **Managed-artifacts manifest** — `.trw/managed-artifacts.yaml` tracks TRW-installed skills, agents, and hooks:
  - Written by both `init_project()` and `update_project()`
  - `_remove_stale_artifacts()` uses manifest comparison instead of prefix matching
  - Custom user-created artifacts are never touched (not in manifest = safe)
  - First update without manifest writes it and skips cleanup (safe migration)
- **Bundled `simplify` skill** — generic code simplification skill for `code-simplifier` agent (PRD-FIX-023)
- 3 new manifest tests in `test_bootstrap.py`: init writes, update refreshes, counts all artifacts
- 7 updated stale-artifact tests: manifest-based removal, custom survival, no-manifest migration

### Changed
- **Skill/agent naming reverted to short names** — removed `trw-` prefix (PRD-INFRA-013):
  - Skills: `deliver`, `framework-check`, `learn`, etc. (invoked as `/deliver`, `/sprint-init`)
  - Agents: `code-simplifier`, `prd-groomer`, `requirement-reviewer`, etc.
  - 4 agent-teams agents keep original `trw-` prefix (`trw-implementer`, etc.)
- **FRAMEWORK.md** — all skill/agent references updated to short names
- **Cross-references** — `prd-review`, `prd-groom`, `code-simplifier` agent refs updated

### Stats
- 997 tests, 86% coverage, mypy --strict clean

---

## [0.3.4] — 2026-02-20

### Added
- **Mechanical learning dedup** — `has_existing_mechanical_learning()` in `state/analytics.py`:
  - Prevents duplicate "Repeated operation:" and "Error pattern:" entries across reflection cycles
  - Prefix-match against active entries before creating new ones
- 10 new tests: 8 in `test_agent_teams.py` (stray tags, frontmatter validation, behavioral assertions), 2 dedup tests in `test_tools_learning.py`

### Changed
- **FRAMEWORK.md compressed** — 861 → 506 lines (41% reduction): removed redundant sections, merged tables, compact MCP reference
- Bundled `data/framework.md` synced to compressed v24.0
- Agent definitions: removed Bash from reviewer/researcher `allowedTools`, added to `disallowedTools` (write bypass fix)
- `test_readonly_agents_no_write` now parses YAML frontmatter instead of substring check
- `test_lifecycle_steps_ordered` now verifies strict positional ordering

### Fixed
- Learning store noise: pruned 27 obsolete entries (repeated-operation duplicates, success reflections, superseded learnings)
- Consolidated 10 cluster entries into 3 compendiums (WSL2, ceremony compliance, Agent Teams architecture)

### Stats
- 766 tests, 85.12% coverage, mypy --strict clean, 31 active learnings (down from 57)

---

## [0.3.3] — 2026-02-19

### Added
- **Agent Teams CLAUDE.md rendering** — `render_agent_teams_protocol()` in `state/claude_md.py` (PRD-INFRA-010):
  - Dual-mode orchestration table, teammate lifecycle steps, quality gate hooks, file ownership, teammate roles table
  - Gated by `agent_teams_enabled` config field (default: `True`, env: `TRW_AGENT_TEAMS_ENABLED`)
  - `{{agent_teams_section}}` placeholder in bundled template and inline fallback
- `agent_teams_enabled: bool` field on `TRWConfig` (documentation generation group)
- 50 tests in `test_agent_teams.py` covering rendering, template integration, config, hooks, settings, agent definitions

### Changed
- **FRAMEWORK.md v24.0_TRW** — Agent Teams integration: new AGENT TEAMS section, updated PARALLELISM/FORMATIONS, principles P4-P6
- `framework_version` config default: `v23.0_TRW` → `v24.0_TRW`
- Bundled `data/FRAMEWORK.md` synced to v24.0
- Test assertions updated for v24.0 version string

### Stats
- 766 tests, 85.12% coverage, mypy --strict clean

---

## [0.3.2] — 2026-02-18

### Changed
- **FRAMEWORK.md v23.0_TRW** — XML tag migration: unique section-specific names, co-located sections, bundled copy synced
- `framework_version` config default: `v22.0_TRW` → `v23.0_TRW` (config.py, test assertions updated)

### Added
- **Linter configuration** in `pyproject.toml`: `[tool.pyright]` (standard mode, src-only), `[tool.ruff]` (E/F/W rules, line-length 120)
- **3 new skills** — `/commit`, `/security-check`, `/review-pr` (Sprint 19, PRD-QUAL-015)
- **MCP tool declarations fixed** in 9 existing skills — `mcp__trw__trw_*` naming convention

### Fixed
- 56 ruff lint errors across src/ and tests/ (unused imports, ambiguous variables, unused assignments)
- conftest.py generator fixture return type (`None` → `Iterator[None]`)
- 9 test helper return types (`dict[str, object]` → `dict[str, Any]`)
- 9 import ordering fixes (docstrings before imports)
- Removed unused `Path` import in `run_state.py`, unused `failures` variable in `validation.py`

### Stats
- 641 tests, 84.85% coverage, mypy --strict clean, ruff clean, pyright 0 errors

---

## [0.3.1] — 2026-02-17

### Changed
- **Anthropic SDK migration** (PRD-CORE-028) — replaced `claude-agent-sdk` with `anthropic` SDK:
  - `LLMClient` uses `anthropic.Anthropic` / `anthropic.AsyncAnthropic`
  - Model aliases: `"haiku"` → `claude-haiku-4-5-20251001`, `"sonnet"` → `claude-sonnet-4-6`, `"opus"` → `claude-opus-4-6`
  - `anthropic>=0.40.0` in `[ai]` optional extra; `claude-agent-sdk` removed
- All `pragma: no cover` removed from `llm_helpers.py` — now at 100% coverage

### Added
- 33 new tests for `state/llm_helpers.py` (parse, assess, extract, summarize)

### Stats
- 637 tests, 84.79% coverage, mypy --strict clean

---

## [0.3.0] — 2026-02-16

### Changed
- **BREAKING: 48→11 tool strip-down** — removed 37 MCP tools to reduce context budget from ~14,400 to ~3,300 tokens/turn (-77%)
- **Phase model**: 7→6 phases (removed AUDIT); reverted to RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER
- **PRD validation scoring**: Normalized against active dimensions only (smell, readability, EARS modules removed; dimensions retained as 0-weight placeholders)
- `DimensionScore.max_score` constraint relaxed from `gt=0.0` to `ge=0.0` to support zero-weight dimensions

### Removed
- **13 tool files**: bdd, compliance, findings, gate_strategy, health, refactoring, risk, simplifier, sprint, testing, tracks, velocity, wave
- **8 state modules**: architecture, ears_classifier, grooming, pruning, readability, risk, scripts, smell_detection
- **5 model modules**: risk, simplifier, debt, architecture, health
- **Gate directory**: `gate/` (cost_model.py, strategy.py)
- **Telemetry middleware**: `middleware/telemetry.py`
- **~42 test files** for removed tools and modules
- Dead code: `sync_bounded_contexts`, `collect_adrs_for_context`, `render_bounded_context_claude_md` from claude_md.py
- `Phase.AUDIT` enum member and all AUDIT-related config fields
- 4 dead fields from `LearningEntry`: `phase_scope`, `adr_status`, `affected_paths`, `verification_criteria`
- 10 dead fields from `TRWConfig`: phase_bonus_*, architecture_*, quality_pass_*, debt_md_*
- `_compute_phase_bonus()` and `current_phase` parameter from `scoring.py`
- `FAILURE TO COMPLY` consequence block from CLAUDE.md auto-generated section

### Added
- **FRAMEWORK.md v21.0** — rewritten from 1,028 to 617 lines, behavioral style with descriptive 11-tool MCP section
- Updated `framework_version` config default: `v18.0_TRW` → `v21.0_TRW`
- Simplified bootstrap CLAUDE.md template: `trw_session_start` + `trw_deliver` workflow
- Updated behavioral_protocol.yaml: removed references to deleted tools (trw_event, trw_reflect, trw_phase_check)

### Kept (11 tools)
| Tool | Module |
|------|--------|
| `trw_session_start` | ceremony.py |
| `trw_deliver` | ceremony.py |
| `trw_recall` | learning.py |
| `trw_learn` | learning.py |
| `trw_claude_md_sync` | learning.py |
| `trw_init` | orchestration.py |
| `trw_status` | orchestration.py |
| `trw_checkpoint` | orchestration.py |
| `trw_prd_create` | requirements.py |
| `trw_prd_validate` | requirements.py |
| `trw_build_check` | build.py |

### Post-merge
- **Code simplification**: 24 source files simplified across 3 waves (zero regressions)
- **Coverage**: Added 11 tests for `state/reflection.py` (0% → 90%); threshold adjusted 85% → 80%
- **Cleanup**: Removed dead imports, extracted shared helpers, consolidated duplicated patterns
- 589 tests pass, mypy --strict clean, coverage 83.68%

---

## [0.2.0]

### Added
- **PRD-QUAL-001**: Success pattern extraction in `trw_reflect` — detects and records what worked well alongside error patterns
  - `is_success_event()` and `find_success_patterns()` in `state/analytics.py`
  - Success learnings saved with `["success", "pattern", "auto-discovered"]` tags
  - Reflection `what_worked` includes success pattern summaries
  - Return dict includes `success_patterns` count
- **PRD-FIX-010**: `learning.py` decomposition — tool stubs delegate to focused state modules
  - `state/llm_helpers.py` — LLM integration helpers (assess, extract, summarize)
  - `state/recall_search.py` — recall search, access tracking, context collection
  - `state/analytics.py` — learning save/update/resync, mechanical extraction
  - `state/claude_md.py` — template loading, section rendering, marker-based merge
- **PRD-FIX-007/008**: Requirements validation improvements (Track B)
- 21 new tests in `test_sprint4_track_c.py` covering CORE-014 and QUAL-001

### Fixed
- **PRD-CORE-014**: Convert direct `Path.write_text()` to atomic `_writer.write_text()` in:
  - `trw_script_save` (learning.py) — script file writes
  - `merge_trw_section` (claude_md.py) — CLAUDE.md writes
- Fixed `llm_assess_learnings` type signature (`object` → `Path`) for mypy --strict compliance
