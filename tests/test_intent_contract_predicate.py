"""PRD-SEC-013 R7: the shared C1-C9 weaken predicate, condition by condition."""

from __future__ import annotations

from trw_mcp.security.intent_contract._control_plane import control_plane_findings
from trw_mcp.security.intent_contract._models import Contract, MustNotHappenClaim, PytestFalsifier
from trw_mcp.security.intent_contract._weaken_predicate import (
    RESERVED_CONDITIONS,
    evaluate_claim_weakening,
    evaluate_locator_weakening,
)

FALSIFIER = PytestFalsifier(kind="pytest", node_id="tests/test_x.py::test_y")


def claim(**overrides: object) -> MustNotHappenClaim:
    base: dict[str, object] = {
        "claim_id": "C-1",
        "text": "no dual submission",
        "authority_class": "human_approved",
        "state": "active",
        "machine_checkable": True,
        "binding_channel": "blocking_hook",
        "anchors": ("protected/module.py",),
        "falsifiers": (FALSIFIER,),
    }
    base.update(overrides)
    return MustNotHappenClaim.model_validate(base)


def contract(*claims: MustNotHappenClaim) -> Contract:
    return Contract(contract_id="X", claims=claims)


def conditions(base: Contract, candidate: Contract) -> set[str]:
    return {hit.condition for hit in evaluate_claim_weakening(base, candidate)}


def test_identical_snapshots_are_not_weakening() -> None:
    snapshot = contract(claim())
    assert evaluate_claim_weakening(snapshot, snapshot) == ()


def test_c1_duplicate_claim_id_is_weakening() -> None:
    dupe = contract(claim(), claim(text="different text"))
    assert "C1" in conditions(contract(claim()), dupe)


def test_c1_duplicate_ignored_when_no_binding_authority() -> None:
    observational = claim(authority_class="observational")
    dupe = contract(observational, observational.model_copy(update={"text": "other"}))
    assert conditions(dupe, dupe) == set()


def test_c2_removed_claim_is_weakening() -> None:
    assert "C2" in conditions(contract(claim()), contract())


def test_c3_authority_downgrade_is_weakening() -> None:
    assert "C3" in conditions(contract(claim()), contract(claim(authority_class="mined_provisional")))


def test_c3_active_to_suspect_is_weakening() -> None:
    """The documented-benign transition totally disarms FR05/FR07 (codex E2)."""
    assert "C3" in conditions(contract(claim()), contract(claim(state="suspect")))


def test_c3_superseded_by_covering_claim_is_not_weakening() -> None:
    successor = claim(claim_id="C-2", anchors=("protected/module.py", "protected/other.py"))
    candidate = contract(claim(state="superseded", superseded_by="C-2"), successor)
    hits = evaluate_claim_weakening(contract(claim()), candidate)
    # C-1's supersession is exempt; the NEW covering claim still costs a
    # signature under C4/R2 (an attacker must not be able to mint a
    # "covering" successor unsigned).
    assert [(hit.claim_id, hit.condition) for hit in hits] == [("C-2", "C4")]


def test_c3_superseded_by_non_covering_claim_is_weakening() -> None:
    successor = claim(claim_id="C-2", anchors=("unrelated/other.py",))
    candidate = contract(claim(state="superseded", superseded_by="C-2"), successor)
    assert "C3" in conditions(contract(claim()), candidate)


def test_c3_superseded_by_inactive_claim_is_weakening() -> None:
    successor = claim(claim_id="C-2", state="stale")
    candidate = contract(claim(state="superseded", superseded_by="C-2"), successor)
    assert "C3" in conditions(contract(claim()), candidate)


def test_c3_superseded_by_missing_target_is_weakening() -> None:
    candidate = contract(claim(state="superseded", superseded_by="C-ghost"))
    assert "C3" in conditions(contract(claim()), candidate)


def test_c4_falsifiers_emptied_is_weakening() -> None:
    assert "C4" in conditions(contract(claim()), contract(claim(falsifiers=())))


def test_c4_falsifier_added_or_changed_is_weakening() -> None:
    changed = PytestFalsifier(kind="pytest", node_id="tests/test_x.py::test_other")
    assert "C4" in conditions(contract(claim()), contract(claim(falsifiers=(changed,))))
    assert "C4" in conditions(contract(claim(falsifiers=())), contract(claim()))


def test_c4_new_binding_claim_with_falsifier_is_protected() -> None:
    assert "C4" in conditions(contract(), contract(claim()))


def test_c4_new_claim_without_falsifier_is_not_protected() -> None:
    assert conditions(contract(), contract(claim(falsifiers=()))) == set()


