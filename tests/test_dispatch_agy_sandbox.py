"""AGY-SANDBOX regression pins (PRD-CORE-291, fixed 2026-09-22).

Four defects in cross-client dispatch to ``agy`` (found by the 2026-09-19
cross-harness interop batch, feedback rows 18/21), each fixed in
``trw_mcp.dispatch``:

- Defect 1 (read-only agy could not read at all off host-confinement): fixed —
  ``build_command`` now always emits ``confined_read_only_argv`` on the
  read-only path, not only when ``confined=True``.
- Defect 2 (``read_only_enforced`` was true with no proof writes were denied):
  fixed — ``_runner._read_only_enforced`` reports True for a host-confinement
  client only when the wrapper actually ran.
- Defect 3 (``--pty`` leaked the raw event log instead of the final answer):
  fixed — ``_normalize_enveloped_events`` now skips an unparseable line instead
  of degrading the whole stream, and only falls back to raw text when no
  terminal envelope is found.
- Defect 4 (a silent subagent deferral was reported ``ok:true``): fixed —
  ``classify_silence`` now recognizes a parsed answer that only announces a
  subagent handoff and reports ``silence_reason='subagent_deferral'``.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._normalize import classify_silence, normalize_output
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._types import DispatchRequest


def _write_stub(tmp_path: Path, name: str, body: str) -> Path:
    stub = tmp_path / name
    stub.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def _patch_argv(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    def _fixed(_req: DispatchRequest, *, confined: bool = False) -> list[str]:
        return argv

    monkeypatch.setattr("trw_mcp.dispatch._runner.build_command", _fixed)


# --- Defect 1: read-only agy cannot read files at all, off host confinement --


class TestDefect1ReadOnlyCannotRead:
    """agy's bare ``--sandbox`` denies reads; its read-enabling flag is safe only inside
    the host write-denial wrapper. So a read-only agy dispatch reads when wrapped, and
    is REFUSED before spawn when no wrapper exists -- never widened to a write-capable run.
    """

    def test_confined_read_only_agy_carries_the_read_enabling_flag(self) -> None:
        req = DispatchRequest(client="agy", prompt="read this file", read_only=True, timeout_s=10)
        assert "--dangerously-skip-permissions" in build_command(req, confined=True)

    def test_unconfined_read_only_agy_never_carries_it(self) -> None:
        req = DispatchRequest(client="agy", prompt="read this file", read_only=True, timeout_s=10)
        assert "--dangerously-skip-permissions" not in build_command(req, confined=False)

    def test_unconfined_read_only_agy_is_refused_before_spawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.dispatch import _runner, dispatch

        monkeypatch.setattr("trw_mcp.dispatch._host_confinement.confinement_prefix", lambda writable=None: [])
        spawned: list[object] = []
        monkeypatch.setattr(_runner.subprocess, "Popen", lambda *a, **k: spawned.append(a))
        result = dispatch(DispatchRequest(client="agy", prompt="read this file", read_only=True, timeout_s=10))
        assert result.ok is False and not spawned
        assert "refused" in result.raw_stderr and result.read_only_enforced is False

    def test_pre_spawn_failure_never_claims_enforcement_even_with_a_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wrapper prefix was found but agy is missing: no child ran, so nothing was enforced."""
        from trw_mcp.dispatch import _runner, dispatch

        monkeypatch.setattr(
            "trw_mcp.dispatch._host_confinement.confinement_prefix",
            lambda writable=None: ["sandbox-exec", "-p", "(version 1)"],
        )
        monkeypatch.setattr(_runner, "_binary_resolves", lambda *_a: False)
        spawned: list[object] = []
        monkeypatch.setattr(_runner.subprocess, "Popen", lambda *a, **k: spawned.append(a))
        result = dispatch(DispatchRequest(client="agy", prompt="read this file", read_only=True, timeout_s=10))
        assert result.exit_code == -127 and not spawned
        assert result.read_only_enforced is False


# --- Defect 2: read_only_enforced is asserted from the request, not measured --


class TestDefect2ReadOnlyEnforcedIsUnproven:
    def test_read_only_enforced_reflects_the_request_not_a_write_denial_measurement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """agy's OWN registry comment (``_client_specs.py`` agy entry, sandbox
        field) states plainly that ``--sandbox`` "restricts SHELL COMMANDS, not
        agy's file-edit tool, and a run under it created files inside cwd and at
        an absolute path outside cwd and --add-dir alike." So a read-only agy
        dispatch WITHOUT host confinement grants no real write denial at all.

        Yet ``DispatchResult.read_only_enforced`` is set unconditionally to
        ``req.read_only`` (``_runner.py`` ``dispatch()``, both the normal and the
        ``_early_result`` path) -- it never reads whether a confinement wrapper
        was actually applied. So an unconfined read-only agy run reports
        ``read_only_enforced=True`` while agy is, by the registry's own written
        evidence, free to write.

        Correct behavior: ``read_only_enforced`` must be True only when writes
        were actually denied (confinement applied, or the client's own read-only
        flag is proven to deny writes). This test simulates the unconfined case
        (no host wrapper — the common non-macOS / no-sandbox-exec case) and
        asserts the result is honest about it.
        """
        monkeypatch.setattr("trw_mcp.dispatch._host_confinement.confinement_prefix", lambda writable=None: [])
        stub = _write_stub(tmp_path, "fake-agy", 'echo \'{"event":"result","result":{"response":"ok"}}\'\n')
        _patch_argv(monkeypatch, [str(stub)])

        result = dispatch(DispatchRequest(client="agy", prompt="x", read_only=True, timeout_s=10))

        assert result.read_only_enforced is False, (
            "agy's --sandbox does not stop file writes (registry comment, agy spec, sandbox field); "
            "with no confinement wrapper applied, read_only_enforced must not claim writes were denied"
        )


