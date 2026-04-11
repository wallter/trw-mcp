"""Regression tests for the UserPromptSubmit hook payload contract."""

from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_HOOK_PATHS = (
    _ROOT.parent / ".claude" / "hooks" / "user-prompt-submit.sh",
    _ROOT / "src" / "trw_mcp" / "data" / "hooks" / "user-prompt-submit.sh",
)


def test_user_prompt_submit_hook_reads_prompt_field() -> None:
    for hook_path in _HOOK_PATHS:
        content = hook_path.read_text(encoding="utf-8")

        assert ".prompt // empty" in content
        assert '"prompt"[[:space:]]*:[[:space:]]*"[^"]*"' in content
        assert ".message // empty" not in content
        assert '"message"[[:space:]]*:[[:space:]]*"[^"]*"' not in content
