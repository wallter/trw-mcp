"""Tests for OpenCode instruction rendering and model-family detection."""

from __future__ import annotations


class TestRenderOpencodeInstructions:
    """Unit tests for render_opencode_instructions portable v25 output."""

    def _render(self, family: str) -> str:
        from trw_mcp.state.claude_md._static_sections import render_opencode_instructions

        return render_opencode_instructions(family)

    def test_legacy_families_emit_identical_portable_content(self) -> None:
        """Legacy family hints no longer fork core protocol text."""
        outputs = {family: self._render(family) for family in ("qwen", "gpt", "claude", "generic")}
        assert len(set(outputs.values())) == 1

    def test_portable_content_has_core_protocol(self) -> None:
        """Portable output includes TRW workflow, project-native validation, and nudges."""
        content = self._render("qwen")
        assert "# TRW Instructions" in content
        assert "trw_session_start()" in content
        assert "trw_deliver()" in content
        assert "project-native" in content
        assert "Nudge Policy" in content

    def test_portable_content_omits_family_prompt_recipes(self) -> None:
        """Provider/model prompt recipes live in adapters, not core OpenCode instructions."""
        content = self._render("qwen")
        for token in (
            "/think",
            "vLLM",
            "chain-of-thought",
            "extended thinking",
            "200K",
            "128K",
            "32K",
        ):
            assert token not in content


class TestDetectModelFamily:
    """Unit tests for detect_model_family — FR05 model ID classification."""

    def _detect(self, model: str) -> str:
        from trw_mcp.bootstrap._opencode import detect_model_family

        return detect_model_family({"model": model})

    def test_qwen_model_id_detected(self) -> None:
        """vllm/Qwen/Qwen3-Coder-Next-FP8 maps to 'qwen'."""
        assert self._detect("vllm/Qwen/Qwen3-Coder-Next-FP8") == "qwen"

    def test_qwen_lowercase_detected(self) -> None:
        """qwen3-coder (lowercase) maps to 'qwen'."""
        assert self._detect("qwen3-coder") == "qwen"

    def test_gpt_model_id_detected(self) -> None:
        """gpt-5.4 maps to 'gpt'."""
        assert self._detect("gpt-5.4") == "gpt"

    def test_gpt4o_model_id_detected(self) -> None:
        """gpt-4o maps to 'gpt'."""
        assert self._detect("gpt-4o") == "gpt"

    def test_o3_mini_model_id_detected(self) -> None:
        """o3-mini maps to 'gpt'."""
        assert self._detect("o3-mini") == "gpt"

    def test_o3_model_id_detected(self) -> None:
        """o3 maps to 'gpt'."""
        assert self._detect("o3") == "gpt"

    def test_o1_model_id_detected(self) -> None:
        """o1 maps to 'gpt'."""
        assert self._detect("o1") == "gpt"

    def test_o1_preview_model_id_detected(self) -> None:
        """o1-preview maps to 'gpt'."""
        assert self._detect("o1-preview") == "gpt"

    def test_claude_sonnet_model_id_detected(self) -> None:
        """claude-sonnet-4-6 maps to 'claude'."""
        assert self._detect("claude-sonnet-4-6") == "claude"

    def test_claude_opus_model_id_detected(self) -> None:
        """claude-opus-4-6 maps to 'claude'."""
        assert self._detect("claude-opus-4-6") == "claude"

    def test_unknown_model_falls_back_to_generic(self) -> None:
        """Unknown model ID maps to 'generic'."""
        assert self._detect("my-custom-model-7b") == "generic"

    def test_empty_model_falls_back_to_generic(self) -> None:
        """Empty model field maps to 'generic'."""
        from trw_mcp.bootstrap._opencode import detect_model_family

        assert detect_model_family({}) == "generic"
        assert detect_model_family({"model": ""}) == "generic"

    def test_llama_model_falls_back_to_generic(self) -> None:
        """llama3 model maps to 'generic'."""
        assert self._detect("meta/llama3-70b-instruct") == "generic"

    def test_case_insensitive_detection(self) -> None:
        """Detection is case-insensitive for all families."""
        assert self._detect("GPT-4O") == "gpt"
        assert self._detect("CLAUDE-SONNET") == "claude"
        assert self._detect("QWEN3-CODER") == "qwen"
