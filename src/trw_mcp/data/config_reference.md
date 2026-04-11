# TRW Configuration Reference

Complete reference for all TRW Framework configuration surfaces.

## Overview

TRW uses four configuration surfaces, listed here in precedence order (highest wins):

1. **Environment variables** (`TRW_*` prefix) -- override everything
2. **`.trw/config.yaml`** -- project-level settings
3. **Field defaults** in `TRWConfig` -- sensible defaults from FRAMEWORK.md
4. **`.mcp.json`** -- MCP server connection (not part of the precedence chain)

When a `TRW_*` environment variable is set, the corresponding `config.yaml` key is ignored. All unknown keys in both env vars and config.yaml are silently discarded.

## `.trw/config.yaml`

Project-level configuration file. Created by `trw-mcp init-project` with minimal defaults. Only fields you want to override need to be present.

### Orchestration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `parallelism_max` | `int` | `10` | Maximum concurrent shards/waves |
| `timebox_hours` | `int` | `8` | Total session timebox in hours |
| `max_research_waves` | `int` | `3` | Maximum research iteration waves |
| `min_shards_target` | `int` | `3` | Target minimum shards per wave |
| `min_shards_floor` | `int` | `2` | Hard minimum shards per wave |
| `consensus_quorum` | `float` | `0.67` | Shard agreement threshold |
| `max_child_depth` | `int` | `2` | Maximum nesting depth for sub-shards |
| `checkpoint_secs` | `int` | `600` | Seconds between auto-checkpoint triggers |

### Phase Time Caps

Percentage of total timebox allocated to each phase. 6-phase model: RESEARCH, PLAN, IMPLEMENT, VALIDATE, REVIEW, DELIVER.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `phase_cap_research` | `float` | `0.25` | Research phase time fraction |
| `phase_cap_plan` | `float` | `0.15` | Planning phase time fraction |
| `phase_cap_implement` | `float` | `0.35` | Implementation phase time fraction |
| `phase_cap_validate` | `float` | `0.10` | Validation phase time fraction |
| `phase_cap_review` | `float` | `0.10` | Review phase time fraction |
| `phase_cap_deliver` | `float` | `0.05` | Delivery phase time fraction |

### Learning Storage & Retrieval

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `learning_max_entries` | `int` | `500` | Maximum learning entries before pruning |
| `learning_promotion_impact` | `float` | `0.7` | Minimum impact score for CLAUDE.md promotion |
| `learning_prune_age_days` | `int` | `30` | Age threshold for pruning low-utility entries |
| `learning_repeated_op_threshold` | `int` | `3` | Threshold for "repeated operation" detection |
| `recall_receipt_max_entries` | `int` | `1000` | Maximum recall receipt log entries |
| `recall_max_results` | `int` | `25` | Default max results for `trw_recall` |
| `recall_compact_fields` | `frozenset[str]` | `{"id","summary","impact","tags","status"}` | Fields included in compact recall mode |

Example:
```yaml
learning_max_entries: 200
learning_promotion_impact: 0.8
recall_max_results: 15
```

### Hybrid Retrieval (Memory)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `memory_store_path` | `str` | `".trw/memory/vectors.db"` | SQLite database path for vector storage |
| `embeddings_enabled` | `bool` | `false` | Enable dense vector embeddings (requires `trw-memory[embeddings]`) |
| `retrieval_embedding_model` | `str` | `"all-MiniLM-L6-v2"` | HuggingFace model for local embeddings |
| `retrieval_embedding_dim` | `int` | `384` | Embedding dimension (must match model) |
| `hybrid_bm25_candidates` | `int` | `50` | BM25 candidate count for hybrid search |
| `hybrid_vector_candidates` | `int` | `50` | Vector candidate count for hybrid search |
| `hybrid_rrf_k` | `int` | `60` | Reciprocal Rank Fusion k parameter |
| `hybrid_reranking_enabled` | `bool` | `false` | Cross-encoder reranking (future) |
| `retrieval_fallback_enabled` | `bool` | `true` | Fall back to keyword search when hybrid unavailable |

### Semantic Dedup

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `dedup_enabled` | `bool` | `true` | Enable semantic deduplication on store |
| `dedup_skip_threshold` | `float` | `0.95` | Cosine similarity threshold to skip (exact duplicate) |
| `dedup_merge_threshold` | `float` | `0.85` | Cosine similarity threshold to merge (near duplicate) |

