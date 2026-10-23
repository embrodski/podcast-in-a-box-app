#!/usr/bin/env python3
"""
Upload a WAV (or other supported audio) to ElevenLabs speech-to-text via the
official API, then write JSON and a text file next to the input.

Requires ELEVENLABS_API_KEY in the environment (or pass --api-key).

Large uncompressed audio (e.g. multi-GB Host Clean Audio-prepped.wav) is
re-encoded with ffmpeg to a compact mono MP3 before upload, and the multipart
POST streams from disk so the full file is never loaded into RAM.

API reference: https://elevenlabs.io/docs/api-reference/speech-to-text/convert
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from win_hidden_console import merge_hidden_console_kwargs


API_URL = "https://api.elevenlabs.io/v1/speech-to-text"

# Uncompressed / large-container formats benefit from a speech-sized re-encode.
_COMPRESS_SUFFIXES = {".wav", ".wave", ".aiff", ".aif", ".flac"}
# Above this size, re-encode before upload (keeps peak RAM and transfer small).
_COMPRESS_THRESHOLD_BYTES = 32 * 1024 * 1024
_STREAM_CHUNK = 1024 * 1024


def _multipart_preamble_and_epilogue(
    fields: dict[str, str],
    *,
    filename: str,
    content_type: str,
) -> tuple[bytes, bytes, str]:
    boundary = uuid.uuid4().hex
    crlf = b"\r\n"
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}'.encode()
            + crlf
        )

    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    )
    parts.append(header.encode())
    preamble = b"".join(parts)
    epilogue = crlf + f"--{boundary}--\r\n".encode()
    return preamble, epilogue, boundary


class _MultipartStream:
    """file-like multipart body that streams the file payload from disk."""

    def __init__(self, fields: dict[str, str], file_path: Path) -> None:
        fname = file_path.name
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        self._preamble, self._epilogue, self._boundary = _multipart_preamble_and_epilogue(
            fields, filename=fname, content_type=ctype
        )
        self._file_path = file_path
        self._file_size = file_path.stat().st_size
        self._total = len(self._preamble) + self._file_size + len(self._epilogue)
        self._buf = b""
        self._gen: Iterator[bytes] | None = None
        self._closed = False

    @property
    def content_type(self) -> str:
        return f"multipart/form-data; boundary={self._boundary}"

    def __len__(self) -> int:
        return self._total

    def _chunks(self) -> Iterator[bytes]:
        yield self._preamble
        with open(self._file_path, "rb") as fp:
            while True:
                block = fp.read(_STREAM_CHUNK)
                if not block:
                    break
                yield block
        yield self._epilogue

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            return b""
        if self._gen is None:
            self._gen = self._chunks()
        if size == 0:
            return b""

        out = bytearray()
        while size < 0 or len(out) < size:
            if not self._buf:
                try:
                    self._buf = next(self._gen)
                except StopIteration:
                    break
            if size < 0:
                out.extend(self._buf)
                self._buf = b""
            else:
                need = size - len(out)
                out.extend(self._buf[:need])
                self._buf = self._buf[need:]
        return bytes(out)

    def close(self) -> None:
        self._closed = True
        self._gen = None
        self._buf = b""


def _should_compress_for_upload(path: Path) -> bool:
    if path.suffix.lower() not in _COMPRESS_SUFFIXES:
        return False
    try:
        return path.stat().st_size >= _COMPRESS_THRESHOLD_BYTES
    except OSError:
        return False


def _compress_audio_for_upload(src: Path) -> Path:
    """
    Re-encode large WAV/FLAC/AIFF to mono 16 kHz 64 kbps MP3 for STT upload.

    Speech-to-text does not need lossless PCM; this cuts multi-GB files to tens
    of MB so upload RAM and transfer stay practical.
    """
    fd, tmp_name = tempfile.mkstemp(prefix="piab-el-upload-", suffix=".mp3")
    os.close(fd)
    tmp = Path(tmp_name)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(tmp),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        **merge_hidden_console_kwargs(),
    )
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 0:
        tmp.unlink(missing_ok=True)
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"ffmpeg compress-for-upload failed for {src}: {detail}")
    return tmp


def _prepare_upload_file(src: Path) -> tuple[Path, Path | None]:
    """
    Return (path_to_upload, temp_to_delete_or_None).

    Large uncompressed audio is compressed to a temp MP3; callers must delete
    the temp path when finished.
    """
    if not _should_compress_for_upload(src):
        return src, None
    src_mb = src.stat().st_size / (1024 * 1024)
    print(
        f"Compressing {src.name} ({src_mb:.1f} MiB) to mono 16 kHz MP3 for upload…",
        file=sys.stderr,
    )
    compressed = _compress_audio_for_upload(src)
    out_mb = compressed.stat().st_size / (1024 * 1024)
    print(f"Upload payload: {compressed.name} ({out_mb:.1f} MiB)", file=sys.stderr)
    return compressed, compressed


def _request_transcription(
    api_key: str,
    wav: Path,
    *,
    model_id: str,
    diarize: bool,
    timestamps_granularity: str,
    language_code: str | None,
    tag_audio_events: bool,
    additional_formats: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    fields: dict[str, str] = {
        "model_id": model_id,
        "diarize": "true" if diarize else "false",
        "timestamps_granularity": timestamps_granularity,
        "tag_audio_events": "true" if tag_audio_events else "false",
    }
    if language_code:
        fields["language_code"] = language_code
    if additional_formats:
        # API expects a JSON-serializable list of export option objects.
        fields["additional_formats"] = json.dumps(additional_formats)

    upload_path, temp_path = _prepare_upload_file(wav)
    body = _MultipartStream(fields, upload_path)
    req = Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "xi-api-key": api_key,
            "Content-Type": body.content_type,
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ElevenLabs API HTTP {e.code}: {detail}") from e
    finally:
        body.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Non-JSON response from API: {raw[:500]}...") from e


def _unwrap_chunks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "transcripts" in payload:
        return list(payload["transcripts"])
    if "message" in payload and "request_id" in payload:
        raise RuntimeError(
            "API returned a webhook-style response (no transcript in-body). "
            "Ensure webhook=false for synchronous transcription."
        )
    return [payload]


def _get_transcription_id(payload: dict[str, Any]) -> str | None:
    """
    Best-effort extraction of ElevenLabs transcription_id from the response.
    """
    tid = payload.get("transcription_id")
    if isinstance(tid, str) and tid.strip():
        return tid.strip()
    if "transcripts" in payload and isinstance(payload.get("transcripts"), list) and payload["transcripts"]:
        t0 = payload["transcripts"][0]
        if isinstance(t0, dict):
            tid = t0.get("transcription_id")
            if isinstance(tid, str) and tid.strip():
                return tid.strip()
    return None


def _extract_additional_format(
    payload: dict[str, Any], requested_format: str
) -> tuple[bytes, str] | None:
    """
    Return (content_bytes, file_extension) for a requested additional format, if present.
    """
    formats = payload.get("additional_formats") or []
    for f in formats:
        if not isinstance(f, dict):
            continue
        if f.get("requested_format") != requested_format:
            continue
        content = f.get("content")
        if not isinstance(content, str):
            continue
        ext = f.get("file_extension") or ""
        if f.get("is_base64_encoded"):
            # We don't need this path right now; keeping the script minimal.
            raise RuntimeError(
                "API returned base64-encoded additional format; this script version expects plain text content."
            )
        return content.encode("utf-8"), ext
    return None

def _ts_srt(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    whole = int(s)
    ms = int(round((s - whole) * 1000))
    if ms >= 1000:
        ms = 0
        whole += 1
    return f"{h:02d}:{m:02d}:{whole:02d},{ms:03d}"


def _speaker_label(speaker_id: str | None) -> str:
    if not speaker_id:
        return "Speaker"
    if speaker_id.startswith("speaker_"):
        rest = speaker_id.removeprefix("speaker_")
        if rest.isdigit():
            return f"Speaker {int(rest)}"
    return speaker_id.replace("_", " ").title()


def _plain_text_from_payload(payload: dict[str, Any]) -> str:
    chunks = _unwrap_chunks(payload)
    texts = [c.get("text", "").strip() for c in chunks]
    return "\n\n".join(t for t in texts if t)


def _srt_from_words(words: list[dict[str, Any]]) -> str:
    """Build SRT-style blocks (timestamp line + body) from diarized word list."""
    filled: list[tuple[dict[str, Any], str | None]] = []
    last_sp: str | None = None
    for w in words:
        sid = w.get("speaker_id")
        if sid is not None:
            last_sp = sid
        filled.append((w, last_sp))

    blocks: list[tuple[float, float, str | None, str]] = []
    i = 0
    while i < len(filled):
        w0, sp0 = filled[i]
        if sp0 is None:
            i += 1
            continue
        j = i
        parts: list[str] = []
        t0: float | None = None
        t1: float | None = None
        while j < len(filled) and filled[j][1] == sp0:
            w, _ = filled[j]
            st = w.get("start", w.get("start_time"))
            en = w.get("end", w.get("end_time"))
            if st is not None and t0 is None:
                t0 = float(st)
            if en is not None:
                t1 = float(en)
            parts.append(w.get("text", ""))
            j += 1
        text = "".join(parts).strip()
        if text and t0 is not None and t1 is not None:
            blocks.append((t0, t1, sp0, text))
        i = j

    lines: list[str] = []
    for start, end, sp, text in blocks:
        lines.append(
            f"{_ts_srt(start)} --> {_ts_srt(end)} [{_speaker_label(sp)}]\n{text}\n"
        )
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def _text_output(payload: dict[str, Any], mode: str) -> str:
    chunks = _unwrap_chunks(payload)

    def _with_nl(s: str) -> str:
        return s if s.endswith("\n") else s + "\n"

    if mode == "plain":
        return _with_nl(_plain_text_from_payload(payload))

    if len(chunks) > 1:
        print(
            "Warning: multichannel transcript; SRT-style text uses the first channel only.",
            file=sys.stderr,
        )
    words = chunks[0].get("words") or []
    if not words:
        return _with_nl(_plain_text_from_payload(payload))
    srt = _srt_from_words(words)
    if not srt.strip():
        return _with_nl(_plain_text_from_payload(payload))
    return _with_nl(srt)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("wav", type=Path, help="Path to .wav (or other audio) file")
    p.add_argument(
        "--api-key",
        default=os.environ.get("ELEVENLABS_API_KEY", ""),
        help="Defaults to ELEVENLABS_API_KEY env var",
    )
    p.add_argument("--model-id", default="scribe_v2")
    p.add_argument("--no-diarize", action="store_true", help="Disable speaker diarization")
    p.add_argument(
        "--text-format",
        choices=("plain", "srt"),
        default="srt",
        help="plain: API transcript text; srt: timestamp + [Speaker N] blocks from words",
    )
    p.add_argument(
        "--json-format",
        choices=("raw", "segmented_json"),
        default="segmented_json",
        help="raw: save full API response; segmented_json: save ElevenLabs segmented export JSON",
    )
    p.add_argument(
        "--save-raw-response",
        action="store_true",
        help="Also save the full raw API response JSON next to the transcript JSON",
    )
    p.add_argument(
        "--language-code",
        default="en",
        help="ISO-639 language hint (default: en)",
    )
    p.add_argument(
        "--tag-audio-events",
        action="store_true",
        help="Include audio events like (laughter) in transcript",
    )
    p.add_argument(
        "--timestamps-granularity",
        default="word",
        choices=("none", "word", "character"),
    )
    args = p.parse_args()

    wav = args.wav.expanduser().resolve()
    if not wav.is_file():
        print(f"Not a file: {wav}", file=sys.stderr)
        sys.exit(1)
    if not args.api_key.strip():
        print(
            "Missing API key: set ELEVENLABS_API_KEY or pass --api-key",
            file=sys.stderr,
        )
        sys.exit(1)

    additional_formats: list[dict[str, Any]] | None = None
    if args.json_format == "segmented_json":
        additional_formats = [
            {
                "format": "segmented_json",
                "include_speakers": True,
                "include_timestamps": True,
            }
        ]

    payload = _request_transcription(
        args.api_key.strip(),
        wav,
        model_id=args.model_id,
        diarize=not args.no_diarize,
        timestamps_granularity=args.timestamps_granularity,
        language_code=args.language_code,
        tag_audio_events=bool(args.tag_audio_events),
        additional_formats=additional_formats,
    )

    out_dir = wav.parent
    stem = wav.stem
    json_path = out_dir / f"{stem} Transcript.json"
    txt_path = out_dir / f"{stem} Text.txt"
    tid_path = out_dir / f"{stem} Transcription ID.txt"

    if args.json_format == "segmented_json":
        extracted = _extract_additional_format(payload, "segmented_json")
        if extracted is None:
            # Fall back to raw response if export wasn't returned.
            json_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        else:
            content_bytes, _ext = extracted
            # The segmented JSON export is itself JSON.
            segmented_obj = json.loads(content_bytes.decode("utf-8"))
            json_path.write_text(
                json.dumps(segmented_obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
    else:
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.save_raw_response:
        raw_path = out_dir / f"{stem} Raw API Response.json"
        raw_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    text_body = _text_output(payload, args.text_format)
    txt_path.write_text(text_body, encoding="utf-8")

    transcription_id = _get_transcription_id(payload)
    if transcription_id:
        tid_path.write_text(transcription_id + "\n", encoding="utf-8")
        print(f"transcription_id: {transcription_id}")
        print(f"Wrote {tid_path}")
    else:
        print("Warning: transcription_id not found in response", file=sys.stderr)

    print(f"Wrote {json_path}")
    if args.save_raw_response:
        print(f"Wrote {raw_path}")
    print(f"Wrote {txt_path}")


if __name__ == "__main__":
    main()
