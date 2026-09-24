"""Coverage for ``scripts/probe-mcp.py`` — the read-only MCP contract probe (PRD-INFRA-184).

The probe is a harness, so its own defects are invisible unless they are tested:
a probe that silently passes, that hardcodes the surface it claims to derive, or
that writes into the live store is worse than no probe at all. These tests pin
the four properties that make its verdicts trustworthy:

1. the fixture really disables egress and carries no credentials;
2. the expected tool surface is DERIVED from the server's disclosure config
   (it tracks ``tool_resolution_mode`` instead of a frozen list);
3. an unverifiable contract is ``inconclusive``, never a pass, and the exit code
   follows the contract verdicts;
4. a live probe run stays inside its fixture — nothing lands in the repo store.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests._layout import MONOREPO_ROOT, requires_monorepo

#: The probe is a monorepo development harness; it is not part of the published package.
pytestmark = requires_monorepo
REPO_ROOT = MONOREPO_ROOT if MONOREPO_ROOT is not None else Path(__file__).resolve().parents[2]
PROBE_PATH = REPO_ROOT / "scripts" / "probe-mcp.py"


def _load_probe() -> ModuleType:
    """Import ``scripts/probe-mcp.py`` by path (its name is not importable)."""
    spec = importlib.util.spec_from_file_location("trw_probe_mcp_script", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe_mod() -> ModuleType:
    """The loaded probe module."""
    return _load_probe()


# ---------------------------------------------------------------------------
# Fixture containment
# ---------------------------------------------------------------------------


def test_probe_script_exists_and_is_executable() -> None:
    """The probe ships as an executable script at the documented path."""
    assert PROBE_PATH.is_file()
    assert os.access(PROBE_PATH, os.X_OK)


def test_fixture_config_disables_egress_and_carries_no_credentials(probe_mod: ModuleType, tmp_path: Path) -> None:
    """The disposable fixture turns telemetry, sync and embeddings off."""
    root = probe_mod.build_fixture(tmp_path / "fixture")
    text = (root / ".trw" / "config.yaml").read_text(encoding="utf-8")

    assert "platform_telemetry_enabled: false" in text
    assert "team_sync_enabled: false" in text
    assert "embeddings_enabled: false" in text
    # A credential in the fixture would let a probe run reach the backend.
    assert "platform_api_key" not in text
    assert "token" not in text.lower()


def test_server_environment_is_an_allowlist_not_a_name_scrub(probe_mod: ModuleType, monkeypatch, tmp_path) -> None:
    """Nothing is inherited except the passthrough names — values cannot smuggle secrets.

    A scrub keyed on variable NAMES passes ``HTTP_PROXY=http://user:pw@host``
    straight through; only an allowlist stops it (Codex audit 2026-09-17, P0-2).
    """
    monkeypatch.setenv("TRW_PLATFORM_API_KEY", "super-secret")
    monkeypatch.setenv("TRW_USER_DIR", "/somewhere/else")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_live")
    monkeypatch.setenv("HTTP_PROXY", "http://user:planted-password@proxy:8080")
    monkeypatch.setenv("DATABASE_URL", "postgres://user:planted-password@host/db")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    env = probe_mod.server_environment(tmp_path, "probe-session")

    assert "TRW_PLATFORM_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "HTTP_PROXY" not in env
    assert "DATABASE_URL" not in env
    assert "planted-password" not in json.dumps(env)
    # The surviving names are exactly the passthrough set plus what the probe sets.
    probe_set = {"TRW_PROJECT_ROOT", "TRW_META_TUNE_ENABLED", "TRW_SESSION_ID"}
    assert set(env) <= set(probe_mod._ENV_PASSTHROUGH) | probe_set | set(probe_mod.fixture_home(tmp_path))
    assert env["TRW_PROJECT_ROOT"] == str(tmp_path)
    assert env["TRW_META_TUNE_ENABLED"] == "false"
    assert env["TRW_SESSION_ID"] == "probe-session"
    # PATH must survive or the server cannot be spawned at all.
    assert env.get("PATH")


def test_server_environment_redirects_the_user_tier_into_the_fixture(
    probe_mod: ModuleType, monkeypatch, tmp_path
) -> None:
    """HOME/XDG/TRW_USER_DIR point inside the fixture, so the user store is disposable.

    ``resolve_user_memory_dir`` reads TRW_USER_DIR > XDG_DATA_HOME > ~/.trw, and
    recall federates the user tier by default: a fixture-scoped project root alone
    leaves a live store reachable (Codex audit 2026-09-17, P0-1).
    """
    monkeypatch.setenv("HOME", str(REPO_ROOT))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("TRW_USER_DIR", raising=False)

    env = probe_mod.server_environment(tmp_path, "probe-session")

    for name in ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TRW_USER_DIR"):
        assert env[name].startswith(str(tmp_path)), name
        assert Path(env[name]).is_dir()
    assert not env["HOME"].startswith(str(REPO_ROOT) + "/.trw")


def test_protected_output_path_refuses_state_dirs_and_databases(probe_mod: ModuleType, tmp_path: Path) -> None:
    """A report destination can never truncate a live store (Codex audit P0-3)."""
    trw_target = tmp_path / ".trw" / "memory" / "memory.db"
    trw_target.parent.mkdir(parents=True)
    assert "refusing to write inside a .trw" in probe_mod.protected_output_path(trw_target)

    db = tmp_path / "elsewhere.db"
    db.write_bytes(b"SQLite format 3\x00rest-of-a-real-database")
    assert "refusing to overwrite an existing SQLite database" in probe_mod.protected_output_path(db)

    assert probe_mod.protected_output_path(tmp_path / "report.json") == ""
    ordinary = tmp_path / "existing.json"
    ordinary.write_text("{}", encoding="utf-8")
    assert probe_mod.protected_output_path(ordinary) == ""


def test_main_refuses_a_protected_destination_before_spawning(probe_mod: ModuleType, tmp_path: Path) -> None:
    """The refusal happens before any server spawn, and the file is untouched."""
    target = tmp_path / ".trw" / "memory" / "memory.db"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"SQLite format 3\x00original")
    launcher = REPO_ROOT / ".venv" / "bin" / "trw-mcp"
    if not launcher.is_file():
        pytest.skip("repo .venv/bin/trw-mcp launcher is not installed")

    assert probe_mod.main(["--server", str(launcher), "--json", str(target), "--quiet"]) == 2
    assert target.read_bytes() == b"SQLite format 3\x00original"


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "0", "-5", "100000", "abc"])
def test_finite_timeout_rejects_unbounded_and_nonsense_values(probe_mod: ModuleType, raw: str) -> None:
    """``--timeout inf`` parsed fine and made the request loop wait forever (P0-5)."""
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        probe_mod._finite_timeout(raw)


def test_finite_timeout_accepts_a_bounded_positive_value(probe_mod: ModuleType) -> None:
    """A normal timeout still parses."""
    assert probe_mod._finite_timeout("30.5") == 30.5
    assert probe_mod._finite_timeout(str(probe_mod.MAX_TIMEOUT_S)) == probe_mod.MAX_TIMEOUT_S


def test_stdio_client_request_gives_up_on_a_silent_server(probe_mod: ModuleType, tmp_path: Path) -> None:
    """A live-but-silent child raises rather than hanging the run."""
    client = probe_mod.StdioClient(
        [sys.executable, "-c", "import sys, time; sys.stdin.read(1); time.sleep(30)"],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "")},
        timeout=1.0,
    )
    try:
        with pytest.raises(probe_mod.ProbeError) as excinfo:
            client.request("initialize", {})
        assert "timeout after 1.0s" in str(excinfo.value)
    finally:
        client.close()


def test_corpus_count_returns_zero_for_an_absent_store(probe_mod: ModuleType, tmp_path: Path) -> None:
    """A fixture with no store yet counts zero rather than raising."""
    assert probe_mod.corpus_count(tmp_path / ".trw") == 0


# ---------------------------------------------------------------------------
# Expected surface is derived, not hardcoded
# ---------------------------------------------------------------------------


def test_expected_tool_surface_is_derived_from_the_disclosure_config(probe_mod: ModuleType) -> None:
    """The expected set equals the server's own resolution, bounded by eligibility."""
    from trw_mcp.middleware.surface_authority import _BOOTSTRAP_TOOLS
    from trw_mcp.models.phase_policy import RIGID_TOOLS
    from trw_mcp.server._surface_manifest_registry import eligible_tool_names, resolve_tool_surface

    surface, inputs = probe_mod.expected_tool_surface()
    eligible = set(eligible_tool_names())

    assert surface, "a vacuous expected set would make the contract unfalsifiable"
    assert surface <= eligible
    assert set(RIGID_TOOLS) <= surface
    assert set(_BOOTSTRAP_TOOLS) <= surface
    assert inputs["task_type"] is None
    assert inputs["eligible_tool_count"] == len(eligible)

    if inputs["tool_resolution_mode"] == "standard" and not inputs["phase_exposure_enabled"]:
        bounded = set(resolve_tool_surface(None, "standard", comms_enabled=bool(inputs["comms_enabled"])).tools)
        assert surface == (bounded | set(RIGID_TOOLS) | set(_BOOTSTRAP_TOOLS)) & eligible
        # Boundedness is the point: a masked tool must NOT be in the expected set,
        # or the probe would never notice the disclosure layer disappearing.
        assert "trw_pipeline_health" not in surface


