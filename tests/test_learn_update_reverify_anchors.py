"""PRD-CORE-231-FR03: ``trw_learn(learning_id=..., metadata={"reverify_anchors": True})``.

PRD-CORE-111-FR03 mandated ``compute_anchor_validity()`` be callable from
the update path's re-verification; the parameter never existed. PRD-CORE-291
merged the standalone ``trw_learn_update`` tool into ``trw_learn``'s update
mode and moved ``reverify_anchors`` into the ``metadata`` bag. These tests
drive the tool's real update-mode implementation against a real daemon-backed
checkout (PRD-CORE-280 slice e1: never an in-process ``memory.db``).

``execute_learn_update`` (not the registered FastMCP closure) is called
directly with an explicit ``trw_dir=daemon_checkout.trw_dir``: the suite's
process-wide ``resolve_trw_dir()`` stand-in (``tests/_path_isolation.py``)
always answers with the bare ``tmp_path/.trw`` it is installed with and does
not honor a per-checkout override, so going through the registered tool
closure would silently land on the wrong (unmigrated) checkout. This mirrors
the established ``daemon_checkout`` convention elsewhere in this suite
(e.g. ``tests/test_learn_anchors.py``), which always calls the adapter
function with an explicit ``trw_dir`` rather than the tool closure.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout


def _anchor() -> dict[str, object]:
    return {"file": "src/mod.py", "symbol_name": "anchored_symbol", "symbol_type": "function"}


def _store(client: object, namespace: str, entry_id: str, *, anchors: list[dict[str, object]]) -> None:
    async def _do() -> None:
        await client.store(  # type: ignore[attr-defined]
            "anchored claim",
            namespace,
            entry_id=entry_id,
            learning={"anchors": anchors, "anchor_validity": 1.0},
        )

    asyncio.run(_do())


def _get(client: object, namespace: str, entry_id: str) -> dict[str, object]:
    async def _do() -> dict[str, object]:
        result = await client.get(entry_id, namespace)  # type: ignore[attr-defined]
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


@pytest.fixture()
def project(daemon_checkout: DaemonCheckout) -> Path:
    root = daemon_checkout.trw_dir.parent
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("def anchored_symbol() -> None:\n    return None\n", encoding="utf-8")
    return root


def test_parameter_is_exposed_and_defaults_off() -> None:
    """Backward compatible: existing callers see no behavior change.

    ``reverify_anchors`` moved into the ``metadata`` bag under PRD-CORE-291;
    the bag's own field default is the backward-compat contract now.
    """
    from tests.conftest import extract_tool_fn, make_test_server
    from trw_mcp.tools._learn_arg_bags import LearnUpdateFields

    signature = inspect.signature(extract_tool_fn(make_test_server("learning"), "trw_learn"))
    assert "metadata" in signature.parameters
    assert LearnUpdateFields().reverify_anchors is False


def test_reverify_updates_score(project: Path, daemon_checkout: DaemonCheckout) -> None:
    """The freshly computed score replaces the write-time one in storage."""
    _store(daemon_checkout.client, daemon_checkout.namespace, "L-upd", anchors=[_anchor()])
    (project / "src" / "mod.py").unlink()

    _update(daemon_checkout, "L-upd", metadata={"reverify_anchors": True})

    entry = _get(daemon_checkout.client, daemon_checkout.namespace, "L-upd")
    assert entry["anchor_validity"] == 0.0


def test_default_leaves_the_write_time_score_alone(project: Path, daemon_checkout: DaemonCheckout) -> None:
    """Without the flag, a routine field edit never recomputes anchors."""
    _store(daemon_checkout.client, daemon_checkout.namespace, "L-noflag", anchors=[_anchor()])
    (project / "src" / "mod.py").unlink()

    _update(daemon_checkout, "L-noflag", status="resolved")

    entry = _get(daemon_checkout.client, daemon_checkout.namespace, "L-noflag")
    assert entry["anchor_validity"] == 1.0


def test_reverify_runs_before_other_field_updates(project: Path, daemon_checkout: DaemonCheckout) -> None:
    """FR03 ordering: the refreshed score persists AND the field edit applies."""
    _store(daemon_checkout.client, daemon_checkout.namespace, "L-both", anchors=[_anchor()])
    (project / "src" / "mod.py").unlink()

    _update(daemon_checkout, "L-both", detail="sharpened detail", metadata={"reverify_anchors": True})

    entry = _get(daemon_checkout.client, daemon_checkout.namespace, "L-both")
    assert entry["anchor_validity"] == 0.0
    assert entry["detail"] == "sharpened detail"


def test_entry_without_anchors_is_a_no_op(project: Path, daemon_checkout: DaemonCheckout) -> None:
    """No anchors => nothing to re-verify; validity stays at its 1.0 default."""
    _store(daemon_checkout.client, daemon_checkout.namespace, "L-bare", anchors=[])

    result = _update(daemon_checkout, "L-bare", status="resolved", metadata={"reverify_anchors": True})

    assert result["status"] == "updated"
    entry = _get(daemon_checkout.client, daemon_checkout.namespace, "L-bare")
    assert entry["anchor_validity"] == 1.0


def test_intact_anchor_is_not_demoted(project: Path, daemon_checkout: DaemonCheckout) -> None:
    """No false demotion when the anchored symbol is still present."""
    _store(daemon_checkout.client, daemon_checkout.namespace, "L-ok", anchors=[_anchor()])

    _update(daemon_checkout, "L-ok", metadata={"reverify_anchors": True})

    entry = _get(daemon_checkout.client, daemon_checkout.namespace, "L-ok")
    assert entry["anchor_validity"] == 1.0
