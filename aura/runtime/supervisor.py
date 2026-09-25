"""Asyncio task supervisor for the AURA real-time runtime (Step 4).

The Supervisor owns *execution*; the Registry owns *metadata and state*::

    Coordinator (future)
          |
          v
    TaskSupervisor.start(call_id, factory, ...)  --register-->  TaskRegistry
          |                                                       (CREATED ->
          +-- spawns asyncio.Task --> awaits outcome -->          RUNNING ->
                                                            SUCCEEDED / FAILED / CANCELLED)

Design notes
------------
- ``start`` registers in the :class:`~aura.runtime.registry.TaskRegistry`
  *before* any coroutine runs, so a duplicate ``call_id`` or a missing event
  loop fails fast and never leaves an orphan coroutine behind.
- The supervised wrapper converts every outcome into a registry transition:
  normal return -> ``SUCCEEDED``, exception -> ``FAILED`` (swallowed so the
  supervisor can never be crashed by a tool), cancellation -> ``CANCELLED``
  (``CancelledError`` is re-raised, the standard asyncio contract).
- **Cancellation is advisory.** ``cancel()`` records ``CANCEL_REQUESTED``
  first, then best-effort signals ``asyncio.Task.cancel()``. A tool that
  ignores the signal still finishes and settles as ``SUCCEEDED`` with
  ``cancel_requested=True`` — the future Acceptance Gate (a later step) uses
  that flag to reject its stale result. This module never assumes instant
  cancellation.
- Results and exceptions are retained per ``call_id`` so the future
  Acceptance Gate / event-dispatch steps have data to read. No events are
  emitted here, no Coordinator is required — the Supervisor only needs a
  registry.
- Records are never deleted; terminal tasks stay represented in both the
  registry and the supervisor.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Dict, Optional

from .registry import InvalidTransition, TaskRecord, TaskRegistry, UnknownCallError

__all__ = ["TaskSupervisor", "CoroFactory"]

#: A zero-argument callable that returns an awaitable (e.g. ``lambda: tool(arg)``
#: or ``functools.partial(tool, arg)``). A factory — not a ready coroutine — so
#: validation failures can never leave an un-awaited coroutine behind.
CoroFactory = Callable[[], Awaitable[Any]]


class TaskSupervisor:
    """Runs async tool calls as supervised ``asyncio.Task``\\ s.

    Small API for the future Coordinator::

        task = supervisor.start(call_id, factory, tool_name=..., spawn_version=...)
        supervisor.cancel(call_id)            # advisory, idempotent
        supervisor.task_for(call_id)          # the asyncio.Task
        supervisor.result(call_id) / supervisor.error(call_id)
    """

    def __init__(self, registry: TaskRegistry) -> None:
        self._registry = registry
        self._tasks: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, Any] = {}
        self._errors: Dict[str, BaseException] = {}

    @property
    def registry(self) -> TaskRegistry:
        return self._registry

    # ------------------------------------------------------------------ start
    def start(
        self,
        call_id: str,
        coro_factory: CoroFactory,
        *,
        tool_name: str,
        spawn_version: int,
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
    ) -> asyncio.Task:
        """Register and run ``coro_factory()`` as a supervised task.

        Registration happens first (``CREATED`` -> ``RUNNING``), then the
        ``asyncio.Task`` is spawned; the wrapper settles the registry on the
        way out. Raises whatever ``TaskRegistry.register`` raises
        (``DuplicateCallError`` / ``ValueError``) or ``TypeError`` for a
        non-callable factory, and ``RuntimeError`` outside a running loop —
        in every failure case the registry is left untouched.
        """
        if not callable(coro_factory):
            raise TypeError(
                f"coro_factory must be callable, got {type(coro_factory).__name__}"
            )
        loop = asyncio.get_running_loop()  # fail before registering anything

        record = self._registry.register(
            call_id=call_id,
            tool_name=tool_name,
            spawn_version=spawn_version,
            plan_id=plan_id,
            step_id=step_id,
        )
        task = loop.create_task(self._run(call_id, coro_factory))
        # Settlement safety net: if the wrapper never ran — asyncio throws
        # CancelledError into a not-yet-started task *without* executing its
        # body — the registry must still reach a terminal state.
        task.add_done_callback(lambda t, cid=call_id: self._ensure_settled(cid, t))
        self._tasks[call_id] = task
        # Atomic in the single-threaded loop: no await between register and here.
        self._registry.mark_running(call_id)
        return task

    # ----------------------------------------------------------------- cancel
    def cancel(self, call_id: str) -> bool:
        """Advisorily cancel a running call. Idempotent.

        ``CANCEL_REQUESTED`` is recorded *before* the asyncio signal is sent
        (tombstone first), then ``Task.cancel()`` is attempted.

        Returns ``True`` only if a live ``asyncio.Task`` was signalled.
        Returns ``False`` when the record is already terminal, when no
        asyncio task backs the record, or when the task had already finished.

        Raises :class:`UnknownCallError` for an unknown ``call_id`` — never
        silently ignored.
        """
        record = self._registry.get(call_id)  # explicit unknown-call handling
        if record.state.is_terminal:
            return False  # nothing left to cancel; repeat-safe no-op
        self._registry.request_cancel(call_id)  # idempotent
        task = self._tasks.get(call_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    # ----------------------------------------------------------------- lookup
    def task_for(self, call_id: str) -> asyncio.Task:
        """Return the ``asyncio.Task`` for ``call_id`` (retained after settle)."""
        task = self._tasks.get(call_id)
        if task is None:
            raise UnknownCallError(
                f"call_id {call_id!r} was not started by this supervisor"
            )
        return task

    def result(self, call_id: str) -> Any:
        """Return value of a succeeded call, else ``None`` (retained forever)."""
        self._registry.get(call_id)  # explicit unknown-call handling
        return self._results.get(call_id)

    def error(self, call_id: str) -> Optional[BaseException]:
        """Exception raised by a failed call, else ``None`` (retained forever)."""
        self._registry.get(call_id)
        return self._errors.get(call_id)

    # --------------------------------------------------------------- internals
    async def _run(self, call_id: str, coro_factory: CoroFactory) -> Any:
        """Run the tool and settle the registry; returns the tool's value.

        Exceptions are swallowed after ``FAILED`` is recorded (a tool must
        never crash the supervisor); ``CancelledError`` is re-raised per the
        asyncio contract.
        """
        try:
            outcome = coro_factory()
            if not inspect.isawaitable(outcome):
                raise TypeError(
                    f"coro_factory for {call_id!r} returned "
                    f"{type(outcome).__name__}, which is not awaitable"
                )
            value = await outcome
        except asyncio.CancelledError:
            self._settle(call_id, self._registry.mark_cancelled)
            raise  # keep standard asyncio cancellation semantics
        except Exception as exc:  # never propagate a tool failure
            self._errors[call_id] = exc
            self._settle(call_id, self._registry.mark_failed)
        else:
            self._results[call_id] = value
            self._settle(call_id, self._registry.mark_succeeded)
            return value

    def _ensure_settled(self, call_id: str, task: asyncio.Task) -> None:
        """Done-callback: guarantee a terminal state even if ``_run`` never ran."""
        record = self._registry.get(call_id)
        if record.state.is_terminal:
            return  # the wrapper already settled it (first settle wins)
        if task.cancelled():
            self._settle(call_id, self._registry.mark_cancelled)
            return
        exc = task.exception()  # also marks it retrieved (no loop warnings)
        if exc is not None:
            self._errors[call_id] = exc
            self._settle(call_id, self._registry.mark_failed)
        else:
            self._settle(call_id, self._registry.mark_succeeded)

    def _settle(self, call_id: str, settle: Callable[[str], TaskRecord]) -> None:
        try:
            settle(call_id)
        except InvalidTransition:
            # Already settled elsewhere (e.g. cancel raced completion, or the
            # tool ignored cancellation and finished first). First settle wins.
            pass
