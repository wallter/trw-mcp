"""CORE-116 targeted-recall amendment: priors cannot manufacture relevance.

These behavioral contracts intentionally fail the historical blended ordering.
They do not claim that lexical-only ranking satisfies the semantic requirement.
"""

from datetime import datetime, timezone

import pytest

from trw_mcp.scoring import RecallContext, rank_by_utility


def _entry(entry_id: str, summary: str, *, detail: str = "", impact: float = 0.6) -> dict[str, object]:
    return {
        "id": entry_id,
        "summary": summary,
        "detail": detail,
        "tags": [],
        "impact": impact,
        "q_value": impact,
        "q_observations": 5,
        "created": datetime.now(timezone.utc).date().isoformat(),
    }


@pytest.mark.parametrize("weight", [0.0, 0.3, 1.0])
def test_positive_context_cannot_override_stronger_query_match(weight: float) -> None:
    relevant = _entry("specific", "Wrapped persistence exception", detail="read-only project tool discovery")
    incidental = _entry("incidental", "tool", impact=0.95)
    incidental.update(domain=["python"], phase_affinity=["IMPLEMENT"], team_origin="core")
    context = RecallContext(
        inferred_domains={"python"}, current_phase="IMPLEMENT", team="core", prd_knowledge_ids={"incidental"}
    )
    result = rank_by_utility(
        [incidental, relevant], ["read-only", "project", "tool", "discovery", "fails"], weight, context=context
    )
    assert result[0]["id"] == "specific", [(row["id"], row["combined_score"]) for row in result]


def test_equal_query_evidence_retains_utility_tiebreak() -> None:
    weak = _entry("weak", "tool discovery", impact=0.1)
    strong = _entry("strong", "tool discovery", impact=0.9)
    assert rank_by_utility([weak, strong], ["tool", "discovery"], 0.3)[0]["id"] == "strong"


def test_wildcard_retains_utility_order() -> None:
    weak = _entry("weak", "tool discovery", impact=0.1)
    strong = _entry("strong", "unrelated", impact=0.9)
    assert rank_by_utility([weak, strong], [], 0.3)[0]["id"] == "strong"


def test_negative_evidence_can_demote_query_match() -> None:
    failed = _entry("failed", "tool discovery", impact=0.9)
    alternative = _entry("alternative", "tool investigation", impact=0.6)
    result = rank_by_utility(
        [failed, alternative],
        ["tool", "discovery"],
        0.3,
        assertion_penalties=lambda row: 1.0 if row is failed else 0.0,
    )
    assert result[0]["id"] == "alternative"


def test_equal_source_scores_are_not_input_order_preferences() -> None:
    from trw_memory.retrieval.fusion import rrf_fuse

    left = [[("a", 2.0), ("b", 2.0), ("c", 1.0)]]
    right = [[("b", 2.0), ("a", 2.0), ("c", 1.0)]]
    assert dict(rrf_fuse(left, tie_scores=True)) == dict(rrf_fuse(right, tie_scores=True))
    assert dict(rrf_fuse(left, tie_scores=True))["a"] == dict(rrf_fuse(left, tie_scores=True))["b"]
    assert dict(rrf_fuse(left))["a"] > dict(rrf_fuse(left))["b"]  # legacy default unchanged


def test_untrusted_private_fields_cannot_manufacture_semantic_evidence() -> None:
    from trw_mcp.state._recall_signals import recall_signal_scope

    relevant = _entry("relevant", "network repair", impact=0.1)
    remote = _entry("remote", "apples", impact=1.0)
    remote.update(cosine=1.0, embedding_space="trusted", combined_score=1000.0, relevance=1000.0)
    with recall_signal_scope("network repair"):
        assert rank_by_utility([remote, relevant], ["network", "repair"], 1.0)[0]["id"] == "relevant"


def test_candidate_order_does_not_change_distinct_relevance_winner() -> None:
    relevant = _entry("relevant", "network repair")
    incidental = _entry("incidental", "repair", impact=0.95)
    for entries in ([incidental, relevant], [relevant, incidental]):
        assert rank_by_utility(entries, ["network", "repair"], 0.3)[0]["id"] == "relevant"


def test_known_invalid_anchor_qualifies_relevance_without_context() -> None:
    broken = _entry("broken", "network repair", impact=0.99)
    broken["anchor_validity"] = 0.0
    alternative = _entry("alternative", "network investigation")
    assert rank_by_utility([broken, alternative], ["network", "repair"], 0.3)[0]["id"] == "alternative"


def test_unknown_cross_store_vectors_do_not_gain_common_dense_authority() -> None:
    from trw_mcp.state._recall_signals import recall_signal_scope

    literal = _entry("literal", "network repair", impact=0.1)
    semantic = _entry("semantic", "restore connectivity", impact=1.0)
    provider = object()
    with recall_signal_scope("network repair") as signals:
        for candidate, score in [(literal, 0.1), (semantic, 1.0)]:
            signals.bind_dense(
                candidate,
                query="network repair",
                provider=provider,
                query_vector=[1.0, 0.0],
                store=object(),
                namespace="default",
                entry_id=str(candidate["id"]),
                cosine=score,
            )
        ranked = rank_by_utility([semantic, literal], ["network", "repair"], 1.0)
    assert ranked[0]["id"] == "literal"
    assert ranked[1]["combined_score"] == 0.0


def test_generation_qualified_cross_store_vectors_share_dense_authority() -> None:
    from trw_memory.embeddings.provenance import EmbeddingSpace

    from trw_mcp.state._recall_signals import recall_signal_scope

    literal = _entry("literal", "network repair", impact=0.1)
    semantic = _entry("semantic", "restore connectivity", impact=1.0)
    with recall_signal_scope("network repair") as signals:
        for candidate, score in [(literal, 0.1), (semantic, 1.0)]:
            # Distinct providers/stores, equal measured generation contracts.
            signals.bind_dense(
                candidate,
                query="network repair",
                provider=object(),
                verified_space=EmbeddingSpace("a" * 64, "fixture-v1", 2),
                query_vector=[1.0, 0.0],
                store=object(),
                namespace="default",
                entry_id=str(candidate["id"]),
                cosine=score,
            )
        ranked = rank_by_utility([semantic, literal], ["network", "repair"], 1.0)
    by_id = {row["id"]: row for row in ranked}
    assert by_id["semantic"]["combined_score"] > 0.0
