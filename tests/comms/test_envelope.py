"""CORE274 bounded bytes, closed payload enums and public provenance."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ValidationError

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401


@pytest.mark.parametrize("scene", [{"comms_body_max_bytes": 4}], indirect=True)
def test_body_limit_is_utf8_bytes_not_characters(scene: SendScene) -> None:
    assert scene.send("a", "🙂")["status"] == "ok"
    assert scene.send("b", "🙂a")["reason"] == "body_too_large"
    assert scene.rows("SELECT charge FROM groups") == [(1,)]


@pytest.mark.parametrize("key", ["", "x" * 129, "é" * 65])
def test_request_key_is_bounded(scene: SendScene, key: str) -> None:
    assert scene.send(key)["reason"] == "invalid_request_key"
    assert scene.rows("SELECT charge FROM groups") == [(0,)]


@pytest.mark.parametrize("delivery", ["on_demand", "interrupt", "on_idle"])
def test_all_delivery_classes_remain_pull_only(scene: SendScene, delivery: str) -> None:
    result = scene.send(delivery_class=delivery)
    assert result["status"] == "ok"
    if delivery == "on_demand":
        assert "delivery" not in result
    else:
        assert result["delivery"] == "pull_only"
    assert result["receipt"]["delivery_class"] == delivery


def test_extra_authority_fields_do_not_reach_admission(scene: SendScene) -> None:
    with pytest.raises(ValidationError):
        scene.send(sender_member_id="impl-2")
    assert scene.rows("SELECT charge FROM groups") == [(0,)]


@pytest.mark.parametrize(
    ("pointer", "valid"),
    [
        ("", False),  # 0 bytes
        ("x", True),  # 1 byte
        ("x" * 512, True),
        ("x" * 513, False),
        ("é" * 256, True),  # 512 UTF-8 bytes, 256 characters
        ("é" * 256 + "x", False),  # 513 bytes: the bound is bytes, not characters
        ("a\nb", False),
        ("a\rb", False),
        ("a\x1bb", False),  # ESC: terminal escape sequences
        ("a‮b", False),  # bidi override (Cf)
        ("a⁦b", False),  # bidi isolate (Cf)
        ("a​b", False),  # zero-width space (Cf)
        ("wt@abc forged line", False),  # line separator (Zl): not C*, still a line break
        ("a b", False),  # paragraph separator (Zp)
        ("branch worker-3/core-322 @ 76a2010, see docs/x.md", True),  # plain spaces are data
        (None, False),
        (7, False),
    ],
)
def test_next_read_is_bounded_printable_data(pointer: object, valid: bool) -> None:
    """PRD-CORE-322 NFR02: 1..512 UTF-8 bytes, no control, format or line-break character."""
    from trw_mcp.comms._envelope import valid_next_read

    assert valid_next_read(pointer) is valid