def test_expected_tool_surface_tracks_tool_resolution_mode_all(probe_mod: ModuleType, monkeypatch) -> None:
    """Flipping the operator escape to ``all`` widens the expected set."""
    from trw_mcp.models import config as config_module
    from trw_mcp.server._surface_manifest_registry import eligible_tool_names

    real = config_module.get_config()

    class _AllMode:
        tool_resolution_mode = "all"
        comms_enabled = False
        phase_exposure_enabled = False
        pipeline_health_gate_graph_min_corpus = getattr(real, "pipeline_health_gate_graph_min_corpus", 10)

    monkeypatch.setattr(config_module, "get_config", lambda: _AllMode())

    surface, inputs = probe_mod.expected_tool_surface()

    assert inputs["tool_resolution_mode"] == "all"
    assert surface == set(eligible_tool_names())
    assert "trw_pipeline_health" in surface


def test_expected_tool_surface_narrows_under_phase_exposure(probe_mod: ModuleType, monkeypatch) -> None:
    """With phase exposure on, the expected set is bounded by the phase policy."""
    from trw_mcp.models import config as config_module
    from trw_mcp.models.phase_policy import DEFAULT_PHASE_POLICY, RIGID_TOOLS

    class _PhaseGated:
        tool_resolution_mode = "standard"
        comms_enabled = False
        phase_exposure_enabled = True
        pipeline_health_gate_graph_min_corpus = 10

    monkeypatch.setattr(config_module, "get_config", lambda: _PhaseGated())

    surface, inputs = probe_mod.expected_tool_surface()

    assert inputs["phase_exposure_enabled"] is True
    assert surface <= set(DEFAULT_PHASE_POLICY.list_for("RESEARCH")) | set(RIGID_TOOLS)


