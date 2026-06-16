"""PRD-CORE-190 wiring gate — seam registry + validate-time gate + CI expiry.

Behavioral tests (real parse -> real warning strings / result keys), not
existence checks. Covers FR01 (SeamEntry + parsing), FR02 (FR-field extraction
+ surface classification), FR03 (gate in warn/block mode + ValidateResultDict
key), and FR05 (backward-compat no-op).
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from trw_mcp.models.requirements import SeamEntry, ValidationFailure
from trw_mcp.state.prd_utils import parse_frontmatter
from trw_mcp.state.validation._prd_scoring_fr import _extract_fr_sections
from trw_mcp.state.validation._prd_scoring_wiring import (
    _classify_fr_surface,
    _extract_fr_wiring_fields,
    _fn_present_in_file,
    _unreachable_wiring_tests,
    _wiring_test_fn_name,
    _wiring_test_path,
    _wiring_test_resolves,
    check_wiring_gate,
    parse_seam_entries,
)
from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

_FIXTURES = Path(__file__).parent / "fixtures" / "seam_wiring"

# A fixed "today" so seam-expiry tests are deterministic regardless of run date.
_TODAY = date(2026, 6, 11)


def _read(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# FR01 — SeamEntry model + parsing
# ---------------------------------------------------------------------------


def test_seam_parse_valid() -> None:
    """A complete seam dict coerces into a SeamEntry."""
    entry = SeamEntry.model_validate(
        {
            "kind": "deferred",
            "target_prd": "PRD-TEST-999",
            "owner": "platform-team",
            "expiry_date": "2099-12-31",
        }
    )
    assert entry.kind == "deferred"
    assert entry.target_prd == "PRD-TEST-999"
    assert entry.expiry_date == "2099-12-31"


def test_seam_missing_field() -> None:
    """A seam missing a required field (owner) raises a validation error."""
    with pytest.raises(Exception):
        SeamEntry.model_validate({"kind": "deferred", "target_prd": "PRD-X", "expiry_date": "2099-01-01"})


def test_seam_bad_kind_rejected() -> None:
    """An out-of-enum kind value is rejected."""
    with pytest.raises(Exception):
        SeamEntry.model_validate(
            {
                "kind": "not-a-kind",
                "target_prd": "PRD-X",
                "owner": "team",
                "expiry_date": "2099-01-01",
            }
        )


def test_seam_malformed_date_rejected() -> None:
    """A non-ISO expiry_date is rejected by the field validator."""
    with pytest.raises(Exception):
        SeamEntry.model_validate(
            {
                "kind": "deferred",
                "target_prd": "PRD-X",
                "owner": "team",
                "expiry_date": "not-a-date",
            }
        )


def test_seam_parse_entries_from_frontmatter() -> None:
    """parse_seam_entries returns typed list with no warnings for a valid seam."""
    fm = parse_frontmatter(_read("prd_valid_seam.md"))
    seams, warnings = parse_seam_entries(fm)
    assert len(seams) == 1
    assert warnings == []
    assert seams[0].owner == "platform-team"


def test_seam_parse_invalid_entry_warns_not_crashes() -> None:
    """A malformed seam entry is skipped with a seam_schema_warning, not a crash."""
    fm = {
        "seams": [
            {"kind": "deferred", "target_prd": "PRD-A", "owner": "t", "expiry_date": "2099-01-01"},
            {"kind": "bad-kind", "target_prd": "PRD-B", "owner": "t", "expiry_date": "2099-01-01"},
        ]
    }
    seams, warnings = parse_seam_entries(fm)
    assert len(seams) == 1  # only the valid one survives
    assert any("seam_schema_warning" in w for w in warnings)
    assert any("entry 1" in w for w in warnings)


def test_seam_parse_empty_yields_no_warning() -> None:
    """A missing seams key is the common backward-compatible case: ([], [])."""
    assert parse_seam_entries({}) == ([], [])
    assert parse_seam_entries({"seams": []}) == ([], [])


# ---------------------------------------------------------------------------
# FR02 — FR-field extraction + surface classification
# ---------------------------------------------------------------------------


def test_extract_consumer_field() -> None:
    """A consumer: line is extracted (with symbol suffix)."""
    block = "### FR01\nsome prose\nconsumer: trw_mcp/tools/ceremony.py::trw_deliver\n"
    fields = _extract_fr_wiring_fields(block)
    assert fields["consumer"] == ["trw_mcp/tools/ceremony.py::trw_deliver"]


def test_extract_multiple_consumers_comma_split() -> None:
    """Comma-separated consumers on one line are split."""
    block = "### FR01\nconsumer: a.py, b.py::sym\n"
    fields = _extract_fr_wiring_fields(block)
    assert fields["consumer"] == ["a.py", "b.py::sym"]


def test_extract_wiring_test_field() -> None:
    """A wiring_test: line is extracted as a single value."""
    block = "### FR01\nwiring_test: tests/test_x.py::test_deliver_wired\n"
    fields = _extract_fr_wiring_fields(block)
    assert fields["wiring_test"] == ["tests/test_x.py::test_deliver_wired"]


def test_extract_ignores_unrelated_keys() -> None:
    """A non-wiring key line is ignored by the extractor."""
    block = "### FR01\nrandom_key: value\nconsumer: c.py\n"
    fields = _extract_fr_wiring_fields(block)
    assert "random_key" not in fields
    assert fields["consumer"] == ["c.py"]


def test_classify_surface_explicit_public() -> None:
    """surface: public is authoritative even when not Must-Have / non-public ip_tier."""
    block = "### FR01\nsurface: public\n"
    assert _classify_fr_surface(block, ip_tier="proprietary") is True


def test_classify_surface_explicit_internal_overrides_inference() -> None:
    """surface: internal exempts even a Must-Have public-ip-tier FR."""
    block = "### FR01\n**Priority**: Must Have\nsurface: internal\n"
    assert _classify_fr_surface(block, ip_tier="public") is False


def test_classify_surface_inference_must_have_public() -> None:
    """No surface line + Must-Have + ip_tier public -> inferred public surface."""
    block = "### FR01\n**Priority**: Must Have\nbody\n"
    assert _classify_fr_surface(block, ip_tier="public") is True


def test_classify_surface_inference_must_have_non_public_exempt() -> None:
    """Must-Have but ip_tier not public -> NOT a public surface."""
    block = "### FR01\n**Priority**: Must Have\n"
    assert _classify_fr_surface(block, ip_tier="proprietary") is False


def test_classify_surface_not_must_have_exempt() -> None:
    """No surface line + not Must-Have -> exempt regardless of ip_tier."""
    block = "### FR01\n**Priority**: Should Have\n"
    assert _classify_fr_surface(block, ip_tier="public") is False


# ---------------------------------------------------------------------------
# FR03 — wiring gate (warn + block)
# ---------------------------------------------------------------------------


def test_wiring_gate_warn_unwired_public_surface() -> None:
    """An unwired public surface with no seam emits a wiring_gate_warning (warn)."""
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — public unwired\n**Priority**: Must Have\nsurface: public\nbody\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn")
    assert failures == []
    assert any("wiring_gate_warning" in w and "FR01" in w for w in warnings)


def test_wiring_consumer_clears() -> None:
    """A consumer: field clears the wiring warning for that FR."""
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — wired\n**Priority**: Must Have\nsurface: public\n"
        "consumer: trw_mcp/tools/ceremony.py::trw_deliver\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn")
    assert not [w for w in warnings if "wiring_gate_warning" in w]
    assert failures == []


def test_wiring_test_clears() -> None:
    """A wiring_test: field clears the wiring warning for that FR."""
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — wired by test\n**Priority**: Must Have\nsurface: public\n"
        "wiring_test: tests/test_x.py::test_fr01_wired\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn")
    assert not [w for w in warnings if "wiring_gate_warning" in w]


def test_wiring_seam_clears() -> None:
    """A valid current seam entry suppresses the wiring warning (v1 mapping)."""
    content = _read("prd_valid_seam.md")
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn")
    assert not [w for w in warnings if "wiring_gate_warning" in w]
    assert failures == []


def test_wiring_gate_block_sets_failure() -> None:
    """Block mode produces a WIRING_GATE_FAIL ValidationFailure for the unwired FR."""
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — public unwired\n**Priority**: Must Have\nsurface: public\nbody\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="block")
    assert any(isinstance(f, ValidationFailure) and f.rule == "WIRING_GATE_FAIL" for f in failures)


def test_wiring_gate_one_seam_does_not_cover_other_unwired_frs() -> None:
    """F2 boundary: ONE valid seam suppresses ALL unwired public FRs (v1 mapping).

    Pins the deliberate v1 seam-to-FR mapping under-block: a PRD with a single
    valid seam entry and TWO genuinely-unwired public-surface FRs emits ZERO
    wiring warnings — the one seam suppresses both, even though it does not
    specifically cover the second FR. This is a documented governance tradeoff
    (no per-FR seam->FR mapping in v1), not a bug. The v2 upgrade keys seams to
    specific FRs; until then this test guards that the v1 behavior is intentional.
    """
    content = (
        "---\nprd:\n  id: PRD-X-902\n  category: CORE\nip_tier: public\nstubs: []\n"
        "seams:\n  - kind: deferred\n    target_prd: PRD-Y\n    owner: t\n"
        "    expiry_date: 2099-12-31\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — public unwired\n**Priority**: Must Have\nsurface: public\nbody\n\n"
        "### FR02 — also public unwired\n**Priority**: Must Have\nsurface: public\nbody\n"
    )
    fm = parse_frontmatter(content)
    # Sanity: both FRs ARE classified public + unwired, so without the seam they
    # would each warn (the seam is what suppresses them).
    sections = list(_extract_fr_sections(content))
    assert len(sections) == 2
    for _name, block in sections:
        assert _classify_fr_surface(block, ip_tier="public") is True

    warnings, failures = check_wiring_gate(content, fm, mode="warn", today=_TODAY)
    # v1: one seam suppresses BOTH unwired public FRs -> zero wiring warnings.
    assert not [w for w in warnings if "wiring_gate_warning" in w]
    assert failures == []


def test_wiring_gate_no_op_when_no_public_surface() -> None:
    """FR05: no public-surface FR -> gate is a no-op (empty warnings + failures)."""
    content = _read("prd_no_seams.md")
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn")
    assert warnings == []
    assert failures == []


# ---------------------------------------------------------------------------
# FR03 integration — validate_prd_quality_v2 surfaces the warnings + result key
# ---------------------------------------------------------------------------


def test_validate_v2_emits_wiring_warning_in_warn_mode() -> None:
    """validate_prd_quality_v2 (default warn) adds a wiring_gate_warning suggestion
    and keeps valid unchanged (never flips it in warn mode)."""
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — public unwired\n**Priority**: Must Have\nsurface: public\nbody\n"
    )
    result = validate_prd_quality_v2(content)
    msgs = [s.message for s in result.improvement_suggestions if s.dimension == "wiring"]
    assert any("wiring_gate_warning" in m for m in msgs)


def test_validate_v2_warn_mode_does_not_flip_valid() -> None:
    """Warn mode never adds a failure or flips valid (FR05 guarantee)."""
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — public unwired\n**Priority**: Must Have\nsurface: public\nbody\n"
    )
    result = validate_prd_quality_v2(content)
    assert not any(f.rule == "WIRING_GATE_FAIL" for f in result.failures)


def test_validate_v2_block_mode_flips_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block mode (config) adds WIRING_GATE_FAIL and sets valid False."""
    from trw_mcp.models.config import get_config

    cfg = get_config()
    cfg = cfg.model_copy(update={"wiring_gate_mode": "block"})
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — public unwired\n**Priority**: Must Have\nsurface: public\nbody\n"
    )
    result = validate_prd_quality_v2(content, config=cfg)
    assert result.valid is False
    assert any(f.rule == "WIRING_GATE_FAIL" for f in result.failures)


