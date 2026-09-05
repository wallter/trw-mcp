"""PRD-FIX-121 — the manifest recorder must not launder a preserved user edit.

``content_hashes`` in ``.trw/managed-artifacts.yaml`` means *"what TRW last
wrote"*. Before this suite existed, two of the three functions that produce
those keys recorded whatever was on disk at the end of an update — including an
edit the update had just finished PRESERVING. On the next run the guard read the
user's own hash back as TRW's baseline and overwrote the file.

**Every preservation assertion here runs at least TWO ``update_project()``
calls.** That is the load-bearing methodological requirement of the suite: a
single-run assertion is TRUE against the defective code, which is why the class
survived review. Anything asserting preservation after one run proves nothing.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import shutil
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project

# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------

#: The seven surfaces measured as DESTROYED on run 2 before the fix, plus the
#: three that were immune (they already routed through the one recorder that
#: declined). Controls stay in the parameterization on purpose: a fix that
#: preserved the seven by breaking the three would be a different regression.
DESTROYED_SURFACES: dict[str, str] = {
    "claude_hooks": ".claude/hooks/session-start.sh",
    "claude_skills": ".claude/skills/trw-deliver/SKILL.md",
    "claude_agents": ".claude/agents/trw-implementer.md",
    "opencode_skills": ".opencode/skills/trw-delegate/SKILL.md",
    "opencode_instructions": ".opencode/INSTRUCTIONS.md",
    "codex_instructions": ".codex/INSTRUCTIONS.md",
    "codex_agents": ".codex/agents/trw-implementer.toml",
    "codex_skills": ".agents/skills/trw-audit/SKILL.md",
}

IMMUNE_CONTROL_SURFACES: dict[str, str] = {
    "copilot_skills": ".github/skills/trw-audit/SKILL.md",
    "cursor_skills": ".cursor/skills/trw-audit/SKILL.md",
    # OQ-4: immune by construction (hardened recorder) but never independently
    # measured before this suite. Parameterized so the claim is checked, not asserted.
    # The path moved with PRD-CORE-252-FR03: Antigravity's own subagent reference
    # documents `.agents/agents`, and TRW's `.antigravitycli/agents` appears in it
    # nowhere. The surface — a whole-file client agent recorded by
    # `_managed_client_artifacts` — is unchanged, which is what this control tests.
    "antigravity_agents": ".agents/agents/trw-implementer.md",
}

ALL_SURFACES: dict[str, str] = {**DESTROYED_SURFACES, **IMMUNE_CONTROL_SURFACES}

_EDIT_MARKER = "trw-fix-121 user edit — must survive every update"


def _user_edit(original: bytes, suffix: str) -> bytes:
    """Append a plausible hand edit in the artifact's own comment syntax."""
    comment = f"# {_EDIT_MARKER}" if suffix in {".sh", ".toml"} else f"<!-- {_EDIT_MARKER} -->"
    return original + f"\n{comment}\n".encode()


def _init_all_clients(root: Path) -> Path:
    repo = root / "proj"
    repo.mkdir()
    (repo / ".git").mkdir()
    result = init_project(repo, ide="all")
    assert not result["errors"], result["errors"]
    return repo


def _apply_user_edits(repo: Path) -> dict[str, bytes]:
    """Hand-edit every parameterized surface; return ``{surface id: user bytes}``."""
    edits: dict[str, bytes] = {}
    for surface_id, rel in ALL_SURFACES.items():
        dest = repo / rel
        assert dest.is_file(), f"{rel} was not installed — the fixture is vacuous for {surface_id}"
        edits[surface_id] = _user_edit(dest.read_bytes(), dest.suffix)
        dest.write_bytes(edits[surface_id])
    return edits


# ---------------------------------------------------------------------------
# FR01 — the recorder omits a user-edited artifact
# ---------------------------------------------------------------------------


