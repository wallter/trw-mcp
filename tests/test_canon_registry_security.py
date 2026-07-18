"""Path/size/secret containment for registry + fingerprint (PRD-INFRA-164 NFR04)."""

from __future__ import annotations

import json

import pytest

from trw_mcp.canons import registry as reg
from trw_mcp.canons._errors import CanonErrorCode, CanonRegistryError
from trw_mcp.canons._loader import MAX_MANIFEST_BYTES, parse_registry
from trw_mcp.canons.fingerprint import RealizedSurface, freeze_fingerprint


def _manifest() -> dict[str, object]:
    return json.loads(reg.bundled_manifest_bytes().decode("utf-8"))


def test_oversized_manifest_fails_with_stable_code() -> None:
    payload = b"{" + b" " * (MAX_MANIFEST_BYTES + 1) + b"}"
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(payload)
    assert exc.value.code is CanonErrorCode.OVERSIZED_INPUT


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/abs/path.md", CanonErrorCode.ABSOLUTE_PATH),
        ("C:\\win\\path.md", CanonErrorCode.ABSOLUTE_PATH),
        ("../../etc/passwd", CanonErrorCode.TRAVERSING_PATH),
        ("a/../../b.md", CanonErrorCode.TRAVERSING_PATH),
        ("bad\x01path.md", CanonErrorCode.CONTROL_CHARACTER),
    ],
)
def test_malicious_paths_are_contained(path: str, code: CanonErrorCode) -> None:
    data = _manifest()
    data["artifacts"][0]["install_targets"] = [  # type: ignore[index]
        {"path": path, "role": "runtime", "update_policy": "managed"}
    ]
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    assert exc.value.code is code


def test_error_messages_are_secret_and_abspath_free() -> None:
    data = _manifest()
    data["artifacts"][0]["authoring_source"] = "/secret/token=hunter2/f.md"  # type: ignore[index]
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    # The path is echoed as the offending value, but the code stops it before I/O
    # and the message is a single stable line, not a resolved checkout path.
    assert exc.value.code is CanonErrorCode.ABSOLUTE_PATH
    assert "\n" not in str(exc.value)


def test_fingerprint_public_payload_is_bounded_allowlist() -> None:
    fp = freeze_fingerprint(
        trw_mcp_version="1.2.3",
        framework_version="v26.1_TRW",
        aaref_version="v3.2.0",
        template_version="3.2",
        registry_digest="deadbeef",
        source_digests={"framework": "aa", "aaref": "bb"},
        surface=RealizedSurface(tools=(), resources=(), prompts=()),
    )
    payload = fp.public_payload()
    assert set(payload) == {
        "schema_version",
        "trw_mcp_version",
        "framework_version",
        "aaref_version",
        "template_version",
        "registry_digest",
        "source_digests",
        "loaded_module_digest",
        "surface_digest",
        "digest",
    }
    # No environment/secret/absolute-path field leaks in.
    serialized = json.dumps(payload)
    for banned in ("/home/", "TRW_", "token", "secret", "password", "PID", "pid"):
        assert banned not in serialized