# ---------------------------------------------------------------------------
# Audit P1-1 — expired seams do NOT suppress the gate
# ---------------------------------------------------------------------------


def _expired_seam_fm() -> dict[str, object]:
    return {
        "seams": [
            {
                "kind": "deferred",
                "target_prd": "PRD-A",
                "owner": "t",
                "expiry_date": "2020-01-01",
            }
        ]
    }


def test_parse_seam_expired_is_excluded_and_warns() -> None:
    """An expired (schema-valid) seam is dropped from valid_seams + warns (P1-1)."""
    seams, warnings = parse_seam_entries(_expired_seam_fm(), today=_TODAY)
    assert seams == []  # expired seam does NOT count as coverage
    assert any("expired" in w and "entry 0" in w for w in warnings)
    # Overdue days are 2026-06-11 - 2020-01-01.
    overdue = (_TODAY - date(2020, 1, 1)).days
    assert any(f"{overdue}d ago" in w for w in warnings)


def test_expired_seam_does_not_suppress_wiring_warning() -> None:
    """The expired-seam fixture FR still emits a wiring_gate_warning (P1-1)."""
    content = _read("prd_expired_seam.md")
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn", today=_TODAY)
    assert any("wiring_gate_warning" in w and "FR01" in w for w in warnings)
    assert any("expired" in w for w in warnings)
    assert failures == []


