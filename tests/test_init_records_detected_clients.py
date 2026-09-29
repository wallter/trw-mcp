"""Init records every client it installs artifacts for; update adopts hash-proven legacy installs.

The record (``.trw/config.yaml`` ``target_platforms``) is the ONE authority update, the uninstall
planner and the manifest ``owners`` field read. A bare init whose detection wrote ``.cursor/*`` used
to leave the record without cursor, so update never refreshed or retired those files.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import _optional_skills, init_project, update_project

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

CURSOR = "cursor-ide"


def _git_repo(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "i",
        ],
        check=True,
    )


def _put_fake_cursor_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "cursor"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / ".Trash").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for var in ("CURSOR_TRACE_ID", "CURSOR_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    # A host with cursor-agent installed would detect cursor-cli too; the test controls detection.
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}/bin")
    root = tmp_path / "proj"
    _git_repo(root)
    monkeypatch.setattr(_optional_skills, "skill_enabled", lambda *_a, **_k: True)
    return root


def _record(root: Path) -> list[str]:
    return list(yaml.safe_load((root / ".trw" / "config.yaml").read_text())["target_platforms"])


def _set_record(root: Path, clients: list[str]) -> None:
    path = root / ".trw" / "config.yaml"
    data = yaml.safe_load(path.read_text())
    data["target_platforms"] = clients
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _disable_assess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_optional_skills, "skill_enabled", lambda n, *_a, **_k: n != "trw-assess")


def _assess_copy(root: Path) -> Path:
    return root / ".cursor" / "skills" / "trw-assess" / "SKILL.md"


def test_bare_init_with_cursor_detected_records_it_and_update_retires_the_cursor_copy(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put_fake_cursor_on_path(tmp_path, monkeypatch)
    assert not init_project(env)["errors"]
    assert _assess_copy(env).is_file(), "precondition: detection installed the cursor copy"
    assert CURSOR in _record(env)

    _disable_assess(monkeypatch)
    update_project(env)

    assert not _assess_copy(env).exists()
    assert not (env / ".claude" / "skills" / "trw-assess" / "SKILL.md").exists()


def test_every_consumer_reads_the_same_record_after_bare_init(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap._client_ownership import update_write_targets
    from trw_mcp.bootstrap._utils import resolve_client_write_targets
    from trw_mcp.bootstrap._version_manifest import _read_manifest
    from trw_mcp.server._subcommands import _run_uninstall

    _put_fake_cursor_on_path(tmp_path, monkeypatch)
    assert not init_project(env)["errors"]

    assert CURSOR in resolve_client_write_targets(env)
    assert CURSOR in update_write_targets(env, None)
    manifest = _read_manifest(env)
    assert manifest is not None
    owners = manifest["owners"]
    assert isinstance(owners, dict)
    cursor_keys = [k for k, v in owners.items() if CURSOR in v]
    assert cursor_keys, "manifest owners attribute the cursor files to cursor-ide"

    # The uninstall planner: a per-client uninstall of cursor-ide finds those files via the record.
    ns = argparse.Namespace(
        target_dir=str(env), dry_run=False, yes=True, delete_memory=False, keep_memory=False, ide=CURSOR
    )
    _run_uninstall(ns)
    assert not _assess_copy(env).exists()
    assert CURSOR not in _record(env)


def test_update_adopts_a_hash_proven_cursor_install_missing_from_the_record(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put_fake_cursor_on_path(tmp_path, monkeypatch)
    assert not init_project(env)["errors"]
    _set_record(env, ["claude-code"])  # what the old init left behind
    assert _assess_copy(env).is_file()

    _disable_assess(monkeypatch)
    update_project(env)

    assert CURSOR in _record(env)
    assert not _assess_copy(env).exists()


def test_a_user_made_cursor_dir_is_not_adopted_and_left_untouched(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not init_project(env, ide="claude-code")["errors"]
    mine = env / ".cursor" / "rules" / "mine.mdc"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"my own rule\n")
    _put_fake_cursor_on_path(tmp_path, monkeypatch)  # detection would fire, but nothing TRW-hashed exists

    update_project(env)

    assert CURSOR not in _record(env)
    assert sorted(p.relative_to(env).as_posix() for p in (env / ".cursor").rglob("*") if p.is_file()) == [
        ".cursor/rules/mine.mdc"
    ]
    assert mine.read_bytes() == b"my own rule\n"


def test_an_edited_trw_cursor_file_is_not_proof_and_is_not_adopted(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put_fake_cursor_on_path(tmp_path, monkeypatch)
    assert not init_project(env)["errors"]
    _set_record(env, ["claude-code"])
    for f in (env / ".cursor").rglob("*"):
        if f.is_file():
            f.write_bytes(f.read_bytes() + b"\nuser edit\n")
    edited = {f: f.read_bytes() for f in (env / ".cursor").rglob("*") if f.is_file()}

    update_project(env)

    assert CURSOR not in _record(env)
    assert {f: f.read_bytes() for f in (env / ".cursor").rglob("*") if f.is_file()} == edited


def test_uninstalled_cursor_is_not_readopted_by_the_next_update(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.server._subcommands import _run_uninstall

    _put_fake_cursor_on_path(tmp_path, monkeypatch)
    assert not init_project(env)["errors"]
    ns = argparse.Namespace(
        target_dir=str(env), dry_run=False, yes=True, delete_memory=False, keep_memory=False, ide=CURSOR
    )
    _run_uninstall(ns)
    assert CURSOR not in _record(env)
    kept_by_user = {f for f in (env / ".cursor").rglob("*") if f.is_file()} if (env / ".cursor").exists() else set()

    update_project(env)

    assert CURSOR not in _record(env)
    after = {f for f in (env / ".cursor").rglob("*") if f.is_file()} if (env / ".cursor").exists() else set()
    assert after == kept_by_user


def test_no_cursor_detected_records_nothing_extra(env: Path) -> None:
    assert not init_project(env)["errors"]
    assert _record(env) == ["claude-code"]
    assert not (env / ".cursor").exists()
    update_project(env)
    assert _record(env) == ["claude-code"]
    assert not (env / ".cursor").exists()


def _manifest_path(root: Path) -> Path:
    return root / ".trw" / "managed-artifacts.yaml"


def _rewrite_hashes(root: Path, keep: callable[[str], bool]) -> dict[str, str]:  # type: ignore[valid-type]
    """Drop the recorded hashes ``keep`` rejects (an install whose manifest never covered cursor)."""
    data = yaml.safe_load(_manifest_path(root).read_text())
    data["content_hashes"] = {k: v for k, v in data["content_hashes"].items() if keep(k)}
    _manifest_path(root).write_text(yaml.safe_dump(data, sort_keys=False))
    return dict(data["content_hashes"])


def _orphan(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _put_fake_cursor_on_path(tmp_path, monkeypatch)
    assert not init_project(root)["errors"]
    _set_record(root, ["claude-code"])
    remaining = _rewrite_hashes(root, lambda k: not k.startswith(".cursor/"))
    assert not any(k.startswith(".cursor/") for k in remaining)


def test_orphaned_install_with_no_manifest_hashes_is_adopted_by_render_equality(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _orphan(env, tmp_path, monkeypatch)
    _disable_assess(monkeypatch)

    result = update_project(env)

    assert CURSOR in _record(env)
    assert not _assess_copy(env).exists(), {k: v for k, v in result.items() if "assess" in str(v)}


def test_one_edited_cursor_file_blocks_adoption_and_is_reported(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _orphan(env, tmp_path, monkeypatch)
    edited = env / ".cursor" / "skills" / "trw-audit" / "SKILL.md"
    edited.write_bytes(edited.read_bytes() + b"\nmine\n")
    before = edited.read_bytes()

    result = update_project(env)

    assert CURSOR not in _record(env)
    assert edited.read_bytes() == before
    assert any(CURSOR in w and ".cursor/skills/trw-audit/SKILL.md" in w for w in result["warnings"]), result["warnings"]


def test_a_missing_cursor_file_blocks_adoption_and_is_reported(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _orphan(env, tmp_path, monkeypatch)
    gone = env / ".cursor" / "skills" / "trw-audit" / "SKILL.md"
    gone.unlink()

    result = update_project(env)

    assert CURSOR not in _record(env)
    assert any(CURSOR in w and "trw-audit/SKILL.md is missing" in w for w in result["warnings"]), result["warnings"]


def test_mixed_hash_proven_and_render_equal_files_are_adopted(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put_fake_cursor_on_path(tmp_path, monkeypatch)
    assert not init_project(env)["errors"]
    _set_record(env, ["claude-code"])
    # An older TRW version's skill: differs from today's render but is exactly what the manifest recorded.
    old = env / ".cursor" / "skills" / "trw-audit" / "SKILL.md"
    old.write_bytes(old.read_bytes() + b"\nolder shipped text\n")
    import hashlib

    old_hash = hashlib.sha256(old.read_bytes()).hexdigest()
    _rewrite_hashes(env, lambda k: False)  # every other cursor file has no recorded hash: render-equal only
    data = yaml.safe_load(_manifest_path(env).read_text())
    data["content_hashes"][".cursor/skills/trw-audit/SKILL.md"] = old_hash
    _manifest_path(env).write_text(yaml.safe_dump(data, sort_keys=False))

    update_project(env)

    assert CURSOR in _record(env)


MDC = ".cursor/rules/trw-ceremony.mdc"


def test_a_git_ignored_edited_ceremony_rule_blocks_adoption_and_survives(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex round 1: every other file matched, the rule writer then replaced the user's ignored edits."""
    _orphan(env, tmp_path, monkeypatch)
    (env / ".gitignore").write_text(f"{MDC}\n")
    mdc = env / MDC
    mdc.write_bytes(mdc.read_bytes() + b"\nMY RULES, unique and ignored by git\n")
    before = mdc.read_bytes()

    result = update_project(env)

    assert CURSOR not in _record(env)
    assert mdc.read_bytes() == before
    assert any(CURSOR in w and MDC in w for w in result["warnings"]), result["warnings"]


