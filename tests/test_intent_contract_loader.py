"""PRD-SEC-013 R10: the ONE strict loader + NFR04 typed-config wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.security.intent_contract.loader import (
    ContractLoadError,
    load_contract,
    load_contract_bytes,
)

VALID = """
contract_id: INTENT-TEST-001
must_not_happen:
  - claim_id: C-1
    text: "No unsigned weakening"
    authority_class: human_approved
    state: active
    machine_checkable: true
    binding_channel: blocking_hook
    anchors: ["protected/module.py"]
    falsifiers:
      - kind: pytest
        node_id: "tests/test_x.py::test_y"
"""


def test_valid_contract_loads_binding_subset() -> None:
    contract = load_contract_bytes(VALID.encode("utf-8"))
    assert contract.contract_id == "INTENT-TEST-001"
    assert len(contract.claims) == 1
    claim = contract.claims[0]
    assert claim.claim_id == "C-1"
    assert claim.anchors == ("protected/module.py",)
    assert claim.falsifiers[0].kind == "pytest"


def test_claims_may_live_under_semantic_delta() -> None:
    contract = load_contract_bytes(
        b"""
semantic_delta:
  must_not_happen:
    - claim_id: C-1
      text: t
      authority_class: policy_derived
      state: active
      machine_checkable: true
      binding_channel: blocking_hook
"""
    )
    assert len(contract.claims) == 1


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_contract(tmp_path / "absent.yaml") is None


def test_present_file_loads_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(VALID, encoding="utf-8")
    contract = load_contract(path)
    assert contract is not None and len(contract.claims) == 1


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"must_not_happen: [\n", "malformed_yaml"),
        (b"contract_id: a\ncontract_id: b\n", "duplicate_key"),
        ("must_not_happen: ÿ\ncontract_id: x".encode("latin-1"), "invalid_utf8"),
        (b"- a\n- b\n", "not_a_mapping"),
        (b"must_not_happen: 3\n", "schema_invalid"),
    ],
)
def test_malformed_documents_fail_closed(raw: bytes, reason: str) -> None:
    with pytest.raises(ContractLoadError) as excinfo:
        load_contract_bytes(raw)
    assert excinfo.value.reason == reason


ANCHOR_DOC = b"""
defaults: &d
  authority_class: human_approved
must_not_happen:
  - claim_id: C-1
    text: t
    <<: *d
    state: active
    machine_checkable: true
    binding_channel: blocking_hook
"""

ALIAS_DOC = b"""
seed: &s "protected/module.py"
must_not_happen:
  - claim_id: C-1
    text: t
    authority_class: human_approved
    state: active
    machine_checkable: true
    binding_channel: blocking_hook
    anchors: [*s]