def test_seam_expiry_today_boundary_is_still_valid() -> None:
    """A seam whose expiry_date == today is still valid (expired iff < today).

    Boundary semantics match scripts/check-seam-expiry.py EXACTLY (its gate is
    ``if expiry < today``), so expiry == today is current through end-of-day.
    """
    fm = {
        "seams": [
            {
                "kind": "deferred",
                "target_prd": "PRD-A",
                "owner": "t",
                "expiry_date": _TODAY.isoformat(),
            }
        ]
    }
    seams, warnings = parse_seam_entries(fm, today=_TODAY)
    assert len(seams) == 1  # equal-to-today is NOT expired
    assert not any("expired" in w for w in warnings)


def test_seam_one_day_past_is_expired() -> None:
    """expiry_date == today - 1 day IS expired (1d overdue)."""
    yesterday = date(2026, 6, 10)
    fm = {
        "seams": [
            {
                "kind": "deferred",
                "target_prd": "PRD-A",
                "owner": "t",
                "expiry_date": yesterday.isoformat(),
            }
        ]
    }
    seams, warnings = parse_seam_entries(fm, today=_TODAY)
    assert seams == []
    assert any("1d ago" in w for w in warnings)


def test_current_seam_with_expiry_still_suppresses() -> None:
    """A future-dated valid seam still suppresses the gate (regression guard)."""
    content = _read("prd_valid_seam.md")
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn", today=_TODAY)
    assert not [w for w in warnings if "wiring_gate_warning" in w]
    assert failures == []


