"""PRD-SEC-013 FR10: never-enrolled vs enrolled-but-stale."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.security.intent_contract.enrollment import (
    EnrollmentError,
    check_enrollment_status,
    compute_digests,
    enrollment_path,
    missing_intent_hooks,
    stale_enrollment_warning,
    write_enrollment,
)

CONTRACT = ".trw/contracts/must-not-happen.yaml"
_INTENT_HOOKS = ("pre-tool-intent-guard.sh", "post-tool-intent-check.sh")


def _install_hooks(root: Path) -> None:
    """Put the two intent hook scripts where ``compute_digests`` looks for them."""
    hooks = root / ".claude/hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for name in _INTENT_HOOKS:
        (hooks / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")


def enrolled_project(tmp_path: Path) -> Path:
    (tmp_path / ".trw/contracts").mkdir(parents=True)
    (tmp_path / CONTRACT).write_text("contract_id: X\nmust_not_happen: []\n", encoding="utf-8")
    hooks = tmp_path / ".claude/hooks"
    hooks.mkdir(parents=True)
    (hooks / "pre-tool-intent-guard.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (hooks / "post-tool-intent-check.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: local\n    hooks:\n      - id: intent-weaken-edit-flag\n", encoding="utf-8"
    )
    write_enrollment(tmp_path, CONTRACT)
    return tmp_path


def test_enrollment_marker_distinguishes_absent_vs_stale(tmp_path: Path) -> None:
    """FR10 evidence artifact: absent is a silent no-op; stale blocks + warns."""
    # 1. Never enrolled — a clean no-op, identical to the absent-contract case.
    assert check_enrollment_status(tmp_path, CONTRACT) == "never_enrolled"
    assert not enrollment_path(tmp_path).exists()

    # 2. Enrolled and untouched.
    root = enrolled_project(tmp_path)
    assert check_enrollment_status(root, CONTRACT) == "current"

    # 3. Hook deleted after enrollment — indistinguishable from (1) before FR10.
    (root / ".claude/hooks/pre-tool-intent-guard.sh").unlink()
    assert check_enrollment_status(root, CONTRACT) == "stale"

    warning = stale_enrollment_warning(root, CONTRACT)
    assert "unsuppressible" in warning
    assert "expected_hook_digest" in warning


def test_contract_edit_after_enrollment_is_stale(tmp_path: Path) -> None:
    root = enrolled_project(tmp_path)
    (root / CONTRACT).write_text("contract_id: X\n", encoding="utf-8")
    assert check_enrollment_status(root, CONTRACT) == "stale"
    assert "expected_contract_digest" in stale_enrollment_warning(root, CONTRACT)


def test_contract_deletion_after_enrollment_is_stale_not_silent(tmp_path: Path) -> None:
    """codex #15: reverting to a pre-intent-substrate state must be LOUD."""
    root = enrolled_project(tmp_path)
    (root / CONTRACT).unlink()
    assert check_enrollment_status(root, CONTRACT) == "stale"


def test_pre_commit_hook_removal_after_enrollment_is_stale(tmp_path: Path) -> None:
    root = enrolled_project(tmp_path)
    (root / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    assert check_enrollment_status(root, CONTRACT) == "stale"


def test_unrelated_pre_commit_edit_does_not_false_positive(tmp_path: Path) -> None:
    root = enrolled_project(tmp_path)
    (root / ".pre-commit-config.yaml").write_text(
        "# a comment\nrepos:\n  - repo: local\n    hooks:\n"
        "      - id: intent-weaken-edit-flag\n      - id: unrelated-hook\n",
        encoding="utf-8",
    )
    assert check_enrollment_status(root, CONTRACT) == "current"


def test_unknown_schema_version_is_stale(tmp_path: Path) -> None:
    root = enrolled_project(tmp_path)
    text = enrollment_path(root).read_text(encoding="utf-8").replace("schema_version: 1", "schema_version: 99")
    enrollment_path(root).write_text(text, encoding="utf-8")
    assert check_enrollment_status(root, CONTRACT) == "stale"


def test_malformed_marker_is_stale_not_never_enrolled(tmp_path: Path) -> None:
    root = enrolled_project(tmp_path)
    enrollment_path(root).write_text("{[not yaml", encoding="utf-8")
    assert check_enrollment_status(root, CONTRACT) == "stale"


def test_digests_are_stable_and_cover_the_three_artifacts(tmp_path: Path) -> None:
    root = enrolled_project(tmp_path)
    first = compute_digests(root, CONTRACT)
    assert set(first) == {
        "expected_contract_digest",
        "expected_hook_digest",
        "expected_pre_commit_config_digest",
    }
    assert compute_digests(root, CONTRACT) == first


# ── PRD-SEC-013 probe round: working-tree deletion of the marker ───────────


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, shell=False)


