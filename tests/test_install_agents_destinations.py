"""PRD-CORE-252-FR03/FR04 + NFR02/NFR04: where agents land, and what happens when they cannot.

These exercise ``_install_agents`` and ``update_project`` on the real path — the
same functions ``init-project`` calls — because the defect being closed was
precisely that the production call site never reached the client-parameterised
code underneath it.
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo
from trw_mcp.agents.agent_formats import agent_format_for
from trw_mcp.agents.tier_resolver import KNOWN_CLIENTS, materialize_agent
from trw_mcp.bootstrap._init_project_skills import _install_agents

REPO_ROOT = MONOREPO_ROOT or PACKAGE_ROOT
BUNDLED_AGENTS_DIR = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "agents"
BOOTSTRAP_DIR = PACKAGE_ROOT / "src" / "trw_mcp" / "bootstrap"

AGENT_CAPABLE_CLIENTS = sorted(c for c in KNOWN_CLIENTS if agent_format_for(c).supports_agents)


def _empty_result() -> dict[str, list[str]]:
    return {"created": [], "skipped": [], "errors": []}


def _bundled_stems() -> list[str]:
    stems = sorted(path.stem for path in BUNDLED_AGENTS_DIR.glob("*.md"))
    assert stems, "no bundled agents found; this test has stopped testing anything"
    return stems


def _channel_owned_agent_names(candidates: set[str]) -> set[str]:
    """Of *candidates*, the stems the distill CHANNEL installers own.

    Derived by asking the owning subsystem's own source, rather than named
    here: a channel that adds or renames its agent cannot turn this test's
    exclusion set into a lie, and a stem that no channel mentions cannot hide
    behind it.
    """
    import trw_mcp.channels as channels_pkg

    channel_source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(Path(channels_pkg.__path__[0]).rglob("*.py"))
    )
    return {stem for stem in candidates if stem in channel_source}


@pytest.mark.unit
def test_all_eleven_land_in_client_destination(tmp_path: Path) -> None:
    """FR03: every agent-capable client gets the whole bundle, in its own directory.

    Fails against HEAD before this change: ``_install_agents`` wrote every
    client's agents to ``.claude/agents`` regardless of the client argument.
    """
    expected = _bundled_stems()
    result = _empty_result()

    _install_agents(tmp_path, force=False, result=result, clients=sorted(KNOWN_CLIENTS))

    for client in AGENT_CAPABLE_CLIENTS:
        fmt = agent_format_for(client)
        dest = tmp_path / str(fmt.destination_dir)
        assert dest.is_dir(), f"{client}: destination {fmt.destination_dir} was not created"
        installed = sorted(path.name for path in dest.iterdir())
        assert installed == sorted(f"{stem}{fmt.filename_suffix}" for stem in expected), (
            f"{client}: destination holds {installed}"
        )

    assert not result["errors"], result["errors"]


@pytest.mark.unit
@requires_monorepo
def test_cursor_cli_is_recorded_once_and_creates_no_directory(tmp_path: Path) -> None:
    """FR03 / US-3: an absence is reported, not silently skipped."""
    result = _empty_result()
    _install_agents(tmp_path, force=False, result=result, clients=["cursor-cli"])

    records = [entry for entry in result.get("info", []) if "cursor-cli" in entry]
    assert len(records) == 1, f"expected exactly one unsupported record, got {records}"
    assert "AGENTS.md" in records[0], "the record must state WHY, not merely that it happened"
    assert not any(tmp_path.iterdir()), "no directory may be created for a client with no agent surface"
    assert not result["errors"]


#: Where each retired stub set WROTE, before PRD-CORE-252 moved anything, with
#: the suffix it used. Read the OLD path, never ``fmt.destination_dir``: the
#: antigravity destination moved from ``.antigravitycli/agents`` to
#: ``.agents/agents``, so resolving it through the CURRENT registry made that
#: client contribute zero names and silently excused it from the retirement
#: check — the one client whose directory moved was the one not being tested.
#: Paired with :func:`_pre_change_tree`, which reads these paths out of git
#: rather than off disk for the same reason one step further on.
#: ``.claude/agents`` is absent because it never had a stub set (it received the
#: bundle) and it also carries dev-repo-only agents this PRD does not govern.
_PRE_CHANGE_STUB_DESTINATIONS: tuple[tuple[str, str, str], ...] = (
    ("cursor-ide", ".cursor/agents", ".md"),
    ("opencode", ".opencode/agents", ".md"),
    ("codex", ".codex/agents", ".toml"),
    ("copilot", ".github/agents", ".agent.md"),
    ("antigravity-cli", ".antigravitycli/agents", ".md"),
)


def _pre_change_tree() -> str:
    """The git tree immediately BEFORE the per-client agent registry landed.

    Read from history, not from the working tree. The working tree is exactly
    what this change migrates: the first ``update-project`` after it sweeps the
    retired stubs, so a test grounded in the live directories measures a
    population that shrinks to nothing the moment the migration works — the
    same vacuity as resolving the pre-change path through the CURRENT registry,
    arriving a day later. History cannot be swept.

    Derived, not pinned: the anchor is the commit that ADDED
    ``agents/agent_formats.py``, so the reference tracks the change itself
    rather than a SHA someone has to remember to update.
    """
    added = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=%H", "--", "trw-mcp/src/trw_mcp/agents/agent_formats.py"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    shas = [line for line in added.stdout.split() if line]
    if added.returncode != 0 or not shas:
        pytest.skip("git history for the agent-format registry is unavailable in this checkout")
    return f"{shas[-1]}^"


def _pre_change_agent_names() -> set[str]:
    """Agent names the PRE-change installer left in this repository's own trees.

    The repo is itself an install produced by the old code and those trees were
    committed, so this is the real pre-change set rather than a list written
    for the test — it cannot be satisfied by editing an expectation.
    """
    tree = _pre_change_tree()
    names: set[str] = set()
    empty: list[str] = []
    for client, rel, suffix in _PRE_CHANGE_STUB_DESTINATIONS:
        listed = subprocess.run(
            ["git", "ls-tree", "--name-only", tree, "--", f"{rel}/"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        found = {Path(line).name.removesuffix(suffix) for line in listed.stdout.splitlines() if line.endswith(suffix)}
        if not found:
            empty.append(client)
        names |= found

    # Non-vacuity per CLIENT, not just in aggregate. Without this, a directory
    # that is missing (or mis-spelled in the table above) drops out silently and
    # the assertion below keeps passing over a smaller and smaller population.
    assert not empty, f"no pre-change agents found for {sorted(empty)}; the retirement check no longer covers them"

    names -= _channel_owned_agent_names(names)
    assert names, "no pre-change agent names found; this test has stopped testing anything"
    return names


@pytest.mark.unit
def test_stub_template_sets_are_gone_and_names_survive(tmp_path: Path) -> None:
    """FR04: the five hand-maintained sources are deleted, and no name vanishes silently.

    The name-preservation half is computed against the set this repository's own
    committed client agent trees carry — the output of the PRE-change installer,
    not a list written for this test. A name that disappears must be recorded as
    retired in ``PREDECESSOR_MAP``; nothing else is an acceptable outcome.
    """
    from trw_mcp.bootstrap._version_migration import PREDECESSOR_MAP

    bootstrap_source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(BOOTSTRAP_DIR.glob("*.py")))
    for retired in ("_CODEX_AGENT_TEMPLATES", "_COPILOT_AGENT_TEMPLATES", "_ANTIGRAVITY_AGENT_TEMPLATES"):
        assert f"{retired}: dict" not in bootstrap_source, f"{retired} is still defined"
        assert f"{retired}.items()" not in bootstrap_source, f"{retired} is still consumed"

    data_root = PACKAGE_ROOT / "src" / "trw_mcp" / "data"
    assert not (data_root / "cursor_ide" / "agents").exists()
    assert not (data_root / "opencode" / "agents").exists()

    previously_shipped = _pre_change_agent_names()

    result = _empty_result()
    _install_agents(tmp_path, force=False, result=result, clients=AGENT_CAPABLE_CLIENTS)
    now_shipped = set(_bundled_stems())
    retired_names = {
        name.removesuffix(".md") for name, successor in PREDECESSOR_MAP["agents"].items() if successor is None
    }

    vanished = previously_shipped - now_shipped
    assert vanished <= retired_names, (
        f"agent names dropped without being recorded as retired: {sorted(vanished - retired_names)}"
    )


@pytest.mark.unit
def test_per_agent_and_per_client_failures_are_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02: failure is isolated at both levels, and nothing escapes."""
    import trw_mcp.bootstrap._init_project as _init_project

    # A bundle where one agent's frontmatter is unparseable.
    fake_bundle = tmp_path / "data"
    (fake_bundle / "agents").mkdir(parents=True)
    good = sorted(BUNDLED_AGENTS_DIR.glob("*.md"))
    for path in good:
        (fake_bundle / "agents" / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (fake_bundle / "agents" / "trw-broken.md").write_text("---\nname: [unterminated\n", encoding="utf-8")
    monkeypatch.setattr(_init_project, "_DATA_DIR", fake_bundle, raising=False)

    target = tmp_path / "project"
    result = _empty_result()
    _install_agents(target, force=False, result=result, clients=["cursor-ide"])

    fmt = agent_format_for("cursor-ide")
    installed = sorted(p.stem for p in (target / str(fmt.destination_dir)).iterdir())
    assert installed == sorted(p.stem for p in good), "the healthy agents must still install"
    assert any("trw-broken" in entry for entry in result["errors"]), result["errors"]

    # A client id absent from the registry: recorded, no exception.
    result2 = _empty_result()
    _install_agents(target, force=False, result=result2, clients=["not-a-registered-client"])
    assert any("not-a-registered-client" in entry for entry in result2.get("info", []))
    assert not result2["errors"]

    # An unwritable destination for one client must not stop the others.
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / ".cursor").write_text("not a directory", encoding="utf-8")
    result3 = _empty_result()
    _install_agents(blocked, force=False, result=result3, clients=["cursor-ide", "codex"])
    assert result3["errors"], "the unwritable client must be recorded"
    assert (blocked / ".codex" / "agents").is_dir(), "the other client still installs"


