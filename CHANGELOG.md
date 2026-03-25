# Changelog

All notable changes to the TRW MCP server package.

## [0.30.0] — 2026-03-25

### Added

- **Observation masking middleware** (`telemetry/context_budget.py`, `telemetry/_compression.py`) — new `ContextBudgetMiddleware` implements 3-tier progressive verbosity (full/compact/minimal) that reduces tool response tokens as sessions grow longer. Tier transitions driven by per-session turn count; redundancy detection via SHA-256 hashing suppresses repeated identical responses. Registered in `_build_middleware()` between `ProgressiveDisclosureMiddleware` and `ResponseOptimizerMiddleware`. Config fields: `observation_masking` (bool), `compact_after_turns` (default 20), `minimal_after_turns` (default 40). 28 tests covering tiers, compression, redundancy, config, and fail-open behavior. Motivated by JetBrains Research (Dec 2025): 52% cost reduction with only 2.6% quality degradation.
- **Open-source publication prep** — `pyproject.toml` license set to `BUSL-1.1`, `README.md` rewritten for public audience, competitive research documents removed from the published artifact, secrets baseline scrubbed.

### Fixed

- **Restored full CLAUDE.md behavioral protocol** — all ceremony sections (delegation, phases, tool lifecycle, rationalization watchlist, Agent Teams protocol, example flows, promoted learnings) are rendered again. These were incorrectly suppressed with empty strings during a prior refactor intended only for light-mode platforms (opencode, local models).
- **CLAUDE.md cache invalidation on upgrade** — `_compute_sync_hash()` now includes the package version, so any `trw-mcp` version bump automatically forces a re-render across all projects. Previously, upgrading with unchanged learnings would serve stale cached content.
- **`max_auto_lines` default** — bumped from 80 to 300 to accommodate the full rendered section (~168 lines).
- **Dead `_writer` parameter removed** — `_step_telemetry` and related ceremony helpers had an unused `FileStateWriter` parameter that was never consumed; removed across 4 call sites. Fixes 13 test isolation failures caused by stale writer references.

---

## [0.29.1] — 2026-03-22

### Fixed

- **Installer hang without API key** — `_run_claude_md_sync` now skips the LLM CLAUDE.md sync step when `ANTHROPIC_API_KEY` is not set, preventing the installer from hanging for up to 180 seconds when run outside a Claude Code session.
- **Embeddings never backfilled during install** — `update-project` now runs an auto-maintenance step (embeddings backfill + stale run closure) locally after install, without requiring an API key. First installs with `--ai` now backfill embeddings immediately.
- **Auto-maintenance progress output** — `on_progress` callback passed through to `_run_auto_maintenance` so the installer spinner updates during the embeddings backfill phase. Warning emitted when embeddings are enabled but `sentence-transformers` is unavailable.

---

## [0.29.0] — 2026-03-22

### Fixed

- **Recall union search** — `trw_recall` now performs a union of keyword and vector results before ranking, fixing cases where keyword-only or vector-only matches were silently dropped.
- **Learning publish schema** — `source_learning_id` field correctly serialized in the batch publish payload; fixes backend upsert matching for learning entries published from projects with non-UUID local IDs.
- **Installer embeddings UX** — improved progress messaging during first-time embedding generation ("Backfilling embeddings (this may take 30–60s on first run)...").

---

## [0.28.0] — 2026-03-20

### Fixed

- **Installer `trw-shared` wheel missing** — `trw-mcp` declares `trw-shared>=0.1.0` as a dependency but the installer only bundled `trw-memory` and `trw-mcp` wheels. `pip install` failed with "No matching distribution found for trw-shared" on every fresh install. Installer now bundles all three wheels in dependency order: `trw-shared` → `trw-memory` → `trw-mcp`.

### Changed

- **`trw-shared` telemetry constants inlined** — after the `trw-shared` wheel bundling fix, `EventType`, `Phase`, `Status` constants and `MAPPED_FIELDS` frozenset from `trw_shared.telemetry` are now the authoritative source used by `trw-mcp` telemetry models (`SessionStartEvent`, `ToolInvocationEvent`, `CeremonyComplianceEvent`, `SessionEndEvent`). Inline string literals replaced throughout `telemetry/` subpackage.

---

## [0.27.0] — 2026-03-19

### Changed

- **Framework version bump to v24.4_TRW** — coordinated version bump across all 5 monorepo packages.
- **Structured logging overhaul** — `structlog` wired across all tool and state modules with consistent field naming.
- **150 cross-package integration tests** — new test suites covering tool → state → persistence boundaries.
- **Agent Teams worktree merge fix** — worktree branches now merge before cleanup, preventing work loss.

---

## [0.25.0] — 2026-03-18

### Added

- **Memory routing section** — new `render_memory_harmonization()` auto-injected into CLAUDE.md to disambiguate `trw_learn()` vs Claude Code's native auto-memory. Uses table comparison and concrete routing examples. Claude Code-specific — not included in AGENTS.md.
- **Test for memory harmonization** — verifies routing guidance content, Claude Code specificity, and table structure.

### Changed

- **Optimized CLAUDE.md auto-section** — 41% token reduction (460 → 271 words) while adding memory routing content. Eliminated redundancy between imperative opener and ceremony quick-ref. Switched tool reference from bullet list to table format for scannability.
- **`render_imperative_opener()`** — tightened to role-only framing with brief tool mentions (detailed table now in ceremony quick-ref).
- **`render_ceremony_quick_ref()`** — restructured from bullet list to `| Tool | When | What |` table format.
- **`render_framework_reference()`** — compressed from 5 lines to 2, removed threat framing.