# ---------------------------------------------------------------------------
# Audit P2-2 — case-insensitive field keys
# ---------------------------------------------------------------------------


def test_extract_consumer_field_case_insensitive_key() -> None:
    """A capitalized ``Consumer:`` key is still extracted (P2-2)."""
    block = "### FR01\nConsumer: trw_mcp/tools/ceremony.py::trw_deliver\n"
    fields = _extract_fr_wiring_fields(block)
    # Key buckets under canonical lowercase; VALUE case is preserved.
    assert fields["consumer"] == ["trw_mcp/tools/ceremony.py::trw_deliver"]


def test_classify_surface_case_insensitive_key() -> None:
    """``Surface: public`` (capital S) is recognized as authoritative (P2-2)."""
    block = "### FR01\nSurface: public\n"
    assert _classify_fr_surface(block, ip_tier="proprietary") is True


def test_capitalized_consumer_clears_wiring_warning() -> None:
    """A ``Consumer:`` line clears the wiring warning end-to-end (P2-2)."""
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — wired with capital key\n**Priority**: Must Have\nSurface: public\n"
        "Consumer: trw_mcp/tools/ceremony.py::trw_deliver\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn", today=_TODAY)
    assert not [w for w in warnings if "wiring_gate_warning" in w]


# ---------------------------------------------------------------------------
# Audit P1-2 — automated test for the CI script (check-seam-expiry.py)
# ---------------------------------------------------------------------------


