"""Prevent packaged guidance from emitting invalid build-check calls."""

from __future__ import annotations

import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"
BUILD_CHECK_CALL = re.compile(r"\btrw_build_check\(([^)]*)\)")


def test_executable_build_check_examples_record_observed_outcomes() -> None:
    """Every concrete call supplies the required outcome; ellipsis-only references remain illustrative."""
    failures: list[str] = []
    for path in sorted(DATA.rglob("*")):
        if path.suffix not in {".md", ".yaml", ".toml", ".sh"}:
            continue
        content = path.read_text(encoding="utf-8")
        for match in BUILD_CHECK_CALL.finditer(content):
            arguments = match.group(1)
            if "..." in arguments or "tests_passed" in arguments:
                continue
            line = content.count("\n", 0, match.start()) + 1
            failures.append(f"{path.relative_to(DATA)}:{line}: {match.group(0)}")

    assert not failures, "build-check examples omit required observed outcomes:\n" + "\n".join(failures)


def test_build_check_examples_do_not_invent_timeout_arguments() -> None:
    """The reporter has no timeout option; validation commands own their own timeouts."""
    failures: list[str] = []
    for path in sorted(DATA.rglob("*")):
        if path.suffix not in {".md", ".yaml", ".toml", ".sh"}:
            continue
        content = path.read_text(encoding="utf-8")
        for match in BUILD_CHECK_CALL.finditer(content):
            if "timeout_secs" not in match.group(1):
                continue
            line = content.count("\n", 0, match.start()) + 1
            failures.append(f"{path.relative_to(DATA)}:{line}: {match.group(0)}")

    assert not failures, "build-check examples use unsupported timeout arguments:\n" + "\n".join(failures)
