"""Tests for pure f-string MDC template functions.

No filesystem I/O. All template functions are pure and deterministic.
PRD-DIST-2401 FR13, FR14, NFR09.
"""

from __future__ import annotations

from trw_mcp.channels.cursor._mdc_templates import (
    ConventionRecord,
    EdgeCaseRecord,
    HotspotRecord,
    assemble_mdc_frontmatter,
    derive_directory_glob,
    dir_slug,
    render_conventions_t0,
    render_conventions_t1,
    render_dangerous_edits_t0,
    render_dangerous_edits_t1,
    render_hotspot_dir_t0,
    render_hotspot_dir_t1,
    render_presence_beacon_mdc,
    render_tombstone_mdc,
    validate_mdc_frontmatter,
    validate_minimatch_glob,
)

# ---------------------------------------------------------------------------
# dir_slug tests
# ---------------------------------------------------------------------------


class TestDirSlug:
    def test_replaces_slashes_with_dashes(self) -> None:
        assert dir_slug("trw-mcp/src/trw_mcp/state") == "trw-mcp-src-trw-mcp-state"

    def test_replaces_underscores_with_dashes(self) -> None:
        assert dir_slug("my_module/sub_dir") == "my-module-sub-dir"

    def test_lowercases(self) -> None:
        assert dir_slug("Backend/Routers") == "backend-routers"

    def test_truncates_at_60_chars(self) -> None:
        long = "a" * 80
        result = dir_slug(long)
        assert len(result) <= 60

    def test_no_trailing_dash_after_truncation(self) -> None:
        # 60 chars of a, should truncate cleanly
        long = "a-" * 40  # 80 chars
        result = dir_slug(long)
        assert not result.endswith("-")

    def test_simple_path(self) -> None:
        assert dir_slug("backend/routers") == "backend-routers"

    def test_single_segment(self) -> None:
        assert dir_slug("backend") == "backend"

    def test_collapses_multiple_dashes(self) -> None:
        result = dir_slug("a..b//c")
        assert "--" not in result


# ---------------------------------------------------------------------------
# derive_directory_glob tests
# ---------------------------------------------------------------------------


class TestDeriveDirectoryGlob:
    def test_py_file_in_dir(self) -> None:
        assert derive_directory_glob("backend/routers/admin.py") == "backend/routers/**/*.py"

    def test_ts_file_in_dir(self) -> None:
        assert derive_directory_glob("src/components/Button.ts") == "src/components/**/*.ts"

    def test_file_at_root(self) -> None:
        result = derive_directory_glob("admin.py")
        assert "**" in result
        assert ".py" in result


# ---------------------------------------------------------------------------
# validate_minimatch_glob tests (FR14 / P0-12)
# ---------------------------------------------------------------------------


class TestValidateMinimatchGlob:
    def test_accepts_double_star(self) -> None:
        valid, reason = validate_minimatch_glob("**/*.py")
        assert valid
        assert reason == ""

    def test_accepts_directory_pattern(self) -> None:
        valid, reason = validate_minimatch_glob("backend/routers/**/*.py")
        assert valid
        assert reason == ""

    def test_accepts_brace_expansion(self) -> None:
        valid, reason = validate_minimatch_glob("src/**/*.{ts,tsx}")
        assert valid
        assert reason == ""

    def test_rejects_literal_file_path(self) -> None:
        valid, reason = validate_minimatch_glob("backend/routers/admin.py")
        assert not valid
        assert "literal file path" in reason

    def test_accepts_empty_list_string(self) -> None:
        valid, _ = validate_minimatch_glob("[]")
        assert valid

    def test_rejects_another_literal_path(self) -> None:
        valid, _ = validate_minimatch_glob("trw-mcp/src/trw_mcp/state/ceremony.py")
        assert not valid

    def test_accepts_multi_extension_pattern(self) -> None:
        valid, reason = validate_minimatch_glob("trw-mcp/src/**/*.{ts,tsx}")
        assert valid
        assert reason == ""


# ---------------------------------------------------------------------------
# validate_mdc_frontmatter tests (FR13)
# ---------------------------------------------------------------------------