def _load_check_seam_expiry() -> object:
    """Import scripts/check-seam-expiry.py via importlib (scripts/ isn't a package)."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "check-seam-expiry.py"
    spec = importlib.util.spec_from_file_location("check_seam_expiry", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_corpus_expired_seam_exits_one(tmp_path: Path) -> None:
    """check_corpus over a dir holding the expired-seam fixture exits 1 (P1-2)."""
    mod = _load_check_seam_expiry()
    (tmp_path / "prd_expired.md").write_text(_read("prd_expired_seam.md"), encoding="utf-8")
    exit_code = mod.check_corpus(tmp_path, today=_TODAY)  # type: ignore[attr-defined]
    assert exit_code == 1


def test_check_corpus_valid_seam_exits_zero(tmp_path: Path) -> None:
    """check_corpus over a dir holding the current-seam fixture exits 0 (P1-2)."""
    mod = _load_check_seam_expiry()
    (tmp_path / "prd_valid.md").write_text(_read("prd_valid_seam.md"), encoding="utf-8")
    exit_code = mod.check_corpus(tmp_path, today=_TODAY)  # type: ignore[attr-defined]
    assert exit_code == 0


def test_check_corpus_today_boundary_exits_zero(tmp_path: Path) -> None:
    """A seam expiring exactly today is NOT overdue -> exit 0 (boundary parity)."""
    mod = _load_check_seam_expiry()
    content = (
        "---\nprd:\n  id: PRD-TEST-906\nip_tier: public\nstubs: []\n"
        "seams:\n  - kind: deferred\n    target_prd: PRD-X\n    owner: t\n"
        f"    expiry_date: {_TODAY.isoformat()}\n---\n# body\n"
    )
    (tmp_path / "prd_boundary.md").write_text(content, encoding="utf-8")
    assert mod.check_corpus(tmp_path, today=_TODAY) == 0  # type: ignore[attr-defined]


def test_check_corpus_invalid_kind_is_advisory_not_failing(tmp_path: Path) -> None:
    """An invalid kind (but current expiry) does NOT change the exit code (P2-1)."""
    mod = _load_check_seam_expiry()
    (tmp_path / "prd_badkind.md").write_text(_read("prd_invalid_kind_seam.md"), encoding="utf-8")
    # Invalid kind is advisory; the future expiry keeps the exit code at 0.
    assert mod.check_corpus(tmp_path, today=_TODAY) == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Audit P2-1 — kind-validation parity between the two parsers
# ---------------------------------------------------------------------------


def test_kind_parity_invalid_rejected_by_both_parsers() -> None:
    """The invalid-kind fixture is rejected by BOTH the Pydantic and CI parsers.

    Pydantic side: parse_seam_entries drops the entry (SeamEntry Literal rejects
    the kind) and warns. CI side: check-seam-expiry's _ALLOWED_KINDS matches the
    same Literal set, so the kind is flagged invalid by both.
    """
    mod = _load_check_seam_expiry()
    content = _read("prd_invalid_kind_seam.md")
    fm = parse_frontmatter(content)

    # Pydantic parser: the bad-kind seam is not valid coverage.
    seams, warnings = parse_seam_entries(fm, today=_TODAY)
    assert seams == []
    assert any("seam_schema_warning" in w for w in warnings)

    # CI parser: the allowed-kind set mirrors the Pydantic Literal exactly.
    pydantic_kinds = set(SeamEntry.model_fields["kind"].annotation.__args__)  # type: ignore[union-attr]
    assert mod._ALLOWED_KINDS == frozenset(pydantic_kinds)  # type: ignore[attr-defined]
    assert "not-a-real-kind" not in mod._ALLOWED_KINDS  # type: ignore[attr-defined]


def test_kind_parity_valid_accepted_by_both_parsers() -> None:
    """The valid-seam fixture's kind is accepted by both parsers (parity)."""
    mod = _load_check_seam_expiry()
    fm = parse_frontmatter(_read("prd_valid_seam.md"))
    seams, _ = parse_seam_entries(fm, today=_TODAY)
    assert len(seams) == 1
    assert seams[0].kind in mod._ALLOWED_KINDS  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Audit P1-3 — MCP-tool-level test for wiring_gate_warnings
# ---------------------------------------------------------------------------


def test_trw_prd_validate_surfaces_wiring_gate_warnings(tmp_path: Path) -> None:
    """trw_prd_validate on an unwired-public fixture returns wiring_gate_warnings.

    The autouse ``_isolate_trw_dir`` fixture redirects ``resolve_project_root``
    to ``tmp_path``, and the tool rejects PRD paths outside the project root, so
    the fixture content is materialized under ``tmp_path`` before the call.
    """
    from tests.conftest import extract_tool_fn, make_test_server

    fn = extract_tool_fn(make_test_server("requirements"), "trw_prd_validate")
    prd = tmp_path / "PRD-TEST-904.md"
    prd.write_text(_read("prd_unwired_public.md"), encoding="utf-8")
    result = fn(prd_path=str(prd))
    assert "wiring_gate_warnings" in result
    warnings = result["wiring_gate_warnings"]
    assert any("wiring_gate_warning" in w and "FR01" in w for w in warnings)
    # FR05 guarantee surfaced through the tool: warn mode never adds the
    # wiring failure code (the minimal fixture may fail other structural
    # dimensions, but never WIRING_GATE_FAIL in default warn mode).
    assert not any(f["rule"] == "WIRING_GATE_FAIL" for f in result["failures"])


