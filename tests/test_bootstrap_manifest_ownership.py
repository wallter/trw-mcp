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
        assert ".claude/hooks/session-start.sh" in result["updated"]
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


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    """Return ``{relpath: bytes}`` for every regular file under *root*."""
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class TestDegradedManifest:
    """NFR01/NFR04: unknown ownership must never resolve to "TRW owns it"."""

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
            except OSError:  # trw-fail-silent-allow: probing that the chmod made the file unreadable; the else branch skips when it did not
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

        def bare_sh_keys(hashes: dict[str, str]) -> list[str]:
            # ``.sh`` keys bare (no ``/``) are ``.claude/hooks/*.sh`` -- the ONE
            # surface ``_framework_content_hashes`` (patched below) baselines.
            # PRD-INFRA-192 FR09 C3 added a SEPARATE ``.sh`` producer
            # (``.github/hooks/*.sh``, full-path keys) that this stub does not
            # touch, so a bare check would stay non-vacuous for the wrong reason.
            return [k for k in hashes if k.endswith(".sh") and "/" not in k]

        repo = _init_all_clients(tmp_path)
        before = _manifest_content_hashes(_read_manifest(repo)) or {}
        assert bare_sh_keys(before), "non-vacuity: hooks must be recorded before the stub"

        monkeypatch.setattr(_version_manifest, "_framework_content_hashes", lambda _src: set())
        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _write_manifest(repo, result)

        assert not result["errors"]
        after = _manifest_content_hashes(_read_manifest(repo)) or {}
        assert not bare_sh_keys(after)


