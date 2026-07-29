"""PRD-FIX-118 — a hook must resolve ITS OWN run, never the newest one.

The defect these tests guard is only visible under concurrency, so every fixture
here is deliberately hostile in the same way the live repository is: the run a
recency heuristic would pick is ALWAYS a foreign run with a lexicographically
later id than the session's own. A test whose fixture holds a single run cannot
fail on this bug, so none of them do.

Layer 1 (identity) is the gate: ``test_server_and_hook_agree_on_one_pin_key``
drives the REAL :func:`resolve_pin_key`, the REAL hook-env writer, and a REAL
POSIX shell, and asserts they land on the same string. If that test fails, every
other requirement in the PRD is unimplementable.

Scope note (honest): this PRD's owned hooks were ``session-start.sh`` and
``post-tool-event.sh`` (plus the shared ``lib-trw.sh`` primitive). Ledger UF-047
migrated the remaining six.
``test_remaining_recency_call_sites_are_the_documented_ones`` pins the hooks that
still resolve by recency so the remaining debt is machine-visible rather than
implied; its expected set is now empty.

Per-hook "unowned" behaviour lives in ``test_hook_ownership_decisions.py`` — each
hook decides for itself what owning no run means, and each decision is asserted in
BOTH directions there.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from _ownership_harness import (
    BUNDLED_HOOKS as _BUNDLED_HOOKS,
)
from _ownership_harness import (
    CLIENT_SESSION_VAR as _CLIENT_SESSION_VAR,
)
from _ownership_harness import (
    FOREIGN_RUN_ID as _FOREIGN_RUN_ID,
)
from _ownership_harness import (
    MIRROR_HOOKS as _MIRROR_HOOKS,
)
from _ownership_harness import (
    OWN_RUN_ID as _OWN_RUN_ID,
)
from _ownership_harness import (
    SESSION_ID as _SESSION_ID,
)
from _ownership_harness import (
    mk_run as _mk_run,
)
from _ownership_harness import (
    project as _project,
)
from _ownership_harness import (
    shell_env as _shell_env,
)
from _ownership_harness import (
    write_hook_env as _write_hook_env,
)
from _ownership_harness import (
    write_pins as _write_pins,
)

_OWNED_HOOKS = (
    "lib-trw.sh",
    "session-start.sh",
    "post-tool-event.sh",
    "stop-ceremony.sh",
    "completion-gate.sh",
    "helper-idle.sh",
    "phase-cycle-stop.sh",
    "pre-compact.sh",
    "session-end.sh",
    "subagent-start.sh",
)


# --------------------------------------------------------------------------- #
# fixture construction -- see _ownership_harness for the builders
# --------------------------------------------------------------------------- #
def _sh(root: Path, script: str, **extra: str) -> subprocess.CompletedProcess[str]:
    """Run *script* in a POSIX shell with lib-trw.sh sourced, as a hook would."""
    return subprocess.run(
        ["sh", "-c", f'. "{_MIRROR_HOOKS / "lib-trw.sh"}"\n{script}'],
        capture_output=True,
        text=True,
        env=_shell_env(root, **extra),
        timeout=60,
        check=False,
    )


def _session_start(
    root: Path, hook: Path, *, payload: dict[str, str] | None = None, **extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(hook)],
        input=json.dumps(payload or {"source": "startup"}),
        capture_output=True,
        text=True,
        env=_shell_env(root, **extra),
        timeout=60,
        check=False,
    )


_HOOK_COPIES = pytest.mark.parametrize(
    "hook_dir",
    [pytest.param(_BUNDLED_HOOKS, id="bundled"), pytest.param(_MIRROR_HOOKS, id="mirror")],
)


# --------------------------------------------------------------------------- #
# FR01 — one pin key, readable on both sides. THE GATE.
# --------------------------------------------------------------------------- #
def test_hook_env_exports_session_id(tmp_path: Path) -> None:
    """FR01: the generated hook-env.sh turns the client variable into the key."""
    root = tmp_path / "proj"
    written = _write_hook_env(root)
    content = written.read_text(encoding="utf-8")
    assert _CLIENT_SESSION_VAR in content
    assert "TRW_SESSION_ID" in content

    # It must EVALUATE to the live value, not bake one in: the file is generated
    # once per install but sourced once per session.
    assert _SESSION_ID not in content
    probe = subprocess.run(
        ["sh", "-c", f'. "{written}"; printf "%s" "${{TRW_SESSION_ID:-<unset>}}"'],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "/bin"), _CLIENT_SESSION_VAR: _SESSION_ID},
        timeout=30,
        check=False,
    )
    assert probe.stdout == _SESSION_ID, probe.stderr


def test_hook_env_does_not_override_an_explicit_session_id(tmp_path: Path) -> None:
    """An operator-forced TRW_SESSION_ID outranks the client variable."""
    written = _write_hook_env(tmp_path / "proj")
    probe = subprocess.run(
        ["sh", "-c", f'. "{written}"; printf "%s" "$TRW_SESSION_ID"'],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", "/bin"),
            _CLIENT_SESSION_VAR: "from-client",
            "TRW_SESSION_ID": "forced-by-operator",
        },
        timeout=30,
        check=False,
    )
    assert probe.stdout == "forced-by-operator"


def test_server_and_hook_agree_on_one_pin_key(tmp_path: Path, monkeypatch) -> None:
    """FR01 layer-1 gate: resolve_pin_key and a shell hook produce one string.

    Drives the real resolver against a FastMCP context whose session id is the
    unobservable value the defect was made of, and asserts the mutually-visible
    client id wins -- then asserts a plain POSIX shell derives the same string
    through the generated hook-env.sh.
    """
    from trw_mcp.state._paths import resolve_pin_key

    class _Ctx:
        session_id = "fastmcp-ctx-uuid-no-hook-can-observe"

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    monkeypatch.setenv(_CLIENT_SESSION_VAR, _SESSION_ID)
    server_key = resolve_pin_key(_Ctx())
    assert server_key == _SESSION_ID, "layer 2b did not beat the unobservable ctx id"

    root = tmp_path / "proj"
    (root / ".trw").mkdir(parents=True, exist_ok=True)
    _write_hook_env(root)
    probe = _sh(root, 'printf "%s" "$(trw_pin_key)"', **{_CLIENT_SESSION_VAR: _SESSION_ID})
    assert probe.stdout == server_key, probe.stderr


def test_pin_key_falls_through_when_client_publishes_nothing(monkeypatch) -> None:
    """No client variable -> unchanged pre-FIX-118 behavior (the ctx id)."""
    from trw_mcp.state._paths import resolve_pin_key

    class _Ctx:
        session_id = "ctx-uuid"

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    monkeypatch.delenv(_CLIENT_SESSION_VAR, raising=False)
    assert resolve_pin_key(_Ctx()) == "ctx-uuid"


def test_explicit_trw_session_id_still_outranks_the_client_variable(monkeypatch) -> None:
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.setenv("TRW_SESSION_ID", "explicit")
    monkeypatch.setenv(_CLIENT_SESSION_VAR, _SESSION_ID)
    assert resolve_pin_key(None) == "explicit"


def test_subagent_shell_inherits_one_identity(tmp_path: Path) -> None:
    """A subagent shell must resolve the SAME key, not mint a second identity.

    Measured on Claude Code 2.1.219: a subagent shell carries
    CLAUDE_CODE_CHILD_SESSION=1 but inherits the parent's CLAUDE_CODE_SESSION_ID
    unchanged, so no capture-once machinery is required. If a future client did
    hand a child a different id, the child would resolve no pin and take the
    explicit unpinned path -- degraded, but never mis-attributed.
    """
    root, own = _project(tmp_path)
    _write_hook_env(root)
    child = _sh(
        root,
        'printf "%s" "$(resolve_owned_run)"',
        **{_CLIENT_SESSION_VAR: _SESSION_ID, "CLAUDE_CODE_CHILD_SESSION": "1"},
    )
    assert child.stdout == f"{own}/", child.stderr


# --------------------------------------------------------------------------- #
# FR02 — the ownership primitive
# --------------------------------------------------------------------------- #
def test_resolves_only_owned_run(tmp_path: Path) -> None:
    """FR02: the owned run wins over a newer foreign run and foreign pins."""
    root, own = _project(tmp_path)
    _write_hook_env(root)
    owned = _sh(root, 'printf "%s" "$(resolve_owned_run)"', **{_CLIENT_SESSION_VAR: _SESSION_ID})
    assert owned.stdout == f"{own}/", owned.stderr
    assert _FOREIGN_RUN_ID not in owned.stdout

    # And the control: find_active_run — the heuristic being replaced — picks the
    # foreign run from this very fixture, so the assertion above is non-vacuous.
    recency = _sh(root, 'printf "%s" "$(find_active_run)"')
    assert _FOREIGN_RUN_ID in recency.stdout, "fixture no longer reproduces the defect"


def test_unowned_session_resolves_nothing(tmp_path: Path) -> None:
    """FR02: a session with no pin entry gets empty output and a non-zero status."""
    root, _ = _project(tmp_path, own_pin=False)
    _write_hook_env(root)
    res = _sh(
        root,
        'if out=$(resolve_owned_run); then printf "RESOLVED:%s" "$out"; else printf "UNOWNED:%s" "$out"; fi',
        **{_CLIENT_SESSION_VAR: _SESSION_ID},
    )
    assert res.stdout == "UNOWNED:", res.stderr


def test_resolve_owned_run_does_not_pin_as_a_side_effect(tmp_path: Path) -> None:
    """NFR04: resolving ownership is read-only — candidate runs stay advisory."""
    root, _ = _project(tmp_path, own_pin=False)
    _write_hook_env(root)
    pins = root / ".trw" / "runtime" / "pins.json"
    before = pins.read_bytes()
    _sh(root, "resolve_owned_run || true", **{_CLIENT_SESSION_VAR: _SESSION_ID})
    assert pins.read_bytes() == before, "resolving ownership mutated the pin store"


def test_pin_outside_project_root_rejected(tmp_path: Path) -> None:
    """A pin pointing outside the repository is not ownership."""
    root, _ = _project(tmp_path, own_pin=False)
    outside = _mk_run(tmp_path / "elsewhere", "other-task", _OWN_RUN_ID)
    _write_pins(root, {_SESSION_ID: {"run_path": str(outside)}})
    _write_hook_env(root)
    res = _sh(root, 'printf "%s" "$(resolve_owned_run)"', **{_CLIENT_SESSION_VAR: _SESSION_ID})
    assert res.stdout == "", f"escaped pin was accepted: {res.stdout}"


def test_pin_to_a_vanished_run_is_not_ownership(tmp_path: Path) -> None:
    root, own = _project(tmp_path)
    shutil.rmtree(own)
    _write_hook_env(root)
    res = _sh(root, 'printf "%s" "$(resolve_owned_run)"', **{_CLIENT_SESSION_VAR: _SESSION_ID})
    assert res.stdout == ""


@pytest.mark.parametrize(
    ("label", "pins"),
    [
        ("truncated", '{"a": {"run_path": "/tmp'),
        ("not-an-object", "[1, 2, 3]"),
        ("empty", ""),
    ],
)
def test_malformed_pins_json_fails_open(tmp_path: Path, label: str, pins: str) -> None:
    """NFR01: a corrupt pin store degrades to unowned — no crash, no adoption."""
    root, _ = _project(tmp_path)
    _write_pins(root, pins)
    _write_hook_env(root)
    res = _sh(root, 'printf "%s" "$(resolve_owned_run)"', **{_CLIENT_SESSION_VAR: _SESSION_ID})
    assert res.stdout == "", f"{label}: {res.stdout}"
    assert "Traceback" not in res.stderr


def test_missing_pins_file_fails_open(tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    (root / ".trw" / "runtime" / "pins.json").unlink()
    _write_hook_env(root)
    res = _sh(root, 'printf "%s" "$(resolve_owned_run)"', **{_CLIENT_SESSION_VAR: _SESSION_ID})
    assert res.stdout == ""
    assert res.returncode == 0


# --------------------------------------------------------------------------- #
# FR03 — hooks consume the primitive instead of recency
# --------------------------------------------------------------------------- #
@_HOOK_COPIES
def test_no_hook_uses_recency_heuristic(hook_dir: Path) -> None:
    """FR03 / acceptance criterion 4: the newest-run-wins glob is gone."""
    offenders = [
        path.name for path in sorted(hook_dir.rglob("*.sh")) if "sort -r | head -1" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"newest-run-wins glob still present in {offenders}"


@_HOOK_COPIES
def test_remaining_recency_call_sites_are_the_documented_ones(hook_dir: Path) -> None:
    """Make the un-migrated hooks visible instead of implied.

    ``expected`` is now EMPTY: ledger UF-047 migrated the last six hooks, so any
    hook that resolves a run by recency unconditionally is a regression, not
    documented debt. This list must shrink, never silently grow.

    "Unguarded" is read off the indentation: a call at column 0 runs on every
    invocation, while an indented one sits inside the identity-unknown branch
    FR03 explicitly permits (a client that publishes no session id at all --
    dropping that branch would disable the hook rather than fix it).
    """
    expected: set[str] = set()
    migrated = {
        "session-start.sh",
        "post-tool-event.sh",
        "stop-ceremony.sh",
        "completion-gate.sh",
        "helper-idle.sh",
        "phase-cycle-stop.sh",
        "pre-compact.sh",
        "session-end.sh",
        "subagent-start.sh",
    }
    # Any find_active_run call on a line that starts in column 0 and is not a
    # comment. Broader than the original `_run_dir=$(find_active_run)` literal so
    # a regression cannot hide behind a renamed variable.
    unguarded = re.compile(r"^(?![#\s]).*\bfind_active_run\b", re.MULTILINE)
    found = {
        path.name
        for path in sorted(hook_dir.rglob("*.sh"))
        if path.name != "lib-trw.sh" and unguarded.search(path.read_text(encoding="utf-8"))
    }
    assert found & migrated == set(), f"a migrated hook regressed to recency: {found & migrated}"
    assert found <= expected, f"new un-migrated recency call sites: {sorted(found - expected)}"


# The lib wrappers that resolve a run by recency INTERNALLY. Calling either one
# re-introduces the defect even in a hook that correctly resolved its own run a
# few lines earlier -- the failure is invisible at the call site, which is exactly
# why it is pinned by name rather than left to review.
_RECENCY_BOUND_LIB_HELPERS = ("infer_phase", "check_ceremony_status")

_MIGRATED_HOOKS = (
    "completion-gate.sh",
    "helper-idle.sh",
    "phase-cycle-stop.sh",
    "pre-compact.sh",
    "session-end.sh",
    "subagent-start.sh",
    "session-start.sh",
    "post-tool-event.sh",
    "stop-ceremony.sh",
)


@_HOOK_COPIES
@pytest.mark.parametrize("hook_name", _MIGRATED_HOOKS)
def test_migrated_hooks_do_not_call_recency_bound_lib_helpers(hook_dir: Path, hook_name: str) -> None:
    """A migrated hook must not launder recency through infer_phase/check_ceremony_status."""
    code = "\n".join(
        line
        for line in (hook_dir / hook_name).read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    # \b will not match inside a prefixed local override such as _pcs_infer_phase,
    # which is the sanctioned replacement, so only the LIB symbol is caught.
    called = [helper for helper in _RECENCY_BOUND_LIB_HELPERS if re.search(rf"\b{helper}\b", code)]
    assert called == [], f"{hook_name} calls recency-bound lib helper(s): {called}"


@_HOOK_COPIES
def test_post_tool_event_ignores_a_foreign_run(hook_dir: Path, tmp_path: Path) -> None:
    """FR03: an identified session never logs an edit into another session's run."""
    if not shutil.which("jq"):
        pytest.skip("pins.json lookup requires jq or python3")
    root, own = _project(tmp_path, own_pin=False)
    _write_hook_env(root)
    foreign_events = root / ".trw" / "runs" / "foreign-task" / _FOREIGN_RUN_ID / "meta" / "events.jsonl"
    before = foreign_events.read_text(encoding="utf-8")

    res = subprocess.run(
        ["sh", str(hook_dir / "post-tool-event.sh")],
        input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "/x/y.py"}, "session_id": _SESSION_ID}),
        capture_output=True,
        text=True,
        env=_shell_env(root, **{_CLIENT_SESSION_VAR: _SESSION_ID}),
        timeout=60,
        check=False,
    )

    assert res.returncode == 0
    assert foreign_events.read_text(encoding="utf-8") == before, "edit logged to a foreign run"


