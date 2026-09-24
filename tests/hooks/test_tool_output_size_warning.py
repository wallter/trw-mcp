"""PRD-INFRA-194-FR04 — the tool-output size warning.

A fourth signal riding the existing PRD-CORE-250 PostToolUse advisory adapter
(``data/hooks/post-tool-degenerate-result.sh``): a rendered ``tool_response``
whose byte length exceeds ``tool_output_size_warning_bytes`` (default 8192)
gets one fixed, non-payload-derived advisory, independent of the three
degenerate-shape rules and gated by its own per-session cooldown.

Every test drives the REAL shipped hook over ``sh`` with a JSON payload on
stdin, reusing ``tests/hooks/_degenerate_result_harness.py`` exactly as
``test_degenerate_result_adapter.py`` does — nothing here re-implements the
adapter's logic.

Design choice (documented once, tested once): when a degenerate-shape
advisory and the size advisory both fire on the same call, they are combined
into ONE ``additionalContext`` string joined by a single space, rather than
emitting two JSON objects (not a valid PostToolUse response) or dropping one.
See ``test_shape_and_size_advisories_combine_into_one_line``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.hooks._degenerate_result_harness import (
    _ADAPTER,
    _HOOKS,
    _MARKER,
    _advisories,
    _payload,
    _project,
    _run,
    pytest_skip_no_jq,
    pytest_skip_no_sh,
)

_SIZE_ADVISORY_TEXT = (
    "This tool output is large (over the configured size threshold) — "
    "prefer narrower queries or summarise before reusing it."
)


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_oversized_valid_json_emits_one_advisory(tmp_path: Path) -> None:
    """A rendered result past the default 8192-byte threshold advises once."""
    root = _project(tmp_path, "oversized")
    payload = _payload(response="x" * 9000)
    result = _run(root, payload)
    assert result.returncode == 0, result.stderr
    advisories = _advisories(result)
    assert advisories == [_SIZE_ADVISORY_TEXT], f"{result.stdout!r} {result.stderr!r}"


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_oversized_second_call_within_cooldown_emits_none(tmp_path: Path) -> None:
    """The size advisory has its own per-session cooldown (NFR05-style)."""
    root = _project(tmp_path, "oversized-cooldown")
    payload = _payload(response="x" * 9000)
    first = _run(root, payload)
    second = _run(root, payload)
    assert len(_advisories(first)) == 1
    assert _advisories(second) == [], "a second oversized result inside the cooldown window advised again"
    assert second.returncode == 0


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_undersized_result_emits_no_size_advisory(tmp_path: Path) -> None:
    """A result well under the default threshold produces no size advisory."""
    root = _project(tmp_path, "undersized")
    payload = _payload(response="a small result, well under the default 8192 byte threshold")
    result = _run(root, payload)
    assert result.returncode == 0
    assert _advisories(result) == []


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_payload_over_the_read_cap_still_advises_once(tmp_path: Path) -> None:
    """The critical edge: a payload so large the stdin read hits max_read_bytes.

    Reading is capped at ``degenerate_result_max_read_bytes`` (default 65536).
    A payload at or beyond that cap is truncated mid-JSON, so ``jq -e .`` would
    fail and the ordinary path falls through to silence — exactly the case a
    size warning must not miss. This drives a 70 KB tool_response, comfortably
    past the 64 KiB default cap, and asserts the hook still classifies it as
    oversized via the read-cap branch (never via jq).
    """
    root = _project(tmp_path, "over-read-cap")
    payload = _payload(response="y" * (70 * 1024))
    assert len(payload.encode("utf-8")) > 65536, "fixture must exceed the default read cap"
    result = _run(root, payload)
    assert result.returncode == 0, result.stderr
    assert _advisories(result) == [_SIZE_ADVISORY_TEXT], f"{result.stdout!r} {result.stderr!r}"


@pytest.mark.skipif(shutil.which("jq") is not None, reason="requires jq to be ABSENT from PATH")
@pytest_skip_no_sh
def test_jq_absent_exits_zero_with_no_advisory(tmp_path: Path) -> None:
    """NFR02: absent jq is silence for the size rule too, not a second parser."""
    root = _project(tmp_path, "no-jq")
    result = _run(root, _payload(response="x" * 9000))
    assert result.returncode == 0
    assert _advisories(result) == []


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_jq_absent_from_path_exits_zero_with_no_advisory(tmp_path: Path) -> None:
    """Same assertion, forced via a jq-less PATH rather than relying on the host.

    Mirrors the ``no-jq`` case in ``test_degenerate_result_nfrs.py::
    test_fail_open_matrix`` — a bare PATH carrying only the POSIX tools the
    hook itself needs, with ``jq`` deliberately excluded.
    """
    root = _project(tmp_path, "bare-path-no-jq")
    bare = tmp_path / "bin-no-jq"
    bare.mkdir()
    keep = ["sh", "cat", "date", "dirname", "grep", "sed", "tr", "head", "cut", "mv", "rm", "mkdir", "awk", "dd"]
    for tool in keep:
        found = shutil.which(tool)
        if found:
            (bare / tool).symlink_to(found)
    result = _run(root, _payload(response="x" * 9000), env={"PATH": str(bare)})
    assert result.returncode == 0, result.stderr
    assert _advisories(result) == []


@pytest_skip_no_sh
@pytest_skip_no_jq
@pytest.mark.parametrize(("length", "fires"), [(100, False), (101, True)])
def test_the_threshold_is_exceeded_not_met(tmp_path: Path, length: int, fires: bool) -> None:
    """A result exactly at the threshold is not oversized; one byte past it is."""
    root = _project(tmp_path, f"boundary-{length}")
    (root / ".trw" / "config.yaml").write_text("tool_output_size_warning_bytes: 100\n", encoding="utf-8")
    assert _advisories(_run(root, _payload(response="a" * length))) == ([_SIZE_ADVISORY_TEXT] if fires else [])


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_configured_threshold_changes_the_outcome(tmp_path: Path) -> None:
    """A .trw/config.yaml override of tool_output_size_warning_bytes is honoured.

    A result too small to trip the shipped default (8192) trips a configured,
    much lower threshold — proving the value actually reaches the classifier
    rather than the shipped default winning regardless of config.
    """
    root = _project(tmp_path, "configured-threshold")
    (root / ".trw" / "config.yaml").write_text("tool_output_size_warning_bytes: 100\n", encoding="utf-8")
    small_but_over_100 = _payload(response="a" * 150)
    assert len(small_but_over_100.encode("utf-8")) < 8192, "fixture must stay under the SHIPPED default"
    result = _run(root, small_but_over_100)
    assert result.returncode == 0
    assert _advisories(result) == [_SIZE_ADVISORY_TEXT], "a lower configured threshold did not fire"


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_hostile_tool_response_never_reaches_stdout_or_a_shell(tmp_path: Path) -> None:
    """NFR03: an oversized, hostile tool_response cannot inject content.

    The advisory is a fixed constant with no payload-derived text, so command
    substitution, backticks, quotes, and ANSI escapes embedded in an OVERSIZED
    result must never appear on stdout/stderr and must never execute.
    """
    root = _project(tmp_path, "hostile-oversized")
    canary = root / "PWNED"
    hostile = f'$(touch {canary}); `touch {canary}`; \x1b[31mRED\x1b[0m; "; touch {canary}; #' + ("z" * 9000)
    result = _run(root, _payload(response=hostile))
    assert result.returncode == 0
    assert not canary.exists(), "payload-derived text reached a shell"
    assert "\x1b" not in result.stdout and "\x1b" not in result.stderr, "an ANSI escape passed through"
    assert "PWNED" not in result.stdout, "payload-derived text leaked onto stdout"
    assert _advisories(result) == [_SIZE_ADVISORY_TEXT]


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_shape_and_size_advisories_combine_into_one_line(tmp_path: Path) -> None:
    """Design choice: both signals firing together combine into ONE additionalContext.

    A single PostToolUse response can only carry one advisory string, so a
    result that is BOTH oversized and carries a truncation marker (rule 2)
    must produce exactly one combined advisory rather than two JSON objects
    or a silently dropped signal.
    """
    root = _project(tmp_path, "combined")
    body = ("a" * 9000) + f"[171470 {_MARKER}"
    result = _run(root, _payload(response=body))
    assert result.returncode == 0, result.stderr
    advisories = _advisories(result)
    assert len(advisories) == 1, f"expected exactly one combined advisory, got {advisories!r}"
    combined = advisories[0]
    assert "could not look" in combined, "the shape advisory is missing from the combined line"
    assert _SIZE_ADVISORY_TEXT in combined, "the size advisory is missing from the combined line"


def test_bundled_and_mirror_hooks_stay_identical() -> None:
    """The .claude mirror must carry the same FR04 branch as the bundled hook."""
    mirror = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / _ADAPTER.name
    if not mirror.exists():
        pytest.skip("not running inside the monorepo checkout")
    assert mirror.read_bytes() == _ADAPTER.read_bytes(), "post-tool-degenerate-result.sh drifted from its mirror"
    mirror_lib = mirror.parent / "lib-trw.sh"
    bundled_lib = _HOOKS / "lib-trw.sh"
    assert mirror_lib.read_bytes() == bundled_lib.read_bytes(), "lib-trw.sh drifted from its mirror"


def test_shell_default_matches_the_typed_field(tmp_path: Path) -> None:
    """The shell accessor's default for size_warning_bytes mirrors the Pydantic field."""
    import subprocess

    from trw_mcp.models.config import TRWConfig

    config = TRWConfig()
    root = _project(tmp_path, "shell-default")
    library = root / ".claude" / "hooks" / "lib-trw.sh"
    child = {k: v for k, v in os.environ.items() if not k.startswith("TRW_TOOL_OUTPUT_SIZE_WARNING_BYTES")}
    child["CLAUDE_PROJECT_DIR"] = str(root)
    result = subprocess.run(
        ["sh", "-c", f'. "{library}" && trw_degenerate_result_setting size_warning_bytes'],
        capture_output=True,
        text=True,
        cwd=root,
        env=child,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(config.tool_output_size_warning_bytes)


def test_the_adapter_reads_size_warning_bytes_through_the_accessor() -> None:
    """FR10-style discipline extended to the new tunable: no bare literal."""
    code = [
        line.strip()
        for line in _ADAPTER.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    body = "\n".join(code)
    assert "trw_degenerate_result_setting size_warning_bytes" in body
    assert "=8192" not in body


def _nested_session_payload(session: str, response_bytes: int) -> str:
    """Past the read cap, with a NESTED ``session_id`` in tool_response AHEAD of the top-level one."""
    import json

    response = {"session_id": "nested-shared", "blob": "z" * response_bytes}
    return json.dumps({"tool_name": "Read", "tool_response": response, "session_id": session})


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_a_nested_session_id_past_the_read_cap_never_shares_a_cooldown(tmp_path: Path) -> None:
    """FIX-151 r2 P1: a payload-derived key could be the nested id; with no env identity nothing suppresses."""
    root = _project(tmp_path, "nested-session-id")
    results = [_run(root, _nested_session_payload(s, 70 * 1024)) for s in ("sess-a", "sess-b", "sess-a")]
    assert [_advisories(r) for r in results] == [[_SIZE_ADVISORY_TEXT]] * 3, [r.stderr for r in results]
    assert list((root / ".trw" / "context").glob("tool-output-size-*.state")) == []


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_past_the_read_cap_the_env_session_identity_keys_the_cooldown(tmp_path: Path) -> None:
    """TRW_SESSION_ID keys the oversized cooldown: a repeat is suppressed, another session is not."""
    root = _project(tmp_path, "env-session-id")
    payload = _nested_session_payload("ignored", 70 * 1024)
    runs = [_run(root, payload, env={"TRW_SESSION_ID": s}) for s in ("sess-a", "sess-a", "sess-b")]
    assert [_advisories(r) for r in runs] == [[_SIZE_ADVISORY_TEXT], [], [_SIZE_ADVISORY_TEXT]]
    context = root / ".trw" / "context"
    assert sorted(p.name for p in context.glob("tool-output-size-*.state")) == [
        "tool-output-size-sess-a.state",
        "tool-output-size-sess-b.state",
    ]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions")
@pytest_skip_no_sh
@pytest_skip_no_jq
def test_an_unwritable_cooldown_state_fails_toward_suppression(tmp_path: Path) -> None:
    """FIX-151 review P2: a cooldown that cannot be recorded suppresses, instead of advising on every call."""
    root = _project(tmp_path, "unwritable-state")
    context = root / ".trw" / "context"
    context.chmod(0o555)
    try:
        results = [_run(root, _payload(response="x" * 9000)) for _ in range(3)]
    finally:
        context.chmod(0o755)
    assert [r.returncode for r in results] == [0, 0, 0]
    assert [_advisories(r) for r in results] == [[], [], []]
    assert list(context.glob("tool-output-size-*")) == [], "a temp file leaked into the state directory"
