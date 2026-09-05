"""PRD-CORE-250 FR06/FR10 + NFR01-NFR06 — the degenerate-result advisory.

Every test here drives the REAL shipped
``data/hooks/post-tool-degenerate-result.sh`` over ``sh`` with a JSON payload on
stdin, exactly as a client does. Nothing re-implements its logic: a replica would
be testing the replica.

The rule the adapter enforces is stated in ``FRAMEWORK-CORE.md`` — *absence of a
measurement is not a measurement of absence* — and its defaults were calibrated
against live data rather than guessed. Truncation markers were harvested from
2,418 real ``tool_result`` bodies (``more lines]`` 21 times, ``Output too large``
4; every other candidate 0). The freshness allowlist was measured over 2,647 live
results: prefix matching fires on 27 (1.02 per 100) where the substring-matched
superset first tried fired on 319 of 2,073 Bash results (15.4%), three times the
NFR06 budget.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.hooks._degenerate_result_harness import (
    _ADAPTER,
    _ADVISORY_KEY,
    _DEFAULT_DEADLINE_RETRY_ATTEMPTS,
    _HOOKS,
    _MARKER,
    _SRC,
    _advisories,
    _payload,
    _project,
    _retry,
    _run,
    pytest_skip_no_jq,
    pytest_skip_no_sh,
)


# --------------------------------------------------------------------------- #
# FR06 — the three degenerate shapes, the two healthy ones
# --------------------------------------------------------------------------- #
@pytest_skip_no_sh
@pytest_skip_no_jq
@pytest.mark.parametrize(
    ("label", "payload_kwargs", "expected"),
    [
        ("empty-string", {"response": ""}, 1),
        ("whitespace-only", {"response": "   \n\t  "}, 1),
        ("empty-object-leaves", {"response": {"stdout": "", "stderr": "", "interrupted": False}}, 1),
        ("truncated-marker", {"response": f"line one\n[171470 {_MARKER}"}, 1),
        ("truncated-in-object", {"response": {"stdout": f"data\n[42 {_MARKER}", "stderr": ""}}, 1),
        (
            "undated-freshness-read",
            {"response": {"stdout": "abc1234 fixed a thing\n"}, "tool": "Bash", "command": "gh api repos/x/y/runs"},
            1,
        ),
        ("healthy-text", {"response": "def add(a, b):\n    return a + b\n"}, 0),
        (
            "dated-freshness-read",
            {"response": {"stdout": "2026-09-03 abc1234 subject\n"}, "tool": "Bash", "command": "git log --date=iso"},
            0,
        ),
        (
            "undated-but-not-allowlisted",
            {"response": {"stdout": "some output\n"}, "tool": "Bash", "command": "pytest -q"},
            0,
        ),
    ],
)
def test_empty_truncated_undated_and_malformed(
    tmp_path: Path, label: str, payload_kwargs: dict[str, object], expected: int
) -> None:
    root = _project(tmp_path, label)
    result = _run(root, _payload(**payload_kwargs))  # type: ignore[arg-type]
    assert result.returncode == 0, result.stderr
    assert len(_advisories(result)) == expected, f"{label}: {result.stdout!r} {result.stderr!r}"


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_malformed_json_is_silence(tmp_path: Path) -> None:
    root = _project(tmp_path, "malformed")
    result = _run(root, '{"tool_response": "unterminated')
    assert result.returncode == 0
    assert _advisories(result) == []


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_the_advisory_is_a_model_visible_posttooluse_block(tmp_path: Path) -> None:
    """OQ-01's resolution, asserted on the wire rather than assumed.

    The line must arrive as PostToolUse ``additionalContext`` — model-visible and
    non-blocking — and must NOT arrive as ``{"decision": "block"}``, which renders
    as an error to the user for something that is not one.
    """
    root = _project(tmp_path, "channel")
    result = _run(root, _payload(response=""))
    document = json.loads(result.stdout.strip())
    assert document["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "could not look" in document["hookSpecificOutput"][_ADVISORY_KEY]
    assert "decision" not in document, "the adapter must never emit a decision — it is advisory"
    assert result.returncode == 0


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_the_cooldown_suppresses_the_second_match(tmp_path: Path) -> None:
    root = _project(tmp_path, "cooldown")
    first = _run(root, _payload(response=""))
    second = _run(root, _payload(response=""))
    assert len(_advisories(first)) == 1
    assert _advisories(second) == [], "a second matching result inside the cooldown window advised again"
    assert second.returncode == 0


def test_registered_in_both_templates() -> None:
    """FR06 registration is part of the FR, not a follow-up."""
    for template in (_SRC / "data" / "settings.json", _SRC / "data" / "plugin" / "hooks" / "hooks.json"):
        document = json.loads(template.read_text(encoding="utf-8"))
        commands = [hook["command"] for entry in document["hooks"]["PostToolUse"] for hook in entry["hooks"]]
        assert any(_ADAPTER.name in command for command in commands), f"{template} does not register the adapter"


# --------------------------------------------------------------------------- #
# FR10 — five typed tunables behind one accessor
# --------------------------------------------------------------------------- #
def test_shell_defaults_match_the_typed_fields() -> None:
    """The two copies of each default cannot drift.

    A shell hook cannot import a Pydantic model, so the accessor in lib-trw.sh
    carries its own copy of every default. The Pydantic field is the source of
    truth; this is what keeps the copy honest.
    """
    from trw_mcp.models.config import TRWConfig

    config = TRWConfig()
    library = (_HOOKS / "lib-trw.sh").read_text(encoding="utf-8")
    accessor = library.split("trw_degenerate_result_setting() {", 1)[1]
    for field in (
        "degenerate_result_cooldown_calls",
        "degenerate_result_max_read_bytes",
        "degenerate_result_deadline_ms",
    ):
        value = getattr(config, field)
        assert f"_tdrs_default='{value}'" in accessor, f"{field}: shell default drifted from the typed default {value}"
    for marker in config.degenerate_result_truncation_markers:
        assert marker in accessor, f"truncation marker {marker!r} is missing from the shell default"
    for prefix in config.degenerate_result_freshness_commands:
        assert prefix in accessor, f"freshness prefix {prefix!r} is missing from the shell default"


def test_the_adapter_carries_no_bare_tunable_literal() -> None:
    """FR10's ``grep_absent`` assertion, generalized.

    Every one of the five values resolves through the accessor. A literal in the
    adapter would be a magic number that ``.trw/config.yaml`` could not reach.
    """
    code = [
        line.strip()
        for line in _ADAPTER.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    body = "\n".join(code)
    for literal in ("=50", "=20", "=65536", "more lines]", "Output too large", "git log"):
        assert literal not in body, f"the adapter hard-codes {literal!r} instead of reading the typed field"
    for key in ("max_read_bytes", "deadline_ms", "cooldown_calls", "truncation_markers", "freshness_commands"):
        assert f"trw_degenerate_result_setting {key}" in body, f"{key} is not read through the accessor"


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_tunables_are_typed_and_configurable(tmp_path: Path) -> None:
    """Each field, set in .trw/config.yaml, changes the OBSERVED behavior."""
    # 1. cooldown_calls: 0 remaining after a 1-call window means the very next
    #    matching result advises again.
    root = _project(tmp_path, "tunable-cooldown")
    (root / ".trw" / "config.yaml").write_text("degenerate_result_cooldown_calls: 1\n", encoding="utf-8")
    assert len(_advisories(_run(root, _payload(response="")))) == 1
    assert _advisories(_run(root, _payload(response=""))) == [], "the 1-call window did not suppress"
    assert len(_advisories(_run(root, _payload(response="")))) == 1, "the window did not expire after 1 call"

    # 2. truncation_markers: a marker nobody ships must be honoured when configured.
    root = _project(tmp_path, "tunable-markers")
    (root / ".trw" / "config.yaml").write_text(
        "degenerate_result_truncation_markers:\n- CORE250-CUSTOM-CAP\n", encoding="utf-8"
    )
    assert len(_advisories(_run(root, _payload(response="body CORE250-CUSTOM-CAP tail")))) == 1
    root = _project(tmp_path, "tunable-markers-default-off")
    (root / ".trw" / "config.yaml").write_text(
        "degenerate_result_truncation_markers:\n- CORE250-CUSTOM-CAP\n", encoding="utf-8"
    )
    assert _advisories(_run(root, _payload(response=f"body [9 {_MARKER} tail"))) == [], (
        "the shipped default marker still fired after the list was replaced"
    )

    # 3. freshness_commands: emptying the list is the documented OQ-02 rollback
    #    for rule 3 and must leave rules 1 and 2 untouched.
    root = _project(tmp_path, "tunable-freshness")
    (root / ".trw" / "config.yaml").write_text("degenerate_result_freshness_commands: []\n", encoding="utf-8")
    undated = _payload(response={"stdout": "abc1234 x\n"}, tool="Bash", command="gh api repos/x/y")
    assert _advisories(_run(root, undated)) == [], "rule 3 fired with an empty allowlist"
    assert len(_advisories(_run(root, _payload(response="")))) == 1, "emptying rule 3 disabled rule 1"

    # 4. max_read_bytes: a cap below the marker's offset hides rule 2.
    root = _project(tmp_path, "tunable-bytes")
    (root / ".trw" / "config.yaml").write_text("degenerate_result_max_read_bytes: 1024\n", encoding="utf-8")
    big = _payload(response="x" * 4000 + f"[9 {_MARKER}")
    assert _advisories(_run(root, big)) == [], "the byte cap was not applied to the read"

    # 5. deadline_ms: a deadline the process cannot beat means silence, never an error.
    root = _project(tmp_path, "tunable-deadline")
    (root / ".trw" / "config.yaml").write_text("degenerate_result_deadline_ms: 5\n", encoding="utf-8")
    # An empty env value is "unset" to the accessor, so config.yaml decides here.
    result = _run(root, _payload(response=""), env={"TRW_DEGENERATE_RESULT_DEADLINE_MS": ""})
    assert result.returncode == 0
    assert _advisories(result) == [], "a 5ms deadline was not enforced"


@pytest_skip_no_sh
@pytest_skip_no_jq
@pytest.mark.xdist_group(name="degenerate_result_latency")
def test_a_non_numeric_config_value_falls_back_to_the_default(tmp_path: Path) -> None:
    """A mistyped tunable must not silently disable the rule it bounds.

    A garbage deadline falls back to the 50 ms PRODUCTION default -- the exact
    budget `test_degenerate_result_nfrs.py::test_p95_latency_under_budget`
    measures -- so this assertion is wall-clock-bound the same way that one is.
    See `_DEFAULT_DEADLINE_RETRY_ATTEMPTS` for why the retry exists and what it
    does and does not tolerate.
    """

    def _attempt(index: int) -> None:
        root = _project(tmp_path, f"garbage-config-{index}")
        (root / ".trw" / "config.yaml").write_text(
            "degenerate_result_deadline_ms: soon\ndegenerate_result_cooldown_calls: lots\n", encoding="utf-8"
        )
        unset = {"TRW_DEGENERATE_RESULT_DEADLINE_MS": ""}
        first = _run(root, _payload(response=""), env=unset)
        assert first.returncode == 0
        assert len(_advisories(first)) == 1, "a garbage deadline made the adapter silent"
        assert _advisories(_run(root, _payload(response=""), env=unset)) == [], (
            "a garbage cooldown disabled suppression"
        )

    _retry(_DEFAULT_DEADLINE_RETRY_ATTEMPTS, _attempt)


def test_shell_bounds_match_the_typed_fields() -> None:
    """The clamp in the shell accessor mirrors each field's own ge/le.

    Pydantic validates ``.trw/config.yaml`` when the SERVER loads it. The hook
    reads the same file with ``grep``/``sed`` and never imports the model, so
    until the accessor clamped, nothing between a hand-edited value and its
    consumer enforced the bound — `degenerate_result_max_read_bytes: 999999999`
    flowed straight into `head -c` and defeated NFR03's cap.

    Two copies of a bound is one more than the source of truth allows, so this
    reads the bounds off the Pydantic fields and requires the shell to carry the
    same pair. Changing a field's `ge`/`le` without changing the accessor fails
    here rather than silently loosening a shipped hook.
    """
    from trw_mcp.models.config import TRWConfig

    accessor = (_HOOKS / "lib-trw.sh").read_text(encoding="utf-8").split("trw_degenerate_result_setting() {", 1)[1]
    fields = TRWConfig.model_fields

    for short, name in (
        ("cooldown_calls", "degenerate_result_cooldown_calls"),
        ("max_read_bytes", "degenerate_result_max_read_bytes"),
        ("deadline_ms", "degenerate_result_deadline_ms"),
    ):
        bounds: dict[str, object] = {}
        for meta in fields[name].metadata:
            for attribute in ("ge", "le"):
                if hasattr(meta, attribute):
                    bounds[attribute] = getattr(meta, attribute)
        assert set(bounds) == {"ge", "le"}, f"{name} lost its bounds — the clamp would have nothing to mirror"
        assert f"_tdrs_min='{bounds['ge']}'" in accessor, f"{short}: shell lower bound drifted from ge={bounds['ge']}"
        assert f"_tdrs_max='{bounds['le']}'" in accessor, f"{short}: shell upper bound drifted from le={bounds['le']}"


@pytest_skip_no_sh
@pytest_skip_no_jq
@pytest.mark.parametrize(
    ("field", "written", "expected"),
    [
        ("degenerate_result_max_read_bytes", "999999999", "1048576"),
        ("degenerate_result_max_read_bytes", "3", "1024"),
        ("degenerate_result_cooldown_calls", "0", "1"),
        ("degenerate_result_cooldown_calls", "9999", "200"),
        ("degenerate_result_deadline_ms", "1", "5"),
        ("degenerate_result_deadline_ms", "100000", "500"),
    ],
)
def test_an_out_of_bound_config_value_is_clamped(tmp_path: Path, field: str, written: str, expected: str) -> None:
    """An out-of-range value resolves to the nearest ALLOWED value, not the default.

    Clamping rather than falling back is deliberate: an out-of-range value states
    an intent — a bigger cap, a longer window — and the honest answer is the
    nearest value the contract permits. Falling back to the default would
    silently ignore what the operator asked for, and falling through unclamped
    is the NFR03 contradiction this closes.
    """
    root = _project(tmp_path, f"clamp-{field}-{written}")
    (root / ".trw" / "config.yaml").write_text(f"{field}: {written}\n", encoding="utf-8")
    short = field.removeprefix("degenerate_result_")
    result = subprocess.run(
        ["sh", "-c", f'. "{root}/.claude/hooks/lib-trw.sh"; trw_degenerate_result_setting {short}'],
        capture_output=True,
        text=True,
        cwd=root,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(root)},
        timeout=30,
        check=False,
    )
    assert result.stdout == expected, f"{field}={written} resolved to {result.stdout!r}, expected {expected!r}"


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_an_oversized_byte_cap_cannot_defeat_the_read_bound(tmp_path: Path) -> None:
    """The clamp on the real path, not just on the accessor.

    A unit test on the accessor proves the number; this proves the number is the
    one the adapter reads. With the cap clamped to its ceiling, content beyond
    1 MiB is still outside the classifier's view.
    """
    root = _project(tmp_path, "clamp-e2e")
    (root / ".trw" / "config.yaml").write_text("degenerate_result_max_read_bytes: 999999999\n", encoding="utf-8")
    beyond_ceiling = _payload(response="q" * (2 * 1024 * 1024) + f"[7 {_MARKER}")
    assert _advisories(_run(root, beyond_ceiling)) == [], "an out-of-range cap let the read run past its ceiling"


@pytest_skip_no_sh
@pytest_skip_no_jq
def test_an_epoch_at_offset_zero_counts_as_dated(tmp_path: Path) -> None:
    """Rule 3's date probe must accept a timestamp that starts the output.

    The epoch alternative was written `[^0-9]1[6-9][0-9]{8}` — a character class
    REQUIRES a preceding character, so `1788469550 built ok`, which is exactly
    what `date +%s` and a bare timestamp column produce, did not match and a
    perfectly fresh result was advised on.
    """
    root = _project(tmp_path, "epoch-offset-zero")
    dated = _payload(response={"stdout": "1788469550 built ok\n"}, tool="Bash", command="date +%s")
    assert _advisories(_run(root, dated)) == [], "an epoch at offset 0 was misread as undated"

    root = _project(tmp_path, "epoch-mid-string")
    assert (
        _advisories(_run(root, _payload(response={"stdout": "at 1788469550 ok\n"}, tool="Bash", command="date"))) == []
    )

    root = _project(tmp_path, "genuinely-undated")
    undated = _payload(response={"stdout": "abc1234 built ok\n"}, tool="Bash", command="date")
    assert len(_advisories(_run(root, undated))) == 1, "the rule stopped firing at all — the probe is now vacuous"