### Memory Consolidation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `memory_consolidation_enabled` | `bool` | `true` | Enable periodic cluster consolidation |
| `memory_consolidation_interval_days` | `int` | `7` | Days between consolidation cycles |
| `memory_consolidation_min_cluster` | `int` | `3` | Minimum entries to form a cluster (min: 2) |
| `memory_consolidation_similarity_threshold` | `float` | `0.75` | Similarity threshold for clustering (0.0-1.0) |
| `memory_consolidation_max_per_cycle` | `int` | `50` | Maximum entries consolidated per cycle (min: 1) |

### Tiered Memory

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `memory_hot_max_entries` | `int` | `50` | Maximum entries in hot tier |
| `memory_hot_ttl_days` | `int` | `7` | Days before hot tier entry demotion |
| `memory_cold_threshold_days` | `int` | `90` | Days before cold tier archival |
| `memory_retention_days` | `int` | `365` | Maximum retention before deletion |
| `memory_score_w1` | `float` | `0.4` | Relevance weight in utility scoring |
| `memory_score_w2` | `float` | `0.3` | Recency weight in utility scoring |
| `memory_score_w3` | `float` | `0.3` | Importance weight in utility scoring |

### Impact Score Distribution

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `impact_forced_distribution_enabled` | `bool` | `true` | Enforce impact score distribution caps |
| `impact_tier_critical_cap` | `float` | `0.05` | Max 5% of entries at 0.9-1.0 impact |
| `impact_tier_high_cap` | `float` | `0.20` | Max 20% of entries at 0.7-0.89 impact |
| `impact_high_threshold_pct` | `float` | `20.0` | Soft cap: max % of learnings >= 0.8 |
| `impact_decay_half_life_days` | `int` | `90` | Half-life for batch impact decay |

### Utility Scoring & Decay

Q-learning + Ebbinghaus decay curves for learning utility.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `learning_decay_half_life_days` | `float` | `14.0` | Days until half utility loss |
| `learning_decay_use_exponent` | `float` | `0.6` | Usage-based decay exponent |
| `learning_utility_prune_threshold` | `float` | `0.10` | Utility below this triggers pruning |
| `learning_utility_delete_threshold` | `float` | `0.05` | Utility below this triggers deletion |
| `q_learning_rate` | `float` | `0.15` | Q-learning update rate (alpha) |
| `q_recurrence_bonus` | `float` | `0.02` | Bonus for recurring patterns |
| `q_cold_start_threshold` | `int` | `3` | Minimum observations before Q-value is trusted |
| `source_human_utility_boost` | `float` | `0.1` | Utility boost for human-sourced learnings |
| `access_count_utility_boost_cap` | `float` | `0.15` | Maximum utility boost from access frequency |

### Documentation Generation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `claude_md_max_lines` | `int` | `500` | Maximum lines in CLAUDE.md auto-generated section |
| `sub_claude_md_max_lines` | `int` | `50` | Maximum lines in sub-module CLAUDE.md |
| `max_auto_lines` | `int` | `80` | Maximum auto-generated lines per section |
| `agents_md_enabled` | `bool` | `true` | Include agent definitions in CLAUDE.md |
| `agent_teams_enabled` | `bool` | `true` | Include Agent Teams section in CLAUDE.md |

Example:
```yaml
claude_md_max_lines: 300
agents_md_enabled: false
```

### Directory Structure & Paths

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `task_root` | `str` | `"docs"` | Root directory for task documentation |
| `runs_root` | `str` | `".trw/runs"` | Where run artifacts are stored (`{runs_root}/{task}/{run_id}/`) |
| `trw_dir` | `str` | `".trw"` | TRW data directory name |
| `worktree_dir` | `str` | `".trees"` | Git worktree directory name |
| `learnings_dir` | `str` | `"learnings"` | Learnings subdirectory |
| `entries_dir` | `str` | `"entries"` | Learning entries subdirectory |
| `receipts_dir` | `str` | `"receipts"` | Recall receipts subdirectory |
| `reflections_dir` | `str` | `"reflections"` | Reflection output subdirectory |
| `scripts_dir` | `str` | `"scripts"` | Scripts subdirectory |
| `patterns_dir` | `str` | `"patterns"` | Detected patterns subdirectory |
| `context_dir` | `str` | `"context"` | Context/state cache directory |
| `scratch_dir` | `str` | `"scratch"` | Scratch workspace directory |
| `logs_dir` | `str` | `"logs"` | Log output directory |
| `events_file` | `str` | `"events.jsonl"` | Run events log filename |
| `checkpoints_file` | `str` | `"checkpoints.jsonl"` | Checkpoint log filename |
| `frameworks_dir` | `str` | `"frameworks"` | Framework snapshots directory |
| `templates_dir` | `str` | `"templates"` | Template files directory |

