"""PRD-CORE-244-FR02 on the UPDATE surface — a promotion needs a basis too.

FR02 put the substantiation rule in ``validate_entry_payload``, the single
chokepoint every *store* surface passes through. ``trw_learn``'s update mode
(``learning_id`` set, formerly the standalone ``trw_learn_update`` tool,
merged by PRD-CORE-291) is not a store: it edits an existing row through
``backend.update(namespace="default")`` and never re-enters the pipeline. So
``trw_learn(learning_id=..., confidence="verified")`` promoted an
evidence-less entry to ``verified`` — the exact defect FR02 closes, reopened
one surface over. An independent review found it.

PRD-CORE-280 slice e1: every test drives the tool's real update-mode
implementation (``execute_learn_update``) against a real daemon-backed
checkout, never an in-process ``memory.db``. ``execute_learn_update`` is
called with an explicit ``trw_dir=daemon_checkout.trw_dir`` because the
suite's process-wide ``resolve_trw_dir()`` stand-in always answers with the
bare ``tmp_path/.trw`` it is installed with, so going through the registered
tool closure would silently land on the wrong (unmigrated) checkout.
"""

from __future__ import annotations

import asyncio

import pytest

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.state._tier_routing import USER_NAMESPACE


def _seed(
    daemon_checkout: DaemonCheckout,
    entry_id: str,
    *,
    evidence: list[str] | None = None,
    anchors: list[dict[str, object]] | None = None,
    confidence: str | None = None,
    namespace: str | None = None,
) -> None:
    learning: dict[str, object] = {}
    if anchors is not None:
        learning["anchors"] = anchors
    if confidence is not None:
        learning["confidence"] = confidence

    async def _do() -> None:
        await daemon_checkout.client.store(
            f"content {entry_id}",
            namespace or daemon_checkout.namespace,
            entry_id=entry_id,
            evidence=evidence,
            learning=learning or None,
        )

    asyncio.run(_do())


def _get(daemon_checkout: DaemonCheckout, entry_id: str, *, namespace: str | None = None) -> dict[str, object]:
    async def _do() -> dict[str, object]:
        result = await daemon_checkout.client.get(entry_id, namespace or daemon_checkout.namespace)
        return dict(result["entry"])

    return asyncio.run(_do())


