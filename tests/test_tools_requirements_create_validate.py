"""PRD create and validate tool tests."""

from __future__ import annotations

import hashlib
import multiprocessing
from pathlib import Path

import pytest

from tests._test_tools_requirements_support import _get_tools, set_project_root  # noqa: F401


def _distinct_key(seed: str) -> str:
    """Return a schema-valid 64-hex cache key derived from a seed string."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _cache_write_worker(root_str: str, specs: list[tuple[str, float]]) -> bool:
    """Module-level spawn-safe worker: store distinct pure-result shards."""
    from trw_mcp.models.requirements import ValidationResultV2
    from trw_mcp.tools._prd_validation_cache import CacheBounds, store_pure_result

    root = Path(root_str)
    # High ceilings: this worker set must lose zero acknowledged distinct keys.
    bounds = CacheBounds(
        max_entries=100_000,
        max_total_bytes=1 << 30,
        max_entry_bytes=1 << 20,
        maintenance_interval=1_000_000,
    )
    for key, score in specs:
        store_pure_result(root, key, ValidationResultV2(total_score=score), bounds=bounds)
    return True


def _cache_reader_worker(root_str: str, keys: list[str], rounds: int) -> int:
    """Read keys repeatedly; return count of successful hits. Never partial."""
    from trw_mcp.tools._prd_validation_cache import load_pure_result_with_reason

    root = Path(root_str)
    hits = 0
    for _ in range(rounds):
        for key in keys:
            result, reason = load_pure_result_with_reason(root, key)
            # A read must be a complete validated entry or a clean miss —
            # never a parse error surfaced as an exception (it would crash here).
            if result is not None:
                assert reason == ""
                hits += 1
    return hits


class TestPrdValidationCacheHardening:
    """PRD-QUAL-114 FR06-FR08 + NFR01/NFR02/NFR04 cache safety tests."""

    def test_cache_bounds_are_config_driven_not_facade(self) -> None:
        """FR08/NFR06: TRWConfig cache knobs flow into CacheBounds (no facade)."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.tools._prd_validation_cache import CacheBounds

        configured = CacheBounds.from_config(
            TRWConfig(
                prd_validation_cache_max_entries=7,
                prd_validation_cache_max_total_bytes=2048,
                prd_validation_cache_max_entry_bytes=512,
                prd_validation_cache_maintenance_interval=3,
            )
        )
        assert configured == CacheBounds(
            max_entries=7,
            max_total_bytes=2048,
            max_entry_bytes=512,
            maintenance_interval=3,
        )
        # Defaults match the PRD-specified ceilings (512 / 64 MiB / 4 MiB / 32).
        defaults = CacheBounds.from_config(TRWConfig())
        assert defaults.max_entries == 512
        assert defaults.max_total_bytes == 64 * 1024 * 1024
        assert defaults.max_entry_bytes == 4 * 1024 * 1024
        assert defaults.maintenance_interval == 32

    def test_validation_cache_concurrent_writers_preserve_distinct_keys(self, tmp_path: Path) -> None:
        """FR06: concurrent distinct-key writers lose zero acknowledged writes."""
        from trw_mcp.tools._prd_validation_cache import load_pure_result

        root = tmp_path / "v2"
        expected: dict[str, float] = {}
        batches: list[list[tuple[str, float]]] = []
        for proc in range(4):
            specs = []
            for i in range(8):
                key = _distinct_key(f"proc-{proc}-key-{i}")
                score = float((proc * 8 + i) % 100)
                specs.append((key, score))
                expected[key] = score
            batches.append(specs)

        # Pytest/xdist workers are multithreaded; forking them can inherit
        # locks in an unusable state. Spawn also proves the workers are truly
        # process-independent rather than relying on inherited module state.
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(processes=4) as pool:
            results = pool.starmap(_cache_write_worker, [(str(root), batch) for batch in batches])
        assert all(results)

        # Every acknowledged distinct key remains independently readable.
        for key, score in expected.items():
            loaded = load_pure_result(root, key)
            assert loaded is not None, f"lost acknowledged write for {key}"
            assert loaded.total_score == score

    def test_validation_cache_corruption_isolated_as_miss(self, tmp_path: Path) -> None:
        """FR07: a corrupt shard is a typed miss; unrelated shards stay hits."""
        from trw_mcp.models.requirements import ValidationResultV2
        from trw_mcp.tools._prd_validation_cache import (
            load_pure_result_with_reason,
            store_pure_result,
        )

        root = tmp_path / "v2"
        good_key = _distinct_key("good")
        bad_key = _distinct_key("bad")
        store_pure_result(root, good_key, ValidationResultV2(total_score=42.0))
        store_pure_result(root, bad_key, ValidationResultV2(total_score=7.0))

        bad_path = root / bad_key[:2] / f"{bad_key}.json"
        bad_path.write_text("{ this is : not json", encoding="utf-8")

        bad_result, bad_reason = load_pure_result_with_reason(root, bad_key)
        assert bad_result is None
        assert bad_reason == "corrupt"

        # Unrelated valid shard remains a hit.
        good_result, good_reason = load_pure_result_with_reason(root, good_key)
        assert good_result is not None
        assert good_reason == ""
        assert good_result.total_score == 42.0

        # Oversized entry (per configured ceiling) is a bounded degraded miss.
        _, oversized_reason = load_pure_result_with_reason(root, good_key, max_entry_bytes=10)
        assert oversized_reason == "oversized"

        # Wrong-schema / wrong-key payloads are corrupt misses, not exceptions.
        bad_path.write_text('{"schema_version": 99, "key": "x", "pure_result": {}}', encoding="utf-8")
        _, schema_reason = load_pure_result_with_reason(root, bad_key)
        assert schema_reason == "corrupt"

    def test_validation_cache_key_cannot_escape_cache_root(self, tmp_path: Path) -> None:
        """NFR04: traversal / non-hex keys never read or write outside the root."""
        from trw_mcp.models.requirements import ValidationResultV2
        from trw_mcp.tools._prd_validation_cache import (
            load_pure_result_with_reason,
            store_pure_result,
        )

        root = tmp_path / "v2"
        root.mkdir(parents=True)
        for bad_key in ("../../etc/passwd", "not-hex", "AB" * 32, "a" * 63, "a" * 65):
            result, reason = load_pure_result_with_reason(root, bad_key)
            assert result is None
            assert reason == "invalid_key"
            with pytest.raises(ValueError):
                store_pure_result(root, bad_key, ValidationResultV2())

        # A symlink whose target escapes the cache root is rejected by path
        # containment before any read (never leaks file contents outside root).
        escape_key = _distinct_key("escape")
        outside = tmp_path / "outside.json"
        outside.write_text("secret", encoding="utf-8")
        escape_shard = root / escape_key[:2] / f"{escape_key}.json"
        escape_shard.parent.mkdir(parents=True, exist_ok=True)
        escape_shard.symlink_to(outside)
        result, reason = load_pure_result_with_reason(root, escape_key)
        assert result is None
        assert reason == "invalid_key"

        # A symlinked entry file that stays within the root is never followed.
        in_root_key = _distinct_key("inroot")
        in_root_target = root / "in_root_target.json"
        in_root_target.write_text("{}", encoding="utf-8")
        in_root_shard = root / in_root_key[:2] / f"{in_root_key}.json"
        in_root_shard.parent.mkdir(parents=True, exist_ok=True)
        in_root_shard.symlink_to(in_root_target)
        result, reason = load_pure_result_with_reason(root, in_root_key)
        assert result is None
        assert reason == "absent"

    def test_validation_cache_bounds_and_legacy_retirement_are_idempotent(self, tmp_path: Path) -> None:
        """FR08: dual-cap eviction preserves the newest entry; legacy retired once."""
        from trw_mcp.models.requirements import ValidationResultV2
        from trw_mcp.tools._prd_validation_cache import (
            CacheBounds,
            load_pure_result,
            retire_legacy_cache,
            store_pure_result,
        )

        root = tmp_path / ".trw" / "cache" / "prd-validation" / "v2"
        bounds = CacheBounds(
            max_entries=3,
            max_total_bytes=1 << 30,
            max_entry_bytes=1 << 20,
            maintenance_interval=4,
        )
        keys = [_distinct_key(f"bound-{i}") for i in range(8)]
        for i, key in enumerate(keys):
            store_pure_result(root, key, ValidationResultV2(total_score=float(i)), bounds=bounds)

        # After a cadence sweep on the 8th write, both ceilings hold.
        shards = list(root.glob("[0-9a-f][0-9a-f]/*.json"))
        assert len(shards) <= bounds.max_entries
        # The just-written newest entry is always preserved.
        newest = load_pure_result(root, keys[-1])
        assert newest is not None
        assert newest.total_score == 7.0

        # Legacy monolithic YAML retirement: one bounded sentinel, idempotent.
        cache_dir = tmp_path / ".trw" / "cache"
        legacy = cache_dir / "prd-validation.yaml"
        legacy.write_text("a" * 4096, encoding="utf-8")
        assert retire_legacy_cache(tmp_path) is True
        sentinel = cache_dir / "prd-validation.legacy-retired"
        assert sentinel.exists()
        assert not legacy.exists()
        # Repeated init is a no-op; at most one retirement artifact exists.
        assert retire_legacy_cache(tmp_path) is False
        legacy.write_text("stale-again", encoding="utf-8")
        assert retire_legacy_cache(tmp_path) is True
        assert not legacy.exists()
        assert len(list(cache_dir.glob("prd-validation.legacy-retired*"))) == 1

    def test_validation_cache_atomicity_under_reader_writer_stress(self, tmp_path: Path) -> None:
        """NFR02: interleaved readers/writers never observe partial state."""
        from trw_mcp.tools._prd_validation_cache import load_pure_result

        root = tmp_path / "v2"
        expected: dict[str, float] = {}
        write_batches: list[list[tuple[str, float]]] = []
        all_keys: list[str] = []
        for proc in range(3):
            specs = []
            for i in range(10):
                key = _distinct_key(f"stress-{proc}-{i}")
                score = float((proc * 10 + i) % 100)
                specs.append((key, score))
                expected[key] = score
                all_keys.append(key)
            write_batches.append(specs)

        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=_cache_write_worker, args=(str(root), batch)) for batch in write_batches]
        # Reader interleaves with writers; any successful read must be complete.
        reader = ctx.Process(target=_cache_reader_worker, args=(str(root), all_keys, 20))
        for proc in procs:
            proc.start()
        reader.start()
        for proc in procs:
            proc.join(timeout=30)
        reader.join(timeout=30)
        assert reader.exitcode == 0
        assert all(proc.exitcode == 0 for proc in procs)

        # No acknowledged non-evicted write is lost (high default ceilings).
        for key, score in expected.items():
            loaded = load_pure_result(root, key)
            assert loaded is not None
            assert loaded.total_score == score

    def test_cache_failure_never_blocks_or_false_greens_validation(self, tmp_path: Path) -> None:
        """NFR01: corrupt cache state degrades to a miss; validation is unchanged."""
        prd_content = """---
prd:
  id: PRD-CORE-114
  title: "Cache Fault"
---

# PRD-CORE-114: Cache Fault

## 1. Problem Statement
Cache faults must never change validation truth.
"""
        prd_path = tmp_path / "fault.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        clean = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert clean["cache"]["hit"] is False

        # Corrupt every shard under the v2 cache root.
        cache_root = tmp_path / ".trw" / "cache" / "prd-validation" / "v2"
        shards = list(cache_root.glob("[0-9a-f][0-9a-f]/*.json"))
        assert shards, "expected a cached shard to corrupt"
        for shard in shards:
            shard.write_text("<<<not json>>>", encoding="utf-8")

        degraded = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        # Cache reports a degraded miss but validation truth is identical.
        assert degraded["cache"]["hit"] is False
        assert degraded["cache"]["degraded"] is True
        assert degraded["cache"]["miss_reason"] == "corrupt"
        assert degraded["valid"] == clean["valid"]
        assert degraded["total_score"] == clean["total_score"]
        assert degraded["cache"]["storage_version"] == 2


