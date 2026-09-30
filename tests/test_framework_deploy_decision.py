"""CODEX-P0-C: one deploy decision for both deploy paths, and an older package never writes over a newer one.

Real files through ``init_project``/``update_project`` (the CLI path) and ``_deploy_frameworks`` (the tool path); no mocks
of the decision itself.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project

FRAMEWORK = Path(".trw/frameworks/FRAMEWORK.md")
RECEIPT = Path(".trw/frameworks/DEPLOYMENT.json")

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt(project: Path) -> dict[str, object]:
    return json.loads((project / RECEIPT).read_text(encoding="utf-8"))


def _set_receipt(project: Path, **fields: object) -> None:
    receipt = _receipt(project)
    receipt.update(fields)
    (project / RECEIPT).write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _decide(project: Path, package_version: str):
    from trw_mcp.bootstrap._utils import _DATA_DIR
    from trw_mcp.canons.registry import bundled_manifest_bytes, load_registry
    from trw_mcp.framework_decision import deploy_decision
    from trw_mcp.models.config import get_config

    config = get_config()
    return deploy_decision(
        project,
        framework_source=(_DATA_DIR / "framework.md").read_text(encoding="utf-8"),
        aaref_source=(_DATA_DIR / "aaref.md").read_text(encoding="utf-8"),
        framework_version=config.framework_version,
        aaref_version=config.aaref_version,
        registry_digest=load_registry(bundled_manifest_bytes()).digest,
        package_version=package_version,
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("TRW_FRAMEWORK_FORCE_DEPLOY", raising=False)
    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    return tmp_path


def _newer_build_of_the_same_framework(project: Path, package_version: str) -> bytes:
    """The scenario of the report: same framework stamp, different (newer) bytes, and a receipt that matches the disk."""
    body = project / FRAMEWORK
    body.write_bytes(body.read_bytes() + b"\n<!-- a newer build of this framework version -->\n")
    receipt = _receipt(project)
    receipt["artifact_digests"][FRAMEWORK.as_posix()] = _sha(body)  # type: ignore[index]
    receipt["package_version"] = package_version
    (project / RECEIPT).write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return body.read_bytes()


def test_a_deploy_records_the_package_version_in_the_receipt(project: Path) -> None:
    from trw_mcp import __version__

    assert _receipt(project)["package_version"] == __version__


def test_the_package_version_does_not_change_the_generation_id(project: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / ".git").mkdir()
    assert not init_project(other, ide="claude-code")["errors"]
    from trw_mcp.framework_integrity import repair_framework_runtime

    before = _receipt(project)["generation_id"]
    from trw_mcp.bootstrap._utils import _DATA_DIR
    from trw_mcp.canons.registry import bundled_manifest_bytes, load_registry
    from trw_mcp.models.config import get_config

    config = get_config()
    repair_framework_runtime(
        other,
        framework_source=(_DATA_DIR / "framework.md").read_text(encoding="utf-8"),
        aaref_source=(_DATA_DIR / "aaref.md").read_text(encoding="utf-8"),
        framework_version=config.framework_version,
        aaref_version=config.aaref_version,
        registry_digest=load_registry(bundled_manifest_bytes()).digest,
        package_version="0.0.1",
    )
    assert _receipt(other)["generation_id"] == before and _receipt(other)["package_version"] == "0.0.1"


@pytest.mark.parametrize(
    ("deployed", "running", "stale"),
    [
        ("8.1.0", "8.1.0.dev26", True),  # the release outranks its own dev builds
        ("8.1.0", "8.1.0rc1", True),
        ("8.1.0.dev26", "8.1.0", False),  # a release upgrades a dev deployment
        ("8.1.0", "8.1.0", False),
        ("8.1.0", "8.1.1", False),
        ("8.10.0", "8.9.0", True),  # a string compare would order these the other way round
        ("8.9.0", "8.10.0", False),
        ("8.1.0.post1", "8.1.0", True),
    ],
)
def test_the_package_watermark_orders_versions_by_pep_440(
    project: Path, deployed: str, running: str, stale: bool
) -> None:
    _set_receipt(project, package_version=deployed)
    (project / FRAMEWORK).write_bytes(b"differs, so the generation is not current\n")

    decision = _decide(project, running)

    assert (decision.action == "skip_stale") is stale


def test_an_unparseable_or_absent_watermark_never_blocks(project: Path) -> None:
    (project / FRAMEWORK).write_bytes(b"differs\n")
    _set_receipt(project, package_version="not a version")
    assert _decide(project, "8.1.0").action == "deploy"
    receipt = _receipt(project)
    del receipt["package_version"]
    (project / RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
    assert _decide(project, "8.1.0").action == "deploy"  # a legacy receipt behaves as before


def test_update_project_leaves_a_newer_build_of_the_same_framework_version_alone(project: Path) -> None:
    kept = _newer_build_of_the_same_framework(project, "999.0.0")

    result = update_project(project)

    assert (project / FRAMEWORK).read_bytes() == kept
    (warning,) = [w for w in result["warnings"] if "Framework deploy skipped" in w]
    assert "last deployed this project's framework" in warning and "DEPLOYMENT.json package_version" in warning
    assert "TRW_FRAMEWORK_FORCE_DEPLOY=1" in warning


def test_the_tool_path_leaves_it_alone_too(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools import _orchestration_helpers as helpers

    kept = _newer_build_of_the_same_framework(project, "999.0.0")
    monkeypatch.chdir(project)

    result = helpers._deploy_frameworks(project / ".trw")

    assert result["status"] == "skipped_stale_package" and result["deployed"] == "999.0.0"
    assert (project / FRAMEWORK).read_bytes() == kept


def test_an_older_deployment_is_upgraded_by_a_newer_package(project: Path) -> None:
    _newer_build_of_the_same_framework(project, "0.0.1")

    update_project(project)

    from trw_mcp.bootstrap._utils import _DATA_DIR

    assert (project / FRAMEWORK).read_bytes() == (_DATA_DIR / "framework.md").read_bytes()


def test_the_force_switch_overrides_the_watermark(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _newer_build_of_the_same_framework(project, "999.0.0")
    monkeypatch.setenv("TRW_FRAMEWORK_FORCE_DEPLOY", "1")

    update_project(project)

    from trw_mcp.bootstrap._utils import _DATA_DIR

    assert (project / FRAMEWORK).read_bytes() == (_DATA_DIR / "framework.md").read_bytes()


def test_a_project_without_a_config_file_is_current_not_redeployed_every_time(project: Path) -> None:
    (project / ".trw/config.yaml").unlink()

    assert _decide(project, "999.0.0").action == "current"
    stamp = (project / ".trw/frameworks/VERSION.yaml").read_bytes()
    update_project(project)
    assert (project / ".trw/frameworks/VERSION.yaml").read_bytes() == stamp, "deployed_at must not move"


def test_both_paths_agree_on_an_untouched_project(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools import _orchestration_helpers as helpers

    before = {p: _sha(project / p) for p in (FRAMEWORK, RECEIPT)}
    monkeypatch.chdir(project)

    assert helpers._deploy_frameworks(project / ".trw")["status"] == "up_to_date"
    update_project(project)

    assert {p: _sha(project / p) for p in (FRAMEWORK, RECEIPT)} == before


def test_the_replaced_edit_warning_names_the_file_the_saved_copy_and_a_restore_command_that_works(
    project: Path,
) -> None:
    edited = b"# my own framework\n"
    (project / FRAMEWORK).write_bytes(edited)

    result = update_project(project)

    (warning,) = [w for w in result["warnings"] if "Framework canon replaced" in w]
    assert "FRAMEWORK.md" in warning and ".trw/frameworks/.rollback/" in warning
    command = next(
        part.strip() for part in warning.split("run: ")[1].split(" (")[0].split("; ") if "FRAMEWORK.md" in part
    )
    assert command.startswith("cp .trw/frameworks/.rollback/") and command.endswith(" .trw/frameworks/FRAMEWORK.md")
    subprocess.run(command, shell=True, cwd=project, check=True)
    assert (project / FRAMEWORK).read_bytes() == edited


def test_doctor_shows_the_saved_copy_until_the_rollback_directory_is_deleted(project: Path) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.server._doctor_framework_integrity import check_framework_integrity

    config = get_config()

    def doctor() -> tuple[str, str]:
        return check_framework_integrity(
            project, framework_version=config.framework_version, aaref_version=config.aaref_version
        )

    (project / FRAMEWORK).write_bytes(b"# my own framework\n")
    update_project(project)

    status, message = doctor()
    assert status == "PASS" and "kept your bytes in .trw/frameworks/.rollback/" in message and "cp " in message

    import shutil

    shutil.rmtree(project / ".trw/frameworks/.rollback")
    status, message = doctor()
    assert status == "PASS" and "kept your bytes" not in message


def test_a_restore_command_quotes_a_hostile_snapshot_directory_name(project: Path) -> None:
    from trw_mcp.bootstrap._framework_modified_guard import restore_command

    hostile = project / ".trw/frameworks/.rollback/x-$(touch pwned);y-20260930T000000000000Z"
    (hostile / ".trw/frameworks").mkdir(parents=True)
    (hostile / ".trw/frameworks/FRAMEWORK.md").write_bytes(b"saved\n")

    command = restore_command(project, hostile, ".trw/frameworks/FRAMEWORK.md")
    subprocess.run(command, shell=True, cwd=project, check=True)

    assert not (project / "pwned").exists()
    assert (project / FRAMEWORK).read_bytes() == b"saved\n"


def test_a_later_clean_deploy_does_not_hide_an_earlier_saved_edit_and_a_plain_upgrade_is_not_an_edit(
    project: Path,
) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.server._doctor_framework_integrity import check_framework_integrity

    config = get_config()

    def note() -> str:
        return check_framework_integrity(
            project, framework_version=config.framework_version, aaref_version=config.aaref_version
        )[1]

    body = project / FRAMEWORK
    body.write_text("older canon body\n", encoding="utf-8")  # a plain upgrade: the receipt records these bytes
    _set_receipt(project, artifact_digests={**_receipt(project)["artifact_digests"], FRAMEWORK.as_posix(): _sha(body)})  # type: ignore[dict-item]
    update_project(project)
    assert "kept your bytes" not in note(), "an ordinary upgrade is not reported as a user edit"

    body.write_bytes(b"# my own framework\n")
    update_project(project)
    assert "kept your bytes" in note()

    body.write_text("older canon body again\n", encoding="utf-8")  # a later plain upgrade makes a newer snapshot
    _set_receipt(project, artifact_digests={**_receipt(project)["artifact_digests"], FRAMEWORK.as_posix(): _sha(body)})  # type: ignore[dict-item]
    update_project(project)

    assert "kept your bytes" in note(), "the earlier edit is still shown"


def test_the_warning_points_at_the_snapshot_that_holds_the_replaced_bytes_not_merely_the_newest(project: Path) -> None:
    from trw_mcp.bootstrap._framework_modified_guard import modified_warning, snapshot_holding

    edited = b"# my own framework\n"
    (project / FRAMEWORK).write_bytes(edited)
    digest = hashlib.sha256(edited).hexdigest()
    update_project(project)
    holder = snapshot_holding(project, FRAMEWORK.as_posix(), digest)
    assert holder is not None
    newer = (
        project / ".trw/frameworks/.rollback/other-deploy-29990101T000000000000Z"
    )  # another deploy's snapshot, newer
    (newer / ".trw/frameworks").mkdir(parents=True)
    (newer / FRAMEWORK).write_bytes(b"someone else's bytes\n")

    warning = modified_warning([FRAMEWORK.as_posix()], project, {FRAMEWORK.as_posix(): digest})

    assert holder.name in warning and "other-deploy" not in warning
