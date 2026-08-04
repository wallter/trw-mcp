"""A default-path proof must not survive the deletion of the file it names.

``default_path_proof_blocking`` validated only that ``receipt`` and
``removal_assertion`` were non-empty strings and that ``source_digest`` started
with ``sha256:``. It never checked that the named test or source file existed,
so a PRD could keep asserting ``functionality_level: live`` on a receipt whose
test file had been deleted — the proof evaporated and the gate said nothing.
That is a Potemkin gate inside the verification machinery itself.

The check is deliberately conservative: only repo-relative source/test paths are
hard-checked, and a token that resolves under ANY package prefix passes. Absolute
paths (``/tmp/...``) and ``.trw/run`` artifacts never hard-block, because neither
is a durable repo file and blocking on them would stop real deliveries. Verified
against every shipped PRD carrying a frontmatter ``default_path_proof`` (5 at the
time of writing, 8 resolvable path tokens between them): zero flagged.

A second defect of the same family lived in the extractor itself: it recognised
only ``py|ts|tsx|sh|md|ya?ml|json``, so a ``.go`` / ``.rs`` / ``.java`` / ``.rb``
/ ``.cs`` / ``.php`` proof path was never extracted and therefore never checked.
That did not error and did not block — it returned green having verified nothing,
which is a stronger failure than the one above: a check that reports success
without running. The tests below hold both halves of the fix — the mainstream
languages are genuinely resolved, and anything the blocking tier still cannot
adjudicate is reported as advisory instead of dropped in silence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.tools._prd_proof_paths import (
    DEFAULT_PATH_PROOF_CHECK_FAILED,
    DEFAULT_PATH_PROOF_FILE_MISSING,
    DEFAULT_PATH_PROOF_UNVERIFIED,
    MISSING_DEFAULT_PATH_PROOF,
    classify_proof_paths,
    default_path_proof_blocking,
    default_path_proof_findings,
    missing_proof_paths,
)

_DIGEST = "sha256:" + "0" * 64


def _proof(receipt: str, removal: str = "superseded path removed") -> dict[str, object]:
    return {"default_path_proof": {"receipt": receipt, "source_digest": _DIGEST, "removal_assertion": removal}}


class TestMissingProofPaths:
    def test_names_a_deleted_test_file(self, tmp_path: Path) -> None:
        missing = missing_proof_paths("proved by tests/test_evaporated.py::test_gone", project_root=tmp_path)
        assert missing == ["tests/test_evaporated.py"]

    def test_existing_file_is_accepted(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_real.py").write_text("", encoding="utf-8")
        assert missing_proof_paths("tests/test_real.py::test_x", project_root=tmp_path) == []

    def test_file_under_a_package_prefix_is_accepted(self, tmp_path: Path) -> None:
        # Receipts routinely write a path relative to the package, e.g.
        # 'server/_tools.py' meaning 'trw-mcp/src/trw_mcp/server/_tools.py'.
        target = tmp_path / "trw-mcp" / "src" / "trw_mcp" / "server"
        target.mkdir(parents=True)
        (target / "_tools.py").write_text("", encoding="utf-8")
        assert missing_proof_paths("wired in server/_tools.py", project_root=tmp_path) == []

    def test_absolute_and_run_artifact_paths_are_ignored(self, tmp_path: Path) -> None:
        blob = "/tmp/coverage-run.log plus .trw/runs/x/meta/receipts/review/r.json"
        assert missing_proof_paths(blob, project_root=tmp_path) == []

    def test_prose_without_paths_is_ignored(self, tmp_path: Path) -> None:
        assert missing_proof_paths("make prd-projection-gate is the production default", tmp_path) == []


class TestDefaultPathProofBlocking:
    def test_live_claim_with_evaporated_receipt_blocks(self, tmp_path: Path) -> None:
        blocking = default_path_proof_blocking(
            _proof("proved by tests/test_evaporated.py::test_gone"), "live", project_root=tmp_path
        )
        assert blocking == [DEFAULT_PATH_PROOF_FILE_MISSING]

    def test_removal_assertion_is_checked_too(self, tmp_path: Path) -> None:
        # The sibling incident had the deleted file named in the removal
        # assertion, not the receipt — both fields must be scanned.
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_real.py").write_text("", encoding="utf-8")
        blocking = default_path_proof_blocking(
            _proof("tests/test_real.py", removal="old path removed per tests/test_deleted.py::test_absent"),
            "live",
            project_root=tmp_path,
        )
        assert blocking == [DEFAULT_PATH_PROOF_FILE_MISSING]

    def test_intact_proof_passes(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_real.py").write_text("", encoding="utf-8")
        assert default_path_proof_blocking(_proof("tests/test_real.py::test_x"), "live", project_root=tmp_path) == []

    def test_structural_checks_still_run_first(self, tmp_path: Path) -> None:
        # The pre-existing contract is preserved: shape failures still report
        # the original token, not the new one.
        assert default_path_proof_blocking({}, "live", project_root=tmp_path) == [MISSING_DEFAULT_PATH_PROOF]
        assert default_path_proof_blocking(
            {"default_path_proof": {"receipt": "r", "source_digest": "md5:x", "removal_assertion": "a"}},
            "live",
            project_root=tmp_path,
        ) == [MISSING_DEFAULT_PATH_PROOF]

    def test_non_live_levels_are_untouched(self, tmp_path: Path) -> None:
        assert default_path_proof_blocking(_proof("tests/test_gone.py"), "partial", project_root=tmp_path) == []


#: One representative proof path per mainstream language the extractor was blind
#: to. Each is a real project layout, not a synthetic ``a/b.ext``.
_MAINSTREAM_PROOF_PATHS: tuple[str, ...] = (
    "internal/server/handler_test.go",
    "src/lib.rs",
    "src/main/java/com/acme/App.java",
    "spec/models/user_spec.rb",
    "src/Acme.Api/Services/AuthService.cs",
    "app/Http/Middleware/Authenticate.php",
    "src/main/kotlin/Main.kt",
    "src/parser.c",
    "include/parser.h",
    "src/engine/app.cpp",
    "lib/acme/util.ex",
    "Sources/App/main.swift",
    "src/components/Panel.tsx",
    "web/src/store.js",
    "db/migrations/0001_init.sql",
)


class TestNonPythonProofPathsAreActuallyChecked:
    """The extractor's language blindness made the gate a no-op off Python.

    Reverting the widened extension set alone turns every case here green while
    verifying nothing — which is exactly the failure the assertions describe.
    """

    @pytest.mark.parametrize("proof_path", _MAINSTREAM_PROOF_PATHS)
    def test_a_vanished_path_blocks(self, tmp_path: Path, proof_path: str) -> None:
        assert missing_proof_paths(f"proved by {proof_path}", project_root=tmp_path) == [proof_path]
        assert default_path_proof_blocking(_proof(f"proved by {proof_path}"), "live", project_root=tmp_path) == [
            DEFAULT_PATH_PROOF_FILE_MISSING
        ]

    @pytest.mark.parametrize("proof_path", _MAINSTREAM_PROOF_PATHS)
    def test_an_intact_path_passes(self, tmp_path: Path, proof_path: str) -> None:
        target = tmp_path / proof_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        findings = default_path_proof_findings(_proof(f"proved by {proof_path}"), "live", project_root=tmp_path)
        assert findings.blocking == []
        assert findings.advisory == []

    def test_a_tsx_path_is_not_truncated_to_ts(self, tmp_path: Path) -> None:
        # Unanchored longest-first alternation: 'src/a.tsx' used to extract as
        # 'src/a.ts', so the gate resolved a file the proof never named — it
        # could pass on the wrong file or block on a real one.
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.ts").write_text("", encoding="utf-8")
        assert missing_proof_paths("proved by src/a.tsx", project_root=tmp_path) == ["src/a.tsx"]


class TestUnverifiableReferencesAreVisible:
    """Whatever the blocking tier cannot adjudicate must still be reported."""

    def test_unrecognised_extension_is_advisory_not_silent(self, tmp_path: Path) -> None:
        missing, unverified = classify_proof_paths("proved by src/analysis.hs", project_root=tmp_path)
        assert missing == []
        assert unverified == ["src/analysis.hs"]
        findings = default_path_proof_findings(_proof("proved by src/analysis.hs"), "live", project_root=tmp_path)
        assert findings.blocking == []
        assert findings.advisory == [DEFAULT_PATH_PROOF_UNVERIFIED]

    def test_a_vanished_run_artifact_is_advisory(self, tmp_path: Path) -> None:
        # Both .trw/ receipts cited by the shipped PRD corpus are already gone.
        # Non-durable, so absence cannot block — but it must not read as passing.
        blob = ".trw/runs/x/20260713T070228Z-8234fdb7/meta/receipts/review/review-abc.json"
        missing, unverified = classify_proof_paths(blob, project_root=tmp_path)
        assert missing == []
        assert unverified == [blob]

    def test_a_run_artifact_still_on_disk_is_verified_silently(self, tmp_path: Path) -> None:
        blob = ".trw/runs/x/meta/receipts/review/review-abc.json"
        target = tmp_path / blob
        target.parent.mkdir(parents=True)
        target.write_text("{}", encoding="utf-8")
        assert classify_proof_paths(blob, project_root=tmp_path) == ([], [])

    def test_an_absolute_path_is_advisory(self, tmp_path: Path) -> None:
        blob = "/tmp/trw-mcp-coverage-does-not-exist-12345.log"
        missing, unverified = classify_proof_paths(blob, project_root=tmp_path)
        assert missing == []
        assert unverified == [blob]

    def test_a_resolver_fault_is_reported_not_swallowed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Fail-open on blocking is correct — infrastructure must not stop a
        # delivery — but returning a bare [] made "could not run" look identical
        # to "passed".
        import trw_mcp.tools._prd_proof_paths as mod

        def _boom(*_args: object, **_kwargs: object) -> tuple[list[str], list[str]]:
            raise OSError("filesystem unavailable")

        monkeypatch.setattr(mod, "classify_proof_paths", _boom)
        findings = default_path_proof_findings(_proof("tests/test_real.py"), "live", project_root=tmp_path)
        assert findings.blocking == []
        assert findings.advisory == [DEFAULT_PATH_PROOF_CHECK_FAILED]


class TestBystandersDoNotStartFailing:
    """Widening the net must not turn ordinary receipt prose into a block."""

    def test_a_legitimately_absent_optional_path_stays_non_blocking(self, tmp_path: Path) -> None:
        # A real receipt (PRD-CORE-219's shape): a durable test file plus an
        # ephemeral coverage log and a run receipt that a fresh clone will not
        # have. Only the durable one may ever hard-block.
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_real.py").write_text("", encoding="utf-8")
        blob = (
            "tests/test_real.py::test_x passing; full log at "
            "/tmp/trw-mcp-coverage-core219.log; receipt "
            ".trw/runs/qa/20260713T070228Z-8234fdb7/meta/receipts/review/review-abc.json"
        )
        findings = default_path_proof_findings(_proof(blob), "live", project_root=tmp_path)
        assert findings.blocking == []
        assert findings.advisory == [DEFAULT_PATH_PROOF_UNVERIFIED]

    @pytest.mark.parametrize(
        "prose",
        [
            "satisfies CONSTITUTION 1.c and step 3.h of the plan",
            "documented at https://trwframework.com/docs/index.md",
            "applies to any and/or all cases, N/A otherwise",
            "make prd-projection-gate is the production default",
            "coverage held at 50/50 across the two suites",
        ],
    )
    def test_prose_is_never_a_path(self, tmp_path: Path, prose: str) -> None:
        assert classify_proof_paths(prose, project_root=tmp_path) == ([], [])


class TestAdvisoryReachesTheProductionGate:
    """A finding only the helper knows about is not a visible finding.

    ``evaluate_prd_coherence`` is what the deliver gate calls; its ``advisory``
    list is what becomes the agent-facing warning and a STALE completion
    component. This test fails if the gate keeps calling the blocking-only
    helper and drops the advisory half.
    """

    def _write_prd(self, root: Path, receipt: str) -> str:
        from trw_mcp.models.config import get_config

        prd_dir = root / get_config().prds_relative_path
        prd_dir.mkdir(parents=True, exist_ok=True)
        (prd_dir / "PRD-TEST-001.md").write_text(
            "---\n"
            "id: PRD-TEST-001\n"
            "status: implemented\n"
            "priority: P2\n"
            "functionality_level: live\n"
            "default_path_proof:\n"
            f"  receipt: {receipt}\n"
            f"  source_digest: {_DIGEST}\n"
            "  removal_assertion: superseded path removed\n"
            "---\n\n# Body\n",
            encoding="utf-8",
        )
        return "PRD-TEST-001"

    def test_unverified_paths_surface_as_a_gate_advisory(self, tmp_path: Path) -> None:
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._prd_transition_gate import evaluate_prd_coherence

        prd_id = self._write_prd(tmp_path, "proved by src/analysis.hs")
        report = evaluate_prd_coherence(prd_id, tmp_path / "run", FileStateReader())
        assert DEFAULT_PATH_PROOF_UNVERIFIED in report.advisory
        assert DEFAULT_PATH_PROOF_UNVERIFIED not in report.blocking

    def test_a_verified_proof_adds_no_advisory(self, tmp_path: Path) -> None:
        # Bystander: the advisory must be earned, not emitted on every PRD.
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._prd_transition_gate import evaluate_prd_coherence

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_real.py").write_text("", encoding="utf-8")
        prd_id = self._write_prd(tmp_path, "proved by tests/test_real.py::test_x")
        report = evaluate_prd_coherence(prd_id, tmp_path / "run", FileStateReader())
        assert DEFAULT_PATH_PROOF_UNVERIFIED not in report.advisory
        assert DEFAULT_PATH_PROOF_FILE_MISSING not in report.blocking


class TestShippedPrdsDoNotRegress:
    def test_every_shipped_default_path_proof_resolves(self) -> None:
        """Non-vacuous guard: the real PRD corpus must stay at zero flags.

        If this ever fails, either a proof genuinely evaporated (the defect this
        gate exists to catch) or the extractor got too aggressive. Both need a
        human look — do not relax the extractor to make it pass.
        """
        import re

        import yaml

        root = Path(__file__).resolve().parents[2]
        prds = sorted((root / "docs" / "requirements-aare-f" / "prds").glob("*.md"))
        assert prds, "PRD corpus not found — this test would be vacuous"

        checked = 0
        offenders: dict[str, list[str]] = {}
        for prd in prds:
            match = re.match(r"^---\n(.*?)\n---\n", prd.read_text(encoding="utf-8"), re.DOTALL)
            if not match:
                continue
            try:
                frontmatter = yaml.safe_load(match.group(1))
            except yaml.YAMLError:
                continue
            if not isinstance(frontmatter, dict) or not isinstance(frontmatter.get("default_path_proof"), dict):
                continue
            checked += 1
            proof = frontmatter["default_path_proof"]
            blob = f"{proof.get('receipt', '')} {proof.get('removal_assertion', '')}"
            missing = missing_proof_paths(blob, project_root=root)
            if missing:
                offenders[prd.name] = missing
        # Non-vacuity floor, not a target: 5 PRDs currently carry a frontmatter
        # default_path_proof. If this trips, the corpus shrank and the guard
        # would otherwise pass by checking nothing.
        assert checked >= 5, f"expected the shipped proof corpus, only saw {checked}"
        assert not offenders, f"default_path_proof names files that no longer exist: {offenders}"
