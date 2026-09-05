"""Shared fixtures for the wiring-detector regression suite.

The fixture is not synthetic. It is the real repository: the specimens named in
the monorepo-internal defect ledger and the two false-positive sets that a
naive implementation of this check gets wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.wiring.detector import DetectorResult, run_detector


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "Makefile").is_file() and (candidate / ".trw" / "channels" / "manifest.yaml").is_file():
            return candidate
    raise RuntimeError("could not locate the monorepo root from the wiring test package")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """The monorepo root — the detector's real subject."""
    return _find_repo_root()


@pytest.fixture(scope="session")
def live_result(repo_root: Path) -> DetectorResult:
    """One full repo-wide run, shared across the suite (it is deterministic)."""
    return run_detector(repo_root)


@pytest.fixture(scope="session")
def finding_keys(live_result: DetectorResult) -> frozenset[str]:
    return frozenset(finding.key for finding in live_result.findings)


@pytest.fixture(scope="session")
def rendered_findings(live_result: DetectorResult) -> str:
    return "\n".join(finding.render() for finding in live_result.findings)
