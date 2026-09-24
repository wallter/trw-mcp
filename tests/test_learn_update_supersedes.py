"""PRD-CORE-194 FR04 — trw_learn's update mode supersession branch.

Coordinator OQ4 resolution: the supersession branch fires ONLY on an explicit
``metadata={"supersedes": <record_id>}`` key, NEVER on a routine field edit.
Updating learning B with ``supersedes=A`` closes A's validity window
(invalid_from + invalidated_by=B) and retains A (no delete). A plain field
edit (no supersedes) leaves every prior record's window open. PRD-CORE-291
merged the standalone ``trw_learn_update`` tool into ``trw_learn``'s update
mode and moved ``supersedes`` from a flat kwarg into the ``metadata`` bag.

Drives the tool's real update-mode implementation against a real
daemon-backed checkout (PRD-CORE-280 slice e1: never an in-process
``memory.db``). ``execute_learn_update`` is called directly with an explicit
``trw_dir=daemon_checkout.trw_dir`` -- the suite's process-wide
``resolve_trw_dir()`` stand-in always answers with the bare
``tmp_path/.trw`` it is installed with, so going through the registered tool
closure would silently land on the wrong (unmigrated) checkout.
"""

from __future__ import annotations

import asyncio

from tests._memory_fixtures import DaemonCheckout


def _seed(daemon_checkout: DaemonCheckout, *ids: str) -> None:
    async def _do() -> None:
        for entry_id in ids:
            await daemon_checkout.client.store(f"content {entry_id}", daemon_checkout.namespace, entry_id=entry_id)

    asyncio.run(_do())


def _get(daemon_checkout: DaemonCheckout, entry_id: str) -> dict[str, object]:
    async def _do() -> dict[str, object]:
        result = await daemon_checkout.client.get(entry_id, daemon_checkout.namespace)
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


def test_learn_update_supersedes(daemon_checkout: DaemonCheckout) -> None:
    """supersedes=A closes A's window with invalidated_by=B; A is retained."""
    _seed(daemon_checkout, "L-aaaa", "L-bbbb")

    # Update B (L-bbbb) declaring it supersedes A (L-aaaa).
    result = _update(daemon_checkout, "L-bbbb", summary="corrected fact", metadata={"supersedes": "L-aaaa"})

    assert result["status"] == "updated"

    a = _get(daemon_checkout, "L-aaaa")
    assert a["invalid_from"] is not None  # window closed
    assert a["invalidated_by"] == "L-bbbb"  # closer is the updating record
    # Retained (not deleted) — still gettable.
    b = _get(daemon_checkout, "L-bbbb")
    assert b["invalid_from"] is None  # the superseding record stays open


def test_plain_edit_does_not_supersede(daemon_checkout: DaemonCheckout) -> None:
    """OQ4: a routine field edit (no supersedes=) closes no window."""
    _seed(daemon_checkout, "L-cccc")

    result = _update(daemon_checkout, "L-cccc", detail="sharper detail")
    assert result["status"] == "updated"

    c = _get(daemon_checkout, "L-cccc")
    assert c["invalid_from"] is None
    assert c["invalidated_by"] is None


def test_supersedes_missing_prior_is_reported(daemon_checkout: DaemonCheckout) -> None:
    """supersedes=<unknown id> does not crash; the edit still applies."""
    _seed(daemon_checkout, "L-dddd")

    result = _update(daemon_checkout, "L-dddd", summary="x", metadata={"supersedes": "L-nope"})
    # The primary update still succeeds; the missing prior is a no-op close.
    assert result["status"] == "updated"
    d = _get(daemon_checkout, "L-dddd")
    assert d["invalid_from"] is None