Example:
```yaml
task_root: docs
runs_root: .trw/runs    # run artifacts: {runs_root}/{task}/{run_id}/
trw_dir: .trw
```

### Framework Version

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `framework_version` | `str` | `"v24.5_TRW"` | Framework version identifier |
| `aaref_version` | `str` | `"v1.1.0"` | AARE-F requirements framework version |

### PRD Quality Gates

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ambiguity_rate_max` | `float` | `0.05` | Maximum allowed ambiguity rate in PRDs |
| `completeness_min` | `float` | `0.85` | Minimum completeness score |
| `traceability_coverage_min` | `float` | `0.90` | Minimum traceability coverage |
| `consistency_validation_min` | `float` | `0.95` | Minimum consistency validation score |

### Phase Gates & Enforcement

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `phase_gate_enforcement` | `str` | `"lenient"` | Gate enforcement mode: `strict`, `lenient`, `off` |
| `prd_min_content_density` | `float` | `0.30` | Minimum PRD content density |
| `prd_required_status_for_implement` | `str` | `"approved"` | PRD status required to enter IMPLEMENT |
| `prds_relative_path` | `str` | `"docs/requirements-aare-f/prds"` | PRD directory relative to project root |
| `index_auto_sync_on_status_change` | `bool` | `true` | Auto-sync INDEX.md on PRD status changes |
| `strict_input_criteria` | `bool` | `false` | Strict phase input criteria (errors vs warnings) |

### Build Verification

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `build_check_enabled` | `bool` | `true` | Enable build verification gate |
| `build_check_timeout_secs` | `int` | `300` | Build check timeout in seconds |
| `build_check_coverage_min` | `float` | `85.0` | Minimum test coverage percentage |
| `build_gate_enforcement` | `str` | `"lenient"` | Build gate mode: `strict`, `lenient`, `off` |
| `build_check_pytest_args` | `str` | `""` | Extra pytest arguments |
| `build_check_mypy_args` | `str` | `"--strict"` | Extra mypy arguments |
| `build_check_pytest_cmd` | `str\|null` | `null` | Custom test command (e.g., `"make test"`) |

Example:
```yaml
build_check_coverage_min: 80.0
build_check_timeout_secs: 600
build_check_pytest_cmd: "make test"
```

### LLM Augmentation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `llm_enabled` | `bool` | `true` | Enable LLM-augmented tools (requires `anthropic` SDK) |
| `llm_default_model` | `str` | `"haiku"` | Default LLM model for augmented tools |

### Debug & Telemetry

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `debug` | `bool` | `false` | Enable debug logging |
| `telemetry` | `bool` | `false` | Session-level telemetry flag |
| `telemetry_enabled` | `bool` | `true` | Per-tool telemetry toggle |
| `telemetry_file` | `str` | `"tool-telemetry.jsonl"` | Telemetry log filename |
| `llm_usage_log_enabled` | `bool` | `true` | Log LLM API usage |
| `llm_usage_log_file` | `str` | `"llm_usage.jsonl"` | LLM usage log filename |

Example:
```yaml
debug: true
telemetry: true
```

### MCP Transport

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mcp_transport` | `str` | `"stdio"` | Transport mode: `stdio`, `sse`, `streamable-http` |
| `mcp_host` | `str` | `"127.0.0.1"` | Bind address for HTTP transports |
| `mcp_port` | `int` | `8100` | Port for HTTP transports |

Example:
```yaml
mcp_transport: streamable-http
mcp_host: 127.0.0.1
mcp_port: 8100
```

