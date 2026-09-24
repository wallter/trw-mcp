"""Monorepo regression guard for the manifest-driven tracked canon mirrors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "trw-mcp/src/trw_mcp/data/framework_canons.json"

if not (_REPO_ROOT / "scripts").is_dir():
    pytest.skip("monorepo-only canon mirror manifest", allow_module_level=True)


def test_manifest_names_one_authoring_source_and_all_tracked_mirrors() -> None:
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    specs = {item["id"]: item for item in raw["artifacts"]}

    assert specs["framework"]["authoring_source"] == "trw-mcp/src/trw_mcp/data/framework.md"
    assert set(specs["framework"]["tracked_mirrors"]) == {
        "FRAMEWORK.md",
        ".trw/frameworks/FRAMEWORK.md",
        "trw-mcp/FRAMEWORK.md",
    }
    assert specs["aaref"]["authoring_source"] == "trw-mcp/src/trw_mcp/data/aaref.md"
    assert "compiled_canons" not in raw, "S4: each canon is one hand-edited document, not compiled views"
    assert set(specs["aaref"]["tracked_mirrors"]) == {
        "AARE-F-FRAMEWORK.md",
        ".trw/frameworks/AARE-F-FRAMEWORK.md",
    }

    # This registry is shipped in the public wheel. Monorepo-private vendor
    # projections are not usable install/runtime paths and must not leak into it.
    # fmt: off
    assert "trw-eval/" not in json.dumps(raw)  # trw-leak-allow: proprietary_path negative assertion: proves the shipped manifest excludes this vendor path
    # fmt: on

    for spec in specs.values():
        source = (_REPO_ROOT / spec["authoring_source"]).read_bytes()
        for mirror in spec["tracked_mirrors"]:
            assert (_REPO_ROOT / mirror).read_bytes() == source


def test_shared_worktree_policy_has_one_normative_source() -> None:
    """PRD-CORE-206-FR05: one authoring source governs the shared-worktree policy.

    The coherent-green + Git-decision-matrix policy is authored once in
    ``framework.md``; every tracked mirror is byte-identical (source parity), and no
    other tracked mirror re-authors the full decision matrix independently.
    """
    raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    specs = {item["id"]: item for item in raw["artifacts"]}
    framework = specs["framework"]

    source_path = _REPO_ROOT / framework["authoring_source"]
    source_bytes = source_path.read_bytes()
    source_text = source_bytes.decode("utf-8")

    # The normative policy lives in the single authoring source.
    marker = "Commit each coherent, focused, green milestone promptly"
    hazard = "command-specific operator authorization and exclusive ownership"
    assert marker in source_text
    assert hazard in source_text

    # Every tracked mirror is a byte-identical projection of that one source — the
    # policy is never re-authored, only mirrored.
    for mirror in framework["tracked_mirrors"]:
        assert (_REPO_ROOT / mirror).read_bytes() == source_bytes, f"mirror drift: {mirror}"


def test_nested_monorepo_instructions_resolve_parent_protocol() -> None:
    """The tracked package adapter links to the canonical monorepo protocol.

    4144a0726 made AGENTS.md the authored package instruction surface, replacing
    the former ignored sync output. Check its actual parent-document routing,
    not the obsolete generated block or combined-framework path spellings.
    """
    nested = _REPO_ROOT / "trw-mcp/AGENTS.md"
    instructions = nested.read_text(encoding="utf-8")
    for name in ("tool-lifecycle.md", "memory-routing.md"):
        relative = f"../docs/documentation/{name}"
        assert f"]({relative})" in instructions
        target = (nested.parent / relative).resolve()
        assert target == _REPO_ROOT / "docs/documentation" / name
        assert target.is_file()
    lifecycle = (_REPO_ROOT / "docs/documentation/tool-lifecycle.md").read_text(encoding="utf-8")
    assert "Do NOT call `trw_deliver` unless" in lifecycle
    assert "trw_session_start()" in lifecycle
    assert "Agent " + "Teams" not in instructions


def test_runtime_instruction_surfaces_point_at_the_one_framework_document() -> None:
    """S4: the compact core view is gone; guidance names FRAMEWORK.md sections."""
    surfaces = (
        "trw-mcp/src/trw_mcp/data/messages/messages.yaml",
        "trw-mcp/src/trw_mcp/data/hooks/session-start.sh",
        "trw-mcp/src/trw_mcp/data/hooks/post-compact.sh",
        "trw-mcp/src/trw_mcp/data/claude_code/loop.md",
        "trw-mcp/src/trw_mcp/server/_app.py",
    )
    for relative in surfaces:
        text = (_REPO_ROOT / relative).read_text(encoding="utf-8")
        assert ".trw/frameworks/FRAMEWORK.md" in text, relative
        assert "FRAMEWORK-CORE" not in text, relative