def test_gate_graph_min_corpus_reads_the_live_config(probe_mod: ModuleType, monkeypatch) -> None:
    """The seed size follows the gate's own threshold field."""
    from trw_mcp.models import config as config_module

    class _Cfg:
        pipeline_health_gate_graph_min_corpus = 37

    monkeypatch.setattr(config_module, "get_config", lambda: _Cfg())
    assert probe_mod.gate_graph_min_corpus() == 37


# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------


def test_rpc_result_raises_on_a_jsonrpc_error(probe_mod: ModuleType) -> None:
    """A JSON-RPC error is a probe failure, never an empty result."""
    with pytest.raises(probe_mod.ProbeError) as excinfo:
        probe_mod.rpc_result({"error": {"code": -32601, "message": "nope"}}, "tools/list")
    assert "tools/list" in str(excinfo.value)


def test_rpc_result_raises_on_a_non_object_result(probe_mod: ModuleType) -> None:
    """A non-object result cannot be interpreted and must not be swallowed."""
    with pytest.raises(probe_mod.ProbeError):
        probe_mod.rpc_result({"result": 7}, "tools/list")


def test_tool_payload_prefers_structured_content(probe_mod: ModuleType) -> None:
    """``structuredContent`` wins, and a single ``result`` wrapper is unwrapped."""
    assert probe_mod.tool_payload({"structuredContent": {"result": {"a": 1}}}) == {"a": 1}
    assert probe_mod.tool_payload({"structuredContent": {"a": 1}}) == {"a": 1}


def test_tool_payload_falls_back_to_text_content(probe_mod: ModuleType) -> None:
    """A JSON text block parses; non-JSON text is preserved, not dropped."""
    assert probe_mod.tool_payload({"content": [{"type": "text", "text": '{"b": 2}'}]}) == {"b": 2}
    assert probe_mod.tool_payload({"content": [{"type": "text", "text": "boom"}]}) == {"text": "boom"}
    assert probe_mod.tool_payload({}) == {}