def test_working_tree_deletion_of_a_tracked_marker_is_stale_not_never_enrolled(tmp_path: Path) -> None:
    """Deleting enrollment.yaml from the WORKING TREE must not disarm the hooks.

    Git tracking is the durable second signal: an uncommitted `rm` leaves the
    file in the index/HEAD, so the checks read `stale` (fail closed) instead of
    the silent `never_enrolled` no-op.
    """
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    root = enrolled_project(tmp_path)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "sec013@example.test")
    _git(root, "config", "user.name", "sec013")
    _git(root, "add", "-A")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "enroll")

    assert check_enrollment_status(root, CONTRACT) == "current"

    (root / ".trw/contracts/enrollment.yaml").unlink()
    assert check_enrollment_status(root, CONTRACT) == "stale"
    assert "enrollment marker" in stale_enrollment_warning(root, CONTRACT)


def test_untracked_absent_marker_is_still_a_clean_no_op(tmp_path: Path) -> None:
    """A project that never enrolled stays a silent no-op, git repo or not."""
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    root = tmp_path / "fresh"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    assert check_enrollment_status(root, CONTRACT) == "never_enrolled"


# ── DX regression: CLI ``--root`` used to be silently ignored ──────────────


def test_cli_enroll_honors_explicit_root_and_not_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI used to write the marker under cwd-derived ``repo_root()`` even
    when an explicit ``--root`` was passed. It must land under the passed root."""
    from trw_mcp.security.intent_contract import enrollment as enrollment_module

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    _install_hooks(target)
    monkeypatch.setattr(enrollment_module, "repo_root", lambda: decoy)

    exit_code = enrollment_module.main(["enroll", "--root", str(target)])

    assert exit_code == 0
    assert (target / ".trw/contracts/enrollment.yaml").exists()
    assert not (decoy / ".trw/contracts").exists()


def test_cli_enroll_without_root_still_falls_back_to_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: omitting ``--root`` must keep the pre-existing behavior."""
    from trw_mcp.security.intent_contract import enrollment as enrollment_module

    fallback = tmp_path / "fallback"
    fallback.mkdir()
    _install_hooks(fallback)
    monkeypatch.setattr(enrollment_module, "repo_root", lambda: fallback)

    exit_code = enrollment_module.main(["enroll"])

    assert exit_code == 0
    assert (fallback / ".trw/contracts/enrollment.yaml").exists()


# ── Truthfulness: enrolling over ABSENT hooks was a vacuous success ─────────
#
# compute_digests digests `<root>/.claude/hooks/{pre,post}-tool-intent-*.sh`, and
# an absent file digests as `<absent>` — which write_enrollment then recorded as
# the EXPECTED state. `enroll` printed success and `status` printed `current` for
# a project where nothing was installed to run. This repo was itself in that
# state on 2026-07-25.


def test_enroll_refuses_when_no_intent_hook_is_installed(tmp_path: Path) -> None:
    with pytest.raises(EnrollmentError) as caught:
        write_enrollment(tmp_path, CONTRACT)

    message = str(caught.value)
    for hook in _INTENT_HOOKS:
        assert hook in message, f"the refusal must name {hook}"
    assert str(tmp_path / ".claude/hooks") in message, "it must name WHERE it looked"
    assert not enrollment_path(tmp_path).exists(), "a refused enrollment must leave no marker"
    assert check_enrollment_status(tmp_path, CONTRACT) == "never_enrolled"