### Project Source Paths

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source_package_path` | `str` | `"trw-mcp/src"` | Source code root for build checks |
| `source_package_name` | `str` | `"trw_mcp"` | Python package name for mypy/pytest |
| `tests_relative_path` | `str` | `"trw-mcp/tests"` | Test directory for build checks |
| `test_map_filename` | `str` | `"test-map.yaml"` | Test map filename for build checks |

### Platform & Update Channel

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `platform_telemetry_enabled` | `bool` | `false` | Opt-in anonymized usage telemetry |
| `update_channel` | `str` | `"latest"` | Update channel: `latest`, `lts` |
| `platform_url` | `str` | `""` | Backend URL (empty = offline mode) |
| `platform_urls` | `list[str]` | `[]` | Multi-backend URLs: fan-out writes, first-success reads |
| `platform_api_key` | `str` | `""` | API key for platform backend |
| `installation_id` | `str` | `""` | Anonymized installation identifier |
| `auto_upgrade` | `bool` | `false` | Auto-install updates on session start |

### Progressive Disclosure

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `progressive_disclosure` | `bool` | `false` | Compact capability cards for non-hot-set tools |

### Knowledge Topology

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `knowledge_sync_threshold` | `int` | `50` | Minimum learnings before auto-sync |
| `knowledge_jaccard_threshold` | `float` | `0.3` | Jaccard similarity threshold for clustering (0.0-1.0) |
| `knowledge_min_cluster_size` | `int` | `3` | Minimum entries per topic cluster (min: 1) |
| `knowledge_output_dir` | `str` | `"knowledge"` | Output directory for topic documents |

### Completion Hooks

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `completion_hooks_blocking` | `bool` | `false` | Block task completion on hook failures |
| `self_review_blocking` | `bool` | `false` | Block completion until self-review passes |

### Auto-Checkpoint & Auto-Recall

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_checkpoint_enabled` | `bool` | `true` | Enable auto-checkpointing |
| `auto_checkpoint_tool_interval` | `int` | `25` | Tool calls between auto-checkpoints |
| `auto_checkpoint_pre_compact` | `bool` | `true` | Checkpoint before context compaction |
| `auto_recall_enabled` | `bool` | `true` | Enable phase-contextual auto-recall |
| `auto_recall_max_results` | `int` | `3` | Maximum auto-recall results |
| `auto_recall_max_tokens` | `int` | `100` | Maximum UserPromptSubmit auto-recall output in tokens |
| `auto_recall_min_score` | `float` | `0.7` | Minimum keyword-match score required for hook injection |

### Learning Auto-Prune

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `learning_auto_prune_on_deliver` | `bool` | `true` | Prune on deliver when count exceeds cap |
| `learning_auto_prune_cap` | `int` | `150` | Prune threshold (active entry count) |

### Advanced Configuration

The sections below cover specialized subsystems. Most projects can use defaults.

#### Scoring & Utility

Outcome-based utility scoring extracted from Sprint 8.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `scoring_default_days_unused` | `int` | `30` | Days without access before utility penalty |
| `scoring_recency_discount_floor` | `float` | `0.5` | Minimum recency discount factor |
| `scoring_error_fallback_reward` | `float` | `-0.3` | Q-learning reward for error outcomes |
| `scoring_error_keywords` | `tuple[str,...]` | `("error","fail","exception","crash","timeout")` | Keywords that trigger error outcome scoring |

#### Outcome Correlation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `learning_outcome_correlation_window_minutes` | `int` | `480` | Time window for correlating recalls with outcomes |
| `learning_outcome_correlation_scope` | `str` | `"session"` | Correlation scope: `session` |
| `learning_outcome_history_cap` | `int` | `20` | Maximum outcome history entries per learning |
| `recall_utility_lambda` | `float` | `0.3` | Weight of recall-outcome correlation in utility |

#### Semantic Validation & Risk