@_HOOK_COPIES
def test_post_tool_event_logs_into_the_owned_run(hook_dir: Path, tmp_path: Path) -> None:
    """The positive half: with a pin, the edit lands in THIS session's run."""
    if not shutil.which("jq"):
        pytest.skip("pins.json lookup requires jq or python3")
    root, own = _project(tmp_path)
    _write_hook_env(root)
    own_events = own / "meta" / "events.jsonl"

    subprocess.run(
        ["sh", str(hook_dir / "post-tool-event.sh")],
        input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "/x/y.py"}, "session_id": _SESSION_ID}),
        capture_output=True,
        text=True,
        env=_shell_env(root, **{_CLIENT_SESSION_VAR: _SESSION_ID}),
        timeout=60,
        check=False,
    )

    assert '"file":"/x/y.py"' in own_events.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# FR04 — an unpinned session says so
# --------------------------------------------------------------------------- #
@_HOOK_COPIES
def test_unpinned_emits_no_foreign_state(hook_dir: Path, tmp_path: Path) -> None:
    """FR04: no tier, phase, event count, or foreign run id for an unpinned session."""
    root, _ = _project(tmp_path, own_pin=False)
    _write_hook_env(root)

    res = _session_start(root, hook_dir / "session-start.sh", **{_CLIENT_SESSION_VAR: _SESSION_ID})

    assert res.returncode == 0
    assert "trw_init" in res.stdout, "the unpinned message must name the remedy"
    assert "CEREMONY — Tier:" not in res.stdout
    assert "MINIMAL" not in res.stdout
    assert _FOREIGN_RUN_ID not in res.stdout
    assert "18 events" not in res.stdout


