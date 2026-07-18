"""INFRA-164 atomic deployment, interruption recovery, and rollback evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH, deploy_framework_generation
from trw_mcp.framework_integrity import inspect_framework_runtime, repair_framework_runtime, rollback_framework_runtime

FRAMEWORK_VERSION = "v26.1_TRW"
AAREF_VERSION = "v3.2.0"
FRAMEWORK = "v26.1_TRW — MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK\n"
AAREF = "# AARE-F\n\n**Version**: 3.2.0\n"


def _repair(target: Path, *, framework: str = FRAMEWORK, fail_after: int | None = None):
    return repair_framework_runtime(
        target,
        framework_source=framework,
        aaref_source=AAREF,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
        registry_digest="registry-v1",
        failure_after_promotions=fail_after,
    )


def test_deployment_receipt_binds_every_promoted_artifact(tmp_path: Path) -> None:
    report = _repair(tmp_path)
    assert report.ok, report.errors

    receipt = json.loads((tmp_path / DEPLOYMENT_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert receipt["registry_digest"] == "registry-v1"
    for relative, expected in receipt["artifact_digests"].items():
        assert hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest() == expected

    (tmp_path / ".trw/frameworks/FRAMEWORK.md").write_text(FRAMEWORK + "drift", encoding="utf-8")
    drifted = inspect_framework_runtime(
        tmp_path,
        framework_source=FRAMEWORK,
        aaref_source=AAREF,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
        registry_digest="registry-v1",
    )
    assert any("receipt artifact digest mismatch" in error for error in drifted.errors)


@pytest.mark.parametrize("fail_after", [1, 2, 3])
def test_interrupted_promotion_restores_complete_previous_generation(tmp_path: Path, fail_after: int) -> None:
    _repair(tmp_path)
    tracked = (
        Path(".trw/frameworks/FRAMEWORK.md"),
        Path(".trw/frameworks/AARE-F-FRAMEWORK.md"),
        Path(".trw/frameworks/VERSION.yaml"),
        DEPLOYMENT_RELATIVE_PATH,
    )
    before = {path: (tmp_path / path).read_bytes() for path in tracked}

    with pytest.raises(OSError, match="injected framework deployment failure"):
        _repair(tmp_path, framework=FRAMEWORK + "next generation\n", fail_after=fail_after)

    assert {path: (tmp_path / path).read_bytes() for path in tracked} == before


def test_explicit_rollback_restores_prior_generation_receipt_last(tmp_path: Path) -> None:
    first = deploy_framework_generation(
        tmp_path,
        artifacts={Path(".trw/frameworks/custom.md"): b"first\n"},
        registry_digest="r1",
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
    )
    first_receipt = (tmp_path / DEPLOYMENT_RELATIVE_PATH).read_bytes()
    second = deploy_framework_generation(
        tmp_path,
        artifacts={Path(".trw/frameworks/custom.md"): b"second\n"},
        registry_digest="r2",
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
    )
    assert first.generation_id != second.generation_id

    rollback_framework_runtime(tmp_path, second.rollback_id)

    assert (tmp_path / ".trw/frameworks/custom.md").read_bytes() == b"first\n"
    assert (tmp_path / DEPLOYMENT_RELATIVE_PATH).read_bytes() == first_receipt


def test_deployment_rejects_symlinked_management_boundary(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".trw").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="crosses symlink"):
        deploy_framework_generation(
            tmp_path,
            artifacts={Path(".trw/frameworks/FRAMEWORK.md"): b"candidate\n"},
            registry_digest="registry",
            framework_version=FRAMEWORK_VERSION,
            aaref_version=AAREF_VERSION,
        )

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "reserved",
    [
        DEPLOYMENT_RELATIVE_PATH,
        Path(".trw/frameworks/.deployment.lock"),
        Path(".trw/frameworks/.rollback"),
        Path(".trw/frameworks/.rollback/attacker/BACKUP.json"),
        Path(".trw/frameworks/.staging"),
        Path(".trw/frameworks/.staging/attacker/body"),
    ],
)
def test_deployment_rejects_reserved_control_paths(tmp_path: Path, reserved: Path) -> None:
    with pytest.raises(ValueError, match="deployment path is reserved"):
        deploy_framework_generation(
            tmp_path,
            artifacts={reserved: b"attacker-controlled"},
            registry_digest="registry",
            framework_version=FRAMEWORK_VERSION,
            aaref_version=AAREF_VERSION,
        )

    assert not (tmp_path / reserved).exists()
    assert not (tmp_path / DEPLOYMENT_RELATIVE_PATH).exists()