def test_enroll_succeeds_once_the_hooks_are_installed(tmp_path: Path) -> None:
    """The positive control: the refusal must not block a real enrollment."""
    (tmp_path / ".trw/contracts").mkdir(parents=True)
    (tmp_path / CONTRACT).write_text("contract_id: X\nmust_not_happen: []\n", encoding="utf-8")
    _install_hooks(tmp_path)

    write_enrollment(tmp_path, CONTRACT)

    assert missing_intent_hooks(tmp_path) == ()
    assert check_enrollment_status(tmp_path, CONTRACT) == "current"


def test_one_missing_hook_is_enough_to_refuse(tmp_path: Path) -> None:
    """Half-installed is not installed: either hook alone leaves a hole."""
    _install_hooks(tmp_path)
    (tmp_path / ".claude/hooks" / _INTENT_HOOKS[1]).unlink()

    assert missing_intent_hooks(tmp_path) == (f".claude/hooks/{_INTENT_HOOKS[1]}",)
    with pytest.raises(EnrollmentError):
        write_enrollment(tmp_path, CONTRACT)


def test_a_missing_shared_library_alone_does_not_refuse(tmp_path: Path) -> None:
    """lib-trw.sh absent still leaves an enrolled hook that runs and fails CLOSED
    (probe finding N6), so it is not the can-never-fire case."""
    _install_hooks(tmp_path)
    assert missing_intent_hooks(tmp_path) == ()
    write_enrollment(tmp_path, CONTRACT)
    assert enrollment_path(tmp_path).exists()