@_HOOK_COPIES
def test_pinned_session_reports_its_own_run(hook_dir: Path, tmp_path: Path) -> None:
    root, _ = _project(tmp_path)
    _write_hook_env(root)

    res = _session_start(root, hook_dir / "session-start.sh", **{_CLIENT_SESSION_VAR: _SESSION_ID})

    assert _OWN_RUN_ID in res.stdout
    assert _FOREIGN_RUN_ID not in res.stdout


@_HOOK_COPIES
def test_no_session_var_client_degrades(hook_dir: Path, tmp_path: Path) -> None:
    """NFR05 + FR04: a client publishing no identity takes the unpinned path.

    ``copilot`` is the profile used here because it runs hooks but is registered
    with no session variable — the exact combination that must degrade rather
    than guess. (``codex`` also publishes nothing but disables hooks outright,
    so it could not distinguish "degraded correctly" from "never ran".)

    Never recency: the fixture's foreign run is newer and would win a glob.
    """
    root, _ = _project(tmp_path)
    hook_env = _write_hook_env(root, client_id="copilot")
    assert "TRW_SESSION_ID" not in hook_env.read_text(encoding="utf-8").replace("# TRW_SESSION_ID", ""), (
        "a profile with no session variable must export nothing"
    )

    res = _session_start(root, hook_dir / "session-start.sh")

    assert res.returncode == 0
    assert "trw_init" in res.stdout
    assert _FOREIGN_RUN_ID not in res.stdout
    assert "CEREMONY — Tier:" not in res.stdout


