"""Tests for the Step 2 session mailbox (aura.runtime.mailbox).

No arbitrary sleeps: waiting is done through asyncio primitives, with
``asyncio.wait_for`` used only as a safety timeout around tasks that are
expected to complete.
"""

import asyncio
import unittest

from aura.runtime.events import Event, EventType
from aura.runtime.mailbox import (
    MailboxFull,
    Priority,
    SessionMailbox,
    SessionMismatchError,
)

SESSION = "sess-1"


def make_event(
    session_id: str = SESSION,
    event_type: EventType = EventType.USER_INPUT,
    payload: dict | None = None,
    seq: int = 0,
) -> Event:
    return Event(
        session_id=session_id,
        event_type=event_type,
        payload=payload or {},
        seq=seq,
    )


class MailboxBasics(unittest.IsolatedAsyncioTestCase):
    async def test_basic_put_get(self):
        mailbox = SessionMailbox(SESSION, maxsize=8)
        event = make_event(payload={"text": "hi"})
        accepted = await mailbox.put(event)
        got = await mailbox.get()
        self.assertEqual(got.event_id, accepted.event_id)
        self.assertEqual(got.payload["text"], "hi")
        self.assertTrue(mailbox.empty())

    async def test_get_waits_for_first_event(self):
        mailbox = SessionMailbox(SESSION, maxsize=8)
        waiter = asyncio.create_task(mailbox.get())
        self.assertFalse(waiter.done())  # deterministic: not yet scheduled
        await mailbox.put(make_event(payload={"i": 1}))
        got = await asyncio.wait_for(waiter, timeout=2)
        self.assertEqual(got.payload["i"], 1)

    async def test_wrong_session_is_rejected(self):
        mailbox = SessionMailbox(SESSION, maxsize=8)
        for prio in (Priority.CONTROL, Priority.DATA):
            with self.assertRaises(SessionMismatchError):
                await mailbox.put(make_event(session_id="other"), priority=prio)
        self.assertEqual(mailbox.qsize, 0)  # nothing leaked in

    async def test_non_event_is_rejected(self):
        mailbox = SessionMailbox(SESSION, maxsize=8)
        with self.assertRaises(TypeError):
            await mailbox.put({"not": "an event"})

    async def test_unknown_priority_is_rejected(self):
        mailbox = SessionMailbox(SESSION, maxsize=8)
        with self.assertRaises(ValueError):
            await mailbox.put(make_event(), priority="urgent")
        self.assertEqual(mailbox.qsize, 0)

    async def test_invalid_constructor_arguments(self):
        with self.assertRaises(ValueError):
            SessionMailbox("", maxsize=8)
        with self.assertRaises(ValueError):
            SessionMailbox(SESSION, maxsize=0)


class MailboxOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_fifo_ordering_for_data(self):
        mailbox = SessionMailbox(SESSION, maxsize=16)
        for i in range(5):
            await mailbox.put(make_event(payload={"i": i}), priority=Priority.DATA)
        got = [await mailbox.get() for _ in range(5)]
        self.assertEqual([e.payload["i"] for e in got], [0, 1, 2, 3, 4])

    async def test_fifo_ordering_for_control(self):
        mailbox = SessionMailbox(SESSION, maxsize=16)
        for i in range(5):
            await mailbox.put(make_event(payload={"i": i}), priority=Priority.CONTROL)
        got = [await mailbox.get() for _ in range(5)]
        self.assertEqual([e.payload["i"] for e in got], [0, 1, 2, 3, 4])

    async def test_control_is_consumed_before_data(self):
        mailbox = SessionMailbox(SESSION, maxsize=16)
        await mailbox.put(make_event(payload={"name": "d1"}), priority=Priority.DATA)
        await mailbox.put(make_event(payload={"name": "c1"}), priority=Priority.CONTROL)
        await mailbox.put(make_event(payload={"name": "d2"}), priority=Priority.DATA)
        await mailbox.put(make_event(payload={"name": "c2"}), priority=Priority.CONTROL)

        got = [await mailbox.get() for _ in range(4)]
        self.assertEqual(
            [e.payload["name"] for e in got], ["c1", "c2", "d1", "d2"]
        )

    async def test_lane_sizes_report_each_priority(self):
        mailbox = SessionMailbox(SESSION, maxsize=16)
        await mailbox.put(make_event(), priority=Priority.DATA)
        await mailbox.put(make_event(), priority=Priority.CONTROL)
        await mailbox.put(make_event(), priority=Priority.DATA)
        self.assertEqual(mailbox.control_size, 1)
        self.assertEqual(mailbox.data_size, 2)
        self.assertEqual(mailbox.qsize, 3)  # both lanes together

        await mailbox.get()  # the CONTROL lane drains first
        self.assertEqual(mailbox.control_size, 0)
        self.assertEqual(mailbox.data_size, 2)  # DATA waits its turn

    async def test_seq_is_monotonically_increasing(self):
        mailbox = SessionMailbox(SESSION, maxsize=16)
        for i in range(5):
            accepted = await mailbox.put(make_event(payload={"i": i}))
            self.assertEqual(accepted.seq, i + 1)
        got = [await mailbox.get() for _ in range(5)]
        seqs = [e.seq for e in got]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), 5)

    async def test_producer_provided_seq_is_not_trusted(self):
        mailbox = SessionMailbox(SESSION, maxsize=16)
        forged = make_event(payload={"name": "forged"}, seq=999)
        accepted = await mailbox.put(forged)
        second = await mailbox.put(make_event(payload={"name": "second"}, seq=7))

        self.assertEqual(accepted.seq, 1)  # mailbox-assigned, not 999
        self.assertEqual(second.seq, 2)  # not 7
        self.assertEqual(forged.seq, 999)  # original event untouched (immutable)

        first = await mailbox.get()
        await mailbox.get()
        self.assertEqual(first.seq, 1)

    async def test_control_priority_does_not_disturb_acceptance_seq(self):
        mailbox = SessionMailbox(SESSION, maxsize=16)
        d1 = await mailbox.put(make_event(payload={"name": "d1"}), Priority.DATA)
        c1 = await mailbox.put(make_event(payload={"name": "c1"}), Priority.CONTROL)
        d2 = await mailbox.put(make_event(payload={"name": "d2"}), Priority.DATA)
        c2 = await mailbox.put(make_event(payload={"name": "c2"}), Priority.CONTROL)
        # seq reflects acceptance (enqueue) order, not delivery order.
        self.assertEqual([d1.seq, c1.seq, d2.seq, c2.seq], [1, 2, 3, 4])

        got = [await mailbox.get() for _ in range(4)]
        self.assertEqual([e.payload["name"] for e in got], ["c1", "c2", "d1", "d2"])
        self.assertEqual([e.seq for e in got], [2, 4, 1, 3])


class MailboxCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_data_put_fails_loudly_when_full(self):
        mailbox = SessionMailbox(SESSION, maxsize=1)
        await mailbox.put(make_event(payload={"i": 0}))
        with self.assertRaises(MailboxFull):
            await mailbox.put(make_event(payload={"i": 1}))
        # Rejected event was not enqueued: capacity is untouched, nothing lost silently.
        self.assertEqual(mailbox.qsize, 1)
        self.assertEqual(mailbox.unfinished_tasks, 1)

    async def test_control_waits_when_full_and_is_never_dropped(self):
        mailbox = SessionMailbox(SESSION, maxsize=1)
        await mailbox.put(make_event(payload={"name": "d-first"}), Priority.DATA)
        self.assertTrue(mailbox.full())

        control_task = asyncio.create_task(
            mailbox.put(make_event(payload={"name": "c"}), Priority.CONTROL)
        )
        self.assertFalse(control_task.done())  # no await yet -> still waiting

        first = await mailbox.get()  # frees space
        self.assertEqual(first.payload["name"], "d-first")

        accepted = await asyncio.wait_for(control_task, timeout=2)
        self.assertEqual(accepted.seq, 2)
        self.assertEqual(mailbox.qsize, 1)

        remaining = await mailbox.get()
        self.assertEqual(remaining.payload["name"], "c")
        self.assertEqual(remaining.event_id, accepted.event_id)

    async def test_capacity_spans_both_priorities(self):
        mailbox = SessionMailbox(SESSION, maxsize=2)
        await mailbox.put(make_event(payload={"i": 0}), Priority.CONTROL)
        await mailbox.put(make_event(payload={"i": 1}), Priority.DATA)
        self.assertTrue(mailbox.full())
        with self.assertRaises(MailboxFull):
            await mailbox.put(make_event(payload={"i": 2}), Priority.DATA)


class MailboxConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_async_producers(self):
        mailbox = SessionMailbox(SESSION, maxsize=64)

        async def producer(pid: int) -> None:
            for i in range(10):
                await mailbox.put(
                    make_event(payload={"p": pid, "i": i}), Priority.DATA
                )

        await asyncio.gather(*(producer(pid) for pid in range(4)))

        self.assertEqual(mailbox.qsize, 40)
        received = [await mailbox.get() for _ in range(40)]

        seqs = [e.seq for e in received]
        self.assertEqual(sorted(seqs), list(range(1, 41)))  # unique + gap-free
        self.assertEqual(seqs, sorted(seqs))  # uniform priority -> FIFO == seq order

        for pid in range(4):  # per-producer FIFO preserved
            indices = [e.payload["i"] for e in received if e.payload["p"] == pid]
            self.assertEqual(indices, list(range(10)))

    async def test_task_done_and_join(self):
        mailbox = SessionMailbox(SESSION, maxsize=8)
        await mailbox.join()  # nothing pending -> returns immediately

        for i in range(3):
            await mailbox.put(make_event(payload={"i": i}))
        self.assertEqual(mailbox.unfinished_tasks, 3)

        for _ in range(3):
            await mailbox.get()
            mailbox.task_done()
        self.assertEqual(mailbox.unfinished_tasks, 0)
        await asyncio.wait_for(mailbox.join(), timeout=2)

    async def test_join_blocks_until_task_done(self):
        mailbox = SessionMailbox(SESSION, maxsize=8)
        await mailbox.put(make_event())
        await mailbox.get()

        join_task = asyncio.create_task(mailbox.join())
        self.assertFalse(join_task.done())
        mailbox.task_done()
        await asyncio.wait_for(join_task, timeout=2)
        self.assertTrue(join_task.done())

    async def test_task_done_too_many_times_raises(self):
        mailbox = SessionMailbox(SESSION, maxsize=8)
        with self.assertRaises(ValueError):
            mailbox.task_done()
        await mailbox.put(make_event())
        await mailbox.get()
        mailbox.task_done()
        with self.assertRaises(ValueError):
            mailbox.task_done()

    async def test_async_consumer_loop_with_sentinel(self):
        mailbox = SessionMailbox(SESSION, maxsize=16)
        collected: list[int] = []

        async def consumer() -> None:
            while True:
                event = await mailbox.get()
                mailbox.task_done()
                if event.event_type is EventType.RUNTIME_ERROR:
                    return
                collected.append(event.payload["i"])

        consumer_task = asyncio.create_task(consumer())
        for i in range(5):
            await mailbox.put(make_event(payload={"i": i}))
        await mailbox.put(make_event(event_type=EventType.RUNTIME_ERROR))

        await asyncio.wait_for(consumer_task, timeout=2)
        self.assertEqual(collected, [0, 1, 2, 3, 4])
        await asyncio.wait_for(mailbox.join(), timeout=2)


if __name__ == "__main__":
    unittest.main()