def test_graph_dead_claim_detection(probe_mod: ModuleType) -> None:
    """The graph claim is read out of the warning's reasons, not its prose."""
    warned = {"pipeline_health_warning": {"reasons": ["knowledge graph dead: 0 edges for 12 memories"]}}
    assert probe_mod._graph_dead_claimed(warned) is True
    assert probe_mod._graph_dead_claimed({"pipeline_health_warning": {"reasons": ["push staleness: ..."]}}) is False
    assert probe_mod._graph_dead_claimed({}) is False
    assert probe_mod._graph_dead_claimed({"pipeline_health_warning": "degraded"}) is False


def test_catalogue_contract_rejects_rows_with_no_identifier(probe_mod: ModuleType) -> None:
    """``resources: [{}]`` used to satisfy "at least one resource" (Codex audit P0-4)."""
    names, contract = probe_mod.catalogue_contract(
        "resources_list", {"resources": [{}]}, list_key="resources", id_field="uri", require_nonempty=True
    )
    assert names == []
    assert contract.status == "fail"


def test_catalogue_contract_rejects_a_missing_list_key(probe_mod: ModuleType) -> None:
    """An empty result object is malformed, not "answered fine"."""
    _names, contract = probe_mod.catalogue_contract(
        "prompts_list", {}, list_key="prompts", id_field="name", require_nonempty=False
    )
    assert contract.status == "fail"
    assert contract.observed == "NoneType"


def test_catalogue_contract_accepts_a_legitimately_empty_optional_list(probe_mod: ModuleType) -> None:
    """A server with no prompts is normal; a server with no resources is not."""
    _names, prompts = probe_mod.catalogue_contract(
        "prompts_list", {"prompts": []}, list_key="prompts", id_field="name", require_nonempty=False
    )
    assert prompts.status == "pass"

    _uris, resources = probe_mod.catalogue_contract(
        "resources_list", {"resources": []}, list_key="resources", id_field="uri", require_nonempty=True
    )
    assert resources.status == "fail"


def test_catalogue_contract_returns_sorted_identifiers(probe_mod: ModuleType) -> None:
    """Well-formed rows yield their sorted identifiers."""
    uris, contract = probe_mod.catalogue_contract(
        "resources_list",
        {"resources": [{"uri": "trw://b"}, {"uri": "trw://a"}]},
        list_key="resources",
        id_field="uri",
        require_nonempty=True,
    )
    assert uris == ["trw://a", "trw://b"]
    assert contract.status == "pass"


def _graph(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"degraded": False, "corpus_count": 17, "edge_count": 0}
    base.update(overrides)
    return base


def test_graph_agreement_passes_when_both_surfaces_agree(probe_mod: ModuleType) -> None:
    """Agreement on one corpus is the contract."""
    contract = probe_mod.graph_agreement_contract(
        session_start={"pipeline_health_warning": {"reasons": ["knowledge graph dead: 0 edges"]}},
        session_start_ok=True,
        graph=_graph(degraded=True),
        min_corpus=10,
    )
    assert contract.status == "pass"


def test_graph_agreement_fails_when_the_two_surfaces_disagree(probe_mod: ModuleType) -> None:
    """The 2026-09-16 finding: session_start says dead, pipeline_health says fine."""
    contract = probe_mod.graph_agreement_contract(
        session_start={"pipeline_health_warning": {"reasons": ["knowledge graph dead: 0 edges for 17 memories"]}},
        session_start_ok=True,
        graph=_graph(degraded=False),
        min_corpus=10,
    )
    assert contract.status == "fail"
    assert "claims dead=True" in str(contract.observed)


def test_graph_agreement_passes_when_neither_surface_flags_the_graph(probe_mod: ModuleType) -> None:
    """A healthy corpus with no warning and degraded=False agrees."""
    contract = probe_mod.graph_agreement_contract(
        session_start={"summary": "fine"},
        session_start_ok=True,
        graph=_graph(degraded=False),
        min_corpus=10,
    )
    assert contract.status == "pass"


