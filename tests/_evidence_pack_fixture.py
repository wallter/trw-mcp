"""Shared fixture for the PRD-CORE-323 evidence-pack tests (``test_evidence_pack*.py``).

Builds a project with one run through the production writers (``FileEventLogger``,
``record_build_receipt``, ``record_review_receipt``) and dispatches CLI verbs through
the real argparse tree and ``SUBCOMMAND_HANDLERS``. Nothing here imports
``trw_mcp.evidence_pack``, so a test module using it still collects, and fails on the
dispatcher's usage exit, before that package exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

PLANTED_KEY = "sk-proj-" + "Zq7Xw4Lm9Tr2Vb8Nc5Hd1Kf6Gj3Ps0Yu"
PLANTED_EMAIL = "leaky.reviewer@example.com"
TRW_MCP_SRC = str(Path(__file__).resolve().parents[1] / "src")
TRW_MEMORY_SRC = str(Path(__file__).resolve().parents[2] / "trw-memory" / "src")


def prd_text(prd_id: str, *, matrix_extra: str = "") -> str:
    return f"""---
prd:
  id: {prd_id}
  title: "Fixture {prd_id}"
  status: approved
  verification:
    mappings:
      - requirement_id: {prd_id}-FR01
        acceptance_criteria:
          - "the pack lists {prd_id}"
        method: test
        evidence_artifact: "trw-mcp/tests/test_fixture.py"
---

# {prd_id}: fixture

## Decisions

- D1: keep it small.

## 12. Traceability Matrix

| Requirement | Source | Implementation | Call chain | Test | Status |
|-------------|--------|----------------|------------|------|--------|
| FR01 | CL-1 | `src/feature.py` | `cli:trw-mcp run evidence-pack` -> `trw_mcp.evidence_pack.build_pack` | t | Planned |
{matrix_extra}"""


@dataclass
class Fixture:
    root: Path
    run: Path
    positive_build: str
    negative_build: str
    corrupt_build: str
    review: str


def build_fixture(
    root: Path,
    *,
    review_reason: str = "limited review",
    matrix_extra: str = "",
    scope_extra: tuple[str, ...] = (),
    run_name: str = "run-1",
) -> Fixture:
    """A project at *root* (the suite's isolated project root) with one run built by the real writers."""
    from trw_mcp.state.persistence import FileEventLogger, FileStateWriter
    from trw_mcp.tools._evidence_writers import record_build_receipt
    from trw_mcp.tools._review_receipt_writer import record_review_receipt

    prds = root / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True, exist_ok=True)
    (prds / "PRD-CORE-901.md").write_text(prd_text("PRD-CORE-901", matrix_extra=matrix_extra), encoding="utf-8")
    (prds / "PRD-CORE-902-second.md").write_text(prd_text("PRD-CORE-902"), encoding="utf-8")
    (root / "src").mkdir(parents=True, exist_ok=True)
    feature = root / "src" / "feature.py"
    feature.write_text("VALUE = 1\n", encoding="utf-8")

    run = root / ".trw" / "runs" / "task" / run_name
    meta = run / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    FileStateWriter().write_yaml(
        meta / "run.yaml",
        {
            "run_id": run_name,
            "task": "task",
            "status": "active",
            "prd_scope": ["PRD-CORE-901", "PRD-CORE-902", *scope_extra],
        },
    )
    FileEventLogger().log_event(meta / "events.jsonl", "file_modified", {"file": str(feature)})

    def _build(passed: bool) -> str:
        outcome = record_build_receipt(
            run,
            root,
            tests_passed=passed,
            static_checks_clean=True,
            scope_label="full",
            coverage_pct=None,
            policy_mode="observe",
        )
        assert outcome is not None and outcome.ok, outcome
        return outcome.receipt_id

    positive, negative = _build(True), _build(False)
    corrupt = "build-corrupt-0001"
    (meta / "receipts" / "build" / f"{corrupt}.json").write_text("{not json", encoding="utf-8")
    review = record_review_receipt(
        run,
        {
            "review_id": "review-1",
            "mode": "manual",
            "substantive": False,
            "limited_reason": review_reason,
            "verdict": "pass",
            "timestamp": "2026-09-26T00:00:00Z",
        },
        ("PRD-CORE-901",),
        policy_mode="observe",
    )
    assert review.ok, review
    return Fixture(root, run, positive, negative, corrupt, review.receipt_id)


def dispatch(argv: list[str]) -> int:
    """Parse *argv* with the real CLI tree and dispatch through SUBCOMMAND_HANDLERS; return the exit code."""
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    with pytest.raises(SystemExit) as exc_info:
        args = _build_arg_parser().parse_args(argv)
        SUBCOMMAND_HANDLERS[args.command](args)
    code = exc_info.value.code
    return code if isinstance(code, int) else 1


def export(fx: Fixture, out: Path) -> int:
    return dispatch(["run", "evidence-pack", str(fx.run), "--out", str(out)])


def sealed_entries(node: object) -> list[dict[str, object]]:
    """Every sealed entry (a dict carrying ``as_of`` and ``sha256``) anywhere under *node*."""
    found: list[dict[str, object]] = []
    if isinstance(node, dict):
        if "as_of" in node and "sha256" in node:
            found.append(node)
        for value in node.values():
            found.extend(sealed_entries(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(sealed_entries(value))
    return found


def canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def load_pack(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_bytes())
    assert isinstance(loaded, dict)
    return loaded


def receipt_block(pack_doc: dict[str, object], receipt_type: str) -> dict[str, object]:
    sections = pack_doc["sections"]
    assert isinstance(sections, dict)
    block = sections["evidence"]["receipts"][receipt_type]
    assert isinstance(block, dict)
    return block


def receipt_item(pack_doc: dict[str, object], receipt_type: str, receipt_id: str) -> dict[str, dict[str, object]]:
    items = receipt_block(pack_doc, receipt_type)["items"]
    assert isinstance(items, list)
    (match,) = [item for item in items if item["receipt_id"] == receipt_id]
    return match


def clone_build_receipts(fx: Fixture, count: int) -> None:
    """Copy the real writer's positive receipt under *count* new ids (bytes as the writer produced them)."""
    builds = fx.run / "meta" / "receipts" / "build"
    raw = (builds / f"{fx.positive_build}.json").read_bytes()
    for index in range(count):
        (builds / f"build-clone-{index:05d}.json").write_bytes(raw)


def snapshot(root: Path, skip: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != skip
    }
