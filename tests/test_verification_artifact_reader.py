"""CORE-205 FR06: actual named bytes, confinement, bounded safe reads."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from tests._evidence_factories import project_with_binding
from trw_mcp.models._evidence_core import EvidenceLimits, ReceiptState
from trw_mcp.models._evidence_records import VerificationOutcome, VerificationReceipt
from trw_mcp.state import _verification_artifact as reader


@pytest.fixture
def artifact(tmp_path: Path) -> tuple[Path, VerificationReceipt]:
    root, binding, _ = project_with_binding(tmp_path, {"src/a.py": "source"})
    (root / "proof").mkdir()
    (root / "proof/result").write_bytes(b"PASS")
    receipt = VerificationReceipt(
        receipt_id="artifact-test",
        run_id="run1",
        requirement_id="FR06",
        mapping_digest="mapping",
        method="test",
        completed_at="2026-09-18",
        content_binding=binding,
        outcome=VerificationOutcome.PASS,
        evidence_artifact_path="proof/result",
        evidence_artifact_digest=hashlib.sha256(b"PASS").hexdigest(),
    )
    return root, receipt


def test_current_and_changed_bytes(artifact: tuple[Path, VerificationReceipt]) -> None:
    root, receipt = artifact
    assert reader.verification_artifact_is_current(receipt, root).state is ReceiptState.VALID
    (root / "proof/result").write_bytes(b"FAIL")
    assert reader.verification_artifact_is_current(receipt, root).state is ReceiptState.STALE_CONTENT


@pytest.mark.parametrize(
    "path", ["/absolute", "../escape", "a//b", "./a", "a/../b", "a/", "C:/x", "a\\b", "a\x00b", "x" * 1025]
)
def test_invalid_paths(artifact: tuple[Path, VerificationReceipt], path: str) -> None:
    root, receipt = artifact
    outcome = reader.verification_artifact_is_current(receipt.model_copy(update={"evidence_artifact_path": path}), root)
    assert outcome.reason_code == "artifact_path_invalid"


@pytest.mark.parametrize("digest", ["f" * 63, "A" * 64, "z" * 64, "f" * 65])
def test_invalid_digests(artifact: tuple[Path, VerificationReceipt], digest: str) -> None:
    root, receipt = artifact
    outcome = reader.verification_artifact_is_current(
        receipt.model_copy(update={"evidence_artifact_digest": digest}), root
    )
    assert outcome.reason_code == "artifact_digest_invalid"


@pytest.mark.parametrize("field", ["evidence_artifact_path", "evidence_artifact_digest"])
def test_legacy_unbound(artifact: tuple[Path, VerificationReceipt], field: str) -> None:
    root, receipt = artifact
    assert (
        reader.verification_artifact_is_current(receipt.model_copy(update={field: ""}), root).state
        is ReceiptState.LEGACY_UNBOUND
    )


def test_missing(artifact: tuple[Path, VerificationReceipt]) -> None:
    root, receipt = artifact
    (root / "proof/result").unlink()
    assert reader.verification_artifact_is_current(receipt, root).state is ReceiptState.MISSING


@pytest.mark.parametrize("kind", ["leaf_link", "parent_link", "directory", "fifo"])
def test_nonregular_paths(artifact: tuple[Path, VerificationReceipt], kind: str) -> None:
    root, receipt = artifact
    path = root / "proof/result"
    path.unlink()
    if kind == "leaf_link":
        path.symlink_to(root / "src/a.py")
    elif kind == "parent_link":
        (root / "proof").rmdir()
        (root / "proof").symlink_to(root / "src", target_is_directory=True)
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    assert reader.verification_artifact_is_current(receipt, root).state is ReceiptState.INVALID


@pytest.mark.parametrize("change", ["replace", "parent_swap", "rewrite"])
def test_mutation_during_read(
    artifact: tuple[Path, VerificationReceipt], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    root, receipt = artifact
    original = reader._read_digest

    def mutate(fd: int, size: int) -> str | None:
        digest = original(fd, size)
        if change == "replace":
            (root / "replacement").write_bytes(b"PASS")
            (root / "replacement").replace(root / "proof/result")
        elif change == "parent_swap":
            (root / "proof").rename(root / "old")
            (root / "proof").symlink_to(root / "old", target_is_directory=True)
        else:
            path = root / "proof/result"
            before = path.stat()
            path.write_bytes(b"FAIL")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        return digest

    monkeypatch.setattr(reader, "_read_digest", mutate)
    assert reader.verification_artifact_is_current(receipt, root).state is ReceiptState.UNSTABLE_READ


def test_size_limit_before_hash(artifact: tuple[Path, VerificationReceipt], monkeypatch: pytest.MonkeyPatch) -> None:
    root, receipt = artifact
    monkeypatch.setattr(EvidenceLimits, "MAX_BOUND_FILE_BYTES", 3)
    assert reader.verification_artifact_is_current(receipt, root).reason_code == "artifact_too_large"


def test_missing_capability(artifact: tuple[Path, VerificationReceipt], monkeypatch: pytest.MonkeyPatch) -> None:
    root, receipt = artifact
    monkeypatch.setattr(reader, "_safe_read_supported", lambda: False)
    assert reader.verification_artifact_is_current(receipt, root).state is ReceiptState.DEGRADED


def test_io_error(artifact: tuple[Path, VerificationReceipt], monkeypatch: pytest.MonkeyPatch) -> None:
    root, receipt = artifact

    def denied(fd: int, size: int) -> str | None:
        raise PermissionError("denied")

    monkeypatch.setattr(reader, "_read_digest", denied)
    assert reader.verification_artifact_is_current(receipt, root).reason_code == "artifact_read_error"


def test_empty_artifact_is_valid(artifact: tuple[Path, VerificationReceipt]) -> None:
    root, receipt = artifact
    (root / "proof/result").write_bytes(b"")
    receipt = receipt.model_copy(update={"evidence_artifact_digest": hashlib.sha256(b"").hexdigest()})
    assert reader.verification_artifact_is_current(receipt, root).state is ReceiptState.VALID


@pytest.mark.parametrize("actual", [b"", b"abcdef"])
def test_stream_size_disagreement(tmp_path: Path, actual: bytes) -> None:
    path = tmp_path / "bytes"
    path.write_bytes(actual)
    with path.open("rb") as handle:
        assert reader._read_digest(handle.fileno(), 4) is None


def test_root_replacement(artifact: tuple[Path, VerificationReceipt], monkeypatch: pytest.MonkeyPatch) -> None:
    root, receipt = artifact
    original = reader._read_digest

    def replace_root(fd: int, size: int) -> str | None:
        digest = original(fd, size)
        root.rename(root.with_name("old-root"))
        root.mkdir()
        return digest

    monkeypatch.setattr(reader, "_read_digest", replace_root)
    assert reader.verification_artifact_is_current(receipt, root).reason_code == "artifact_path_changed"


def test_runtime_unsupported_capability(
    artifact: tuple[Path, VerificationReceipt], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, receipt = artifact

    def unsupported(fd: int, size: int) -> str | None:
        raise NotImplementedError("safe operation unsupported")

    monkeypatch.setattr(reader, "_read_digest", unsupported)
    assert reader.verification_artifact_is_current(receipt, root).reason_code == "artifact_safe_read_unavailable"
