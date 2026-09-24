"""MR1–4: the emitted policy is canonical, portable, and qualified."""

from __future__ import annotations

import hashlib

import pytest

from trw_mcp.state._store_counts import StoreCounts as _StoreCounts
from trw_mcp.state.claude_md.sections import _memory_routing as routing


def test_renderer_consumes_loaded_policy_once(monkeypatch: pytest.MonkeyPatch) -> None:
    body = "<!-- canonical metadata -->\n\n# TRW Memory Routing\n\nSENTINEL policy.\n\n## Scope\n\nSENTINEL scope.\n"
    calls: list[str] = []

    def read(filename: str) -> str:
        calls.append(filename)
        return body

    monkeypatch.setattr(routing, "_read_bundled_surface", read)
    monkeypatch.setattr(routing, "_load_analytics_counts", lambda: (12, 34))
    monkeypatch.setattr(routing, "_load_store_counts", lambda: _StoreCounts(total=40, local=34, synced=6))
    rendered = routing.render_memory_harmonization()
    assert calls == ["memory-routing.md"]
    assert "### Memory Routing\n" in rendered
    assert "#### Scope\n" in rendered
    assert "SENTINEL policy." in rendered
    assert "SENTINEL scope." in rendered
    assert "canonical metadata" not in rendered
    digest = hashlib.sha256(body.encode()).hexdigest()[:12]
    assert f"{routing.MEMORY_ROUTING_SYNC_MARKER_PREFIX}{digest} -->\n" in rendered
    # PRD-FIX-141-FR04: the claim names its population instead of printing one
    # unqualified number for two different ones.
    assert "40 learnings in this project's store" in rendered
    assert "34 recorded locally across 12 prior sessions" in rendered
    assert "6 pulled from team sync" in rendered
    assert "native" not in rendered.lower()  # no independently hardcoded policy


@pytest.mark.parametrize("fallback", [False, True])
def test_renderer_portable_policy(monkeypatch: pytest.MonkeyPatch, fallback: bool) -> None:
    if fallback:

        def missing(_filename: str) -> str:
            raise OSError("missing packaged policy")

        monkeypatch.setattr(routing, "_read_bundled_surface", missing)
    monkeypatch.setattr(routing, "_load_analytics_counts", lambda: (0, 0))
    monkeypatch.setattr(routing, "_load_store_counts", lambda: None)
    rendered = routing.render_memory_harmonization()
    for forbidden in (
        "**NEVER**",
        "exclusively",
        "Filename scan only",
        "Primary session only",
        "200-line index cap",
        "auto-pruned",
        "only for personal preferences",
    ):
        assert forbidden not in rendered
    for required in (
        "preferred",
        "privacy",
        "authoritative",
        "native",
        "trw_recall",
        "trw_learn()",
        "trw_learn(learning_id=",  # correction path, merged from trw_learn_update by PRD-CORE-291
        'scope="project"',
        'scope="user"',
        'options={"include_tiers": ["project"]}',
        "delivery obligations",
    ):
        assert required in rendered
    body = routing._FALLBACK_MEMORY_ROUTING if fallback else routing.load_memory_routing()
    digest = hashlib.sha256(body.encode()).hexdigest()[:12]
    assert f"{routing.MEMORY_ROUTING_SYNC_MARKER_PREFIX}{digest} -->" in rendered


def test_fallback_matches_packaged_policy() -> None:
    assert routing._FALLBACK_MEMORY_ROUTING == routing._read_bundled_surface("memory-routing.md")