class TestTrwPrdCreate:
    """Tests for trw_prd_create tool."""

    def test_creates_prd(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Add user authentication with OAuth2 support",
            category="CORE",
            priority="P1",
            title="User Authentication",
        )
        assert result["prd_id"] == "PRD-CORE-001"
        assert result["title"] == "User Authentication"
        assert result["sections_generated"] == 12
        assert "content" in result

        content = result["content"]
        assert "---" in content
        assert "Problem Statement" in content
        assert "Goals & Non-Goals" in content
        assert "Traceability Matrix" in content
        assert "test_coverage_target: 0.85" not in content
        assert "test_coverage_target:\n" in content

    def test_auto_generates_title(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Implement caching layer for API responses",
            category="INFRA",
        )
        assert result["title"] == "Implement caching layer for API responses"

    def test_saves_to_disk(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Feature request",
            category="CORE",
            title="Test Feature",
        )
        assert result["output_path"] != ""
        assert Path(result["output_path"]).exists()

    def test_invalid_priority(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import ValidationError

        tools = _get_tools()
        with pytest.raises(ValidationError, match="Invalid priority"):
            tools["trw_prd_create"].fn(
                input_text="test",
                priority="P99",
            )

    def test_priority_affects_confidence(self, tmp_path: Path) -> None:
        """P0 → 0.9, P1 → 0.7, P2 → 0.6, P3 → 0.5 in both frontmatter and body."""
        tools = _get_tools()
        for priority, expected in [("P0", 0.9), ("P1", 0.7), ("P2", 0.6), ("P3", 0.5)]:
            result = tools["trw_prd_create"].fn(
                input_text=f"Test for {priority}",
                priority=priority,
                title=f"Confidence {priority}",
                sequence=int(priority[1]) + 10,
            )
            content = result["content"]
            assert f"**Implementation Confidence**: {expected}" in content
            assert f"**Priority**: {priority}" in content

    def test_auto_increments_sequence(self, tmp_path: Path) -> None:
        """When sequence=1 (default), auto-increment from existing PRDs."""
        tools = _get_tools()

        r1 = tools["trw_prd_create"].fn(
            input_text="First PRD",
            category="CORE",
            title="First",
        )
        assert r1["prd_id"] == "PRD-CORE-001"

        r2 = tools["trw_prd_create"].fn(
            input_text="Second PRD",
            category="CORE",
            title="Second",
        )
        assert r2["prd_id"] == "PRD-CORE-002"

    def test_explicit_sequence_not_overridden(self, tmp_path: Path) -> None:
        """When sequence > 1 is explicitly set, use it as-is."""
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Explicit sequence PRD",
            category="CORE",
            title="Explicit",
            sequence=42,
        )
        assert result["prd_id"] == "PRD-CORE-042"

    def test_accepts_project_extra_category_when_config_singleton_is_stale(self, tmp_path: Path) -> None:
        """Repo-local extra categories work even if TRWConfig was cached before config read."""
        from trw_mcp.models.config import TRWConfig, reload_config

        reload_config(TRWConfig(extra_prd_categories=[]))
        (tmp_path / ".trw" / "config.yaml").write_text("extra_prd_categories:\n- CONTENT\n")

        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Content PRD",
            category="CONTENT",
            title="Content Category",
        )

        assert result["prd_id"] == "PRD-CONTENT-001"
        assert result["category"] == "CONTENT"
        reload_config(None)


