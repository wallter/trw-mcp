from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig

SAMPLE_PRD = """\
---
id: PRD-TEST-001
status: draft
---
# PRD-TEST-001: Test Feature

## 3. Functional Requirements

### FR01: Implement `UserValidator` class

The `UserValidator` class MUST validate input using the `--strict` flag.

Given a user input
When `validate()` is called with `--strict`
Then validation errors are returned as `ValidationResult` objects.

### FR02: Add `DataProcessor` pipeline

The `DataProcessor` MUST support `--dry-run` mode for testing.

## 4. Non-Functional Requirements

NFR content here.
"""

SAMPLE_DIFF_WITH_MATCHES = """\
diff --git a/src/validator.py b/src/validator.py
+class UserValidator:
+    def validate(self, strict=True):
+        result = validate()
+        return ValidationResult(errors=[])
+    --strict flag handling
+DataProcessor
+--dry-run
"""

SAMPLE_DIFF_WITHOUT_MATCHES = """\
diff --git a/src/other.py b/src/other.py
+class SomethingElse:
+    pass
"""

SAMPLE_DIFF_REMOVED_ONLY = """\
diff --git a/src/validator.py b/src/validator.py
--- a/src/validator.py
+++ b/src/validator.py
-class UserValidator:
-    def validate(self, strict=True):
-        return ValidationResult(errors=[])
+class NewValidator:
+    pass
"""


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory."""
    d = tmp_path / "runs" / "20260304T120000Z-reconcile-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: reconcile-test\nstatus: active\nphase: review\ntask_name: test-task\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


def make_config(*, prds_relative_path: str = "prds") -> TRWConfig:
    return TRWConfig(prds_relative_path=prds_relative_path)
