"""Per-test-file execution receipts, recorded from a pytest junit report (UF-GATES-02, PRD-QUAL-120).

An acceptance manifest may mark a requirement ACCEPTED only on an EXECUTED receipt: proof that the test file the
requirement maps to ran in this run and passed. This module is that proof's one producer and one reader.

``record_junit_report`` folds a junit report into ``<run>/meta/test-file-receipts.json``, keyed by repo-relative
test file path. Each entry holds the outcome counts and a sha256 of the file's bytes, so a receipt is bound to the
exact test content that ran. Several suites (one per package) accumulate into one file; a later report for a file
replaces the earlier one. ``load_test_file_receipts`` returns them, or nothing when the file is missing or its
digest does not match (a hand-edited pass is not a receipt).
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog

from trw_mcp.state._containment import ContainmentError, assert_trw_write_contained
from trw_mcp.state.persistence import write_text_atomic

logger = structlog.get_logger(__name__)

RECEIPTS_FILE = "test-file-receipts.json"


@dataclass(frozen=True, slots=True)
class TestFileOutcome:
    """What one test file did in this run, bound to the bytes that ran."""

    __test__ = False  # not a pytest test class, despite the name

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    content_digest: str = ""

    @property
    def proven(self) -> bool:
        """True when the file ran at least one passing test and nothing failed or errored."""
        return self.passed > 0 and self.failed == 0 and self.errors == 0


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _payload_digest(files: dict[str, dict[str, object]]) -> str:
    return _digest(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _resolve_file(classname: str, suite_root: Path) -> Path | None:
    """The test file a junit ``classname`` names: the longest dotted prefix that exists as a ``.py`` file."""
    parts = classname.split(".")
    for end in range(len(parts), 0, -1):
        candidate = suite_root.joinpath(*parts[:end]).with_suffix(".py")
        if candidate.is_file():
            return candidate
    return None


def _outcome(case: ET.Element) -> str:
    for kind in ("failure", "error", "skipped"):
        if case.find(kind) is not None:
            return kind
    return "passed"


def record_junit_report(report: Path, *, suite_root: Path, repo_root: Path, run_path: Path) -> int:
    """Fold *report* into the run's receipts; returns how many test files it recorded.

    Raises ``ValueError`` when the report cannot be parsed: a gate that asked for receipts must not proceed as if
    the suite had produced none.
    """
    try:
        tree = ET.parse(report)  # noqa: S314 - a local report this run's own pytest wrote
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"junit report {report} is unreadable: {exc}") from exc

    counts: dict[Path, dict[str, int]] = {}
    for case in tree.getroot().iter("testcase"):
        path = _resolve_file(case.get("classname", ""), suite_root)
        if path is None:
            continue  # nothing on disk to bind the outcome to
        bucket = counts.setdefault(path, {"passed": 0, "failed": 0, "errors": 0, "skipped": 0})
        kind = _outcome(case)
        bucket[{"failure": "failed", "error": "errors"}.get(kind, kind)] += 1

    files = _load_files(run_path)
    for path, c in counts.items():
        rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
        files[rel] = asdict(TestFileOutcome(**c, content_digest=_digest(path.read_bytes())))

    target = run_path / "meta" / RECEIPTS_FILE
    assert_trw_write_contained(target)  # a planted symlink under .trw must not redirect the write
    if target.parent.is_symlink():  # a custom run dir outside .trw gets no walk above; its meta dir still must not
        raise ContainmentError(f"refusing to write receipts through a symlink: {target.parent}", path=str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"files": files, "digest": _payload_digest(files)}, indent=2, sort_keys=True)
    write_text_atomic(target, payload, mode=0o644)
    logger.info("test_file_receipts_recorded", report=str(report), files=len(counts))
    return len(counts)


def _load_files(run_path: Path) -> dict[str, dict[str, object]]:
    target = run_path / "meta" / RECEIPTS_FILE
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        files = data["files"]
        if not isinstance(files, dict) or data.get("digest") != _payload_digest(files):
            logger.warning("test_file_receipts_digest_mismatch", path=str(target))
            return {}
    # trw-fail-silent-allow: warns; an unreadable receipt must prove nothing, so the manifest falls back to UNKNOWN
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("test_file_receipts_unreadable", path=str(target), exc_info=True)
        return {}
    return files


def load_test_file_receipts(run_path: Path) -> dict[str, TestFileOutcome]:
    """The run's per-test-file receipts, keyed by repo-relative path; empty when absent or tampered."""
    return {rel: TestFileOutcome(**entry) for rel, entry in _load_files(run_path).items()}  # type: ignore[arg-type]
