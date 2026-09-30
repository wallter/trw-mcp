"""An unresolved-receipt refusal counts every receipt but repeats no caller id that is not clean (E2E-INC-125)."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp._refusal_echo import WITHHELD

_SECRET = "ghp_" + "a" * 36


def test_an_unresolved_receipt_is_counted_but_a_secret_shaped_id_is_withheld(tmp_path: Path) -> None:
    from trw_mcp.state._factory_receipt_gate import unresolved_receipts

    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    message = json.dumps(
        {
            "factory": 1,
            "kind": "READY",
            "attempt": "a",
            "subject_sha": "a" * 40,
            "receipts": {"build": ["build-0123456789abcdef", _SECRET]},
        }
    )

    found = unresolved_receipts(run, message)

    assert len(found) == 2, "both receipts are reported as unresolved"
    assert any("build-0123456789abcdef" in line for line in found), "an ordinary receipt id stays visible"
    assert all(_SECRET not in line for line in found) and any(WITHHELD in line for line in found)
