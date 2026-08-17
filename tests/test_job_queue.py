"""Tests for the persistent Fast Preview / Full job queue."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.controller.job_queue import JobQueueStore


class JobQueueTests(unittest.TestCase):
    def test_enqueue_fifo_and_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobQueueStore(Path(tmp) / "queue.json")
            a = Path(tmp) / "a"
            b = Path(tmp) / "b"
            a.mkdir()
            b.mkdir()
            store.enqueue(a, "full", name="A")
            store.enqueue(b, "full", name="B")
            current, waiting = store.full_current_and_waiting()
            self.assertIsNone(current)
            self.assertEqual([row.name for row in waiting], ["A", "B"])
            nxt = store.next_queued("full")
            self.assertIsNotNone(nxt)
            assert nxt is not None
            self.assertEqual(nxt.name, "A")
            store.mark_running(a, "full")
            current, waiting = store.full_current_and_waiting()
            self.assertEqual(current.name if current else None, "A")
            self.assertEqual([row.name for row in waiting], ["B"])
            self.assertTrue(store.cancel(a, "full"))
            current, waiting = store.full_current_and_waiting()
            self.assertIsNone(current)
            self.assertEqual([row.name for row in waiting], ["B"])

    def test_interrupt_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobQueueStore(Path(tmp) / "queue.json")
            folder = Path(tmp) / "sess"
            folder.mkdir()
            store.enqueue(folder, "fast_preview", name="S")
            store.mark_running(folder, "fast_preview")
            found = store.interrupt_running()
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].status, "interrupted")
            self.assertEqual(len(store.interrupted()), 1)

    def test_requeue_stale_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobQueueStore(Path(tmp) / "queue.json")
            folder = Path(tmp) / "sess"
            folder.mkdir()
            store.enqueue(folder, "full", name="S")
            store.mark_running(folder, "full")
            found = store.requeue_stale_running("full")
            self.assertEqual(len(found), 1)
            self.assertEqual(store.entry_for(folder, "full").status, "queued")

    def test_hold_removes_from_auto_queue_and_resume_requeues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobQueueStore(Path(tmp) / "queue.json")
            folder = Path(tmp) / "sess"
            other = Path(tmp) / "other"
            folder.mkdir()
            other.mkdir()
            store.enqueue(folder, "full", name="S")
            store.enqueue(other, "full", name="O")
            held = store.hold(folder, "full")
            self.assertIsNotNone(held)
            assert held is not None
            self.assertEqual(held.status, "held")
            nxt = store.next_queued("full")
            self.assertIsNotNone(nxt)
            assert nxt is not None
            self.assertEqual(nxt.name, "O")
            current, waiting = store.full_current_and_waiting()
            self.assertIsNone(current)
            self.assertEqual([row.name for row in waiting], ["O"])
            self.assertEqual(len(store.held()), 1)
            self.assertIn(folder.resolve(), store.protected_folders())
            self.assertIsNone(store.hold(folder, "full"))
            store.hold(other, "full")
            self.assertFalse(store.has_running_or_queued())
            again = store.enqueue(folder, "full", name="S")
            self.assertEqual(again.status, "queued")
            self.assertEqual(store.next_queued("full").name, "S")
            self.assertEqual(len(store.held()), 1)

    def test_hold_ignores_running_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobQueueStore(Path(tmp) / "queue.json")
            folder = Path(tmp) / "sess"
            folder.mkdir()
            store.enqueue(folder, "fast_preview", name="S")
            store.mark_running(folder, "fast_preview")
            self.assertIsNone(store.hold(folder, "fast_preview"))
            self.assertEqual(store.entry_for(folder, "fast_preview").status, "running")

    def test_has_running_or_queued_ignores_failed_and_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobQueueStore(Path(tmp) / "queue.json")
            folder = Path(tmp) / "sess"
            folder.mkdir()
            self.assertFalse(store.has_running_or_queued())
            store.enqueue(folder, "full", name="S")
            self.assertTrue(store.has_running_or_queued())
            store.mark_running(folder, "full")
            self.assertTrue(store.has_running_or_queued())
            store.mark_failed(folder, "full")
            self.assertFalse(store.has_running_or_queued())
            self.assertTrue(store.has_active_work())


if __name__ == "__main__":
    unittest.main()
