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


_START_PHRASE = "I solemnly swear I'm up to no good"
_PAUSE_PHRASE = "Computer, Pause Program"
_RESUME_PHRASE = "Computer, Resume Program"
_END_PHRASE = "Be excellent to each other and party on dudes"
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


def recording_controls_html() -> str:
    """Centered rich-text copy for the B4 recording-controls panel."""
    large = f"font-size:{_PHRASE_FONT_SIZE};"
    start = _quoted_phrase(_START_PHRASE, _PHRASE_COLOR_GREEN)
    pause = _quoted_phrase(_PAUSE_PHRASE, _PHRASE_COLOR_BLUE)
    resume = _quoted_phrase(_RESUME_PHRASE, _PHRASE_COLOR_BLUE)
    end = _quoted_phrase(_END_PHRASE, _PHRASE_COLOR_GREEN)
    warning = (
        f'<span style="color:{_WARNING_COLOR_RED};">'
        f"{html.escape('THIS WILL STOP RECORDING! DO NOT PUSH UNTIL YOU ARE DONE WITH THE PODCAST!')}"
        "</span>"
    )
    return (
        '<div style="text-align:center;">'
        "Program is running.<br>"
        "<br>"
        f'<span style="{large}">Start Phrase is {start}</span><br>'
        "<br>"
        f'<span style="{large}">Pause Phrase is {pause}</span><br>'
        f'<span style="{large}">Resume Phrase is {resume}</span><br>'
        "<br>"
        f'<span style="{large}">End Phrase is {end}</span><br>'
        "<br>"
        "<br>"
        f"When you are done, press Continue. {warning}"
        "</div>"
    )


def recording_instructions(phrases: RecordingPhrases | None = None) -> str:
    del phrases
    return recording_controls_html()