@_HOOK_COPIES
def test_unpinned_resume_emits_no_foreign_state(hook_dir: Path, tmp_path: Path) -> None:
    """The second call site (resume) must degrade identically to startup."""
    root, _ = _project(tmp_path, own_pin=False)
    _write_hook_env(root)

    res = _session_start(
        root,
        hook_dir / "session-start.sh",
        payload={"source": "resume"},
        **{_CLIENT_SESSION_VAR: _SESSION_ID},
    )

    assert "trw_init" in res.stdout
    assert _FOREIGN_RUN_ID not in res.stdout
    assert "CEREMONY — Tier:" not in res.stdout


# --------------------------------------------------------------------------- #
# FR05 — one authority for ceremony tier
# --------------------------------------------------------------------------- #
@_HOOK_COPIES
def test_single_tier_authority(hook_dir: Path) -> None:
    """FR05: the hook no longer contains a second tier resolver at all."""
    source = (hook_dir / "session-start.sh").read_text(encoding="utf-8")
    assert "complexity_class" not in source
    assert "CEREMONY — Tier:" not in source


@_HOOK_COPIES
def test_pinned_run_tier_is_never_printed(hook_dir: Path, tmp_path: Path) -> None:
    """Even for an OWNED run carrying a tier, the hook defers to session_start."""
    root, own = _project(tmp_path)
    (own / "meta" / "run.yaml").write_text("task: owned-task\ncomplexity_class: COMPREHENSIVE\n", encoding="utf-8")
    _write_hook_env(root)

    res = _session_start(root, hook_dir / "session-start.sh", **{_CLIENT_SESSION_VAR: _SESSION_ID})

    assert "COMPREHENSIVE" not in res.stdout
    assert "trw_session_start" in res.stdout, "the hook must point at the single authority"