"""


@pytest.mark.parametrize("raw", [ANCHOR_DOC, ALIAS_DOC])
def test_anchor_and_alias_documents_are_rejected(raw: bytes) -> None:
    """typ="safe" resolves these silently; the pre-scan is what rejects them."""
    with pytest.raises(ContractLoadError) as excinfo:
        load_contract_bytes(raw)
    assert excinfo.value.reason == "anchor_alias_or_tag"


def test_custom_tag_is_rejected_by_the_prescan() -> None:
    with pytest.raises(ContractLoadError) as excinfo:
        load_contract_bytes(b"contract_id: !custom x\n")
    assert excinfo.value.reason == "anchor_alias_or_tag"


def test_python_object_tag_fails_closed() -> None:
    """The dangerous-tag case fails closed regardless of which layer catches it."""
    with pytest.raises(ContractLoadError) as excinfo:
        load_contract_bytes(b"contract_id: !!python/object:os.system 'x'\n")
    assert excinfo.value.reason in {"anchor_alias_or_tag", "malformed_yaml"}


def test_unknown_claim_field_is_rejected() -> None:
    raw = VALID.replace("    state: active", "    state: active\n    sneaky_field: 1").encode("utf-8")
    with pytest.raises(ContractLoadError) as excinfo:
        load_contract_bytes(raw)
    assert excinfo.value.reason == "schema_invalid"


def test_non_nfc_claim_id_is_rejected() -> None:
    # U+0041 U+0301 (A + combining acute) is NFD, not NFC.
    raw = VALID.replace("claim_id: C-1", 'claim_id: "C-Á"').encode("utf-8")
    with pytest.raises(ContractLoadError) as excinfo:
        load_contract_bytes(raw)
    assert excinfo.value.reason == "non_nfc"


def test_non_nfc_anchor_path_is_rejected() -> None:
    raw = VALID.replace('anchors: ["protected/module.py"]', 'anchors: ["protected/Á.py"]').encode("utf-8")
    with pytest.raises(ContractLoadError) as excinfo:
        load_contract_bytes(raw)
    assert excinfo.value.reason == "non_nfc"


def test_empty_and_claimless_documents_are_valid_no_ops() -> None:
    assert load_contract_bytes(b"").claims == ()
    assert load_contract_bytes(b"contract_id: x\n").claims == ()


def test_explicit_empty_must_not_happen_list_loads_clean() -> None:
    """A genuinely empty claims list is legitimate — must NOT trip the near-miss check."""
    contract = load_contract_bytes(b"contract_id: x\nmust_not_happen: []\n")
    assert contract.claims == ()


@pytest.mark.parametrize("near_miss_key", ["claims", "must_not_happens", "mustNotHappen"])
def test_near_miss_document_key_raises_instead_of_loading_zero_claims(near_miss_key: str) -> None:
    """A document using a plausible-but-wrong top-level key must fail LOUD.

    Before this check, a contract authored under ``claims:`` instead of
    ``must_not_happen:`` loaded silently as zero claims — silently-empty means
    silently-no-enforcement, the worst failure direction for this package.
    """
    raw = f"{near_miss_key}:\n  - claim_id: C-1\n    text: t\n".encode()
    with pytest.raises(ContractLoadError) as excinfo:
        load_contract_bytes(raw)
    assert excinfo.value.reason == "schema_invalid"
    assert "must_not_happen" in str(excinfo.value)
    assert near_miss_key in str(excinfo.value)


def test_duplicate_claim_ids_are_preserved_for_c1() -> None:
    raw = (VALID + VALID.split("must_not_happen:")[1]).encode("utf-8")
    contract = load_contract_bytes(raw)
    assert len(contract.claims) == 2
    assert contract.duplicate_ids() == frozenset({"C-1"})


def test_intent_config_defaults_and_no_ledger_path_knob() -> None:
    intent = TRWConfig().security.intent
    assert intent.enabled is True
    assert intent.pre_write_hook_budget_seconds == 1.0
    assert intent.post_edit_hook_budget_seconds == 5.0
    assert intent.falsifier_timeout_seconds == 3.0
    assert intent.false_block_window_size == 30
    assert intent.falsifier_allowed_commands == ("pytest",)
    assert intent.retro_compensator_window_commits == 500
    # R13: no configurable ledger path may exist at all.
    assert "override_ledger_path" not in type(intent).model_fields


def test_falsifier_allowlist_defaults_to_the_runtime_config() -> None:
    """One allowlist for checker and runtime (R10): None resolves the typed config."""
    argv_contract = (
        b"must_not_happen:\n  - claim_id: C-1\n    text: t\n"
        b"    authority_class: human_approved\n    state: active\n"
        b"    machine_checkable: true\n    binding_channel: blocking_hook\n"
        b'    falsifiers:\n      - kind: argv\n        argv: ["bash", "-c", "true"]\n'
    )
    assert "bash" not in TRWConfig().security.intent.falsifier_allowed_commands
    with pytest.raises(ContractLoadError) as excinfo:
        load_contract_bytes(argv_contract)
    assert excinfo.value.reason == "schema_invalid"
    # An explicit allowlist still parameterizes the same one check.
    assert load_contract_bytes(argv_contract, ("bash",)).claims[0].falsifiers[0].kind == "argv"
