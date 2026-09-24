"""Embeddings offline-switch detection (PRD-QUAL-110-FR04).

Belongs to the ``_memory_connection.py`` facade, whose embedder load discloses
its egress through :func:`disclose_download`.

``TRW_OFFLINE`` is the TRW master offline switch; ``HF_HUB_OFFLINE`` is the
upstream huggingface_hub convention (also honored by trw-memory's embedding
init via ``local_files_only``, which trw-memory forces for either switch). Any
truthy value engages offline mode, so an air-gapped deployer can prove zero
huggingface.co egress.
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


def disclose_download(logger: Any, model: str) -> None:
    """Log the huggingface.co egress *model*'s first load may cause (PRD-QUAL-110-FR04).

    Emitted before the load, and only when no offline switch is engaged: with one
    engaged, trw-memory loads from the local cache only, so there is no egress to
    disclose.
    """
    if embeddings_offline(dict(os.environ)):
        return
    logger.info(
        "embedder_download_disclosure",
        model=model,
        source="huggingface.co",
        detail=(
            "Embeddings are enabled; the local embedding model may be downloaded "
            "from huggingface.co on first use. Set TRW_OFFLINE=1 (or "
            "HF_HUB_OFFLINE=1) to suppress this network egress."
        ),
    )