class TestInvalidManifestRefuses:
    """PRD-INFRA-192-NFR02: a missing/corrupt/unsupported-schema manifest makes
    ``update_project`` refuse before touching any artifact, rather than guessing
    ownership. This supersedes the old fail-open "preserves user edits anyway"
    behavior asserted by the retired ``test_corrupt_manifest_still_preserves_user_edits``
    — refusal makes that assertion vacuously true (nothing was touched at all),
    so it is replaced with an explicit refusal + byte-identical-tree contract.
    """

    def _assert_refuses_without_side_effects(self, repo: Path) -> None:
        before = _snapshot_tree(repo)
        assert before, "non-vacuity: the tree snapshot must contain at least one file"
        assert any(rel.startswith(".trw" + "/") for rel in before), (
            "non-vacuity: the snapshot must cover .trw/ (memory/learnings), not just client dirs"
        )

        result = update_project(repo)

        assert len(result["errors"]) == 1, result["errors"]
        (message,) = result["errors"]
        assert "refusing to update" in message
        assert "clean reinstall: `trw-mcp uninstall --keep-memory` then `trw-mcp init-project`" in message

        after = _snapshot_tree(repo)
        assert after == before, "refusal must not modify a single byte of the project"

        # A second call also refuses — no side effect from the first call made
        # the manifest valid.
        result2 = update_project(repo)
        assert len(result2["errors"]) == 1, result2["errors"]
        assert "refusing to update" in result2["errors"][0]
        assert _snapshot_tree(repo) == before

    @pytest.mark.parametrize("degradation", ["absent", "empty", "corrupt", "non_mapping"])
    def test_missing_or_malformed_manifest_refuses(self, degradation: str, tmp_path: Path) -> None:
        repo = _init_all_clients(tmp_path)
        manifest = repo / ".trw" / "managed-artifacts.yaml"
        if degradation == "absent":
            manifest.unlink()
        elif degradation == "empty":
            manifest.write_text("", encoding="utf-8")
        elif degradation == "corrupt":
            manifest.write_text("content_hashes: [oops\n  version: :::\n", encoding="utf-8")
        else:  # non_mapping — a YAML list at the document root, not a mapping
            manifest.write_text("- version\n- 2\n", encoding="utf-8")

        self._assert_refuses_without_side_effects(repo)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("content_hashes", None),
            ("content_hashes", ["a.md"]),
            ("content_hashes", {"a.md": 3}),
            ("agents", "trw-lead.md"),
            ("custom_hooks", [1]),
            ("packages", ["trw-mcp"]),
            ("packages", {"trw-mcp": None}),
            ("owners", ["a.md"]),
            ("owners", {"a.md": "claude-code"}),
            ("owners", {"a.md": [1]}),
            ("tombstones", {"a.md": "oops"}),
            ("tombstones", [1]),
        ],
    )
    def test_parseable_manifest_with_a_malformed_field_refuses(self, field: str, value: object, tmp_path: Path) -> None:
        """A current-schema manifest whose ownership fields are the wrong type refuses too.

        The reader coerces a wrong-typed field to empty, so an update would rewrite
        the ownership record from nothing (codex review of 14ae6dd34).
        """
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        repo = _init_all_clients(tmp_path)
        manifest_path = repo / ".trw" / "managed-artifacts.yaml"
        data = FileStateReader().read_yaml(manifest_path)
        if value is None:
            del data[field]
        else:
            data[field] = value
        FileStateWriter().write_yaml(manifest_path, data)

        self._assert_refuses_without_side_effects(repo)
        assert repr(field) in update_project(repo)["errors"][0]

    @pytest.mark.parametrize("literal", ["2.0", "true", "'2'"])
    def test_a_version_that_is_not_an_int_refuses(self, literal: str, tmp_path: Path) -> None:
        """Codex round 2: YAML ``2.0`` equals ``2`` in Python and passed the check, then
        crashed the reader's ``int(str(...))``."""
        repo = _init_all_clients(tmp_path)
        manifest = repo / ".trw" / "managed-artifacts.yaml"
        text = manifest.read_text(encoding="utf-8")
        assert "version: 2\n" in text
        manifest.write_text(text.replace("version: 2\n", f"version: {literal}\n", 1), encoding="utf-8")

        self._assert_refuses_without_side_effects(repo)
        assert "unsupported schema version" in update_project(repo)["errors"][0]

    @pytest.mark.parametrize("version", [1, 99])
    def test_unsupported_schema_version_refuses(self, version: int, tmp_path: Path) -> None:
        """An otherwise-valid manifest with a schema version this build does not
        read is refused exactly like a missing/corrupt one — no legacy reader."""
        repo = _init_all_clients(tmp_path)
        manifest = repo / ".trw" / "managed-artifacts.yaml"
        from trw_mcp.bootstrap._version_manifest import MANIFEST_VERSION

        original = manifest.read_text(encoding="utf-8")
        current = f"version: {MANIFEST_VERSION}"
        assert current in original, "non-vacuity: the real manifest must carry the current version"
        downgraded = original.replace(current, f"version: {version}", 1)
        manifest.write_text(downgraded, encoding="utf-8")

        self._assert_refuses_without_side_effects(repo)

    def test_dry_run_also_refuses(self, tmp_path: Path) -> None:
        repo = _init_all_clients(tmp_path)
        (repo / ".trw" / "managed-artifacts.yaml").unlink()
        before = _snapshot_tree(repo)

        result = update_project(repo, dry_run=True)

        assert len(result["errors"]) == 1
        assert "refusing to update" in result["errors"][0]
        assert "clean reinstall" in result["errors"][0]
        assert _snapshot_tree(repo) == before

    def test_fresh_install_in_new_target_succeeds(self, tmp_path: Path) -> None:
        """A brand-new ``init_project`` writes a current-schema manifest, so the very
        next ``update_project`` on that target succeeds with no errors — the
        refusal is specific to an existing installation with a bad manifest,
        not a blanket block on updates."""
        repo = _init_all_clients(tmp_path)
        manifest = repo / ".trw" / "managed-artifacts.yaml"
        from trw_mcp.bootstrap._version_manifest import MANIFEST_VERSION

        assert f"version: {MANIFEST_VERSION}" in manifest.read_text(encoding="utf-8")

        result = update_project(repo)
        assert not result["errors"], result["errors"]