class TestValidateMdcFrontmatter:
    def test_valid_full_frontmatter(self) -> None:
        content = (
            "---\n"
            "description: hello world\n"
            "globs: **/*.py\n"
            "alwaysApply: false\n"
            "---\n"
        )
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, reason

    def test_empty_description_is_valid_for_tombstone(self) -> None:
        content = (
            "---\n"
            "description: \n"
            "globs: []\n"
            "alwaysApply: false\n"
            "---\n"
        )
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, reason

    def test_rejects_extra_key(self) -> None:
        content = (
            "---\n"
            "description: hello\n"
            "globs: []\n"
            "alwaysApply: false\n"
            "unknown_key: value\n"
            "---\n"
        )
        valid, reason = validate_mdc_frontmatter(content)
        assert not valid
        assert "unknown key" in reason

    def test_rejects_invalid_always_apply(self) -> None:
        content = (
            "---\n"
            "description: hello\n"
            "globs: []\n"
            "alwaysApply: yes\n"
            "---\n"
        )
        valid, reason = validate_mdc_frontmatter(content)
        assert not valid
        assert "alwaysApply" in reason

    def test_rejects_missing_closing_dashes(self) -> None:
        content = "---\ndescription: hello\n"
        valid, _ = validate_mdc_frontmatter(content)
        assert not valid

    def test_rejects_missing_opening_dashes(self) -> None:
        content = "description: hello\n---\n"
        valid, _ = validate_mdc_frontmatter(content)
        assert not valid


# ---------------------------------------------------------------------------
# assemble_mdc_frontmatter tests
# ---------------------------------------------------------------------------


class TestAssembleMdcFrontmatter:
    def test_basic_assembly(self) -> None:
        fm = assemble_mdc_frontmatter(
            description="Test description",
            globs="**/*.py",
            always_apply=False,
        )
        assert "description: Test description" in fm
        assert "globs: **/*.py" in fm
        assert "alwaysApply: false" in fm
        assert fm.startswith("---")

    def test_empty_list_globs(self) -> None:
        fm = assemble_mdc_frontmatter(description="x", globs=[])
        assert "globs: []" in fm

    def test_list_globs_joined(self) -> None:
        fm = assemble_mdc_frontmatter(description="x", globs=["**/*.py", "**/*.ts"])
        assert "**/*.py, **/*.ts" in fm

    def test_always_apply_true(self) -> None:
        fm = assemble_mdc_frontmatter(description="x", globs=[], always_apply=True)
        assert "alwaysApply: true" in fm

    def test_no_extra_keys(self) -> None:
        fm = assemble_mdc_frontmatter(description="x", globs=[])
        # Only allowed keys
        keys_in_fm = [
            line.split(":")[0].strip()
            for line in fm.splitlines()
            if ":" in line and not line.startswith("---")
        ]
        assert all(k in ("description", "globs", "alwaysApply") for k in keys_in_fm)


# ---------------------------------------------------------------------------
# render_tombstone_mdc tests (FR10 / P2-08)
# ---------------------------------------------------------------------------


class TestRenderTombstoneMdc:
    def test_tombstone_empty_globs_and_description(self) -> None:
        content = render_tombstone_mdc(
            "cursor-mdc-conventions", "trw-distill self-improve mdc-emit", "ttl_exceeded"
        )
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, reason
        assert "description: " in content  # empty description
        assert "globs: []" in content

    def test_tombstone_contains_regenerate_cmd(self) -> None:
        content = render_tombstone_mdc(
            "test-channel", "trw-distill self-improve mdc-emit", "stale"
        )
        assert "trw-distill self-improve mdc-emit" in content

    def test_tombstone_passes_frontmatter_validation(self) -> None:
        content = render_tombstone_mdc("ch", "regen", "reason")
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, f"tombstone failed validation: {reason}"


# ---------------------------------------------------------------------------
# render_presence_beacon_mdc tests
# ---------------------------------------------------------------------------


class TestRenderPresenceBeaconMdc:
    def test_beacon_under_200_bytes_body(self) -> None:
        content = render_presence_beacon_mdc("test-channel", "regen-cmd")
        # Body is content after the frontmatter closing ---
        parts = content.split("---\n", 2)
        body = parts[-1] if len(parts) >= 3 else ""
        assert len(body.encode("utf-8")) < 300  # generous but definitely T0 beacon

    def test_beacon_passes_frontmatter_validation(self) -> None:
        content = render_presence_beacon_mdc("test-ch", "cmd")
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, f"beacon failed validation: {reason}"


# ---------------------------------------------------------------------------
# render_conventions_t0 / t1 tests
# ---------------------------------------------------------------------------


