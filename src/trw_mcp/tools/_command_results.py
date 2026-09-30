"""Typed ``command_results`` for trw_build_check: parse, validate and render (CORE-205 FR04, CORE-320).

Split from ``_evidence_writers`` (the receipt writer) so each stays under the effective-LOC gate.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal, cast

from trw_mcp.models._evidence_core import EvidenceLimits
from trw_mcp.models._evidence_plans import BuildCommandResult, CommandClass

REQUIRED_BUILD_COMMAND_IDS: tuple[str, ...] = ("tests", "static_checks")

# The exact JSON keys this parser understands. Anything else is rejected BY
# NAME. Before 2026-07-27 unknown keys were silently dropped, so the common
# caller guess ``{"id": "tests", "passed": true}`` produced an entry with no
# command_id and a defaulted-to-failure exit code — a green run recorded red,
# then reported to the caller as though *they* had misreported it.
COMMAND_RESULT_FIELDS: tuple[str, ...] = (
    "command_id",
    "label",
    "command_class",
    "exit_code",
    "started_at",
    "completed_at",
    "test_count",
    "failure_count",
    "coverage_pct",
    "limitations",
    # PRD-CORE-320 FR01/FR03: the capability's integration claim, its call site and optional revert proof.
    "integration",
    "call_site",
    "mutation_proof",
)
_INTEGRATIONS = ("wired", "isolated", "not_applicable")

_MAX_LABEL_BYTES = EvidenceLimits.MAX_FREE_TEXT_BYTES

COMMAND_RESULT_EXAMPLE = '{"command_id": "tests", "label": "pytest -q", "command_class": "test", "exit_code": 0}'


def _require_int(value: object, *, field: str, where: str) -> int:
    """Coerce a JSON scalar to int, or say exactly which field was unusable."""
    # ``bool`` is an ``int`` subclass in Python; accepting it would quietly turn
    # the ``"passed": true`` mistake into ``exit_code=1`` (= failed).
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{where}: {field!r} must be an integer, got {value!r}")  # noqa: TRY004 - one caller-facing exception type for every malformed command_results payload.
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{where}: {field!r} must be an integer, got {value!r}") from None


def _optional_int(value: object, *, field: str, where: str) -> int | None:
    return None if value is None else _require_int(value, field=field, where=where)


def _optional_float(value: object, *, field: str, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{where}: {field!r} must be a number, got {value!r}")  # noqa: TRY004 - one caller-facing exception type for every malformed command_results payload.
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{where}: {field!r} must be a number, got {value!r}") from None


def _optional_text(raw: dict[str, object], field: str, *, where: str) -> str:
    """A claim field's text: absent or null is ""; any other non-string is refused by name.

    ``str(false)`` or ``str({})`` would turn a missing proof into a non-empty one that renders
    "wired, revert-proven" (sol core-320-s3 r2), so these fields never go through ``str()``.
    """
    value = raw.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{where}: {field!r} must be a string, got {type(value).__name__}")  # noqa: TRY004 - callers catch ValueError for every caller-input error
    return value


def _parse_one_command_result(raw: object, index: int) -> BuildCommandResult:
    """Validate a single caller-supplied command-result mapping.

    Every rejection names the field actually at fault. The rule this enforces:
    a typed command result is *evidence*, and evidence that cannot state its own
    outcome is not evidence — so ``exit_code`` is required rather than defaulted.
    Defaulting it to 1 silently converted a passing run into a failing record;
    defaulting it to 0 would fabricate a pass. Neither is reportable, so we fail
    loudly instead (CONSTITUTION HB-1/HB-4).
    """
    where = f"command_results[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(  # noqa: TRY004 - see above
            f"{where}: expected an object, got {type(raw).__name__}. Example: {COMMAND_RESULT_EXAMPLE}"
        )

    unknown = sorted(set(raw) - set(COMMAND_RESULT_FIELDS))
    if unknown:
        raise ValueError(
            f"{where}: unrecognized field(s) {unknown}. Accepted fields are {list(COMMAND_RESULT_FIELDS)}. "
            f"Example: {COMMAND_RESULT_EXAMPLE}"
        )

    command_id = str(raw.get("command_id", "")).strip()
    if not command_id:
        raise ValueError(
            f"{where}: 'command_id' is required and identifies which planned command this result covers "
            f"(the build plan requires {list(REQUIRED_BUILD_COMMAND_IDS)}). Example: {COMMAND_RESULT_EXAMPLE}"
        )
    where = f"{where} (command_id={command_id!r})"

    if "exit_code" not in raw or raw["exit_code"] is None:
        raise ValueError(
            f"{where}: 'exit_code' is required — it is the only field that states whether the command "
            f"passed, and inferring one would fabricate the outcome. Use 0 for success. "
            f"Example: {COMMAND_RESULT_EXAMPLE}"
        )

    raw_class = str(raw.get("command_class", "other"))
    try:
        command_class = CommandClass(raw_class)
    except ValueError:
        accepted = [member.value for member in CommandClass]
        raise ValueError(f"{where}: unknown 'command_class' {raw_class!r}; accepted values are {accepted}") from None

    integration = _optional_text(raw, "integration", where=where) or "not_applicable"
    if integration not in _INTEGRATIONS:  # never coerced toward "wired" (PRD-CORE-320 NFR02)
        raise ValueError(f"{where}: unknown 'integration' {integration!r}; accepted values are {list(_INTEGRATIONS)}")

    return BuildCommandResult(
        command_id=command_id,
        label=str(raw.get("label", "")),
        command_class=command_class,
        exit_code=_require_int(raw["exit_code"], field="exit_code", where=where),
        started_at=str(raw.get("started_at", "")),
        completed_at=str(raw.get("completed_at", "")),
        test_count=_optional_int(raw.get("test_count"), field="test_count", where=where),
        failure_count=_optional_int(raw.get("failure_count"), field="failure_count", where=where),
        coverage_pct=_optional_float(raw.get("coverage_pct"), field="coverage_pct", where=where),
        limitations=str(raw.get("limitations", "")),
        integration=cast("Literal['wired', 'isolated', 'not_applicable']", integration),
        call_site=_optional_text(raw, "call_site", where=where),
        mutation_proof=_optional_text(raw, "mutation_proof", where=where),
    )


def integration_claims(results: Sequence[BuildCommandResult] | None) -> dict[str, str]:
    """``command_id -> rendered claim`` for every result that states an integration (PRD-CORE-320 FR03).

    ``trw_build_check`` returns this as ``integration_claims``, so a wired claim with a revert
    proof reads "wired, revert-proven: <proof>" and an isolated one is never a bare pass.
    """
    return {r.command_id: r.render_integration_claim() for r in results or () if r.integration != "not_applicable"}


def _decode_json_payload(raw: str) -> list[object]:
    """Accept a JSON-serialized ``command_results`` array.

    fastmcp 3.2.4 does no JSON-string pre-parsing (verified 2026-07-27), so a
    client that serializes this argument — the Claude Code #3084 shape — is
    rejected by pydantic before any TRW code runs, with an error that says
    nothing about how to proceed. The only workaround available to such a
    caller is to DROP ``command_results``, which in enforce mode means no
    BuildReceipt is written at all. Tolerating the string form therefore
    strengthens the evidence path rather than relaxing it: the decoded payload
    goes through exactly the same per-entry validation.
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"command_results was a string but is not valid JSON ({exc.msg} at position {exc.pos}). "
            f"Supply the array itself, or a JSON encoding of it. Example: [{COMMAND_RESULT_EXAMPLE}]"
        ) from None
    if not isinstance(decoded, list):
        raise ValueError(  # noqa: TRY004 - see above
            f"command_results decoded to {type(decoded).__name__}, expected a JSON array of command results. "
            f"Example: [{COMMAND_RESULT_EXAMPLE}]"
        )
    return decoded


