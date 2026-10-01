"""UF-PRD-51: the C9 control-plane locator protects a USER project's hook registration and fast-path sidecar.

(b) It read only ``trw-mcp/src/trw_mcp/data/settings.json``, a path that exists solely in the TRW monorepo, so in
a user project both sides of the comparison were absent and deleting the intent-hook registration from
``.claude/settings.json`` was no finding at all (ledger UF-079 / UF-BOOT-29).
(a) PRD-CORE-254 section 13 item 4: a sidecar with its patterns stripped under a correct digest yields a fast ALLOW,
which no shell-readable cache can prevent; its filed follow-up is to make a COMMITTED forgery a C9 finding. Enrollment
rewrites the sidecar together with the marker, so a sidecar change WITHOUT a marker change is the signal.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

_SIDE = ".trw/contracts/enrollment.globs"
_MARKER = ".trw/contracts/enrollment.yaml"
_REPO = __import__("pathlib").Path(__file__).resolve().parents[2]
_CONTRACT = ".trw/contracts/must-not-happen.yaml"


def _real(rel: str) -> bytes:
    return (_REPO / rel).read_bytes()


def _findings(base: dict[str, bytes], candidate: dict[str, bytes]) -> tuple[str, ...]:
    from trw_mcp.security.intent_contract._control_plane import control_plane_findings

    return control_plane_findings(base.get, candidate.get)


def _settings(*hooks: str) -> bytes:
    entries = [
        {"hooks": [{"type": "command", "command": f'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/{h}"'}]} for h in hooks
    ]
    return json.dumps({"hooks": {"PreToolUse": entries}}).encode()


def test_removing_the_intent_hook_from_a_user_projects_settings_is_a_finding() -> None:
    base = {".claude/settings.json": _settings("pre-tool-intent-guard.sh", "session-start.sh")}
    candidate = {".claude/settings.json": _settings("session-start.sh")}

    hits = _findings(base, candidate)

    assert any("hook registration removed or altered" in f and "pre-tool-intent-guard.sh" in f for f in hits), hits


def test_an_unrelated_hook_change_in_settings_is_not_a_finding() -> None:
    base = {".claude/settings.json": _settings("pre-tool-intent-guard.sh", "session-start.sh")}
    candidate = {".claude/settings.json": _settings("pre-tool-intent-guard.sh")}

    assert not [f for f in _findings(base, candidate) if "hook registration" in f]


def test_a_sidecar_stripped_of_its_patterns_is_a_finding() -> None:
    sidecar = _real(_SIDE)
    stripped = b"".join(line for line in sidecar.splitlines(keepends=True) if not line.startswith(b"p "))
    base = {_CONTRACT: _real(_CONTRACT), _SIDE: sidecar, _MARKER: _real(_MARKER)}
    candidate = {**base, _SIDE: stripped}

    hits = _findings(base, candidate)

    assert any("sidecar" in f and _SIDE in f for f in hits), hits


def test_a_sidecar_rewritten_by_re_enrollment_is_not_a_finding() -> None:
    """Re-enrollment after a hook edit rewrites the g1 digests: same records, new values, no finding."""
    sidecar = _real(_SIDE)
    refreshed = b"".join(
        b"g1 " + b"0" * 64 + b" " + line.split()[-1] + b"\n" if line.startswith(b"g1 ") else line
        for line in sidecar.splitlines(keepends=True)
    )
    assert refreshed != sidecar
    base = {_CONTRACT: _real(_CONTRACT), _SIDE: sidecar, _MARKER: _real(_MARKER)}

    assert not [f for f in _findings(base, {**base, _SIDE: refreshed}) if "sidecar" in f]


def test_a_configured_sidecar_path_is_the_one_protected() -> None:
    cfg = b"security:\n  intent:\n    glob_sidecar_path: .trw/other.globs\n"
    sidecar = _real(_SIDE)
    stripped = b"".join(line for line in sidecar.splitlines(keepends=True) if not line.startswith(b"p "))
    base = {".trw/config.yaml": cfg, _CONTRACT: _real(_CONTRACT), ".trw/other.globs": sidecar}
    candidate = {**base, ".trw/other.globs": stripped}

    assert any(".trw/other.globs" in f for f in _findings(base, candidate))


def test_a_forged_sidecar_is_caught_even_when_the_marker_also_changes() -> None:
    """Codex UF-PRD-51-r1 KI1: any marker edit (a comment) exempted a sidecar stripped of its patterns."""
    sidecar = _real(_SIDE)
    stripped = b"".join(line for line in sidecar.splitlines(keepends=True) if not line.startswith(b"p "))
    assert stripped != sidecar, "the real sidecar carries pattern lines"
    marker = _real(_MARKER)
    base = {_CONTRACT: _real(_CONTRACT), _SIDE: sidecar, _MARKER: marker}
    candidate = {_CONTRACT: _real(_CONTRACT), _SIDE: stripped, _MARKER: marker + b"# touched\n"}

    hits = _findings(base, candidate)

    assert any("sidecar" in f and _SIDE in f for f in hits), hits


def test_a_sidecar_matching_its_contract_is_not_a_finding() -> None:
    """Codex r2 KI1+KI2: equivalent patterns in another order are no forgery, and the matcher must really run
    (the candidate differs from the base, so the comparison is reached)."""
    sidecar = _real(_SIDE).splitlines(keepends=True)
    patterns = [line for line in sidecar if line.startswith(b"p ")]
    assert len(patterns) > 1
    rest = [line for line in sidecar if not line.startswith(b"p ")]
    header = [line for line in rest if line.startswith(b"#")]
    reordered = b"".join(header + patterns[::-1] + [line for line in rest if not line.startswith(b"#")])
    base = {_CONTRACT: _real(_CONTRACT), _SIDE: _real(_SIDE), _MARKER: _real(_MARKER)}

    assert not [f for f in _findings(base, {**base, _SIDE: reordered}) if "sidecar" in f]


def test_a_sidecar_stripped_of_its_freshness_records_is_a_finding() -> None:
    """Codex r2 P0-1: keep every p line and the digest, drop the g records; the shell then trusts a stale cache."""
    sidecar = _real(_SIDE)
    stripped = b"".join(line for line in sidecar.splitlines(keepends=True) if not line.startswith((b"g0 ", b"g1 ")))
    base = {_CONTRACT: _real(_CONTRACT), _SIDE: sidecar, _MARKER: _real(_MARKER)}

    hits = _findings(base, {**base, _SIDE: stripped})

    assert any("sidecar" in f and _SIDE in f for f in hits), hits


def _timed(pre: int) -> bytes:
    entry = {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
            {
                "type": "command",
                "command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/pre-tool-intent-guard.sh"',
                "timeout": pre,
            }
        ],
    }
    return json.dumps({"hooks": {"PreToolUse": [entry]}}).encode()


def test_the_legacy_timeout_migration_is_not_a_finding() -> None:
    """Codex UF-PRD-51-r1 KI2: update-project's 5000 -> bundled-timeout migration needed a weakening signature."""
    hits = _findings({".claude/settings.json": _timed(5000)}, {".claude/settings.json": _timed(60)})

    assert not [f for f in hits if "hook" in f], hits


