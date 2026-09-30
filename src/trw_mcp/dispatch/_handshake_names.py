"""The two environment names of the dispatch receipt channel (CODEX-P0-B-ZERO-TOOL).

A leaf with no imports, shared by ``dispatch/_handshake.py`` (the runner, which sets them in the child) and
``middleware/handshake_receipt.py`` (the server, which reads them). The runner side is imported on the pre-edit hint path,
which must stay off ``fastmcp`` (``tests/test_edit_hint_hook_budget.py``), so the names cannot live in the middleware module.
"""

PATH_ENV = "TRW_DISPATCH_HANDSHAKE"
NONCE_ENV = "TRW_DISPATCH_NONCE"
REQUIRE_ENV = "TRW_DISPATCH_REQUIRE_HANDSHAKE"