def test_trw_prd_validate_seam_suppresses_wiring_gate_warnings(tmp_path: Path) -> None:
    """A current seam fixture yields no wiring_gate_warnings through the tool."""
    from tests.conftest import extract_tool_fn, make_test_server

    fn = extract_tool_fn(make_test_server("requirements"), "trw_prd_validate")
    prd = tmp_path / "PRD-TEST-901.md"
    prd.write_text(_read("prd_valid_seam.md"), encoding="utf-8")
    result = fn(prd_path=str(prd))
    assert not [w for w in result["wiring_gate_warnings"] if "wiring_gate_warning" in w]


# ---------------------------------------------------------------------------
# PRD residual B2 — wiring_test reachability ("existence first")
# ---------------------------------------------------------------------------


def test_wiring_test_path_strips_nodeid_suffix() -> None:
    """The ::nodeid pytest selector is stripped to leave the bare path."""
    assert _wiring_test_path("tests/test_x.py::test_fr01_wired") == "tests/test_x.py"
    assert _wiring_test_path("tests/test_x.py") == "tests/test_x.py"
    assert _wiring_test_path('  "tests/test_x.py::test_a"  ') == "tests/test_x.py"


def test_wiring_test_resolves_existing_file(tmp_path: Path) -> None:
    """A declared wiring_test path that exists under the root resolves True."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a(): pass\n")
    assert _wiring_test_resolves(tmp_path, "tests/test_x.py::test_a") is True


def test_wiring_test_resolves_missing_file(tmp_path: Path) -> None:
    """A declared wiring_test path that does NOT exist resolves False."""
    assert _wiring_test_resolves(tmp_path, "tests/missing.py::test_a") is False


def test_wiring_test_resolves_rejects_path_escape(tmp_path: Path) -> None:
    """An absolute path or .. traversal escaping the root is never reachable."""
    outside = tmp_path.parent / "outside_secret.py"
    outside.write_text("x = 1\n")
    # Absolute path escape.
    assert _wiring_test_resolves(tmp_path, str(outside)) is False
    # Relative .. traversal escape — even though the file genuinely exists, it
    # is outside the project root so the escape guard rejects it.
    assert _wiring_test_resolves(tmp_path, "../outside_secret.py") is False


def test_unreachable_wiring_tests_declared_exists(tmp_path: Path) -> None:
    """A declared+existing wiring_test yields no unreachable findings."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a(): pass\n")
    block = "### FR01\nwiring_test: tests/test_x.py::test_a\n"
    assert _unreachable_wiring_tests(block, tmp_path) == []


def test_unreachable_wiring_tests_declared_missing_names_path(tmp_path: Path) -> None:
    """A declared+missing wiring_test surfaces the bare path token."""
    block = "### FR01\nwiring_test: tests/missing.py::test_a\n"
    missing = _unreachable_wiring_tests(block, tmp_path)
    assert missing == ["tests/missing.py"]


def test_wiring_gate_declared_missing_test_emits_warning(tmp_path: Path) -> None:
    """A public FR with a declared-but-missing wiring_test emits an advisory
    reachability wiring_gate_warning naming the path — never silently wired."""
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — declares a missing test\n**Priority**: Must Have\nsurface: public\n"
        "wiring_test: tests/does_not_exist.py::test_fr01\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn", project_root=tmp_path)
    assert any(
        "wiring_gate_warning" in w and "FR01" in w and "tests/does_not_exist.py" in w and "does not exist" in w
        for w in warnings
    )
    # Reachability is advisory even though FR01 "declares" a wiring_test, so it
    # is still considered wired for the no-wiring gate (no second "no consumer:/
    # wiring_test:" warning) — only the reachability finding appears.
    assert failures == []


