"""Contract tests for the dispatch client-spec registry (PRD-CORE-266).

A NEW file rather than an append to ``test_dispatch_commands.py``: that file is
held uncommitted by the ``PRD-LOCAL-074`` lane, and the scoped commit helper
re-stages whole files, so appending here would sweep another lane's work.

Covers FR01 (data-only registry), FR02 (byte-identical migration), FR03 (verified
new entries), FR05 (per-client token union), NFR01 (no I/O in the hot path),
NFR02 (fail-closed) and NFR04 (no client literal outside the registry module).
"""

from __future__ import annotations

import ast
import json
import time
from datetime import date
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from trw_mcp.dispatch import build_command
from trw_mcp.dispatch._client_specs import (
    CLIENT_SPECS,
    SUPPORTED_CLIENTS,
    ClientSpec,
    ClientVerification,
    DispatchClient,
    SandboxPosture,
    SubAgentSupport,
    UnknownClientError,
    client_spec_for,
)
from trw_mcp.dispatch._types import _FORBIDDEN_EXTRA_ARG_TOKENS, DispatchRequest

_BASELINE = Path(__file__).parent / "fixtures" / "dispatch_argv_baseline.json"
_DISPATCH_PKG = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "dispatch"


def _req(client: str, **kw: object) -> DispatchRequest:
    base: dict[str, object] = {"client": client, "prompt": "audit this"}
    base.update(kw)
    return DispatchRequest(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# FR01 — the registry is data, and it covers exactly the Literal
# --------------------------------------------------------------------------- #


def test_registry_is_data_only_and_covers_every_dispatch_client() -> None:
    # Key set equals the Literal member set exactly: neither a member with no
    # entry (a request the builder cannot serve) nor an entry outside the Literal
    # (a spec nothing can reach).
    assert set(CLIENT_SPECS) == set(get_args(DispatchClient))
    assert set(SUPPORTED_CLIENTS) == set(CLIENT_SPECS)

    # Zero callable fields. This is the FR01 property the predecessor failed: a
    # closure cannot be serialized, diffed, or rendered, so a single callable
    # field anywhere would take the whole registry back to code.
    for client, spec in CLIENT_SPECS.items():
        for name in type(spec).model_fields:
            value = getattr(spec, name)
            assert not callable(value), f"{client}.{name} is callable; every ClientSpec field must be data"


@pytest.mark.parametrize("client", list(SUPPORTED_CLIENTS))
def test_every_entry_round_trips_through_json_unchanged(client: str) -> None:
    # The liftability property: an entry must survive a JSON round trip so the
    # same shape can cross a package boundary as a specification.
    spec = CLIENT_SPECS[client]  # type: ignore[index]
    assert ClientSpec.model_validate_json(spec.model_dump_json()) == spec


def test_agent_surface_is_derived_not_restated() -> None:
    from trw_mcp.agents.agent_formats import agent_format_for

    for spec in CLIENT_SPECS.values():
        if spec.profile_id is None:
            continue
        assert spec.agent_surface == agent_format_for(spec.profile_id).supports_agents


def test_entry_without_a_client_profile_reports_absence_not_false() -> None:
    # An entry absent from the client-profile registry must report "no profile"
    # rather than False: "we have not established this" and "this client has no
    # agent surface" are different facts (US-002).
    grok = CLIENT_SPECS["grok"]
    assert grok.profile_id is None
    assert grok.agent_surface is None
    assert grok.agent_surface is not False


def test_version_argv_is_per_entry_data_not_a_hardcoded_flag() -> None:
    # The reason version_argv is a field: at least one vendor exposes the version
    # as a SUBCOMMAND. A hardcoded "--version" would silently mis-probe it.
    assert CLIENT_SPECS["grok"].version_argv == ("version",)
    assert CLIENT_SPECS["claude"].version_argv == ("--version",)


# --------------------------------------------------------------------------- #
# FR02 — byte-identical argv for the four clients that predate the registry
# --------------------------------------------------------------------------- #


def _baseline() -> dict[str, object]:
    return json.loads(_BASELINE.read_text(encoding="utf-8"))


def test_argv_baseline_fixture_covers_the_full_option_cross_product() -> None:
    # Guards the guard: a truncated fixture would make the byte-identity test
    # below pass vacuously. 4 clients x 2 isolate x 2 read_only x 2 model x
    # 2 cwd = 64.
    rows = _baseline()["cross_product"]
    assert isinstance(rows, list)
    assert len(rows) == 64
    assert len({row["client"] for row in rows}) == 4
    assert all(len(row["argv"]) > 0 for row in rows)


def test_argv_byte_identity_across_the_option_cross_product() -> None:
    data = _baseline()
    for row in data["cross_product"]:
        req = DispatchRequest(
            client=row["client"],
            prompt=str(data["prompt"]),
            model=row["model"],
            cwd=Path(row["cwd"]) if row["cwd"] else None,
            isolate=bool(row["isolate"]),
            read_only=bool(row["read_only"]),
        )
        assert build_command(req) == row["argv"], (
            f"argv changed for {row['client']} "
            f"(isolate={row['isolate']}, read_only={row['read_only']}, "
            f"model={row['model']}, cwd={row['cwd']}) — the registry migration is "
            "supposed to be behaviour-preserving"
        )


def test_extra_args_and_cwd_positions_are_unchanged() -> None:
    data = _baseline()
    for row in data["extra_args_position"]:
        req = DispatchRequest(
            client=row["client"],
            prompt=str(data["prompt"]),
            extra_args=["--verbose", "--foo=bar"],
            cwd=Path(str(data["cwd"])),
        )
        assert build_command(req) == row["argv"]


def test_opencode_dir_flag_comes_from_registry_data_not_a_branch() -> None:
    # The one client-conditional branch the migration removed. Flipping the
    # registry field must flip the behaviour, which proves the builder reads the
    # data rather than still special-casing the client id.
    argv = build_command(_req("opencode", cwd=Path("/tmp/proj")))
    assert argv[argv.index("--dir") + 1] == "/tmp/proj"

    without_flag = CLIENT_SPECS["opencode"].model_copy(update={"cwd_flag": None})
    assert without_flag.cwd_flag is None
    assert CLIENT_SPECS["claude"].cwd_flag is None
    assert "--dir" not in build_command(_req("claude", cwd=Path("/tmp/proj")))


# --------------------------------------------------------------------------- #
# FR03 — the new entries carry a recorded verification
# --------------------------------------------------------------------------- #


def test_every_entry_carries_a_complete_verification_record() -> None:
    for client, spec in CLIENT_SPECS.items():
        assert spec.verification.evidence.strip(), f"{client} has an empty evidence string"
        assert isinstance(spec.verification.verified_at, date)
        assert spec.verification.method in get_args(type(spec.verification).model_fields["method"].annotation)


def test_cursor_cli_and_copilot_specs_carry_a_verification_record() -> None:
    copilot = CLIENT_SPECS["copilot"]
    assert copilot.verification.method == "executable"
    assert "1.0.83" in copilot.verification.evidence
    # The fragments measured from `copilot --help` at 1.0.83 on this box.
    assert copilot.prompt_flag == "-p"
    assert copilot.structured_output_argv == ("--output-format", "json")
    assert copilot.model_flag == "--model"
    assert copilot.version_argv == ("--version",)
    # Read-only is copilot's headless default: the write-enabling flag is the one
    # the help text calls "required for non-interactive mode", so omitting it IS
    # the read-only mechanism.
    assert copilot.read_only_argv == ()
    assert copilot.allow_writes_argv == ("--allow-all-tools",)
    assert copilot.sandbox == "available_default_off"

    cursor = CLIENT_SPECS["cursor-cli"]
    assert cursor.verification.method == "primary_source"
    assert "cursor.com/docs/cli" in cursor.verification.evidence
    # OQ-3: both documented spellings are recorded and neither is silently
    # preferred — the alias survives so the readiness probe can say which
    # answered.
    assert cursor.binary == "cursor-agent"
    assert "agent" in cursor.binary_aliases
    assert cursor.binary_names == ("cursor-agent", "agent")
    assert cursor.agent_surface is False

    for spec in (copilot, cursor):
        assert spec.forbidden_tokens, f"{spec.client_id} must declare its own bypass tokens"


def test_new_entries_forward_no_provider_credential_that_was_not_recorded() -> None:
    # NFR03: a client with no credential variable in its cited source gets the
    # BASE allowlist alone. Recording an empty tuple is the honest state, and the
    # env builder derives from it rather than from a second table.
    from trw_mcp.dispatch._env import build_subprocess_env

    planted = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-secret", "XAI_API_KEY": "xai-secret"}
    for client in ("cursor-cli", "copilot", "grok"):
        assert CLIENT_SPECS[client].credential_env == ()  # type: ignore[index]
        env = build_subprocess_env(client, source_env=planted)  # type: ignore[arg-type]
        assert env == {"PATH": "/usr/bin"}


def test_recorded_credentials_still_reach_the_client_that_declares_them() -> None:
    # Non-vacuity for the test above: the empty-allowlist result must come from
    # an EMPTY declaration, not from a builder that stopped forwarding anything.
    from trw_mcp.dispatch._env import build_subprocess_env

    planted = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-secret", "AWS_SECRET_ACCESS_KEY": "nope"}
    env = build_subprocess_env("claude", source_env=planted)
    assert env["ANTHROPIC_API_KEY"] == "sk-secret"
    assert "AWS_SECRET_ACCESS_KEY" not in env


# --------------------------------------------------------------------------- #
# FR05 — per-client tokens extend the shared floor as a union
# --------------------------------------------------------------------------- #


def test_per_client_tokens_extend_the_shared_floor_as_a_union() -> None:
    from trw_mcp.dispatch._types import effective_forbidden_tokens

    # Asserted as a SUBSET relation against the live constant, never against a
    # count: the floor's size is owned by another lane (PRD-LOCAL-074) and this
    # property must hold whichever generation of it is on disk.
    assert _FORBIDDEN_EXTRA_ARG_TOKENS, "the shared floor must not be empty"
    for client in SUPPORTED_CLIENTS:
        effective = effective_forbidden_tokens(client)
        assert _FORBIDDEN_EXTRA_ARG_TOKENS <= effective, f"{client} narrowed the shared floor"
        assert CLIENT_SPECS[client].forbidden_tokens <= effective


def test_a_token_declared_in_both_the_floor_and_an_entry_collapses_silently() -> None:
    from trw_mcp.dispatch._types import effective_forbidden_tokens

    floor_token = next(iter(_FORBIDDEN_EXTRA_ARG_TOKENS))
    spec = CLIENT_SPECS["copilot"].model_copy(update={"forbidden_tokens": frozenset({floor_token})})
    assert floor_token in spec.forbidden_tokens
    # A set union cannot double-count, and no error is raised on the real path.
    effective = effective_forbidden_tokens("copilot")
    assert floor_token in effective
    assert len(effective) == len(_FORBIDDEN_EXTRA_ARG_TOKENS | CLIENT_SPECS["copilot"].forbidden_tokens)


@pytest.mark.parametrize(
    "client,token",
    [
        ("copilot", "--allow-all"),
        ("copilot", "--allow-all-tools"),
        ("copilot", "--allow-all-paths"),
        ("copilot", "--allow-all-urls"),
        ("copilot", "--allow-tool"),
        ("copilot", "--allow-url"),
        ("copilot", "--yolo"),
        ("copilot", "--autopilot"),
        ("copilot", "--mode"),
        ("cursor-cli", "-f"),
        ("cursor-cli", "--force"),
        ("cursor-cli", "--yolo"),
        ("cursor-cli", "--approve-mcps"),
    ],
)
def test_new_client_bypass_token_is_rejected_in_extra_args(client: str, token: str) -> None:
    # Named literally rather than derived from the registry: a token DELETED from
    # a spec would silently shrink a derived sweep and stay green.
    with pytest.raises(ValidationError) as exc:
        _req(client, extra_args=[token])
    assert token in str(exc.value)
    assert "security flag" in str(exc.value)


def test_new_client_bypass_token_is_rejected_in_flag_value_form() -> None:
    with pytest.raises(ValidationError):
        _req("copilot", extra_args=["--mode=autopilot"])


def test_a_clients_own_token_does_not_leak_into_another_clients_floor() -> None:
    # The union is PER CLIENT: copilot's --autopilot must not become a codex
    # restriction, or the "can only add restrictions" property would silently
    # become "adds restrictions everywhere".
    assert "--autopilot" not in _FORBIDDEN_EXTRA_ARG_TOKENS
    argv = build_command(_req("codex", extra_args=["--autopilot"]))
    assert "--autopilot" in argv


def _contains_sequence(argv: list[str], fragment: tuple[str, ...]) -> bool:
    """True iff *fragment* appears as a CONTIGUOUS run inside *argv*.

    Set membership is the wrong test here: codex's read-only and write fragments
    both begin with ``--sandbox`` and differ only in the value that follows, so a
    set intersection reports a write bypass on a correctly read-only command.
    """
    if not fragment:
        return False
    span = len(fragment)
    return any(tuple(argv[i : i + span]) == fragment for i in range(len(argv) - span + 1))


def test_read_only_never_emits_the_write_enabling_fragment_for_any_client() -> None:
    # Stated in terms of each spec's OWN allow_writes_argv, which a sweep over a
    # fixed bypass-token set cannot see: a new client whose write flag is spelled
    # differently would slip past that sweep.
    for client, spec in CLIENT_SPECS.items():
        if not spec.allow_writes_argv:
            continue
        argv = build_command(_req(client))
        assert not _contains_sequence(argv, spec.allow_writes_argv), (
            f"{client} emitted its write-enabling fragment under read_only=True"
        )
        rw = build_command(_req(client, read_only=False))
        assert _contains_sequence(rw, spec.allow_writes_argv), f"{client} did not actually enable writes"
        # Non-vacuity: the read-only fragment, when the client has one, IS there.
        if spec.read_only_argv:
            assert _contains_sequence(argv, spec.read_only_argv)


# --------------------------------------------------------------------------- #
# NFR02 — fail closed
# --------------------------------------------------------------------------- #


def test_unknown_client_id_raises_rather_than_returning_a_default_spec() -> None:
    with pytest.raises(UnknownClientError) as exc:
        client_spec_for("not-a-real-cli")
    assert "not-a-real-cli" in str(exc.value)


def test_unverified_entry_must_state_what_verification_is_outstanding() -> None:
    with pytest.raises(ValidationError) as exc:
        ClientVerification(method="unverified", evidence="something", verified_at=date(2026, 9, 4))
    assert "outstanding" in str(exc.value)


def test_a_spec_whose_base_argv_disagrees_with_its_binary_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        ClientSpec(
            client_id="bogus",
            binary="bogus",
            base_argv=("something-else",),
            version_argv=("--version",),
            output_shape="trailing_text",
            sub_agents="unknown",
            sandbox="none",
            verification=ClientVerification(method="primary_source", evidence="x", verified_at=date(2026, 9, 4)),
        )
    assert "bogus" in str(exc.value)


def test_registry_validation_names_the_offending_client_id() -> None:
    # Import-time validation is a function so its failure mode is testable
    # without a broken module on disk.
    from trw_mcp.dispatch import _client_specs as mod

    original = dict(mod.CLIENT_SPECS)
    try:
        mod.CLIENT_SPECS.pop("grok")
        with pytest.raises(ValueError) as exc:
            mod._validate_registry()
        assert "grok" in str(exc.value)
    finally:
        mod.CLIENT_SPECS.clear()
        mod.CLIENT_SPECS.update(original)
    mod._validate_registry()


# --------------------------------------------------------------------------- #
# NFR01 / NFR04 — no I/O in the builder, no client literal outside the registry
# --------------------------------------------------------------------------- #


def test_registry_lookup_plus_argv_build_stays_under_five_milliseconds() -> None:
    # The builder must stay pure. A PATH lookup or a file read sneaking in would
    # show up here long before it showed up as a wrong argv.
    for client in SUPPORTED_CLIENTS:
        req = _req(client)
        samples = []
        for _ in range(100):
            start = time.perf_counter()
            build_command(req)
            samples.append(time.perf_counter() - start)
        samples.sort()
        median = samples[len(samples) // 2]
        assert median < 0.005, f"{client}: median build {median * 1000:.3f} ms exceeds the 5 ms budget"


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_no_client_literal_outside_the_registry_module() -> None:
    # AST string constants, so a client id mentioned in a COMMENT or a prose
    # docstring is not a finding — only a value the code can branch on.
    #
    # `_run_job.py` is exempt and named here rather than silently skipped: its
    # `_client_from_req` labels an unparseable request with a literal client id.
    # Removing that literal means widening `DispatchResult.client` to allow None,
    # a public contract change outside this PRD's boundary. It is recorded as
    # debt in PRD-CORE-266-NFR04, not absorbed.
    exempt = {"_client_specs.py", "_run_job.py"}
    ids = set(get_args(DispatchClient))
    offenders: dict[str, list[str]] = {}
    for path in sorted(_DISPATCH_PKG.glob("*.py")):
        if path.name in exempt:
            continue
        hits = sorted({c for c in _string_constants(path) if c in ids})
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"client id literals outside the registry module: {offenders}"


def test_the_builder_contains_no_client_comparison() -> None:
    # Parsed, not grepped: this module's own prose mentions the comparison it
    # forbids, and a text search would fail on the documentation rather than on
    # the code.
    tree = ast.parse((_DISPATCH_PKG / "_commands.py").read_text(encoding="utf-8"))
    comparisons = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute) and node.left.attr == "client"
    ]
    assert not comparisons, "build_command still branches on the client id"
    # No callable-valued spec field can reach the builder either: nothing in the
    # module imports or annotates one.
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "Callable" not in names | attrs


def test_sandbox_and_sub_agent_fields_are_typed_tri_states() -> None:
    # A boolean here would collapse "TRW sandboxes this" with "the client has a
    # sandbox TRW does not turn on" — the exact conflation FR06 exists to stop.
    sandbox_values = set(get_args(SandboxPosture))
    sub_agent_values = set(get_args(SubAgentSupport))
    assert sandbox_values == {"enforced", "available_default_off", "none"}
    assert sub_agent_values == {"yes", "no", "unknown"}
    for spec in CLIENT_SPECS.values():
        assert spec.sandbox in sandbox_values
        assert spec.sub_agents in sub_agent_values
        assert not isinstance(spec.sandbox, bool)
    assert CLIENT_SPECS["copilot"].sandbox == "available_default_off"
