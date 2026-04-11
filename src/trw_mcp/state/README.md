# state/ — TRW MCP State Management

71 modules organized into 6 sub-packages and ~38 top-level modules.

## Sub-Packages

| Package | Modules | Purpose |
|---------|---------|---------|
| `analytics/` | 7 | Session analytics, run scanning, dedup, counters |
| `claude_md/` | 4 | CLAUDE.md generation, section management, promotion |
| `consolidation/` | 3 | Memory consolidation pipeline (trw-memory facade) |
| `memory/` | 5 | Hybrid memory model definitions |
| `validation/` | 14 | PRD quality gates, phase gates, scoring, progression |

## Top-Level Module Ownership Map

### Core Persistence
| Module | Lines | Owner | Purpose |
|--------|-------|-------|---------|
| `persistence.py` | 516 | Primary | Atomic YAML/JSONL I/O with portable file locking |
| `_paths.py` | 320 | Primary | Project root, .trw dir, run path resolution, session identity |
| `_helpers.py` | — | Support | Backward-compat shims, config loading helpers |
| `_constants.py` | — | Support | Shared constants |

### Learning & Memory
| Module | Lines | Owner | Purpose |
|--------|-------|-------|---------|
| `memory_adapter.py` | 459 | **Facade** | Public CRUD for learning storage; delegates to trw-memory |
| `_memory_connection.py` | 404 | Internal | Singleton backend lifecycle, embedder management |
| `_memory_queries.py` | — | Internal | Keyword/hybrid search routing |
| `_memory_transforms.py` | — | Internal | Format conversions between trw-mcp and trw-memory |
| `memory_store.py` | — | Internal | Low-level store operations |
| `dedup.py` | 384 | Primary | Semantic deduplication (cosine similarity, batch dedup) |
| `recall_search.py` | — | Primary | Recall search strategies |
| `recall_tracking.py` | — | Primary | Recall outcome tracking |
| `retrieval.py` | — | Primary | Hybrid retrieval orchestration |
| `learning_injection.py` | — | Primary | Agent learning injection (CORE-075) |

### Tier Lifecycle
| Module | Lines | Owner | Purpose |
|--------|-------|-------|---------|
| `tiers.py` | 582 | **Primary** | Hot/Warm/Cold tier manager (TierManager class) |
| `_tier_sweep.py` | 299 | Internal | Promotion/demotion algorithms |
| `_tier_scoring.py` | — | Internal | Importance score computation |
| `trust.py` | — | Primary | Progressive trust model |

### Ceremony & Nudges
| Module | Lines | Owner | Purpose |
|--------|-------|-------|---------|
| `ceremony_feedback.py` | 686 | Primary | Self-improving ceremony feedback loop (CORE-069) |
| `ceremony_progress.py` | — | Public | Live ceremony progress API for tool state reads/writes |
| `ceremony_nudge.py` | — | Legacy facade | Archived nudge orchestration (kept for backward compatibility/tests) |
| `_nudge_state.py` | — | Internal | CeremonyState, NudgeContext data models |
| `_nudge_messages.py` | 401 | Internal | Nudge message templates and formatting |
| `_nudge_rules.py` | — | Internal | Nudge decision logic (when to show which nudge) |

### Analytics & Reporting
| Module | Lines | Owner | Purpose |
|--------|-------|-------|---------|
| `dashboard.py` | 446 | Primary | Quality dashboard aggregation |
| `index_sync.py` | 450 | Primary | INDEX.md / ROADMAP.md synchronization |
| `report.py` | — | Primary | Run report generation |
| `reflection.py` | — | Primary | Session reflection and pattern discovery |

### Infrastructure
| Module | Lines | Owner | Purpose |
|--------|-------|-------|---------|
| `prd_utils.py` | 457 | Primary | PRD frontmatter parsing, file discovery |
| `knowledge_topology.py` | 543 | Primary | Tag-based knowledge clustering (CORE-021) |
| `semantic_checks.py` | — | Primary | Rubric-based semantic validation |
| `phase.py` | — | Primary | Phase tracking and transitions |
| `auto_upgrade.py` | — | Primary | Schema auto-migration |
| `dry_check.py` | — | Primary | DRY violation scanning |
| `usage_profiler.py` | — | Primary | Tool usage profiling for progressive disclosure |
| `progressive_middleware.py` | — | Primary | Progressive disclosure middleware state |
| `receipts.py` | — | Primary | Recall receipt tracking |
| `llm_helpers.py` | — | Primary | LLM client facade |
| `otel_wrapper.py` | — | Primary | OpenTelemetry integration |

## Dependency Direction

```
tools/ ──imports──▶ state/ ──imports──▶ models/
                                        config/
```

State modules NEVER import from `tools/`. This layering is enforced.