Quality dimension weights for PRD validation (must sum to 100).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `validation_density_weight` | `float` | `20.0` | Content density hygiene weight |
| `validation_structure_weight` | `float` | `20.0` | Structural completeness weight |
| `validation_implementation_readiness_weight` | `float` | `25.0` | Execution-readiness signal weight |
| `validation_traceability_weight` | `float` | `35.0` | Traceability and proof weight |
| `validation_smell_weight` | `float` | `0.0` | Requirement smell dimension weight (reserved) |
| `validation_readability_weight` | `float` | `0.0` | Readability dimension weight (reserved) |
| `validation_ears_weight` | `float` | `0.0` | EARS pattern dimension weight (reserved) |
| `validation_skeleton_threshold` | `float` | `30.0` | Score below this = skeleton status |
| `validation_draft_threshold` | `float` | `60.0` | Score below this = draft status |
| `validation_review_threshold` | `float` | `85.0` | Score above this = review-ready |
| `validation_fk_optimal_min` | `float` | `8.0` | Flesch-Kincaid optimal minimum grade level |
| `validation_fk_optimal_max` | `float` | `12.0` | Flesch-Kincaid optimal maximum grade level |
| `risk_scaling_enabled` | `bool` | `true` | Scale validation strictness by PRD risk level |

#### PRD Grooming & Research

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `grooming_max_iterations` | `int` | `5` | Maximum grooming iteration cycles |
| `grooming_target_completeness` | `float` | `0.85` | Target completeness score for grooming exit |
| `grooming_research_scope` | `str` | `"full"` | Scope: `full`, `codebase`, `minimal` |
| `grooming_placeholder_density_threshold` | `float` | `0.10` | Max placeholder density before flagging |
| `grooming_partial_density_threshold` | `float` | `0.20` | Max partial content density before flagging |
| `finding_dedup_threshold` | `float` | `0.6` | Similarity threshold for research finding dedup |
| `findings_dir` | `str` | `"findings"` | Findings subdirectory name |
| `findings_entries_dir` | `str` | `"entries"` | Finding entries subdirectory |
| `findings_registry_file` | `str` | `"registry.yaml"` | Findings registry filename |

#### Reflection & Patterns

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `reflect_sequence_lookback` | `int` | `3` | Number of recent events to inspect during reflection |
| `reflect_max_positive_learnings` | `int` | `5` | Maximum positive learnings per reflection |
| `reflect_max_success_patterns` | `int` | `5` | Maximum success patterns per reflection |
| `reflect_q_value_threshold` | `float` | `0.6` | Q-value threshold for pattern extraction |

#### Phase Reversion

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `reversion_rate_elevated` | `float` | `0.15` | Reversion rate considered elevated (warning) |
| `reversion_rate_concerning` | `float` | `0.30` | Reversion rate considered concerning (alert) |

#### Technical Debt & Compliance

Technical debt registry and scoring.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `debt_registry_filename` | `str` | `"debt-registry.yaml"` | Debt registry filename |
| `debt_id_prefix` | `str` | `"DEBT"` | Prefix for debt item IDs |
| `debt_initial_decay_score` | `float` | `0.5` | Initial decay score for new debt items |
| `debt_decay_base_score` | `float` | `0.3` | Base decay score floor |
| `debt_decay_daily_rate` | `float` | `0.01` | Daily decay rate for debt priority |
| `debt_decay_assessment_rate` | `float` | `0.05` | Decay rate per assessment cycle |
| `debt_auto_promote_threshold` | `float` | `0.9` | Score above this auto-promotes to critical |
| `debt_actionable_threshold` | `float` | `0.7` | Score above this marks debt as actionable |
| `debt_budget_critical_ratio` | `float` | `0.20` | Sprint budget ratio for critical debt |
| `debt_budget_high_ratio` | `float` | `0.15` | Sprint budget ratio for high-priority debt |
| `debt_default_wave_size` | `int` | `5` | Default wave size for debt remediation |

Compliance auditing.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `compliance_strictness` | `str` | `"lenient"` | Strictness: `strict`, `lenient`, `off` |
| `compliance_long_session_event_threshold` | `int` | `5` | Event count threshold for long-session detection |
| `compliance_pass_threshold` | `float` | `0.8` | Score above this = pass |
| `compliance_warning_threshold` | `float` | `0.5` | Score below this = warning |
| `compliance_dir` | `str` | `"compliance"` | Compliance artifacts subdirectory |
| `compliance_history_file` | `str` | `"history.jsonl"` | Compliance history log filename |
| `compliance_changelog_filename` | `str` | `"CHANGELOG.md"` | Changelog filename |

