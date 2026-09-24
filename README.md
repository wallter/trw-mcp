# trw-mcp

**Give your coding agent a memory that carries over from one session to the next.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License: BSL 1.1](https://img.shields.io/badge/License-BSL_1.1-orange.svg)](https://trwframework.com/license)
[![MCP](https://img.shields.io/badge/MCP-compatible-green)](https://modelcontextprotocol.io/)
[![Docs](https://img.shields.io/badge/docs-trwframework.com-blue)](https://trwframework.com/docs)

trw-mcp is a local [MCP](https://modelcontextprotocol.io/) server for the coding agent you already use. Your agent writes down what it learns in a repository (patterns, gotchas, decisions) and gets the relevant pieces back when the next session starts. It also keeps run state and delivery checks, so work can be picked up where it stopped. One installer sets it up for Claude Code, Codex, Cursor, GitHub Copilot CLI, OpenCode, Antigravity CLI and Grok Build CLI.

Local-first · MCP-native · source-available (BSL 1.1)

**[Quick start](#quick-start)** · **[What's new in 6.x](#whats-new-in-6x)** · **[Upgrading](#upgrading)** · **[Tools](#mcp-tools)** · **[Configuration](#configuration)** · **[Network and security](#network-and-security)** · **[Troubleshooting](#troubleshooting)**

## Why TRW

A new coding-agent session usually starts without what the last one learned. It rediscovers the same repository quirks, retries the approach that failed last week, and asks again why a file is laid out the way it is.

With trw-mcp, the agent records short learnings as it works, gets up to three relevant ones back at the start of the next session, and can search for more. Checkpoints let a later session, or a different agent, resume a run. Your learnings and run state stay on your machine: learnings in one local store, run state in each project's `.trw/` directory.

What sets it apart:

- **More than memory.** Recall, run state, build evidence, delivery checks and requirements tracing come from one server.
- **One setup for many clients.** The same install configures instructions, hooks, skills and agents for each supported client.
- **Local by design.** Memory lives in SQLite on your machine and runs without a cloud service.

## What you get

- **Memory across sessions.** `trw_learn` records a learning; `trw_session_start` recalls once at session start and returns up to three short stubs; `trw_recall` searches on demand.
- **Hybrid recall.** Every recall uses keyword search; vector search joins it when embeddings are installed, which the installer does by default.
- **One store for all your projects.** A local memory daemon (from trw-memory) serves one store per user account; each checkout reads and writes its own namespace.
- **Resumable runs.** Phases, checkpoints, events and delivery records live in the project's `.trw/` directory.
- **Delivery checks.** `trw_build_check` records the build and test results your agent ran (it runs nothing itself). By default `trw_deliver` refuses coding work with no passing build record.
- **Requirements workflow.** Write [AARE-F](https://trwframework.com/docs/requirements) PRDs, validate them, and trace requirements to code.
- **Client setup.** `trw-mcp init-project` writes each selected client's instruction file, MCP config, hooks, skills and agent definitions where that client supports them.

## Quick start

Inside the git repository you want to set up, run:

```bash
curl -fsSL https://trwframework.com/install.sh | bash
```

Sign in or create a free account in the browser when asked, and the installer does the rest: it installs trw-mcp and trw-memory with semantic recall, caches the recall model, sets up the project for your client and brings over learnings from an older install.

Installer flags go after `bash -s --`, for example `curl -fsSL https://trwframework.com/install.sh | bash -s -- --ide codex`; `--no-embeddings` and `--no-migrate` work the same way. `--allow-unauthenticated` skips the account and installs the trw-mcp package only, without embeddings. What a signed-in install connects to is listed under [Network and security](#network-and-security).

Or install with pip (no account; migration and model caching are then up to you):

```bash
pip install trw-mcp 'trw-memory[embeddings]'   # drop the second package for keyword-only recall
cd /path/to/your/repo
trw-mcp init-project .                        # add --ide <id> to choose clients
trw-mcp doctor .                              # checks the install, clients and retrieval
```

Reconnect your MCP client afterwards (`/mcp` in Claude Code; restart the session elsewhere).

**Supported clients** (`--ide` id): Antigravity CLI (`antigravity-cli`), Claude Code (`claude-code`), Codex (`codex`), GitHub Copilot CLI (`copilot`), Cursor CLI (`cursor-cli`), Cursor IDE (`cursor-ide`), Grok Build CLI (`grok`) and OpenCode (`opencode`). `--ide all` sets up every client, which suits a repository shared across tools. Without `--ide`, `init-project` detects the client. Per-client details: [trwframework.com/docs/clients](https://trwframework.com/docs/clients).

**Requirements:** Python 3.10 to 3.14 and a git repository, on macOS (arm64, x86_64), Linux with glibc (x86_64, aarch64) or Windows x86_64. Alpine and Windows ARM are not supported, because `sqlite-vec` publishes no wheel for them.

<sub>Alpha release: source-available under the Business Source License 1.1, free for any use except offering a competing commercial product, converting to Apache 2.0 on 2030-03-21. The API may still change.</sub>

## What's new in 6.x
<!-- whats-new: 6.1.0 -->

- **One memory store for all your projects.** A local daemon serves every checkout, each in its own namespace.
- **Semantic recall out of the box.** The installer adds embeddings and the recall model; every install has vector storage.
- **Your old store moves for you.** The installer migrates existing learnings, with a backup and a rollback command.
- **A leaner session start.** `trw_session_start` runs one recall and returns up to three short stubs.
- **Retrieval health at a glance.** `trw-mcp doctor` and `trw_session_start` report what recall is using, with a one-line fix.
- **Recall keeps working offline.** Without a cached model, recall falls back to keyword search instead of failing.
- **Fewer setup snags.** Hooks read their input without jq, and a hook, skill or agent you delete stays deleted.

Full list: [CHANGELOG.md](https://github.com/wallter/trw-mcp/blob/main/CHANGELOG.md)

## Upgrading

Re-run the installer in each checkout, then reconnect every MCP client (a client started before the upgrade keeps the old server until it restarts). The installer updates the project and, coming from 5.x, moves the checkout's learnings into your user store. With pip:

```bash
pip install -U trw-mcp 'trw-memory[embeddings]'
trw-mcp update-project --ide <id>      # add --dry-run to preview
trw-mcp memory migrate --to user       # 5.x only: preview, then add --apply
```

Coming from 5.x:

- Until an old store that still holds learnings is migrated, the memory tools and `trw-mcp doctor` stop and print the command. `--apply` backs up first; to undo, stop the daemon and run `trw-mcp memory migrate --to user --rollback <manifest>`.
- Migrated rows without a vector are found by keyword until a daemon-side re-embed pass exists.
- Memory security settings are daemon-wide: set them as `MEMORY_*` variables where the daemon starts (see [Security defaults](#security-defaults)).
- Renamed and moved: trw_decision is now `trw_assess` (config key `assess_enabled`); trw_learn_update is replaced by `trw_learn(learning_id=...)`; less common parameters of `trw_recall`, `trw_build_check` and `trw_review` moved into `options`; three agents merged into `trw-implementer`, `trw-prd-groomer` and `trw-auditor`; retired config keys log a warning ([list](https://github.com/wallter/trw-mcp/blob/main/src/trw_mcp/data/config-retired-keys.json)). The [6.0.0 CHANGELOG entry](https://github.com/wallter/trw-mcp/blob/main/CHANGELOG.md) lists every removed API and flag.

## How it works

- **Processes.** Each MCP client starts its own `trw-mcp` process over stdio. Memory calls go to one local memory daemon per user account, which the first memory call starts and which exits when idle.
- **Where data lives.** Run state and logs live in the project's `.trw/`; client files go where each client reads them. Learnings live in the daemon's store, `~/.trw/memory/memory.db` (or under `$TRW_USER_DIR` or `$XDG_DATA_HOME/trw` when set), under the checkout's `project_namespace`, reached through the checkout's grant (`.trw/runtime/memory-token`).
- **Tool surface.** A session sees the tools its task needs. The rest stay registered and can be listed with `trw_skill_discovery` or requested with `trw_request_tool_access`; `trw_profile_explain` shows why.
- **Lifecycle.** TRW's method runs work through RESEARCH, PLAN, IMPLEMENT, VALIDATE, REVIEW and DELIVER, with exit checks between phases. The protocol is in [FRAMEWORK.md](https://github.com/wallter/trw-mcp/blob/main/FRAMEWORK.md) and the [lifecycle docs](https://trwframework.com/docs/lifecycle).

<a id="mcp-tools"></a>

## MCP tools, skills and agents

trw-mcp exposes <!-- inv:tools -->48<!-- /inv --> tools. The most used:

| Area | Tools |
|------|-------|
| Session and runs | `trw_session_start`, `trw_init`, `trw_status`, `trw_checkpoint`, `trw_pre_compact_checkpoint`, `trw_heartbeat`, `trw_adopt_run` |
| Memory | `trw_learn`, `trw_recall`, `trw_graph_related`, `trw_instructions_sync` |
| Verification and delivery | `trw_build_check`, `trw_review`, `trw_deliver`, `trw_delivery_status` |
| Requirements | `trw_prd_create`, `trw_prd_validate`, `trw_prd_diff` |
| Code intelligence | `trw_code_search`, `trw_code_symbol`, `trw_code_index_update`, `trw_before_edit_hint`, `trw_codebase_risk_report` |
| Coordination (optional) | `trw_peers`, `trw_send`, `trw_inbox`, `trw_dispatch`, `trw_dispatch_status` |
| Surface and diagnostics | `trw_skill_discovery`, `trw_request_tool_access`, `trw_profile_explain`, `trw_query_events`, `trw_mcp_security_status` |
| Experimental, off by default | `trw_assess` |

The [tool reference](https://trwframework.com/docs/tools) covers the rest.

**Skills (<!-- inv:skills -->26<!-- /inv --> bundled).** Workflows the agent loads only when invoked; the invocation syntax depends on the client. The ones you invoke directly:

- Delivery: `/trw-deliver`, `/trw-commit`, `/trw-reflect`, `/trw-sprint-init`, `/trw-sprint-finish`
- Requirements: `/trw-prd-new`, `/trw-prd-ready`
- Review and quality: `/trw-audit`, `/trw-self-review`, `/trw-security-check`, `/trw-test-strategy`, `/trw-dry-check`, `/trw-delegate`, `/trw-plan-review`
- Memory: `/trw-learn`, `/trw-memory-audit`, `/trw-memory-optimize`
- Framework: `/trw-ceremony-guide`, `/trw-framework-check`, `/trw-project-health`, `/trw-code-search`, `/trw-feedback`

**Agents (<!-- inv:agents -->8<!-- /inv --> bundled).** Optional role definitions for clients that support delegation: trw-lead, trw-implementer, trw-researcher, trw-reviewer, trw-auditor, trw-adversarial-auditor, trw-prd-groomer and trw-requirement-reviewer. TRW does not need multiple agents; the same lifecycle works in one session.

## CLI

```bash
trw-mcp init-project .                # set up TRW in a project
trw-mcp update-project .              # refresh an existing installation
trw-mcp doctor .                      # check the environment, clients and retrieval
trw-mcp check-instructions .          # instruction-to-tool parity (exits 1 on mismatch)
trw-mcp audit .                       # audit the TRW configuration
trw-mcp config-reference              # list the TRW_ variables for config settings
trw-mcp version-status                # compare package, framework and live-server versions
trw-mcp memory migrate --to user      # preview moving an old checkout store (--apply to move)
trw-mcp memory token                  # mint this checkout's memory grant
trw-mcp export --scope learnings      # export learnings as JSON (--format csv also works)
trw-mcp uninstall .                   # remove TRW from a project; ~/.trw is kept
```

## Configuration

Settings come from `.trw/config.yaml`, then `~/.trw/config.yaml`, and `TRW_`-prefixed environment variables override both. Full reference: [trwframework.com/docs/config](https://trwframework.com/docs/config).

```yaml
# .trw/config.yaml (all optional, shown with their defaults)
embeddings_enabled: true           # trw-mcp's own embedding use (learn-time dedup); needs trw-memory[embeddings]
deliver_gate_mode: "block_coding"  # no passing build record blocks coding work or a run that changed files; "advisory" only warns
ceremony_mode: "full"              # or "light"
comms_enabled: true                # peer messaging tools (formation members only)
dispatch_tools_exposed: false      # tools that launch another installed agent client
assess_enabled: false              # the experimental trw_assess tool
```

A running server caches its settings, so reconnect the client after a change. A malformed `.trw/config.yaml` logs a warning and falls back to defaults; set `TRW_CONFIG_STRICT=1` to fail closed instead.

**Optional features:**

- **Peer messaging** (`comms_enabled`, on by default) exposes `trw_peers`, `trw_send` and `trw_inbox`. Outside a formation they refuse and create no state. Messages are pull-based: a message does not wake an idle agent. Set `comms_enabled: false` to hide the tools.
- **Dispatch** (`dispatch_tools_exposed`, off by default) launches another installed agent client, for example for a second-opinion review (`trw-mcp dispatch --client <client> ...`). It does not install that client or supply its credentials. `dispatch_child_trw_access` (off by default) gives supported children TRW's own stdio MCP connection and no other host configuration. Containment differs by client: headless Antigravity, for example, still loads its own configured MCP servers, and TRW's macOS write-denial wrapper for it does not isolate network access. Check `trw-mcp dispatch --help` and the result's `sandbox_verified` field before relying on a read-only review.
- **trw_assess** (`assess_enabled`, off by default, experimental) returns calibrated probabilities for judgment calls. It is advisory. With the setting on and an `OPENROUTER_API_KEY` in the environment or the project `.env`, it sends the question and its state, with secrets redacted, to an allowlisted `openrouter.ai` endpoint.

None of these switches grants permission to modify files or bypass review gates, and peer messages are not trusted instructions.

## Network and security

<a id="telemetry--network-behavior"></a>

trw-mcp is local-first. Tool-call telemetry is recorded locally in `.trw/logs/tool-telemetry.jsonl` (`telemetry_enabled`, on by default). Learning content and usage telemetry are uploaded only if you turn on `learning_sharing_enabled` or `platform_telemetry_enabled`; both are off by default.

**Platform connection.** A signed-in `install.sh` install connects the project to the TRW platform: it adds `platform_urls` (`https://api.trwframework.com`) to `.trw/config.yaml` and stores your API key in `.trw/credentials.yaml`. With a platform URL configured, TRW checks for updates at each session start and polls for team learnings every five minutes by default, both authenticated with that key. To turn the connection off, remove `platform_urls` (and `platform_url` or `backend_url`, if set) from `.trw/config.yaml` and `~/.trw/config.yaml`. A pip install with `trw-mcp init-project` does not configure one. These two requests are independent of `TRW_OFFLINE`, which covers model downloads.

These are the surfaces that can reach the network:

| Surface | When | Default | How to control it |
|---------|------|---------|-------------------|
| Installer sign-in | `install.sh` device login and installer download from trwframework.com | on for `install.sh` | `--allow-unauthenticated`, or install with pip |
| Embedding model download | Only when `trw-memory[embeddings]` is installed and the configured model (default `BAAI/bge-small-en-v1.5`) is not complete in the local Hugging Face cache. A complete cache makes **zero** huggingface.co requests | `embeddings_enabled: true` | `TRW_OFFLINE=1` or `HF_HUB_OFFLINE=1`; fill the cache first if you want vector recall offline |
| Re-ranker model load | In the memory daemon, when `trw-memory[embeddings]` is installed. The cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`) is loaded without a cache check, so a first online load may contact huggingface.co even when the files are already cached | on when the extra is installed | `TRW_OFFLINE=1`, `HF_HUB_OFFLINE=1` or trw-memory's `local_only` load it from the cache only; if it is not cached, recall skips re-ranking |
| Update check | Each `trw_session_start`, when a platform URL is configured: `GET <platform URL>/v1/releases/latest`, with your API key as a bearer token over HTTPS (or HTTP to localhost). It only checks; installing needs `auto_upgrade: true` (off by default) | on with a platform URL | remove the platform URL |
| Team sync pull | Every `sync_interval_seconds` (default 300), when a platform URL and API key are configured: `GET /v1/intel/state` with the API key, this client's id, the model family and the framework version. Pulled team learnings are merged only with `team_sync_enabled: true` (off by default) | on with a platform URL and key | remove the platform URL |
| Usage telemetry | Only when enabled | off (`platform_telemetry_enabled: false`) | leave it off |
| Learning publishing | Only when enabled | off (`learning_sharing_enabled: false`) | leave it off |
| `trw_assess` backend | Only with `assess_enabled` and an `OPENROUTER_API_KEY` | off | leave `assess_enabled: false` |
| Feedback submission | Only when you call `trw_submit_feedback` or run `/trw-feedback`, with a backend URL and API key configured: `POST <backend>/v1/submissions` with your API key, the message and attached metadata | off until invoked | do not invoke it |

With `TRW_OFFLINE=1`, `trw_session_start` makes no huggingface.co calls. The embedding download is independent of the consent flags (`learning_sharing_enabled`, `platform_telemetry_enabled`) — those govern learning and telemetry upload, not the model fetch; only the cache and the offline switches do. The `embedding_egress` row of `trw-mcp doctor` reports which posture applies: `cache-first` (the complete local snapshot is used, no request is made) or `network-capable` (a huggingface.co request may occur on first embed). Loading a model that ships its own Python code is refused unless you set trw-memory's `embedding_trust_remote_code: true`; the default model does not need it.

### Security defaults

| Capability | Default | Notes |
|-----------|---------|-------|
| Encryption at rest (SQLCipher) | off | opt in with trw-memory's `encryption_enabled` |
| Secret redaction in logs | on | API keys, tokens and secret-named fields are masked in log output |
| Secrets in memory writes | refused | with PII detection on (`pii_enabled`, the default), a write containing a recognized API-key pattern is refused; a secret in a shape the patterns do not recognize is not caught. Other detected PII (emails, phone numbers) is recorded as metadata and stored as written |
| Recall output filter | redact | flagged values are masked in recall results (`recall_filter_mode: redact`) |
| Memory poisoning detection | observe | anomalies are recorded, not quarantined |
| Learning and telemetry upload | off | `learning_sharing_enabled: false`, `platform_telemetry_enabled: false`; the update check and team-sync pull are separate (see above) |
| File permissions | `0700` / `0600` | `.trw/` state directories are owner-only; `memory.db` and secret files are owner read/write |

The memory security settings (RBAC, the recall filter, canary, poisoning detection, trust scoring and provenance) are daemon-wide, because one daemon serves every checkout. Set them as `MEMORY_*` variables in the environment the daemon starts from; a trw-mcp process that resolves a different value is refused and told which variable to set.

For an air-gapped or compliance-sensitive setup: install with pip or remove the platform URL, export `TRW_OFFLINE=1` (pre-fill the model cache for vector recall) and `TRW_CONFIG_STRICT=1`, and leave `platform_telemetry_enabled`, `learning_sharing_enabled` and `assess_enabled` off. Then check that `.trw/` directories are `0700`, the daemon's store is `0600`, and `trw_session_start` makes no outbound connection.

### Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `TRW_OFFLINE` | Blocks the Hugging Face model download | unset |
| `HF_HUB_OFFLINE` | Hugging Face's own offline switch, also honored | unset |
| `TRW_CONFIG_STRICT` | Fail closed on a malformed `.trw/config.yaml` | unset (warns and uses defaults) |
| `TRW_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` | from `--debug` or defaults |
| `TRW_PLATFORM_API_KEY` | Platform credential, kept out of git-tracked config | unset |
| `TRW_PROBE_ENABLED` | Enables the sandboxed `trw_probe` experiment tool | unset |
| `ENABLE_TOOL_SEARCH` | Claude Code's MCP tool deferral (`true`/`false`) | client default |
| `MEMORY_*` | trw-memory settings; see the [trw-memory README](https://github.com/wallter/trw-memory) | per setting |

`trw-mcp config-reference` prints the `TRW_` variable for every config setting. It does not list variables read directly from the environment, such as `TRW_OFFLINE` and `TRW_PROBE_ENABLED`.

## Troubleshooting

**`[Errno 2] No such file or directory` on a TRW tool call.** The server process exited. In Claude Code run `/mcp` to reconnect; in other clients, restart the session.

**`trw_session_start` finds no learnings.** Normal on first use. Learnings accumulate as the agent calls `trw_learn`.

**Recall is keyword-only.** Check the `retrieval` row of `trw-mcp doctor`, which names the missing piece and its fix. Usually `trw-memory[embeddings]` is not installed in the interpreter trw-mcp runs from (the daemon uses the same one). Install it, stop the memory daemon (send SIGTERM to the pid in `daemon.json` beside the store) so the next call starts a fresh one, and reconnect the client. `embeddings_enabled: false` is reported as a choice, not a fault.

**Memory tools fail closed and print a `memory migrate` command.** The checkout still has learnings in its old `.trw/memory/memory.db`. Run the printed command; see [Upgrading](#upgrading).

**Memory is refused after moving a checkout.** Mint a grant for the namespace it was pinned to: `trw-mcp memory token --namespace <pinned namespace>`.

**`update-project` refuses because `.trw/managed-artifacts.yaml` is missing or invalid.** Run `trw-mcp uninstall --keep-memory`, then `trw-mcp init-project`.

**The memory store's `-wal` file keeps growing.** A store reclaims WAL space only on SQLite 3.51.3 or newer (or the 3.44.6 and 3.50.7 backports). Older versions are safe but the file does not shrink. The `memory_wal` row of `trw-mcp doctor` reports the SQLite engine in use, but the WAL it measures is the checkout's old `.trw/memory/memory.db-wal`, not your user store (`~/.trw/memory/` by default), so check that store's `memory.db-wal` size yourself. When the engine is too old, the row names which of `python3.14`, `python3.13`, `python3.12` and `python3` on your PATH carry a qualifying SQLite (it probes those four within a 5-second budget and does not search further).

### Debugging

`trw-mcp --debug serve` or `TRW_LOG_LEVEL=DEBUG trw-mcp serve`. Logs go to `.trw/logs/trw-mcp-YYYY-MM-DD.jsonl`.

## Development

```bash
git clone https://github.com/wallter/trw-mcp.git && cd trw-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/test_dedup_trw_learn.py -v  # one file while you work
pytest tests/ -q -n 8                    # the full suite before a change is done
mypy --strict src/trw_mcp/
ruff check src/
```

See [CONTRIBUTING.md](https://github.com/wallter/trw-mcp/blob/main/CONTRIBUTING.md) and, for vulnerability reports, [SECURITY.md](https://github.com/wallter/trw-mcp/blob/main/SECURITY.md).

## License

[Business Source License 1.1](https://trwframework.com/license): source-available, free for non-competing use, and it converts to Apache 2.0 on 2030-03-21. The [LICENSE](https://github.com/wallter/trw-mcp/blob/main/LICENSE) file has the exact terms.

---

Built by [Tyler Wall](http://tylerrwall.com) · [TRW Framework](https://trwframework.com) · [Documentation](https://trwframework.com/docs) · [License](https://trwframework.com/license)
