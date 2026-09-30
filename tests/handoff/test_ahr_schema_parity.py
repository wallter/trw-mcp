"""PRD-CORE-347-FR01: the packaged schema is a pinned byte copy of the AHR 1.0-rc.1 schema.

Update only by re-copying specs/handoff/ahr.schema.json and the vectors together, then
bump SPEC_VERSION and the pins below. Never edit the packaged schema in place.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources

from trw_mcp.handoff import schema_text

PINNED_SHA256 = "c1492da9d2293972fdc08cbc72706ce1eb6e3d821ac3378db039b5a1424fcc0a"
PINNED_ID = "https://trwframework.com/schemas/ahr/1/ahr.schema.json"
PINNED_SPEC_VERSION = "1.0-rc.1"

_DATA = resources.files("trw_mcp.data").joinpath("ahr")


def test_packaged_schema_bytes_are_pinned() -> None:
    assert hashlib.sha256(_DATA.joinpath("ahr.schema.json").read_bytes()).hexdigest() == PINNED_SHA256


def test_packaged_schema_id_and_spec_version() -> None:
    assert json.loads(schema_text())["$id"] == PINNED_ID
    assert _DATA.joinpath("SPEC_VERSION").read_text(encoding="utf-8").strip() == PINNED_SPEC_VERSION