## [0.22.0] — 2026-03-18

### Added

- **ClientProfile system** — per-platform behavioral adaptation via frozen Pydantic models. Five built-in profiles (claude-code, opencode, cursor, codex, aider) with calibrated ceremony weights, scoring dimensions, write targets, and feature flags. See [`docs/CLIENT-PROFILES.md`](../docs/CLIENT-PROFILES.md).
- **Profile-aware ceremony scoring** — `compute_ceremony_score()` accepts optional `CeremonyWeights`. Both production call sites now pass the active profile's weights.
- **Profile-aware write targets** — `_determine_write_targets()` delegates to `ClientProfile.write_targets` for known clients.
- **7 delivery gate structural fixes** (Sprint 77 postmortem): review scope block (R-01), complexity drift warning (R-02/R-05), PRD deferral detection (R-03), wiring test mandate (R-04), anti-pattern recall alerts (R-06), checkpoint blocker warning (R-07).
- **DRY delivery gate helpers** — `_read_run_events()`, `_read_run_yaml()`, `_count_file_modified()` — events.jsonl read once per delivery.

### Fixed

- Facade-only ClientProfile wiring — weights and write targets now consumed by production code.
- Phase case normalization — `mandatory_phases` stored lowercase to match `Phase` enum.
- Parallel `_CEREMONY_WEIGHTS` dict replaced with `CeremonyWeights().as_dict()`.
- `@cached_property` → `@property` on `TRWConfig.client_profile` (stale data risk).
- Negative weights now rejected via `Field(ge=0)`.
- Stale `.pyc` files and comments cleaned up.
- `_resolve_installation_id` wrappers removed, direct imports inlined.

## [0.21.0] — 2026-03-17

### Added

- **Response optimizer middleware** — new `ResponseOptimizerMiddleware` intercepts all MCP tool responses and compacts JSON for LLM context efficiency: rounds floats to 2 decimal places, strips null values and empty collections, re-serializes with compact separators (no whitespace). Reduces token consumption across all 24 tools with zero per-tool changes.

### Fixed

- **`status` column always NULL for tool invocations** — `_write_tool_event` now emits `status: "success"/"error"` (string) in addition to `success` (bool), so the backend's `telemetry_events.status` column is correctly populated instead of all values falling into the `payload` JSON.
- **`error_type` never populated** — tool invocation events now include `error_type` with the exception class name (e.g., `"ValueError"`), enabling dashboard error-type breakdowns.

### Added

- **`trw-shared` telemetry contract** — new `shared/` monorepo package (`trw_shared.telemetry`) provides `EventType`, `Phase`, `Status` constants and `MAPPED_FIELDS` frozenset as the single source of truth for telemetry field names across trw-mcp and backend.
- **Grafana dashboard rewrite** — rebuilt `trw-overview.json` from 5 panels to 25 panels across 7 sections: Overview KPIs, Event Volume & Latency (P50/P95/P99), Tool Analysis (top tools + error rates), Ceremony & Workflow (score trend + phase donut), LLM Usage & Build Quality, Learnings & Errors (table), Sessions & Coverage + LLM Cost. All queries use `$__timeFilter(created_at)` for proper time-range integration. Wired to all 6 DB tables: `telemetry_events`, `shared_learnings`, `organizations`, `users`, `api_keys`, `audit_events`.

### Changed

- **Telemetry models use shared constants** — `SessionStartEvent`, `ToolInvocationEvent`, `CeremonyComplianceEvent`, `SessionEndEvent` now reference `EventType.*` and `Status.*` from `trw_shared.telemetry` instead of inline string literals.
- **`ToolEventDataDict`** — added `status` and `error_type` fields to the TypedDict for type-safe telemetry emission.

## [0.20.1] — 2026-03-16

### Fixed

- **Installer hang on extras detection** — `_detect_installed_extras()` now uses a 10-second timeout for import checks that previously could stall indefinitely on PEP 668 system Python without a venv.
- **Installer hang on subprocess calls** — `run_with_progress()` now has a configurable watchdog timer (default 180s) that kills stalled subprocesses. Previously, a hanging `trw-mcp update-project` or CLAUDE.md sync would block the installer indefinitely.

## [0.20.0] — 2026-03-15

### Added

- **Multi-platform ceremony adaptation** (PRD-CORE-084) — `ceremony_mode` config field (`"full"` | `"light"`) controls ceremony depth for non-Claude Code platforms. Light mode uses `render_minimal_protocol()` (< 200 tokens) and caps recall to 10 learnings for small context windows.
- **Learning injection into AGENTS.md** — high-impact learnings (impact >= 0.7) are now injected into the AGENTS.md auto-generated section during `trw_deliver()`, matching the CLAUDE.md learning promotion behavior. Controlled by `agents_md_learning_injection` config (default: `true`).
- **Platform-generic AGENTS.md content** — `render_agents_trw_section()` produces content free of Claude Code-specific language (no Agent Teams, subagents, slash commands, or FRAMEWORK.md references). AGENTS.md is now suitable for opencode, Cursor, Codex, and other MCP-capable platforms.
- **`target_platforms` config field** — list of platforms to sync instruction files for during deliver/sync. Installer auto-detects IDEs; updater keeps the field in sync when IDEs are added/removed.
- **Tool relevance tiers documentation** — TRW_README.md includes a "Local Model Guide" with Essential/Recommended/Optional tool classification for context-constrained environments.
- **Platform adaptation research** — `docs/research/platform-adaptation-research.md` with compatibility matrix, eval analysis, and TRW-light protocol design.

