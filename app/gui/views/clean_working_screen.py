"""Screen A4 — Clean Old Working Files."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.controller.paths import DEFAULT_WORK_ROOT
from app.gui.dialogs import confirm_action
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label
from app.gui.widgets.worker import CallableWorker


class CleanWorkingFilesScreen(ScreenWidget):
    """A4 — choose logged projects and remove Raw/Input/Temp/Preview Files."""

    screen_id = "A4"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._checks: list[tuple[QCheckBox, Path]] = []
        self._worker: CallableWorker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(heading_label("Clean Old Working Files"))
        layout.addWidget(
            body_label(
                "These projects still have working folders (Raw, Input, Temp, or "
                "Preview Files) besides Output. Select projects to clean — Output "
                "is kept; other working folders are moved to the Recycle Bin."
            )
        )

        self._empty = body_label("No projects need cleaning.")
        self._empty.hide()
        layout.addWidget(self._empty)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._list_host)
        layout.addWidget(self._scroll, stretch=1)

        self._scan = QPushButton("Scan For Lost Sessions")
        self._scan.setMinimumHeight(40)
        self._scan.clicked.connect(self._on_scan)
        layout.addWidget(self._scan)

        self._status = body_label("")
        self._status.setStyleSheet("color: #666;")
        layout.addWidget(self._status)

        row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self._leave)
        row.addWidget(cancel)
        row.addStretch()
        self._clean = QPushButton("Clean")
        self._clean.setMinimumHeight(40)
        self._clean.clicked.connect(self._on_clean)
        row.addWidget(self._clean)
        layout.addLayout(row)

    def on_enter(self) -> None:
        self._status.setText("")
        self._reload_list()

    def _return_screen(self) -> str:
        ctx = self.context()
        if ctx is not None and ctx.clean_return_screen:
            return ctx.clean_return_screen
        return "A1"

    def _leave(self) -> None:
        target = self._return_screen()
        ctx = self.context()
        if ctx is not None:
            ctx.clean_return_screen = None
        self.navigate.emit(target)

    def _clear_list(self) -> None:
        self._checks.clear()
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _reload_list(self) -> None:
        self._clear_list()
        try:
            candidates = self.controller.list_clean_working_candidates()
        except Exception as exc:
            self._empty.setText(f"Could not read the process log:\n{exc}")
            self._empty.show()
            self._scroll.hide()
            self._clean.setEnabled(False)
            return

        if not candidates:
            self._empty.setText("No projects need cleaning.")
            self._empty.show()
            self._scroll.hide()
            self._clean.setEnabled(False)
            return

        self._empty.hide()
        self._scroll.show()
        self._clean.setEnabled(True)

        for row in candidates:
            folder = Path(str(row["project_folder"]))
            name = str(row.get("name") or folder.name)
            removable = row.get("removable_subfolders") or []
            email = row.get("email")
            begun = row.get("begun_at") or ""
            detail_bits = [", ".join(str(x) for x in removable)]
            if email:
                detail_bits.append(str(email))
            if begun:
                detail_bits.append(f"begun {begun}")
            label = f"{name}\n{folder}\n({'; '.join(detail_bits)})"
            check = QCheckBox(label)
            check.setStyleSheet("QCheckBox { padding: 8px 4px; }")
            self._list_layout.addWidget(check)
            self._checks.append((check, folder))

    def _on_scan(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._scan.setEnabled(False)
        self._clean.setEnabled(False)
        self._status.setText(f"Scanning {DEFAULT_WORK_ROOT} for lost sessions…")
        self._worker = CallableWorker(self.controller.scan_lost_clean_sessions)
        self._worker.finished_ok.connect(self._on_scan_done)
        self._worker.failed.connect(self._on_scan_failed)
        self._worker.start()

    def _on_scan_done(self, found: object) -> None:
        self._scan.setEnabled(True)
        rows = found if isinstance(found, list) else []
        self._reload_list()
        if rows:
            self._status.setText(f"Found {len(rows)} lost session(s).")
        else:
            self._status.setText("No lost sessions found.")

    def _on_scan_failed(self, message: str) -> None:
        self._scan.setEnabled(True)
        self._reload_list()
        self._status.setText("Scan failed.")
        QMessageBox.warning(self, "Scan failed", message)

    def _selected_folders(self) -> list[Path]:
        return [folder for check, folder in self._checks if check.isChecked()]

    def _on_clean(self) -> None:
        selected = self._selected_folders()
        if not selected:
            QMessageBox.information(
                self,
                "Nothing selected",
                "Check one or more projects before clicking Clean.",
            )
            return
        if self._worker is not None and self._worker.isRunning():
            return

        names = "\n".join(f"• {p.name}" for p in selected)
        if not confirm_action(
            self,
            title="Clean selected projects?",
            text=f"Move working folders to the Recycle Bin for {len(selected)} project(s)?",
            detail=(
                f"{names}\n\n"
                "Removes Raw, Input, Temp, and Preview Files. Output is kept."
            ),
        ):
            return

        self._clean.setEnabled(False)
        self._scan.setEnabled(False)
        self._status.setText("Cleaning…")
        self._worker = CallableWorker(self._run_clean, selected)
        self._worker.finished_ok.connect(self._on_clean_done)
        self._worker.failed.connect(self._on_clean_failed)
        self._worker.start()

    def _run_clean(self, folders: list[Path]) -> list[dict]:
        return self.controller.clean_working_files(folders)

    def _on_clean_done(self, results: object) -> None:
        self._scan.setEnabled(True)
        self._clean.setEnabled(True)
        rows = results if isinstance(results, list) else []
        deleted_count = sum(len(r.get("deleted") or []) for r in rows if isinstance(r, dict))
        error_lines: list[str] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            folder = r.get("project_folder", "")
            for err in r.get("errors") or []:
                error_lines.append(f"{folder}: {err}")

        self._reload_list()
        if deleted_count:
            QMessageBox.information(
                self,
                "Remember to empty your Recycle Bin",
                "Working folders were moved to the Recycle Bin.\n\n"
                "Remember to empty your Recycle Bin to reclaim disk space.",
            )
        if error_lines:
            self._status.setText("Finished with errors — see details.")
            QMessageBox.warning(
                self,
                "Some folders could not be cleaned",
                "\n".join(error_lines[:20]),
            )
            return
        if deleted_count:
            self._status.setText(f"Cleaned {deleted_count} working folder(s).")
            self._leave()
            return
        self._status.setText("Nothing was deleted.")

    def _on_clean_failed(self, message: str) -> None:
        self._scan.setEnabled(True)
        self._clean.setEnabled(True)
        self._status.setText("Clean failed.")
        QMessageBox.critical(self, "Clean failed", message)
