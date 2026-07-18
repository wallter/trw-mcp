"""PRD-CORE-205 NFR02 — path confinement + no sensitive payloads in receipts."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trw_mcp.models._evidence_core import EntryState, ReceiptState
from trw_mcp.models._evidence_plans import BuildCommandResult, CommandClass
from trw_mcp.tools._evidence_binding import (
    StableReadError,
    build_content_binding,
    mint_run_owned_scope,
    read_content_entry,
)


class TestReceiptsRejectEscapePathsAndRedactSensitivePayloads:
    def test_traversal_path_rejected_at_model(self) -> None:
        from trw_mcp.models._evidence_core import ContentEntry

        with pytest.raises(ValidationError):
            ContentEntry(path="../../etc/passwd", state=EntryState.FILE, byte_digest="a" * 64, byte_size=1)

    def test_absolute_path_rejected_at_model(self) -> None:
        from trw_mcp.models._evidence_core import ContentEntry

        with pytest.raises(ValidationError):
            ContentEntry(path="/etc/passwd", state=EntryState.FILE, byte_digest="a" * 64, byte_size=1)

    def test_escaping_symlink_fails_read(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("SECRET_VALUE", encoding="utf-8")
        (project / "leak").symlink_to(outside)
        with pytest.raises(StableReadError):
            read_content_entry(project, "leak")

    def test_out_of_root_journal_paths_never_scoped(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / "a.py").write_text("code", encoding="utf-8")
        run = project / "run"
        (run / "meta").mkdir(parents=True)
        import json

        (run / "meta" / "events.jsonl").write_text(
            "\n".join(
                json.dumps({"event": "file_modified", "file": f})
                for f in [str(project / "a.py"), "/etc/shadow", "../../secrets.env"]
            ),
            encoding="utf-8",
        )
        scope = mint_run_owned_scope(run, project, scope_id="sc1")
        assert scope.required_paths == ("a.py",)
        outcome = build_content_binding(scope, project)
        assert outcome.state is ReceiptState.VALID
        bound_paths = {e.path for e in outcome.binding.entries} if outcome.binding else set()
        assert bound_paths == {"a.py"}

    def test_command_result_does_not_store_raw_argv_or_env(self) -> None:
        # NFR02: the model carries a redacted label + class, not raw argv/env.
        result = BuildCommandResult(
            command_id="pytest", label="pytest -q", command_class=CommandClass.TEST, exit_code=0
        )
        fields = set(BuildCommandResult.model_fields)
        for forbidden in ("argv", "env", "environment", "raw_command", "provider_response"):
            assert forbidden not in fields
        assert result.label == "pytest -q"

    def test_oversized_free_text_rejected(self) -> None:
        from trw_mcp.models._evidence_core import EvidenceLimits

        huge = "x" * (EvidenceLimits.MAX_FREE_TEXT_BYTES + 1)
        with pytest.raises(ValidationError):
            BuildCommandResult(
                command_id="c",
                label="ok",
                command_class=CommandClass.OTHER,
                exit_code=0,
                limitations=huge,
            )