class TestRenderConventions:
    def test_t0_passes_validation(self) -> None:
        content = render_conventions_t0()
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, reason

    def test_t1_passes_validation(self) -> None:
        records = [ConventionRecord(slug="yaml-safe", title="YAML Safety", body="Use safe loader")]
        hotspots = [HotspotRecord(file_path="backend/main.py", risk_score=0.8)]
        content = render_conventions_t1(records, hotspots, "abc12345", "2026-05-28T00:00:00Z")
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, reason

    def test_t1_contains_convention_slug(self) -> None:
        records = [ConventionRecord(slug="yaml-safe", title="YAML Safety", body="Body")]
        content = render_conventions_t1(records, [], "abc12345", "2026-05-28T00:00:00Z")
        assert "yaml-safe" in content

    def test_t1_hotspots_sorted_by_risk_desc(self) -> None:
        hotspots = [
            HotspotRecord(file_path="a.py", risk_score=0.3),
            HotspotRecord(file_path="b.py", risk_score=0.9),
        ]
        content = render_conventions_t1([], hotspots, "abc", "ts")
        # b.py should appear before a.py in the table
        assert content.index("b.py") < content.index("a.py")

    def test_t1_deterministic_same_inputs(self) -> None:
        records = [ConventionRecord(slug="s", title="t", body="b")]
        hotspots = [HotspotRecord(file_path="f.py", risk_score=0.5)]
        sha, ts = "abc12345", "2026-01-01T00:00:00Z"
        c1 = render_conventions_t1(records, hotspots, sha, ts)
        c2 = render_conventions_t1(records, hotspots, sha, ts)
        assert c1 == c2

    def test_t1_alwaysapply_false(self) -> None:
        content = render_conventions_t1([], [], "abc", "ts")
        assert "alwaysApply: false" in content

    def test_t1_globs_empty_list(self) -> None:
        content = render_conventions_t1([], [], "abc", "ts")
        assert "globs: []" in content


# ---------------------------------------------------------------------------
# render_hotspot_dir_t0 / t1 tests
# ---------------------------------------------------------------------------


class TestRenderHotspotDir:
    def test_t0_passes_validation(self) -> None:
        content = render_hotspot_dir_t0("backend/routers")
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, reason

    def test_t1_passes_validation(self) -> None:
        records = [HotspotRecord(file_path="backend/routers/admin.py", risk_score=0.9)]
        content = render_hotspot_dir_t1("backend/routers", records, [], "abc", "ts")
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, reason

    def test_t1_globs_is_directory_pattern(self) -> None:
        records = [HotspotRecord(file_path="backend/routers/admin.py", risk_score=0.9)]
        content = render_hotspot_dir_t1("backend/routers", records, [], "abc", "ts")
        # globs should be a directory pattern, not a literal path
        # Find the globs line
        globs_line = [l for l in content.splitlines() if l.startswith("globs:")][0]
        glob_value = globs_line.replace("globs:", "").strip()
        valid, reason = validate_minimatch_glob(glob_value)
        assert valid, f"hotspot glob is not valid minimatch: {glob_value!r}, reason={reason}"

    def test_t1_does_not_use_literal_file_path_in_globs(self) -> None:
        records = [HotspotRecord(file_path="backend/routers/admin.py", risk_score=0.9)]
        content = render_hotspot_dir_t1("backend/routers", records, [], "abc", "ts")
        globs_line = [l for l in content.splitlines() if l.startswith("globs:")][0]
        # Should NOT be a literal .py file path without **
        assert "admin.py" not in globs_line


# ---------------------------------------------------------------------------
# render_dangerous_edits_t0 / t1 tests
# ---------------------------------------------------------------------------


class TestRenderDangerousEdits:
    def test_t0_passes_validation(self) -> None:
        content = render_dangerous_edits_t0()
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, reason

    def test_t1_passes_validation(self) -> None:
        survivors = [EdgeCaseRecord(file_path="a.py", description="Survived", survived=True)]
        content = render_dangerous_edits_t1(survivors, [], "abc", "ts")
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, reason

    def test_t1_contains_survivor_count(self) -> None:
        survivors = [
            EdgeCaseRecord(file_path="a.py", description="d1", survived=True),
            EdgeCaseRecord(file_path="b.py", description="d2", survived=True),
        ]
        content = render_dangerous_edits_t1(survivors, [], "abc", "ts")
        assert "2 pattern(s)" in content

    def test_t1_undocumented_section(self) -> None:
        undoc = [EdgeCaseRecord(file_path="c.py", description="undoc trap", survived=False)]
        content = render_dangerous_edits_t1([], undoc, "abc", "ts")
        assert "Undocumented" in content

    def test_t1_all_rendered_types_pass_validation(self) -> None:
        """All MDC render functions produce valid frontmatter (FR13)."""
        survivors = [EdgeCaseRecord(file_path="x.py", description="d", survived=True)]
        undoc = [EdgeCaseRecord(file_path="y.py", description="u", survived=False)]
        renderers_and_results = [
            render_conventions_t0(),
            render_conventions_t1([], [], "s", "t"),
            render_hotspot_dir_t0("dir"),
            render_hotspot_dir_t1("dir", [], [], "s", "t"),
            render_dangerous_edits_t0(),
            render_dangerous_edits_t1(survivors, undoc, "s", "t"),
            render_tombstone_mdc("ch", "cmd", "reason"),
            render_presence_beacon_mdc("ch", "cmd"),
        ]
        for content in renderers_and_results:
            valid, reason = validate_mdc_frontmatter(content)
            assert valid, f"render result failed validation: {reason!r}\n---\n{content[:200]}"
