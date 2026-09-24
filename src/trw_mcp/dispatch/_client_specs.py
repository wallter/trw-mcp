"""Typed, data-only registry of the coding-agent CLIs dispatch can launch.

Belongs to the ``trw_mcp.dispatch`` package. This module is the SINGLE source of
per-client facts: the binary name, the argv fragments, the bypass tokens that
client's own vendor documents, which instruction files it reads, and — the point
of the module — *how TRW established any of that*.

Why data and not code (PRD-CORE-266-FR01)
-----------------------------------------
The predecessor encoded each client's argv policy as four Python callables on a
frozen dataclass, plus one ``if req.client == "opencode"`` branch in the builder.
A closure cannot be serialized, diffed, rendered into a documentation table, or
lifted across a package boundary as a *specification*; a frozen model with tuple
fields can. Every field here is data, so an entry round-trips through
``model_dump_json`` unchanged and the same shape can be handed to a shared
cross-harness seam without rewriting behaviour into it.

Why ``verification`` is a required field (PRD-CORE-266-FR03/FR04)
-----------------------------------------------------------------
Prior learning L-bo54 records that P0 failures in client integration plans
cluster on one thing: unconfirmed client capabilities recorded as though
shipped. Nothing in the previous shape distinguished a flag someone ran on a box
from a flag someone remembered. So every entry states its method — an executable
probe, a dated vendor page, or nothing at all — and an ``unverified`` entry is
refused at resolution rather than dispatched on provisional data.

Registry invariants, enforced at import
---------------------------------------
- the key set equals the :data:`DispatchClient` Literal member set exactly;
- ``client_id`` equals the key it is filed under;
- ``base_argv`` is non-empty and starts with ``binary``;
- an ``unverified`` entry names the verification still outstanding.

A malformed entry therefore fails at import with the offending client id, not at
the first dispatch call.

The typed shapes live in the ``_client_spec_types`` sibling and are re-exported
here, so ``_client_specs`` remains the one import site for the registry.

This module imports nothing from ``_types``. The dependency runs the other way
(``_types`` reads the registry for the Literal and the per-client token union),
so the direction is one-way and the registry stays liftable.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, get_args

from trw_mcp.dispatch._client_spec_types import REVIEWER_ARGV_PLACEHOLDERS as REVIEWER_ARGV_PLACEHOLDERS
from trw_mcp.dispatch._client_spec_types import ClientSpec as ClientSpec
from trw_mcp.dispatch._client_spec_types import ClientVerification as ClientVerification
from trw_mcp.dispatch._client_spec_types import DispatchPosture as DispatchPosture
from trw_mcp.dispatch._client_spec_types import OutputShape as OutputShape
from trw_mcp.dispatch._client_spec_types import SandboxPosture as SandboxPosture
from trw_mcp.dispatch._client_spec_types import SubAgentSupport as SubAgentSupport
from trw_mcp.dispatch._client_spec_types import UnknownClientError as UnknownClientError
from trw_mcp.dispatch._client_spec_types import VerificationMethod as VerificationMethod

__all__ = [
    "CLIENT_SPECS",
    "REVIEWER_ARGV_PLACEHOLDERS",
    "SUPPORTED_CLIENTS",
    "ClientSpec",
    "ClientVerification",
    "DispatchClient",
    "DispatchPosture",
    "OutputShape",
    "SandboxPosture",
    "SubAgentSupport",
    "UnknownClientError",
    "VerificationMethod",
    "client_spec_for",
]

#: The coding-agent CLIs the dispatch layer knows about. Being listed here is
#: NOT permission to launch: an entry whose verification method is ``unverified``
#: is registered so its state is visible and refusable, not so it can be run.
DispatchClient = Literal[
    "claude",
    "codex",
    "agy",
    "opencode",
    "cursor-cli",
    "copilot",
    "grok",
]

# of a version probe would overstate what was re-checked.
_LIVE_2026_06_21 = "argv flags verified live on this box 2026-06-21; binary and --version re-confirmed 2026-09-05"

CLIENT_SPECS: dict[DispatchClient, ClientSpec] = {
    # claude -p "<prompt>" --output-format json  -> {.result: str}
    #
    # Isolation keeps USER-level auth but drops this project's ceremony:
    #   --setting-sources user                   -> load only user settings
    #   --strict-mcp-config + empty --mcp-config -> no MCP servers, so the child
    #                                               cannot recurse into the host
    #                                               trw MCP.
    # --bare was REJECTED: it also drops user login ("Not logged in").
    #
    # read_only: in headless -p mode claude denies edits by default (there is no
    # approval prompt to satisfy), so read_only=True adds nothing. read_only=False
    # must EXPLICITLY opt in, otherwise --allow-writes would be a silent no-op.
    #
    # sandbox=none: claude exposes no sandbox flag this layer uses. Isolation
    # limitation: the child still READS the project CLAUDE.md it is pointed at
    # (intentional — it must see the code it audits).
    "claude": ClientSpec(
        client_id="claude",
        binary="claude",
        base_argv=("claude",),
        structured_output_argv=("--output-format", "json"),
        isolation_argv=("--setting-sources", "user", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'),
        # REVIEWER POSTURE (OD-6 / PRD-SEC-015-FR06). Emitted INSTEAD of
        # isolation_argv, which is why it repeats --setting-sources/--strict-mcp-config
        # rather than adding to them: two --mcp-config values on one command line is
        # an ambiguity, not a defense. Same three flags, same evidence record — only
        # the payload changes, from the empty server map to TRW's own stdio server.
        #
        # --strict-mcp-config is what makes this an OD-6 posture rather than a hint:
        # with it, the ONLY MCP servers the child sees are the ones in this argv, so
        # the reviewed repository's .mcp.json cannot add a server or replace ours.
        # The ``env`` key on a server entry is the same one the installer's
        # .mcp.json writer treats as user customization (bootstrap/_mcp_json.py
        # ``_is_user_customized_trw_entry``), so it is this client's documented
        # per-server environment channel, not an invention here.
        #
        # ONE layer, stated plainly: claude has no client-side MCP tool allowlist TRW
        # can use — its --allowedTools flag pre-authorises tool use without a prompt
        # and is in the forbidden-token floor as a permission bypass. So {reviewer_tools}
        # is deliberately absent here and the bound is the SERVER-side role alone (the
        # actual control per PRD-SEC-015; the codex allowlist below is defense in depth
        # that claude simply does not get). Do not "fix" this by adding --allowedTools.
        reviewer_argv_template=(
            "--setting-sources",
            "user",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{"trw":{"command":"{mcp_command}","args":{mcp_args},'
            '"env":{"TRW_SURFACE_ROLE":"reviewer"}}}}',
        ),
        reviewer_env={"TRW_SURFACE_ROLE": "reviewer"},
        # with_trw POSTURE (PRD-CORE-281-FR02). The reviewer template above minus
        # the role marking: same three flags, same evidence, same documented
        # per-server key names — only the env payload is dropped, so the child
        # gets an ORDINARY TRW session instead of the bounded reviewer surface.
        #
        # --setting-sources user and --strict-mcp-config are BOTH retained, and
        # that is what keeps this an opt-in to ONE server rather than to the host:
        # the child still loads no project settings and no hooks, and the only
        # MCP server it sees is the one rendered here — this repository's
        # .mcp.json cannot add one or replace ours.
        trw_access_argv_template=(
            "--setting-sources",
            "user",
            "--strict-mcp-config",
            "--mcp-config",
            # The env payload is the nested-launch marker and nothing else: it
            # tells the child's TRW server it may not dispatch. It is a static
            # literal, so no request field can drop or change it.
            '{"mcpServers":{"trw":{"command":"{mcp_command}","args":{mcp_args},"env":{"TRW_DISPATCH_CHILD":"1"}}}}',
        ),
        allow_writes_argv=("--permission-mode", "acceptEdits"),
        model_flag="--model",
        tier_profile="claude-code",
        # `claude --help` (2.1.280, 2026-09-22): "--effort <level> ... (low, medium, high,
        # xhigh, max)". The ONLY effort control Opus 5.5 honours from the CLI; the
        # top-level effortLevel setting no longer applies to it.
        effort_flag="--effort",
        effort_levels=("low", "medium", "high", "xhigh", "max"),
        prompt_flag="-p",
        version_argv=("--version",),
        output_shape="single_json_object",
        # Anthropic / Bedrock / Vertex auth surfaces for Claude Code.
        credential_env=(
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "AWS_REGION",
            "AWS_PROFILE",
        ),
        instruction_files=("CLAUDE.md",),
        profile_id="claude-code",
        sub_agents="yes",
        sandbox="none",
        verification=ClientVerification(
            method="executable",
            evidence=f"claude 2.1.261 --version; {_LIVE_2026_06_21}",
            verified_at=date(2026, 6, 21),
        ),
    ),
    # codex exec "<prompt>" — picks up host MCP/hooks unless isolated.
    # read_only=True -> --sandbox read-only; read_only=False -> --sandbox
    # workspace-write, so --allow-writes ACTUALLY enables writes rather than
    # silently dropping the sandbox.
    "codex": ClientSpec(
        client_id="codex",
        fresh_mcp_server_table=True,
        binary="codex",
        base_argv=("codex", "exec"),
        always_argv=("--skip-git-repo-check",),
        # X-19: JSONL events carry the turn's own verdict (turn.completed / turn.failed /
        # error), so an API-side stop is no longer indistinguishable from an answer.
        # Schema measured on codex-cli 0.155.0 (tests/fixtures/codex_exec_json_ok.jsonl).
        structured_output_argv=("--json",),
        isolation_argv=("--ignore-user-config",),
        # REVIEWER POSTURE (OD-6 / PRD-SEC-015-FR06/FR07). Two layers, rendered
        # from one source: the SERVER-side role (mcp_servers.trw.env.TRW_SURFACE_ROLE
        # — the actual control, enforced by SurfaceAuthorityMiddleware) and the
        # CLIENT-side enabled_tools allowlist (defense in depth, hard-enforced by
        # codex). Both are the exact key/value forms scripts/audit-external.sh runs
        # live, and the allowlist is rendered from reviewer_tools_toml_array(), the
        # same SSOT scripts/print_reviewer_tools.py reads, so the two layers cannot
        # disagree.
        #
        # command/args are supplied here as well, which audit-external.sh does NOT do
        # — and that difference IS the OD-6 decision. That script depends on the
        # reviewed repository's .codex/config.toml to supply the transport; OD-6 says
        # a reviewer's MCP server must come from TRW's argv, never from the repo it is
        # auditing (L-XW1l). ``mcp_servers.trw.command``/``args`` are the documented
        # key names for that table (bootstrap/_codex.py::_trw_mcp_server_entry writes
        # exactly those; docs/CLIENT-PROFILES.md §245 states them), and ``-c`` is
        # measured to reach dotted mcp_servers.trw.* sub-keys, so this composes two
        # verified facts rather than inventing a flag.
        #
        # NO --ignore-user-config under this posture, deliberately (L-VupD): probe 2
        # showed it drops project MCP servers outright and probe 3 showed the -c
        # overrides failed beside it with "invalid transport in mcp_servers.trw" —
        # measured when only enabled_tools/env were overridden and no transport
        # existed anywhere. Supplying command/args here is what removes that
        # dependency, but the combination has NOT been run, so the flag stays off
        # until the live FR-12 probe settles it.
        # TRUST RESIDUE, recorded rather than absorbed: the child therefore still
        # READS the reviewed repo's .codex/config.toml. Our -c flags win for the trw
        # server only after _posture renames it to an unpredictable per-launch
        # table and disables the legacy trw entry: dotted -c leaves otherwise
        # MERGE with inherited enabled/cwd/env/tool filters. The surface does not come from
        # that file; but OTHER servers declared there still load. posture="reviewer"
        # bounds the TRW surface, not the child's whole tool inventory — do not
        # report it as full config isolation.
        reviewer_argv_template=(
            "-c",
            'mcp_servers.trw.command="{mcp_command}"',
            "-c",
            "mcp_servers.trw.args={mcp_args}",
            "-c",
            'mcp_servers.trw.env.TRW_SURFACE_ROLE="reviewer"',
            "-c",
            "mcp_servers.trw.enabled_tools={reviewer_tools}",
        ),
        reviewer_env={"TRW_SURFACE_ROLE": "reviewer"},
        # with_trw POSTURE (PRD-CORE-281-FR02). The reviewer template minus the
        # role env and minus the enabled_tools allowlist: the child gets an
        # ORDINARY TRW session. ``-c mcp_servers.trw.command``/``args`` are the
        # same documented dotted keys bootstrap/_codex.py::_trw_mcp_server_entry
        # writes, so this composes verified facts rather than inventing a flag.
        #
        # NO --ignore-user-config here, for the SAME measured reason as the
        # reviewer template (L-VupD): beside it, codex drops project MCP servers
        # and the -c overrides fail with "invalid transport in mcp_servers.trw".
        # TRUST RESIDUE, recorded rather than absorbed: the child therefore still
        # reads the project's .codex/config.toml, so OTHER servers declared there
        # load too. with_trw guarantees that TRW's server is present and comes
        # from TRW's argv; it is NOT a claim of full config isolation.
        # OPEN, stated rather than assumed: whether codex launches an MCP server
        # INSIDE its --sandbox read-only confinement has not been measured here.
        # If it does, the child's TRW server can recall but not persist, so treat
        # a read-only with_trw codex child as read-mostly until that is probed.
        trw_access_argv_template=(
            "-c",
            'mcp_servers.trw.command="{mcp_command}"',
            "-c",
            "mcp_servers.trw.args={mcp_args}",
            # Nested-launch marker, same dotted env transport the reviewer
            # template uses; last, so nothing in this template re-assigns it.
            "-c",
            'mcp_servers.trw.env.TRW_DISPATCH_CHILD="1"',
            # Codex filters the CLI environment again for stdio MCP children.
            "-c",
            'mcp_servers.trw.env_vars=["TRW_PROJECT_ROOT"]',
        ),
        # The residue above, as DATA the runner can report (PRD-CORE-281-FR04).
        # claude declares none because its with_trw template re-emits the whole
        # of its isolation_argv bar the empty server map; codex's DROPS
        # --ignore-user-config outright, which is a real and asymmetric loss.
        trw_access_config_residue=(
            "--ignore-user-config is NOT emitted under with_trw (codex rejects the -c transport "
            "overrides beside it), so this child still reads the user's ~/.codex/config.toml and "
            "the project's .codex/config.toml: a fresh per-launch TRW server comes from argv "
            "and the legacy trw entry is disabled (incompatible legacy HTTP config fails closed), but OTHER "
            "MCP servers declared in those files load as well. with_trw guarantees the presence "
            "and provenance of the trw server, not full config isolation."
        ),
        read_only_argv=("--sandbox", "read-only"),
        allow_writes_argv=("--sandbox", "workspace-write"),
        model_flag="--model",
        version_argv=("--version",),
        output_shape="json_lines",
        # forbidden_tokens (REPAIR-DESIGN-01): ``-c``/``--config`` set ANY nested
        # config key (sandbox_mode, approval_policy, mcp_servers.trw.env — the
        # nested-launch marker and the reviewer role), so every raw override is
        # refused, not a denylist of keys; the typed request fields are the
        # supported way to choose a posture. ``-s``/``-a`` are codex's short
        # aliases of ``--sandbox``/``--ask-for-approval``, already floored in their
        # long form (codex-cli 0.153.4 ``codex --help``, measured 2026-09-05 by
        # trw-loop). ``-s`` stays client-specific: it means a session to opencode.
        forbidden_tokens=frozenset({"-c", "--config", "-s", "-a"}),
        credential_env=("OPENAI_API_KEY", "OPENAI_BASE_URL"),
        instruction_files=("AGENTS.md",),
        profile_id="codex",
        sub_agents="yes",
        sandbox="enforced",
        verification=ClientVerification(
            method="executable",
            evidence=f"codex-cli 0.153.2 --version; {_LIVE_2026_06_21}",
            verified_at=date(2026, 6, 21),
        ),
    ),
    # agy -p "<prompt>" (Antigravity CLI). Raw stdout by default; the PTY
    # fallback is applied by the runner, not here, since it wraps the whole argv.
    #
    # Isolation limitation: agy exposes no host-config/MCP isolation flag in this
    # version, so TRW MCP recursion is NOT mitigated for agy. isolation_argv is
    # empty because there is nothing to emit, not because isolation was skipped.
    "agy": ClientSpec(
        client_id="agy",
        binary="agy",
        base_argv=("agy",),
        # agy 1.2.0 gained `--output-format text|json|stream-json` and a matching
        # `--input-format`. The 1.1.26 entry recorded trailing_text because that
        # was all 1.1.26 had; re-verified live on this box 2026-09-11, the stream
        # is tagged-envelope NDJSON terminating in an `result` envelope.
        structured_output_argv=("--output-format", "stream-json"),
        read_only_argv=("--sandbox",),
        allow_writes_argv=("--dangerously-skip-permissions",),
        # MEASURED 2026-09-16 (agy 1.2.4, Darwin 25.5.0), PRD-CORE-277-FR02. Headless agy
        # AUTO-DENIES file reads without --dangerously-skip-permissions: a read-only review
        # exited 0 having read nothing, with "no output produced — a tool required the
        # 'command' permission that headless mode cannot prompt for". So agy's read-only
        # lane is only usable when the permission prompt is relaxed AND the host denies the
        # writes instead. Under `sandbox-exec -p "(version 1)(allow default)(deny
        # file-write*)(allow file-write* (subpath \"/dev\"))"` the same run READ the
        # workspace canary and BOTH write attempts (inside cwd and outside it) failed with
        # "operation not permitted". The runner emits this fragment only when it actually
        # built that wrapper; with no wrapper the fragment is omitted and agy still cannot
        # read, which is the fail-closed direction.
        confined_read_only_argv=("--dangerously-skip-permissions",),
        host_confinement=True,
        # isolated_review stays unset (PRD-CORE-297-FR05 probe, agy 1.2.8, 2026-09-23).
        # With the lane's temp HOME as the ONE writable path, the confined preflight
        # passed (exit 0, "No MCP servers configured."), but the run failed: that HOME
        # holds no agy credentials, so agy starts an OAuth login and exits 1 with
        # "authentication failed or timed out"; the canary is never read. Isolation held:
        # contamination clean, the escape write denied, the caller byte-identical.
        # Seeding real credentials into the lane would widen it; not done.
        model_flag="--model",
        # `agy --help` (1.2.7, 2026-09-22): "--effort  Reasoning effort for the current
        # CLI session (low|medium|high)". xhigh/max requests clamp down to high.
        effort_flag="--effort",
        effort_levels=("low", "medium", "high"),
        # MEASURED 2026-09-16: without --add-dir, agy loads NONE of the project's
        # instruction files (the AGENTS.md canary came back NOT LOADED); with it, the canary
        # returned the file's first line. instruction_files below declares AGENTS.md, so
        # without this flag that declaration was a claim the argv did not deliver.
        cwd_flag="--add-dir",
        prompt_flag="-p",
        version_argv=("--version",),
        output_shape="enveloped_ndjson_events",
        # GEMINI_API_KEY is Antigravity's OWN documented credential variable — it
        # is NOT a remnant of the removed `gemini` client profile. Do not delete
        # it in a gemini sweep.
        credential_env=("GEMINI_API_KEY", "ANTIGRAVITY_API_KEY"),
        instruction_files=("AGENTS.md",),
        profile_id="antigravity-cli",
        sub_agents="yes",
        # CORRECTED 2026-09-16 from "enforced" (PRD-CORE-277). `agy --help` describes
        # --sandbox as "Run in a sandbox with terminal restrictions enabled" -- it restricts
        # SHELL COMMANDS, not agy's file-edit tool, and a run under it created files inside
        # cwd and at an absolute path outside cwd and --add-dir alike. TRW does emit the
        # flag, so "available_default_off" reads oddly for a flag that IS passed; it is
        # nevertheless the honest member of this vocabulary, because the operator must not
        # read this row as write protection. The protection TRW can actually provide is the
        # host wrapper above, which is per-run and platform-dependent and is therefore
        # reported per run in DispatchResult.sandbox_verified, not here.
        sandbox="available_default_off",
        verification=ClientVerification(
            method="executable",
            evidence=(
                f"agy 1.1.26 --version; {_LIVE_2026_06_21}. RE-VERIFIED against agy 1.2.0 "
                "on 2026-09-11: `agy --help` lists --output-format text|json|stream-json, "
                "--input-format text|stream-json, --print/-p, --model, --sandbox, "
                "--dangerously-skip-permissions, --add-dir, --agent, --effort, --mode; and "
                "`agy -p '...' --output-format stream-json` was RUN, emitting tagged-envelope "
                "NDJSON (init -> step_update{text_delta} -> result{status,response,usage}). "
                "RE-VERIFIED against agy 1.2.4 on 2026-09-16: read-only (--sandbox alone) "
                "exits 0 with EMPTY stdout and an auto-denied permission on stderr; "
                "--add-dir loads AGENTS.md while its absence loads nothing; and under a "
                "macOS sandbox-exec write-denial profile with --dangerously-skip-permissions "
                "the workspace was readable while every write failed"
            ),
            verified_at=date(2026, 9, 16),
        ),
    ),
    # opencode run "<prompt>" --format json --dir <cwd> -> NDJSON events.
    #
    # Isolation limitation: opencode reads .opencode/ config from --dir and this
    # version has no --ignore-config flag, so config-driven recursion is a
    # documented gap.
    "opencode": ClientSpec(
        client_id="opencode",
        binary="opencode",
        base_argv=("opencode", "run"),
        structured_output_argv=("--format", "json"),
        # `opencode run --help` at 1.18.30 has no --dangerously-skip-permissions
        # (the binary does not contain the string); its non-interactive write grant
        # is `--auto` ("auto-approve permissions that are not explicitly denied"),
        # which the opencode CLI docs list for `run` (checked 2026-09-23). Until then
        # a write dispatch failed at argv parse (help, exit 1). Read-only runs never
        # carry it: read-only IS the omission of this fragment, and `--auto` stays
        # in _FORBIDDEN_EXTRA_ARG_TOKENS so a caller cannot add it.
        allow_writes_argv=("--auto",),
        model_flag="--model",
        cwd_flag="--dir",
        version_argv=("--version",),
        output_shape="ndjson_events",
        # opencode is multi-provider — forward all three provider keys. The two
        # OPENCODE_CONFIG* variables are config-LOCATION pointers, not secrets:
        # without them the child always loads the repo's opencode.json (OpenCode's
        # loader order is global -> OPENCODE_CONFIG -> project -> OPENCODE_CONFIG_DIR,
        # last wins), so a dispatch cannot be pointed at a working provider on a
        # box whose repo config names a dead local server (peer finding 2026-09-17).
        credential_env=(
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "OPENCODE_CONFIG",
            "OPENCODE_CONFIG_DIR",
        ),
        instruction_files=("AGENTS.md",),
        profile_id="opencode",
        sub_agents="yes",
        sandbox="none",
        verification=ClientVerification(
            method="executable",
            evidence=f"opencode 1.18.28 --version; {_LIVE_2026_06_21}",
            verified_at=date(2026, 6, 21),
        ),
    ),
    # Cursor CLI. method=primary_source: the vendor parameter reference was read,
    # the binary was NOT run — neither documented spelling resolves on this box.
    #
    # binary/binary_aliases (OQ-3): the vendor overview documents the executable
    # as `agent`; this repository spells it `cursor-agent` in 194 places. `binary`
    # is the repository's spelling because that is what bootstrap installs
    # against; `agent` is carried as an alias so the readiness probe can report
    # which one actually answered instead of guessing.
    #
    # forbidden_tokens: -f/--force, --yolo and --approve-mcps are the parameter
    # reference's own permission-bypass flags. --sandbox is already in the shared
    # floor and is deliberately NOT repeated here — the union collapses it either
    # way, and repeating it would imply the floor might not cover it.
    "cursor-cli": ClientSpec(
        client_id="cursor-cli",
        binary="cursor-agent",
        binary_aliases=("agent",),
        base_argv=("cursor-agent",),
        structured_output_argv=("--output-format", "json"),
        read_only_argv=("--sandbox", "enabled"),
        model_flag="--model",
        prompt_flag="-p",
        version_argv=("--version",),
        forbidden_tokens=frozenset({"-f", "--force", "--yolo", "--approve-mcps"}),
        # The reference documents --output-format text|json|stream-json and names
        # `json` separately from `stream-json`, so a single document is the shape
        # it implies. Inferred from the page, not observed: no cursor binary runs
        # on this box. An inferred shape costs nothing, because the parser
        # degrades to trailing text on any parse failure (OQ-4).
        output_shape="single_json_object",
        # No provider credential variable appears in the cited reference, so none
        # is forwarded. Cursor authenticates through its own login flow.
        credential_env=(),
        instruction_files=("AGENTS.md",),
        profile_id="cursor-cli",
        # The vendor documents cloud/background agents, which is not a headless
        # sub-agent surface TRW can drive through a one-shot subprocess. Recorded
        # as unknown rather than resolved to yes or no on that basis.
        sub_agents="unknown",
        sandbox="enforced",
        verification=ClientVerification(
            method="primary_source",
            evidence=(
                "https://cursor.com/docs/cli/reference/parameters fetched 2026-09-04 "
                "(-p/--print, --output-format text|json|stream-json, --model, -f/--force, "
                "--yolo, --sandbox enabled|disabled, --approve-mcps, -v/--version); "
                "https://cursor.com/docs/cli/overview fetched 2026-09-04 for the binary name. "
                "Neither 'cursor-agent' nor 'agent' resolves on this box"
            ),
            verified_at=date(2026, 9, 4),
        ),
    ),
    # GitHub Copilot CLI. method=executable: `copilot --version` and
    # `copilot --help` were run on this box at 1.0.83 on 2026-09-04 and re-run
    # 2026-09-05; every fragment below is copied from that output.
    #
    # read_only: the help text annotates --allow-all-tools as "required for
    # non-interactive mode". Omitting it is therefore exactly what keeps a
    # headless copilot run unable to approve a write, so read_only_argv is empty
    # and allow_writes_argv is that flag.
    #
    # sandbox=available_default_off: `copilot help sandbox` at 1.0.83 records
    # OS-level command sandboxing as experimental and DISABLED BY DEFAULT, gated
    # behind --experimental or a managed policy, and on Linux requiring bwrap
    # 0.5.0+ plus slirp4netns/iptables//dev/net/tun. TRW does not enable it, so
    # reporting a boolean here would either claim protection TRW does not provide
    # or erase the difference from a client with no sandbox at all.
    #
    # ADJACENT RISK, recorded not silently absorbed: --add-dir (widens file
    # access to another directory) and --assisted-approval (re-routes tool
    # approval) are also in this help output and are NOT blocked below. They sit
    # outside the token list PRD-CORE-266-FR05 enumerates; a later lane can add
    # them with this note as the evidence.
    "copilot": ClientSpec(
        client_id="copilot",
        binary="copilot",
        base_argv=("copilot",),
        structured_output_argv=("--output-format", "json"),
        allow_writes_argv=("--allow-all-tools",),
        model_flag="--model",
        prompt_flag="-p",
        version_argv=("--version",),
        forbidden_tokens=frozenset(
            {
                "--allow-all",
                "--allow-all-tools",
                "--allow-all-paths",
                "--allow-all-urls",
                "--allow-tool",
                "--allow-url",
                "--yolo",
                "--autopilot",
                "--mode",
            }
        ),
        # `--output-format json` is annotated in the help text as "JSONL, one JSON
        # object per line", which is the same shape codex emits. Inferred from that
        # annotation rather than from observed output (OQ-4); the parser degrades
        # to trailing text if the annotation is wrong.
        output_shape="json_lines",
        # `copilot login` / the GitHub CLI credential chain, not a provider API key
        # variable: none appears in `copilot --help` at 1.0.83, so none is
        # forwarded and copilot receives the base allowlist alone.
        credential_env=(),
        instruction_files=(".github/copilot-instructions.md", "AGENTS.md"),
        profile_id="copilot",
        # OQ-2: --agent selects ONE top-level custom agent headlessly; naming a
        # sub-agent for a single non-interactive run is undocumented at 1.0.83.
        sub_agents="unknown",
        sandbox="available_default_off",
        verification=ClientVerification(
            method="executable",
            evidence=(
                "GitHub Copilot CLI 1.0.83 on this box, `copilot --version` / `copilot --help` / "
                "`copilot help sandbox` read 2026-09-04 and re-read 2026-09-05: -p/--prompt, "
                "--output-format text|json (JSONL, one object per line), --model, -v/--version, "
                "--allow-all-tools ('required for non-interactive mode'), --allow-all, "
                "--allow-all-paths, --allow-all-urls, --allow-tool, --allow-url, --yolo, "
                "--autopilot, --mode interactive|plan|autopilot; sandboxing experimental and off "
                "by default, needing bwrap 0.5.0+ on Linux"
            ),
            verified_at=date(2026, 9, 5),
        ),
    ),
    # xAI Grok Build CLI. Headless is `-p` / `--single`; a trailing positional
    # prompt starts the interactive TUI. JSON is one object with `.text`.
    #
    # `--sandbox read-only` REFUSED TO START on this macOS box (2026-09-19):
    # docker.sock symlink broke Seatbelt apply. So read_only_argv is empty —
    # headless default denies writes — rather than a flag that cannot boot.
    # `--always-approve` / `--yolo` stay forbidden. Reviewer / with_trw stay
    # refused: GROK_CONFIG overlay drops mcp_servers (OQ-1 closed).
    "grok": ClientSpec(
        client_id="grok",
        binary="grok",
        base_argv=("grok",),
        # NO --permission-mode here: build_command emits always_argv BEFORE the
        # posture fragment, so keeping dontAsk in both places put
        # `--permission-mode dontAsk ... --permission-mode acceptEdits` on one
        # write-path command line, and which one grok honours is unverified.
        # The posture now sets exactly one (W1 audit MF2, 2026-09-19).
        always_argv=("--no-subagents",),
        structured_output_argv=("--output-format", "json"),
        # dontAsk IS the read-only denial, proven live 2026-09-19 (W3 probe 3): a
        # dontAsk turn asked to write ends stopReason=cancelled with nothing written.
        read_only_argv=("--permission-mode", "dontAsk"),
        # `auto`, NOT `acceptEdits`: headless `-p` has nobody to approve an edit, so
        # acceptEdits denies exactly like dontAsk. Proven live 2026-09-19 (W3 probe): the
        # same create-a-file prompt under acceptEdits ends stopReason=cancelled with no
        # file, both through dispatch and running grok directly, while under
        # `--permission-mode auto` it ends end_turn and the file exists. `--always-approve`
        # and `--yolo` stay in forbidden_tokens, and bypassPermissions is unused.
        #
        # NOT equivalent to claude's write posture (W1 review condition): claude's
        # acceptEdits approves EDITS, while grok's auto approves tool executions for the
        # turn, shell included, and grok's write path is unsandboxed here because sandbox
        # is unavailable_on_host and `--sandbox` is forbidden. An explicit --allow-writes
        # grok child is therefore a broader grant than the same call to claude; the
        # CHANGELOG row says so too.
        allow_writes_argv=("--permission-mode", "auto"),
        model_flag="--model",
        # `grok --help` (1.0.34, 2026-09-22) documents `--max-turns <N>`; a probe at
        # --max-turns 1 exited 1 with "Error: max turns reached" on stderr.
        max_turns_flag="--max-turns",
        max_turns_exhausted_marker="max turns reached",
        # NO effort_flag, deliberately. `grok --help` (1.0.34, 2026-09-22) documents
        # --reasoning-effort (alias --effort) but does not enumerate its values, and
        # learning them would take a live model call. A guessed value fails at argv
        # parse, so grok children run at grok's own default until a probe records them.
        prompt_flag="-p",
        cwd_flag="--cwd",
        version_argv=("version",),
        forbidden_tokens=frozenset({"--tools", "--disallowed-tools", "--always-approve", "--yolo", "--sandbox"}),
        output_shape="single_json_object",
        # Subscription auth is ~/.grok/auth.json under HOME (grok login). HOME is
        # already on the dispatch base allowlist. XAI_API_KEY is a CI fallback
        # only and is not required; do not forward it (session token wins anyway).
        credential_env=(),
        instruction_files=("AGENTS.md",),
        profile_id="grok",
        # --no-subagents is on every dispatched command line, so TRW's own runs
        # never have them, whatever the binary supports (W1 audit SF3).
        sub_agents="no",
        # The flag exists but does not start here (Seatbelt read-only refused on
        # the verified box), so it is in forbidden_tokens above.
        # `available_default_off` would invite an operator to turn on something
        # that would not run; read-only rests on dontAsk's denial, not a sandbox.
        sandbox="unavailable_on_host",
        verification=ClientVerification(
            method="executable",
            evidence=(
                "grok 1.0.34 (3736acbc8658) [stable] on this box 2026-09-19: "
                "`grok version`; `grok -p 'Reply with exactly the word PONG and nothing else.' "
                "--output-format json --no-subagents --permission-mode dontAsk` exit 0, "
                "stdout one JSON object with text='PONG', stopReason='end_turn'. "
                "`--sandbox read-only` refused to start (docker.sock symlink / Seatbelt). "
                "Output-format values: plain|json|streaming-json|streaming-messages-json. "
                "Sandbox profiles (docs): off|workspace|devbox|read-only|strict. "
                "W3 live probes 2026-09-19: `--permission-mode` twice is refused outright "
                "('the argument --permission-mode <MODE> cannot be used multiple times'), so "
                "every write dispatch exited 2 while both fragments carried it; and a dontAsk "
                "turn asked to write ends stopReason=cancelled with no file created, which is "
                "the measured read-only denial this spec now rests on. Write posture: "
                "acceptEdits ALSO ends cancelled with no file written, headlessly and with "
                "one flag, both through dispatch and direct; `--permission-mode auto` ends "
                "stopReason='end_turn' and creates the file, so auto is the only verified "
                "write posture."
            ),
            verified_at=date(2026, 9, 19),
        ),
    ),
}

#: ``str``-keyed index over the same entries. Exists so :func:`client_spec_for`
#: can accept the loose ``str`` a CLI or MCP payload carries without a cast: the
#: registry dict is keyed on the Literal and is invariant in its key type.
#: ``_validate_registry`` asserts ``spec.client_id`` equals the key it is filed
#: under, so this index and ``CLIENT_SPECS`` cannot hold different membership.
_SPEC_BY_ID: dict[str, ClientSpec] = {spec.client_id: spec for spec in CLIENT_SPECS.values()}

#: Runtime tuple form of the :data:`DispatchClient` Literal, DERIVED from the
#: registry rather than restated beside it. Config-field defaults, the env
#: allowlist, the command builder and the doctor row all read this one value, so
#: adding an entry above widens every one of them with no further edit.
SUPPORTED_CLIENTS: tuple[DispatchClient, ...] = tuple(CLIENT_SPECS)


def _validate_registry() -> None:
    """Fail loudly at import if an entry is missing, surplus, or misfiled.

    Runs at import so a malformed registry cannot reach a dispatch call. Each
    failure names the offending client id — a bare "registry invalid" would leave
    a maintainer diffing two id sets by eye.
    """
    literal_ids = set(get_args(DispatchClient))
    registry_ids = set(CLIENT_SPECS)
    missing = sorted(literal_ids - registry_ids)
    surplus = sorted(registry_ids - literal_ids)
    if missing:
        raise ValueError(f"DispatchClient members with no registry entry: {missing}")
    if surplus:
        raise ValueError(f"registry entries absent from the DispatchClient Literal: {surplus}")
    for key, spec in CLIENT_SPECS.items():
        if spec.client_id != key:
            raise ValueError(f"registry entry filed under {key!r} declares client_id {spec.client_id!r}")


_validate_registry()


def client_spec_for(client: str) -> ClientSpec:
    """Return the registered spec for *client*.

    Raises:
        UnknownClientError: when *client* has no entry. There is deliberately no
            default spec — see :class:`UnknownClientError`.
    """
    try:
        return _SPEC_BY_ID[client]
    except KeyError:
        raise UnknownClientError(f"no dispatch client spec registered for {client!r}") from None
