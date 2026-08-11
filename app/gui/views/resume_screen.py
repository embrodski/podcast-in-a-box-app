"""Screen A2 — pick a session to resume."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from app.controller.session_store import PIAB_STATE_FILENAME
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label


class ResumeScreen(ScreenWidget):
    screen_id = "A2"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._sessions: list[Path] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(
            body_label("Choose a previous session folder to continue where you left off.")
        )

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.itemDoubleClicked.connect(self._open_selected)
        layout.addWidget(self._list, stretch=1)

        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self._list)
        copy_shortcut.activated.connect(self._copy_list_selection)

        row = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(lambda: self.navigate.emit("A1"))
        row.addWidget(back)
        browse = QPushButton("Browse folder…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        row.addStretch()
        open_btn = QPushButton("Open")
        open_btn.setDefault(True)
        open_btn.clicked.connect(self._open_selected)
        row.addWidget(open_btn)
        layout.addLayout(row)

    def on_enter(self) -> None:
        self._reload()

    def _reload(self) -> None:
        self._list.clear()
        self._sessions = self.controller.list_recent_sessions(limit=25)
        if not self._sessions:
            self._list.addItem("(No recent sessions found under E:\\PodcastRoom)")
            return
        for folder in self._sessions:
            screen = self.controller.resume_screen_for(folder)
            item = QListWidgetItem(f"{folder.name}  —  continue at {screen}")
            item.setData(256, str(folder))
            self._list.addItem(item)

    def _copy_list_selection(self) -> None:
        items = self._list.selectedItems()
        if not items:
            current = self._list.currentItem()
            if current is not None:
                items = [current]
        text = "\n".join(item.text() for item in items if item and item.text())
        if text:
            QApplication.clipboard().setText(text)

    def _selected_folder(self) -> Path | None:
        item = self._list.currentItem()
        if item is None:
            return None
        raw = item.data(256)
        if not raw:
            return None
        return Path(str(raw))

    def _open_selected(self) -> None:
        folder = self._selected_folder()
        if folder is None:
            return
        self._resume_folder(folder)

    def _browse(self) -> None:
        start = str(self.controller.scan_root)
        chosen = QFileDialog.getExistingDirectory(self, "Select session folder", start)
        if not chosen:
            return
        folder = Path(chosen)
        if not (folder / PIAB_STATE_FILENAME).is_file():
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                "Not a PIAB session",
                f"No {PIAB_STATE_FILENAME} found in:\n{folder}",
            )
            return
        self._resume_folder(folder)

    def _resume_folder(self, folder: Path) -> None:
        screen = self.controller.resume_screen_for(folder)
        self.navigate_session.emit(screen, folder)