def _update(daemon_checkout: DaemonCheckout, learning_id: str, **kwargs: object) -> dict[str, object]:
    """Drive ``trw_learn``'s real update-mode implementation against *daemon_checkout*."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state.memory_adapter import update_learning as adapter_update
    from trw_mcp.state.persistence import FileStateWriter
    from trw_mcp.tools._learn_arg_bags import parse_learn_update_fields
    from trw_mcp.tools._learn_update_impl import execute_learn_update

    metadata = kwargs.pop("metadata", "")
    upd, fields_reject = parse_learn_update_fields(metadata)  # type: ignore[arg-type]
    if fields_reject is not None:
        return fields_reject
    return execute_learn_update(
        trw_dir=daemon_checkout.trw_dir,
        config=get_config(),
        writer=FileStateWriter(),
        adapter_update=adapter_update,
        project_root=lambda: daemon_checkout.trw_dir.parent,
        learning_id=learning_id,
        status=kwargs.pop("status", None),  # type: ignore[arg-type]
        summary=kwargs.pop("summary", None),  # type: ignore[arg-type]
        detail=kwargs.pop("detail", None),  # type: ignore[arg-type]
        impact=kwargs.pop("impact", None),  # type: ignore[arg-type]
        tags=kwargs.pop("tags", None),  # type: ignore[arg-type]
        type=kwargs.pop("type", None),  # type: ignore[arg-type]
        confidence=kwargs.pop("confidence", None),  # type: ignore[arg-type]
        upd=upd,
    )


class TestConfidencePromotionNeedsABasis:
    def test_promotion_without_evidence_is_refused(self, daemon_checkout: DaemonCheckout) -> None:
        """The bypass. Fails against the pre-fix tree, where status was 'updated'."""
        _seed(daemon_checkout, "L-bare")

        result = _update(daemon_checkout, "L-bare", confidence="verified")

        assert result["status"] == "invalid"
        assert result["reason"] == "unsubstantiated_verified"
        stored = _get(daemon_checkout, "L-bare")
        assert stored["confidence"] == "unverified", "the refused promotion must not have landed"

    def test_promotion_with_existing_evidence_proceeds(self, daemon_checkout: DaemonCheckout) -> None:
        _seed(daemon_checkout, "L-cited", evidence=["measured against memory.db on 2026-09-03"])

        result = _update(daemon_checkout, "L-cited", confidence="verified")

        assert result["status"] == "updated"
        stored = _get(daemon_checkout, "L-cited")
        assert stored["confidence"] == "verified"

    def test_existing_anchors_substantiate_the_promotion(self, daemon_checkout: DaemonCheckout) -> None:
        _seed(
            daemon_checkout,
            "L-anchored",
            anchors=[{"symbol_name": "update_learning", "file": "state/_memory_update.py"}],
        )

        assert _update(daemon_checkout, "L-anchored", confidence="verified")["status"] == "updated"

    def test_assertions_supplied_by_the_SAME_call_substantiate_it(self, daemon_checkout: DaemonCheckout) -> None:
        """The gate reads the POST-update entry, not the pre-update one.

        An author adding the assertion that proves the claim, in the same call
        that raises the confidence, is the good path — refusing it would push
        people to make two calls or to stop asserting at all.
        """
        _seed(daemon_checkout, "L-together")

        result = _update(
            daemon_checkout,
            "L-together",
            confidence="verified",
            metadata={
                "assertions": [{"type": "glob_exists", "target": "pyproject.toml"}],
            },
        )

        assert result["status"] == "updated"
        stored = _get(daemon_checkout, "L-together")
        assert stored["confidence"] == "verified"
        assert len(stored["assertions"]) == 1  # type: ignore[arg-type]

    @pytest.mark.parametrize("confidence", ["unverified", "low", "medium", "high"])
    def test_only_the_verified_claim_needs_a_basis(self, daemon_checkout: DaemonCheckout, confidence: str) -> None:
        """Demotion, and every non-verified level, are unaffected."""
        _seed(daemon_checkout, f"L-{confidence}")

        assert _update(daemon_checkout, f"L-{confidence}", confidence=confidence)["status"] == "updated"

    def test_a_verified_entry_can_still_be_demoted(self, daemon_checkout: DaemonCheckout) -> None:
        """Removing a claim never needs substantiation."""
        _seed(daemon_checkout, "L-demote", confidence="verified", evidence=["a citation"])

        assert _update(daemon_checkout, "L-demote", confidence="unverified")["status"] == "updated"
        stored = _get(daemon_checkout, "L-demote")
        assert stored["confidence"] == "unverified"

    def test_refusal_does_not_apply_the_other_fields_in_the_call(self, daemon_checkout: DaemonCheckout) -> None:
        """The whole update is refused, so a rejected promotion cannot half-land."""
        _seed(daemon_checkout, "L-partial")

        result = _update(daemon_checkout, "L-partial", confidence="verified", metadata={"task_type": "coding"})

        assert result["status"] == "invalid"
        stored = _get(daemon_checkout, "L-partial")
        assert stored["task_type"] == ""


class TestOtherConfidenceSurfacesReachTheGate:
    """The rule must hold for every surface that can set ``confidence``."""

    def test_store_surface_still_refuses(self, daemon_checkout: DaemonCheckout) -> None:
        """The store surface's own gate refuses an unsubstantiated verified claim.

        Routed through the daemon's real ``memory_store`` tool rather than a
        raw ``guarded_store(backend, entry, ...)`` call. The refusal comes back
        as an ``invalid`` status (PRD-CORE-280 e3) naming the same
        substantiation rule the in-process ``guarded_store`` raised.
        """

        async def _do() -> dict[str, object]:
            return await daemon_checkout.client.store(
                "claim", daemon_checkout.namespace, entry_id="L-store", learning={"confidence": "verified"}
            )

        result = asyncio.run(_do())
        assert result["status"] == "invalid"
        assert "substantiating artifact" in str(result["error"])

    def test_the_two_surfaces_share_one_implementation(self) -> None:
        """A copied rule is free to drift: the update path must use the store's rule itself (PRD-CORE-294 FR03).

        Both surfaces run inside the memory daemon (PRD-CORE-280 e3), so an
        in-process spy can no longer observe the call. What must hold is that
        there is one rule: the update path's ``reject_unsubstantiated_verified``
        IS the store gate's function object, and the store gate calls it. The
        refusal each surface returns is covered by
        ``test_store_surface_still_refuses`` and the update refusals above.
        """
        import inspect

        from trw_memory.lifecycle import correction
        from trw_memory.security import poisoning

        assert correction.reject_unsubstantiated_verified is poisoning.reject_unsubstantiated_verified
        assert "reject_unsubstantiated_verified(entry" in inspect.getsource(poisoning)

    def test_offline_cli_learn_routes_through_the_store_gate(self, daemon_checkout: DaemonCheckout) -> None:
        """``trw-mcp local learn`` calls execute_learn, which stores through the daemon's store gate.

        Since 2.0.1 the offline path accepts --confidence, so the gate itself must
        refuse a verified claim with no substantiation -- the same store gate the
        MCP tool runs, not a local shortcut that could promote anything. On the
        daemon route (PRD-CORE-280 e3) the refusal is a ``rejected`` answer, not a
        raised ``SchemaValidationError``, and nothing is stored.
        """
        import inspect

        from trw_mcp.services import orchestration_service

        assert "execute_learn" in inspect.getsource(orchestration_service.write_local_learning)

        result = orchestration_service.write_local_learning(
            "verified without evidence must be refused",
            "This claims verified confidence with no substantiation at all.",
            trw_dir=daemon_checkout.trw_dir,
            confidence="verified",
        )

        assert (result["status"], result["reason"]) == ("rejected", "invalid")
        assert "substantiating artifact" in str(result["message"])

        async def _do() -> dict[str, object]:
            return await daemon_checkout.client.get(str(result["learning_id"]), daemon_checkout.namespace)

        assert asyncio.run(_do()).get("status") != "ok", "a refused learning is not stored"

    def test_sync_pull_routes_through_the_store_gate(self) -> None:
        """A pulled peer entry is written by the store's apply_synced, which runs prepare_entry_for_store."""
        import inspect

        from trw_memory.sync.delta import apply_synced_entry

        from trw_mcp.sync import pull

        assert "store.apply_synced(" in inspect.getsource(pull)
        assert "prepare_entry_for_store" in inspect.getsource(apply_synced_entry)


