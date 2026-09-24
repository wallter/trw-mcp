# trw-mcp

**Persistent engineering memory for AI coding agents** — an MCP server for cross-session recall, evidence-backed delivery, and spec-driven development. Part of [TRW Framework](https://trwframework.com).

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License: BSL 1.1](https://img.shields.io/badge/License-BSL_1.1-orange.svg)](https://trwframework.com/license)
[![MCP](https://img.shields.io/badge/MCP-compatible-green)](https://modelcontextprotocol.io/)
[![Docs](https://img.shields.io/badge/docs-trwframework.com-blue)](https://trwframework.com/docs)

> **Release status:** Alpha and source-available under BSL 1.1. The current
> package is suitable for evaluation and dogfooding, but it does not claim a
> production-stable API or support SLA.

> Coding-agent sessions are usually stateless. TRW keeps each project's run state in `.trw/`, stores learnings in one memory store on your machine, and recalls relevant learnings when the next session starts.

**[What's new in 6.0.0](#whats-new-in-600)** · **[Upgrading from 5.x](#upgrading-from-5x)** · **[Quick start](#quick-start)** · **[Core tools](#mcp-tools)** · **[Configuration](#configuration)** · **[Security and network behavior](#telemetry--network-behavior)** · **[Development](#development)**

## What's new in 6.0.0

trw-mcp 6.0.0 and trw-memory 3.0.0 were released together on 2026-09-24. What changes for you:

- **One memory store per machine.** The memory daemon serves every checkout from one store under `~/.trw` (by default `~/.trw/memory/memory.db`). Each checkout reads and writes its own namespace (`project_namespace` in `.trw/config.yaml`) through its own grant (`.trw/runtime/memory-token`). trw-mcp no longer serves memory from, or writes to, a checkout's `.trw/memory/memory.db`.
- **An explicit migration.** `trw-mcp memory migrate --to user` previews, `--apply` moves a checkout's existing learnings into the daemon (with a backup and a manifest), and `--rollback MANIFEST` undoes it. Nothing migrates on its own.
- **Portable learnings go to `user:local` on every install.** There is no user-tier opt-in any more, and team sync never pushes `user:local` rows.
- **Recall runs in the daemon.** trw-mcp no longer loads its own embedding model for recall. The first recall after the daemon starts pays the model load (about 10 s on a cold disk cache in one measurement), later recalls take about 0.25 s.
- **Ranking no longer uses reward feedback.** trw-memory 3.0.0 removed the Q-value blend; a learning's base impact now decays with how often it has been recalled, so the same store can rank differently after the upgrade.
- **`trw_decision` is now `trw_assess`**, with no alias. The skill is now `trw-assess` and the config key is now `assess_enabled`.
- **`trw_learn_update` is removed.** Call `trw_learn(learning_id=...)` to update a learning.
- **Less common tool parameters moved into `options`** on `trw_recall`, `trw_build_check` and `trw_review`.
- **`trw_session_start` runs one recall** and returns at most three short stubs; `trw_recall(ids=[...])` fetches any stub's full row.
- **Memory security settings are daemon-wide.** RBAC, the recall filter, canary, poisoning, trust-scoring and provenance settings come from the daemon's environment, and a trw-mcp process whose values differ is refused with the key named.
- **Uninstall keeps `~/.trw`.** `uninstall --delete-memory` (which replaces `--user-tier`) deletes only this checkout's own namespace.
- **`init-project --ide <client>` without Claude Code no longer writes `.mcp.json` or the Claude Code skills and agents** (Codex and Copilot still get `.claude/hooks`, which their hook commands run), and `trw-mcp sync pull --full` replays every team learning into a checkout.

The full list, including every removed API, is in the [CHANGELOG](https://github.com/wallter/trw-mcp/blob/main/CHANGELOG.md).

## Upgrading from 5.x

Do these steps in order in each checkout.

1. **Install and update the project.** Use your client's id for `--ide` (`antigravity-cli`, `claude-code`, `codex`, `copilot`, `cursor-cli`, `cursor-ide`, `grok`, `opencode`), or `--ide all`:

   ```bash
   pip install -U "trw-mcp==6.0.0" "trw-memory==3.0.0"
   trw-mcp update-project --ide <id>    # add --dry-run to preview
   ```

   If the checkout's `.trw/memory/memory.db` holds no learnings, `update-project` pins `project_namespace` and mints the checkout's grant, and you are done with memory.
2. **If `update-project` prints `trw-mcp memory migrate --to user --apply`, run it from the checkout.** Until you do, memory tools and `trw-mcp doctor` fail closed with that same command in the message.
   - Without `--apply` the command only previews: row counts per namespace, and which ids the daemon already holds. It writes nothing.
   - `--apply` needs the daemon running. It writes a backup (`.trw/memory/memory.db.pre-user-<stamp>`) and a manifest (`migration-<stamp>.json`), merges the rows into the daemon in one transaction, and checks the row, vector and edge counts before it pins `project_namespace`. `busy` means nothing changed: retry. `uncertain` means rerun it; the import is idempotent.
   - To roll back, stop the daemon and run `trw-mcp memory migrate --to user --rollback <manifest>`. It rebuilds the project store and removes the pin. The backup is kept.
3. **Reconnect every MCP client** (`/mcp` in Claude Code; restart the session elsewhere). A client started before the upgrade keeps running 5.x code until it restarts.
4. **Set the memory security settings in the daemon's environment**, not per project: `rbac_enabled`, `default_role`, `namespace_roles`, `enable_recall_filter`, `recall_filter_mode`, `canary_fail_mode`, `poisoning_detection_mode`, `enable_trust_scoring`, `trust_scoring_mode` and `provenance_required` (as `MEMORY_*` variables). A per-project value that differs from the daemon's now refuses the store.

Breaking changes you are likely to hit:

- **Renamed or removed tools.** `trw_decision` is an unknown tool: call `trw_assess`. `trw_learn_update` is an unknown tool: call `trw_learn(learning_id=...)`. `status`, `summary`, `detail`, `impact`, `tags`, `type` and `confidence` stay top-level; `supersedes`, `reverify_anchors`, `expires` and `team_origin` go in `metadata`.
- **The `options` mapping.** A flat name that moved is an unknown-keyword error, and an unknown `options` key is rejected with the accepted set:

  | Tool | Stays top-level | Moves into `options` |
  |------|-----------------|----------------------|
  | `trw_recall` | `query`, `tags`, `status`, `max_results`, `ids` | `min_impact`, `topic`, `include_tiers`, `as_of`, `include_superseded` (`compact`, `ultra_compact` and `token_budget` are removed) |
  | `trw_build_check` | `tests_passed`, `test_count`, `failure_count`, `coverage_pct`, `static_checks_clean`, `scope` | `mypy_clean`, `failures`, `run_path`, `min_coverage`, `command_results` |
  | `trw_review` | `findings`, `mode`, `reviewer_findings`, `reviewer_identity`, `review_completed` | `run_path`, `prd_ids`, `external_receipt_path`, `adversarial_pass` |

- **Removed flags.** `trw-mcp --memory-db`, `init-project --source-package` and `--test-path`, `update-project --repair-embeddings` and `--embedding-after`, and the installer's `--user-tier` / `--no-user-tier` flags and `TRW_USER_TIER`. `uninstall --user-tier` is now `uninstall --delete-memory`.
- **Retired config keys.** A `.trw/config.yaml` that still sets one logs a retired-key warning and the value is ignored. Among them: `decision_enabled` (use `assess_enabled`), `user_tier_enabled`, `extra_read_stores`, `external_store_recall_cap`, `observation_masking`, `compact_after_turns`, `minimal_after_turns`, `hybrid_bm25_candidates`, `hybrid_vector_candidates`, `hybrid_search_candidate_pool_size`, `llm_utility_filter_enabled`, `lifecycle_use_fsrs`, `contradiction_penalty_reward` and `embeddings_auto_backfill_on_low_coverage`. The full list is [`src/trw_mcp/data/config-retired-keys.json`](src/trw_mcp/data/config-retired-keys.json).
- **Merged agents.** `trw-tester` is now part of `trw-implementer`, `trw-requirement-writer` of `trw-prd-groomer`, and `trw-traceability-checker` of `trw-auditor`. Point custom prompts at the new names.
- **Hooks from an older install stop seeing deliveries** because the run-log row is now `tool_call`. `update-project` refreshes them.
- **`update-project` refuses a missing or invalid `.trw/managed-artifacts.yaml`** and changes nothing. Recover with `trw-mcp uninstall --keep-memory`, then `trw-mcp init-project`.
- **Package versions are recorded under `packages` in `.trw/managed-artifacts.yaml`**, no longer in `.trw/frameworks/VERSION.yaml`.
- **A `.trw/channels/manifest.yaml` entry with `tier_default` or `tier_min` fails to load.** Delete those keys, or delete the file and run `update-project`.
- **A moved checkout needs its grant named:** `trw-mcp memory token --namespace <pinned namespace>`.
- **Rows without a vector are found by keyword only** until a daemon-side re-embed pass exists; `trw-memory reembed` refuses while a daemon runs.
- **The `trw-memory` CLI has no local mode.** See the [trw-memory README](https://github.com/wallter/trw-memory) for its changes.

## How it fits

trw-mcp is the MCP server component of [TRW (The Real Work)](https://trwframework.com) — a methodology layer for AI-assisted development that turns each coding session's discoveries into permanent institutional knowledge. It works alongside [trw-memory](https://github.com/wallter/trw-memory), the standalone memory engine.

- **trw-mcp** (this repo): MCP server with <!-- inv:tools -->48<!-- /inv --> tools, <!-- inv:skills -->26<!-- /inv --> skills, <!-- inv:agents -->8<!-- /inv --> agents
- **[trw-memory](https://github.com/wallter/trw-memory)**: Standalone memory engine with hybrid retrieval, scoring, and lifecycle

## What it does

trw-mcp is a [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI coding agents **persistent engineering memory**. It records what you learn during development sessions (patterns, gotchas, architecture decisions) and recalls relevant knowledge at the start of every new session. Over time, your AI coding assistant **accumulates captured learnings** in the memory store and recalls them at session start. *Whether this yields measurable task-completion lift is an open empirical question; early SWE-bench single-shot measurements (n=40/47) showed null. See the [verification docs](https://trwframework.com/docs/verification) for the current methodology and evidence posture.*

Beyond memory, the server provides:

- **Run lifecycle** — phases, checkpoints, events, resumable state, and delivery records.
- **Verification gates** — project-native build evidence and structured review/delivery checks.
- **Requirements workflows** — [AARE-F PRDs](https://trwframework.com/docs), validation, and requirement-to-code traceability.
- **Client integration** — generated instruction files, hooks, skills, and capability-aware tool exposure for supported coding clients.
- **Code intelligence** — lexical/symbol search, before-edit context, dependency relationships, and risk signals.

**Dogfooding scale**: thousands of tests across hundreds of PRDs, dogfooded across the TRW monorepo (coverage gate enforced at 80%, 90% target for new code). This codebase was built by AI agents using TRW. *Scale proves the framework is usable at volume; whether it improves outcomes vs baseline is measured via the eval bench, not inferred from these counts.*

## Quick Start

Requires Python 3.10+ and a Git repository. The installer supports Claude Code, Codex, Cursor, OpenCode, Copilot, Grok, and Antigravity; use `--ide all` when a repository is shared across clients. See the [full quickstart guide](https://trwframework.com/docs/quickstart) for client-specific setup.

```bash
# Recommended: install TRW
curl -fsSL https://trwframework.com/install.sh | bash

# Bootstrap the current repository (client is auto-detected)
cd /path/to/your/repo
trw-mcp init-project .

# Confirm the installation and resolved client surfaces
trw-mcp doctor .
```

### Manual / advanced install

```bash
# Install from PyPI. sqlite-vec is a base dependency, so vector storage works out of the box.
# Semantic recall also needs the embedding model: trw-memory[embeddings] pulls
# sentence-transformers and torch (several hundred MB). install-trw.py adds it by default.
pip install trw-mcp
pip install 'trw-memory[embeddings]'

# Or install from source
git clone https://github.com/wallter/trw-mcp.git
cd trw-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Supported platforms and interpreters

**Supported platforms:** macOS arm64/x86_64, manylinux x86_64/aarch64 and Windows x86_64 -- the platforms `sqlite-vec` (a base dependency) publishes wheels for. musl Linux (Alpine) and Windows ARM are unsupported: `sqlite-vec` has no wheel or sdist there, so `pip install` fails.


`trw-mcp` is tested on CPython 3.10 through 3.14 (this repository's own development
interpreter is CPython 3.14.7). The interpreter's bundled SQLite matters too: the memory
store only RECLAIMS WAL space on SQLite >= 3.51.3 (or the 3.44.6 / 3.50.7 backports). Below
that, checkpoints still run and the store is safe, but the `-wal` file grows without
shrinking — `trw-mcp doctor`'s `memory_wal` row reports the engine in use and names the
interpreters on your PATH that would qualify. The driver is selected by `trw-memory` at
import, ranking the interpreter's SQLite against an installed `pysqlite3` so an older wheel
can never replace a newer engine. The optional `[sqlite-fix]` extra pulls `pysqlite3-binary`
on x86_64 Linux only; no published wheel currently bundles a qualifying SQLite.

### Deploy to a Project

`trw-mcp init-project` bootstraps the full TRW framework in any git repository. Full configuration reference at [trwframework.com/docs/config](https://trwframework.com/docs/config).

```bash
trw-mcp init-project .              # current directory
trw-mcp init-project /path/to/repo  # specific project
trw-mcp init-project . --ide codex  # force Codex bootstrap
trw-mcp init-project . --force      # overwrite existing files
```

Every installation creates `.trw/`, pins the checkout's memory namespace (`project_namespace`) and mints its memory grant. The Claude Code files (`.mcp.json` and `.claude/` hooks, skills, and agent definitions) are written only when `claude-code` is one of the selected clients; a bare `init-project` includes it, and `--ide codex` or `--ide copilot` also get `.claude/hooks`, which their hook commands run. Each selected client integration adds its own instruction, MCP, hook, skill, and agent surfaces where supported. Bundled skills and agent definitions are runtime inputs to `init-project` and `update-project`, not examples that can be discarded. Managed updates preserve user-authored content where the target format supports safe merging; review `--force` before using it in a customized repository.

### Configuration

Settings via environment variables (prefix `TRW_`) or `.trw/config.yaml`. Full reference at [trwframework.com/docs/config](https://trwframework.com/docs/config).

```yaml
# .trw/config.yaml — top settings (all optional, shown with defaults)
embeddings_enabled: true           # trw-mcp's own embedding model (learn-time dedup); needs trw-memory[embeddings]
learning_max_entries: 500          # Max learnings before auto-pruning
build_check_enabled: true          # Record build/test results with trw_build_check (it runs nothing itself)
deliver_gate_mode: "block_coding"  # Block delivery for coding/rca/eval tasks without a passing build record;
                                   # set to "advisory" to restore warn-only posture (changed 2026-06-10)
ceremony_mode: "full"              # "full" or "light"
```

## Telemetry & network behavior

trw-mcp is **local-first**: with the default configuration it persists everything under your project's `.trw/` directory and makes **no outbound network calls** except the optional embedding-model download described below. There is no built-in usage tracking, phone-home, or content upload unless you explicitly enable it.

### What can touch the network, when, and how to turn it off

| Surface | When | Default | Opt-out / control |
|---------|------|---------|-------------------|
| **Embedding model download** | Only when the configured embedding model (default `BAAI/bge-small-en-v1.5`) is **not** already complete in your local Hugging Face cache. A complete cached snapshot makes **zero** huggingface.co requests: the loader probes the cache first and forces `local_files_only=True`. Only relevant when `trw-memory[embeddings]` is installed. trw-mcp loads the model on first use for learn-time dedup, recall-result dedup, consolidation and telemetry publishing; recall itself runs in the memory daemon, which loads its own copy under trw-memory's rules | `embeddings_enabled: true` | `TRW_OFFLINE=1` (or `HF_HUB_OFFLINE=1`) suppresses the fetch; populate the cache first if you want semantic recall offline. A disclosure log line is emitted before any fetch |
| **Re-ranker model download** | Only in the memory daemon, which runs `trw_recall` and `trw_session_start` recalls with the same ranking as trw-memory's `MemoryClient.recall()`, including the cross-encoder re-ranker (`cross-encoder/ms-marco-MiniLM-L-6-v2`), and only when `trw-memory[embeddings]` is installed and the model is not cached | on when the extra is present | the same offline switches; an uncached re-ranker is skipped and recall keeps fusion order. See the [trw-memory README](https://github.com/wallter/trw-memory) |
| **Usage telemetry** | Only if explicitly enabled | **off** (gated by `platform_telemetry_enabled`, default `false`) | leave `platform_telemetry_enabled=false`; see PRD-SEC-004 |
| **Learning-content publishing** | Only if explicitly enabled | **off** (gated by `learning_sharing_enabled`, default `false`) | leave `learning_sharing_enabled=false`; learning content is never published off-box by default |

With `TRW_OFFLINE=1` set, `session_start` makes **zero** huggingface.co calls — a testable invariant for air-gapped deployments. Since the cache-first resolution landed, a **warm cache reaches the same zero-call result with no switch set at all**.

**Embedding egress is independent of the consent flags.** `learning_sharing_enabled` and `platform_telemetry_enabled` govern learning-content publishing and usage telemetry only; neither one gates the model fetch. What governs embedding egress is the local cache plus the offline switches (`TRW_OFFLINE` / `HF_HUB_OFFLINE`) and trw-memory's `local_only`. Run `trw-mcp doctor` to read the current state — its `embedding_egress` row reports the cache state (`complete`/`incomplete`/`absent`) and the effective posture (`cache-first`, `offline-forced`, or `network-capable`).

Loading a model that ships its own Python modules is refused unless you set trw-memory's `embedding_trust_remote_code: true`; the shipped default model does not need it.

### Cross-client messaging and dispatch

Peer messaging is on by default. To also enable dispatch, merge these
**top-level** keys into this project's `.trw/config.yaml` (do not replace your
other settings):

```yaml
dispatch_tools_exposed: true
dispatch_child_trw_access: true
```

- `comms_enabled` (default `true`) exposes peer enrollment, sending and inbox
  operations (`trw_peers`, `trw_send`, `trw_inbox`). Outside a formation they
  refuse and create no state; only a formation member can enroll or exchange
  messages. Messaging is pull-based: a message does not wake an idle agent or
  guarantee when it will read the inbox. Set `comms_enabled: false` to hide the
  three tools.
- `dispatch_tools_exposed` (default `false`) advertises the dispatch tool pack. Dispatch launches
  another installed agent client; exposing the tools does not install that client
  or supply its credentials.
- `dispatch_child_trw_access` (default `false`) gives supported dispatched children only TRW's own
  stdio MCP connection. It does not import host hooks or other client configuration.
  For clients without an MCP argv channel, this config default falls back to no
  TRW access; an explicit per-call `--with-trw` / `with_trw=True` request is refused.
  Reviewer posture has its own restricted TRW connection and cannot be combined
  with `with_trw=True`. Nested-launch guards remain in force.

Restart each client's TRW MCP connection, or start a new client session, after
changing configuration: an already-running server caches its settings. Environment
variables such as `TRW_COMMS_ENABLED` override YAML; project settings override
`~/.trw/config.yaml`. Set `TRW_CONFIG_STRICT=1` in the server's environment to fail
closed on invalid configuration rather than falling back with a warning.

To opt out again, set the corresponding keys to `false`, remove any conflicting
environment overrides, and restart the connections. These switches do not grant
permission to modify files, bypass review gates, or treat peer messages as trusted
instructions.

### Environment-variable inventory

| Variable | Purpose | Default |
|----------|---------|---------|
| `TRW_OFFLINE` | Master offline switch — blocks the huggingface.co embedding-model download | unset (online) |
| `HF_HUB_OFFLINE` | Upstream huggingface_hub offline switch — also honored by trw-mcp | unset |
| `TRW_PROBE_ENABLED` | Enables the optional sandboxed `trw_probe` experiment tool | unset (probe disabled) |
| `ENABLE_TOOL_SEARCH` | Force-enable/disable MCP tool-search auto-deferral (`true`/`false`) | auto-detected |
| `TRW_LOG_LEVEL` | Explicit log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`) | derived from `--debug` / defaults |
| `TRW_PLATFORM_API_KEY` | Platform credential (PRD-SEC-005) — read from the environment, kept out of git-tracked config | unset |
| `TRW_CONFIG_STRICT` | Fail **closed** on a malformed `.trw/config.yaml` instead of reverting to defaults | unset (fail-open, but loud) |
| `MEMORY_*` | trw-memory engine knobs (see the [trw-memory README](https://github.com/wallter/trw-memory)). The memory security settings are daemon-wide: set them in the environment the daemon starts from | per-field |

A malformed `.trw/config.yaml` always emits a `WARNING` (and a stderr notice) rather than being silently discarded; set `TRW_CONFIG_STRICT=1` to make the load fail closed so security overrides are never dropped unnoticed.

### Security defaults

| Capability | Default | Notes |
|-----------|---------|-------|
| Encryption at rest (SQLCipher) | **off** | opt-in via trw-memory `encryption_enabled` |
| Secret redaction in logs | **on** | API keys, tokens, and secret-named fields are masked in log output by default |
| PII detection (memory content) | **warn** | PII (emails, API keys, etc.) is detected and logged but stored as-is by default (`pii_action: warn`); set `pii_action: block` to reject such writes, or `redact` to mask them |
| Recall output filtering | **redact** | SEC-001 recall filter masks flagged values returned by recall (`recall_filter_mode: redact`) |
| Memory poisoning detection | **observe** | detects and records statistical anomalies, does not quarantine, by default |
| Remote sync / publishing | **off** | `learning_sharing_enabled=false`, `platform_telemetry_enabled=false` |
| `.trw/` directory permissions | `0700` | state/secret dirs are owner-only |
| `memory.db` / secret files | `0600` | owner read/write only (consistent with `pins.json`) |

Since 6.0.0 the recall-filter and poisoning-detection settings (with RBAC, canary, trust-scoring and provenance) are daemon-wide: one memory daemon serves every checkout on the machine, so set them in the environment the daemon starts from. A trw-mcp process that resolves a different value is refused and told which `MEMORY_` variable to set.

### Enterprise hardening recipe

For an air-gapped or compliance-sensitive deployment:

```bash
export TRW_OFFLINE=1            # no huggingface.co egress; pre-populate the model cache for semantic recall
export TRW_CONFIG_STRICT=1      # malformed config fails closed, never silently reverts
# Leave telemetry + learning-sharing at their secure defaults:
#   platform_telemetry_enabled: false
#   learning_sharing_enabled:   false
```

Then verify: `.trw/` dirs are `0700`, the daemon's store (`~/.trw/memory/memory.db` by default) is `0600`, and no outbound connection is attempted at `session_start`.

<a id="mcp-tools"></a>

## MCP Tools (<!-- inv:tools -->48<!-- /inv -->)

The table below covers the most-used tools out of the full <!-- inv:tools -->48<!-- /inv -->. For the complete, always-current list run `trw-mcp config-reference` or browse the [tool reference docs](https://trwframework.com/docs).

| Category | Tools | Purpose |
|----------|-------|---------|
| **Session** | `session_start`, `init`, `status`, `checkpoint`, `pre_compact_checkpoint`, `heartbeat`, `adopt_run` | Run lifecycle, progress tracking, and pin/liveness management |
| **Learning** | `learn`, `recall`, `instructions_sync` | Knowledge capture, retrieval, and instruction-file refresh |
| **Quality** | `build_check`, `review`, `deliver` | Verification and delivery |
| **Requirements** | `prd_create`, `prd_validate`, `prd_diff` | [Spec-driven development](https://trwframework.com/docs) with AARE-F PRDs |
| **Code intelligence** | `code_search`, `code_symbol`, `code_index_update`, `before_edit_hint`, `before_edit_hint_batch`, `codebase_risk_report` | Repo-aware search, symbol lookup, and risk signals |
| **Observability** | `query_events`, `surface_diff`, `mcp_security_status` | Event history, surface diffs, and security status |

## Skills (<!-- inv:skills -->26<!-- /inv -->)

Slash-command workflows — zero tokens until triggered. Full skill reference at [trwframework.com/docs](https://trwframework.com/docs).

**Sprint & Delivery**: `/trw-sprint-init` · `/trw-sprint-finish` · `/trw-deliver` · `/trw-commit` · `/trw-reflect`

**Requirements**: `/trw-prd-new` · `/trw-prd-ready` · `/trw-prd-groom` · `/trw-prd-review` · `/trw-exec-plan`

**Quality**: `/trw-audit` · `/trw-self-review` · `/trw-delegate` · `/trw-dry-check` · `/trw-security-check` · `/trw-test-strategy`

**Framework**: `/trw-framework-check` · `/trw-project-health` · `/trw-memory-audit` · `/trw-memory-optimize`

## Agents (<!-- inv:agents -->8<!-- /inv -->)

Optional specialized agent definitions for clients and harnesses that support delegation. TRW does not require multi-agent execution; the same lifecycle works sequentially.

| Role | Agent | Purpose |
|------|-------|---------|
| **Core Team** | trw-lead, trw-implementer, trw-researcher, trw-reviewer, trw-auditor, trw-adversarial-auditor | Orchestration, TDD + test authoring, research, review, spec-vs-code audit (incl. traceability), adversarial audit |
| **Requirements** | trw-prd-groomer, trw-requirement-reviewer | PRD lifecycle specialists |

## The 6-Phase Model

TRW implements a structured execution lifecycle: **RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER** with phase gates, build checks, adversarial audits, and delivery ceremony. See [FRAMEWORK.md](FRAMEWORK.md) for the full specification, or read the [lifecycle overview at trwframework.com/docs/lifecycle](https://trwframework.com/docs/lifecycle).

## CLI Commands

```bash
trw-mcp init-project .                # Deploy TRW to a project
trw-mcp update-project .              # Update existing installation
trw-mcp doctor .                      # Diagnose environment and client setup
trw-mcp check-instructions .          # Validate instruction-tool parity (exit 1 on mismatch)
trw-mcp audit .                       # Audit TRW configuration
trw-mcp config-reference              # Print all TRW_ environment variables
trw-mcp version-status                # Compare package, framework, and live-server versions
trw-mcp memory migrate --to user      # Preview moving a checkout's old project store into the daemon (--apply to move)
trw-mcp memory token                  # Mint this checkout's memory grant
trw-mcp export --format json          # Export learnings
trw-mcp uninstall .                   # Remove TRW from a project (keeps ~/.trw)
```

### Headless Antigravity reviews

Use TRW's dispatcher rather than invoking `agy -p` directly:

```bash
trw-mcp dispatch --client agy --cwd /path/to/repo \
  --prompt-file /path/to/review.txt --no-with-trw --json --verify-sandbox
```

Select an installed model with `--model` if needed. Headless Antigravity can
exit zero without doing a review when its tool permissions require a prompt.
TRW classifies that empty/denied result as unsuccessful; inspect `ok`,
`silence_reason`, and `sandbox_verified`, not only the child exit code.

On macOS, TRW pairs its headless read permission allowance with a host
`sandbox-exec` filesystem-write denial. Do not copy the allowance into a raw
CLI invocation or disable permissions globally. Without the host wrapper, TRW
withholds that allowance. `--verify-sandbox` costs an additional model call and
checks file reads and attempted writes in a disposable fixture.

This supports file-reading reviews, not unrestricted shell-based testing:
Antigravity commands that initialize helper files can fail under write denial.
The bound does **not** isolate network access or the client's existing MCP
servers. Antigravity does not support TRW's enforced reviewer MCP posture or
explicit child TRW injection; `--no-with-trw` prevents requesting injection,
not loading the client's own configured servers. A review role prompt is not
an additional security boundary.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v --cov=trw_mcp --cov-report=term-missing

# Type checking (strict mode)
mypy --strict src/trw_mcp/

# Targeted testing during development
pytest tests/test_tools_learning.py -k "test_recall" -v
```

## Architecture

```
src/trw_mcp/
  server/             # FastMCP entry point, middleware chain
  bootstrap/          # init-project: deploy TRW to target repos
  models/             # Pydantic v2 models (config, run, learning, etc.)
  tools/              # MCP tool implementations
  state/              # State management (persistence, validation, analytics)
  middleware/         # FastMCP middleware (ceremony, response optimizer)
  telemetry/          # Telemetry pipeline (models, sender, anonymizer)
  data/               # Bundled hooks, skills, agents for init-project
```

## Troubleshooting

**MCP connection error: "[Errno 2] No such file or directory"**
The MCP server process crashed. In Claude Code, type `/mcp` to reconnect. For other clients, restart your CLI tool.

**`trw_session_start()` returns "No learnings found"**
This is normal on first use: learnings accumulate as you work. Call `trw_learn()` to record a discovery; it is stored when the call returns.

**stale `.trw/` state after upgrading**
Run `trw-mcp update-project .` to migrate your project state to the latest schema. If it prints `trw-mcp memory migrate --to user --apply`, run that too (see [Upgrading from 5.x](#upgrading-from-5x)). If `update-project` refuses because `.trw/managed-artifacts.yaml` is missing or invalid, run `trw-mcp uninstall --keep-memory` and then `trw-mcp init-project`.

**Recall is keyword-only despite `embeddings_enabled=true`**
Semantic recall needs sqlite-vec (a base dependency since 6.1.0) and the embedding model from `trw-memory[embeddings]`, in the environment trw-mcp runs from; the memory daemon starts with the same interpreter. `trw-mcp doctor` reports which one is missing. Install `trw-memory[embeddings]` (install-trw.py does this by default since 6.1.0), stop the memory daemon (send SIGTERM to the pid in `daemon.json` beside the store) so the next call starts a fresh one, and reconnect the MCP client.

### Debugging

Enable debug logging:

```bash
trw-mcp --debug serve              # Debug mode with file logging
TRW_LOG_LEVEL=DEBUG trw-mcp serve  # Via environment variable
```

Logs are written to `.trw/logs/trw-mcp-YYYY-MM-DD.jsonl`.

## License

[Business Source License 1.1](https://trwframework.com/license) — source-available, free for non-competing use. Converts to Apache 2.0 on 2030-03-21. See the [full license terms](https://trwframework.com/license).

---

Built by [Tyler Wall](http://tylerrwall.com) · [TRW Framework](https://trwframework.com) · [Documentation](https://trwframework.com/docs) · [License](https://trwframework.com/license)