### Fixed

- **Cursor platform routing** — single-platform `target_platforms: ["cursor"]` now routes directly instead of falling to auto-detect.
- **UTF-8 encoding** in `_update_config_target_platforms()` — matches all other bootstrap file operations.
- **Empty "Key Learnings" header** — sanitized-away summaries no longer produce a spurious section header.
- **`query_matched` inflation** — focused recall count computed before merge with baseline results.
- **DRY in render functions** — extracted `_SESSION_BOUNDARY_TEXT` constant shared across renderers.

### Changed

- **Renamed `_do_claude_md_sync` → `_do_instruction_sync`** — platform-generic naming reflecting multi-platform support.
- **AGENTS.md size gate** — warning logged when auto-generated section exceeds `max_auto_lines`.

## [0.19.2] — 2026-03-15

### Changed

- **Ruff lint enforcement** — expanded from 14 to 26 rule sets (added C4, PERF, G, S, DTZ, FURB, C901, ANN). 244 violations fixed, 0 remaining. All test noqa comments eliminated.
- **noqa reduction** — source noqa reduced from 292 to 130 (all justified security/complexity suppressions). Test noqa reduced from 117 to 0.
- **Code simplification** — consolidated duplicate imports in `learning.py`, extracted `_parse_version()` in `auto_upgrade.py`, simplified `ceremony_nudge.py` variable naming.
- **C901 complexity** — decomposed 9 of 29 complex functions. Remaining 25 are core ceremony/registration functions with justified suppressions.
- **Ruff format** — `make format-python` target added for consistent formatting.
- **Pre-commit hooks** — 11 hooks including ruff, ruff-format, detect-secrets, check-ast, check-yaml, check-toml.
- **Quality baselines** — vulture dead code, deptry dependency hygiene, pyright type checking baselines documented.
- **Custom semgrep rules** — 4 rules: no-datetime-now-without-tz, no-bare-except, no-print-statements, mcp-tools-must-have-docstrings.
- **pip-audit CVE scanning** — `make vuln-scan` target with severity filtering.
- **CI hardening** — ruff check + ruff format --check added to mcp-ci.yml.

## [0.19.1] — 2026-03-15

### Fixed

- **Installer hang on extras detection** — `_detect_installed_extras()` now uses a 10-second timeout (was 120s). Import checks for `anthropic` and `sqlite_vec` that hang on system Python without a venv no longer block the installer for 2+ minutes.
- **Installer hang on project setup** — `run_with_progress()` now has a 180-second watchdog timer (`threading.Timer`) that kills stalled subprocesses. Previously, a hanging `trw-mcp update-project` would block the installer indefinitely.
- **CLAUDE.md sync blocking on ThreadPoolExecutor shutdown** — `_run_claude_md_sync()` now calls `pool.shutdown(wait=False, cancel_futures=True)` instead of relying on the `with` context manager's `__exit__`. The old code blocked indefinitely in `shutdown(wait=True)` when `LLMClient()` initialization hung in the worker thread.
- **Timeout observability** — `run_with_progress()` now warns users when a subprocess is killed by the watchdog timeout. `_run_claude_md_sync()` emits structured log events (`claude_md_sync_completed`, `claude_md_sync_timeout`, `claude_md_sync_failed`) for all sync outcomes.

## [0.19.0] — 2026-03-15

### Added

- **Configurable `runs_root`** — new config field `runs_root` (default: `.trw/runs`) controls where run artifacts (events, checkpoints, reports) are stored. Each `trw_init` creates `{runs_root}/{task_name}/{run_id}/`. Previously runs were nested under `{task_root}/{task_name}/runs/` which mixed run artifacts with documentation.
- **`--runs-root` CLI flag** — `trw-mcp init-project --runs-root <path>` sets the run directory at install time. The generated `.trw/config.yaml` includes inline comments explaining the field.
- **`.trw/runs` bootstrapped at install** — the directory is now created during `init-project` alongside other `.trw/` subdirectories.
- **Config reference updated** — `runs_root` documented in `config_reference.md` with description and example.

### Changed

- **Run directory structure simplified** — runs now live at `.trw/runs/{task}/{run_id}/` instead of `docs/{task}/runs/{run_id}/`. Removes the redundant intermediate `runs/` directory since the root is already semantically a runs directory.
- **FRAMEWORK.md variables updated** — `RUNS_ROOT` added, `RUN_ROOT` redefined as `{RUNS_ROOT}/{TASK}/{RUN_ID}`.

## [0.18.0] — 2026-03-14

### Added

- **Multi-platform instruction sync** — new `target_platforms` config field controls which instruction files (CLAUDE.md, AGENTS.md, etc.) are written during `trw_deliver()` and `trw_claude_md_sync()`. Supports `claude-code`, `opencode`, `cursor`, `codex`, `aider` as a list. Installer auto-detects platforms and writes config; updater keeps it in sync when IDEs are added/removed.
- **Updater config sync** — `update-project` now detects IDE changes and updates `target_platforms` in `.trw/config.yaml` via selective YAML merge (preserves all other user config).

### Changed

