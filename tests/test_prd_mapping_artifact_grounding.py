"""Completed PRD mappings ground local files without claiming execution evidence."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.prd_utils import parse_frontmatter
from trw_mcp.state.validation.prd_integrity import run_prd_integrity_checks

_EXISTS = "verification_artifact_exists"
_UNSUPPORTED = "verification_artifact_locator_unsupported"


def _document(
    status: str,
    artifact: str = "evidence/result.json",
    *,
    nested: bool = False,
    method: str = "test",
    kind: str = "software_behavior",
    body: str = "",
) -> str:
    metadata = f"id: PRD-CORE-901\ncategory: CORE\nstatus: {status}\nfunctionality_level: live\n"
    if nested:
        metadata = "prd:\n" + "".join("  " + line + "\n" for line in metadata.splitlines())
    return (
        "---\n" + metadata + "template_version: '3.2'\nrisk_level: low\nverification:\n  mappings:\n"
        "    - requirement_id: PRD-CORE-901-FR01\n"
        "      acceptance_criteria: [The observed value matches the required target]\n"
        f"      method: {method}\n      requirement_kind: {kind}\n"
        f"      evidence_artifact: {json.dumps(artifact)}\n"
        "      pass_condition: The recorded result satisfies the requirement\n"
        "      automated: true\n---\n# PRD-CORE-901\n"
        "## 4. Functional Requirements\n### PRD-CORE-901-FR01: Observable behavior\n"
        "The system shall provide the specified result.\n" + body
    )


def _checks(root: Path, content: str, *, extra_roots: list[Path] | None = None) -> list[ValidationFailure]:
    failures, _ = run_prd_integrity_checks(
        content,
        parse_frontmatter(content),
        project_root=root,
        prds_relative_path="docs/prds",
        extra_roots=extra_roots,
    )
    return [failure for failure in failures if failure.rule.startswith("verification_artifact_")]


@pytest.mark.parametrize("status", ["draft", "review", "approved", "implemented", "done", "delivered", "complete"])
@pytest.mark.parametrize("nested", [False, True])
def test_plain_mapping_artifact_grounding_follows_lifecycle_not_metadata_layout(
    tmp_path: Path, status: str, nested: bool
) -> None:
    failures = _checks(tmp_path, _document(status, nested=nested))
    if status in {"implemented", "done", "delivered", "complete"}:
        assert [(failure.rule, failure.severity) for failure in failures] == [(_EXISTS, "error")]
    else:
        assert failures == [], "prospective plans cannot require future artifacts to already exist"


@pytest.mark.parametrize("method", ["test", "analysis", "inspection", "demonstration"])
@pytest.mark.parametrize("kind", ["software_behavior", "non_behavioral"])
def test_local_artifact_grounding_is_method_and_requirement_kind_neutral(
    tmp_path: Path, method: str, kind: str
) -> None:
    failures = _checks(tmp_path, _document("implemented", method=method, kind=kind))
    assert any(failure.rule == _EXISTS and failure.severity == "error" for failure in failures)


@pytest.mark.parametrize("artifact", ["evidence/result.json", "`evidence/result.json`", "evidence/result.json (new)"])
def test_formatting_and_planned_marker_do_not_excuse_completed_mapping(tmp_path: Path, artifact: str) -> None:
    failures = _checks(tmp_path, _document("done", artifact, body="New evidence will be created (planned)."))
    assert any(failure.rule == _EXISTS and failure.severity == "error" for failure in failures)


def test_existing_file_then_deletion_changes_grounding_without_prd_change(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence" / "result.json"
    artifact.parent.mkdir()
    artifact.write_text('{"result": "not execution proof"}', encoding="utf-8")
    content = _document("done")
    assert _checks(tmp_path, content) == []
    artifact.unlink()
    assert [failure.rule for failure in _checks(tmp_path, content)] == [_EXISTS]


@pytest.mark.parametrize("suffix", ["::test_not_present", ":999", ":999-1000", "#L999", "#L999-L1000"])
def test_supported_suffixes_check_only_file_not_symbol_or_execution(tmp_path: Path, suffix: str) -> None:
    artifact = tmp_path / "tests" / "test_evidence.py"
    artifact.parent.mkdir()
    artifact.write_text("# No functions and no execution receipt.\n", encoding="utf-8")
    assert _checks(tmp_path, _document("done", "tests/test_evidence.py" + suffix)) == []


@pytest.mark.parametrize(
    "artifact", ["https://example.test/proof.json", "receipt:verification-123", "Human review meeting notes"]
)
def test_nonlocal_or_free_text_locator_is_observably_not_locally_verified(tmp_path: Path, artifact: str) -> None:
    failures = _checks(tmp_path, _document("done", artifact))
    assert [(failure.rule, failure.severity) for failure in failures] == [(_UNSUPPORTED, "warning")]


@pytest.mark.parametrize(
    "locator",
    [
        "tests/existing.py::test_a; tests/missing.py::test_b",
        "tests/existing.py::test_a and review meeting notes",
        "tests/existing.py::test_a && pytest tests/missing.py",
    ],
)
def test_composite_or_prose_after_selector_cannot_verify_only_first_file(tmp_path: Path, locator: str) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "existing.py").write_bytes(b"# only first file exists")
    failures = _checks(tmp_path, _document("done", locator))
    assert [(failure.rule, failure.severity) for failure in failures] == [(_UNSUPPORTED, "warning")]


@pytest.mark.parametrize("artifact", ["../outside.json", "/absolute/proof.json", "reports/*.json"])
def test_unsafe_local_locator_is_not_treated_as_present(tmp_path: Path, artifact: str) -> None:
    failures = _checks(tmp_path, _document("done", artifact))
    assert any(
        failure.rule == "verification_artifact_locator_invalid" and failure.severity == "error" for failure in failures
    )


def test_extra_repository_root_can_supply_named_local_artifact(tmp_path: Path) -> None:
    root = tmp_path / "docs-repo"
    root.mkdir()
    extra = tmp_path / "code-repo"
    (extra / "evidence").mkdir(parents=True)
    (extra / "evidence" / "result.json").write_bytes(b"evidence")
    content = _document("done")
    assert [failure.rule for failure in _checks(root, content)] == [_EXISTS]
    assert _checks(root, content, extra_roots=[extra]) == []


def test_directory_at_artifact_path_is_not_a_file(tmp_path: Path) -> None:
    (tmp_path / "evidence" / "result.json").mkdir(parents=True)
    assert [failure.rule for failure in _checks(tmp_path, _document("done"))] == [_EXISTS]


def test_supported_whole_path_with_spaces_is_not_split_into_prose(tmp_path: Path) -> None:
    artifact = tmp_path / "review reports" / "final evidence.json"
    artifact.parent.mkdir()
    artifact.write_bytes(b"exists")
    assert _checks(tmp_path, _document("done", "./review reports/final evidence.json")) == []
    # Even an existing path cannot make ambiguous bare prose locally verified.
    failures = _checks(tmp_path, _document("done", "review reports/final evidence.json"))
    assert [(failure.rule, failure.severity) for failure in failures] == [(_UNSUPPORTED, "warning")]


@pytest.fixture
def metadata_descriptors(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    descriptors: list[int] = []
    original_open, original_read = os.open, os.read

    def metadata_open(*args: object, **kwargs: object) -> int:
        fd = original_open(*args, **kwargs)  # type: ignore[arg-type]
        descriptors.append(fd)
        assert stat.S_ISDIR(os.fstat(fd).st_mode), "existence grounding must not open file payloads"
        return fd

    def no_payload_read(fd: int, count: int) -> bytes:
        assert fd not in descriptors, "metadata grounding cannot read payload bytes"
        return original_read(fd, count)

    monkeypatch.setattr(os, "open", metadata_open)
    monkeypatch.setattr(os, "read", no_payload_read)
    return descriptors


def _assert_descriptors_closed(descriptors: list[int]) -> None:
    assert descriptors, "the anchored metadata traversal must actually run"
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("alias_kind", ["leaf", "parent"])
def test_internal_alias_is_grounded_with_metadata_only_and_closed_resources(
    tmp_path: Path, metadata_descriptors: list[int], alias_kind: str
) -> None:
    from trw_mcp.state.validation._prd_integrity_artifacts import _existing_local_file

    real = tmp_path / "real"
    real.mkdir()
    (real / "result.json").write_bytes(b"file bytes are not execution evidence")
    if alias_kind == "parent":
        (tmp_path / "evidence").symlink_to(real, target_is_directory=True)
    else:
        (tmp_path / "evidence").mkdir()
        (tmp_path / "evidence" / "result.json").symlink_to(real / "result.json")
    assert _existing_local_file(tmp_path, "evidence/result.json")
    _assert_descriptors_closed(metadata_descriptors)


@pytest.mark.parametrize("race", ["leaf-before", "parent-before", "leaf-after", "parent-after"])
def test_metadata_grounding_rejects_leaf_or_parent_swap_and_closes_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata_descriptors: list[int], race: str
) -> None:
    from trw_mcp.state.validation._prd_integrity_artifacts import _existing_local_file

    root = tmp_path / "project"
    parent = root / "evidence"
    parent.mkdir(parents=True)
    artifact = parent / "result.json"
    artifact.write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.json").write_bytes(b"outside")
    original_stat = os.stat
    leaf_stats = 0
    raced = False

    def race_during_stat(path: str, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal leaf_stats, raced
        if not isinstance(path, int) and Path(path).name == "result.json" and "dir_fd" in kwargs:
            leaf_stats += 1
            trigger = 1 if race.endswith("before") else 2
            if leaf_stats == trigger:
                raced = True
                if race.startswith("leaf"):
                    if race.endswith("before"):
                        artifact.unlink()
                        artifact.symlink_to(outside / "result.json")
                    else:
                        replacement = parent / "replacement.json"
                        replacement.write_bytes(b"replacement inode")
                        os.replace(replacement, artifact)
                else:
                    parent.rename(root / "detached")
                    if race.endswith("before"):
                        parent.symlink_to(outside, target_is_directory=True)
                    else:
                        parent.mkdir()
                        (parent / "result.json").write_bytes(b"replacement parent")
        return original_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "stat", race_during_stat)
    assert not _existing_local_file(root, "evidence/result.json")
    assert raced
    _assert_descriptors_closed(metadata_descriptors)


def test_symlink_to_outside_file_does_not_ground_local_artifact(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "evidence").mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"not in an authorized root")
    (root / "evidence" / "result.json").symlink_to(outside)
    failures = _checks(root, _document("done"))
    assert any(failure.rule == _EXISTS and failure.severity == "error" for failure in failures)


def test_warm_pure_cache_rechecks_mapping_only_artifact_after_creation_and_deletion(tmp_path: Path) -> None:
    from tests.test_aaref_32_prd_contract import _validate

    content = _document("implemented")
    first = _validate(tmp_path, content, verbose=True)
    assert any(failure["rule"] == _EXISTS for failure in first["failures"])
    artifact = tmp_path / "evidence" / "result.json"
    artifact.parent.mkdir()
    artifact.write_bytes(b"evidence")
    second = _validate(tmp_path, content, verbose=True)
    assert second["cache"]["hit"] is True
    assert not any(failure["rule"] == _EXISTS for failure in second["failures"])
    artifact.unlink()
    third = _validate(tmp_path, content, verbose=True)
    assert third["cache"]["hit"] is True
    assert any(failure["rule"] == _EXISTS for failure in third["failures"])
    assert first["cache"]["key"] == second["cache"]["key"] == third["cache"]["key"]


@pytest.mark.parametrize("status", ["implemented", "done", "delivered", "complete"])
def test_rootless_dynamic_refresh_cannot_silently_claim_artifact_grounding(status: str) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.requirements import ValidationResultV2
    from trw_mcp.state.validation._prd_quality_refresh import refresh_dynamic_prd_validation

    report: dict[str, object] = {}
    result = refresh_dynamic_prd_validation(
        ValidationResultV2(valid=True), _document(status), config=TRWConfig(), project_root=None, budget_report=report
    )
    assert report["validation_partial"] is True
    assert "integrity:verification_artifacts" in report["checks_skipped"]
    assert any("project root unavailable" in warning for warning in result.integrity_warnings)
