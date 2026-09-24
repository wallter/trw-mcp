"""Own a pre-created awaitable until its wrapping coroutine actually starts.

A sync callable can return a coroutine. Cancellation before the wrapper's first
step bypasses its ``finally`` block, so the inner coroutine needs an explicit
owner that disposes it when Task throws into the unstarted outer coroutine.
"""

from __future__ import annotations

from collections.abc import Awaitable, Coroutine, Generator
from typing import Any, cast


class OwnedAwaitable(Coroutine[Any, Any, object]):
    def __init__(self, outer: Coroutine[Any, Any, object], pending: Awaitable[object]) -> None:
        self._outer = outer
        self._pending: Awaitable[object] | None = pending
        self._started = False

    def __await__(self) -> Generator[Any, Any, object]:
        return cast("Generator[Any, Any, object]", self)

    def __iter__(self) -> Generator[Any, Any, object]:
        return self.__await__()

    def __next__(self) -> Any:
        return self.send(None)

    def send(self, value: Any) -> Any:
        self._started = True
        return self._outer.send(value)

    def throw(self, *args: Any) -> Any:
        if not self._started:
            self._dispose()
        return self._outer.throw(*args)

    def close(self) -> None:
        if not self._started:
            self._dispose()
        self._outer.close()

    def _dispose(self) -> None:
        pending, self._pending = self._pending, None
        if pending is None:
            return
        close = getattr(pending, "close", None)
        cancel = getattr(pending, "cancel", None)
        if callable(close):
            close()
        elif callable(cancel):
            cancel()