class TestTrwPrdValidate:
    """Tests for trw_prd_validate tool."""

    def test_validates_good_prd(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-001
  title: "Test PRD"
  version: "1.0"
  status: draft
  priority: P1

confidence:
  implementation_feasibility: 0.8
  requirement_clarity: 0.8
  estimate_confidence: 0.7

traceability:
  implements: [KE-FRAME-001]
  depends_on: []
---

# PRD-CORE-001: Test PRD

## 1. Problem Statement
We need to solve X.

## 2. Goals & Non-Goals
Goals and non-goals.

## 3. User Stories
User stories here.

## 4. Functional Requirements
Requirements.

## 5. Non-Functional Requirements
NFRs.

## 6. Technical Approach
Approach.

## 7. Test Strategy
Testing.

## 8. Rollout Plan
Rollout.

## 9. Success Metrics
Metrics.

## 10. Dependencies & Risks
Risks.

## 11. Open Questions
Questions.

## 12. Traceability Matrix
Matrix.
"""
        prd_path = tmp_path / "test.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert result["valid"] is True
        assert len(result["sections_found"]) == 12

    def test_validates_incomplete_prd(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-002
  title: "Incomplete"
---

# Incomplete PRD

## 1. Problem Statement
Only one section.
"""
        prd_path = tmp_path / "incomplete.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert result["valid"] is False
        assert len(result["failures"]) > 0

    def test_detects_low_density(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-003
  title: "Sparse"
  version: "1.0"
  status: draft
  priority: P1

traceability:
  implements: [KE-001]
---

# PRD-CORE-003: Sparse PRD

## 1. Problem Statement
The system should be fast.

## 2. Goals & Non-Goals
## 3. User Stories
## 4. Functional Requirements
## 5. Non-Functional Requirements
## 6. Technical Approach
## 7. Test Strategy
## 8. Rollout Plan
## 9. Success Metrics
## 10. Dependencies & Risks
## 11. Open Questions
## 12. Traceability Matrix
"""
        prd_path = tmp_path / "sparse.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert result["total_score"] < 80.0

    def test_validation_cache_hits_for_unchanged_content(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-086
  title: "Cache"
---

# PRD-CORE-086: Cache

## 1. Problem Statement
Cache validation results by content hash.
"""
        prd_path = tmp_path / "cache.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        # Token-bloat W5: cache.key is a verbose-only debug field.
        first = tools["trw_prd_validate"].fn(prd_path=str(prd_path), verbose=True)
        second = tools["trw_prd_validate"].fn(prd_path=str(prd_path), verbose=True)

        assert first["cache"]["hit"] is False
        assert second["cache"]["hit"] is True
        assert first["cache"]["key"] == second["cache"]["key"]

    def test_validation_cache_reports_hash_metadata_and_current_path(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-087
  title: "Cache Metadata"
---

# PRD-CORE-087: Cache Metadata

## 1. Problem Statement
Cache validation metadata should be inspectable.
"""
        first_path = tmp_path / "first.md"
        second_path = tmp_path / "second.md"
        first_path.write_text(prd_content, encoding="utf-8")
        second_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        # Token-bloat W5: content_hash/config_hash are verbose-only debug fields;
        # validator_version stays in the compact default (decision-relevant).
        first = tools["trw_prd_validate"].fn(prd_path=str(first_path), verbose=True)
        second = tools["trw_prd_validate"].fn(prd_path=str(second_path), verbose=True)

        assert first["cache"]["content_hash"].startswith("sha256:")
        assert first["cache"]["config_hash"].startswith("sha256:")
        assert first["cache"]["validator_version"]
        assert second["cache"]["hit"] is True
        assert second["path"] == str(second_path.resolve())
        assert second["cache"]["content_hash"] == first["cache"]["content_hash"]

    def test_file_not_found(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import StateError

        tools = _get_tools()
        with pytest.raises(StateError, match="not found"):
            tools["trw_prd_validate"].fn(prd_path=str(tmp_path / "nonexistent.md"))
