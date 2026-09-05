"""PRD-CORE-247-FR03: ``trw-mcp local feedback`` / ``local recall`` reuse the tool code.

The load-bearing assertion is not "the subcommand exists" — it is that the CLI
branch reaches the SAME top-level callable the MCP tool wrapper reaches, so no
second redaction, validation, ranking, or persistence path exists to drift from
it. That is asserted three ways:

1. the shared callables are observed to be invoked exactly once per CLI run;
2. both subcommands exit 0 against a real ``.trw`` in a subprocess (the real
   argparse dispatch, not a hand-built ``Namespace``); and
3. an AST scan of the CLI/marshalling modules proves neither imports a
   redaction, ranking, or storage primitive of its own.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src" / "trw_mcp"


# ---------------------------------------------------------------------------
# FR03 — the shared callables are what the CLI reaches
# ---------------------------------------------------------------------------


def test_local_feedback_and_recall_call_the_shared_implementations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR03 acceptance: each shared callable is invoked exactly once per CLI run.

    The seam patched is ``submit_feedback`` / ``execute_recall`` — the callables
    the MCP tool wrappers use — NOT the CLI functions under test. If the CLI
    branch ever grew its own implementation, these counters would stay at zero
    while the command still succeeded, which is exactly the wiring defect
    PRD-FIX-073-FR03 shipped.
    """
    from trw_mcp.server import _subcommands_misc

    feedback_calls: list[dict[str, Any]] = []
    recall_calls: list[tuple[str, Path]] = []

    def _fake_submit_feedback(**kwargs: Any) -> dict[str, object]:
        feedback_calls.append(kwargs)
        return {"success": True, "feedback_id": "fb-1"}

    def _fake_execute_recall(query: str, trw_dir: Path, config: object, **kwargs: Any) -> dict[str, object]:
        recall_calls.append((query, trw_dir))
        return {"learnings": [{"id": "L-1", "summary": "shared recall reached"}]}

    monkeypatch.setattr("trw_mcp.tools.submit_feedback.submit_feedback", _fake_submit_feedback)
    monkeypatch.setattr("trw_mcp.tools._recall_impl.execute_recall", _fake_execute_recall)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        _subcommands_misc._run_local(
            argparse.Namespace(
                local_command="feedback",
                category="bug",
                subject="transport down",
                message="the surface never attached",
                contact_email=None,
            )
        )
    assert exit_info.value.code == 0
    assert len(feedback_calls) == 1, "local feedback must reach submit_feedback exactly once"
    assert feedback_calls[0]["subject"] == "transport down"

    with pytest.raises(SystemExit) as exit_info:
        _subcommands_misc._run_local(
            argparse.Namespace(
                local_command="recall",
                query="degraded mode",
                tag=[],
                max_results=3,
            )
        )
    assert exit_info.value.code == 0
    assert len(recall_calls) == 1, "local recall must reach execute_recall exactly once"
    assert recall_calls[0][0] == "degraded mode"


def test_local_recall_and_feedback_exit_zero_through_the_real_cli(tmp_path: Path) -> None:
    """FR03 acceptance: both subcommands exit 0 against a live ``.trw``.

    Real argparse dispatch in a subprocess, so a subparser that was declared but
    never routed would fail here even though the unit test above passes.
    """
    subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", "local", "learn", "--summary", "seed", "--detail", "seeded row"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        check=True,
    )

    recall = subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", "local", "recall", "--query", "seed"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert recall.returncode == 0, recall.stderr

    feedback = subprocess.run(
        [
            sys.executable,
            "-m",
            "trw_mcp.server",
            "local",
            "feedback",
            "--category",
            "bug",
            "--subject",
            "s",
            "--message",
            "m",
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    # An unconfigured backend is a reported result, never a traceback: the CLI
    # inherits submit_feedback's never-raises contract.
    assert feedback.returncode == 0, feedback.stderr
    assert "Traceback" not in feedback.stderr
    assert "Feedback" in feedback.stdout


def test_local_usage_lists_recall_and_feedback() -> None:
    """FR03: the capability must be reachable without reading argparse source."""
    result = subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", "local"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "recall" in result.stdout
    assert "feedback" in result.stdout
    assert "--query" in result.stdout
    assert "--category" in result.stdout


def test_no_redaction_ranking_or_persistence_logic_is_duplicated() -> None:
    """FR03 acceptance: the CLI branch marshals arguments and formats output only.

    An AST import scan, not a substring grep: the point is that neither module
    pulls in a storage, redaction, or ranking primitive it could reimplement
    against. They may import the two shared entry points and nothing else from
    those subsystems.
    """
    forbidden_names = {
        "_redact_pii",
        "rank_by_utility",
        "store_learning",
        "search_patterns",
        "recall_learnings",
        "_submit_feedback_impl",
        "get_backend",
    }
    for rel in ("services/local_surface_service.py", "server/_subcommands_misc.py"):
        tree = ast.parse((_SRC / rel).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        leaked = imported & forbidden_names
        assert not leaked, f"{rel} imports a duplicated primitive: {sorted(leaked)}"