def test_rejected_combined_update_preserves_both_records_project_prior(daemon_checkout: DaemonCheckout) -> None:
    """PRD-FIX-134: rejection must precede supersession writes (project-tier prior).

    Ports the ``project-prior`` half of the original
    ``@pytest.mark.parametrize("user_prior", [False, True])`` case to the
    daemon route. The ``user-prior`` half is BLOCKED below
    (``test_rejected_combined_update_preserves_both_records_user_prior``):
    on the daemon route the prior-resolution for ``supersedes`` happens
    *inside* the daemon subprocess (``memory_update_impl`` looks up
    ``patch.supersedes`` only within the entry's own namespace —
    trw-memory/src/trw_memory/tools/update.py), which this process cannot
    observe or substitute a backend into.
    """
    _seed(daemon_checkout, "L-target")
    _seed(daemon_checkout, "L-prior")
    before_prior = _get(daemon_checkout, "L-prior")
    before_target = _get(daemon_checkout, "L-target")

    result = _update(
        daemon_checkout,
        "L-target",
        summary="must not land",
        detail="must not land either",
        confidence="verified",
        metadata={"supersedes": "L-prior"},
    )

    assert result["status"] == "invalid"
    assert result["reason"] == "unsubstantiated_verified"
    assert _get(daemon_checkout, "L-prior") == before_prior
    assert _get(daemon_checkout, "L-target") == before_target


def test_rejected_combined_update_preserves_both_records_user_prior(daemon_checkout: DaemonCheckout) -> None:
    """PRD-FIX-134: rejection must precede supersession writes (user-tier prior).

    The user-prior half of the original parametrised case, on the daemon route:
    the prior sits in ``user:local`` of the one store (PRD-CORE-280 FR06), and a
    refused update changes neither record.
    """
    _seed(daemon_checkout, "L-target")
    _seed(daemon_checkout, "L-prior", namespace=USER_NAMESPACE)
    before_prior = _get(daemon_checkout, "L-prior", namespace=USER_NAMESPACE)
    before_target = _get(daemon_checkout, "L-target")

    result = _update(
        daemon_checkout,
        "L-target",
        summary="must not land",
        detail="must not land either",
        confidence="verified",
        metadata={"supersedes": "L-prior"},
    )

    assert result["status"] == "invalid"
    assert result["reason"] == "unsubstantiated_verified"
    assert _get(daemon_checkout, "L-prior", namespace=USER_NAMESPACE) == before_prior
    assert _get(daemon_checkout, "L-target") == before_target


def test_substantiated_combined_update_preserves_supersession(daemon_checkout: DaemonCheckout) -> None:
    """PRD-FIX-134: a same-call assertion still substantiates promotion."""
    _seed(daemon_checkout, "L-target")
    _seed(daemon_checkout, "L-prior")
    assertion = {"type": "glob_exists", "target": "README.md"}

    result = _update(
        daemon_checkout,
        "L-target",
        summary="corrected knowledge",
        confidence="verified",
        metadata={"supersedes": "L-prior", "assertions": [assertion]},
    )

    assert result["status"] == "updated"
    target = _get(daemon_checkout, "L-target")
    prior = _get(daemon_checkout, "L-prior")
    assert target["confidence"] == "verified"
    assert target["content"] == "corrected knowledge"
    assert len(target["assertions"]) == 1  # type: ignore[arg-type]
    assert target["assertions"][0]["target"] == "README.md"  # type: ignore[index]
    assert prior["invalid_from"] is not None
    assert prior["invalidated_by"] == "L-target"