# --------------------------------------------------------------------------- #
# The two copies must not drift
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", _OWNED_HOOKS)
def test_bundled_and_mirror_hooks_identical(name: str) -> None:
    assert (_BUNDLED_HOOKS / name).read_bytes() == (_MIRROR_HOOKS / name).read_bytes(), (
        f"{name} drifted between the bundled copy and the .claude mirror"
    )


# --------------------------------------------------------------------------- #
# The registry itself
# --------------------------------------------------------------------------- #
def test_session_identity_registry_covers_every_active_profile() -> None:
    """A new profile must make a deliberate, truthful claim about its identity."""
    from trw_mcp.client_profiles.catalog import _ACTIVE_CLIENT_ORDER
    from trw_mcp.client_profiles.session_identity import CLIENT_SESSION_ID_ENV_VARS

    missing = set(_ACTIVE_CLIENT_ORDER) - set(CLIENT_SESSION_ID_ENV_VARS)
    assert missing == set(), f"profiles with no session-identity claim: {sorted(missing)}"


@pytest.mark.parametrize(
    "value",
    ["", "   ", " padded ", "with space", "line\nbreak", "x" * 201, "\x00null"],
)
def test_unusable_client_session_ids_are_rejected(value: str) -> None:
    """A value the hook could not reproduce byte-for-byte must not become a key."""
    from trw_mcp.client_profiles.session_identity import resolve_client_session_id

    assert resolve_client_session_id({_CLIENT_SESSION_VAR: value}) is None


def test_usable_client_session_id_is_returned_verbatim() -> None:
    from trw_mcp.client_profiles.session_identity import resolve_client_session_id

    assert resolve_client_session_id({_CLIENT_SESSION_VAR: _SESSION_ID}) == _SESSION_ID