def test_lowering_an_intent_hook_timeout_below_the_bundled_value_is_a_finding() -> None:
    """A short timeout lets the client kill the guard mid-decision: that weakens it."""
    hits = _findings({".claude/settings.json": _timed(60)}, {".claude/settings.json": _timed(1)})

    assert any("pre-tool-intent-guard.sh" in f and "timeout" in f for f in hits), hits


def test_adding_a_short_timeout_where_none_was_set_is_a_finding() -> None:
    """Codex r2 P0-2: an omitted timeout (the client default) turned into `timeout: 1` slipped past the floor."""

    def settings(timeout: int | None) -> bytes:
        guard = {"type": "command", "command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/pre-tool-intent-guard.sh"'}
        if timeout is not None:
            guard["timeout"] = timeout
        other = {"type": "command", "command": "sh other.sh", "timeout": 5}
        return json.dumps({"hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [other, guard]}]}}).encode()

    hits = _findings({".claude/settings.json": settings(None)}, {".claude/settings.json": settings(1)})

    assert any("pre-tool-intent-guard.sh" in f and "timeout" in f for f in hits), hits


def _g_rewrite(rewrite) -> bytes:  # type: ignore[no-untyped-def]
    out = []
    for line in _real(_SIDE).splitlines(keepends=True):
        out.extend(rewrite(line) if line.startswith((b"g0 ", b"g1 ")) else [line])
    return b"".join(out)


@pytest.mark.parametrize(
    "rewrite",
    [
        pytest.param(lambda line: [b"g0 __missing__ " + line.split()[-1] + b"\n"], id="g0-forgery"),  # codex r3 KI1
        pytest.param(lambda line: [line, line], id="duplicate-record"),
        pytest.param(lambda line: [b"g2 " + b" ".join(line.split()[1:]) + b"\n"], id="unknown-verb"),
        pytest.param(
            lambda line: [b"g1 " + b"a" * 63 + b" " + line.split()[-1] + b"\n"] if line.startswith(b"g1 ") else [line],
            id="bad-hex-length",
        ),
    ],
)
def test_a_malformed_freshness_record_fails_closed(rewrite) -> None:  # type: ignore[no-untyped-def]
    """Lead ruling on codex UF-PRD-51-r3 KI1: only `g0 <path>` / `g1 <64-hex> <path>`, one per guarded path."""
    base = {_CONTRACT: _real(_CONTRACT), _SIDE: _real(_SIDE), _MARKER: _real(_MARKER)}
    forged = _g_rewrite(rewrite)
    assert forged != base[_SIDE]

    hits = _findings(base, {**base, _SIDE: forged})

    assert any("sidecar" in f and _SIDE in f for f in hits), hits


def test_an_omitted_timeout_is_judged_against_the_600s_client_default() -> None:
    """Codex r3 KI2: Claude Code's documented default for a command hook is 600 s, not 60."""
    from trw_mcp.security.intent_contract import _control_plane

    assert _control_plane._CLIENT_DEFAULT_TIMEOUT == 600
