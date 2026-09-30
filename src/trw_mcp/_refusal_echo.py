"""What a refusal may say about something the caller sent (E2E-INC-125; the rule of E2E-VALIDATION-ERROR-ECHO-R2).

A refusal never repeats a caller's VALUE: a secret pasted into the wrong argument must not land in the transcript or the
server log, and no detector catches every shape of secret. A caller's KEY name is different: a typo in a field name is
what the caller needs to see, and a key that looks like an identifier cannot carry a secret the detector would have
caught. So a key is shown only when it is identifier-shaped, short, and passes the one secret detector unchanged;
anything else is withheld, never partially shown.
"""

from __future__ import annotations

import re

from trw_mcp.telemetry.anonymizer import redact_secrets

__all__ = ["WITHHELD", "key_name"]

WITHHELD = "<withheld>"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,39}")


def key_name(key: object) -> str:
    """*key* as it may be echoed in a refusal: itself when identifier-shaped and clean, else :data:`WITHHELD`."""
    text = key if isinstance(key, str) else str(key)
    if _IDENTIFIER.fullmatch(text) and redact_secrets(text) == text:
        return text
    return WITHHELD
