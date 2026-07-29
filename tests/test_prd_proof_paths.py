"""A default-path proof must not survive the deletion of the file it names.

``default_path_proof_blocking`` validated only that ``receipt`` and
``removal_assertion`` were non-empty strings and that ``source_digest`` started
with ``sha256:``. It never checked that the named test or source file existed,
so a PRD could keep asserting ``functionality_level: live`` on a receipt whose
test file had been deleted — the proof evaporated and the gate said nothing.
That is a Potemkin gate inside the verification machinery itself.

The check is deliberately conservative: only repo-relative source/test paths are
resolved, and a token that resolves under ANY package prefix passes. Absolute
paths (``/tmp/...``) and ``.trw/run`` artifacts are ignored, because neither is a
durable repo file and flagging them would false-block real deliveries. Verified
against every shipped PRD carrying a frontmatter ``default_path_proof`` (5 at the
time of writing, 8 resolvable path tokens between them): zero flagged.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.tools._prd_proof_paths import (
    DEFAULT_PATH_PROOF_FILE_MISSING,
    MISSING_DEFAULT_PATH_PROOF,
    default_path_proof_blocking,
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
