"""Performance benchmark tests for validation pipeline.

Uses pytest-benchmark to measure:
- PRD validation V2 performance on realistic content
- Malformed input graceful degradation
"""

from __future__ import annotations

from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation import validate_prd_quality_v2


_SAMPLE_PRD_CONTENT = """\
---
prd:
  id: PRD-CORE-099
  title: Benchmark Test PRD
  status: draft
  priority: P1
  category: CORE
  confidence:
    overall: 0.85
    technical: 0.90
    requirements: 0.80
  quality_gates:
    ambiguity_rate: 0.03
    completeness_score: 0.88
    traceability_coverage: 0.92
  dates:
    created: 2026-01-15
    target: 2026-03-01
---

# PRD-CORE-099: Benchmark Test PRD

## Problem Statement

This is a comprehensive PRD used for benchmarking the validation pipeline.
The system needs to process PRDs of various sizes efficiently while maintaining
accuracy in quality scoring across all six dimensions.

## Goals and Non-Goals

### Goals
- Validate PRD content within acceptable time bounds
- Score quality across completeness, clarity, structure, smell, testability, and traceability
- Support incremental validation as PRDs are edited

### Non-Goals
- Real-time validation during typing
- Cross-PRD dependency analysis

## Requirements

### Functional Requirements

#### FR-001: Validation Pipeline Performance
The system SHALL complete PRD validation within 500ms for documents up to 50KB.

**Acceptance Criteria:**
- Given a 50KB PRD document
- When validate_prd_quality_v2 is called
- Then the result is returned within 500ms

#### FR-002: Multi-Dimensional Scoring
The system SHALL score PRDs across six quality dimensions.

**Acceptance Criteria:**
- Given a valid PRD document
- When validation is performed
- Then scores for completeness, clarity, structure, smell, testability, and traceability are returned

#### FR-003: Malformed Input Handling
The system SHALL gracefully handle malformed or empty input.

**Acceptance Criteria:**
- Given malformed markdown content
- When validation is attempted
- Then a result with appropriate error information is returned without raising exceptions

## Technical Design

### Architecture
The validation pipeline consists of:
1. YAML frontmatter parsing
2. Section structure analysis
3. Six-dimension scoring engine
4. Smell detection
5. Readability analysis

### Data Flow
```
Input PRD → Parse YAML → Extract Sections → Score Dimensions → Aggregate → Result
```

## Dependencies

- pydantic>=2.0.0 for validation models
- ruamel.yaml>=0.18.0 for YAML parsing

## Testing Strategy

Unit tests cover each dimension scorer independently.
Integration tests verify the full pipeline end-to-end.

## Rollout Plan

Phase 1: Core validation pipeline
Phase 2: Performance optimization
Phase 3: Incremental validation support

## Success Metrics

- P95 validation latency < 500ms for 50KB documents
- Zero unhandled exceptions on malformed input
- Quality score accuracy > 90% vs human review

## Open Questions

None at this time.

## Appendix

### Glossary
- **PRD**: Product Requirements Document
- **Dimension**: One of six quality scoring axes
"""

# Repeat the content to approach ~50KB
_LARGE_PRD_CONTENT = _SAMPLE_PRD_CONTENT + ("\n" + "Additional context. " * 100 + "\n") * 30


@pytest.mark.integration
class TestValidationBenchmarks:
    """Benchmark tests for PRD validation pipeline."""

    def test_validate_small_prd_benchmark(
        self, benchmark: Any,
    ) -> None:
        """Benchmark validation of a small (~3KB) PRD."""
        config = TRWConfig()

        def run() -> None:
            validate_prd_quality_v2(_SAMPLE_PRD_CONTENT, config)

        benchmark(run)

    def test_validate_large_prd_benchmark(
        self, benchmark: Any,
    ) -> None:
        """Benchmark validation of a large (~50KB) PRD.

        Target: < 500ms per validation.
        """
        config = TRWConfig()
        assert len(_LARGE_PRD_CONTENT.encode()) > 30_000, (
            f"Content too small: {len(_LARGE_PRD_CONTENT.encode())} bytes"
        )

        def run() -> None:
            validate_prd_quality_v2(_LARGE_PRD_CONTENT, config)

        result = benchmark(run)
        # The benchmark fixture handles timing assertions via
        # --benchmark-max-time flag if needed

    def test_validate_large_prd_under_500ms(self) -> None:
        """Non-benchmark assertion: large PRD validates in <500ms."""
        import time

        config = TRWConfig()
        start = time.perf_counter()
        validate_prd_quality_v2(_LARGE_PRD_CONTENT, config)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 500, f"Validation took {elapsed_ms:.1f}ms, exceeding 500ms target"


@pytest.mark.integration
class TestMalformedInputGraceful:
    """Verify graceful degradation on malformed inputs."""

    def test_empty_string(self) -> None:
        """Empty string produces a result without raising."""
        result = validate_prd_quality_v2("")
        assert result is not None
        assert hasattr(result, "total_score")

    def test_no_frontmatter(self) -> None:
        """Content without YAML frontmatter still validates."""
        result = validate_prd_quality_v2("# Just a title\n\nSome body text.")
        assert result is not None
        assert result.total_score >= 0.0

    def test_malformed_yaml(self) -> None:
        """Malformed YAML frontmatter degrades gracefully."""
        content = "---\n  bad:\n    yaml: [\n---\n\n# Title\nBody."
        result = validate_prd_quality_v2(content)
        assert result is not None

    def test_only_frontmatter(self) -> None:
        """PRD with only frontmatter and no body text."""
        content = (
            "---\nprd:\n  id: PRD-TEST-001\n  title: Test\n"
            "  status: draft\n  priority: P1\n  category: CORE\n---\n"
        )
        result = validate_prd_quality_v2(content)
        assert result is not None
        # Should score low on completeness (scale 0-100)
        assert result.total_score < 50

    def test_binary_content(self) -> None:
        """Binary-like content doesn't crash the validator."""
        content = "\x00\x01\x02 header\n\nsome text"
        result = validate_prd_quality_v2(content)
        assert result is not None
