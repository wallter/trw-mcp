from __future__ import annotations

from pathlib import Path

from tests._factories import make_run_dir_with_structure
from trw_mcp.state.persistence import FileStateWriter

_MINIMAL_PRD_CONTENT = """\
---
prd:
  id: PRD-TEST-001
  title: Test PRD
  version: '1.0'
  status: draft
  priority: P1
  category: CORE
---

## 1. Problem Statement
This is a test PRD with minimal content.

## 2. Goals & Non-Goals
Goals listed here.

## 3. User Stories
User stories here.

## 4. Functional Requirements
FR01: The system shall do something.

## 5. Non-Functional Requirements
NFR01: Performance requirements.

## 6. Technical Approach
Technical approach details.

## 7. Test Strategy
Test strategy details.

## 8. Rollout Plan
Rollout plan details.

## 9. Success Metrics
Success metrics here.

## 10. Dependencies & Risks
Dependencies and risks.

## 11. Open Questions
Open questions here.

## 12. Traceability Matrix
| FR | Implementation | Test |
|----|----------------|------|
| FR01 | `src/module.py:func` | `test_tools_module.py:test_func` |
"""


def _make_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    return make_run_dir_with_structure(
        tmp_path,
        task="extra-coverage-test",
        run_id="20260101T000000Z-extra1234",
        writer=writer,
        with_scratch_orchestrator=True,
    )


def _make_prd_file(
    prds_dir: Path,
    prd_id: str,
    status: str = "approved",
) -> Path:
    content = f"""\
---
prd:
  id: {prd_id}
  title: Test PRD
  version: "1.0"
  status: {status}
  priority: P1
---

# {prd_id}
"""
    prd_file = prds_dir / f"{prd_id}.md"
    prd_file.write_text(content, encoding="utf-8")
    return prd_file
