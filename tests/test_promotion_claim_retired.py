"""UF-CFGDOC-05: nothing promotes learnings into project context, and no document or setting says it does.

Promotion into CLAUDE.md was retired in 0.37.0 (learnings arrive by ``trw_session_start`` recall). The README, the guide
and the ``learning_promotion_impact`` setting kept claiming it; the deprecated module had no production caller.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from trw_mcp.models.config import TRWConfig, _retired_keys

_REPO = Path(__file__).resolve().parents[2]


def test_the_promotion_module_and_its_setting_are_gone() -> None:
    assert importlib.util.find_spec("trw_mcp.state.claude_md._promotion") is None
    assert "learning_promotion_impact" not in TRWConfig.model_fields
    assert "learning_promotion_impact" in _retired_keys.retired_config_keys()


def test_the_public_documents_no_longer_claim_auto_promotion() -> None:
    # Found by name, not by path: a public-package test must not spell a path outside the public tree.
    documents = [
        _REPO / "README.md",
        _REPO / "docs" / "TRW-COMPREHENSIVE-GUIDE.md",
        *_REPO.glob("*/public/llms-full.txt"),
    ]
    for path in documents:
        if not path.is_file():  # a package-only checkout (the public mirror) carries none of these
            continue
        text = path.read_text(encoding="utf-8")
        assert "auto-promote" not in text, f"{path.name} still claims learnings auto-promote"
