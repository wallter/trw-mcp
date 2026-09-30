"""PRD-CORE-342 FR07: mailbox v6 (the admissions.traceparent carrier) and the gated upgrade chain."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.test_comms_schema_v5 import _v5_handoff
from tests.comms.test_formation_cli_comms_upgrade import _dump, _execute, _open_refusal, _verdict, _version
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.comms import _schema, _store, _upgrade

_TP = "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01"


def _v5_mailbox(env: FormationFixture) -> Any:
    """A real v5 file: the v6 build's store with the v6 step undone and stamped 5."""
    manifest = _v5_handoff(env)
    _execute(
        manifest,
        ("ALTER TABLE admissions DROP COLUMN traceparent", ()),
        ("UPDATE schema_meta SET value='5'", ()),
    )
    assert _verdict(manifest, version=5) is None
    return manifest


def test_v5_upgrades_to_v6_through_the_gated_chain(formation_env: FormationFixture) -> None:
    manifest = _v5_mailbox(formation_env)
    assert _open_refusal(manifest) is _store.StoreRefusal.UPGRADE_REQUIRED  # refused until upgraded
    before = _dump(manifest)
    result = _upgrade.upgrade(manifest, ttl_seconds=86400)  # would fail re-running V5_STEPS if ungated
    assert (result["status"], result["schema_version"], result["from_version"]) == ("upgraded", 6, 5)
    assert _version(manifest) == "6" and _verdict(manifest) is None and _open_refusal(manifest) is None
    after = _dump(manifest)
    assert after["admissions"] == [(*row, None) for row in before["admissions"]]  # old rows: NULL carrier
    assert _upgrade.upgrade(manifest, ttl_seconds=86400)["status"] == "already_current"


def test_v6_rollback_restores_the_verified_v5_file(formation_env: FormationFixture) -> None:
    manifest = _v5_mailbox(formation_env)
    before = _dump(manifest)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert _upgrade.rollback(manifest)["schema_version"] == 5
    assert _version(manifest) == "5" and _verdict(manifest, version=5) is None
    assert _dump(manifest) == before


def test_fresh_v6_ddl_equals_the_upgraded_ddl() -> None:
    assert _schema.ddl_statements(6)[-1] == "ALTER TABLE admissions ADD COLUMN traceparent TEXT"
    assert _schema.ddl_statements(6)[:-1] == _schema.ddl_statements(5)


@pytest.mark.parametrize("bad", ["garbage", _TP.upper(), _TP + "-x"])
def test_an_invalid_stored_traceparent_is_corrupt(formation_env: FormationFixture, bad: str) -> None:
    manifest = _v5_handoff(formation_env)
    _execute(manifest, ("UPDATE admissions SET traceparent=?", (bad,)))
    assert "invalid traceparent" in str(_verdict(manifest))


def _inbox(scene: SendScene, **arguments: Any) -> dict[str, Any]:
    result = asyncio.run(scene.server.call_tool("trw_inbox", arguments)).structured_content
    assert isinstance(result, dict)
    return result


def test_send_stores_the_senders_traceparent_first_value_wins(
    scene: SendScene, otel_spans: InMemorySpanExporter
) -> None:
    tracer = trace.get_tracer("t")
    scene.actor("impl-1")
    with tracer.start_as_current_span("sender") as sender:
        first = scene.send()
    with tracer.start_as_current_span("retry"):
        retry = scene.send()  # an exact retry from another span
    assert first["status"] == "ok" and retry["receipt"]["message_id"] == first["receipt"]["message_id"]
    ((stored,),) = scene.rows("SELECT traceparent FROM admissions")
    assert stored is not None and stored.split("-")[1] == f"{sender.get_span_context().trace_id:032x}"  # first wins
    # Stored for the receiver's code path (PRD-CORE-349, held); not projected into the fetch response.
    scene.actor("impl-2")
    (item,) = _inbox(scene)["items"]
    assert "traceparent" not in item


def test_no_valid_span_stores_null(scene: SendScene, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.telemetry import otel_propagation

    monkeypatch.setattr(otel_propagation, "current_traceparent", lambda: None)
    scene.actor("impl-1")
    assert scene.send()["status"] == "ok"
    assert scene.rows("SELECT traceparent FROM admissions") == [(None,)]


def test_v6_is_one_way_a_v5_reader_refuses_a_v6_mailbox(formation_env: FormationFixture) -> None:
    """The honest claim: mailboxes created or upgraded by this build are v6 and are not readable as v5."""
    manifest = _v5_mailbox(formation_env)
    _upgrade.upgrade(manifest, ttl_seconds=86400)

    refusal = _verdict(manifest, version=5)

    assert refusal is not None and "unsupported schema version" in refusal


def test_otel_on_never_upgrades_or_writes_a_v5_store(formation_env: FormationFixture) -> None:
    """The mixed fleet: this build with tracing enabled meets a v5 store. It refuses; it never migrates it."""
    manifest = _v5_mailbox(formation_env)
    exporter = InMemorySpanExporter()
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with provider.get_tracer("t").start_as_current_span("traced"):
        assert _open_refusal(manifest) is _store.StoreRefusal.UPGRADE_REQUIRED

    assert _version(manifest) == "5" and _verdict(manifest, version=5) is None  # still a clean, untouched v5
