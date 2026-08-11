"""Recording flow helpers for the controller."""

from __future__ import annotations

from dataclasses import dataclass

from app.controller.paths import ensure_scripts_path


@dataclass(frozen=True)
class RecordingPhrases:
    start_phrase: str
    end_phrase_display: str


def get_recording_phrases() -> RecordingPhrases:
    ensure_scripts_path()
    from piab_multicorder_record import build_recording_message, format_end_phrase_display
    from podcast_phrase_gates import end_phrases_from_gates, load_phrase_gates

    gates = load_phrase_gates()
    start = str(gates.get("start_phrase") or "").strip()
    end_phrases = end_phrases_from_gates(gates)
    end_display = format_end_phrase_display(end_phrases)
    if not start:
        raise RuntimeError("Start phrase is not configured in podcast-phrase-gates.json.")
    return RecordingPhrases(start_phrase=start, end_phrase_display=end_display)


def recording_instructions(phrases: RecordingPhrases) -> str:
    ensure_scripts_path()
    from piab_multicorder_record import build_recording_message
    from podcast_phrase_gates import end_phrases_from_gates, load_phrase_gates

    gates = load_phrase_gates()
    return build_recording_message(
        start_phrase=phrases.start_phrase,
        end_phrases=end_phrases_from_gates(gates),
    )
