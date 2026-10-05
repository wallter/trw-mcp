"""The ordered registry of ``trw-mcp doctor`` checks.

Belongs to the ``_subcommands_doctor.py`` facade. Extracted as pure data (no
functions) to keep that file under its 350 effective-LOC gate as new checks
are appended -- the resolution of each named function still happens against
``_subcommands_doctor.py``'s own module globals at run time (test-monkeypatch
indirection), which is unaffected by where this tuple itself lives.
"""

from __future__ import annotations

# (check row name, module-level function name). The function is resolved from the
# _subcommands_doctor module globals at run time so test monkeypatches on the named
# check functions take effect (test-monkeypatch indirection).
CHECKS: tuple[tuple[str, str], ...] = (
    ("python_version", "_check_python_version"),
    ("config", "_check_config"),
    ("mcp_import", "_check_mcp_import"),
    ("profile", "_check_profile"),
    ("instruction_surface", "_check_instruction_gate"),
    ("trw_dir", "_check_trw_dir"),
    ("framework_integrity", "_check_framework_integrity"),
    # PRD-CORE-248 FR06: memory_wal runs BEFORE memory_backend. The backend
    # check opens the store, and opening a SQLite store checkpoints and rewrites
    # its WAL — so a WAL row placed after it would report the state the
    # diagnostic itself produced, not the state the operator came to see.
    ("memory_wal", "_check_memory_wal"),
    ("memory_backend", "_check_memory_backend"),
    ("memory_daemon", "_check_memory_daemon"),
    ("embedding_egress", "_check_embedding_egress"),
    ("backend_connectivity", "_check_backend_connectivity"),
    ("installer_flag_advisory", "_check_installer_flag_advisory"),
    ("stubs", "_check_stubs"),
    ("agent_parity", "_check_agent_parity"),
    ("antigravity_mcp", "_check_antigravity_mcp"),
    ("tendencies_xref", "_check_tendencies_xref"),
    # PRD-CORE-266-FR06: appended LAST so every pre-existing row keeps its
    # position — the doctor's row order is asserted by its own tests and relied
    # on by operator habit; it is the later of the two subprocess-spawning checks.
    ("formation_readiness", "_check_formation_readiness"),
    # PRD-INFRA-189 FR02/FR05: appended after it for the same reason.
    ("gnu_timeout", "_check_gnu_timeout"),
    ("foreign_client_paths", "_check_foreign_client_paths"),
    # PRD-FIX-149 FR07: appended last for the same reason as the two rows above.
    ("version_status", "_check_version_status_compatible"),
    ("jev", "_check_jev"),
    # PLAN.md §3b item 3: appended last for the same reason.
    ("retrieval", "_check_retrieval"),
    ("stray_servers", "_check_stray_servers"),
    ("stale_servers", "_check_stale_servers"),
    ("claude_code_version", "_check_claude_code_version"),
    # PRD-CORE-300 slice S3a: appended last for the same reason as the rows
    # above — reports the same status `trw-mcp telemetry security` does, now
    # that its former MCP-tool form is retired.
    ("mcp_security", "_check_mcp_security"),
    # PRD-CORE-300-FR05 slice S3b: appended last for the same reason as the rows above.
    ("pipeline_health", "_check_pipeline_health"),
    # PRD-FIX-155: appended last for the same reason.
    ("hook_python", "_check_hook_python"),
    ("hook_family", "_check_hook_family"),
    # 2026-09-26 audit: appended last for the same reason as the rows above.
    ("distill", "_check_distill"),
    ("dispatch_credentials", "_check_dispatch_credentials"),  # PRD-CORE-304-FR04: appended last, as above
    ("sync_health", "_check_sync_health"),  # PRD-CORE-311-FR07: appended last, as above
    ("retired_artifacts", "_check_retired_artifacts"),  # PRD-INFRA-200-FR05: appended last, as above
    ("launcher_divergence", "_check_launcher_divergence"),  # PRD-INFRA-200-FR01: appended last, as above
    ("checkout_access", "_check_checkout_access"),  # PRD-CORE-316 P3: appended last, as above
    ("hook_channel", "_check_hook_channel"),  # PRD-CORE-336-FR04: appended last, as above
    ("memory-ledger-sample", "_check_memory_ledger_sample"),  # PRD-CORE-334-FR04: appended last, as above
    ("hint_hub_downrank", "_check_hint_hub"),  # ANCHOR-HUB-DOWNRANK: appended last, as above
    ("hint_delivery", "_check_hint_delivery"),  # HINT-DELIVERY-CANARY: appended last, as above
    ("shared_mcp", "_check_shared_mcp"),  # opt-in shared trw-mcp (trw_mcp.shared_server): appended last, as above
    ("distill_ingest", "_check_distill_ingest"),  # 2026-09-27 audit touchpoint #3: appended last, as above
    ("trw_trash", "_check_trw_trash"),  # SAFE-PUBLISH: appended last, as above
    # learning L-5ist: placed before codex_observation, whose own test pins that row last.
    ("user_tier_yaml", "_check_user_yaml"),
    ("claude_md_masks_agents_md", "_check_claude_md_masks_agents_md"),  # REMOVE-S2: before codex_observation (L-5ist)
    ("antigravity_hook", "_check_antigravity_hook"),  # UF-BOOT-08: before codex_observation (L-5ist)
    ("codex_observation", "_check_codex_observation"),  # CODEX-P0-A S3: appended last, as above
)
