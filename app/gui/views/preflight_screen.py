"""Screen A0 — startup preflight checks."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.controller.types import PreflightReport
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label

_STATUS_SYMBOLS = {"ok": "✓", "warn": "!", "fail": "✗"}


class PreflightWorker(QThread):
    finished = Signal(object)

    def __init__(self, controller) -> None:
        super().__init__()
        self._controller = controller

    def run(self) -> None:
        self.finished.emit(self._controller.run_preflight())


class PreflightScreen(ScreenWidget):
    screen_id = "A0"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._report: PreflightReport | None = None
        self._worker: PreflightWorker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        heading = heading_label("Checking this computer…")
        layout.addWidget(heading)

        self._subtitle = body_label(
            "Verifying vMix, FFmpeg, disk space, and other prerequisites."
        )
        layout.addWidget(self._subtitle)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["", "Check", "Status"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 28)
        self._table.setColumnWidth(1, 120)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        layout.addWidget(self._table, stretch=1)

        self._summary = body_label("")
        layout.addWidget(self._summary)

        row = QHBoxLayout()
        row.addStretch()
        self._retry = QPushButton("Check again")
        self._retry.clicked.connect(self._start_check)
        row.addWidget(self._retry)
        self._continue = QPushButton("Continue")
        self._continue.setDefault(True)
        self._continue.setEnabled(False)
        self._continue.clicked.connect(lambda: self.navigate.emit("A1"))
        row.addWidget(self._continue)
        layout.addLayout(row)

    def on_enter(self) -> None:
        self._start_check()

    def _start_check(self) -> None:
        self._retry.setEnabled(False)
        self._continue.setEnabled(False)
        self._summary.setText("Running checks…")
        self._table.setRowCount(0)
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = PreflightWorker(self.controller)
        self._worker.finished.connect(self._on_report)
        self._worker.start()

    def _on_report(self, report: PreflightReport) -> None:
        self._report = report
        self._table.setRowCount(len(report.checks))
        for row, check in enumerate(report.checks):
            symbol = QTableWidgetItem(_STATUS_SYMBOLS.get(check.status, "?"))
            symbol.setTextAlignment(Qt.AlignCenter)
            if check.status == "fail":
                symbol.setForeground(Qt.red)
            elif check.status == "warn":
                symbol.setForeground(Qt.darkYellow)
            else:
                symbol.setForeground(Qt.darkGreen)
            self._table.setItem(row, 0, symbol)
            self._table.setItem(row, 1, QTableWidgetItem(check.id))
            self._table.setItem(row, 2, QTableWidgetItem(check.message))

        can_continue = report.ok_for_recording or report.ok_for_autocut
        parts = []
        if report.ok_for_recording:
            parts.append("Recording is available.")
        else:
            parts.append("Recording is blocked until failed checks are fixed.")
        if report.ok_for_autocut:
            parts.append("Autocut is available.")
        else:
            parts.append("Autocut is blocked until failed checks are fixed.")
        self._summary.setText(" ".join(parts))
        self._continue.setEnabled(can_continue)
        self._retry.setEnabled(True)