def test_the_proof_set_is_the_set_of_files_the_real_writers_overwrite(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edit every file under .cursor, git-ignored so no dirty-guard hides a rewrite: the files the adoption
    proof lists must equal the files an explicit ``--ide cursor-ide`` update really rewrites."""
    from trw_mcp.bootstrap._client_adoption import writer_overwrites

    _put_fake_cursor_on_path(tmp_path, monkeypatch)
    assert not init_project(env)["errors"]
    (env / ".gitignore").write_text(".cursor/\n")
    files = [f for f in (env / ".cursor").rglob("*") if f.is_file() and f.suffix != ".json"]
    for f in files:
        f.write_bytes(f.read_bytes() + b"\nedit\n")
    edited = {f: f.read_bytes() for f in files}

    predicted = writer_overwrites(env, CURSOR, {})
    assert {f: f.read_bytes() for f in files} == edited, "listing the writers' targets must not touch the project"

    update_project(env, ide=CURSOR)
    rewritten = sorted(f.relative_to(env).as_posix() for f in files if f.read_bytes() != edited[f])

    assert MDC in predicted
    # Merge targets are kept out of the proof (their writers preserve user content); nothing else may differ.
    assert set(rewritten) - set(predicted) <= {".cursor/mcp.json", ".cursor/hooks.json", ".cursor/cli.json"}
    assert set(predicted) <= set(rewritten)


@pytest.mark.parametrize(
    ("client", "dirs"), [("codex", [".codex", ".agents"]), ("opencode", [".opencode"]), ("copilot", [".github"])]
)
def test_other_clients_writers_are_listed_and_an_edited_target_blocks_adoption(
    env: Path, client: str, dirs: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan for writers the proof misses, for every other client: whatever its writers rewrite is proven."""
    from trw_mcp.bootstrap._client_adoption import writer_overwrites

    monkeypatch.setattr("trw_mcp.bootstrap._utils.detect_ide", lambda _t: ["claude-code"])
    assert not init_project(env, ide=client)["errors"]
    (env / ".gitignore").write_text("".join(f"{d}/\n" for d in dirs))
    # Config files are merge targets: corrupting one makes the writers report it, which is the next test's case.
    files = [
        f
        for d in dirs
        if (env / d).is_dir()
        for f in (env / d).rglob("*")
        if f.is_file() and f.suffix not in {".json", ".toml"}
    ]
    for f in files:
        f.write_bytes(f.read_bytes() + b"\nedit\n")
    _set_record(env, ["claude-code"])
    _rewrite_hashes(env, lambda k: False)

    predicted = writer_overwrites(env, client, {})

    edited_rel = {f.relative_to(env).as_posix() for f in files}
    assert set(predicted) <= edited_rel
    if predicted:
        update_project(env)
        assert client not in _record(env)


def _orphaned_opencode(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("trw_mcp.bootstrap._utils.detect_ide", lambda _t: ["claude-code"])
    assert not init_project(root, ide="opencode")["errors"]
    _set_record(root, ["claude-code"])
    _rewrite_hashes(root, lambda k: not k.startswith(".opencode/"))
    (root / ".gitignore").write_text(".opencode/\n")
    from trw_mcp.bootstrap._managed_client_artifacts import MANAGED_CLIENT_ARTIFACT_SOURCES

    for source in MANAGED_CLIENT_ARTIFACT_SOURCES:  # explicit init leaves a render file absent; that alone blocks
        if source.client == "opencode":
            for rel, data in source.contents().items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(data)
    skill = next((root / ".opencode" / "skills").rglob("SKILL.md"))
    skill.write_bytes(skill.read_bytes() + b"\nmy edit, ignored by git\n")
    return skill


def test_a_later_opencode_stage_overwrite_blocks_adoption(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex r2: the check handed the real OpenCode updater an empty result, its first stage died on
    ``result["created"]`` and the five later stages never ran, so what they overwrite went unproven."""
    from trw_mcp.bootstrap import _opencode

    skill = _orphaned_opencode(env, monkeypatch)
    before = skill.read_bytes()
    real = _opencode.install_opencode_skills

    def clobbering_stage(target_dir: Path, **kwargs: object) -> dict[str, list[str]]:
        out = real(target_dir, **kwargs)  # type: ignore[arg-type]
        if target_dir != env:  # only the scratch run: the real update never reaches this file
            (target_dir / skill.relative_to(env)).write_bytes(b"clobbered by a later stage\n")
        return out  # type: ignore[no-any-return]

    monkeypatch.setattr(_opencode, "install_opencode_skills", clobbering_stage)

    result = update_project(env)

    assert "opencode" not in _record(env)
    assert skill.read_bytes() == before
    rel = skill.relative_to(env).as_posix()
    assert any("opencode" in w and rel in w for w in result["warnings"]), result["warnings"]


def test_a_writer_that_records_an_error_blocks_adoption_with_the_reason(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _opencode

    _orphaned_opencode(env, monkeypatch)
    monkeypatch.setattr(_opencode, "generate_opencode_config", lambda _t: {"errors": ["config merge exploded"]})

    result = update_project(env)

    assert "opencode" not in _record(env)
    assert any("could not be enumerated" in w and "config merge exploded" in w for w in result["warnings"]), result[
        "warnings"
    ]


def test_an_unrecognised_result_key_or_a_stage_warning_blocks_adoption(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _opencode, _update_project
    from trw_mcp.bootstrap._client_adoption import WritersNotEnumerable, writer_overwrites

    _orphaned_opencode(env, monkeypatch)

    def sets_a_key(_t: Path, result: dict[str, list[str]], *_a: object, **_k: object) -> None:
        result["mystery"] = ["x"]

    with monkeypatch.context() as patch:
        patch.setattr(_update_project, "_update_opencode_artifacts", sets_a_key)
        with pytest.raises(WritersNotEnumerable, match="mystery"):
            writer_overwrites(env, "opencode", {})

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr(_opencode, "install_opencode_commands", boom)
    with pytest.raises(WritersNotEnumerable, match="commands update skipped"):
        writer_overwrites(env, "opencode", {})