class TestRecorderDeclinesUserEdits:
    """FR01: a user-edited artifact produces NO ``content_hashes`` key."""

    def test_edited_hook_gets_no_manifest_entry(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest
        from trw_mcp.bootstrap._version_migration import _write_manifest

        repo = _init_all_clients(tmp_path)
        hook = repo / ".claude" / "hooks" / "session-start.sh"
        untouched_hash = _manifest_content_hashes(_read_manifest(repo))
        assert untouched_hash and "session-start.sh" in untouched_hash, (
            "non-vacuity: the key must be present BEFORE the edit, or the test proves nothing"
        )

        hook.write_bytes(_user_edit(hook.read_bytes(), ".sh"))
        _write_manifest(repo, {"updated": [], "created": [], "errors": []})

        hashes = _manifest_content_hashes(_read_manifest(repo))
        assert hashes is not None
        # Omission — not a sentinel, not null. A missing key is what makes
        # _is_user_modified fall through to the framework baseline and preserve.
        assert "session-start.sh" not in hashes
        # Non-vacuity control: unedited siblings are still recorded.
        assert any(k.endswith(".sh") for k in hashes), "the recorder recorded nothing at all"

    def test_edit_is_omitted_not_recorded_as_null(self, tmp_path: Path) -> None:
        from ruamel.yaml import YAML

        from trw_mcp.bootstrap._version_migration import _write_manifest

        repo = _init_all_clients(tmp_path)
        hook = repo / ".claude" / "hooks" / "session-start.sh"
        hook.write_bytes(_user_edit(hook.read_bytes(), ".sh"))
        _write_manifest(repo, {"updated": [], "created": [], "errors": []})

        raw = YAML(typ="safe").load((repo / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8"))
        assert "session-start.sh" not in raw["content_hashes"]


# ---------------------------------------------------------------------------
# FR02 / FR03 — survives run 2, and run 5
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def two_run_project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, dict[str, bytes]]]:
    """One install, all surfaces hand-edited, TWO consecutive ``update_project`` runs.

    Module-scoped because the observation is per-run, not per-surface: a shared
    pair of runs is exactly the situation the defect occurs in, and rebuilding a
    full multi-client install per surface would cost minutes for no extra signal.
    """
    repo = _init_all_clients(tmp_path_factory.mktemp("two-run"))
    edits = _apply_user_edits(repo)
    first = update_project(repo)
    assert not first["errors"], first["errors"]
    second = update_project(repo)
    assert not second["errors"], second["errors"]
    yield repo, edits


class TestTwoRunPreservation:
    """FR02/FR03: a preserved edit survives the SECOND run, and the fifth."""

    @pytest.mark.parametrize("surface_id", sorted(ALL_SURFACES))
    def test_user_edit_survives_two_updates(
        self,
        surface_id: str,
        two_run_project: tuple[Path, dict[str, bytes]],
    ) -> None:
        repo, edits = two_run_project
        dest = repo / ALL_SURFACES[surface_id]
        assert dest.read_bytes() == edits[surface_id]

    def test_untouched_artifacts_are_still_rewritten(
        self,
        two_run_project: tuple[Path, dict[str, bytes]],
    ) -> None:
        """Non-vacuity control: the updater is not simply refusing to write.

        Without this, a writer that stopped writing anything at all would pass
        every preservation assertion above.
        """
        repo, _ = two_run_project
        untouched = repo / ".claude" / "hooks" / "lib-trw.sh"
        assert untouched.is_file()
        bundled = (Path(inspect.getfile(init_project)).parent.parent / "data" / "hooks" / "lib-trw.sh").read_bytes()
        assert untouched.read_bytes() == bundled

    def test_preservation_is_idempotent_across_five_runs(self, tmp_path: Path) -> None:
        """FR03: bytes survive five runs AND every run reports the preservation.

        The reporting half is a distinct requirement: at HEAD the run-2
        destruction was silent (``result['modified'] == []``), so a fix that kept
        the bytes but stopped reporting would leave the operator unable to tell
        preservation from a no-op.
        """
        repo = _init_all_clients(tmp_path)
        hook = repo / ".claude" / "hooks" / "session-start.sh"
        user_bytes = _user_edit(hook.read_bytes(), ".sh")
        hook.write_bytes(user_bytes)

        for run in range(1, 6):
            result = update_project(repo)
            assert not result["errors"], result["errors"]
            assert hook.read_bytes() == user_bytes, f"destroyed on run {run}"
            assert str(hook) in result.get("modified", []), f"preservation unreported on run {run}"


# ---------------------------------------------------------------------------
# FR04 — the fix must not freeze artifacts (RISK-001)
# ---------------------------------------------------------------------------


def _bundle_with_override(bundle_root: Path, rel: str, content: str) -> Path:
    """A copy of the shipped data dir with one artifact set to *content* ("bundle N")."""
    from trw_mcp.bootstrap._utils import _DATA_DIR

    old_bundle = bundle_root / "data"
    if not old_bundle.exists():
        shutil.copytree(_DATA_DIR, old_bundle)
    target = old_bundle / rel
    assert target.is_file(), f"{rel} is not a bundled artifact"
    target.write_text(content, encoding="utf-8")
    return old_bundle


class TestStaleArtifactsStillRefresh:
    """FR04: an UNEDITED artifact still advances when the bundle does."""

    def test_unedited_artifact_refreshes_to_new_bundle(
        self,
        tmp_path: Path,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        repo = _init_all_clients(tmp_path)
        old_bundle = _bundle_with_override(tmp_path_factory.mktemp("bundle-n"), "hooks/session-start.sh", "old hook\n")
        hook = repo / ".claude" / "hooks" / "session-start.sh"

        # Bundle N: TRW writes the older hook, so the manifest records a hash
        # TRW actually wrote (as opposed to re-baselining hand-written content,
        # which is the laundering FR01 forbids).
        update_project(repo, data_dir=old_bundle)
        assert hook.read_text(encoding="utf-8") == "old hook\n"

        # Bundle N+1: user never touched it -> must refresh, not freeze.
        result = update_project(repo)
        assert hook.read_text(encoding="utf-8") != "old hook\n"
        assert str(hook) in result["updated"]
        assert str(hook) not in result.get("modified", [])

    def test_refresh_survives_a_second_run(
        self,
        tmp_path: Path,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        """The refreshed artifact stays current — the fix does not oscillate."""
        repo = _init_all_clients(tmp_path)
        old_bundle = _bundle_with_override(tmp_path_factory.mktemp("bundle-n"), "hooks/session-start.sh", "old hook\n")
        hook = repo / ".claude" / "hooks" / "session-start.sh"
        update_project(repo, data_dir=old_bundle)
        update_project(repo)
        refreshed = hook.read_bytes()
        assert refreshed != b"old hook\n", "non-vacuity: the artifact never left bundle N"

        result = update_project(repo)
        assert hook.read_bytes() == refreshed
        assert str(hook) not in result.get("modified", [])


# ---------------------------------------------------------------------------
# FR05 — no recorder may bypass the shared predicate
# ---------------------------------------------------------------------------


def _content_hash_producers(source: str, func_name: str) -> set[str]:
    """Every expression that contributes keys to a local ``content_hashes`` map.

    Deliberately over-broad: any assignment to, subscript-store on, or method
    call against ``content_hashes`` counts. A fourth recorder inlined into
    ``_write_manifest`` therefore shows up here no matter how it is spelled.
    """
    tree = ast.parse(textwrap.dedent(source))
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name)

    def callee(node: ast.AST) -> str:
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                return node.func.id
            if isinstance(node.func, ast.Attribute):
                return node.func.attr
        return type(node).__name__

    producers: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "content_hashes":
                    producers.add(callee(node.value))
                if isinstance(target, ast.Subscript) and getattr(target.value, "id", None) == "content_hashes":
                    producers.add("SUBSCRIPT_STORE")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and getattr(node.func.value, "id", None) == "content_hashes"
        ):
            producers.update(callee(arg) for arg in node.args)
    return producers


#: One representative artifact per registry member: the one to hand-edit, and an
#: untouched sibling that MUST still be recorded. The equality assertion against
#: ``MANIFEST_RECORDERS`` below is what makes a newly added recorder fail loudly
#: instead of silently reintroducing the gap (RISK-002).
RECORDER_CASES: dict[str, tuple[str, str]] = {
    "core_artifacts": (".claude/hooks/session-start.sh", "lib-trw.sh"),
    # PRD-CORE-252-FR04: `.codex/agents` left this recorder when codex agents
    # became materializations of the shared bundle; the codex skills mirror is
    # what it still owns, and is what its ownership behaviour must be proven on.
    "codex_artifacts": (".agents/skills/trw-audit/SKILL.md", ".agents/skills/trw-deliver/SKILL.md"),
    "managed_client_artifacts": (".github/skills/trw-audit/SKILL.md", ".github/skills/trw-deliver/SKILL.md"),
}


def _keys_recorded_when_nothing_is_ours(
    recorders: tuple[object, ...],
    repo: Path,
    prev: dict[str, str] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, set[str]]:
    """``{recorder name: keys it still produced}`` with the predicate forced to True.

    Both forms of the shared ownership predicate are patched to report EVERY
    artifact as a user edit. An honest recorder therefore returns nothing at all;
    any key that survives is a key produced without consulting the predicate,
    wherever inside the recorder it is spelled.

    This is the layer the three static checks cannot supply. They inspect
    function NAMES — ``_write_manifest``'s producers, the collector's producers,
    and the registry's name set — so a fourth key producer merged into an
    EXISTING recorder's body is invisible to all of them. Measured 2026-07-28: a
    rogue producer inside ``_record_core_artifacts`` passed all three and
    laundered a user-authored hook into the ownership baseline.
    """
    from trw_mcp.bootstrap import _managed_client_artifacts

    monkeypatch.setattr(_managed_client_artifacts, "artifact_user_edited", lambda *a, **k: True)
    monkeypatch.setattr(_managed_client_artifacts, "artifact_user_edited_against", lambda *a, **k: True)
    return {r.name: set(r.record(repo, prev, None)) for r in recorders}  # type: ignore[attr-defined]


class TestNoUnguardedRecorder:
    """FR05: the registry is total, and every member declines a user edit."""

    def test_no_recorder_records_anything_when_the_predicate_declines_everything(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR05 runtime totality — every key must have passed through the predicate.

        ``test_every_manifest_recorder_declines_user_edits`` proves each recorder
        declines ONE hand-picked artifact. That is not the property FR05 needs:
        it says nothing about the other artifacts the same recorder produces. This
        one covers every key against every artifact actually on disk.
        """
        from trw_mcp.bootstrap._manifest_recorders import MANIFEST_RECORDERS
        from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest

        repo = _init_all_clients(tmp_path)
        prev = _manifest_content_hashes(_read_manifest(repo))

        # Non-vacuity: unpatched, every registry member records real keys, so an
        # empty result below means "declined", never "nothing was installed".
        for recorder in MANIFEST_RECORDERS:
            assert recorder.record(repo, prev, None), f"{recorder.name} recorded nothing — the fixture is vacuous"

        surviving = _keys_recorded_when_nothing_is_ours(MANIFEST_RECORDERS, repo, prev, monkeypatch)
        leaked = {name: sorted(keys) for name, keys in surviving.items() if keys}
        assert not leaked, (
            "a manifest recorder produced content_hashes keys without consulting "
            f"artifact_user_edited/_against — those artifacts can be laundered: {leaked}"
        )

    def test_runtime_guard_catches_a_producer_inside_an_existing_recorder(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-vacuity for the guard above: it must FAIL on the real defect shape.

        A guard that has never been observed red is not a guard. The rogue here
        is the exact shape that passed all three static layers — an extra
        unconditional key merged into a registered recorder's return dict.
        """
        from trw_mcp.bootstrap._manifest_recorders import MANIFEST_RECORDERS, ManifestRecorder
        from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest

        repo = _init_all_clients(tmp_path)
        prev = _manifest_content_hashes(_read_manifest(repo))
        rogue_path = repo / ".claude" / "hooks" / "rogue-hook.sh"
        rogue_path.write_bytes(b"#!/bin/sh\n# the user's own hook; TRW never wrote it\n")
        honest = MANIFEST_RECORDERS[0]

        def _rogue(target_dir: Path, prev_hashes: dict[str, str] | None, data_dir: Path | None) -> dict[str, str]:
            recorded = honest.record(target_dir, prev_hashes, data_dir)
            recorded["rogue-hook.sh"] = hashlib.sha256(rogue_path.read_bytes()).hexdigest()
            return recorded

        rogue_registry = (ManifestRecorder(honest.name, honest.surfaces, _rogue), *MANIFEST_RECORDERS[1:])
        surviving = _keys_recorded_when_nothing_is_ours(rogue_registry, repo, prev, monkeypatch)

        assert surviving[honest.name] == {"rogue-hook.sh"}, (
            "the runtime guard did not isolate the unguarded producer — it would "
            "not have gone red on the measured defect"
        )

    def test_write_manifest_derives_content_hashes_only_from_the_registry(self) -> None:
        from trw_mcp.bootstrap import _version_migration

        producers = _content_hash_producers(inspect.getsource(_version_migration), "_write_manifest")
        assert producers == {"collect_manifest_content_hashes"}, (
            f"_write_manifest gained a content_hashes producer outside the registry: {producers}"
        )

    def test_producer_extractor_catches_an_inlined_recorder(self) -> None:
        """Non-vacuity: the AST guard above must actually detect a rogue recorder."""
        rogue = """
            def _write_manifest(target_dir, result, data_dir=None):
                content_hashes = collect_manifest_content_hashes(target_dir, None, data_dir)
                content_hashes.update(_rogue_recorder(target_dir))
                return content_hashes
        """
        assert _content_hash_producers(rogue, "_write_manifest") == {
            "collect_manifest_content_hashes",
            "_rogue_recorder",
        }

    def test_collector_iterates_only_the_declared_registry(self) -> None:
        from trw_mcp.bootstrap import _manifest_recorders

        source = inspect.getsource(_manifest_recorders)
        producers = _content_hash_producers(source, "collect_manifest_content_hashes")
        # The only contributor is ``recorder.record(...)`` for a recorder drawn
        # from MANIFEST_RECORDERS.
        assert producers == {"record"}
        loops = [
            n
            for n in ast.walk(ast.parse(textwrap.dedent(source)))
            if isinstance(n, ast.For) and getattr(n.iter, "id", None) == "MANIFEST_RECORDERS"
        ]
        assert len(loops) == 1

    def test_recorder_cases_cover_every_registry_member(self) -> None:
        from trw_mcp.bootstrap._manifest_recorders import MANIFEST_RECORDERS

        assert {r.name for r in MANIFEST_RECORDERS} == set(RECORDER_CASES), (
            "a manifest recorder was added or renamed without a decline case — "
            "add it to RECORDER_CASES so its ownership behavior is proven"
        )

    @pytest.mark.parametrize("recorder_name", sorted(RECORDER_CASES))
    def test_every_manifest_recorder_declines_user_edits(self, recorder_name: str, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._manifest_recorders import MANIFEST_RECORDERS
        from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest

        recorder = next(r for r in MANIFEST_RECORDERS if r.name == recorder_name)
        edited_rel, untouched_key_fragment = RECORDER_CASES[recorder_name]

        repo = _init_all_clients(tmp_path)
        prev = _manifest_content_hashes(_read_manifest(repo))

        baseline = recorder.record(repo, prev, None)
        edited_key = next(k for k in baseline if edited_rel.endswith(k) or k == edited_rel)
        assert any(untouched_key_fragment.endswith(k) or k == untouched_key_fragment for k in baseline), (
            f"non-vacuity: {recorder_name} did not record the untouched control artifact"
        )

        dest = repo / edited_rel
        dest.write_bytes(_user_edit(dest.read_bytes(), dest.suffix))
        after = recorder.record(repo, prev, None)

        assert edited_key not in after, f"{recorder_name} laundered a user edit into the ownership baseline"
        # Non-vacuity: the recorder still records everything it legitimately owns.
        assert any(untouched_key_fragment.endswith(k) or k == untouched_key_fragment for k in after)


# ---------------------------------------------------------------------------
# NFR01 / NFR04 — degrade toward preservation
# ---------------------------------------------------------------------------


class TestDegradedManifest:
    """NFR01/NFR04: unknown ownership must never resolve to "TRW owns it"."""

    @pytest.mark.parametrize("degradation", ["absent", "empty", "corrupt"])
    def test_corrupt_manifest_still_preserves_user_edits(self, degradation: str, tmp_path: Path) -> None:
        repo = _init_all_clients(tmp_path)
        hook = repo / ".claude" / "hooks" / "session-start.sh"
        user_bytes = _user_edit(hook.read_bytes(), ".sh")
        hook.write_bytes(user_bytes)

        manifest = repo / ".trw" / "managed-artifacts.yaml"
        if degradation == "absent":
            manifest.unlink()
        elif degradation == "empty":
            manifest.write_text("", encoding="utf-8")
        else:
            manifest.write_text("content_hashes: [oops\n  version: :::\n", encoding="utf-8")

        update_project(repo)
        assert hook.read_bytes() == user_bytes, f"destroyed on run 1 with a {degradation} manifest"
        update_project(repo)
        assert hook.read_bytes() == user_bytes, f"destroyed on run 2 with a {degradation} manifest"

    def test_unreadable_artifact_fails_toward_preservation(self, tmp_path: Path) -> None:
        """NFR04: an OSError on the artifact read leaves it UNRECORDED, never raises."""
        from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest
        from trw_mcp.bootstrap._version_migration import _write_manifest

        repo = _init_all_clients(tmp_path)
        hook = repo / ".claude" / "hooks" / "session-start.sh"
        hook.chmod(0o000)
        if hook.is_file():
            try:
                hook.read_bytes()
            except OSError:
                pass
            else:  # running as root — the premise cannot be established
                hook.chmod(0o755)
                pytest.skip("cannot make a file unreadable as this user")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _write_manifest(repo, result)
        hook.chmod(0o755)

        assert not result["errors"]
        hashes = _manifest_content_hashes(_read_manifest(repo))
        assert hashes is not None
        assert "session-start.sh" not in hashes
        assert any(k.endswith(".sh") for k in hashes), "non-vacuity: nothing was recorded at all"

    def test_unreadable_bundled_source_fails_toward_preservation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """NFR04, bundled side: no framework baseline means ownership is undecidable.

        ``_framework_content_hashes`` returns an empty set when the bundled file
        cannot be read. Recording anyway would let the error path reintroduce the
        defect — "we could not check" degrading to the reassuring answer.
        """
        from trw_mcp.bootstrap import _version_manifest
        from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest
        from trw_mcp.bootstrap._version_migration import _write_manifest

        repo = _init_all_clients(tmp_path)
        before = _manifest_content_hashes(_read_manifest(repo)) or {}
        assert any(k.endswith(".sh") for k in before), "non-vacuity: hooks must be recorded before the stub"

        monkeypatch.setattr(_version_manifest, "_framework_content_hashes", lambda _src: set())
        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _write_manifest(repo, result)

        assert not result["errors"]
        after = _manifest_content_hashes(_read_manifest(repo)) or {}
        assert not any(k.endswith(".sh") for k in after)


# ---------------------------------------------------------------------------
# NFR02 — the explicit-authorization escape hatch is unchanged
# ---------------------------------------------------------------------------


class TestForceStillOverwrites:
    """NFR02: ``force=True`` must still overwrite a hand edit.

    Stated in the PRD against ``update_project(force=True)``, which does not
    exist — ``--force`` is an ``init-project`` flag, and the per-client
    generators take a ``force`` kwarg. Both real surfaces are exercised here.
    """

    def test_force_overwrites_user_edit(self, tmp_path: Path) -> None:
        repo = _init_all_clients(tmp_path)
        hook = repo / ".claude" / "hooks" / "session-start.sh"
        bundled = hook.read_bytes()
        hook.write_bytes(_user_edit(bundled, ".sh"))

        init_project(repo, force=True, ide="all")

        assert hook.read_bytes() == bundled

    def test_generator_force_overwrites_user_edit(self, tmp_path: Path) -> None:
        """``force`` still discards a hand edit — now through the shared installer.

        PRD-CORE-252-FR04 retired ``generate_codex_agents`` with the codex stub
        template dictionary; codex agents come from the shared bundle, so the
        escape hatch this asserts is ``_install_agents(force=True)``.
        """
        from trw_mcp.bootstrap._init_project_skills import _install_agents

        repo = _init_all_clients(tmp_path)
        agent = repo / ".codex" / "agents" / "trw-implementer.toml"
        bundled = agent.read_bytes()
        agent.write_bytes(_user_edit(bundled, ".toml"))

        result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}
        _install_agents(repo, force=True, result=result, clients=["codex"])

        assert not result["errors"], result["errors"]
        assert agent.read_bytes() == bundled


# ---------------------------------------------------------------------------
# NFR03 — bounded cost
# ---------------------------------------------------------------------------


class TestRecorderCost:
    """NFR03: bundled content is read once per SURFACE, not once per file."""

    def test_bundled_contents_read_once_per_surface(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.bootstrap import _managed_client_artifacts
        from trw_mcp.bootstrap._managed_client_artifacts import (
            MANAGED_CLIENT_ARTIFACT_SOURCES,
            ManagedArtifactSource,
        )
        from trw_mcp.bootstrap._version_migration import _write_manifest

        repo = _init_all_clients(tmp_path)
        calls: dict[str, int] = {}

        def _counting(source: ManagedArtifactSource) -> ManagedArtifactSource:
            def wrapped() -> dict[str, bytes]:
                calls[source.surface] = calls.get(source.surface, 0) + 1
                return source.contents()

            return ManagedArtifactSource(source.client, source.surface, wrapped)

        monkeypatch.setattr(
            _managed_client_artifacts,
            "MANAGED_CLIENT_ARTIFACT_SOURCES",
            tuple(_counting(s) for s in MANAGED_CLIENT_ARTIFACT_SOURCES),
        )
        _write_manifest(repo, {"updated": [], "created": [], "errors": []})

        assert calls, "non-vacuity: the counting stub was never reached"
        assert set(calls.values()) == {1}, f"a surface's contents() was read more than once: {calls}"


# ---------------------------------------------------------------------------
# Mechanism-level regression: the predicate is shared, not copied
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_artifact_user_edited_reuses_the_hash_set_form() -> None:
    """The bytes form is a thin adapter over the hash-set form, not a fifth copy."""
    from trw_mcp.bootstrap._managed_client_artifacts import artifact_user_edited

    source = inspect.getsource(artifact_user_edited)
    assert "artifact_user_edited_against" in source
    assert "_is_user_modified" not in source.split('"""')[-1]


def test_declined_artifact_is_absent_not_hashed_as_bundled(tmp_path: Path) -> None:
    """A declined entry must be OMITTED, not silently recorded as the BUNDLED hash.

    Recording the bundled hash instead of the user's would also make the next
    run overwrite (the guard would read "matches what TRW wrote"), so the two
    failure modes are worth distinguishing explicitly.
    """
    from trw_mcp.bootstrap._utils import _DATA_DIR
    from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest
    from trw_mcp.bootstrap._version_migration import _write_manifest

    repo = _init_all_clients(tmp_path)
    hook = repo / ".claude" / "hooks" / "session-start.sh"
    hook.write_bytes(_user_edit(hook.read_bytes(), ".sh"))
    _write_manifest(repo, {"updated": [], "created": [], "errors": []})

    hashes = _manifest_content_hashes(_read_manifest(repo)) or {}
    bundled_hash = hashlib.sha256((_DATA_DIR / "hooks" / "session-start.sh").read_bytes()).hexdigest()
    assert hashes.get("session-start.sh") != bundled_hash
    assert "session-start.sh" not in hashes


# ---------------------------------------------------------------------------
# Round-2 regression: bundle-driven enumeration orphans a file inside a KEPT dir
# ---------------------------------------------------------------------------

_SKILL_KEY = ".agents/skills/trw-audit/SKILL.md"
_DROPPED_KEY = ".agents/skills/trw-audit/audit-framework.md"


@pytest.fixture
def codex_bundle(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A writable copy of the codex skills bundle, wired in as the live source.

    Lets a test drop a file from a skill that stays bundled — the shape the
    directory-granular cleanup cannot see.
    """
    from trw_mcp.bootstrap import _codex

    fake = tmp_path_factory.mktemp("codex-bundle") / "skills"
    shutil.copytree(_codex._codex_skills_source_dir(), fake)
    monkeypatch.setattr(_codex, "_codex_skills_source_dir", lambda: fake)
    return fake


class TestDroppedBundleFileDoesNotFreeze:
    """A file dropped from a KEPT skill dir must be swept, not silently orphaned.

    ``_codex_manifest_hashes`` became bundle-driven in the PRD-FIX-121 fix on the
    stated grounds that ``_remove_stale_client_artifacts`` "deletes exactly
    those". That held at skill-DIRECTORY granularity only. Drop one file and it
    stayed on disk with no manifest record, so ``_is_user_modified``'s
    (correct, load-bearing) "no record → user's file → preserve" fallback froze
    it at its old bytes the moment upstream re-added the name. Measured
    2026-07-28: ``refreshed=False``, ``result['preserved']`` naming a file the
    user had never touched — a REGRESSION versus the pre-fix filesystem scan,
    which kept recording the orphan so baseline 2 still matched.
    """

    def test_dropped_file_is_swept_and_refreshes_when_upstream_readds_it(
        self,
        tmp_path: Path,
        codex_bundle: Path,
    ) -> None:
        repo = _init_all_clients(tmp_path)
        dropped = repo / _DROPPED_KEY
        skill_md = repo / _SKILL_KEY
        assert dropped.is_file() and skill_md.is_file(), "fixture: both files must install"

        # Upstream drops ONE file; the skill itself stays bundled, and its
        # sibling advances — the non-vacuity control for the sweep.
        (codex_bundle / "trw-audit" / "audit-framework.md").unlink()
        bundled_skill_md = codex_bundle / "trw-audit" / "SKILL.md"
        bundled_skill_md.write_bytes(bundled_skill_md.read_bytes() + b"\n<!-- upstream v2 -->\n")

        update_project(repo)

        assert not dropped.exists(), "the orphaned file survived the sweep"
        assert skill_md.read_bytes() == bundled_skill_md.read_bytes(), (
            "control: a file that IS still bundled must still refresh — a sweep "
            "that deleted everything would pass without this"
        )

        # Upstream re-adds the name with NEW content. Pre-fix this was frozen.
        readded = b"# audit framework v2\n"
        (codex_bundle / "trw-audit" / "audit-framework.md").write_bytes(readded)
        result = update_project(repo)

        assert dropped.read_bytes() == readded
        assert not any(_DROPPED_KEY in entry for entry in result.get("preserved", []))

    def test_a_user_authored_file_in_a_kept_skill_dir_is_never_deleted(
        self,
        tmp_path: Path,
        codex_bundle: Path,
    ) -> None:
        """HB-2: the sweep needs proof of TRW authorship, not "it sits in a trw- dir"."""
        repo = _init_all_clients(tmp_path)
        mine = repo / ".agents" / "skills" / "trw-audit" / "my-team-notes.md"
        mine.write_bytes(b"# my notes, never bundled\n")

        update_project(repo)
        update_project(repo)

        assert mine.is_file(), "the sweep deleted a file the user authored"
        assert mine.read_bytes() == b"# my notes, never bundled\n"

    def test_a_user_edited_dropped_file_is_preserved(
        self,
        tmp_path: Path,
        codex_bundle: Path,
    ) -> None:
        """HB-2: content drifted from the manifest record means the user edited it."""
        repo = _init_all_clients(tmp_path)
        dropped = repo / _DROPPED_KEY
        user_bytes = _user_edit(dropped.read_bytes(), ".md")
        dropped.write_bytes(user_bytes)

        (codex_bundle / "trw-audit" / "audit-framework.md").unlink()
        update_project(repo)
        update_project(repo)

        assert dropped.read_bytes() == user_bytes, "a hand edit was swept as a stale TRW file"

    def test_sweep_is_wired_into_update_project(self, tmp_path: Path, codex_bundle: Path) -> None:
        """The pre-run manifest must actually reach the sweep.

        ``manifest_hashes`` defaults to ``None`` (which disables the sweep, the
        safe direction), so a dropped kwarg anywhere on the
        ``update_project`` → ``_cleanup_stale_artifacts`` →
        ``_remove_stale_client_artifacts`` chain would silently restore the
        regression. This pins the wiring at the seam, in addition to the
        end-to-end behaviour above.
        """
        from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest

        repo = _init_all_clients(tmp_path)
        prev = _manifest_content_hashes(_read_manifest(repo)) or {}
        assert _DROPPED_KEY in prev, "non-vacuity: init must record the file the sweep will need"

        (codex_bundle / "trw-audit" / "audit-framework.md").unlink()
        result = update_project(repo)

        # Must be the REMOVAL of the CODEX copy. A bare "…audit-framework.md"
        # match was measured passing against the pre-fix source: the claude /
        # copilot / cursor mirrors all install a file of that name and report it
        # in ``updated``, so the loose form proved nothing.
        assert any(
            entry.startswith("removed:") and entry.endswith(_DROPPED_KEY) for entry in result.get("updated", [])
        ), f"the removal was never reported — the sweep did not run: {result.get('updated', [])}"

    def test_dry_run_reports_the_removal_without_deleting(self, tmp_path: Path, codex_bundle: Path) -> None:
        from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest
        from trw_mcp.bootstrap._version_migration import _remove_stale_client_artifacts

        repo = _init_all_clients(tmp_path)
        prev = _manifest_content_hashes(_read_manifest(repo))
        (codex_bundle / "trw-audit" / "audit-framework.md").unlink()

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_client_artifacts(repo, result, dry_run=True, manifest_hashes=prev)

        assert (repo / _DROPPED_KEY).is_file()
        assert any(e.startswith("would remove:") and e.endswith("audit-framework.md") for e in result["updated"])

    def test_sweep_is_disabled_without_a_prior_manifest(self, tmp_path: Path, codex_bundle: Path) -> None:
        """No baseline means no proof of authorship — degrade to leaving it alone."""
        from trw_mcp.bootstrap._version_migration import _remove_stale_client_artifacts

        repo = _init_all_clients(tmp_path)
        (codex_bundle / "trw-audit" / "audit-framework.md").unlink()

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_client_artifacts(repo, result, manifest_hashes=None)

        assert (repo / _DROPPED_KEY).is_file()


def test_every_directory_surface_declares_a_file_key_source() -> None:
    """P10 guard for the sweep itself: a dir surface without one is a silent hole.

    ``bundled_files=None`` disables the file-granular sweep. That is the correct
    default for file surfaces (``.codex/agents``, ``.cursor/commands`` — their
    artifacts ARE the entries) and a reintroduction of this defect for any
    directory surface, which is exactly how the codex hole appeared.
    """
    from trw_mcp.bootstrap._version_migration_clients import _CLIENT_ARTIFACT_SURFACES

    missing = [s.client_dir for s in _CLIENT_ARTIFACT_SURFACES if s.is_dir_artifact and s.bundled_files is None]
    assert not missing, f"directory surfaces with no file-key source cannot sweep dropped files: {missing}"
    assert any(s.is_dir_artifact for s in _CLIENT_ARTIFACT_SURFACES), "non-vacuity: no directory surface exists"
