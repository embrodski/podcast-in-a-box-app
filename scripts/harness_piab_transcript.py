"""Human-readable PIAB transcript from ElevenLabs SRT-style Text.txt."""

from __future__ import annotations

from pathlib import Path

from clean_human_transcript import clean_transcript_text

FULL_INTERVIEW_TRANSCRIPT_TXT = "Full Interview Transcript.txt"
DEFAULT_HOST_LABEL = "Host"


def detail_json_path(state: dict) -> Path | None:
    raw = state.get("main_transcript_json")
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.is_file() else None


def elevenlabs_text_path(detail_json: Path) -> Path:
    """Sibling ``Text.txt`` written by ``elevenlabs_transcribe_wav.py``."""
    stem = detail_json.stem
    if stem.endswith(" Transcript"):
        stem = stem[: -len(" Transcript")]
    return detail_json.parent / f"{stem} Text.txt"


def resolve_speaker_labels(state: dict) -> tuple[str, str]:
    """Return (label for speaker_0, label for speaker_1) honoring swap_speaker_ids."""
    guest = str(state.get("name") or state.get("guest_name") or "").strip()
    if not guest:
        raise ValueError("Guest name missing from session state.")
    host = str(state.get("host_name") or DEFAULT_HOST_LABEL).strip() or DEFAULT_HOST_LABEL
    if state.get("swap_speaker_ids"):
        return guest, host
    return host, guest


def build_human_transcript_text(state: dict) -> str:
    detail = detail_json_path(state)
    if detail is None:
        raise FileNotFoundError(
            "Detail transcript JSON missing; run prep transcribe step first."
        )
    text_path = elevenlabs_text_path(detail)
    if not text_path.is_file():
        raise FileNotFoundError(
            f"ElevenLabs text transcript missing: {text_path}. "
            "Re-run transcribe or check Input/ for * Text.txt."
        )
    speaker_0, speaker_1 = resolve_speaker_labels(state)
    return clean_transcript_text(
        text_path.read_text(encoding="utf-8"),
        host=speaker_0,
        guest=speaker_1,
    )


def write_human_transcript_to_output(state: dict, output_dir: Path) -> Path:
    """Write ``Output/Full Interview Transcript.txt``; return path."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / FULL_INTERVIEW_TRANSCRIPT_TXT
    dest.write_text(build_human_transcript_text(state), encoding="utf-8")
    return dest