def test_c5_machine_checkable_flip_is_weakening() -> None:
    assert "C5" in conditions(contract(claim()), contract(claim(machine_checkable=False)))


def test_c6_anchor_removal_is_weakening_but_addition_is_not() -> None:
    two = claim(anchors=("protected/module.py", "protected/other.py"))
    assert "C6" in conditions(contract(two), contract(claim()))
    assert "C6" not in conditions(contract(claim()), contract(two))


def test_c7_channel_downgrade_is_weakening() -> None:
    for channel in ("post_edit_check", "pr_batch", "advisory", "none"):
        assert "C7" in conditions(contract(claim()), contract(claim(binding_channel=channel)))


def test_non_binding_authority_edits_are_ignored() -> None:
    observational = claim(authority_class="observational")
    assert conditions(contract(observational), contract(observational.model_copy(update={"state": "stale"}))) == set()


def test_c8_is_documented_as_reserved() -> None:
    assert RESERVED_CONDITIONS == ("C8",)


# --- C9 control plane -------------------------------------------------------


def reader(files: dict[str, bytes]) -> object:
    def _read(path: str) -> bytes | None:
        return files.get(path)

    return _read


CONFIG_ON = b"security:\n  intent:\n    enabled: true\n"
CONFIG_OFF = b"security:\n  intent:\n    enabled: false\n"
SETTINGS_WITH_HOOK = (
    b'{"hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit", "hooks": '
    b'[{"type": "command", "command": "sh pre-tool-intent-guard.sh"}]}]}}'
)
PRE_COMMIT_WITH_HOOK = b"repos:\n  - repo: local\n    hooks:\n      - id: intent-weaken-edit-flag\n"


def findings(
    base: dict[str, bytes], candidate: dict[str, bytes], removed: frozenset[str] = frozenset()
) -> tuple[str, ...]:
    return control_plane_findings(reader(base), reader(candidate), removed_or_renamed=removed)  # type: ignore[arg-type]


def test_c9_enabled_flip_is_control_plane_weakening() -> None:
    hits = findings({".trw/config.yaml": CONFIG_ON}, {".trw/config.yaml": CONFIG_OFF})
    assert any("control-plane config modified" in f for f in hits)


def test_c9_contract_locator_change_is_weakening() -> None:
    moved = b"security:\n  intent:\n    contract_path: .trw/contracts/elsewhere.yaml\n"
    hits = findings({".trw/config.yaml": CONFIG_ON}, {".trw/config.yaml": moved})
    assert any("contract locator changed" in f for f in hits)


def test_c9_contract_rename_is_weakening() -> None:
    hits = findings(
        {".trw/contracts/must-not-happen.yaml": b"x"},
        {},
        removed=frozenset({".trw/contracts/must-not-happen.yaml"}),
    )
    assert any("renamed, moved, or deleted" in f for f in hits)


def test_c9_enrollment_marker_rename_is_weakening() -> None:
    hits = findings({}, {}, removed=frozenset({".trw/contracts/enrollment.yaml"}))
    assert any("enrollment.yaml" in f for f in hits)


def test_c9_hook_deregistration_is_weakening() -> None:
    hits = findings(
        {"trw-mcp/src/trw_mcp/data/settings.json": SETTINGS_WITH_HOOK},
        {"trw-mcp/src/trw_mcp/data/settings.json": b'{"hooks": {"PreToolUse": []}}'},
    )
    assert any("hook registration removed" in f for f in hits)


def test_c9_pre_commit_hook_removal_is_weakening() -> None:
    hits = findings(
        {".pre-commit-config.yaml": PRE_COMMIT_WITH_HOOK},
        {".pre-commit-config.yaml": b"repos: []\n"},
    )
    assert any("pre-commit hook registration removed" in f for f in hits)


def test_c9_hook_addition_alone_is_not_weakening() -> None:
    assert findings({}, {"trw-mcp/src/trw_mcp/data/settings.json": SETTINGS_WITH_HOOK}) == ()


def test_c9_unchanged_control_plane_is_clean() -> None:
    files = {
        ".trw/config.yaml": CONFIG_ON,
        "trw-mcp/src/trw_mcp/data/settings.json": SETTINGS_WITH_HOOK,
        ".pre-commit-config.yaml": PRE_COMMIT_WITH_HOOK,
    }
    assert findings(files, files) == ()


def test_locator_findings_are_wrapped_as_c9_hits() -> None:
    hits = evaluate_locator_weakening(("contract locator changed: a -> b",))
    assert len(hits) == 1
    assert hits[0].condition == "C9"
    assert hits[0].claim_id == "<control-plane>"
