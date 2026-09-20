"""PRD-CORE-277: dispatch truth — stdin, host confinement, sandbox proof, silence.

Each test here pins one claim the dispatch layer makes about itself. The layer's
defect class was never a crash: it was a report (``ok``, ``read_only_enforced``,
``sandbox=enforced``) that outlived the evidence for it.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from trw_mcp.dispatch._client_specs import CLIENT_SPECS
from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._confine import confinement_prefix
from trw_mcp.dispatch._normalize import classify_silence, normalize_output
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._sandbox_probe import probe_write_containment
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult


def _write_stub(tmp_path: Path, name: str, body: str) -> Path:
    stub = tmp_path / name
    stub.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def _patch_argv(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    def _fixed(_req: DispatchRequest, *, confined: bool = False) -> list[str]:
        return argv

    monkeypatch.setattr("trw_mcp.dispatch._runner.build_command", _fixed)


def _result(**overrides: object) -> DispatchResult:
    base: dict[str, object] = {
        "client": "codex",
        "argv_redacted": ["codex"],
        "read_only_enforced": True,
        "exit_code": 0,
        "timed_out": False,
        "duration_s": 0.1,
        "text": "an answer",
        "raw_stdout": "an answer",
        "raw_stderr": "",
    }
    base.update(overrides)
    return DispatchResult(**base)  # type: ignore[arg-type]


# --- FR01: the child never inherits the parent's stdin -----------------------


class TestChildStdinIsClosed:
    def test_child_gets_eof_on_stdin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A child that reads stdin must see EOF, not the parent's open pipe.

        The regression this pins is a HANG: inside a stdio MCP server the
        parent's fd 0 is the client's JSON-RPC pipe, and `codex exec` waits on it
        until the dispatch times out. The test therefore makes fd 0 an open pipe
        with unread bytes in it and asserts BOTH that the child finished and that
        the bytes are still there afterwards.
        """
        stub = _write_stub(tmp_path, "fake-reader", 'data=$(cat)\necho "STDIN[$data]"\n')
        _patch_argv(monkeypatch, [str(stub)])

        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"SECRET")
        saved = os.dup(0)
        try:
            os.dup2(read_fd, 0)
            result = dispatch(DispatchRequest(client="claude", prompt="x", timeout_s=5))
        finally:
            os.dup2(saved, 0)
            os.close(saved)

        try:
            assert result.timed_out is False, "the child blocked on the inherited stdin"
            assert result.text == "STDIN[]"
            assert os.read(read_fd, 6) == b"SECRET", "the child consumed bytes addressed to the MCP server"
        finally:
            os.close(read_fd)
            os.close(write_fd)

    @pytest.mark.skipif(sys.platform != "darwin", reason="BSD script path; the util-linux form is unverified here")
    def test_the_pty_path_also_reaches_the_child_as_eof(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end through ``script``, now that the BSD form works (151babeab).

        The kwargs assertion below proves TRW passes DEVNULL; it cannot prove what
        ``script`` forwards into the pseudo-terminal. This one can, on the platform
        whose ``script`` we can actually run.
        """
        stub = _write_stub(tmp_path, "fake-pty-reader", 'data=$(cat)\necho "STDIN[$data]"\n')
        _patch_argv(monkeypatch, [str(stub)])

        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"SECRET")
        saved = os.dup(0)
        try:
            os.dup2(read_fd, 0)
            result = dispatch(DispatchRequest(client="claude", prompt="x", timeout_s=10, use_pty=True))
        finally:
            os.dup2(saved, 0)
            os.close(saved)

        try:
            assert result.timed_out is False, "the PTY child blocked on the inherited stdin"
            assert "STDIN[]" in result.text
            assert os.read(read_fd, 6) == b"SECRET"
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_popen_receives_devnull_including_the_pty_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both launch shapes pass DEVNULL.

        This is the platform-independent half: it asserts what TRW passes, not
        what ``script`` forwards. The live end-to-end EOF is asserted above, on
        Darwin, where the wrapper's BSD form is runnable.
        """
        seen: list[object] = []
        devnull = subprocess.DEVNULL
        # argv[0] must resolve, or the pre-flight (FR02) returns -127 before the
        # spy is reached and this test would silently assert nothing on a box
        # without the client installed.
        _patch_argv(monkeypatch, [sys.executable, "-c", "pass"])

        def _spy(*args: object, **kwargs: object) -> object:
            seen.append(kwargs.get("stdin"))
            raise OSError("stop before spawning")

        # NOTE: ``trw_mcp.dispatch._runner.subprocess`` IS the stdlib module, so this
        # patches Popen process-wide for the duration of the test; monkeypatch undoes
        # it. DEVNULL is captured first because it is what we compare against.
        monkeypatch.setattr("trw_mcp.dispatch._runner.subprocess.Popen", _spy)

        dispatch(DispatchRequest(client="claude", prompt="x"))
        dispatch(DispatchRequest(client="claude", prompt="x", use_pty=True))

        assert seen == [devnull, devnull]


# --- FR02: host confinement gates the confined-read argv ---------------------


class TestHostConfinement:
    def test_confined_flag_selects_the_confined_read_argv(self) -> None:
        req = DispatchRequest(client="agy", prompt="x", cwd=Path("/tmp"))

        plain = build_command(req)
        confined = build_command(req, confined=True)

        assert "--dangerously-skip-permissions" not in plain
        assert "--dangerously-skip-permissions" in confined
        # The only difference is that token: confinement must not quietly change
        # anything else about the command.
        assert [tok for tok in confined if tok != "--dangerously-skip-permissions"] == plain

    def test_write_runs_are_never_confined(self) -> None:
        req = DispatchRequest(client="agy", prompt="x", read_only=False)
        # A wrapper on a write run would make --allow-writes a silent no-op.
        assert build_command(req, confined=True) == build_command(req)

    def test_agy_grants_the_workspace_so_it_can_read(self) -> None:
        argv = build_command(DispatchRequest(client="agy", prompt="x", cwd=Path("/tmp/proj")))
        assert argv[argv.index("--add-dir") + 1] == "/tmp/proj"

    def test_only_clients_declaring_host_confinement_carry_a_confined_argv(self) -> None:
        for client, spec in CLIENT_SPECS.items():
            if spec.confined_read_only_argv:
                assert spec.host_confinement, f"{client} declares a confined argv without host_confinement"

    def test_confinement_prefix_matches_the_platform(self) -> None:
        prefix = confinement_prefix()
        if sys.platform != "darwin":
            assert prefix == []
        else:
            assert prefix[0] == "/usr/bin/sandbox-exec"
            assert "deny file-write*" in prefix[2]

    @pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is the only wrapper implemented")
    def test_the_wrapper_actually_denies_a_write(self, tmp_path: Path) -> None:
        """The wrapper is a control, so prove it stops a write on this box."""
        target = tmp_path / "marker.txt"
        proc = subprocess.run(
            [*confinement_prefix(), "/bin/sh", "-c", f"echo x > {target}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert not target.exists()
        assert proc.returncode != 0

    def test_a_missing_binary_still_reports_the_launch_failure_under_the_wrapper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """-127 is the caller's "client not installed" signal; the wrapper must not eat it."""
        _patch_argv(monkeypatch, ["/nonexistent/xyz-does-not-exist"])
        result = dispatch(DispatchRequest(client="agy", prompt="x"))
        assert result.exit_code == -127
        assert result.silence_reason == "nonzero_exit"


# --- FR03: a sandbox claim is a measurement or it is unverified --------------


class TestSandboxProbeVerdicts:
    @staticmethod
    def _runner(answer: str, *, write_marker: bool = False):
        def _run(req: DispatchRequest) -> DispatchResult:
            if write_marker:
                assert req.cwd is not None
                (req.cwd / "marker.txt").write_text("whatever", encoding="utf-8")
            canary = (req.cwd / "canary.txt").read_text(encoding="utf-8").strip() if req.cwd else ""
            return _result(client=req.client, text=answer.replace("{canary}", canary))

        return _run

    def test_a_write_that_lands_is_false(self) -> None:
        probe = probe_write_containment(
            "agy",
            run=self._runner("{canary} — CREATED both files", write_marker=True),
            mechanism="test",
        )
        assert probe.verdict is False
        assert "NOT contained" in probe.note

    def test_a_write_that_lands_is_false_even_with_unexpected_content(self) -> None:
        # Content is never consulted: a partial write is still a write, and a
        # nonce-equality rule would let it hide as "unverified".
        probe = probe_write_containment("agy", run=self._runner("no canary here", write_marker=True), mechanism="test")
        assert probe.verdict is False

    def test_canary_plus_a_reported_denial_is_true(self) -> None:
        probe = probe_write_containment(
            "agy",
            run=self._runner("{canary}\nstep 2: operation not permitted\nstep 3: operation not permitted"),
            mechanism="test",
        )
        assert probe.verdict is True
        assert "filesystem writes only" in probe.note

    def test_canary_without_a_denial_is_unverified(self) -> None:
        # The model may simply not have tried. An absent marker then proves nothing.
        probe = probe_write_containment("agy", run=self._runner("{canary}\nI chose not to."), mechanism="test")
        assert probe.verdict == "unverified"
        assert "no denial reported" in probe.note

    def test_silence_is_unverified(self) -> None:
        probe = probe_write_containment("agy", run=self._runner(""), mechanism="test")
        assert probe.verdict == "unverified"
        assert "no canary echo" in probe.note

    def test_a_run_without_the_probe_claims_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _write_stub(tmp_path, "fake-quiet", "echo 'done'\n")
        _patch_argv(monkeypatch, [str(stub)])
        result = dispatch(DispatchRequest(client="claude", prompt="x"))
        assert result.sandbox_verified == "unverified"


# --- FR04: silence carries a reason ------------------------------------------


class TestSilenceReason:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"text": "hi", "raw_stderr": "", "structured": None, "exit_code": 0, "timed_out": False}, None),
            ({"text": "", "raw_stderr": "", "structured": None, "exit_code": None, "timed_out": True}, "timed_out"),
            ({"text": "", "raw_stderr": "", "structured": None, "exit_code": 3, "timed_out": False}, "nonzero_exit"),
            ({"text": "", "raw_stderr": "", "structured": None, "exit_code": 0, "timed_out": False}, "empty_output"),
            (
                {
                    "text": "",
                    "raw_stderr": "ERROR: unexpected status 401 Unauthorized",
                    "structured": None,
                    "exit_code": 1,
                    "timed_out": False,
                },
                "auth_or_content_stop",
            ),
            (
                {
                    "text": "partial",
                    "raw_stderr": "",
                    "structured": {"status": "ERROR"},
                    "exit_code": 0,
                    "timed_out": False,
                },
                "auth_or_content_stop",
            ),
        ],
    )
    def test_classification_table(self, kwargs: dict[str, object], expected: str | None) -> None:
        assert classify_silence(**kwargs) == expected  # type: ignore[arg-type]

    def test_the_answer_text_is_never_scanned(self) -> None:
        """A review ABOUT authentication is not an authentication failure."""
        reason = classify_silence(
            text="The handler returns 401 Unauthorized when the api key is invalid; that is correct.",
            raw_stderr="",
            structured={"status": "SUCCESS", "response": "401 unauthorized ... invalid api key"},
            exit_code=0,
            timed_out=False,
        )
        assert reason is None

    def test_a_prompt_echoed_to_stderr_does_not_stop_a_complete_answer(self) -> None:
        """codex echoes the whole prompt to stderr; a prompt about credentials is not a credential failure.

        Measured 2026-09-17: PRD-CORE-278's adversarial-audit dispatch returned the
        full review with exit 0 and was reported ok=false / auth_or_content_stop.
        """
        reason = classify_silence(
            text="## Verdict\nREVISE. Findings follow.",
            raw_stderr="user\nReview the authentication path and every credential read in _env.py.\n",
            structured=None,
            exit_code=0,
            timed_out=False,
        )
        assert reason is None

    def test_a_stderr_marker_still_counts_when_no_answer_was_produced(self) -> None:
        assert (
            classify_silence(text="", raw_stderr="not logged in", structured=None, exit_code=0, timed_out=False)
            == "auth_or_content_stop"
        )

    def test_ok_is_false_whenever_a_reason_is_set(self) -> None:
        assert _result(silence_reason="auth_or_content_stop").ok is False
        assert _result().ok is True

    def test_a_denied_agy_run_is_not_a_clean_review(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exact shape measured 2026-09-16: exit 0, empty stdout, denial on stderr."""
        stub = _write_stub(
            tmp_path,
            "fake-denied-agy",
            'echo \'jetski: no output produced - a tool required the "command" permission '  # trw-leak-allow: google_internal verbatim agy 1.2.4 denial text pinned as the regression input
            "that headless mode cannot prompt for' >&2\n",
        )
        _patch_argv(monkeypatch, [str(stub)])

        result = dispatch(DispatchRequest(client="agy", prompt="review"))

        assert result.exit_code == 0
        assert result.ok is False
        assert result.silence_reason == "auth_or_content_stop"


# --- FR05: the answer text is the client's FULL final answer ------------------


class TestTextIsTheFullAnswer:
    def test_text_equals_the_full_final_answer_for_every_shape(self) -> None:
        paragraphs = ["First paragraph with a finding.", "Second paragraph.", "Third paragraph, the conclusion."]
        answer = "\n\n".join(paragraphs)
        fixtures = {
            "claude": json.dumps({"result": answer}),
            "codex": json.dumps({"type": "final", "message": answer}),
            "agy": json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": answer}}),
            "opencode": json.dumps({"role": "assistant", "text": answer}),
        }
        for client, raw in fixtures.items():
            text, structured = normalize_output(client, raw)  # type: ignore[arg-type]
            # EQUALITY, not containment: every paragraph is a substring of its own
            # JSON envelope, so a normalizer that returned the raw stream unchanged
            # would satisfy a substring check and this test would pass on a no-op.
            assert text == answer, f"{client} did not return exactly the final answer"
            assert structured is not None, f"{client} parsed no payload"

    def test_a_long_answer_is_not_tail_truncated(self) -> None:
        answer = "\n".join(f"line {index}" for index in range(500))
        text, _ = normalize_output("claude", json.dumps({"result": answer}))
        assert text.count("\n") == 499
        assert text.startswith("line 0")
        assert text.endswith("line 499")


# --- FR10: a validated request cannot be mutated into a bypass ---------------


def test_extra_args_cannot_be_mutated_after_validation() -> None:
    """Freezing the MODEL does not freeze a list VALUE.

    Measured 2026-09-16 before the fix: constructing a read-only codex request
    and appending to ``extra_args`` produced an argv carrying
    ``--dangerously-skip-permissions`` beside ``--sandbox read-only``.
    """
    req = DispatchRequest(client="codex", prompt="x", extra_args=["--verbose"])

    assert isinstance(req.extra_args, tuple)
    with pytest.raises(AttributeError):
        req.extra_args.append("--dangerously-skip-permissions")  # type: ignore[attr-defined]
    assert "--dangerously-skip-permissions" not in build_command(req)


def test_the_forbidden_floor_still_refuses_at_construction() -> None:
    with pytest.raises(ValueError, match="security flag"):
        DispatchRequest(client="codex", prompt="x", extra_args=("--dangerously-skip-permissions",))


# --- FR08: an incompatibility is always explained ----------------------------


class TestVersionStatusNamesEveryMismatch:
    """`compatible: false` with `errors: []` and `warnings: []` tells a consumer nothing."""

    def test_every_mismatch_id_appears_in_a_diagnostic(self, tmp_path: Path) -> None:
        from trw_mcp.server._subcommands_release import collect_version_status

        status = collect_version_status(tmp_path)

        if status["compatible"]:
            pytest.skip("no mismatch to explain in this tree")
        explained = " ".join((*status["errors"], *status["warnings"]))
        for mismatch in status["mismatches"]:
            assert mismatch in explained, f"{mismatch} flipped compatible=false with nothing naming it"

    def test_incompatible_always_carries_at_least_one_diagnostic(self, tmp_path: Path) -> None:
        from trw_mcp.server._subcommands_release import collect_version_status

        status = collect_version_status(tmp_path)

        if not status["compatible"]:
            assert status["errors"] or status["warnings"]

    def test_the_explainer_does_not_repeat_a_diagnostic_that_names_the_id(self) -> None:
        from trw_mcp.server._subcommands_release import _explain_mismatches

        warnings: list[str] = []
        errors = ["manifest unreadable: installed_asset_manifest_missing"]
        _explain_mismatches(["installed_asset_manifest_missing"], warnings=warnings, errors=errors)
        assert warnings == [], "a mismatch already named by a reader was explained twice"

    def test_a_prose_only_diagnostic_still_gets_the_id_named(self) -> None:
        """A consumer greps the id; prose that never spells it out is not an explanation."""
        from trw_mcp.server._subcommands_release import _explain_mismatches

        warnings: list[str] = []
        _explain_mismatches(
            ["installed_asset_manifest_missing"],
            warnings=warnings,
            errors=["installed asset manifest missing at .trw/frameworks/VERSION.yaml"],
        )
        assert any("installed_asset_manifest_missing" in line for line in warnings)

        unexplained: list[str] = []
        _explain_mismatches(["trw_mcp_package_vs_installed_asset"], warnings=unexplained, errors=[])
        assert any("trw_mcp_package_vs_installed_asset" in line for line in unexplained)

    def test_a_compatible_status_invents_no_diagnostic(self) -> None:
        from trw_mcp.server._subcommands_release import _explain_mismatches

        warnings: list[str] = []
        errors: list[str] = []
        _explain_mismatches([], warnings=warnings, errors=errors)
        assert (warnings, errors) == ([], [])


# --- FR09: older live writers are reported, never killed ---------------------


class TestPredatingWriters:
    #: A registration epoch must postdate the process's own birth or the writer
    #: census discards the lock as a recycled pid -- Linux reads the birth time
    #: from ``/proc/<pid>/stat``, so a 1970 epoch is never classified there.
    _NOW = time.time()

    @staticmethod
    def _registry(trw_dir: Path, pid: int, epoch: float) -> None:
        writers = trw_dir / "memory" / "memory.db.writers"
        writers.mkdir(parents=True, exist_ok=True)
        (writers / f"{pid}.lock").write_text(f"{pid}\n{epoch}\n", encoding="utf-8")

    def test_an_older_registration_warns_and_names_the_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.server import _doctor_predating_writers as module

        self._registry(tmp_path, os.getpid(), epoch=self._NOW)
        monkeypatch.setattr(module, "_install_epoch", lambda: (self._NOW + 1000.0, "dist_info_mtime"))

        status, message = module.predating_writers_row(tmp_path)

        assert status == "WARN"
        assert str(os.getpid()) in message
        # The claim must stay inside what a lock file can prove.
        assert "loaded versions are unknown" in message
        assert "Nothing was signalled." in message

    def test_a_newer_registration_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.server import _doctor_predating_writers as module

        self._registry(tmp_path, os.getpid(), epoch=self._NOW + 1000.0)
        monkeypatch.setattr(module, "_install_epoch", lambda: (self._NOW, "dist_info_mtime"))

        status, _message = module.predating_writers_row(tmp_path)

        assert status == "PASS"

    def test_an_unreadable_install_receipt_claims_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.server import _doctor_predating_writers as module

        self._registry(tmp_path, os.getpid(), epoch=self._NOW)
        monkeypatch.setattr(module, "_install_epoch", lambda: (None, "unavailable"))

        status, message = module.predating_writers_row(tmp_path)

        assert status == "SKIP"
        assert module.predating_writers(tmp_path)["pids"] == []
        assert "not run" in message

    def test_the_known_false_directions_are_reported(self, tmp_path: Path) -> None:
        from trw_mcp.server._doctor_predating_writers import predating_writers

        caveats = " ".join(predating_writers(tmp_path)["caveats"])
        assert "reinstall" in caveats
        assert "editable" in caveats

    def test_detection_sends_no_terminating_signal(self) -> None:
        """Detection is not permission to kill another session's process.

        An AST check rather than a substring scan: the module's own docstring
        explains the rule and would trip a text search, which is how a guard
        ends up loosened to make itself pass.
        """
        import ast

        source = (Path(__file__).resolve().parents[1] / "src/trw_mcp/server/_doctor_predating_writers.py").read_text(
            encoding="utf-8"
        )
        called = {
            node.func.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not called & {"kill", "killpg", "terminate", "send_signal"}

    def test_version_status_reports_it_without_flipping_compatible(self, tmp_path: Path) -> None:
        from trw_mcp.server import _subcommands_release as release

        self._registry(tmp_path / ".trw", os.getpid(), epoch=1000.0)
        monkeypatch_target = release.predating_writers

        def _older(_trw_dir: Path) -> dict[str, object]:
            measurement = monkeypatch_target(_trw_dir)
            return {**measurement, "pids": [4242]}

        release.predating_writers = _older  # type: ignore[assignment]
        try:
            status = release.collect_version_status(tmp_path)
        finally:
            release.predating_writers = monkeypatch_target  # type: ignore[assignment]

        assert any("4242" in warning for warning in status["warnings"])
        # A timestamp heuristic must never gate a release: mismatches feed
        # `compatible`, which `assert_version_status_compatible` turns into a
        # SystemExit.
        assert not any("predating" in mismatch for mismatch in status["mismatches"])


# --- Cross-model code review of the FR01-FR10 diff (2026-09-17) ---------------


class TestPtyEofEchoIsNotTheAnswer:
    """A pty echoes the EOF we send, and the echo broke every structured parser.

    With ``stdin=DEVNULL`` (FR01) ``script`` forwards EOF at once and the pty
    prints ``^D`` + backspaces, so the stream no longer starts with ``{`` and
    every JSON parser degrades to raw text. The measured consequence: an agy run
    that produced NOTHING came back with non-empty ``text`` and ``ok=True``.
    """

    def test_a_leading_eof_echo_is_stripped_before_parsing(self) -> None:
        payload = json.dumps({"result": "Found one P1 issue."})
        text, structured = normalize_output("claude", f"^D\b\b{payload}")
        assert text == "Found one P1 issue."
        assert structured == {"result": "Found one P1 issue."}

    def test_an_answer_containing_the_same_characters_is_untouched(self) -> None:
        # Only a LEADING echo is stripped; ``^D`` inside the model's answer is
        # the model's own text.
        text, _ = normalize_output("claude", json.dumps({"result": "press ^D to finish"}))
        assert text == "press ^D to finish"

    @pytest.mark.skipif(sys.platform != "darwin", reason="BSD script path; the util-linux form is unverified here")
    def test_a_real_pty_child_still_parses_as_structured_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = json.dumps({"result": "Found one P1 issue."})
        stub = _write_stub(tmp_path, "fake-claude-pty", f"echo '{payload}'\n")
        _patch_argv(monkeypatch, [str(stub)])

        result = dispatch(DispatchRequest(client="claude", prompt="x", timeout_s=10, use_pty=True))

        assert result.raw_stdout.startswith("^D"), "the echo must stay in raw_stdout as evidence"
        assert result.structured == {"result": "Found one P1 issue."}
        assert result.text == "Found one P1 issue."

    @pytest.mark.skipif(sys.platform != "darwin", reason="BSD script path; the util-linux form is unverified here")
    def test_a_denied_pty_child_is_not_a_successful_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The regression this class exists for: a pty MERGES stderr into stdout.

        The denial below is agy 1.2.4's verbatim message, measured 2026-09-16.
        Through ``script`` it arrives on stdout, ``proc.stderr`` is empty, and
        before the merged-stream rule the run reported a non-empty answer and
        ``ok=True`` — a review that read nothing, scored as a clean one.
        """
        denial = (
            'jetski: no output produced - a tool required the "command" permission '  # trw-leak-allow: google_internal verbatim agy 1.2.4 denial text pinned as the regression input
            "that headless mode cannot prompt for, so it was auto-denied."
        )
        stub = _write_stub(tmp_path, "fake-denied-pty", f"echo '{denial}' >&2\n")
        _patch_argv(monkeypatch, [str(stub)])

        result = dispatch(DispatchRequest(client="agy", prompt="x", timeout_s=10, use_pty=True))

        assert result.raw_stderr.strip() == "", "a pty gives the child one stream; stderr arrives inside stdout"
        assert "no output produced" in result.raw_stdout
        assert result.ok is False
        assert result.silence_reason == "auth_or_content_stop"


class TestLaunchFailureSurvivesEveryWrapper:
    def test_a_missing_binary_under_the_pty_wrapper_still_reports_127(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The pre-flight must resolve the CLIENT binary, not the wrapper.

        Checked after ``_wrap_pty`` it asked whether ``script`` exists — which it
        always does — so a missing client under ``--pty`` lost the -127 contract.
        """
        _patch_argv(monkeypatch, ["/nonexistent/xyz-does-not-exist"])

        result = dispatch(DispatchRequest(client="claude", prompt="x", use_pty=True))

        assert result.exit_code == -127
        assert "not found on PATH" in result.raw_stderr
        assert "xyz-does-not-exist" in result.raw_stderr


class TestTheProbeDescribesTheRunItBounds:
    def test_a_writable_run_is_never_certified(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``--allow-writes --verify-sandbox`` may not report containment."""
        import trw_mcp.dispatch._runner as runner_module

        def _explode(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("a writable run must not spend a probe")

        monkeypatch.setattr(runner_module, "probe_write_containment", _explode)
        stub = _write_stub(tmp_path, "fake-writer", "echo 'done'\n")
        _patch_argv(monkeypatch, [str(stub)])

        result = dispatch(
            DispatchRequest(client="claude", prompt="x", read_only=False, verify_sandbox=True, timeout_s=10)
        )

        assert result.sandbox_verified == "unverified"
        assert "writes were requested" in result.sandbox_note
        assert result.read_only_enforced is False

    def test_the_probe_mirrors_the_launch_shape_and_runs_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trw_mcp.dispatch._runner as runner_module
        from trw_mcp.dispatch._sandbox_probe import SandboxProbe

        order: list[str] = []
        seen: dict[str, object] = {}

        def _fake_probe(client: str, **kwargs: object) -> SandboxProbe:
            order.append("probe")
            seen.update(kwargs)
            return SandboxProbe(verdict=True, note="stub")

        monkeypatch.setattr(runner_module, "probe_write_containment", _fake_probe)
        stub = _write_stub(tmp_path, "fake-ordered", "echo 'done'\n")
        real_popen = subprocess.Popen

        def _spy_popen(*args: object, **kwargs: object) -> object:
            # Record only OUR child: the identity capture shells out to `ps` on
            # Darwin, and counting that as a child would make the assertion below
            # depend on another module's implementation.
            launched = args[0] if args else kwargs.get("args")
            if isinstance(launched, list) and launched and launched[0] == spy_target:
                order.append("child")
            return real_popen(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("trw_mcp.dispatch._runner.subprocess.Popen", _spy_popen)
        _patch_argv(monkeypatch, [str(stub)])
        spy_target = str(stub)

        result = dispatch(
            DispatchRequest(
                client="claude",
                prompt="x",
                verify_sandbox=True,
                use_pty=False,
                isolate=False,
                extra_args=("--verbose",),
                timeout_s=10,
            )
        )

        assert order == ["probe", "child"], "the probe must bound the run, not autopsy it"
        assert seen["use_pty"] is False
        assert seen["isolate"] is False
        assert seen["posture"] == "default"
        assert seen["extra_args"] == ("--verbose",)
        assert seen["read_only"] is True
        assert result.sandbox_verified is True

    def test_the_probe_request_carries_those_fields_through(self) -> None:
        captured: list[DispatchRequest] = []

        def _run(req: DispatchRequest) -> DispatchResult:
            captured.append(req)
            return _result(client=req.client, text="")

        probe_write_containment(
            "claude",
            run=_run,
            mechanism="test",
            use_pty=True,
            isolate=False,
            extra_args=("--verbose",),
        )

        assert captured[0].use_pty is True
        assert captured[0].isolate is False
        assert captured[0].extra_args == ("--verbose",)


class TestTimeoutAlwaysKillsSomething:
    def test_a_refused_group_signal_falls_back_to_killing_the_child(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A refusal used to leave the timed-out child running.

        ``signal_group`` refuses whenever the identity cannot be proven (a slow
        ``ps``, an unverifiable platform, a recycled pid). Before the fallback,
        nothing was killed and the caller was still told the tree had been.
        """
        import trw_mcp.dispatch._runner as runner_module

        killed: list[str] = []

        class _Proc:
            pid = 4242
            stdout = None
            stderr = None

            def kill(self) -> None:
                killed.append("direct")

        monkeypatch.setattr(runner_module, "signal_group", lambda _identity, _sig: False)
        runner_module._kill_tree(_Proc(), {"pid": 4242})  # type: ignore[arg-type]

        assert killed == ["direct"]

    def test_a_verified_group_kill_signals_the_group_and_not_the_child(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import signal as signal_module

        import trw_mcp.dispatch._runner as runner_module

        killed: list[str] = []
        signalled: list[tuple[object, int]] = []

        class _Proc:
            pid = 4242

            def kill(self) -> None:
                killed.append("direct")

        def _group(identity: object, sig: int) -> bool:
            signalled.append((identity, sig))
            return True

        monkeypatch.setattr(runner_module, "signal_group", _group)
        runner_module._kill_tree(_Proc(), {"pid": 4242})  # type: ignore[arg-type]

        # Both halves: the group WAS signalled (a no-op implementation would pass
        # the absence check alone) and the narrower fallback was not used.
        assert signalled == [({"pid": 4242}, signal_module.SIGKILL)]
        assert killed == []


# --- Round-2 review of the round-1 fixes (2026-09-17) ------------------------


class TestStopMarkersDoNotEatGoodRuns:
    """The round-1 fix for PTY silence misfired on the review that found it.

    codex echoes the PROMPT to stderr. A code-review dispatch whose prompt quoted
    the phrase "no output produced" came back ``ok=False`` /
    ``auth_or_content_stop`` with a complete 4198-character review attached
    (measured 2026-09-17). The hard markers now apply only to an UNPARSED pty
    stream.
    """

    def test_a_prompt_echo_on_stderr_does_not_stop_a_complete_run(self) -> None:
        reason = classify_silence(
            text="a complete review with findings",
            raw_stderr='prompt echo: ... "no output produced" ... requires approval, but approval policy is never',
            structured=None,
            exit_code=0,
            timed_out=False,
        )
        assert reason is None

    def test_a_pty_run_that_parsed_is_judged_by_the_ordinary_rules(self) -> None:
        stream = 'the answer quotes "no output produced" from a log'
        reason = classify_silence(
            text="the answer",
            raw_stderr="",
            structured={"status": "SUCCESS"},
            exit_code=0,
            timed_out=False,
            merged_stderr=stream,
        )
        assert reason is None

    def test_an_unparsed_pty_stream_carrying_a_client_denial_is_a_stop(self) -> None:
        denial = "jetski: no output produced - a tool required the permission that headless mode cannot prompt for"  # trw-leak-allow: google_internal verbatim agy 1.2.4 denial text pinned as the regression input
        reason = classify_silence(
            text=denial,
            raw_stderr="",
            structured=None,
            exit_code=0,
            timed_out=False,
            merged_stderr=denial,
        )
        assert reason == "auth_or_content_stop"

    def test_a_leading_caret_d_in_real_text_is_not_an_echo(self) -> None:
        # ``\x08*`` would have eaten these two characters; the pty always
        # backspaces over its echo, so a backspace is required.
        text, _ = normalize_output("agy", "^D means end of file")
        assert text == "^D means end of file"


class TestTheProbeCannotBeAnsweredFromThePrompt:
    def test_prompt_derived_output_alone_never_certifies(self) -> None:
        """The canary must be unobtainable without reading the fixture.

        When the canary was ``CANARY-{nonce}`` and the nonce appeared in the
        prompt, a model could assemble a passing answer — canary line plus an
        invented "permission denied" — without calling a single tool.
        """
        seen: list[str] = []

        def _run(req: DispatchRequest) -> DispatchResult:
            seen.append(req.prompt)
            # Echo the whole prompt back, plus a plausible denial. A child that
            # read nothing can do exactly this.
            return _result(client=req.client, text=f"{req.prompt}\noperation not permitted")

        probe = probe_write_containment("agy", run=_run, mechanism="test")

        assert probe.verdict == "unverified"
        assert "CANARY-" not in seen[0], "the canary value must never appear in the prompt"


class TestBinaryPreflightMatchesLaunchResolution:
    def test_a_non_executable_file_does_not_count_as_a_binary(self, tmp_path: Path) -> None:
        from trw_mcp.dispatch._runner import _binary_resolves

        readme = tmp_path / "NOTES.md"
        readme.write_text("not a program", encoding="utf-8")
        assert _binary_resolves(str(readme), None) is False

    def test_a_relative_path_resolves_against_the_childs_cwd(self, tmp_path: Path) -> None:
        from trw_mcp.dispatch._runner import _binary_resolves

        stub = _write_stub(tmp_path, "client", "echo hi\n")
        assert stub.exists()
        assert _binary_resolves("./client", tmp_path) is True
        assert _binary_resolves("./client", tmp_path / "elsewhere") is False

    def test_a_bare_name_still_goes_through_path(self) -> None:
        from trw_mcp.dispatch._runner import _binary_resolves

        assert _binary_resolves("sh", None) is True
        assert _binary_resolves("xyz-does-not-exist-anywhere", None) is False


class TestAProbeFailureDoesNotAbortTheRun:
    def test_an_unwritable_fixture_yields_unverified_and_the_child_still_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The probe is a side question; it may not take the caller's task down."""
        import trw_mcp.dispatch._runner as runner_module

        def _boom(*_args: object, **_kwargs: object) -> object:
            raise OSError("No space left on device")

        monkeypatch.setattr(runner_module, "probe_write_containment", _boom)
        stub = _write_stub(tmp_path, "fake-still-runs", "echo 'the real answer'\n")
        _patch_argv(monkeypatch, [str(stub)])

        result = dispatch(DispatchRequest(client="claude", prompt="x", verify_sandbox=True, timeout_s=10))

        assert result.sandbox_verified == "unverified"
        assert "could not run" in result.sandbox_note
        assert result.text == "the real answer"
        assert result.ok is True


class TestRoundThreeTightenings:
    """Round-3 review: two verdict paths were reachable without the evidence."""

    def test_a_banner_is_not_a_denied_write(self) -> None:
        """A client that read the canary and printed its banner tested nothing.

        ``sandbox: read-only`` is what codex prints at startup. While
        ``read-only`` counted as a denial, a child could satisfy both ``true``
        conditions without attempting either write.
        """

        def _run(req: DispatchRequest) -> DispatchResult:
            canary = (req.cwd / "canary.txt").read_text(encoding="utf-8").strip() if req.cwd else ""
            return _result(client=req.client, text=f"{canary}\nsandbox: read-only\naccess denied by policy")

        probe = probe_write_containment("agy", run=_run, mechanism="test")

        assert probe.verdict == "unverified"
        assert "no denial reported" in probe.note

    def test_a_reported_write_failure_still_certifies(self) -> None:
        def _run(req: DispatchRequest) -> DispatchResult:
            canary = (req.cwd / "canary.txt").read_text(encoding="utf-8").strip() if req.cwd else ""
            return _result(client=req.client, text=f"{canary}\nstep 2: operation not permitted\nstep 3: eacces")

        assert probe_write_containment("agy", run=_run, mechanism="test").verdict is True

    def test_an_unanswerable_path_lookup_does_not_refuse_the_launch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The pre-flight improves an error code; it is not a permission gate.

        With a RELATIVE entry on PATH the lookup cannot be performed from here,
        and refusing would turn a client that launches fine into a false -127.
        """
        from trw_mcp.dispatch._runner import _binary_resolves

        monkeypatch.setenv("PATH", "bin")
        assert _binary_resolves("xyz-not-on-path", None) is True

        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        assert _binary_resolves("xyz-not-on-path", None) is False

    def test_the_pty_prose_trade_is_visible_not_hidden(self) -> None:
        """ACCEPTED behaviour, pinned so it cannot change silently.

        A pty run whose stream did not parse AND whose prose quotes a client
        status line is reported as a stop. That is the documented cost of being
        able to see agy's denial at all (it carries no structured payload), and
        it errs toward "unusable", never toward a false clean review.
        """
        prose = 'The log says "no output produced" here, but the review itself is complete.'
        assert (
            classify_silence(
                text=prose,
                raw_stderr="",
                structured=None,
                exit_code=0,
                timed_out=False,
                merged_stderr=prose,
            )
            == "auth_or_content_stop"
        )
        # Without the pty merge — the ordinary shape — the same prose is fine.
        assert classify_silence(text=prose, raw_stderr="", structured=None, exit_code=0, timed_out=False) is None