def parse_build_command_results(
    raw_results: list[dict[str, object]] | str | None,
) -> tuple[BuildCommandResult, ...] | None:
    """Parse the public JSON shape without weakening the strict receipt model.

    Returns ``None`` only when the caller supplied nothing at all. An empty list
    is a caller error, not a legacy report: it claims typed evidence and then
    supplies none.
    """
    if raw_results is None:
        return None
    entries: list[object]
    if isinstance(raw_results, str):
        stripped = raw_results.strip()
        if not stripped:
            # An empty string is how several clients encode an UNSET optional
            # string argument, so it routes to the legacy path — where
            # ``_require_tests_passed`` still demands an explicit outcome. It
            # never yields a pass without evidence.
            return None
        entries = _decode_json_payload(stripped)
    else:
        entries = list(raw_results)
    if not entries:
        raise ValueError(
            "command_results was supplied but empty. Omit it entirely to report legacy booleans, or "
            f"supply one entry per executed command (required: {list(REQUIRED_BUILD_COMMAND_IDS)}). "
            f"Example: [{COMMAND_RESULT_EXAMPLE}]"
        )
    return tuple(_parse_one_command_result(raw, index) for index, raw in enumerate(entries))


def _bounded_label(text: str) -> str:
    """Cut a label to the receipt's free-text byte bound; the full text lives in the status cache."""
    return text.encode("utf-8")[:_MAX_LABEL_BYTES].decode("utf-8", "ignore")


def synthesize_build_command_results(
    *, scope: str, tests_passed: bool, static_checks_clean: bool | None
) -> tuple[BuildCommandResult, ...]:
    """Typed results for a caller that reported only scalars, so the receipt is still written.

    Each exit code is the caller's own boolean and each result says it was synthesized. The plan
    requires both commands, so an omitted static outcome is recorded as NOT RUN (exit 1, stated in
    ``limitations``) rather than dropped or passed: the receipt never claims what was not reported.
    """
    note = "synthesized from scalar trw_build_check arguments"
    static_note = note if static_checks_clean is not None else "not run (static_checks_clean omitted)"
    return (
        BuildCommandResult(
            command_id="tests",
            label=_bounded_label(scope),
            command_class=CommandClass.TEST,
            exit_code=0 if tests_passed else 1,
            limitations=note,
        ),
        BuildCommandResult(
            command_id="static_checks",
            label=_bounded_label(f"{scope}: static checks"),
            command_class=CommandClass.STATIC,
            exit_code=0 if static_checks_clean else 1,
            not_run=static_checks_clean is None,
            limitations=static_note,
        ),
    )