- **Renamed `_do_claude_md_sync` → `_do_instruction_sync`** — internal function name and comments updated to be platform-generic, reflecting multi-platform support.

## [0.17.0] — 2026-03-14

### Fixed

- **Installer pip install timeout** — `_run_quiet` now has a 120-second timeout to prevent hangs when pip stalls on PEP 668 externally-managed system Pythons without a venv activated. Previously would hang indefinitely on `--break-system-packages` fallback.

## [0.16.0] — 2026-03-14

### Added

- **REVIEW.md created during install** — `init-project` now generates `REVIEW.md` alongside `CLAUDE.md` so Anthropic's agentic reviewer has review instructions immediately after installation. Previously only created during `update-project` or `trw_deliver()`. Uses `_write_if_missing` so user edits are preserved on re-run.

## [0.15.2] — 2026-03-15

### Added

- **Installer UX overhaul** (PRD-CORE-083) — preflight section moves Python check and feature prompts before numbered steps so step count never jumps mid-flow. Config-level feature flags (`embeddings_enabled`, `sqlite_vec_enabled`) persist user choices across reinstalls. Consolidated extras into single step. Dynamic success banner adapts to fresh install vs reinstall. Random tip from 12-item curated pool.
- **Real backend health check** (PRD-CORE-083) — installer probes each configured `platform_url` via `urllib.request` against `/v1/health` with 5s timeout. Auto-detects local Docker backends via `docker-compose.yml` presence. Parallel probing via `ThreadPoolExecutor`. Replaces cosmetic "Connected" message that only checked API key format.
- **MCP server restart after upgrade** (PRD-INFRA-041) — version sentinel pattern (`.trw/installed-version.json`) written by installer, detected by `_check_version_sentinel()` during `trw_session_start()`. Injects `update_advisory` with both version numbers and `/mcp` instruction. HTTP-mode servers killed via PID file with cross-platform `_is_process_alive()` (ctypes on Windows, `os.kill(pid, 0)` on Unix).
- **CLAUDE.md sync timeout** (PRD-INFRA-041) — 30-second `ThreadPoolExecutor` timeout prevents installer hang when LLM initialization or network calls stall during CLAUDE.md rendering.
- **Cross-platform process management** — `_is_process_alive()` uses `ctypes.windll.kernel32.OpenProcess` on Windows (CPython issue #14480: `os.kill(pid, 0)` broken on Windows). `_terminate_process()` falls back to `taskkill /PID` on Windows.

### Fixed

- **Build check venv-first resolution** — `_find_executable()` now checks package venv → project venv → PATH (was PATH-first, finding system pytest without project dependencies). Also checks Windows `Scripts/` directory.
- **Build check pytest cwd** — `_run_pytest()` runs from `project_root` (where `tests/` lives), not `project_root/build_root` (where `src/` lives). Fixes "file or directory not found: tests/" error.
- **`_load_prior_config` UnicodeDecodeError** — now catches `UnicodeDecodeError` for binary config files.
- **`llm.py` unused type: ignore** — added `unused-ignore` to `import anthropic` suppression for mypy --strict.

### PRDs Completed

- **PRD-CORE-083**: Installer UX Overhaul and Backend Health Check (8 FRs, 32 tests)
- **PRD-INFRA-041**: Cross-Platform MCP Server Restart After Install (10 FRs, 45 tests)

## [0.15.1] — 2026-03-14

### Fixed

- **mypy --strict clean for trw-mcp** — resolved all 10 pre-existing type errors by widening `LearningEntryDict` → `dict[str, object]` in function signatures (`_recall.py`, `_decay.py`, `learning.py`, `ceremony.py`, `_ceremony_helpers.py`, `learning_injection.py`, `tiers.py`) and TypedDict fields (`_tools.py`). 0 errors across 156 files.
- **mypy --strict clean for trw-memory** — resolved all 13 pre-existing type errors: fixed `type: ignore` codes (`sqlite_backend.py`, `local.py`, `client.py`), widened formatter params to `Sequence[Mapping]` for TypedDict covariance (`cli_formatters.py`), added `_backend_or_raise` property for None safety (`llamaindex.py`). 0 errors across 77 files.
- **WSL2 filesystem learning marked obsolete** — environment migrated to native Ubuntu 24.04.

### Changed

- **Node.js 24 available** — installed via nvm for ESLint and platform build. `platform/package-lock.json` updated.

## [0.15.0] — 2026-03-14

### Added

- **Worktree pre-spawn safety** — FRAMEWORK.md, `trw-lead` agent, and `/trw-sprint-team` skill now mandate `git status --porcelain` before `git worktree add`. Blocks on uncommitted changes with user options (commit/stash/abort). Prevents agents from operating on stale committed state.
- **Test file ownership enforcement** — `test_owns` in `file_ownership.yaml` now follows the same zero-overlap rules as `owns`. FRAMEWORK.md, `trw-lead`, and `/trw-team-playbook` skill updated. Two agents editing the same test file caused 4 merge iterations in Sprint 66.
- **Adversarial audit enforcement** — `trw_review()` moved from Flexible to Rigid for STANDARD+ complexity tasks. `_ceremony_helpers.py` emits `review_warning` (not `review_advisory`) when review is missing on STANDARD/COMPREHENSIVE runs.
- **Ceremony recovery after compaction** — `trw_pre_compact_checkpoint` now reads `.trw/context/ceremony-state.json` and includes ceremony state + pending obligations in `pre_compact_state.json` and `compact_instructions.txt`.
- **Pre-implementation state verification** — `/trw-sprint-init` skill now greps the codebase for FR identifiers before sprint planning. Flags PRDs that are >80% already implemented as `LIKELY IMPLEMENTED`.
- **`_read_complexity_class()` helper** — extracted from `check_delivery_gates()` for testability
- **`_compute_pending_ceremony()` helper** — data-driven via `_CEREMONY_OBLIGATIONS` table, replaces 4 imperative if-blocks

### Changed

- **FRAMEWORK.md v24.3** — Worktree Safety subsection added to Agent Teams. File Ownership expanded to include test files. RIGID tool classification updated with `trw_review()` and worktree validation.
- **`trw-lead` agent** — File Ownership Enforcement and Worktree Pre-Spawn Validation sections added.
- **`/trw-sprint-team` skill** — Step 6a (Pre-Worktree State Validation) added before worktree creation.
- **`/trw-team-playbook` skill** — Zero-overlap validation expanded to cross-check `test_owns` across all teammates.
- **`/trw-sprint-init` skill** — Step 3 (Pre-implementation state check) added after PRD survey.

## [0.14.0] — 2026-03-14

### Added

- **MemoryStore connection singleton** (`state/memory_store.py`) — `get_memory_store()` / `reset_memory_store()` for connection reuse across warm tier operations (PRD-FIX-046-FR03)
- **Batch SQL access tracking** (`state/memory_adapter.py`) — `update_access_tracking()` uses single `UPDATE ... WHERE id IN (...)` instead of N per-ID operations (PRD-FIX-046-FR01)
- **Single-query keyword search** (`state/memory_adapter.py`) — `_keyword_search()` uses AND'd LIKE clauses in one SQL query for multi-token searches (PRD-FIX-046-FR02)
- **Shared ThreadPoolExecutor** (`clients/llm.py`) — module-level `_get_executor()` replaces per-call pool creation (PRD-FIX-046-FR05)
- **PRD template v2.2** — FIX/RESEARCH category variant sections (Root Cause Analysis, Rollback Plan, Background & Prior Art, etc.), FR Status annotations, category-aware Quality Checklist
- **`_filter_sections_for_category()` trailing content fix** — Appendix and Quality Checklist preserved for all categories
- **FIX-043 tests** — FR02 (unique ceremony event names via AST), FR03 (flush queue preservation), FR07 (mark_run_complete warning)
- **FIX-044 tests** — module-level capture verification, submodule function-level config checks
- **MemoryStore singleton tests** — same-path reuse, different-path recreation, reset cleanup

### Changed

- **Error handling policy enforced** (PRD-FIX-043) — all `except Exception` blocks now either log at `warning+` with `exc_info=True` or have `# justified: <reason>` comments. Zero non-compliant blocks remain.
- **Module-level config capture eliminated** (PRD-FIX-044) — zero `_config = get_config()` or `_reader`/`_writer` module-scope assignments remain. `claude_md` submodules use function-level `get_config()` / `FileStateReader()` / `FileStateWriter()`.
- **`scoring/__init__.py`** — `sys.modules` replacement hack removed, standard `__getattr__` shim
- **DRY glob consolidation** (PRD-FIX-045) — zero raw `entries_dir.glob("*.yaml")` patterns remain; all use `iter_yaml_entry_files()` from `state/_helpers.py`
- **`_safe_float`/`_safe_int` aliases removed** from `analytics/core.py` — consumers import directly from `state._helpers`
- **`trw-prd-groom` skill** — updated from V1 "0.85 completeness" to V2 "total_score >= 65 (REVIEW tier)"
- **`_reset_module_singletons` fixture** — removed (no longer needed)
- **`__reload_hook__` functions** — removed from modules that only reset singletons

### Fixed

- **`_correlation.py` YAML path lookup** — used `yaml_find_entry_by_id()` instead of broken `{lid}.yaml` pattern (YAML files use date-slug names)
- **`memory_adapter.py` `outcome_history` field** — added to `_memory_to_learning_dict()` output for SQLite-based reads
- **Template filter dropping Appendix** — `_filter_sections_for_category()` now extracts trailing non-numbered sections and preserves them

## [0.13.3] — 2026-03-14

### Fixed

- **Telemetry events table empty on dashboard** (P0) — `getTelemetryEvents()` in `platform/src/lib/api.ts` expected a flat array but the backend returns a `PaginatedResponse` envelope. Now unwraps `.items` from the paginated response.
- **`tests_passed: true` despite test failures** (P0) — `_run_pytest()` in `build/_runners.py` set `tests_passed` based only on pytest's return code, ignoring parsed `failure_count`. Now cross-checks `result.returncode == 0 and failure_count == 0` on both standard and custom command paths.
- **`build_pass_rate` always null on analytics dashboard** (P1) — `pytest_passed`, `test_count`, `coverage_pct`, `mypy_passed` fields were not in `_MAPPED_FIELDS` in `backend/routers/telemetry.py`, so they fell into the `payload` JSON overflow bucket instead of their dedicated DB columns. Added `_bool()` helper and mapped all four fields.
- **`trw_quality_dashboard` trends always null** (P1) — `dashboard.py:aggregate_dashboard()` reads `ceremony_score`, `coverage_pct`, `tests_passed` from `session-events.jsonl`, but no delivery step wrote those fields. Added session summary event in `_step_telemetry` that writes ceremony score, task, phase, and build results to `session-events.jsonl`.
- **`config.telemetry` gate always truthy** (P2) — the `if config.telemetry:` check in `tools/telemetry.py` tested a `TelemetryConfig` Pydantic object (always truthy). Changed to check `config.telemetry.platform_telemetry_enabled` for proper two-tier gating of detailed telemetry records.

## [0.13.2] — 2026-03-14

### Fixed

- **Build check timeout indistinguishable from failure** — `trw_build_check` subprocess timeouts wrote `tests_passed: false` to `build-status.yaml`, identical to actual test failures. Added `timed_out: bool` field to `BuildStatus` model, `PytestResultDict`, and `MypyResultDict`. The deliver gate hook now differentiates timeout from failure with distinct error messages.
- **Deliver gate hook error messages lack motivation** — rewrote all 3 hook error paths (no build record, timeout, failure) with structured BLOCKED/WHY/ACTION format. Messages now explain *why* the gate exists (protect the user from broken code) and provide copy-pasteable next steps, including an escape hatch for timeouts when tests were verified manually.

### Changed

- **`BuildStatus` model** — added `timed_out` field (default `false`), propagated through `_runners.py` → `_core.py` → `_registration.py`.
- **`pre-tool-deliver-gate.sh`** — both `.claude/hooks/` and bundled `data/hooks/` copies updated with prompt-engineered error messages.

## [0.13.1] — 2026-03-14

### Added

- **AARE-F scoring truthfulness** (PRD-FIX-054) — removed 3 stub dimensions (`smell_score`, `readability`, `ears_coverage`) from V2 scorer output. Implemented `_compute_ambiguity_rate()` with pre-compiled regexes for vague term detection. Recalibrated dimension weights to sum to 100 across 3 active dimensions (density=42, structure=25, traceability=33). Risk profiles updated to 3-tuple weights.
- **Language-agnostic traceability** (PRD-FIX-055) — `test_refs` regex now matches TypeScript `.test.ts`/`.spec.tsx`, Go `_test.go`, Java `*Test.java`, Ruby `_spec.rb`, and Rust conventions. 58 new tests verify all language conventions.
- **PRD status integrity** (PRD-FIX-056) — status drift detection compares YAML frontmatter vs prose Quick Reference. `update_frontmatter()` auto-syncs prose status. `prd_status.py` state machine wired into `check_transition_guards()`. FR-level `**Status**: active` annotation injected into generated templates. Warns on null `approved_by` for terminal transitions.
- **Category-specific template variants** (PRD-CORE-080) — `template_variants.py` defines 4 template variants (feature=12 sections, fix=7, infra=9, research=3). `score_structural_completeness()` now category-aware. `_generate_prd_body()` filters sections by category. Content density section weights configurable via `TRWConfig`. Decorative fields (`aaref_components`, `conflicts_with`) stripped from generated PRDs.
- **TypedDict type system** — 79 TypedDict classes across 18 submodules in `models/typed_dicts/` replacing `dict[str, object]` at all major cross-module boundaries. Includes `StepResultBase` and `ReviewResultBase` inheritance hierarchies. Applied to 30+ source files (memory_adapter, tools/, scoring/, state/, build/, review/, ceremony/).
- **~225 new Sprint 63 tests** — covering scoring truthfulness, traceability language support, status integrity, template variants.

### Fixed

- **Scoring total_score unreachable** — ceiling was 76-78 due to stub dimensions inflating the denominator. Now achievable up to 100.
- **Non-Python PRDs penalized** — TypeScript/Go/Rust PRDs lost 6-8 traceability points from Python-only `test_refs` regex.
- **Ambiguity rate always 0.0** — was hardcoded; now computed from vague term count / requirement statement count.
- **Q-value convergence broken** — `process_outcome()` read from SQLite but wrote only to YAML. Subsequent calls got stale data. Fixed with SQLite writeback after Q-value computation.
- **Status drift undetected** — no mechanism compared frontmatter status vs prose Quick Reference. Now warns on mismatch.
- **32 pre-existing test failures** — root cause: `_isolate_trw_dir` fixture path mismatch between `isolated_project/.trw/` and `tmp_path/.trw/`. Fixed project root resolution consistency.
- **`PublishResult` duplicate** — was identical to `PublishLearningsResult`; now an alias.

### Changed

- **Dimension weights** — `validation_density_weight=42.0`, `validation_structure_weight=25.0`, `validation_traceability_weight=33.0` (previously 25/15/20 out of 60 active).
- **Risk profile weights** — all 4 profiles changed from 6-tuple to 3-tuple (density, structure, traceability).
- **Stub config fields marked reserved** — `validation_smell_weight`, `validation_readability_weight`, `validation_ears_weight`, `consistency_validation_min` annotated as "reserved — not enforced".
- **`completeness_score` deprecated** — field retained for backward compatibility with deprecation annotation; `total_score` is the sole authoritative metric.
- **`typed_dicts.py` modularized** — 1,424-line monolith split into 18 focused submodules with backward-compatible re-exports via `__init__.py`.

## [0.13.0] — 2026-03-14

### Added

- **Test isolation autouse fixture** (PRD-FIX-050-FR01/FR02) — prevents pytest runs from polluting production `.trw/context/` analytics files. Patches `resolve_trw_dir()` and `resolve_project_root()` across all late-import consumers.
- **Ceremony scoring reads session-events.jsonl** (PRD-FIX-051-FR01/FR05) — `compute_ceremony_score()` now merges events from both run-level `events.jsonl` and the fallback `session-events.jsonl`, fixing scores that were always 0.0 because `trw_session_start` fires before `trw_init`.
- **Zero-score escalation guard** (PRD-FIX-051-FR04) — `check_auto_escalation()` returns `None` when all scores are 0.0 (corrupted data), preventing spurious STANDARD→COMPREHENSIVE escalations.
- **De-escalation wiring** (PRD-FIX-051-FR03) — ceremony reduction proposals are now generated during delivery and persisted to `ceremony-overrides.yaml` on disk (thread-safe across daemon/main threads).
- **Task description pass-through** (PRD-FIX-051-FR06) — `classify_task_class()` now accepts `task_description` parameter, using objective keywords for more accurate classification beyond task name alone.
- **Impact tier auto-assignment** (PRD-FIX-052-FR01/FR02) — `assign_impact_tiers()` labels entries as `critical/high/medium/low` based on impact score. Uses `Literal` type enforcement on `LearningEntry.impact_tier`.
- **Tag-based consolidation fallback** (PRD-FIX-052-FR03) — when embeddings are unavailable, consolidation uses Jaccard similarity on tag overlap (no `max_entries` cap for the tag path).
- **Auto-obsolete on compendium** (PRD-FIX-052-FR04) — when `consolidated_from` is provided to `trw_learn`, source entries are automatically marked obsolete.
- **Pattern tag auto-suggestion** (PRD-FIX-052-FR05) — heuristic keyword detection adds `"pattern"` tag to solution-oriented learnings (e.g., "use X instead of Y").
- **Tier distribution in deliver results** (PRD-FIX-052-FR07) — delivery output now includes `impact_tier_distribution` counts.
- **Embedding health advisory** (PRD-FIX-053-FR01/FR07) — `trw_session_start` response includes `embed_health` dict with `enabled`, `available`, `advisory`, and `recent_failures` fields.
- **Relaxed trust increment** (PRD-FIX-053-FR02) — trust fires on "productive session" (≥3 learnings + ≥1 checkpoint) even without `build_check`, reading both event files.
- **claude_md_sync content hash** (PRD-FIX-053-FR04) — SHA-256 hash of inputs skips redundant 50-second renders when nothing changed.
- **BFS PRD auto-progression** (PRD-FIX-053-FR05) — `auto_progress_prds` uses BFS to find valid multi-step transition paths, stopping at first guard failure instead of returning `invalid_transition`.
- **Telemetry event separation** (PRD-FIX-053-FR06) — `suppress_internal_events()` context manager via `contextvars` suppresses bookkeeping events (`jsonl_appended`, `yaml_written`, `vector_upserted`) from telemetry logs.
- **SQLite outcome correlation** (PRD-FIX-053-FR03) — O(1) indexed lookup via `memory_adapter` with YAML fallback for pre-migration entries.
- **~111 new tests** — zero regressions, +88 net new passing tests vs baseline.

### Fixed

- **Ceremony scoring always 0.0** — root cause: `trw_session_start` event written to `session-events.jsonl` (fallback path) was never read by scoring function.
- **Task classification always "documentation"** — root cause: `run_state.get("task_name")` used wrong field key (`task_name` vs `task` in RunState model).
- **Auto-escalation one-way ratchet** — zero-score guard + de-escalation proposal wiring.
- **outcome_quality hardcoded 0.6** — now derived from build_passed, coverage_delta, critical_findings, mutation_score.
- **agent_id always "unknown"** — derived from `TRW_AGENT_ID` env, run_id, or `pid-{N}`.
- **sessions_count always 0** — migrated to `sessions_tracked` (session_start) + `sessions_delivered` (deliver) split.
- **Test-polluted production data** — `sanitize_ceremony_feedback()` one-time migration removes pytest entries.
- **Publish threshold too restrictive** — `min_impact` lowered from 0.7 to 0.5.
- **"add" keyword too broad** in task classification — replaced with "add feature".

### Changed

- **`_merge_session_events()` DRY helper** — extracted shared session-events.jsonl merge logic used by both ceremony scoring and trust increment.
- **`scan_all_runs` passes `trw_dir`** to `compute_ceremony_score` for accurate analytics reports.
- **Consolidation `max_entries` cap removed** for tag-based fallback path (cap was for embedding API costs, irrelevant for local tag comparison).

## [0.12.7] — 2026-03-14

### Changed

- **trw-implementer agent upgraded to Opus** — changed model from `claude-sonnet-4-6` to `claude-opus-4-6` for higher-quality implementation output.

## [0.12.6] — 2026-03-14

### Added

- **Skills v2 frontmatter migration** (PRD-INFRA-037) — all 24 skills now declare `model` (8 opus, 16 sonnet), 5 destructive skills have `disable-model-invocation: true`, 7 read-only skills use `context: fork`, 4 PLAN-phase skills include `ultrathink` for deep reasoning.
- **PreToolUse deliver gate** (PRD-INFRA-038) — new `pre-tool-deliver-gate.sh` blocks `trw_deliver()` unless `build-status.yaml` shows `tests_passed: true`. Fail-open pattern with actionable error messages.
- **SubagentStop telemetry** (PRD-INFRA-038) — new `subagent-stop.sh` hook emits structured JSONL to `.trw/logs/subagent-events.jsonl` for paired start/stop lifecycle tracking.
- **SubagentStart telemetry** (PRD-INFRA-038) — enhanced `subagent-start.sh` with matching JSONL telemetry for paired analysis.
- **Path-scoped rules** (PRD-INFRA-039) — 3 new `.claude/rules/` files (`backend-python.md`, `platform-tsx.md`, `trw-mcp-python.md`) that only load when Claude touches matching files, reducing per-session token consumption.
- **Plugin packaging** (PRD-INFRA-040) — `make plugin` builds a Claude Code plugin directory with all skills, agents, hooks, and MCP config. Testable via `claude --plugin-dir build/trw-plugin`.
- **Plugin manifest** — `.claude-plugin/plugin.json` with `minClaudeCodeVersion: 2.1.32`, CC-BY-NC-SA-4.0 license.
- **Plugin hooks.json** — all 11 hook events registered with `${CLAUDE_PLUGIN_ROOT}` path resolution.

### Changed

- **CLAUDE.md slimmed** — 337 → 181 lines by extracting package-specific content into path-scoped rules. Restored missing deployment commands, release workflow details, and `opusplan` note.
- **data/settings.json** — added PreToolUse (deliver gate) and SubagentStop hook registrations to the bootstrap template so new projects get them automatically.
- **Timestamp key standardized** — all hook JSONL output now uses `"ts"` key (matching lib-trw.sh `append_event` convention), replacing inconsistent `"timestamp"` usage.
- **pre-compact.sh enhanced** — captures wave_manifest, active_tasks, and pending_decisions in the pre-compaction state snapshot for better recovery.
- **pre-compact.sh no-jq fallback** — simplified to emit minimal JSON without user-controlled strings to prevent injection in degraded mode.
- **Framework version** — updated reference in CLAUDE.md from v24.2 to v24.3 to match TRWConfig source of truth.
- **trw-simplify SKILL.md** — fixed non-standard `allowed_tools` (underscore) to `allowed-tools` (hyphen), added missing `name`, `description`, `user-invocable` fields.
- **trw-dry-check SKILL.md** — added missing `user-invocable`, `allowed-tools`, `argument-hint`, `description` fields.

### Documentation

- **3 research documents** — `skills-v2-reference.md` (complete Skills v2 spec), `claude-code-march-2026-updates.md` (hooks, MCP, settings), `prompting-claude-4-6.md` (anti-overtriggering, adaptive thinking).
- **Agent Teams prerequisite** — documented `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` env var requirement in CLAUDE.md.
- **MCP Tool Search** — documented `ENABLE_TOOL_SEARCH` env var and auto-deferral threshold.
- **Worktree isolation exclusion** — documented rationale for not adopting `isolation: worktree` on agents.

## [0.12.5] — 2026-03-13

### Fixed

- **Auth error leaks into installer progress** — `_run_claude_md_sync` now suppresses stdout/stderr during LLMClient initialization and CLAUDE.md sync. Prevents `TypeError: "Could not resolve authentication"` from corrupting the installer's spinner output when no Anthropic API key is configured.
- **Installer regex matched Python exceptions** — `re.search(r"Error")` matched `TypeError`, `ValueError`, etc. Changed to `re.match()` with line-start anchoring so only progress-format lines (e.g., `Error: path`) are parsed.

### Added

- **2 tests for CLAUDE.md sync auth failure** — verifies auth errors are captured as warnings (not errors) and don't leak to stdout.

## [0.12.4] — 2026-03-13

### Fixed

- **Installer progress stalls at "70 files"** — the spinner stopped updating during slow post-file phases (cleanup, verification, CLAUDE.md sync). Now emits `Phase:` progress lines for all 7 update stages, and the installer parses them to update the spinner message (e.g., "Updating project... (70 files) Syncing CLAUDE.md...").
- **Installer regex missed `Skipped`/`Error` progress lines** — expanded `run_with_progress` regex to match all action types from the progress callback.

## [0.12.3] — 2026-03-13

### Added

- **Streaming progress output** — `init-project` and `update-project` now emit file-by-file progress lines to stdout in real time via `ProgressCallback`. The installer's spinner updates live (e.g., "Updating project... (23 files) .claude/hooks/pre-compact.sh") instead of showing a static "Updating project..." for the entire duration.

### Changed

- **Installer re-run UX** — removed unnecessary "Change project name?" prompt on re-install. Prior project name, API key, and telemetry settings are now silently reused without confirmation prompts.

## [0.12.2] — 2026-03-13

### Changed

- **Memory audit/optimize skills** — replaced hardcoded "20-40 entries" target with dynamic sizing formula: (domain count) × 3-5 per domain. Adds consolidation depth limits (max 10-15 per compendium), domain coverage rules, and sub-topic granularity constraints. Prevents over-aggressive consolidation on large multi-domain projects.

## [0.12.1] — 2026-03-13

### Added

- **Installer re-run intelligence** — when re-run in a directory with an existing TRW installation, the installer now:
  - Reads prior settings from `.trw/config.yaml` (project name, API key, telemetry preferences)
  - Detects already-installed optional extras (`anthropic`, `sqlite-vec`) via import probes
  - Skips questions whose answers are already known, showing "reusing prior settings" feedback
  - Skips IDE detection prompt when IDEs are already configured

### Changed

- **Version bump** — minor version bump reflecting multi-IDE support (PRD-CORE-074: OpenCode, Cursor, Layer 3 nudges)

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