def test_wiring_gate_declared_existing_test_no_warning(tmp_path: Path) -> None:
    """A public FR with a declared+existing wiring_test emits no warning."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_real.py").write_text("def test_fr01(): pass\n")
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — declares a real test\n**Priority**: Must Have\nsurface: public\n"
        "wiring_test: tests/test_real.py::test_fr01\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn", project_root=tmp_path)
    assert not [w for w in warnings if "wiring_gate_warning" in w]
    assert failures == []


def test_wiring_gate_reachability_advisory_even_in_block_mode(tmp_path: Path) -> None:
    """The reachability finding is advisory (a warning) even in block mode — a
    missing declared test is not a WIRING_GATE_FAIL failure."""
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — declares a missing test\n**Priority**: Must Have\nsurface: public\n"
        "wiring_test: tests/missing.py::test_fr01\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="block", project_root=tmp_path)
    assert any("tests/missing.py" in w and "does not exist" in w for w in warnings)
    assert not any(f.rule == "WIRING_GATE_FAIL" for f in failures)


def test_wiring_gate_no_project_root_skips_reachability() -> None:
    """When project_root is None the reachability check is skipped (original
    presence-only contract)."""
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — declares a missing test\n**Priority**: Must Have\nsurface: public\n"
        "wiring_test: tests/missing.py::test_fr01\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn")
    # No project_root -> no reachability finding; the declared wiring_test still
    # clears the no-wiring gate, so zero wiring warnings.
    assert not [w for w in warnings if "wiring_gate_warning" in w]
    assert failures == []


# ---------------------------------------------------------------------------
# PRD residual B2 — collection-grade: function-name presence check
# ---------------------------------------------------------------------------


def test_wiring_test_fn_name_extracts_leaf() -> None:
    """The ::nodeid leaf is extracted from a pytest nodeid (B2 collection-grade)."""
    assert _wiring_test_fn_name("tests/test_x.py::test_fr01_wired") == "test_fr01_wired"
    assert _wiring_test_fn_name("tests/test_x.py") == ""
    assert _wiring_test_fn_name('  "tests/test_x.py::test_a"  ') == "test_a"


def test_wiring_test_fn_name_class_scoped_returns_leaf() -> None:
    """Class-scoped nodeids (TestClass::test_method) return the leaf method name."""
    assert _wiring_test_fn_name("tests/test_x.py::TestClass::test_method") == "test_method"


def test_fn_present_in_file_finds_top_level_function(tmp_path: Path) -> None:
    """A top-level ``def fn_name(`` line is found by the static scanner."""
    f = tmp_path / "test_x.py"
    f.write_text("def test_fr01_wired():\n    pass\n", encoding="utf-8")
    assert _fn_present_in_file(f, "test_fr01_wired") is True


def test_fn_present_in_file_finds_method(tmp_path: Path) -> None:
    """An indented method (class body) is also found."""
    f = tmp_path / "test_x.py"
    f.write_text("class T:\n    def test_method(self):\n        pass\n", encoding="utf-8")
    assert _fn_present_in_file(f, "test_method") is True


def test_fn_present_in_file_missing_returns_false(tmp_path: Path) -> None:
    """A function not defined in the file returns False."""
    f = tmp_path / "test_x.py"
    f.write_text("def some_other_func():\n    pass\n", encoding="utf-8")
    assert _fn_present_in_file(f, "test_fr01_missing") is False


def test_fn_present_in_file_no_nodeid_returns_true(tmp_path: Path) -> None:
    """When fn_name is '' (no nodeid), the check is a no-op (returns True)."""
    f = tmp_path / "test_x.py"
    f.write_text("# empty\n", encoding="utf-8")
    assert _fn_present_in_file(f, "") is True


def test_unreachable_wiring_tests_fn_missing_emits_path_nodeid_token(
    tmp_path: Path,
) -> None:
    """File exists but declared function name is absent → token is path::fn (B2 next rung)."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_other_thing():\n    pass\n", encoding="utf-8")
    block = "### FR01\nwiring_test: tests/test_x.py::test_missing_fn\n"
    missing = _unreachable_wiring_tests(block, tmp_path)
    assert missing == ["tests/test_x.py::test_missing_fn"]


