"""AURA real-time / concurrency runtime (Member 2).

Responsibilities of this package:

- event processing and per-session event ordering
- mailbox / queues (ingress lanes, coalescing, backpressure, dead-letter)
- task registry and task lifecycle
- task supervision (spawn, watchdog, exactly-one outcome event)
- cancellation (tombstone-then-signal)
- stale-result protection (acceptance gate)
- the Coordinator (single writer per session)

Delivered so far:

- Step 1: importable package shell.
- Step 2: ``aura.runtime.events`` (immutable ``Event`` / ``EventType``) and
  ``aura.runtime.mailbox`` (``SessionMailbox``: bounded, CONTROL/DATA
  priority, per-session isolation, mailbox-assigned sequence numbers).

Planned modules (NOT yet created):

- ``aura.runtime.coordinator``
- ``aura.runtime.registry``
- ``aura.runtime.supervisor``
- ``aura.runtime.cancellation``
- ``aura.runtime.acceptance``

No coordinator, tools, planner, or state store exist yet.
"""