# --- Defect 3: --pty leaks the raw event log instead of the final answer -----


class TestDefect3PtyLeaksRawEventLog:
    def test_a_non_json_first_line_under_pty_returns_the_whole_raw_stream_as_text(self) -> None:
        """``_normalize_enveloped_events`` (agy's output shape) degrades to
        RAW TEXT — the whole cleaned stdout, every NDJSON line included — the
        moment ANY line fails to start with ``{``. Under ``--pty`` a non-JSON
        first line is exactly what agy's own log-write failure produces under
        sandbox confinement (FEEDBACK-DELTA sub_v0juC6aoC9Y3jjaB, 11/11 runs),
        so the caller's ``.text`` becomes the raw tagged-envelope event log
        instead of the final answer extracted from the terminal ``result``
        envelope.

        Correct behavior: a stream whose terminal ``result`` envelope IS present
        and parseable should still yield the extracted answer even when an
        earlier line could not be parsed as an event.
        """
        raw = (
            "agy: warning: could not write session log (read-only filesystem)\n"
            '{"event":"init"}\n'
            '{"event":"result","result":{"status":"success","response":"the real final answer"}}\n'
        )
        text, structured = normalize_output("agy", raw)
        assert text == "the real final answer", (
            f"a non-JSON first line must not blank out an otherwise-parseable terminal envelope; got text={text!r}"
        )


# --- Defect 4: silent subagent deferral is reported as ok:true ---------------


class TestDefect4SilentSubagentDeferral:
    def test_a_terminal_result_envelope_with_no_real_verdict_is_not_silently_ok(self) -> None:
        """agy's registry entry declares ``sub_agents='yes'`` (PRD-CORE-277), but
        no code anywhere in ``trw_mcp.dispatch`` (``_normalize.py``,
        ``_types.py``) inspects agy's envelope for a subagent-deferral signal.
        ``_structured_stop`` only reads a fixed ``_STATUS_FIELDS`` vocabulary
        (status/subtype/error/stop_reason/...); a terminal ``result`` envelope
        whose own status field never resolves (e.g. an ongoing/pending value the
        vocabulary does not recognize as OK) is exactly the class this test
        pins, and it must NOT be classified as a clean completion.

        FEEDBACK-DELTA sub_lDW_XMo1oaNtfCI9: ``dispatch --client agy`` returned
        ``ok:true`` for a review that never ran because agy deferred to a
        subagent and the dispatch never got a verdict. The failure mode is NOT
        an unrecognized status value (that case IS already caught by
        ``_structured_stop``'s ``_OK_STATUS_VALUES`` allowlist -- a status like
        ``'delegated'`` already trips a stop). The real gap is the case agy
        itself measurably produces: the CLI's OWN ``status`` field claims
        ``'success'`` while the ``response`` text says the work was merely
        handed off to a subagent and never returned. There is no seam in this
        codebase that reads the answer text for a deferral marker, or that
        otherwise distinguishes "the subagent finished and this is its answer"
        from "agy claims success but only delegated" -- both currently produce
        ``ok=True``.
        """
        structured = {
            "status": "success",
            "response": "Delegated to subagent review-helper; it will report back.",
        }
        text = "Delegated to subagent review-helper; it will report back."
        silence_reason = classify_silence(
            text=text,
            raw_stderr="",
            structured=structured,
            exit_code=0,
            timed_out=False,
        )
        assert silence_reason is not None, (
            "agy's own status='success' beside a response that only announces a subagent "
            "handoff must not be classified as a clean, verdict-bearing answer"
        )

    @pytest.mark.parametrize(
        "text",
        [
            # A completed review that quotes the marker as evidence.
            "Verdict: BLOCK. P2 _normalize.py:425 treats any answer containing "
            '"delegated to subagent" as a deferral. ' + "Supporting detail. " * 30,
            # A short completed answer that cites the marker in code formatting.
            "P2: `delegated to subagent` at _normalize.py:398 is matched anywhere in the text.",
            # A completed review that mentions a handoff in passing, unquoted.
            "Verdict: PASS. The first pass was delegated to subagent lint-helper, which returned; "
            + "finding detail. " * 30,
            # A short completed answer that mentions a delegation, unquoted (CORE-291 round 2).
            "Verdict: MERGE. The first pass was delegated to subagent lint-helper, which returned with no findings",
        ],
        ids=[
            "long-review-quoting-marker",
            "short-answer-citing-marker",
            "long-review-mentioning-handoff",
            "short-verdict-mentioning-handoff",
        ],
    )
    def test_a_completed_answer_quoting_the_marker_is_not_a_deferral(self, text: str) -> None:
        silence_reason = classify_silence(
            text=text,
            raw_stderr="",
            structured={"status": "success", "response": text},
            exit_code=0,
            timed_out=False,
        )
        assert silence_reason is None
