"""INFRA-164 atomic deployment, interruption recovery, and rollback evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH, deploy_framework_generation
from trw_mcp.framework_integrity import inspect_framework_runtime, repair_framework_runtime, rollback_framework_runtime

FRAMEWORK_VERSION = "v26.2_TRW"
AAREF_VERSION = "v3.2.0"
FRAMEWORK = "v26.2_TRW — MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK\n"
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


# ── Receipt binding scope (L-QhRy fresh-install regression) ──────────────────
#
# Before this fix EVERY fresh bundle install failed its own `framework_integrity`
# doctor check: `repair_framework_runtime` bound `.trw/config.yaml` and
# `.trw/frameworks/VERSION.yaml` into the receipt, and `install-trw.py --script`
# rewrote both AFTER the receipt was promoted (persisting `target_platforms`,
# refreshing the stamp), so the digests could never match.


def _seed_config(target: Path, body: str) -> Path:
    config = target / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(body, encoding="utf-8")
    return config


def test_receipt_binds_canon_bodies_and_not_mutable_projections(tmp_path: Path) -> None:
    _seed_config(tmp_path, "framework_version: v0_TRW\nproject_name: demo\n")

    report = _repair(tmp_path)
    assert report.ok, report.errors

    receipt = json.loads((tmp_path / DEPLOYMENT_RELATIVE_PATH).read_text(encoding="utf-8"))
    bound = set(receipt["artifact_digests"])
    assert ".trw/frameworks/FRAMEWORK.md" in bound
    assert ".trw/frameworks/AARE-F-FRAMEWORK.md" in bound
    assert ".trw/config.yaml" not in bound
    assert ".trw/frameworks/VERSION.yaml" not in bound

    # The unbound projections are still deployed by the same generation.
    assert (tmp_path / ".trw/frameworks/VERSION.yaml").is_file()
    assert "framework_version: v26.2_TRW" in (tmp_path / ".trw/config.yaml").read_text(encoding="utf-8")


def test_post_receipt_config_and_stamp_writes_keep_integrity_green(tmp_path: Path) -> None:
    _seed_config(tmp_path, "framework_version: v0_TRW\nproject_name: demo\n")
    assert _repair(tmp_path).ok

    # Exactly what `install-trw.py --script` does after project setup: persist
    # the selected client surfaces, then refresh the stamp's install metadata.
    config = tmp_path / ".trw/config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + 'target_platforms:\n  - "claude-code"\n', encoding="utf-8")
    stamp = tmp_path / ".trw/frameworks/VERSION.yaml"
    stamp.write_text(stamp.read_text(encoding="utf-8") + "trw_mcp_version: 9.9.9\n", encoding="utf-8")

    report = inspect_framework_runtime(
        tmp_path,
        framework_source=FRAMEWORK,
        aaref_source=AAREF,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
        registry_digest="registry-v1",
    )
    assert report.ok, report.errors


def test_canon_body_drift_still_fails_after_unbinding_projections(tmp_path: Path) -> None:
    _seed_config(tmp_path, "framework_version: v0_TRW\n")
    assert _repair(tmp_path).ok

    (tmp_path / ".trw/frameworks/FRAMEWORK.md").write_text(FRAMEWORK + "drift", encoding="utf-8")
    report = inspect_framework_runtime(
        tmp_path,
        framework_source=FRAMEWORK,
        aaref_source=AAREF,
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
        registry_digest="registry-v1",
    )
    assert any("receipt artifact digest mismatch" in error for error in report.errors)


def test_mutable_artifacts_are_promoted_and_rollback_covered(tmp_path: Path) -> None:
    mutable = Path(".trw/frameworks/stamp.yaml")
    first = deploy_framework_generation(
        tmp_path,
        artifacts={Path(".trw/frameworks/custom.md"): b"first\n"},
        mutable_artifacts={mutable: b"generation: 1\n"},
        registry_digest="r1",
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
    )
    assert (tmp_path / mutable).read_bytes() == b"generation: 1\n"
    assert (
        mutable.as_posix()
        not in json.loads((tmp_path / DEPLOYMENT_RELATIVE_PATH).read_text(encoding="utf-8"))["artifact_digests"]
    )

    second = deploy_framework_generation(
        tmp_path,
        artifacts={Path(".trw/frameworks/custom.md"): b"second\n"},
        mutable_artifacts={mutable: b"generation: 2\n"},
        registry_digest="r2",
        framework_version=FRAMEWORK_VERSION,
        aaref_version=AAREF_VERSION,
    )
    assert first.generation_id != second.generation_id

    rollback_framework_runtime(tmp_path, second.rollback_id)
    assert (tmp_path / mutable).read_bytes() == b"generation: 1\n"


def test_generation_id_ignores_mutable_projection_bytes(tmp_path: Path) -> None:
    """A stamp-only change is not a new generation — the canon bytes decide."""
    kwargs = {
        "artifacts": {Path(".trw/frameworks/custom.md"): b"body\n"},
        "registry_digest": "r1",
        "framework_version": FRAMEWORK_VERSION,
        "aaref_version": AAREF_VERSION,
    }
    first = deploy_framework_generation(tmp_path, mutable_artifacts={Path(".trw/x.yaml"): b"a\n"}, **kwargs)
    second = deploy_framework_generation(tmp_path, mutable_artifacts={Path(".trw/x.yaml"): b"b\n"}, **kwargs)

    assert first.generation_id == second.generation_id


def test_path_declared_both_bound_and_mutable_is_rejected(tmp_path: Path) -> None:
    shared = Path(".trw/frameworks/custom.md")
    with pytest.raises(ValueError, match="declared both bound and mutable"):
        deploy_framework_generation(
            tmp_path,
            artifacts={shared: b"bound\n"},
            mutable_artifacts={shared: b"mutable\n"},
            registry_digest="registry",
            framework_version=FRAMEWORK_VERSION,
            aaref_version=AAREF_VERSION,
        )

    assert not (tmp_path / shared).exists()
    assert not (tmp_path / DEPLOYMENT_RELATIVE_PATH).exists()


@pytest.mark.parametrize("reserved", [DEPLOYMENT_RELATIVE_PATH, Path(".trw/frameworks/.staging/x")])
def test_mutable_artifacts_obey_reserved_path_guard(tmp_path: Path, reserved: Path) -> None:
    with pytest.raises(ValueError, match="deployment path is reserved"):
        deploy_framework_generation(
            tmp_path,
            artifacts={Path(".trw/frameworks/custom.md"): b"body\n"},
            mutable_artifacts={reserved: b"attacker-controlled"},
            registry_digest="registry",
            framework_version=FRAMEWORK_VERSION,
            aaref_version=AAREF_VERSION,
        )

    assert not (tmp_path / DEPLOYMENT_RELATIVE_PATH).exists()