class TestOpencodeDistillOwnership:
    """PRD-INFRA-192 FR12: the opencode distill files are recorded by the one recorder registry.

    Their installer used to keep its own ``commands``/``explorer_agent`` keys,
    which ``_write_manifest`` dropped, so every update ran with no baseline and
    overwrote a user's edit to ``.opencode/commands/trw-*.md``.
    """

    _COMMANDS = (
        ".opencode/commands/trw-before-edit.md",
        ".opencode/commands/trw-distill-hotspots.md",
        ".opencode/commands/trw-distill-conventions.md",
    )

    def _init_opencode(self, root: Path) -> Path:
        repo = root / "proj"
        (repo / ".git").mkdir(parents=True)
        assert not init_project(repo, ide="opencode")["errors"]
        return repo

    def test_user_edits_survive_repeated_updates(self, tmp_path: Path) -> None:
        repo = self._init_opencode(tmp_path)
        edited = {rel: (repo / rel).read_bytes() + b"\nmy note\n" for rel in self._COMMANDS}
        for rel, body in edited.items():
            (repo / rel).write_bytes(body)

        for _ in range(2):
            assert not update_project(repo, ide="opencode")["errors"]
            assert {rel: (repo / rel).read_bytes() for rel in self._COMMANDS} == edited

    def test_manifest_records_them_as_trw_owned_and_nowhere_else(self, tmp_path: Path) -> None:
        from trw_mcp.state.persistence import FileStateReader

        repo = self._init_opencode(tmp_path)
        assert not update_project(repo, ide="opencode")["errors"]
        manifest = FileStateReader().read_yaml(repo / ".trw" / "managed-artifacts.yaml")

        content_hashes = manifest["content_hashes"]
        assert isinstance(content_hashes, dict)
        for rel in self._COMMANDS:
            assert content_hashes[rel] == hashlib.sha256((repo / rel).read_bytes()).hexdigest()
        assert "commands" not in manifest
        assert "explorer_agent" not in manifest
        assert not {Path(rel).name for rel in self._COMMANDS} & set(manifest["custom_opencode_commands"])

    def test_recorded_stale_command_is_refreshed(self, tmp_path: Path) -> None:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        repo = self._init_opencode(tmp_path)
        rel = self._COMMANDS[0]
        current = (repo / rel).read_bytes()
        (repo / rel).write_bytes(b"an older TRW body\n")
        manifest_path = repo / ".trw" / "managed-artifacts.yaml"
        manifest = FileStateReader().read_yaml(manifest_path)
        manifest["content_hashes"][rel] = hashlib.sha256(b"an older TRW body\n").hexdigest()
        FileStateWriter().write_yaml(manifest_path, manifest)

        assert not update_project(repo, ide="opencode")["errors"]
        assert (repo / rel).read_bytes() == current


class TestOpencodeDistillWriteFailure:
    """Codex round 2: a distill file the installer failed to write returned ``status: error``
    that never reached ``result['errors']``, so init reported success and wrote a manifest."""

    def test_failed_first_install_reports_and_writes_no_manifest(self, tmp_path: Path) -> None:
        repo = tmp_path / "proj"
        (repo / ".git").mkdir(parents=True)
        (repo / ".opencode" / "commands" / "trw-before-edit.md").mkdir(parents=True)

        errors = init_project(repo, ide="opencode")["errors"]

        assert any("trw-before-edit.md not written" in error for error in errors), errors
        assert not (repo / ".trw" / "managed-artifacts.yaml").exists()

    def test_failed_explorer_write_on_first_install_reports_and_writes_no_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("trw_mcp.bootstrap._distill_entitlement.distill_artifacts_entitled", lambda **_kwargs: True)
        repo = tmp_path / "proj"
        (repo / ".git").mkdir(parents=True)
        (repo / ".opencode" / "agents" / "trw-distill-explorer.md").mkdir(parents=True)

        errors = init_project(repo, ide="opencode")["errors"]

        assert any("explorer agent not written" in error for error in errors), errors
        assert not (repo / ".trw" / "managed-artifacts.yaml").exists()

    def test_failed_write_during_update_restores_prior_files_and_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        repo = tmp_path / "proj"
        (repo / ".git").mkdir(parents=True)
        assert not init_project(repo, ide="opencode")["errors"]
        # Non-vacuity: a successful update would rewrite the manifest's packages
        # and refresh this TRW-recorded stale command.
        monkeypatch.setattr(
            "trw_mcp.bootstrap._version_manifest.resolved_package_versions",
            lambda: {"trw-mcp": "99.0.0", "trw-memory": "98.0.0"},
        )
        hotspots = repo / ".opencode" / "commands" / "trw-distill-hotspots.md"
        hotspots.write_text("an older TRW body\n", encoding="utf-8")
        manifest_path = repo / ".trw" / "managed-artifacts.yaml"
        manifest = FileStateReader().read_yaml(manifest_path)
        manifest["content_hashes"][".opencode/commands/trw-distill-hotspots.md"] = hashlib.sha256(
            b"an older TRW body\n"
        ).hexdigest()
        FileStateWriter().write_yaml(manifest_path, manifest)
        target = repo / ".opencode" / "commands" / "trw-before-edit.md"
        target.unlink()
        target.mkdir()
        before = _snapshot_tree(repo)

        result = update_project(repo, ide="opencode")

        assert any("trw-before-edit.md not written" in error for error in result["errors"]), result["errors"]
        assert _snapshot_tree(repo) == before


