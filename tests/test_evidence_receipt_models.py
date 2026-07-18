"""PRD-CORE-205 FR01/NFR06 — content-binding model + canonicalization tests.

Pure, I/O-free coverage of the deterministic manifest digest, scope-shrink
protection, path confinement, and portable-path canonicalization. Stable
filesystem reads are covered in ``test_evidence_binding.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.models.evidence_receipts import (
    ContentBinding,
    ContentEntry,
    EntryState,
    EvidenceLimits,
    ReceiptState,
    ReceiptValidationResult,
    RunOwnedScope,
    compute_manifest_digest,
    compute_scope_digest,
)


def _file_entry(path: str, digest: str = "a" * 64, size: int = 10) -> ContentEntry:
    return ContentEntry(path=path, state=EntryState.FILE, byte_digest=digest, byte_size=size)


def _binding(entries: tuple[ContentEntry, ...], scope_id: str = "s1", project: str = "proj") -> ContentBinding:
    required = tuple(e.path for e in entries)
    scope_digest = compute_scope_digest(scope_id, project, required)
    return ContentBinding(
        scope_id=scope_id,
        scope_digest=scope_digest,
        project_identity=project,
        entries=entries,
        manifest_digest=compute_manifest_digest(entries),
    )


class TestManifestDigest:
    def test_order_permutation_yields_same_digest(self) -> None:
        a = _file_entry("src/a.py", "1" * 64)
        b = _file_entry("src/b.py", "2" * 64)
        assert compute_manifest_digest((a, b)) == compute_manifest_digest((b, a))

    def test_one_byte_digest_change_changes_manifest(self) -> None:
        base = (_file_entry("src/a.py", "1" * 64),)
        mutated = (_file_entry("src/a.py", "1" * 63 + "2"),)
        assert compute_manifest_digest(base) != compute_manifest_digest(mutated)

    def test_added_entry_changes_manifest(self) -> None:
        base = (_file_entry("src/a.py"),)
        added = (_file_entry("src/a.py"), _file_entry("src/b.py"))
        assert compute_manifest_digest(base) != compute_manifest_digest(added)

    def test_deleted_state_changes_manifest(self) -> None:
        present = (_file_entry("src/a.py"),)
        deleted = (ContentEntry(path="src/a.py", state=EntryState.DELETED),)
        assert compute_manifest_digest(present) != compute_manifest_digest(deleted)

    def test_symlink_target_change_changes_manifest(self) -> None:
        one = (ContentEntry(path="l", state=EntryState.SYMLINK, link_target="a"),)
        two = (ContentEntry(path="l", state=EntryState.SYMLINK, link_target="b"),)
        assert compute_manifest_digest(one) != compute_manifest_digest(two)

    def test_binding_rejects_tampered_manifest(self) -> None:
        entries = (_file_entry("src/a.py"),)
        with pytest.raises(ValidationError):
            ContentBinding(
                scope_id="s1",
                scope_digest=compute_scope_digest("s1", "proj", ("src/a.py",)),
                project_identity="proj",
                entries=entries,
                manifest_digest="deadbeef",
            )

    def test_binding_rejects_duplicate_paths(self) -> None:
        entries = (_file_entry("src/a.py"), _file_entry("src/a.py"))
        with pytest.raises(ValidationError):
            ContentBinding(
                scope_id="s1",
                scope_digest=compute_scope_digest("s1", "proj", ("src/a.py",)),
                project_identity="proj",
                entries=entries,
                manifest_digest=compute_manifest_digest(entries),
            )


class TestPathConfinement:
    @pytest.mark.parametrize("bad", ["/abs/path.py", "../escape.py", "a/../b.py", "./rel.py", "a\x00b.py"])
    def test_rejects_unsafe_paths(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            ContentEntry(path=bad, state=EntryState.FILE, byte_digest="a" * 64, byte_size=1)

    def test_rejects_backslash_separators(self) -> None:
        # NFR06: repository-relative paths serialize with '/'; a caller supplying
        # a Windows-spelled path must normalize BEFORE constructing the entry.
        with pytest.raises(ValidationError):
            ContentEntry(path="src\\a.py", state=EntryState.FILE, byte_digest="a" * 64, byte_size=1)

    def test_file_entry_requires_digest_and_size(self) -> None:
        with pytest.raises(ValidationError):
            ContentEntry(path="src/a.py", state=EntryState.FILE)

    def test_symlink_requires_target(self) -> None:
        with pytest.raises(ValidationError):
            ContentEntry(path="l", state=EntryState.SYMLINK)


class TestContentBindingNormalizesPortableRepoPaths:
    """NFR06 evidence artifact: portable canonical form on every platform."""

    def test_content_binding_normalizes_portable_repo_paths(self) -> None:
        # Same repository-relative byte manifest -> same digest regardless of the
        # order entries were discovered in or the platform that produced them.
        unicode_entry = _file_entry("src/café.py", "3" * 64)
        deleted = ContentEntry(path="src/gone.py", state=EntryState.DELETED)
        entries = (unicode_entry, deleted)
        d1 = compute_manifest_digest(entries)
        d2 = compute_manifest_digest((deleted, unicode_entry))
        assert d1 == d2
        # Byte-for-byte deterministic across constructions.
        assert d1 == compute_manifest_digest(
            (
                _file_entry("src/café.py", "3" * 64),
                ContentEntry(path="src/gone.py", state=EntryState.DELETED),
            )
        )


class TestRunOwnedScope:
    def test_caller_cannot_shrink_required(self) -> None:
        scope = RunOwnedScope(
            scope_id="s1",
            scope_digest=compute_scope_digest("s1", "proj", ("src/a.py", "src/b.py")),
            project_identity="proj",
            required_paths=("src/a.py", "src/b.py"),
        )
        # A caller proposal that drops src/b.py does NOT cover required scope.
        assert scope.caller_cannot_shrink(("src/a.py", "src/b.py")) is True
        assert scope.caller_cannot_shrink(("src/a.py",)) is False

    def test_effective_paths_union_additive_only(self) -> None:
        scope = RunOwnedScope(
            scope_id="s1",
            scope_digest=compute_scope_digest("s1", "proj", ("src/a.py",)),
            project_identity="proj",
            required_paths=("src/a.py",),
            proposed_paths=("src/extra.py",),
        )
        assert scope.effective_paths == ("src/a.py", "src/extra.py")

    def test_scope_digest_tamper_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RunOwnedScope(
                scope_id="s1",
                scope_digest="wrong",
                project_identity="proj",
                required_paths=("src/a.py",),
            )


class TestReceiptValidationResult:
    def test_only_valid_is_positive(self) -> None:
        assert ReceiptValidationResult(state=ReceiptState.VALID, reason_code="ok").is_positive is True
        for state in ReceiptState:
            if state is ReceiptState.VALID:
                continue
            assert ReceiptValidationResult(state=state, reason_code="x").is_positive is False

    def test_diagnostics_are_bounded(self) -> None:
        huge = "z" * (EvidenceLimits.MAX_FREE_TEXT_BYTES + 500)
        result = ReceiptValidationResult(state=ReceiptState.INVALID, reason_code="x", diagnostics=huge)
        assert len(result.diagnostics.encode("utf-8")) <= EvidenceLimits.MAX_FREE_TEXT_BYTES
