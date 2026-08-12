"""Recording flow helpers for the controller."""

from __future__ import annotations

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


def recording_instructions(phrases: RecordingPhrases) -> str:
    ensure_scripts_path()
    from piab_multicorder_record import build_recording_message
    from podcast_phrase_gates import (
        end_phrases_from_gates,
        load_phrase_gates,
        start_countdown_allow_in_from_gates,
        start_countdown_tokens_from_gates,
    )

    gates = load_phrase_gates()
    return build_recording_message(
        trigger_phrase=phrases.trigger_phrase,
        end_phrases=end_phrases_from_gates(gates),
        countdown_tokens=start_countdown_tokens_from_gates(gates) or None,
        allow_in=start_countdown_allow_in_from_gates(gates),
    )
