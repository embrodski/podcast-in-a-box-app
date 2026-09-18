"""Recording flow helpers for the controller."""

from __future__ import annotations

import html
from dataclasses import dataclass

from app.controller.paths import ensure_scripts_path


@dataclass(frozen=True)
class RecordingPhrases:
    trigger_phrase: str
    countdown_display: str | None
    end_phrase_display: str

    @property
    def start_phrase(self) -> str:
        """Legacy combined display string."""
        if self.countdown_display:
            return f"{self.trigger_phrase}, then {self.countdown_display}"
        return self.trigger_phrase


_PHRASE_COLOR_GREEN = "#4ade80"
_PHRASE_COLOR_BLUE = "#60a5fa"
_WARNING_COLOR_RED = "#f87171"
_PHRASE_FONT_SIZE = "20px"


def get_recording_phrases() -> RecordingPhrases:
    ensure_scripts_path()
    from piab_multicorder_record import format_end_phrase_display
    from podcast_phrase_gates import (
        end_phrases_from_gates,
        format_countdown_hint,
        load_phrase_gates,
        start_countdown_allow_in_from_gates,
        start_countdown_tokens_from_gates,
        start_trigger_phrase_from_gates,
    )

    gates = load_phrase_gates()
    trigger = start_trigger_phrase_from_gates(gates)
    end_phrases = end_phrases_from_gates(gates)
    end_display = format_end_phrase_display(end_phrases)
    if not trigger:
        raise RuntimeError(
            "Start trigger phrase is not configured in podcast-phrase-gates.json."
        )
    countdown = start_countdown_tokens_from_gates(gates)
    countdown_display = (
        format_countdown_hint(
            countdown,
            allow_in=start_countdown_allow_in_from_gates(gates),
        )
        if countdown
        else None
    )
    return RecordingPhrases(
        trigger_phrase=trigger,
        countdown_display=countdown_display,
        end_phrase_display=end_display,
    )


def _quoted_phrase(phrase: str, color: str) -> str:
    return f'"<span style="color:{color};">{html.escape(phrase)}</span>"'


WARMUP_PHRASES_INTRO = "Once cameras are warmed up, these are your go/stop phrases:"


def _recording_quoted_phrases() -> tuple[str, str, str, str]:
    ensure_scripts_path()
    from podcast_phrase_gates import (
        end_phrases_from_gates,
        load_phrase_gates,
        pause_phrases_from_gates,
        start_trigger_phrase_from_gates,
        unpause_phrases_from_gates,
    )

    gates = load_phrase_gates(create_file_if_missing=False)
    start = start_trigger_phrase_from_gates(gates)
    pause_phrases = pause_phrases_from_gates(gates)
    unpause_phrases = unpause_phrases_from_gates(gates)
    end_phrases = end_phrases_from_gates(gates)
    pause = pause_phrases[0] if pause_phrases else ""
    resume = unpause_phrases[0] if unpause_phrases else ""
    end = end_phrases[0] if end_phrases else ""
    return (
        _quoted_phrase(start, _PHRASE_COLOR_GREEN),
        _quoted_phrase(pause, _PHRASE_COLOR_BLUE),
        _quoted_phrase(resume, _PHRASE_COLOR_BLUE),
        _quoted_phrase(end, _PHRASE_COLOR_GREEN),
    )


def recording_phrase_lines_html() -> str:
    """Start / pause / resume / end phrase lines (no wrapping div)."""
    start_q, pause_q, resume_q, end_q = _recording_quoted_phrases()
    large = f"font-size:{_PHRASE_FONT_SIZE};"
    return (
        f'<span style="{large}">Start Phrase is {start_q}</span><br>'
        "<br>"
        f'<span style="{large}">Pause Phrase is {pause_q}</span><br>'
        f'<span style="{large}">Resume Phrase is {resume_q}</span><br>'
        "<br>"
        f'<span style="{large}">End Phrase is {end_q}</span>'
    )


def recording_warmup_phrases_html() -> str:
    """Phrase cheat-sheet shown on the B4 camera-warmup page."""
    large = f"font-size:{_PHRASE_FONT_SIZE};"
    return (
        '<div style="text-align:center;">'
        f'<span style="{large}">{html.escape(WARMUP_PHRASES_INTRO)}</span><br>'
        "<br>"
        f"{recording_phrase_lines_html()}"
        "</div>"
    )


def recording_controls_html() -> str:
    """Centered rich-text copy for the B4 recording-controls panel."""
    warning = (
        f'<span style="color:{_WARNING_COLOR_RED};">'
        f"{html.escape('THIS WILL STOP RECORDING! DO NOT PUSH UNTIL YOU ARE DONE WITH THE PODCAST!')}"
        "</span>"
    )
    return (
        '<div style="text-align:center;">'
        "Program is running.<br>"
        "<br>"
        f"{recording_phrase_lines_html()}<br>"
        "<br>"
        "<br>"
        f"When you are done, press Stop. {warning}"
        "</div>"
    )


def recording_instructions(phrases: RecordingPhrases | None = None) -> str:
    del phrases
    return recording_controls_html()
