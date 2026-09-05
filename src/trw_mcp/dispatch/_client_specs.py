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

from trw_mcp.dispatch._client_spec_types import ClientSpec as ClientSpec
from trw_mcp.dispatch._client_spec_types import ClientVerification as ClientVerification
from trw_mcp.dispatch._client_spec_types import OutputShape as OutputShape
from trw_mcp.dispatch._client_spec_types import SandboxPosture as SandboxPosture
from trw_mcp.dispatch._client_spec_types import SubAgentSupport as SubAgentSupport
from trw_mcp.dispatch._client_spec_types import UnknownClientError as UnknownClientError
from trw_mcp.dispatch._client_spec_types import VerificationMethod as VerificationMethod

__all__ = [
    "CLIENT_SPECS",
    "SUPPORTED_CLIENTS",
    "ClientSpec",
    "ClientVerification",
    "DispatchClient",
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
        allow_writes_argv=("--permission-mode", "acceptEdits"),
        model_flag="--model",
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
        binary="codex",
        base_argv=("codex", "exec"),
        always_argv=("--skip-git-repo-check",),
        isolation_argv=("--ignore-user-config",),
        read_only_argv=("--sandbox", "read-only"),
        allow_writes_argv=("--sandbox", "workspace-write"),
        model_flag="--model",
        version_argv=("--version",),
        output_shape="json_lines",
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
        read_only_argv=("--sandbox",),
        allow_writes_argv=("--dangerously-skip-permissions",),
        model_flag="--model",
        prompt_flag="-p",
        version_argv=("--version",),
        output_shape="trailing_text",
        # GEMINI_API_KEY is Antigravity's OWN documented credential variable — it
        # is NOT a remnant of the removed `gemini` client profile. Do not delete
        # it in a gemini sweep.
        credential_env=("GEMINI_API_KEY", "ANTIGRAVITY_API_KEY"),
        instruction_files=("AGENTS.md",),
        profile_id="antigravity-cli",
        sub_agents="yes",
        sandbox="enforced",
        verification=ClientVerification(
            method="executable",
            evidence=f"agy 1.1.26 --version; {_LIVE_2026_06_21}",
            verified_at=date(2026, 6, 21),
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
        allow_writes_argv=("--dangerously-skip-permissions",),
        model_flag="--model",
        cwd_flag="--dir",
        version_argv=("--version",),
        output_shape="ndjson_events",
        # opencode is multi-provider — forward all three provider keys.
        credential_env=("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"),
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
    # xAI Grok Build CLI. method=unverified — REGISTERED SO IT CAN BE REFUSED,
    # not so it can be run. Resolution raises before any argv is built, so the
    # provisional fragments below can never reach a command line; they exist to
    # document what is known and to make the gap concrete rather than absent.
    #
    # version_argv is the reason this field is per-entry data: the vendor exposes
    # the version as the SUBCOMMAND `grok version`, not as a flag.
    #
    # profile_id is None — grok is absent from KNOWN_CLIENTS, so its agent
    # surface is reported as "no client profile" rather than as False.
    "grok": ClientSpec(
        client_id="grok",
        binary="grok",
        base_argv=("grok",),
        model_flag="--model",
        version_argv=("version",),
        forbidden_tokens=frozenset({"--tools", "--disallowed-tools"}),
        # The reference documents --output-format but enumerates no values, so TRW
        # passes none and there is no machine format to parse. trailing_text is
        # the measured-absence answer here, not a guess.
        output_shape="trailing_text",
        credential_env=(),
        instruction_files=("AGENTS.md",),
        profile_id=None,
        sub_agents="unknown",
        # The vendor documents --sandbox <PROFILE> but enumerates no profile
        # values (OQ-1), so TRW cannot name one to pass. The sandbox exists and
        # TRW does not enable it — which is exactly available_default_off.
        sandbox="available_default_off",
        verification=ClientVerification(
            method="unverified",
            evidence=(
                "https://docs.x.ai/build/cli/reference fetched 2026-09-04: binary grok, "
                "-m/--model, --tools, --disallowed-tools, --sandbox <PROFILE> with profile "
                "values NOT enumerated, --output-format with values NOT enumerated, version "
                "as the subcommand `grok version`. The binary does not resolve on this box"
            ),
            verified_at=date(2026, 9, 4),
            outstanding=(
                "the --sandbox profile values and the --output-format values must be "
                "established against the installed executable or a vendor page that "
                "enumerates them, since neither is stated by the current reference"
            ),
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
