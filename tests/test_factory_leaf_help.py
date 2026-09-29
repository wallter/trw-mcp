"""The experimental factory leaf commands describe themselves in ``--help``.

A caller (or a model) that runs ``trw-mcp receipt verify --help`` needs more than the usage line:
what the command reads, that it is Alpha, an example, and what each exit code means.
"""

from __future__ import annotations

import re

import pytest

from trw_mcp.server._cli_argparse import _build_arg_parser


@pytest.mark.parametrize("argv", [["receipt", "verify"], ["factory", "status"]])
def test_leaf_help_has_a_description_an_example_and_exit_codes(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _build_arg_parser().parse_args([*argv, "--help"])
    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    _usage, _, rest = help_text.partition("\n\n")
    description = rest.split("\n\n", 1)[0]
    assert description.startswith("EXPERIMENTAL (Alpha"), f"no description sentence after the usage line:\n{help_text}"
    assert len(description.splitlines()) >= 2
    assert f"trw-mcp {' '.join(argv)} " in help_text, "the example names the command"
    assert "exit codes:" in help_text
    codes_text = help_text.split("exit codes:", 1)[1]
    for code in ("0", "1", "2", "3"):
        assert re.search(rf"(?:^|[\s;:]){code} \w", codes_text), f"exit code {code} is not explained"
