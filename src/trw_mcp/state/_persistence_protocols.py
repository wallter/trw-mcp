"""State persistence Protocol interfaces — extracted from persistence.py for module-size compliance.

Belongs to the ``persistence.py`` facade. Re-exported there for backward
compatibility with callers that type-annotate against StateReader / StateWriter /
EventLogger via the parent module.

Protocols (PEP 544 structural typing) decouple the persistence layer from the
concrete File* implementations — callers depend on the interface, not the class.
"""

from __future__ import annotations
