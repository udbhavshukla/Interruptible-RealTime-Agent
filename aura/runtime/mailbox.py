"""SessionMailbox: the ordered event queue for one session (Step 2).

A mailbox belongs to exactly **one** session and has a single consumer (the
future Coordinator) and any number of async producers::

    producers --put()--> SessionMailbox --get()--> Coordinator (future)

Guarantees implemented here:

- **Session isolation** : an event whose ``session_id`` does not match the
  mailbox's session is rejected with :class:`SessionMismatchError`.
- **Authoritative ordering** : ``seq`` is assigned by the mailbox at
  acceptance time and is monotonically increasing. A producer-supplied
  ``seq`` is ignored (the event is re-created, never mutated).
- **Two priorities** : ``CONTROL`` is always consumed before ``DATA`` when
  both are waiting; FIFO order is preserved *within* each priority.
- **Bounded capacity** (``maxsize`` applies to the total of both queues):
  - a full mailbox never silently drops a ``CONTROL`` event: the ``put``
    waits for space;
  - a full mailbox rejects ``DATA`` with :class:`MailboxFull` (explicit,
    deterministic, nothing lost without notice).
- **Explicit completion** : ``task_done()`` / ``join()`` follow the
  ``asyncio.Queue`` contract so the Coordinator can await a fully-processed
  batch.

Deliberately *not* implemented yet (later steps, only if needed): coalescing,
multiple ingress lanes, backpressure framework, dead-letter queue, replay,
metrics, journaling.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from enum import Enum
from typing import Deque, Union

from .events import Event

__all__ = [
    "Priority",
    "SessionMailbox",
    "SessionMismatchError",
    "MailboxFull",
]


class Priority(str, Enum):
    """Delivery priority of a mailbox entry."""

    CONTROL = "control"
    DATA = "data"

    def __str__(self) -> str:
        return self.value


class SessionMismatchError(ValueError):
    """Raised when an event is put into a mailbox of a different session."""


class MailboxFull(RuntimeError):
    """Raised when a DATA event is offered to a full mailbox.

    Deterministic, fail-loud behaviour: the caller always knows the event was
    *not* accepted (it is never silently lost).
    """


def _coerce_priority(priority: Union[Priority, str]) -> Priority:
    if isinstance(priority, Priority):
        return priority
    try:
        return Priority(priority)
    except ValueError as exc:
        raise ValueError(
            f"unknown priority {priority!r}; expected one of "
            f"{[p.value for p in Priority]}"
        ) from exc


class SessionMailbox:
    """Bounded, priority-aware, single-consumer event queue for one session."""

    def __init__(self, session_id: str, maxsize: int = 1000) -> None:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        if isinstance(maxsize, bool) or not isinstance(maxsize, int) or maxsize < 1:
            raise ValueError("maxsize must be a positive integer")

        self._session_id = session_id
        self._maxsize = maxsize
        self._control: Deque[Event] = deque()
        self._data: Deque[Event] = deque()
        self._next_seq = 1
        self._unfinished_tasks = 0

        self._changed = asyncio.Condition()  # protects all mutable state above
        self._finished = asyncio.Event()  # join() waits on this

    # ------------------------------------------------------------------ state
    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def unfinished_tasks(self) -> int:
        return self._unfinished_tasks

    @property
    def control_size(self) -> int:
        return len(self._control)

    @property
    def data_size(self) -> int:
        return len(self._data)

    @property
    def qsize(self) -> int:
        return len(self._control) + len(self._data)

    def empty(self) -> bool:
        return self.qsize == 0

    def full(self) -> bool:
        return self.qsize >= self._maxsize

    # -------------------------------------------------------------------- put
    async def put(
        self, event: Event, priority: Union[Priority, str] = Priority.DATA
    ) -> Event:
        """Accept ``event`` into the mailbox and return it with its assigned ``seq``.

        Raises
        ------
        SessionMismatchError
            ``event.session_id`` does not belong to this mailbox.
        MailboxFull
            The mailbox is full and ``priority`` is ``DATA`` (fail-loud; the
            event is not enqueued).
        ValueError
            Unknown priority.

        ``CONTROL`` events wait for space instead of failing, so they are
        never dropped.
        """
        prio = _coerce_priority(priority)
        if not isinstance(event, Event):
            raise TypeError(f"expected Event, got {type(event).__name__}")
        if event.session_id != self._session_id:
            raise SessionMismatchError(
                f"event {event.event_id} belongs to session {event.session_id!r}, "
                f"but this mailbox belongs to session {self._session_id!r}"
            )

        async with self._changed:
            if prio is Priority.DATA:
                if self.full():
                    raise MailboxFull(
                        f"mailbox for session {self._session_id!r} is full "
                        f"(maxsize={self._maxsize}); DATA event "
                        f"{event.event_id} was not accepted"
                    )
            else:
                # CONTROL: wait for space rather than ever dropping it.
                while self.full():
                    await self._changed.wait()

            # Acceptance point: assign the authoritative seq here (after any
            # wait), so seq order always matches enqueue order.
            accepted = replace(event, seq=self._next_seq)
            self._next_seq += 1

            if prio is Priority.CONTROL:
                self._control.append(accepted)
            else:
                self._data.append(accepted)

            self._unfinished_tasks += 1
            self._finished.clear()
            self._changed.notify_all()
            return accepted

    # -------------------------------------------------------------------- get
    async def get(self) -> Event:
        """Return the next event: CONTROL first, then DATA (FIFO within each).

        Waits (async) while the mailbox is empty.
        """
        async with self._changed:
            while not self._control and not self._data:
                await self._changed.wait()
            if self._control:
                event = self._control.popleft()
            else:
                event = self._data.popleft()
            self._changed.notify_all()
            return event

    # ------------------------------------------------------------- completion
    def task_done(self) -> None:
        """Mark the item returned by :meth:`get` as fully processed.

        Mirrors ``asyncio.Queue.task_done``; calling it more often than there
        were completed ``get()`` calls raises ``ValueError``.
        """
        if self._unfinished_tasks <= 0:
            raise ValueError("task_done() called more times than items were processed")
        self._unfinished_tasks -= 1
        if self._unfinished_tasks == 0:
            self._finished.set()

    async def join(self) -> None:
        """Block until every item that was accepted has been ``task_done()``-ed."""
        if self._unfinished_tasks > 0:
            await self._finished.wait()

    def __repr__(self) -> str:
        return (
            f"SessionMailbox(session_id={self._session_id!r}, "
            f"maxsize={self._maxsize}, control={len(self._control)}, "
            f"data={len(self._data)}, next_seq={self._next_seq})"
        )