Git workflow and enterprise compliance (Sprint 44).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `commit_fr_trailer_enabled` | `bool` | `true` | Add FR trailers to commit messages |
| `sprint_integration_branch_pattern` | `str` | `"sprint-{N}-integration"` | Integration branch naming pattern |
| `compliance_review_retention_days` | `int` | `365` | Retention days for compliance review records |
| `provenance_enabled` | `bool` | `true` | Enable provenance tracking for artifacts |
| `confidence_threshold` | `float` | `0.8` | Confidence threshold for provenance (0.0-1.0) |

#### Wave Adaptation & Velocity

Wave adaptation controls.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `adaptation_enabled` | `bool` | `true` | Enable wave adaptation |
| `max_total_waves` | `int` | `8` | Maximum total waves per run |
| `max_adaptations_per_run` | `int` | `5` | Maximum adaptation events per run |
| `max_shards_added_per_adaptation` | `int` | `3` | Maximum shards added per adaptation |
| `adaptation_auto_approve_threshold` | `int` | `5` | Auto-approve adaptations below this shard count |

Velocity tracking and statistical analysis.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `velocity_alert_min_runs` | `int` | `5` | Minimum runs before velocity alerts activate |
| `velocity_alert_r_squared_min` | `float` | `0.4` | Minimum R-squared for velocity trend alerts |
| `framework_overhead_threshold` | `float` | `0.30` | Maximum acceptable framework overhead ratio |
| `velocity_history_max_entries` | `int` | `200` | Maximum velocity history entries |
| `velocity_stable_threshold` | `float` | `0.05` | Velocity change below this = stable |
| `velocity_effective_q_threshold` | `float` | `0.5` | Q-threshold for effective velocity calc |
| `velocity_sign_test_alpha` | `float` | `0.1` | Alpha for sign test in velocity analysis |
| `velocity_confounder_jump_ratio` | `float` | `1.5` | Ratio threshold for confounder detection |

#### Adaptive Gates & Code Quality

Shard evaluation gates.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `gate_default_type` | `str` | `"FULL"` | Default gate type |
| `gate_strategy` | `str` | `"hybrid"` | Gate strategy: `hybrid` |
| `gate_early_stop_confidence` | `float` | `0.85` | Confidence threshold for early-stop |
| `gate_max_rounds` | `int` | `5` | Maximum evaluation rounds |
| `gate_convergence_epsilon` | `float` | `0.05` | Convergence threshold |
| `gate_escalation_enabled` | `bool` | `true` | Enable gate escalation |
| `gate_max_total_judges` | `int` | `13` | Maximum judge count |
| `gate_tokens_per_vote` | `int` | `2000` | Token budget per vote |
| `gate_debate_context_multiplier` | `float` | `1.5` | Context multiplier for debate rounds |
| `gate_critic_overhead_multiplier` | `float` | `2.0` | Overhead multiplier for critic reviews |
| `gate_tokens_per_1k_chars` | `int` | `500` | Tokens per 1K chars of source |
| `gate_architecture_score_penalty` | `float` | `0.1` | Architecture violation score penalty |

Code simplifier.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_simplify_enabled` | `bool` | `false` | Enable automatic code simplification |
| `simplifier_wave_size` | `int` | `10` | Files per simplifier wave |
| `sprint_code_simplifier_wave_size` | `int` | `10` | Legacy alias for `simplifier_wave_size` |
| `sprint_commit_pattern` | `str` | `"feat(sprint{num}): Track {track}"` | Sprint commit message pattern |
| `simplifier_verification_timeout_secs` | `int` | `120` | Verification timeout after simplification |
| `simplifier_backup_dir` | `str` | `".trw/simplifier-backups"` | Backup directory for pre-simplification state |

#### Run Maintenance

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `run_auto_close_enabled` | `bool` | `true` | Auto-close orphaned runs |
| `run_auto_close_age_days` | `int` | `7` | Days before auto-closing stale runs |
| `run_stale_ttl_hours` | `int` | `48` | Hours TTL for stale run detection |

#### Complexity Classification

Tier boundaries and signal weights for `classify_complexity()`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `complexity_tier_minimal` | `int` | `3` | Raw score at or below this = MINIMAL tier |
| `complexity_tier_comprehensive` | `int` | `7` | Raw score above this = COMPREHENSIVE tier |
| `complexity_weight_novel_patterns` | `int` | `3` | Weight for novel pattern signals |
| `complexity_weight_cross_cutting` | `int` | `2` | Weight for cross-cutting concern signals |
| `complexity_weight_architecture_change` | `int` | `3` | Weight for architecture change signals |
| `complexity_weight_external_integration` | `int` | `2` | Weight for external integration signals |
| `complexity_weight_large_refactoring` | `int` | `1` | Weight for large refactoring signals |
| `complexity_weight_files_affected_max` | `int` | `5` | Max weight from files-affected count |
| `complexity_hard_override_threshold` | `int` | `2` | Min high-risk signals to force COMPREHENSIVE |

#### Mutation Testing & Cross-Model Review

Mutation testing (optional VALIDATE gate).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mutation_enabled` | `bool` | `false` | Enable mutation testing |
| `mutation_threshold` | `float` | `0.50` | Standard feature mutation kill threshold |
| `mutation_threshold_critical` | `float` | `0.70` | Critical path mutation kill threshold |
| `mutation_threshold_experimental` | `float` | `0.30` | Experimental code mutation kill threshold |
| `mutation_critical_paths` | `tuple[str,...]` | `("tools/","state/","models/")` | Paths treated as critical for mutation thresholds |
| `mutation_experimental_paths` | `tuple[str,...]` | `("scratch/",)` | Paths treated as experimental |
| `mutation_timeout_secs` | `int` | `300` | Mutation testing timeout in seconds |

