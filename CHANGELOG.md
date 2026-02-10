# Changelog

All notable changes to the TRW MCP server package.

## [Unreleased]

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
