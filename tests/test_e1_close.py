"""E1 title-bar close should confirm-and-abort like F4."""

from __future__ import annotations

import unittest

from app.gui.views.processing_screen import e1_close_requires_confirm


class E1CloseConfirmTests(unittest.TestCase):
    def test_running_job_requires_confirm(self) -> None:
        self.assertTrue(
            e1_close_requires_confirm(has_running_job=True, queue_status="running")
        )

    def test_queued_job_requires_confirm(self) -> None:
        self.assertTrue(
            e1_close_requires_confirm(has_running_job=False, queue_status="queued")
        )

    def test_held_job_closes_without_confirm(self) -> None:
        self.assertFalse(
            e1_close_requires_confirm(has_running_job=False, queue_status="held")
        )

    def test_no_queue_entry_closes_without_confirm(self) -> None:
        self.assertFalse(
            e1_close_requires_confirm(has_running_job=False, queue_status=None)
        )