Cross-model review (route diff to external model).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cross_model_review_enabled` | `bool` | `false` | Enable cross-model review |
| `cross_model_provider` | `str` | `"gemini-2.5-pro"` | External model provider/name |
| `cross_model_review_timeout_secs` | `int` | `30` | Timeout for cross-model review call |
| `cross_model_review_block_on_critical` | `bool` | `true` | Block merge on critical findings |

Multi-agent review.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `review_confidence_threshold` | `int` | `80` | Confidence threshold (0-100) for review findings |

#### Dependency Audit & API Fuzz

Dependency audit gate.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `dep_audit_enabled` | `bool` | `true` | Enable dependency audit gate |
| `dep_audit_level` | `str` | `"high"` | Severity level: `critical`, `high`, `medium`, `low` |
| `dep_audit_timeout_secs` | `int` | `30` | Audit timeout in seconds |
| `dep_audit_block_on_patchable_only` | `bool` | `true` | Only block when fix versions are available |

API fuzz and placeholder comment detection.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `comment_check_enabled` | `bool` | `true` | Enable placeholder comment detection hook |
| `api_fuzz_enabled` | `bool` | `false` | Enable Schemathesis API fuzzing |
| `api_fuzz_base_url` | `str` | `"http://localhost:8000"` | Base URL for API fuzz target |
| `api_fuzz_level` | `str` | `"strict"` | Fuzz level: `strict`, `lenient` |
| `api_fuzz_timeout_secs` | `int` | `120` | API fuzz timeout in seconds |

#### ATDD & PostToolUse Validation

Acceptance Test-Driven Development.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `atdd_enabled` | `bool` | `true` | Enable ATDD test skeleton generation |
| `test_skeleton_dir` | `str` | `""` | Test skeleton directory (empty = auto-detect from pyproject.toml) |

PostToolUse incremental validation.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `incremental_validation_enabled` | `bool` | `true` | Incremental type checking after Edit/Write |
| `security_check_enabled` | `bool` | `true` | Security pattern detection after Edit/Write |

#### Compaction

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `compact_instructions_template` | `str` | `""` | Custom compaction instructions (empty = built-in TRW template) |
| `pause_after_compaction` | `bool` | `false` | Pause execution after context compaction |

---

## `TRW_*` Environment Variables

Every field in `TRWConfig` can be overridden via an environment variable with the `TRW_` prefix and uppercase field name.

**Mapping rule**: `field_name` becomes `TRW_FIELD_NAME`.

Examples:

| Config Field | Environment Variable | Example Value |
|-------------|---------------------|---------------|
| `debug` | `TRW_DEBUG` | `true` |
| `task_root` | `TRW_TASK_ROOT` | `docs` |
| `build_check_coverage_min` | `TRW_BUILD_CHECK_COVERAGE_MIN` | `90.0` |
| `mcp_transport` | `TRW_MCP_TRANSPORT` | `streamable-http` |
| `llm_enabled` | `TRW_LLM_ENABLED` | `false` |
| `platform_api_key` | `TRW_PLATFORM_API_KEY` | `trw_key_...` |

Environment variables take the highest precedence. When set, the corresponding `config.yaml` value is ignored.

```bash
# Run with debug mode and custom coverage threshold
TRW_DEBUG=true TRW_BUILD_CHECK_COVERAGE_MIN=90.0 trw-mcp
```

---

## `.mcp.json`

Configures how Claude Code connects to the TRW MCP server. Located at the project root.

### Schema

```json
{
  "mcpServers": {
    "trw": {
      "command": "trw-mcp",
      "args": ["--debug"]
    }
  }
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `mcpServers.trw.command` | `string` | Command to start the MCP server. Use `"trw-mcp"` (bare name, resolved via PATH) |
| `mcpServers.trw.args` | `string[]` | CLI arguments. `["--debug"]` enables debug logging |

### Portable Commands

Always use bare command names, never absolute paths:

```json
// Correct - portable across machines
{"command": "trw-mcp", "args": ["--debug"]}

// Correct - fallback when trw-mcp not on PATH
{"command": "python", "args": ["-m", "trw_mcp.server", "--debug"]}

// WRONG - breaks when venv path differs
{"command": "/home/user/project/.venv/bin/trw-mcp", "args": ["--debug"]}
```

The server emits a warning on startup if it detects a stale absolute path. Run `trw-mcp update-project .` to fix.

### Multiple MCP Servers

You can configure additional MCP servers alongside TRW. The `trw-mcp update-project` command preserves all non-trw entries:

```json
{
  "mcpServers": {
    "trw": {
      "command": "trw-mcp",
      "args": ["--debug"]
    },
    "another-server": {
      "command": "other-mcp-server",
      "args": []
    }
  }
}
```

### Example File

A `.mcp.json.example` is provided at the repository root for reference. Copy it to `.mcp.json` for a fresh setup:

```bash
cp .mcp.json.example .mcp.json
```

---

## `.claude/settings.json`

Configures Claude Code hooks, permissions, and agent settings. Managed by `trw-mcp init-project` and `trw-mcp update-project`.

### Structure

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "command": ".claude/hooks/pre-edit.sh"
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "command": ".claude/hooks/post-edit.sh"
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "command": ".claude/hooks/stop-ceremony.sh"
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "command": ".claude/hooks/pre-compact.sh"
      }
    ]
  },
  "permissions": {
    "allow": [
      "Bash(trw-mcp*)",
      "Bash(make *)",
      "Bash(cd *)"
    ]
  }
}
```

### Hook Types

| Hook | When | Purpose |
|------|------|---------|
| `PreToolUse` | Before Edit/Write/MultiEdit | Security checks, validation |
| `PostToolUse` | After Edit/Write/MultiEdit | Incremental type checking |
| `Stop` | Agent turn ends | Ceremony compliance enforcement |
| `PreCompact` | Before context compaction | Save checkpoint to prevent data loss |
| `SessionStart` | New session begins | Load session state |
| `TeammateIdle` | Teammate goes idle | Quality gate for Agent Teams |
| `TaskCompleted` | Task marked complete | Completion verification |

### Permissions

The `permissions.allow` array whitelists specific Bash commands that Claude Code can run without prompting.

---

## Security Considerations

### Environment Variables

- Environment variables at `DEBUG` log level may be logged to `.trw/logs/`. Avoid storing secrets in `TRW_*` variables unless the log directory is secured.
- `TRW_PLATFORM_API_KEY` is the only sensitive variable. It is never logged by TRW tools.

### `.trw/config.yaml`

- Contains project configuration only, not secrets. Safe to commit to version control.
- Permissions: readable by project collaborators. No special file mode required.

### `.trw/` Directory

- The `.trw/.gitignore` excludes transient files (logs, memory database, PID files) from version control.
- Learnings (`learnings/`) are committed -- they contain project knowledge, not secrets.
- Logs (`logs/`) are gitignored -- they may contain debug-level information.

### `.mcp.json`

- May contain API keys for other MCP servers. Do NOT commit to version control if it includes third-party secrets.
- Add `.mcp.json` to `.gitignore` if your configuration includes sensitive values.
- Use `.mcp.json.example` as a committed template instead.
- The `_check_mcp_json_portability()` diagnostic intentionally does NOT log file contents.
