"""PRD-INFRA-191-FR06: no test pins the current framework stamp.

The v27.1 -> v27.2 move edited 29 test files. Only two of them read the real
canon; the other 27 used the live stamp as arbitrary fixture data, so every bump
rewrote them for nothing. Fixtures now use the synthetic ``v99.9_TRW``, and the
two canon tests read the stamp from ``TRWConfig``. This guard keeps it that way.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Monorepo invariant: it scans sibling packages' test trees, which the standalone
# trw-mcp mirror does not carry.
if not (_REPO_ROOT / "scripts").is_dir():
    pytest.skip("monorepo-only invariant (repo-root scripts/ absent in standalone mirror)", allow_module_level=True)

_TEST_TREE_PATHSPECS = ("*/tests/*", "tests/*", "scripts/tests/*")


def _tracked_test_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", *_TEST_TREE_PATHSPECS],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"git ls-files unavailable: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def _pins(text: str, stamp: str) -> bool:
    return stamp in text or f"{stamp.removesuffix('_TRW')} mandate" in text


def test_no_test_file_pins_the_current_framework_stamp() -> None:
    stamp = TRWConfig.model_fields["framework_version"].default
    files = _tracked_test_files()
    assert len(files) > 1000, "test-tree listing is implausibly small; the guard would be vacuous"

    offenders = []
    for rel in files:
        path = _REPO_ROOT / rel
        if not path.is_file():  # tracked but deleted in the working tree
            continue
        # Binary fixtures decode to replacement characters, which cannot form a stamp.
        text = path.read_bytes().decode("utf-8", errors="replace")
        if _pins(text, stamp):
            offenders.append(rel)

    assert not offenders, (
        f"these test files contain the current framework stamp {stamp}; a framework bump would have "
        "to edit them. Use the synthetic v99.9_TRW for fixture data, or read the stamp from "
        f"TRWConfig.model_fields['framework_version'].default when the test checks the real canon: {offenders}"
    )


def test_the_guard_detects_both_forms() -> None:
    # A made-up stamp, so this file never pins the real one (it is itself scanned).
    assert _pins("framework: v1.2_TRW\n", "v1.2_TRW")
    assert _pins("> **v1.2 mandate**", "v1.2_TRW")
    assert not _pins("framework: v99.9_TRW\n", "v1.2_TRW")