def test_cli_enroll_exits_nonzero_and_explains_when_hooks_are_absent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.security.intent_contract import enrollment as enrollment_module

    exit_code = enrollment_module.main(["enroll", "--root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "REFUSED" in captured.err
    assert "enrolled:" not in captured.out, "it must not also claim success"
    assert not enrollment_path(tmp_path).exists()


def test_cli_enroll_opt_out_pre_enrolls_but_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An operator CAN pre-enroll deliberately — it just must not look like protection."""
    from trw_mcp.security.intent_contract import enrollment as enrollment_module

    exit_code = enrollment_module.main(["enroll", "--allow-missing-hooks", "--root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert enrollment_path(tmp_path).exists()
    assert "WARNING" in captured.out and "no intent hook is installed" in captured.out


def test_cli_refuses_a_dangling_root_instead_of_using_the_default_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Finding F5 (2026-07-25): `enroll --root` (no value) enrolled the WRONG project.

    ``parse_root_arg`` deliberately hands a valueless ``--root`` back rather than
    swallowing it, and the CLI then dispatched on ``args[0] == "enroll"``, ignored
    the dangling flag, resolved ``repo_root()`` and exited 0 — minting a marker in
    a project the operator never named. A docstring in test_intent_contract_paths.py
    asserted the opposite ("the CLI then fails its own usage check and exits 2");
    it was never true.
    """
    from trw_mcp.security.intent_contract import enrollment as enrollment_module

    wrong_root = tmp_path / "wrong"
    (wrong_root / ".trw/contracts").mkdir(parents=True)
    _install_hooks(wrong_root)
    monkeypatch.setattr(enrollment_module, "repo_root", lambda: wrong_root)

    exit_code = enrollment_module.main(["enroll", "--root"])

    captured = capsys.readouterr()
    assert exit_code == 2, "a dangling --root must be a usage error, not a silent default"
    assert "unrecognized argument(s): --root" in captured.err
    assert not enrollment_path(wrong_root).exists(), "no marker may be written under the unintended root"


def test_cli_still_accepts_a_well_formed_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Non-vacuous counterpart: the refusal must not break the legitimate form."""
    from trw_mcp.security.intent_contract import enrollment as enrollment_module

    (tmp_path / ".trw/contracts").mkdir(parents=True)
    _install_hooks(tmp_path)
    assert enrollment_module.main(["enroll", "--root", str(tmp_path)]) == 0
    assert enrollment_path(tmp_path).exists()
    assert "unrecognized" not in capsys.readouterr().err


def test_ledger_cli_refuses_a_dangling_root_too(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Same shape, same fix: `verify --root` reported on the DEFAULT root's chain."""
    del tmp_path
    from trw_mcp.security.intent_contract import ledger as ledger_module

    assert ledger_module.main(["verify", "--root"]) == 2
    assert "unrecognized argument(s): --root" in capsys.readouterr().err


def test_cli_status_distinguishes_current_from_current_with_no_hooks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`current` alone was read as "protection is on"."""
    from trw_mcp.security.intent_contract import enrollment as enrollment_module

    (tmp_path / ".trw/contracts").mkdir(parents=True)
    (tmp_path / CONTRACT).write_text("contract_id: X\nmust_not_happen: []\n", encoding="utf-8")
    write_enrollment(tmp_path, CONTRACT, allow_missing_hooks=True)

    assert enrollment_module.main(["status", "--root", str(tmp_path)]) == 0
    hookless = capsys.readouterr()
    assert hookless.out.strip() == "current (NO HOOKS INSTALLED — nothing is registered to run)"
    assert "missing intent hooks" in hookless.err

    _install_hooks(tmp_path)
    write_enrollment(tmp_path, CONTRACT)
    assert enrollment_module.main(["status", "--root", str(tmp_path)]) == 0
    installed = capsys.readouterr()
    assert installed.out.strip() == "current", "the honest case must stay a plain `current`"
    assert installed.err == ""


# ── PRD-CORE-254: the glob sidecar written alongside the marker ─────────────
#
# The sidecar is the ONLY thing that lets the shell answer "no claim anchors
# this path" without an interpreter. Every test below asserts on the artifact
# the hooks actually parse, byte for byte, because a renderer that agreed with
# `anchor_matches` in Python but not with POSIX `case` would be invisible here
# and wrong in production.

_CLAIM_TEMPLATE = (
    "contract_id: X\n"
    "must_not_happen:\n"
    "  - claim_id: C-1\n"
    '    text: "no"\n'
    "    authority_class: policy_derived\n"
    "    state: {state}\n"
    "    machine_checkable: {machine_checkable}\n"
    "    binding_channel: {channel}\n"
    "    anchors: {anchors}\n"
)


def _sidecar_lines(root: Path) -> list[str]:
    from trw_mcp.security.intent_contract._sidecar import glob_sidecar_path

    text = glob_sidecar_path(root).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def _project_with_claim(tmp_path: Path, **claim: str) -> Path:
    fields = {"state": "active", "machine_checkable": "true", "channel": "blocking_hook", "anchors": '["src/a.py"]'}
    fields.update(claim)
    (tmp_path / ".trw/contracts").mkdir(parents=True)
    (tmp_path / CONTRACT).write_text(_CLAIM_TEMPLATE.format(**fields), encoding="utf-8")
    _install_hooks(tmp_path)
    write_enrollment(tmp_path, CONTRACT)
    return tmp_path


def test_refresh_hooks_writes_glob_sidecar(tmp_path: Path) -> None:
    """FR01: enrol/refresh emit one pattern set per eligible anchor + the digest.

    The trailing line is asserted against a FRESH ``compute_digests`` call rather
    than against the marker, so a writer that recorded its own idea of the
    contract digest would fail here.
    """
    from trw_mcp.security.intent_contract.enrollment import refresh_hook_digest

    root = _project_with_claim(tmp_path)
    lines = _sidecar_lines(root)

    assert lines[0] == "p src/a.py"
    assert lines[1] == "p src/a.py/*"
    assert lines[-1] == "sha256:" + compute_digests(root, CONTRACT)["expected_contract_digest"]

    (root / ".claude/hooks/pre-tool-intent-guard.sh").write_text("#!/bin/sh\nexit 0\n# v2\n", encoding="utf-8")
    assert refresh_hook_digest(root) is True
    assert _sidecar_lines(root)[-1] == "sha256:" + compute_digests(root, CONTRACT)["expected_contract_digest"]
    assert check_enrollment_status(root, CONTRACT) == "current"


def test_a_glob_anchor_renders_unchanged_and_a_file_anchor_renders_both_forms(tmp_path: Path) -> None:
    """FR01: the rendering reproduces ``anchor_matches``, including its two branches."""
    root = _project_with_claim(tmp_path, anchors='["src/**/*.py", "docs/", "src/b.py"]')

    patterns = [line[2:] for line in _sidecar_lines(root) if line.startswith("p ")]
    assert patterns == ["src/**/*.py", "docs", "docs/*", "src/b.py", "src/b.py/*"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("state", "suspect"), ("machine_checkable", "false"), ("channel", "advisory")],
)
def test_only_enforceable_claims_are_rendered(tmp_path: Path, field: str, value: str) -> None:
    """FR01: the sidecar can never be WIDER than what the hooks enforce.

    An ineligible claim rendered here would defer writes Python allows — merely
    slow. The dangerous direction is the other one, and it is covered by
    ``eligible_claims`` being the single filter both layers call.
    """
    root = _project_with_claim(tmp_path, **{field: value})

    assert [line for line in _sidecar_lines(root) if line.startswith("p ")] == []


def test_a_contract_with_no_eligible_claims_still_binds_its_digest(tmp_path: Path) -> None:
    """FR01: zero patterns is a legitimate, fully-usable sidecar — every path is fast."""
    root = enrolled_project(tmp_path)
    lines = _sidecar_lines(root)

    assert [line for line in lines if line.startswith("p ")] == []
    assert lines[-1].startswith("sha256:")
    assert any(line.startswith("g1 ") for line in lines), "the freshness lines must still be written"


def test_guarded_artifacts_cover_every_digested_file(tmp_path: Path) -> None:
    """The drift guard: what the marker digests and what the shell watches are ONE set.

    ``compute_digests`` hashing a file the sidecar does not list would let that
    file change without the hooks noticing — the marker would read ``stale``
    (Python blocks) while the fast path kept answering ALLOW.
    """
    from trw_mcp.security.intent_contract._control_plane import HOOK_SUPPORT_FILES, INTENT_HOOK_FILES
    from trw_mcp.security.intent_contract._sidecar import guarded_artifacts
    from trw_mcp.security.intent_contract.paths import PRE_COMMIT_CONFIG_PATH

    digested = {
        CONTRACT,
        PRE_COMMIT_CONFIG_PATH,
        *(f".claude/hooks/{name}" for name in (*INTENT_HOOK_FILES, *HOOK_SUPPORT_FILES)),
    }

    assert set(guarded_artifacts(CONTRACT)) == digested


def test_the_sidecar_records_presence_so_an_appearing_artifact_is_visible(tmp_path: Path) -> None:
    """A guarded file that did not exist at write time is recorded as ``g0``.

    ``.pre-commit-config.yaml`` appearing later CHANGES
    ``expected_pre_commit_config_digest`` and makes the marker stale, so the
    shell has to be able to see an absence become a presence.
    """
    root = _project_with_claim(tmp_path)
    lines = _sidecar_lines(root)

    assert "g0 .pre-commit-config.yaml" in lines
    (root / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    write_enrollment(root, CONTRACT)
    assert "g1 .pre-commit-config.yaml" in _sidecar_lines(root)


def test_an_unloadable_contract_leaves_no_sidecar(tmp_path: Path) -> None:
    """A contract Python refuses to load is a BLOCK, so the shell gets no shortcut."""
    from trw_mcp.security.intent_contract._sidecar import glob_sidecar_path

    root = _project_with_claim(tmp_path)
    assert glob_sidecar_path(root).exists()

    (root / CONTRACT).write_text("must_not_happen: [not-a-mapping]\n", encoding="utf-8")
    write_enrollment(root, CONTRACT)

    assert not glob_sidecar_path(root).exists(), "an unloadable contract must not leave a usable sidecar"


def test_refresh_hooks_removes_the_sidecar_when_the_contract_drifted(tmp_path: Path) -> None:
    """FR05/FR06: refresh-hooks re-blesses the HOOK half only, so a drifted
    contract keeps the project stale — and a stale project must have no sidecar.

    Rendering one here would have been the bypass: patterns from the NEW contract,
    a digest line matching the OLD marker, and Python still blocking every write.
    """
    from trw_mcp.security.intent_contract._sidecar import glob_sidecar_path
    from trw_mcp.security.intent_contract.enrollment import refresh_hook_digest

    root = _project_with_claim(tmp_path)
    (root / CONTRACT).write_text(
        _CLAIM_TEMPLATE.format(
            state="active", machine_checkable="true", channel="blocking_hook", anchors='["src/a.py", "src/c.py"]'
        ),
        encoding="utf-8",
    )
    (root / ".claude/hooks/pre-tool-intent-guard.sh").write_text("#!/bin/sh\nexit 0\n# v2\n", encoding="utf-8")

    refresh_hook_digest(root)

    assert check_enrollment_status(root, CONTRACT) == "stale"
    assert not glob_sidecar_path(root).exists()


def test_refresh_hooks_cli_updates_hook_and_sidecar_digests(tmp_path: Path) -> None:
    """FR06: one named command re-blesses the hook digest AND the sidecar.

    The vendor-resync case end to end: new hook bytes make the marker stale (both
    control points then block every write); ``refresh-hooks`` restores ``current``
    with no manual marker edit, and leaves a sidecar bound to the live contract.
    """
    from trw_mcp.security.intent_contract.enrollment import main

    root = _project_with_claim(tmp_path)
    (root / ".claude/hooks/post-tool-intent-check.sh").write_text("#!/bin/sh\nexit 0\n# vendor v2\n", encoding="utf-8")
    assert check_enrollment_status(root, CONTRACT) == "stale"

    assert main(["refresh-hooks", "--root", str(root)]) == 0

    assert check_enrollment_status(root, CONTRACT) == "current"
    assert _sidecar_lines(root)[-1] == "sha256:" + compute_digests(root, CONTRACT)["expected_contract_digest"]
    assert main(["status", "--root", str(root)]) == 0


def test_refresh_hooks_on_a_never_enrolled_clone_writes_nothing(tmp_path: Path) -> None:
    """FR06/OQ3: ``check-bundle-sync.sh --fix`` runs this unconditionally.

    A clone that never opted in must come back untouched — no marker, no evidence
    file, and no sidecar. Minting any of them here would arm a project on the
    strength of an installer action (finding F-E).
    """
    from trw_mcp.security.intent_contract._sidecar import glob_sidecar_path
    from trw_mcp.security.intent_contract.enrollment import enrollment_evidence_path, main

    _install_hooks(tmp_path)

    assert main(["refresh-hooks", "--root", str(tmp_path)]) == 0

    assert not enrollment_path(tmp_path).exists()
    assert not enrollment_evidence_path(tmp_path).exists()
    assert not glob_sidecar_path(tmp_path).exists()


def test_the_sidecar_is_never_newer_than_the_marker(tmp_path: Path) -> None:
    """The ordering the hooks rely on: sidecar first, marker stamped after it.

    The hooks refuse a sidecar newer than the marker — the only signal available
    to them that this file was written outside an enrollment. Both writers are
    checked, including the refresh that changes no digest at all.
    """
    from trw_mcp.security.intent_contract._sidecar import glob_sidecar_path
    from trw_mcp.security.intent_contract.enrollment import refresh_hook_digest

    root = _project_with_claim(tmp_path)
    assert glob_sidecar_path(root).stat().st_mtime <= enrollment_path(root).stat().st_mtime

    assert refresh_hook_digest(root) is False, "nothing changed, so no digest should move"
    assert glob_sidecar_path(root).stat().st_mtime <= enrollment_path(root).stat().st_mtime


def test_the_named_remedy_command_is_wired_into_both_resync_paths() -> None:
    """FR06 wiring: the Makefile target and ``--fix`` both call the same CLI.

    A remedy the block message names but no target implements is the shape this
    FR exists to remove, so the wiring is asserted rather than assumed.
    """
    repo = Path(__file__).resolve().parents[2]
    makefile = (repo / "Makefile").read_text(encoding="utf-8")
    sync = (repo / "scripts" / "check-bundle-sync.sh").read_text(encoding="utf-8")
    command = "trw_mcp.security.intent_contract.enrollment refresh-hooks"

    assert "\nrefresh-enrollment:" in makefile
    assert command in makefile
    assert command in sync
    assert 'FIX_MODE" = true' in sync.split(command)[0].rsplit("\n\n", 1)[-1]


def test_the_stale_warning_names_the_remedy_command(tmp_path: Path) -> None:
    """FR06: the unsuppressible warning names BOTH commands and which is which."""
    root = _project_with_claim(tmp_path)
    (root / ".claude/hooks/pre-tool-intent-guard.sh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    warning = stale_enrollment_warning(root, CONTRACT)

    assert "make refresh-enrollment" in warning
    assert "enrollment enroll" in warning
