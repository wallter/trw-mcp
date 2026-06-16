"""Embeddings offline-switch detection (PRD-QUAL-110-FR04).

Belongs to the ``_memory_connection.py`` facade. Re-exported there
(``_embeddings_offline``) for back-compat so the warm-up path and tests keep a
single import point.

``TRW_OFFLINE`` is the TRW master offline switch; ``HF_HUB_OFFLINE`` is the
upstream huggingface_hub convention (also honored by trw-memory's embedding
init via ``local_files_only``). Any truthy value engages offline mode and
suppresses the all-MiniLM-L6-v2 download so an air-gapped deployer can prove
zero huggingface.co egress at ``session_start``.
"""

import os
from typing import Any

_OFFLINE_ENV_VARS = ("TRW_OFFLINE", "HF_HUB_OFFLINE")
_TRUTHY = ("1", "true", "yes", "on")


def embeddings_offline(env: dict[str, str]) -> bool:
    """Return True when an offline switch is engaged (PRD-QUAL-110-FR04).

    Checks ``TRW_OFFLINE`` and ``HF_HUB_OFFLINE`` for any truthy value.
    """
    return any(env.get(name, "").strip().lower() in _TRUTHY for name in _OFFLINE_ENV_VARS)


def warmup_suppressed_by_offline(logger: Any) -> bool:
    """Gate the embedder warm-up on the offline switch (PRD-QUAL-110-FR04).

    Returns True (suppress warm-up — no download) when an offline switch is
    engaged, logging ``embedder_warmup_skipped_offline``. Otherwise returns
    False after emitting the first-run ``embedder_download_disclosure`` log line
    that discloses the huggingface.co egress BEFORE the download thread starts.
    """
    if embeddings_offline(dict(os.environ)):
        logger.info(
            "embedder_warmup_skipped_offline",
            reason="offline_switch",
            switches=_OFFLINE_ENV_VARS,
        )
        return True
    logger.info(
        "embedder_download_disclosure",
        model="all-MiniLM-L6-v2",
        source="huggingface.co",
        detail=(
            "Embeddings are enabled; the local embedding model may be downloaded "
            "from huggingface.co on first use. Set TRW_OFFLINE=1 (or "
            "HF_HUB_OFFLINE=1) to suppress this network egress."
        ),
    )
    return False
