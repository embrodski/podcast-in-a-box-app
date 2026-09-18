"""F2a — side-by-side A/B sync offset choice."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.gui.widgets.path_banner import PathBanner
from app.gui.widgets.screen_base import ScreenWidget
from app.gui.widgets.selectable_text import body_label, heading_label
from app.gui.widgets.video_playback import MediaTimelineControls, close_media_player
from app.gui.widgets.worker import CallableWorker, start_callable_worker


class SyncOffsetReviewScreen(ScreenWidget):
    """F2a — side-by-side A/B sync offset choice before general 1-min approval."""

    screen_id = "F2a"

    def __init__(self, controller, parent=None) -> None:
        super().__init__(controller, parent)
        self._worker: CallableWorker | None = None
        self._no_offset_path: Path | None = None
        self._forced_path: Path | None = None

        self._player_left = QMediaPlayer(self)
        self._audio_left = QAudioOutput(self)
        self._player_left.setAudioOutput(self._audio_left)

        self._player_right = QMediaPlayer(self)
        self._audio_right = QAudioOutput(self)
        self._player_right.setAudioOutput(self._audio_right)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(heading_label("Which sync sounds better?"))
        layout.addWidget(
            body_label(
                "Video sync was uncertain, so two 1-minute previews were rendered. "
                "They differ only in audio offset — watch both and pick the one with "
                "better lip sync. You will review cameras and speakers on the next screen."
            )
        )

        self._status = body_label("")
        layout.addWidget(self._status)

        row = QHBoxLayout()
        row.setSpacing(12)

        left_col = QVBoxLayout()
        left_col.addWidget(body_label("Start-aligned (no offset)"))
        self._video_left = QVideoWidget()
        self._video_left.setMinimumHeight(320)
        self._player_left.setVideoOutput(self._video_left)
        left_col.addWidget(self._video_left, stretch=1)
        self._play_left = QPushButton("Play left")
        self._play_left.clicked.connect(lambda: self._toggle(self._player_left, self._play_left))
        left_col.addWidget(self._play_left)
        row.addLayout(left_col, stretch=1)

        right_col = QVBoxLayout()
        right_col.addWidget(body_label("Forced detected offset"))
        self._video_right = QVideoWidget()
        self._video_right.setMinimumHeight(320)
        self._player_right.setVideoOutput(self._video_right)
        right_col.addWidget(self._video_right, stretch=1)
        self._play_right = QPushButton("Play right")
        self._play_right.clicked.connect(lambda: self._toggle(self._player_right, self._play_right))
        right_col.addWidget(self._play_right)
        row.addLayout(right_col, stretch=1)

        layout.addLayout(row, stretch=1)

        self._timeline = MediaTimelineControls()
        self._timeline.bind_players(self._player_left, self._player_right)
        layout.addWidget(self._timeline)

        both_row = QHBoxLayout()
        self._play_both = QPushButton("Play both")
        self._play_both.clicked.connect(self._play_both_clips)
        both_row.addWidget(self._play_both)
        self._pause_both = QPushButton("Pause both")
        self._pause_both.clicked.connect(self._pause_both_clips)
        both_row.addWidget(self._pause_both)
        both_row.addStretch()
        layout.addLayout(both_row)

        self._banner = PathBanner()
        layout.addWidget(self._banner)

        actions = QHBoxLayout()
        start_btn = QPushButton("Use start-aligned (left)")
        start_btn.setMinimumHeight(44)
        start_btn.clicked.connect(lambda: self._choose("start_aligned"))
        actions.addWidget(start_btn)

        forced_btn = QPushButton("Use forced offset (right)")
        forced_btn.setMinimumHeight(44)
        forced_btn.setDefault(True)
        forced_btn.clicked.connect(lambda: self._choose("forced_offset"))
        actions.addWidget(forced_btn)
        layout.addLayout(actions)

    def on_enter(self) -> None:
        self._player_left.stop()
        self._player_right.stop()
        self._timeline.reset()
        self._play_left.setText("Play left")
        self._play_right.setText("Play right")

        folder = self.session_folder()
        if folder is None:
            self._status.setText("No session folder.")
            self._banner.set_path(None)
            self.setEnabled(False)
            return

        self.setEnabled(True)
        self._banner.set_path(folder)

        try:
            state = self.controller.load_session_state(folder)
            no_path, forced_path = self.controller.resolve_ab_test_paths(state, folder)
        except Exception as exc:
            self._status.setText(str(exc))
            self.setEnabled(False)
            return

        self._no_offset_path = no_path
        self._forced_path = forced_path
        self._status.setText(
            f"Left: {no_path.name}  ·  Right: {forced_path.name}"
        )
        self._player_left.setSource(QUrl.fromLocalFile(str(no_path)))
        self._player_right.setSource(QUrl.fromLocalFile(str(forced_path)))

    def on_leave(self) -> None:
        close_media_player(self._player_left)
        close_media_player(self._player_right)
        self._timeline.reset()
        self._play_left.setText("Play left")
        self._play_right.setText("Play right")

    def _toggle(self, player: QMediaPlayer, button: QPushButton) -> None:
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
            button.setText(button.text().replace("Pause", "Play"))
        else:
            player.play()
            button.setText(button.text().replace("Play", "Pause"))

    def _play_both_clips(self) -> None:
        self._player_left.play()
        self._player_right.play()
        self._play_left.setText("Pause left")
        self._play_right.setText("Pause right")

    def _pause_both_clips(self) -> None:
        self._player_left.pause()
        self._player_right.pause()
        self._play_left.setText("Play left")
        self._play_right.setText("Play right")

    def _choose(self, choice: str) -> None:
        folder = self.session_folder()
        if folder is None:
            return
        self._run_choice_worker(choice, folder)

    def _run_choice_worker(self, choice: str, folder: Path) -> None:
        self.setEnabled(False)
        self._status.setText("Saving your choice…")

        def _task() -> dict:
            return self.controller.record_sync_offset_choice(folder, choice)

        if start_callable_worker(
            self,
            _task,
            on_ok=self._on_choice_saved,
            on_fail=self._on_choice_failed,
        ) is None:
            self.setEnabled(True)

    def _on_choice_saved(self, _state: object) -> None:
        self.setEnabled(True)
        self.navigate.emit("F2")

    def _on_choice_failed(self, message: str) -> None:
        self.setEnabled(True)
        self._status.setText(message)
        QMessageBox.warning(self, "Could not save choice", message)