class TestManifestRecordsPackages:
    """PRD-INFRA-192 FR12: the manifest's ``packages`` is the one record of resolved versions."""

    def test_init_and_update_record_the_interpreter_versions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib.metadata

        from trw_mcp.state.persistence import FileStateReader

        repo = _init_all_clients(tmp_path)
        manifest = repo / ".trw" / "managed-artifacts.yaml"
        assert FileStateReader().read_yaml(manifest)["packages"] == {
            "trw-mcp": importlib.metadata.version("trw-mcp"),
            "trw-memory": importlib.metadata.version("trw-memory"),
        }

        monkeypatch.setattr(
            "trw_mcp.bootstrap._version_manifest.resolved_package_versions",
            lambda: {"trw-mcp": "99.0.0", "trw-memory": "98.0.0"},
        )
        assert not update_project(repo)["errors"]
        assert FileStateReader().read_yaml(manifest)["packages"] == {"trw-mcp": "99.0.0", "trw-memory": "98.0.0"}

    def test_failed_update_does_not_claim_the_new_versions_and_a_retry_does(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.bootstrap import _update_project

        repo = _init_all_clients(tmp_path)
        manifest = repo / ".trw" / "managed-artifacts.yaml"
        before = manifest.read_bytes()
        monkeypatch.setattr(
            "trw_mcp.bootstrap._version_manifest.resolved_package_versions",
            lambda: {"trw-mcp": "99.0.0", "trw-memory": "98.0.0"},
        )
        real_phases = _update_project._run_post_update_phases

        def failing(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(_update_project, "_run_post_update_phases", failing)
        assert update_project(repo)["errors"]
        assert manifest.read_bytes() == before

        monkeypatch.setattr(_update_project, "_run_post_update_phases", real_phases)
        assert not update_project(repo)["errors"]
        assert "99.0.0" in manifest.read_text(encoding="utf-8")

    def test_init_that_reported_errors_writes_no_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing_integrations(*args: object, result: dict[str, list[str]], **kwargs: object) -> None:
            result["errors"].append("integration failed")

        monkeypatch.setattr("trw_mcp.bootstrap._init_project.run_install_integrations", failing_integrations)
        repo = tmp_path / "proj"
        (repo / ".git").mkdir(parents=True)
        assert init_project(repo, ide="claude-code")["errors"]
        assert not (repo / ".trw" / "managed-artifacts.yaml").exists()

        refusal = update_project(repo)["errors"]
        assert len(refusal) == 1
        assert "is missing" in refusal[0]
        assert "trw-mcp uninstall --keep-memory" in refusal[0]

    def test_version_yaml_carries_no_package_stamp(self, tmp_path: Path) -> None:
        repo = _init_all_clients(tmp_path)
        (repo / ".trw" / "frameworks" / "VERSION.yaml").write_text(
            (repo / ".trw" / "frameworks" / "VERSION.yaml").read_text(encoding="utf-8")
            + "trw_mcp_version: 0.0.1\ntrw_memory_version: 0.0.1\n",
            encoding="utf-8",
        )
        (repo / ".trw" / "frameworks" / "FRAMEWORK.md").write_text("stale\n", encoding="utf-8")
        assert not update_project(repo)["errors"]
        stamp = (repo / ".trw" / "frameworks" / "VERSION.yaml").read_text(encoding="utf-8")
        assert "trw_mcp_version" not in stamp
        assert "trw_memory_version" not in stamp


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
    """A writable copy of the canonical skills bundle, wired in as the live source.

    Every client now renders from the one canonical corpus
    (``_client_skills.canonical_skills_dir()``, PRD-CORE-291-FR04); redirecting
    it is how a test drops a file from a skill that stays bundled — the shape
    the directory-granular cleanup cannot see. Patched at
    ``trw_mcp.bootstrap._client_skills`` (the consumer site imports it via a
    local ``from ._client_skills import canonical_skills_dir`` inside each
    call, so the module attribute is what's actually looked up).
    """
    from trw_mcp.bootstrap import _client_skills

    fake = tmp_path_factory.mktemp("codex-bundle") / "skills"
    shutil.copytree(_client_skills.canonical_skills_dir(), fake)
    monkeypatch.setattr(_client_skills, "canonical_skills_dir", lambda: fake)
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

        from trw_mcp.bootstrap._client_skills import render_skill_md

        assert not dropped.exists(), "the orphaned file survived the sweep"
        expected = render_skill_md(bundled_skill_md.read_text(encoding="utf-8"), "codex").encode("utf-8")
        assert skill_md.read_bytes() == expected, (
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

        # Must be the REMOVAL of the CODEX copy, named exactly: the claude /
        # copilot / cursor mirrors all carry a file of that name.
        assert _DROPPED_KEY in result["cleaned"], f"the sweep did not run: {result['cleaned']}"

    def test_dry_run_reports_the_removal_without_deleting(self, tmp_path: Path, codex_bundle: Path) -> None:
        repo = _init_all_clients(tmp_path)
        (codex_bundle / "trw-audit" / "audit-framework.md").unlink()

        result = update_project(repo, dry_run=True)

        assert (repo / _DROPPED_KEY).is_file()
        assert _DROPPED_KEY in result["cleaned"]

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


# ---------------------------------------------------------------------------
# PRD-INFRA-190 FR04: never overwrite uncommitted work the installer did not write
# FR07: a dropped content_hashes key is reported
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True
    )


def _committed_install(root: Path, *, exclude: str | None = None) -> Path:
    """A real git repo, installed and settled, committed except for *exclude*."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    assert not init_project(root, ide="claude-code")["errors"]
    assert not update_project(root)["errors"]
    _git(root, "add", "-A", "--", ".", *([f":!{exclude}"] if exclude else []))
    _git(root, "commit", "-qm", "installed")
    return root


def _next_bundle(tmp_path: Path) -> Path:
    """Bundle N+1: a new settings.json env key and a changed hook."""
    import json

    from trw_mcp.bootstrap import _DATA_DIR

    bundle = tmp_path / "bundle"
    shutil.copytree(_DATA_DIR, bundle)
    settings = bundle / "settings.json"
    data = json.loads(settings.read_text(encoding="utf-8"))
    data.setdefault("env", {})["TRW_BUNDLE_NEXT"] = "1"
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    hook = bundle / "hooks" / "session-start.sh"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# bundle N+1\n", encoding="utf-8")
    return bundle


class TestUncommittedChangesAreNeverOverwritten:
    """FR04, for a modified, a staged and an untracked file."""

    @pytest.mark.parametrize("state", ["modified", "staged", "untracked"])
    def test_uncommitted_edit_the_installer_did_not_record_is_preserved(self, tmp_path: Path, state: str) -> None:
        rel = ".claude/settings.json"
        repo = _committed_install(tmp_path / "repo", exclude=rel if state == "untracked" else None)
        settings = repo / rel
        edited = settings.read_text(encoding="utf-8").replace('"env": {', '"env": {\n    "MY_OWN_FLAG": "1",', 1)
        assert edited != settings.read_text(encoding="utf-8"), "fixture: the edit must change the file"
        settings.write_text(edited, encoding="utf-8")
        if state == "staged":
            _git(repo, "add", rel)

        result = update_project(repo, data_dir=_next_bundle(tmp_path))

        assert not result["errors"], result["errors"]
        assert settings.read_text(encoding="utf-8") == edited
        assert f"{rel} (uncommitted_changes)" in result["preserved"]
        assert rel not in result["updated"]

    @pytest.mark.parametrize("state", ["modified", "staged", "untracked"])
    def test_uncommitted_bytes_the_installer_recorded_are_refreshed(self, tmp_path: Path, state: str) -> None:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        rel = ".claude/hooks/session-start.sh"
        repo = _committed_install(tmp_path / "repo", exclude=rel if state == "untracked" else None)
        hook = repo / rel
        written = hook.read_text(encoding="utf-8") + "\n# what the installer wrote last time\n"
        hook.write_text(written, encoding="utf-8")
        if state == "staged":
            _git(repo, "add", rel)
        manifest_path = repo / ".trw" / "managed-artifacts.yaml"
        manifest = FileStateReader().read_yaml(manifest_path)
        manifest["content_hashes"]["session-start.sh"] = hashlib.sha256(written.encode("utf-8")).hexdigest()
        FileStateWriter().write_yaml(manifest_path, manifest)
        bundle = _next_bundle(tmp_path)

        result = update_project(repo, data_dir=bundle)

        assert not result["errors"], result["errors"]
        assert hook.read_bytes() == (bundle / "hooks" / "session-start.sh").read_bytes()
        assert rel in result["updated"]


def test_a_user_edited_agent_produces_exactly_one_dropped_key_warning(tmp_path: Path) -> None:
    """FR07: the recorder's omission is visible, named, and says why."""
    repo = _committed_install(tmp_path / "repo")
    agent = repo / ".claude" / "agents" / "trw-implementer.md"
    agent.write_text(agent.read_text(encoding="utf-8") + "\n<!-- my note -->\n", encoding="utf-8")

    result = update_project(repo)

    dropped = [w for w in result["warnings"] if w.startswith("manifest_key_dropped:")]
    assert dropped == ["manifest_key_dropped: trw-implementer.md (user_edited)"]


class TestOwnersRecorded:
    """PRD-INFRA-192 FR12: ``owners`` records which client(s) own each key."""

    def test_init_records_owners_per_declared_client(self, tmp_path: Path) -> None:
        """``init --ide claude-code`` then ``update --ide codex``: ``.claude/hooks`` is shared, ``.claude/skills`` is not."""
        from trw_mcp.state.persistence import FileStateReader

        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".git").mkdir()
        assert not init_project(repo, ide="claude-code")["errors"]
        assert not update_project(repo, ide="codex")["errors"]

        manifest = FileStateReader().read_yaml(repo / ".trw" / "managed-artifacts.yaml")
        owners = manifest["owners"]
        hook_keys = [k for k in owners if (repo / ".claude" / "hooks" / k).is_file()]
        assert hook_keys, "precondition: at least one hook key recorded"
        for key in hook_keys:
            assert sorted(owners[key]) == ["claude-code", "codex"], (key, owners[key])

        skill_keys = [k for k in owners if k.endswith("/SKILL.md") and (repo / ".claude" / "skills" / k).is_file()]
        assert skill_keys, "precondition: at least one skill key recorded"
        for key in skill_keys:
            assert owners[key] == ["claude-code"], (key, owners[key])

    def test_update_evaluates_owners_at_write_time_including_the_ide_override(self, tmp_path: Path) -> None:
        """``update --ide codex`` on a claude-code project: codex joins ``.claude/hooks``' owners."""
        from trw_mcp.state.persistence import FileStateReader

        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".git").mkdir()
        assert not init_project(repo, ide="claude-code")["errors"]

        manifest_before = FileStateReader().read_yaml(repo / ".trw" / "managed-artifacts.yaml")
        hook_key = next(k for k in manifest_before["owners"] if (repo / ".claude" / "hooks" / k).is_file())
        assert manifest_before["owners"][hook_key] == ["claude-code"]

        assert not update_project(repo, ide="codex")["errors"]

        manifest_after = FileStateReader().read_yaml(repo / ".trw" / "managed-artifacts.yaml")
        # codex declares .claude/hooks too (shared hook-command surface), and by
        # the time _write_manifest runs, target_platforms already includes codex
        # (PRD-INFRA-192 FR12: evaluated at write time, not before the update).
        assert sorted(manifest_after["owners"][hook_key]) == ["claude-code", "codex"]

    def test_leftover_key_no_recorded_client_declares_gets_empty_owners(self, tmp_path: Path) -> None:
        """A key whose path no client in the run set covers gets ``owners: []`` and is kept on disk."""
        from trw_mcp.bootstrap._client_ownership import owners_for_content_hashes

        owners = owners_for_content_hashes({"stale-agent.md": "deadbeef"}, ["opencode"])
        assert owners == {"stale-agent.md": []}

    def test_owners_map_survives_an_update_that_adds_no_new_client(self, tmp_path: Path) -> None:
        """A bare ``update-project`` (no --ide) still (re)computes owners from the recorded set."""
        from trw_mcp.state.persistence import FileStateReader

        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".git").mkdir()
        assert not init_project(repo, ide="claude-code")["errors"]
        assert not update_project(repo)["errors"]

        manifest = FileStateReader().read_yaml(repo / ".trw" / "managed-artifacts.yaml")
        hook_key = next(k for k in manifest["owners"] if (repo / ".claude" / "hooks" / k).is_file())
        assert manifest["owners"][hook_key] == ["claude-code"]