@pytest.mark.parametrize(
    ("kwargs", "marker"),
    [
        ({"session_start_ok": False}, "did not succeed"),
        ({"graph": _graph(degraded=None)}, "no boolean graph_edges.degraded"),
        ({"graph": {"degraded": False}}, "no integer graph_edges.corpus_count"),
        ({"graph": _graph(corpus_count=10)}, "does not exceed the gate minimum"),
    ],
)
def test_graph_agreement_refuses_to_claim_agreement_without_evidence(
    probe_mod: ModuleType, kwargs: dict[str, object], marker: str
) -> None:
    """Missing evidence is inconclusive, never a pass.

    ``bool(graph.get("degraded"))`` turned a MISSING key into ``False`` and
    compared it against another ``False``, reporting 12/12 PASS on an empty
    payload (Codex audit 2026-09-17, P0-4).
    """
    call: dict[str, object] = {
        "session_start": {},
        "session_start_ok": True,
        "graph": _graph(),
        "min_corpus": 10,
    }
    call.update(kwargs)
    contract = probe_mod.graph_agreement_contract(**call)

    assert contract.status == "inconclusive"
    assert contract.passed is False
    assert marker in contract.detail


# ---------------------------------------------------------------------------
# Verdict accounting
# ---------------------------------------------------------------------------


def test_inconclusive_is_not_a_pass(probe_mod: ModuleType) -> None:
    """An unverifiable contract must not be counted as satisfied."""
    report = probe_mod.Report(generated_at="now")
    report.record("ok", ok=True, detail="fine")
    report.add(probe_mod.Contract(name="unknown", status="inconclusive", detail="vacuous corpus"))

    payload = report.to_dict()
    assert payload["summary"] == {"total": 2, "passed": 1, "failed": 1, "all_passed": False}
    assert [c.name for c in report.failures] == ["unknown"]


def test_render_human_explains_only_the_failures(probe_mod: ModuleType) -> None:
    """The human summary carries expected/observed for anything not passing."""
    report = probe_mod.Report(generated_at="now")
    report.record("good", ok=True, detail="fine", expected=1, observed=1)
    report.record("bad", ok=False, detail="version leak", expected="3.0.0", observed="3.4.7")

    text = probe_mod.render_human(report)

    assert "PASS" in text and "FAIL" in text
    assert "version leak" in text
    assert "'3.4.7'" in text
    assert "1/2 contracts passed" in text


def test_golden_view_is_status_only(probe_mod: ModuleType) -> None:
    """The golden records verdicts, not volatile versions or counts."""
    report = probe_mod.Report(generated_at="now")
    report.record("b", ok=True, detail="", expected="3.0.0", observed="3.0.0")
    report.record("a", ok=False, detail="", expected=1, observed=2)

    view = probe_mod.golden_view(report)

    assert view == {"a": "fail", "b": "pass"}
    assert list(view) == ["a", "b"], "sorted so the golden diff is stable"


def test_apply_golden_writes_then_detects_drift(probe_mod: ModuleType, tmp_path: Path) -> None:
    """A missing golden is created; a changed verdict is reported as drift."""
    golden = tmp_path / "golden.json"
    report = probe_mod.Report(generated_at="now")
    report.record("server_version", ok=False, detail="")

    ok, notes = probe_mod.apply_golden(report, golden, update=False)
    assert ok is True
    assert golden.exists()
    assert json.loads(golden.read_text())["contracts"] == {"server_version": "fail"}
    assert any("golden written" in note for note in notes)

    ok_again, notes_again = probe_mod.apply_golden(report, golden, update=False)
    assert ok_again is True and notes_again == []

    fixed = probe_mod.Report(generated_at="now")
    fixed.record("server_version", ok=True, detail="")
    drifted, diffs = probe_mod.apply_golden(fixed, golden, update=False)
    assert drifted is False
    assert diffs == ["server_version: golden=fail observed=pass"]

    updated, _ = probe_mod.apply_golden(fixed, golden, update=True)
    assert updated is True
    assert json.loads(golden.read_text())["contracts"] == {"server_version": "pass"}


def test_apply_golden_reports_a_contract_that_disappeared(probe_mod: ModuleType, tmp_path: Path) -> None:
    """A contract present in the golden but missing from the run is drift."""
    golden = tmp_path / "golden.json"
    golden.write_text(json.dumps({"contracts": {"server_version": "fail", "retired": "pass"}}), encoding="utf-8")
    report = probe_mod.Report(generated_at="now")
    report.record("server_version", ok=False, detail="")

    ok, diffs = probe_mod.apply_golden(report, golden, update=False)

    assert ok is False
    assert diffs == ["retired: golden=pass observed=<absent>"]


def test_main_refuses_a_missing_server_executable(probe_mod: ModuleType, tmp_path: Path) -> None:
    """A missing launcher exits 2 (harness error), distinct from a contract failure."""
    assert probe_mod.main(["--server", str(tmp_path / "nope"), "--quiet"]) == 2