def test_unreachable_wiring_tests_fn_present_returns_empty(tmp_path: Path) -> None:
    """File exists and function name is present → no unreachable findings."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_real_fn():\n    pass\n", encoding="utf-8")
    block = "### FR01\nwiring_test: tests/test_x.py::test_real_fn\n"
    missing = _unreachable_wiring_tests(block, tmp_path)
    assert missing == []


def test_wiring_gate_fn_missing_emits_function_not_found_advisory(
    tmp_path: Path,
) -> None:
    """A public FR with file-exists but function-absent wiring_test emits an
    advisory warning naming the missing function (B2 collection-grade rung).

    The file exists so the file-existence check passes, but the declared
    function name (test_fr01_does_not_exist) is not defined in the file.
    The gate must emit a function-not-found advisory, NOT the 'does not exist'
    path advisory, and NOT a WIRING_GATE_FAIL (always advisory).
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_real.py").write_text("def test_some_other_fn():\n    pass\n", encoding="utf-8")
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — file exists, function missing\n**Priority**: Must Have\nsurface: public\n"
        "wiring_test: tests/test_real.py::test_fr01_does_not_exist\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn", project_root=tmp_path)
    assert any(
        "wiring_gate_warning" in w and "FR01" in w and "test_fr01_does_not_exist" in w and "not found" in w
        for w in warnings
    ), f"Expected function-not-found advisory, got: {warnings}"
    # The reachability advisory is never a hard failure.
    assert not any(f.rule == "WIRING_GATE_FAIL" for f in failures)


def test_wiring_gate_fn_present_no_warning(tmp_path: Path) -> None:
    """File exists and function name is present → no wiring advisory at all."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_real.py").write_text("def test_fr01_wired():\n    pass\n", encoding="utf-8")
    content = (
        "---\nprd:\n  id: PRD-X-001\n  category: CORE\nip_tier: public\nstubs: []\n---\n"
        "## 3. Functional Requirements\n\n"
        "### FR01 — file exists, function present\n**Priority**: Must Have\nsurface: public\n"
        "wiring_test: tests/test_real.py::test_fr01_wired\n"
    )
    fm = parse_frontmatter(content)
    warnings, failures = check_wiring_gate(content, fm, mode="warn", project_root=tmp_path)
    assert not [w for w in warnings if "wiring_gate_warning" in w]
    assert failures == []


def test_seam_malformed_int_kind_rejected() -> None:
    """kind=123 (int, not str) is rejected with a clear validation error.

    Pydantic v2: an int where a Literal[str] is declared fails the union
    check and surfaces a ValidationError — never silently coerced to a
    string or passed through to the wiring gate.
    """
    with pytest.raises(Exception):
        from trw_mcp.models.requirements import SeamEntry

        SeamEntry.model_validate(
            {
                "kind": 123,
                "target_prd": "PRD-X",
                "owner": "team",
                "expiry_date": "2099-01-01",
            }
        )


def test_seam_malformed_null_owner_rejected() -> None:
    """owner=null is rejected with a ValidationError (min_length=1 required)."""
    with pytest.raises(Exception):
        from trw_mcp.models.requirements import SeamEntry

        SeamEntry.model_validate(
            {
                "kind": "deferred",
                "target_prd": "PRD-X",
                "owner": None,
                "expiry_date": "2099-01-01",
            }
        )


def test_seam_malformed_garbage_expiry_rejected() -> None:
    """A garbage expiry value is rejected — never crashes the gate silently."""
    with pytest.raises(Exception):
        from trw_mcp.models.requirements import SeamEntry

        SeamEntry.model_validate(
            {
                "kind": "deferred",
                "target_prd": "PRD-X",
                "owner": "team",
                "expiry_date": "not-a-real-date-at-all-garbage",
            }
        )


def test_parse_seam_entries_malformed_int_kind_warns_not_crashes() -> None:
    """parse_seam_entries with int kind gracefully skips with seam_schema_warning."""
    fm = {"seams": [{"kind": 123, "target_prd": "PRD-X", "owner": "team", "expiry_date": "2099-01-01"}]}
    seams, warnings = parse_seam_entries(fm)
    assert seams == []
    assert any("seam_schema_warning" in w for w in warnings)


def test_parse_seam_entries_null_owner_warns_not_crashes() -> None:
    """parse_seam_entries with owner=null gracefully skips with seam_schema_warning."""
    fm = {"seams": [{"kind": "deferred", "target_prd": "PRD-X", "owner": None, "expiry_date": "2099-01-01"}]}
    seams, warnings = parse_seam_entries(fm)
    assert seams == []
    assert any("seam_schema_warning" in w for w in warnings)