@pytest.mark.unit
def test_idempotent_and_concurrent_safe(tmp_path: Path) -> None:
    """NFR04: a second run changes no bytes; disjoint destinations are race-free."""
    first = _empty_result()
    _install_agents(tmp_path, force=False, result=first, clients=AGENT_CAPABLE_CLIENTS)
    before = {
        path: path.read_bytes()
        for client in AGENT_CAPABLE_CLIENTS
        for path in (tmp_path / str(agent_format_for(client).destination_dir)).iterdir()
    }

    second = _empty_result()
    _install_agents(tmp_path, force=False, result=second, clients=AGENT_CAPABLE_CLIENTS)
    assert second["created"] == [], "a second run must create nothing"
    assert len(second["skipped"]) == len(before)
    assert {path: path.read_bytes() for path in before} == before, "a second run changed bytes"

    # Two per-client installs into one tree, concurrently.
    concurrent_root = tmp_path / "concurrent"
    results = {client: _empty_result() for client in ("cursor-ide", "codex")}
    threads = [
        threading.Thread(
            target=_install_agents,
            args=(concurrent_root, False, results[client], None),
            kwargs={"clients": [client]},
        )
        for client in results
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    expected = _bundled_stems()
    for client, result in results.items():
        fmt = agent_format_for(client)
        dest = concurrent_root / str(fmt.destination_dir)
        assert sorted(p.stem for p in dest.iterdir()) == expected, f"{client} incomplete"
        assert not result["errors"], result["errors"]


@pytest.mark.unit
def test_update_bytes_equal_a_fresh_materialization(tmp_path: Path) -> None:
    """FR03: install and update cannot diverge, because they share one function."""
    from trw_mcp.bootstrap._template_updater import _update_agents
    from trw_mcp.bootstrap._utils import _DATA_DIR

    # Seed a tree that looks like a pre-change install: agents in the wrong
    # dialect at the wrong destination for a codex project.
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("target_platforms:\n  - codex\n", encoding="utf-8")
    stale = tmp_path / ".codex" / "agents"
    stale.mkdir(parents=True)
    (stale / "trw-explorer.toml").write_text('name = "trw_explorer"\n', encoding="utf-8")

    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
    _update_agents(tmp_path, _DATA_DIR, result, dry_run=False)

    fmt = agent_format_for("codex")
    for path in sorted(BUNDLED_AGENTS_DIR.glob("*.md")):
        dest = tmp_path / fmt.destination_for(path.stem)
        expected = materialize_agent(path.read_text(encoding="utf-8"), client="codex")
        assert dest.read_text(encoding="utf-8") == expected, f"{path.stem}: update bytes differ from fresh render"


@pytest.mark.unit
def test_a_user_edited_agent_survives_update(tmp_path: Path) -> None:
    """FR03: content-aware preservation is unchanged by the destination change."""
    from trw_mcp.bootstrap._template_updater import _update_agents
    from trw_mcp.bootstrap._utils import _DATA_DIR

    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("target_platforms:\n  - cursor-ide\n", encoding="utf-8")

    install = _empty_result()
    _install_agents(tmp_path, force=False, result=install, clients=["cursor-ide"])

    fmt = agent_format_for("cursor-ide")
    # Record TRW's own last write BEFORE the edit — that is what a real manifest
    # holds, and recording the edited bytes instead is exactly the ownership
    # laundering the guard exists to prevent.
    manifest = {
        fmt.destination_for(path.stem): hashlib.sha256(
            (tmp_path / fmt.destination_for(path.stem)).read_bytes()
        ).hexdigest()
        for path in BUNDLED_AGENTS_DIR.glob("*.md")
    }

    edited = tmp_path / fmt.destination_for("trw-auditor")
    edited.write_text("---\nname: trw-auditor\n---\n\nmy own body\n", encoding="utf-8")
    mine = edited.read_bytes()
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
    _update_agents(tmp_path, _DATA_DIR, result, dry_run=False, manifest_hashes=manifest)

    assert edited.read_bytes() == mine, "a user-edited agent was overwritten"
    assert any(str(edited) in entry for entry in result.get("modified", [])), (
        "the preserved edit must be reported, not silently kept"
    )


@pytest.mark.unit
def test_init_project_threads_the_selected_clients(tmp_path: Path) -> None:
    """FR03: the production call site passes the client. It never used to.

    Reads the call site rather than mocking it: the whole defect was a real
    argument that a real caller declined to pass, and a mock of that caller
    would have reported success at HEAD.
    """
    source = (BOOTSTRAP_DIR / "_init_project.py").read_text(encoding="utf-8")
    assert "_install_agents(target_dir, force, result, on_progress, clients=ide_targets)" in source


@pytest.mark.unit
def test_a_traversing_agent_name_is_rejected_before_it_becomes_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR03 on the install path: no write outside the client's destination."""
    import trw_mcp.bootstrap._init_project as _init_project

    fake_bundle = tmp_path / "data"
    (fake_bundle / "agents").mkdir(parents=True)
    (fake_bundle / "agents" / "..escape.md").write_text(
        "---\nname: escape\ndescription: x\nmodel: balanced\neffort: low\n"
        "maxTurns: 1\nmemory: project\ntools:\n  - Read\ndisallowedTools:\n  - Bash\n---\n\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_init_project, "_DATA_DIR", fake_bundle, raising=False)

    target = tmp_path / "project"
    target.mkdir()
    result = _empty_result()
    _install_agents(target, force=False, result=result, clients=["cursor-ide"])

    assert any("Rejected agent name" in entry for entry in result["errors"]), result["errors"]
    assert not list(target.rglob("*escape*")), "a traversing name must not be written anywhere"


@pytest.mark.unit
def test_an_oversized_bundled_agent_is_rejected_with_a_bounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR03: the byte cap is enforced before the file is read into memory."""
    import trw_mcp.bootstrap._init_project as _init_project

    fake_bundle = tmp_path / "data"
    (fake_bundle / "agents").mkdir(parents=True)
    cap = agent_format_for("cursor-ide").max_agent_bytes
    (fake_bundle / "agents" / "trw-huge.md").write_text("x" * (cap + 1), encoding="utf-8")
    monkeypatch.setattr(_init_project, "_DATA_DIR", fake_bundle, raising=False)

    target = tmp_path / "project"
    result = _empty_result()
    _install_agents(target, force=False, result=result, clients=["cursor-ide"])

    assert any("cap" in entry for entry in result["errors"]), result["errors"]
    assert not (target / ".cursor" / "agents" / "trw-huge.md").exists()


@pytest.mark.unit
def test_no_client_is_short_of_the_bundle(tmp_path: Path) -> None:
    """Completion evidence 2: the per-client missing sets intersect to nothing."""
    result = _empty_result()
    _install_agents(tmp_path, force=False, result=result, clients=AGENT_CAPABLE_CLIENTS)

    expected = set(_bundled_stems())
    missing_per_client = []
    for client in AGENT_CAPABLE_CLIENTS:
        fmt = agent_format_for(client)
        dest = tmp_path / str(fmt.destination_dir)
        present = {path.name.removesuffix(fmt.filename_suffix) for path in dest.iterdir()}
        missing_per_client.append(expected - present)

    assert set().union(*missing_per_client) == set(), f"specialists still missing: {missing_per_client}"


@pytest.mark.integration
def test_update_project_installs_agents_end_to_end(tmp_path: Path) -> None:
    """FR03 wiring: the real ``update-project`` entry point reaches the installer.

    The focused per-client update tests stub ``_update_framework_files``, so
    none of them can see this seam; without an unstubbed run, the agent update
    could be unreachable in production and every one of them would still pass —
    which is exactly the shape of the defect this PRD closes.
    """
    from trw_mcp.bootstrap import init_project, update_project

    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="codex")["errors"]

    fmt = agent_format_for("codex")
    dest = tmp_path / str(fmt.destination_dir)
    for path in dest.iterdir():
        path.unlink()

    result = update_project(tmp_path, ide="codex")

    assert not result["errors"], result["errors"]
    assert sorted(p.name.removesuffix(fmt.filename_suffix) for p in dest.iterdir()) == _bundled_stems()
