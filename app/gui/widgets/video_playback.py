"""Shared video playback controls (seek bar, skip buttons)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

_SKIP_MS = 10_000


def format_playback_ms(ms: int) -> str:
    total_sec = max(0, ms) // 1000
    minutes, seconds = divmod(total_sec, 60)
    return f"{minutes}:{seconds:02d}"


class MediaTimelineControls(QWidget):
    """Seek bar and ±10 second skip buttons for one or more media players."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._players: list[QMediaPlayer] = []
        self._seeking = False
        self._duration_ms = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._back_10 = QPushButton("−10 sec")
        self._back_10.setFixedWidth(72)
        self._back_10.clicked.connect(self._skip_backward)
        layout.addWidget(self._back_10)

        self._time_current = QLabel("0:00")
        self._time_current.setFixedWidth(40)
        self._time_current.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._time_current)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.sliderPressed.connect(self._begin_seek)
        self._slider.sliderReleased.connect(self._finish_seek)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        layout.addWidget(self._slider, stretch=1)

        self._time_total = QLabel("0:00")
        self._time_total.setFixedWidth(40)
        self._time_total.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._time_total)

        self._fwd_10 = QPushButton("+10 sec")
        self._fwd_10.setFixedWidth(72)
        self._fwd_10.clicked.connect(self._skip_forward)
        layout.addWidget(self._fwd_10)

        self.set_enabled(False)

    def bind_players(self, *players: QMediaPlayer) -> None:
        for player in self._players:
            try:
                player.durationChanged.disconnect(self._on_duration_changed)
                player.positionChanged.disconnect(self._on_position_changed)
            except (RuntimeError, TypeError):
                pass

        self._players = list(players)
        for player in self._players:
            player.durationChanged.connect(self._on_duration_changed)
            player.positionChanged.connect(self._on_position_changed)

    def reset(self) -> None:
        self._seeking = False
        self._duration_ms = 0
        self._slider.setRange(0, 0)
        self._slider.setValue(0)
        self._time_current.setText("0:00")
        self._time_total.setText("0:00")
        self.set_enabled(False)

    def set_enabled(self, enabled: bool) -> None:
        self._slider.setEnabled(enabled)
        self._back_10.setEnabled(enabled)
        self._fwd_10.setEnabled(enabled)

    def _begin_seek(self) -> None:
        self._seeking = True

    def _finish_seek(self) -> None:
        self._apply_position(self._slider.value())
        self._seeking = False

    def _on_slider_moved(self, value: int) -> None:
        self._time_current.setText(format_playback_ms(value))

    def _apply_position(self, ms: int) -> None:
        clamped = max(0, ms)
        if self._duration_ms > 0:
            clamped = min(clamped, self._duration_ms)
        for player in self._players:
            player.setPosition(clamped)

    def _on_position_changed(self, position: int) -> None:
        if self._seeking:
            return
        self._slider.blockSignals(True)
        self._slider.setValue(position)
        self._slider.blockSignals(False)
        self._time_current.setText(format_playback_ms(position))

    def _on_duration_changed(self, duration: int) -> None:
        if duration <= self._duration_ms:
            return
        self._duration_ms = duration
        self._slider.setRange(0, max(duration, 0))
        self._time_total.setText(format_playback_ms(duration))
        self.set_enabled(duration > 0)

    def _skip_backward(self) -> None:
        if not self._players:
            return
        self._apply_position(self._players[0].position() - _SKIP_MS)

    def _skip_forward(self) -> None:
        if not self._players:
            return
        target = self._players[0].position() + _SKIP_MS
        if self._duration_ms > 0:
            target = min(target, self._duration_ms)
        self._apply_position(target)


class SingleVideoReviewPane(QWidget):
    """Video surface with timeline and play/pause for one clip."""

    def __init__(self, parent: QWidget | None = None, *, min_video_height: int = 280) -> None:
        super().__init__(parent)

        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._video = QVideoWidget()
        self._video.setMinimumHeight(min_video_height)
        self._player.setVideoOutput(self._video)
        layout.addWidget(self._video, stretch=1)

        self._timeline = MediaTimelineControls()
        self._timeline.bind_players(self._player)
        layout.addWidget(self._timeline)

        controls = QHBoxLayout()
        self._play = QPushButton("Play")
        self._play.clicked.connect(self._toggle_playback)
        controls.addWidget(self._play)
        controls.addStretch()
        layout.addLayout(controls)

    @property
    def player(self) -> QMediaPlayer:
        return self._player

    @property
    def play_button(self) -> QPushButton:
        return self._play

    def set_source(self, path: Path) -> None:
        self.stop()
        self._timeline.reset()
        self._player.setSource(QUrl.fromLocalFile(str(path)))

    def stop(self) -> None:
        self._player.stop()
        self._play.setText("Play")

    def set_controls_enabled(self, enabled: bool) -> None:
        self._play.setEnabled(enabled)
        if not enabled:
            self._timeline.set_enabled(False)

    def _toggle_playback(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._play.setText("Play")
        else:
            self._player.play()
            self._play.setText("Pause")