# ---------------------------------------------------------------------------
# Live probe (spawns the real stdio server)
# ---------------------------------------------------------------------------


def _repo_store_rows_matching(pattern: str) -> int:
    db = REPO_ROOT / ".trw" / "memory" / "memory.db"
    if not db.is_file():
        return 0
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10.0) as conn:
        row = conn.execute("SELECT COUNT(*) FROM memories WHERE content LIKE ?", (pattern,)).fetchone()
    return int(row[0]) if row else 0


@pytest.mark.slow
@pytest.mark.xfail(
    strict=True,
    reason="scripts/probe-mcp.py seeds an unpinned fixture that the 6.0.0 daemon-only store refuses; "
    "fixture-daemon port in 6.0.1",
)
def test_live_probe_reports_every_contract_and_stays_inside_its_fixture(tmp_path: Path) -> None:
    """End-to-end: the probe spawns the real server, reports, and contaminates nothing.

    Marked ``slow`` (additive to this file's default ``integration`` marker) so
    it never runs in the unit tier: it spawns a real ``trw-mcp`` process.
    """
    launcher = REPO_ROOT / ".venv" / "bin" / "trw-mcp"
    if not launcher.is_file():
        pytest.skip("repo .venv/bin/trw-mcp launcher is not installed")

    before = _repo_store_rows_matching("probe fixture seed%")
    report_path = tmp_path / "report.json"
    golden_path = tmp_path / "golden.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--json",
            str(report_path),
            "--golden",
            str(golden_path),
            "--quiet",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode in (0, 1), f"probe harness error:\n{completed.stderr[-4000:]}"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    names = {c["name"] for c in report["contracts"]}
    # EXACT set, not a subset: a subset check let a deleted contract pass
    # unnoticed, including the graph contract itself (Codex audit P1-7).
    assert names == {
        "server_name",
        "server_version",
        "exposed_tool_set",
        "resources_list",
        "prompts_list",
        "session_start_ok",
        "init_ok",
        "status_ok",
        "recall_ok",
        "tool_access_grant",
        "pipeline_health_callable",
        "graph_verdict_agreement",
    }
    assert report["schema"] == "trw-probe-mcp/1"
    assert report["environment"]["server_info"]["name"] == "trw"
    assert report["environment"]["exposed_tools"], "the probe must observe a live tool list"
    assert report["environment"]["fixture_root"].startswith(("/tmp", "/private/var", "/var"))

    # Exit status follows the verdicts — a probe that always exits 0 proves nothing.
    assert (completed.returncode == 0) == report["summary"]["all_passed"]

    # Containment, empirical half: the seeded corpus went to the fixture store.
    assert _repo_store_rows_matching("probe fixture seed%") == before
    assert report["environment"]["fixture_corpus_rows"] >= report["environment"]["seeded_learnings"]
    # Containment, structural half: every configured store path — project tier AND
    # user tier — resolves inside the fixture, so no live store is even addressable.
    fixture_root = report["environment"]["fixture_root"]
    for name, value in report["environment"]["fixture_home"].items():
        assert value.startswith(fixture_root), name

    # The golden was created by this run and matches the observed verdicts.
    golden = json.loads(golden_path.read_text(encoding="utf-8"))["contracts"]
    assert golden == {c["name"]: c["status"] for c in report["contracts"]}


@pytest.mark.slow
def test_live_probe_with_no_seed_reports_the_graph_contract_inconclusive(tmp_path: Path) -> None:
    """``--seed 0`` leaves the corpus below the gate minimum, so agreement is unprovable.

    Exercises the inconclusive path through real orchestration rather than a
    hand-built Report, and pins that an unprovable contract still fails the run.
    """
    launcher = REPO_ROOT / ".venv" / "bin" / "trw-mcp"
    if not launcher.is_file():
        pytest.skip("repo .venv/bin/trw-mcp launcher is not installed")

    report_path = tmp_path / "report.json"
    completed = subprocess.run(
        [sys.executable, str(PROBE_PATH), "--seed", "0", "--json", str(report_path), "--quiet"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 1, f"expected a contract-level failure:\n{completed.stderr[-4000:]}"

    report = json.loads(report_path.read_text(encoding="utf-8"))
    graph = next(c for c in report["contracts"] if c["name"] == "graph_verdict_agreement")
    assert graph["status"] == "inconclusive"
    assert report["summary"]["all_passed"] is False
    assert report["environment"]["seeded_learnings"] == 0
