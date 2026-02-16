# Changelog

All notable changes to the TRW MCP server package.

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
