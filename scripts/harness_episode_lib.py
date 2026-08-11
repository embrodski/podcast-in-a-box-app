"""Shared helpers for inkhaven-episode-harness."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SUBFOLDERS = ("Raw", "Input", "Output", "Temp")
PIAB_STATE_FILENAME = "podcast-in-a-box.json"
RAW_NAME_RE = re.compile(r"raw", re.IGNORECASE)
INKHAVEN_PREFIX_RE = re.compile(r"^inkhaven\s+(.+)$", re.IGNORECASE)
BEN_HOST_RE = re.compile(r"\b(ben|host)\b", re.IGNORECASE)
INTRO_RE = re.compile(r"\bintro\b", re.IGNORECASE)
READING_RE = re.compile(r"\breading\b", re.IGNORECASE)
REPO_ROOT = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_conversation_wavs.py"
ELEVENLABS_KEY_FILENAME = "ElevenLabs 100k Key.txt"
ELEVENLABS_KEY_FILE = REPO_ROOT / ELEVENLABS_KEY_FILENAME
# Fallback PIAB pipeline repo on this machine (see README); also PIAB_UPSTREAM_ROOT env.
DEFAULT_UPSTREAM_REPO_ROOT = Path(r"E:\PodcastRoom\Cursor\automated-video-editing")
CLEAN_RE = re.compile(r"clean", re.IGNORECASE)
WIDE_RE = re.compile(r"\bwide\b", re.IGNORECASE)
FRONT_RE = re.compile(r"\bfront\b", re.IGNORECASE)
SIDE_RE = re.compile(r"\bside\b", re.IGNORECASE)
VID_RE = re.compile(r"\bvid\b|\bvideo\b", re.IGNORECASE)
RAW_WORD_RE = re.compile(r"\braw\b", re.IGNORECASE)
INTERVIEW_RE = re.compile(r"\binterview\b", re.IGNORECASE)
EDITED_RE = re.compile(r"\bedited\b", re.IGNORECASE)
CLOSING_RE = re.compile(r"\bclosing\b", re.IGNORECASE)
# Stitch stdout markers: mm:ss Label (minutes unpadded when timeline >= 100 min).
STITCH_TIMECODE_MARKER_RE = re.compile(r"^\d{1,3}:\d{2}\s+\S+\s*$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def extract_guest_name(episode_folder: Path) -> str:
    match = INKHAVEN_PREFIX_RE.match(episode_folder.name.strip())
    if not match:
        raise ValueError(
            f"Episode folder name must start with 'Inkhaven ' "
            f"(got {episode_folder.name!r})."
        )
    name = match.group(1).strip()
    if not name:
        raise ValueError(
            f"Guest name is empty after 'Inkhaven ' in {episode_folder.name!r}."
        )
    return name


def piab_state_path(episode_folder: Path) -> Path:
    return episode_folder.resolve() / PIAB_STATE_FILENAME


def is_piab_folder(episode_folder: Path) -> bool:
    return piab_state_path(episode_folder).is_file()


def episode_json_path(episode_folder: Path, name: str | None = None) -> Path:
    episode_folder = episode_folder.resolve()
    piab = piab_state_path(episode_folder)
    if piab.is_file():
        return piab
    guest = name or extract_guest_name(episode_folder)
    return episode_folder / f"{guest}-episode.json"


def load_episode_state(episode_folder: Path) -> dict:
    episode_folder = episode_folder.resolve()
    path = episode_json_path(episode_folder)
    if not path.is_file():
        raise FileNotFoundError(
            f"Episode state not found ({path}). Run init_inkhaven_episode.py "
            f"or piab_init_session.py first."
        )
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_episode_state_if_exists(episode_folder: Path) -> dict:
    """Return episode JSON dict, or ``{}`` when the state file has not been created yet."""
    episode_folder = episode_folder.resolve()
    piab = piab_state_path(episode_folder)
    if piab.is_file():
        with piab.open(encoding="utf-8") as fh:
            return json.load(fh)
    try:
        path = episode_json_path(episode_folder)
    except ValueError:
        return {}
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def audit_raw_source_inventory(raw_dir: Path) -> dict[str, object]:
    """
    Warn when Raw does not contain the usual 8- or 13-file harness source set.

    Counts files whose names contain ``raw`` (case-insensitive) with audio/video extensions.
    """
    if not raw_dir.is_dir():
        return {"raw_file_count": 0, "warnings": [f"Raw folder not found: {raw_dir}"]}
    exts = {".wav", ".mp4"}
    names: list[str] = []
    for path in raw_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in exts:
            continue
        if RAW_NAME_RE.search(path.name) is None:
            continue
        names.append(path.name)
    count = len(names)
    warnings: list[str] = []
    if count not in (8, 13):
        warnings.append(
            f"Expected 8 or 13 raw source media files in {raw_dir}, found {count}: "
            f"{sorted(names, key=str.lower)}"
        )
    return {"raw_file_count": count, "raw_file_names": sorted(names, key=str.lower), "warnings": warnings}


def save_episode_state(episode_folder: Path, state: dict) -> Path:
    episode_folder = episode_folder.resolve()
    state["updated_at"] = utc_now_iso()
    if state.get("kind") == "podcast_in_a_box" or is_piab_folder(episode_folder):
        path = piab_state_path(episode_folder)
    else:
        path = episode_json_path(episode_folder, state.get("name"))
    with path.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    return path


def step_state(
    steps: dict,
    step_id: str,
    *,
    title: str,
    status: str,
    **extra: object,
) -> dict:
    prior = steps.get(step_id, {})
    step = {"id": step_id, "title": title, "status": status, **extra}
    if status == "completed":
        step["completed_at"] = utc_now_iso()
    elif status == "skipped":
        step["completed_at"] = utc_now_iso()
        step["skipped"] = True
    elif status == "in_progress" and "started_at" not in step:
        step["started_at"] = prior.get("started_at") or utc_now_iso()
    if "started_at" in prior and "started_at" not in step:
        step["started_at"] = prior["started_at"]
    elif "completed_at" in prior and status not in ("completed", "skipped"):
        step["completed_at"] = prior["completed_at"]
    return step


def raw_wav_candidates(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"Raw folder not found: {raw_dir}")
    out: list[Path] = []
    for path in raw_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() != ".wav":
            continue
        if RAW_NAME_RE.search(path.name) is None:
            continue
        out.append(path)
    return sorted(out, key=lambda p: p.name.lower())


def _filter_scope(files: list[Path], *, intro: bool) -> list[Path]:
    scoped: list[Path] = []
    for path in files:
        name = path.name
        if READING_RE.search(name):
            continue
        is_intro = INTRO_RE.search(name) is not None
        if intro and not is_intro:
            continue
        if not intro and is_intro:
            continue
        scoped.append(path)
    return scoped


def find_conversation_wav_pair(raw_dir: Path, *, intro: bool) -> tuple[Path, Path]:
    """Return (ben/host wav, guest/other wav) for main or intro."""
    scoped = _filter_scope(raw_wav_candidates(raw_dir), intro=intro)
    ben_files = [p for p in scoped if BEN_HOST_RE.search(p.name)]
    guest_files = [p for p in scoped if not BEN_HOST_RE.search(p.name)]

    if len(ben_files) != 1:
        scope = "intro" if intro else "main"
        raise FileNotFoundError(
            f"Expected exactly one {scope} Ben/Host audio raw WAV in {raw_dir}, "
            f"found {len(ben_files)}: {[p.name for p in ben_files]}."
        )
    if len(guest_files) != 1:
        scope = "intro" if intro else "main"
        raise FileNotFoundError(
            f"Expected exactly one {scope} Guest (non-Ben/Host) audio raw WAV "
            f"in {raw_dir}, found {len(guest_files)}: {[p.name for p in guest_files]}."
        )
    return ben_files[0], guest_files[0]


def has_intro_audio_pair(raw_dir: Path) -> bool:
    try:
        find_conversation_wav_pair(raw_dir, intro=True)
        return True
    except FileNotFoundError:
        return False


def combined_audio_output_name(wav1: Path) -> str:
    first_word = wav1.stem.split()[0] if wav1.stem.split() else wav1.stem
    return f"{first_word} Combined Audio.wav"


def elevenlabs_key_file_candidates() -> list[Path]:
    """Key file locations checked in order (deduplicated)."""
    roots: list[Path] = [REPO_ROOT]
    upstream = os.environ.get("PIAB_UPSTREAM_ROOT", "").strip()
    if upstream:
        roots.append(Path(upstream))
    else:
        roots.append(DEFAULT_UPSTREAM_REPO_ROOT)
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        path = root.resolve() / ELEVENLABS_KEY_FILENAME
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def find_elevenlabs_key_file() -> Path | None:
    for path in elevenlabs_key_file_candidates():
        if path.is_file():
            return path
    return None


def read_elevenlabs_api_key() -> str:
    env_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if env_key:
        return env_key
    key_path = find_elevenlabs_key_file()
    if key_path is None:
        searched = ", ".join(str(path) for path in elevenlabs_key_file_candidates())
        raise FileNotFoundError(
            f"Missing ElevenLabs API key. Set ELEVENLABS_API_KEY or create one of: {searched}"
        )
    key = key_path.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError(f"API key file is empty: {key_path}")
    return key


def find_clean_audio_files(
    raw_dir: Path,
    *,
    main_combined: Path | None,
    intro_combined: Path | None,
) -> dict[str, Path]:
    """Step 8: locate user-exported DeRoom WAVs newer than combined outputs."""
    results: dict[str, Path] = {}
    wavs = [p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() == ".wav"]

    def pick(scope: str, reference: Path) -> Path:
        ref_mtime = reference.stat().st_mtime
        candidates: list[Path] = []
        for path in wavs:
            if CLEAN_RE.search(path.name) is None:
                continue
            if path.stat().st_mtime <= ref_mtime:
                continue
            name = path.name
            is_intro = INTRO_RE.search(name) is not None
            is_reading = READING_RE.search(name) is not None
            if scope == "main":
                if is_intro or is_reading:
                    continue
            elif scope == "intro":
                if not is_intro or is_reading:
                    continue
            else:
                raise ValueError(scope)
            candidates.append(path)
        if not candidates:
            raise FileNotFoundError(
                f"No clean audio in {raw_dir} for {scope} "
                f"(newer than {reference.name})."
            )
        return max(candidates, key=lambda p: p.stat().st_mtime)

    if main_combined and main_combined.is_file():
        results["main_clean_audio"] = pick("main", main_combined)
    if intro_combined and intro_combined.is_file():
        results["intro_clean_audio"] = pick("intro", intro_combined)
    return results


def podcast_swap_speaker_ids_cli_args(state: dict) -> list[str]:
    """CLI args for convert_transcript_json.py --swap-speaker-ids."""
    if state.get("swap_speaker_ids"):
        return ["--swap-speaker-ids"]
    return []


def pick_interview_videos(prepped_videos: list[str]) -> tuple[Path, Path, Path]:
    """Return (host/ben, guest, wide) paths from prepped video filenames."""
    paths = [Path(p) for p in prepped_videos]
    ben = next((p for p in paths if BEN_HOST_RE.search(p.name)), None)
    wide = next((p for p in paths if WIDE_RE.search(p.name)), None)
    guest = next(
        (p for p in paths if not BEN_HOST_RE.search(p.name) and not WIDE_RE.search(p.name)),
        None,
    )
    if not ben or not guest or not wide:
        raise FileNotFoundError(f"Could not find Ben/Guest/Wide prepped in {prepped_videos}")
    return ben, guest, wide


def podcast_phrase_cli_args(state: dict) -> list[str]:
    """CLI args for generate_full_dsl.py start/end/pause phrase options."""
    from podcast_phrase_gates import podcast_phrase_cli_args as _shared_phrase_cli_args

    return _shared_phrase_cli_args(state)


def reading_keep_rows_cli_args(state: dict) -> list[str]:
    """CLI args for generate_reading_dsl.py --keep-rows from episode state."""
    rows = state.get("reading_keep_rows")
    if not rows:
        return []
    return ["--keep-rows", ",".join(str(int(r)) for r in rows)]


def reading_spoken_expansions(state: dict) -> dict[str, str]:
    """Episode-only spoken expansions from reading_dsl_notes.normalize_expansions."""
    notes = state.get("reading_dsl_notes")
    if not isinstance(notes, dict):
        return {}
    raw = notes.get("normalize_expansions")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        k = str(key).strip()
        v = str(value).strip()
        if k and v:
            out[k] = v
    return out


def apply_spoken_text_expansions(text: str, expansions: dict[str, str]) -> str:
    """Rewrite article/transcript text using episode-specific spoken expansions.

    Does not touch the shared reading normalizer. ``pp`` uses word boundaries so
    it does not expand inside words like ``app``.
    """
    if not text or not expansions:
        return text
    out = text
    less_than = expansions.get("<")
    if less_than:
        out = out.replace("<", f" {less_than} ")
    percentage_points = expansions.get("pp")
    if percentage_points:
        out = re.sub(
            r"\bpp\b",
            f" {percentage_points} ",
            out,
            flags=re.IGNORECASE,
        )
    # Collapse horizontal whitespace but keep newlines (article line structure).
    out = re.sub(r"[^\S\n]+", " ", out)
    out = "\n".join(line.strip() for line in out.split("\n"))
    return out


def apply_episode_reading_spoken_expansions(
    state: dict,
    *,
    article_txt: Path | None = None,
    simplified_json: Path | None = None,
) -> list[str]:
    """Apply episode-only expansions to reading article and/or simplified transcript.

    Returns the list of paths rewritten (empty if no expansions configured).
    """
    expansions = reading_spoken_expansions(state)
    if not expansions:
        return []

    rewritten: list[str] = []
    if article_txt is not None and article_txt.is_file():
        original = article_txt.read_text(encoding="utf-8")
        updated = apply_spoken_text_expansions(original, expansions)
        if updated != original:
            article_txt.write_text(updated, encoding="utf-8")
            rewritten.append(str(article_txt))

    if simplified_json is not None and simplified_json.is_file():
        data = json.loads(simplified_json.read_text(encoding="utf-8"))
        changed = False
        if isinstance(data, dict):
            for row in data.values():
                if not isinstance(row, dict):
                    continue
                text = row.get("text")
                if isinstance(text, str):
                    new_text = apply_spoken_text_expansions(text, expansions)
                    if new_text != text:
                        row["text"] = new_text
                        changed = True
                words = row.get("words")
                if isinstance(words, list):
                    for word in words:
                        if not isinstance(word, dict):
                            continue
                        wtext = word.get("text")
                        if isinstance(wtext, str):
                            new_wtext = apply_spoken_text_expansions(wtext, expansions)
                            if new_wtext != wtext:
                                word["text"] = new_wtext
                                changed = True
        if changed:
            simplified_json.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            rewritten.append(str(simplified_json))

    return rewritten


def should_skip_reading(state: dict) -> bool:
    return bool(state.get("skip_reading"))


def intro_steps_active(state: dict) -> bool:
    step6 = state.get("steps", {}).get("06_intro_conversation_sync", {})
    return step6.get("status") == "completed"


def run_conversation_sync(wav1: Path, wav2: Path) -> Path:
    if not SYNC_SCRIPT.is_file():
        raise FileNotFoundError(f"Missing sync script: {SYNC_SCRIPT}")
    cmd = [sys.executable, str(SYNC_SCRIPT), str(wav1), str(wav2)]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "conversation-sync failed.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    output = wav1.parent / combined_audio_output_name(wav1)
    if not output.is_file():
        raise FileNotFoundError(
            f"Expected combined output missing after sync: {output}"
        )
    return output
