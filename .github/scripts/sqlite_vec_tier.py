"""Run trw-mcp's sqlite-vec tier and fail loudly if the C extension is not really exercised.

PRD-SEC-019 FR01: trw-mcp's CI pinned Python 3.11 after "sqlite-vec segfaults on GitHub
Actions Ubuntu runners" (2026-03-25, no root cause recorded). This script is the one
definition of the tier that proves or disproves that claim on the interpreter it runs
under. The workflow ``sqlite-vec-py312.yml`` and the monorepo's local container repro both
call it, so the two cannot drift.

Three steps, each in a child process so a segfault is reported by signal name instead of
killing this script:

1. ``--probe``: load sqlite-vec into a plain ``sqlite3`` connection, create a vec0 table,
   insert and run a KNN query; then open trw-memory's ``SQLiteBackend`` and require
   ``vec_available`` plus a vector round trip. trw-memory loads sqlite-vec fail-open (a
   load failure only logs a warning), so without this step a broken extension would pass
   the tier as silently degraded BM25.
2. pytest on ``TIER``: the trw-mcp test modules that open a real ``SQLiteBackend`` (which
   loads the extension) and store or migrate vectors through it.
3. The JUnit report: zero tests, or any skip whose message names sqlite, fails the run
   (trw-mcp's counterpart of trw-memory's ``TRW_REQUIRE_SQLITE_VEC=1``).

Proves: the installed sqlite-vec wheel loads and answers vec0 KNN queries under this
interpreter and its SQLite, and the tier's tests pass on it. Does not prove: the rest of
trw-mcp's suite passes on this interpreter (run the ``full`` suite for that).

Usage (from the trw-mcp package root): ``python .github/scripts/sqlite_vec_tier.py [-n WORKERS]``.
The worker count is the only option: no pytest selection filter (``-k``, ``-m``, node ids) can
narrow the tier and still print PASS.
"""

from __future__ import annotations

import argparse
import platform
import signal
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from importlib.metadata import version
from pathlib import Path

TIER = (
    "tests/test_retrieval_capability.py",
    "tests/test_doctor_memory_store.py",
    "tests/test_store_migration.py",
    "tests/test_store_migration_strays.py",
)
_DIM = 8


def _environment() -> str:
    libc, libc_version = platform.libc_ver()
    return (
        f"python {platform.python_version()} ({sys.executable}) | {platform.machine()} | "
        f"{libc or 'libc?'} {libc_version or '?'} | sqlite {sqlite3.sqlite_version} | "
        f"sqlite-vec {version('sqlite-vec')} | trw-memory {version('trw-memory')} | trw-mcp {version('trw-mcp')}"
    )


def _probe() -> None:
    """Exercise the C extension directly, then through trw-memory's backend. Raises on any failure."""
    import sqlite_vec
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    (vec_version,) = conn.execute("select vec_version()").fetchone()
    conn.execute(f"create virtual table v using vec0(embedding float[{_DIM}])")
    for rowid in range(1, _DIM + 1):
        unit = [1.0 if i == rowid - 1 else 0.0 for i in range(_DIM)]
        conn.execute("insert into v(rowid, embedding) values (?, ?)", (rowid, sqlite_vec.serialize_float32(unit)))
    query = sqlite_vec.serialize_float32([0.0, 0.0, 1.0] + [0.0] * (_DIM - 3))
    nearest = conn.execute("select rowid from v where embedding match ? order by distance limit 1", (query,)).fetchone()
    if nearest != (3,):
        raise AssertionError(f"vec0 KNN returned {nearest!r}, expected (3,)")
    conn.close()

    with tempfile.TemporaryDirectory() as tmp:
        backend = SQLiteBackend(Path(tmp) / "memory.db", dim=_DIM)
        try:
            if not backend.vec_available:
                raise AssertionError("trw-memory SQLiteBackend opened with vec_available=False")
            for index, entry_id in enumerate(("L-a", "L-b", "L-c")):
                backend.store(MemoryEntry(id=entry_id, content=f"learning {entry_id}", namespace="default"))
                backend.upsert_vector(entry_id, [1.0 if i == index else 0.0 for i in range(_DIM)], namespace="default")
            hits = backend.search_vectors([0.0, 1.0] + [0.0] * (_DIM - 2), top_k=1, namespace="default")
            if not hits or hits[0][0] != "L-b":
                raise AssertionError(f"SQLiteBackend.search_vectors returned {hits!r}, expected L-b first")
        finally:
            backend.close()
    print(f"probe ok: vec_version {vec_version}; vec0 KNN and SQLiteBackend vector round trip pass")


def _run(argv: list[str], step: str, environment: str) -> int:
    """Run a child; name the signal when it dies of one (a segfault is the result this tier looks for)."""
    code = subprocess.run(argv, check=False).returncode  # noqa: S603 -- argv is this interpreter plus fixed args
    if code < 0:
        name = signal.Signals(-code).name
        print(f"FAIL {step}: killed by {name} ({environment})", file=sys.stderr)
    elif code != 0:
        print(f"FAIL {step}: exit {code}", file=sys.stderr)
    return code


def _junit_failure(junit: Path) -> str | None:
    root = ET.parse(junit).getroot()  # noqa: S314 -- a report this run just wrote
    cases = list(root.iter("testcase"))
    if not cases:
        return "the tier collected no tests"
    for case in cases:
        for skipped in case.iter("skipped"):
            reason = skipped.get("message", "")
            if "sqlite" in reason.lower():
                return f"{case.get('classname')}::{case.get('name')} skipped: {reason}"
    return None


def _workers(value: str) -> str:
    if value != "auto" and not value.isdigit():
        raise argparse.ArgumentTypeError(f"expected a worker count or auto, got {value!r}")
    return value


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="trw-mcp sqlite-vec tier (PRD-SEC-019 FR01)")
    parser.add_argument("-n", dest="workers", type=_workers, default="0", help="pytest-xdist workers: a count or auto")
    workers = parser.parse_args(argv).workers
    missing = [path for path in TIER if not Path(path).is_file()]
    if missing:
        print(f"FAIL: tier modules missing (run from the trw-mcp package root): {missing}", file=sys.stderr)
        return 2
    environment = _environment()
    print(environment, flush=True)
    python = [sys.executable, "-X", "faulthandler"]
    if code := _run([*python, __file__, "--probe"], "probe", environment):
        return code
    junit = Path(tempfile.mkdtemp()) / "sqlite-vec-tier.xml"
    if code := _run(
        [*python, "-m", "pytest", *TIER, "-q", "-rs", f"--junitxml={junit}", "-p", "no:cacheprovider", "-n", workers],
        "pytest",
        environment,
    ):
        return code
    if failure := _junit_failure(junit):
        print(f"FAIL junit: {failure}", file=sys.stderr)
        return 1
    print(f"PASS sqlite-vec tier ({environment})")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        _probe()
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
