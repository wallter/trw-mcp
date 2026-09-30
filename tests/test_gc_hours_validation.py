"""E2E-INC-051: ``trw-mcp gc`` refuses a negative hours window instead of silently treating every run as stale."""

from __future__ import annotations

import pytest

from trw_mcp.server._cli_argparse import _build_arg_parser


@pytest.mark.parametrize("flag", ["--staleness-hours", "--grace-hours"])
@pytest.mark.parametrize("value", ["-5", "abc", "1.5"])
def test_a_bad_hours_value_is_refused_with_a_reason(flag: str, value: str, capsys: pytest.CaptureFixture[str]) -> None:
    parser = _build_arg_parser()

    with pytest.raises(SystemExit) as done:
        parser.parse_args(["gc", flag, value])

    assert done.value.code == 2
    err = capsys.readouterr().err
    assert flag in err and ("negative" in err or "whole number" in err)


@pytest.mark.parametrize("flag", ["--staleness-hours", "--grace-hours"])
def test_zero_and_positive_hours_are_accepted(flag: str) -> None:
    parser = _build_arg_parser()

    assert getattr(parser.parse_args(["gc", flag, "0"]), flag[2:].replace("-", "_")) == 0
    assert getattr(parser.parse_args(["gc", flag, "48"]), flag[2:].replace("-", "_")) == 48
