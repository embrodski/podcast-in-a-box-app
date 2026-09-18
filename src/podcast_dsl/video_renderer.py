"""
Video rendering and FFmpeg operations.
"""

import os
import sys
import subprocess
import tempfile
import hashlib
import shutil
import sqlite3
import json
from typing import List, Dict, Tuple, Optional
from multiprocessing import Pool, cpu_count
from functools import lru_cache
from .config import (
    SHORTEN_JOIN_DEFAULT_CROSSFADE_MS,
    SHORTEN_JOIN_DEFAULT_PADDING_MS,
    get_segment_config,
    is_reading_dsl_text,
    segment_uses_embedded_audio,
)
from .clip_processing import get_clip_info, parse_segment_id, load_transcript
from .color_match import build_color_match_vf
from .zero_cross_snap import snap_boundary_group_time, snap_enabled

try:
    from win_hidden_console import install_hidden_console
except ImportError:
    _scripts = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
    _scripts = os.path.normpath(_scripts)
    if _scripts not in sys.path:
        sys.path.insert(0, _scripts)
    from win_hidden_console import install_hidden_console

install_hidden_console()


# Cache directory for intermediate results
def _default_cache_dir() -> str:
    override = os.environ.get('PODCAST_DSL_CACHE_DIR', '').strip()
    if override:
        return override
    return os.path.expanduser('~/.cache/podcast_dsl')


CACHE_DIR = _default_cache_dir()
CACHE_DB = os.path.join(CACHE_DIR, 'cache.db')
OUTPUT_FPS = 24000 / 1001
OUTPUT_FPS_STR = '24000/1001'
VIDEO_ENCODER_ENV = 'PODCAST_DSL_VIDEO_ENCODER'
VIDEO_PRESET_ENV = 'PODCAST_DSL_VIDEO_PRESET'
VIDEO_HARDWARE_ENCODE_ENV = 'PODCAST_DSL_HARDWARE_ENCODE'
VIDEO_DOWNSCALE_4K_ENV = 'PODCAST_DSL_DOWNSCALE_4K_TO_1080P'
DEFAULT_VIDEO_ENCODER = 'libx264'
AUTO_HARDWARE_ENCODERS = ('h264_nvenc', 'h264_qsv', 'h264_amf')
# (stage_default_preset, encoder_env, hardware_encode_flag, preset_env_raw) -> resolved name
_resolved_encoder_memo: Dict[Tuple[str, str, bool, str], str] = {}
ENCODER_TEST_WIDTH = 1280
ENCODER_TEST_HEIGHT = 720

# Set during render_dsl / render_all_cams so reading DSLs default to embedded MP4 audio.
_active_dsl_text: Optional[str] = None


def _set_render_dsl_context(dsl_file: Optional[str]) -> None:
    global _active_dsl_text
    if dsl_file and dsl_file != "-":
        try:
            with open(dsl_file, encoding="utf-8") as f:
                _active_dsl_text = f.read()
        except OSError:
            _active_dsl_text = None
    else:
        _active_dsl_text = None


def _use_video_embedded_audio(config: dict) -> bool:
    return segment_uses_embedded_audio(config, dsl_text=_active_dsl_text)


def _final_concat_use_reencode() -> bool:
    """Reading edits use embedded per-clip audio; re-encode final concat for NLE timeline sync."""
    return bool(_active_dsl_text and is_reading_dsl_text(_active_dsl_text))


def _append_fast_input_seek(cmd: List[str], path: str, start_sec: float) -> None:
    """Seek before -i (fast, less accurate A/V alignment at cut points)."""
    cmd.extend(['-ss', str(start_sec), '-i', path])


def _append_accurate_muxed_input_seek(
    cmd: List[str], path: str, start_sec: float, duration_sec: float,
) -> None:
    """Seek after -i so embedded audio and video stay aligned (slower, NLE-safe)."""
    cmd.extend(['-i', path, '-ss', str(start_sec), '-t', str(duration_sec)])


DOWNSCALE_WIDTH_1080P = 1920
DOWNSCALE_HEIGHT_1080P = 1080


def _ffmpeg_cmd_base() -> List[str]:
    """Build a low-noise FFmpeg command prefix."""
    return ['ffmpeg', '-hide_banner', '-nostats', '-loglevel', 'error', '-y']


def _sanity_sidecar_path(dsl_file: str) -> Optional[str]:
    if not dsl_file or dsl_file == "-":
        return None
    return f"{dsl_file}.sanity.json"


def _run_dsl_sanity_check(dsl_file: str) -> None:
    sanity_path = _sanity_sidecar_path(dsl_file)
    if not sanity_path or not os.path.exists(sanity_path):
        return

    with open(sanity_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    warnings = data.get("warnings", [])
    issues = data.get("blocking_issues", [])
    notes = data.get("selection_notes", [])

    if notes:
        print("Reading sanity notes:")
        for note in notes:
            print(f"  - {note}")
        print()

    if warnings:
        print("Reading sanity warnings:")
        for warning in warnings[:10]:
            print(f"  - [{warning.get('idx')}] {warning.get('text', '')}")
        if len(warnings) > 10:
            print(f"  ... and {len(warnings) - 10} more")
        print()

    if issues:
        details = "\n".join(
            f"  - [{issue.get('idx')}] {issue.get('text', '')}"
            for issue in issues[:10]
        )
        more = ""
        if len(issues) > 10:
            more = f"\n  ... and {len(issues) - 10} more"
        raise ValueError(
            "Reading sanity check failed: obvious dropped article lines were detected "
            f"before render.\nDSL: {dsl_file}\nSanity report: {sanity_path}\n{details}{more}"
        )


def _map_encoder_preset(video_encoder: str, preset: str) -> str:
    preset = (preset or 'fast').lower()
    if video_encoder == 'libx264':
        return preset
    if video_encoder == 'h264_nvenc':
        return {
            'ultrafast': 'p1',
            'superfast': 'p2',
            'veryfast': 'p2',
            'faster': 'p3',
            'fast': 'fast',
            'medium': 'medium',
            'slow': 'slow',
            'slower': 'p6',
            'veryslow': 'p7',
        }.get(preset, preset)
    if video_encoder == 'h264_qsv':
        return {
            'ultrafast': 'veryfast',
            'superfast': 'veryfast',
            'veryfast': 'veryfast',
            'faster': 'faster',
            'fast': 'fast',
            'medium': 'medium',
            'slow': 'slow',
            'slower': 'slower',
            'veryslow': 'veryslow',
        }.get(preset, preset)
    if video_encoder == 'h264_amf':
        return {
            'ultrafast': 'speed',
            'superfast': 'speed',
            'veryfast': 'speed',
            'faster': 'speed',
            'fast': 'speed',
            'medium': 'balanced',
            'slow': 'quality',
            'slower': 'quality',
            'veryslow': 'quality',
        }.get(preset, preset)
    raise ValueError(f"Unsupported video encoder: {video_encoder}")


def _append_video_encoder_args(cmd: List[str], video_encoder: str, preset: str, quality_level: int) -> None:
    mapped_preset = _map_encoder_preset(video_encoder, preset)
    if video_encoder == 'libx264':
        cmd.extend([
            '-c:v', 'libx264',
            '-preset', mapped_preset,
            '-crf', str(quality_level),
        ])
        return
    if video_encoder == 'h264_nvenc':
        cmd.extend([
            '-c:v', 'h264_nvenc',
            '-preset', mapped_preset,
            '-tune', 'hq',
            '-rc', 'vbr',
            '-cq', str(quality_level),
            '-b:v', '0',
        ])
        return
    if video_encoder == 'h264_qsv':
        cmd.extend([
            '-c:v', 'h264_qsv',
            '-preset', mapped_preset,
            '-global_quality', str(quality_level),
        ])
        return
    if video_encoder == 'h264_amf':
        cmd.extend([
            '-c:v', 'h264_amf',
            '-preset', mapped_preset,
            '-rc', 'qvbr',
            '-qvbr_quality_level', str(quality_level),
        ])
        return
    raise ValueError(f"Unsupported video encoder: {video_encoder}")


def _encoder_test_command(output_path: str, video_encoder: str, preset: str) -> List[str]:
    cmd = _ffmpeg_cmd_base() + [
        '-f', 'lavfi',
        '-i', f'color=c=black:s={ENCODER_TEST_WIDTH}x{ENCODER_TEST_HEIGHT}:r=24000/1001:d=0.1',
        '-frames:v', '1',
    ]
    _append_video_encoder_args(cmd, video_encoder, preset, quality_level=23)
    cmd.extend([
        '-pix_fmt', 'yuv420p',
        '-profile:v', 'high',
        '-an',
        output_path,
    ])
    return cmd


@lru_cache(maxsize=None)
def _encoder_is_usable(video_encoder: str, preset: str) -> bool:
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
            temp_path = tmp.name
        os.unlink(temp_path)
        cmd = _encoder_test_command(temp_path, video_encoder, preset)
        result = subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return result.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _pick_auto_hardware_encoder(stage_default_preset: str) -> str:
    requested_preset = os.environ.get(VIDEO_PRESET_ENV, '').strip() or stage_default_preset
    for candidate in AUTO_HARDWARE_ENCODERS:
        if _encoder_is_usable(candidate, requested_preset):
            return candidate
    return 'libx264'


def _resolved_video_encoder(stage_default_preset: str) -> str:
    """
    Resolve which H.264 encoder FFmpeg should use for a pipeline stage.

    Default is libx264. Auto hardware probing runs only when the
    ``PODCAST_DSL_HARDWARE_ENCODE`` environment variable is set (CLI: ``--hardware-encode``)
    or when the user passes ``--video-encoder auto`` (legacy alias for that path).
    Explicit h264_nvenc / h264_qsv / h264_amf are returned as-is (with runtime libx264
    fallback in _run_ffmpeg_h264_encode_with_fallback when those fail mid-render).
    """
    raw = (os.environ.get(VIDEO_ENCODER_ENV) or DEFAULT_VIDEO_ENCODER).strip()
    enc_env = raw.lower()
    hw_flag = os.environ.get(VIDEO_HARDWARE_ENCODE_ENV, '').strip().lower() in (
        '1',
        'true',
        'yes',
    )
    preset_raw = os.environ.get(VIDEO_PRESET_ENV, '').strip()
    key = (stage_default_preset, enc_env, hw_flag, preset_raw)
    if key in _resolved_encoder_memo:
        return _resolved_encoder_memo[key]

    if enc_env in ('h264_nvenc', 'h264_qsv', 'h264_amf'):
        out = enc_env
    elif enc_env == 'auto':
        out = _pick_auto_hardware_encoder(stage_default_preset)
    elif hw_flag:
        out = _pick_auto_hardware_encoder(stage_default_preset)
    else:
        out = 'libx264'

    _resolved_encoder_memo[key] = out
    return out


def _is_hardware_video_encoder(enc: str) -> bool:
    return enc in AUTO_HARDWARE_ENCODERS


def _encoder_attempt_chain(primary_encoder: str) -> List[str]:
    p = (primary_encoder or 'libx264').strip().lower()
    if _is_hardware_video_encoder(p):
        return [p, 'libx264']
    return [p]


def _run_ffmpeg_h264_encode_with_fallback(
    cmd_prefix: List[str],
    output_path: str,
    *,
    stage_default_preset: str,
    quality_level: int,
    use_cache: bool,
    err_context: str,
    cache_extension: str = '.mp4',
) -> None:
    """
    Append H.264 encoder args and output_path, run FFmpeg; if a hardware encoder fails,
    retry once with libx264 (separate cache key when caching is enabled).
    """
    preset = _requested_video_preset(stage_default_preset)
    primary = _resolved_video_encoder(stage_default_preset)
    last_stderr = ''

    for attempt_idx, enc in enumerate(_encoder_attempt_chain(primary)):
        cmd = list(cmd_prefix)
        _append_video_encoder_args(cmd, enc, preset, quality_level)
        cmd.append(output_path)

        if attempt_idx > 0:
            print(
                f'Note: falling back to libx264 after hardware encoder ({primary}) failed '
                f'({err_context}).',
                file=sys.stderr,
            )

        cache_cmd = cmd[:-1] if use_cache else None
        if use_cache and cache_cmd:
            cached_file = _get_cached_file(cache_cmd, extension=cache_extension)
            if cached_file:
                shutil.copy2(cached_file, output_path)
                return

        result = subprocess.run(
            cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        ok = (
            result.returncode == 0
            and os.path.exists(output_path)
            and os.path.getsize(output_path) > 0
        )
        if ok:
            if use_cache and cache_cmd:
                _cache_file(cache_cmd, output_path, extension=cache_extension)
            return

        last_stderr = result.stderr or ''

    print(f'\nError: FFmpeg failed ({err_context}): {output_path}', file=sys.stderr)
    if last_stderr.strip():
        print(last_stderr, file=sys.stderr)
    raise RuntimeError(f'FFmpeg failed ({err_context}): {output_path}')


def _requested_video_preset(stage_default_preset: str) -> str:
    return os.environ.get(VIDEO_PRESET_ENV, '').strip() or stage_default_preset


@lru_cache(maxsize=None)
def _get_video_dimensions(video_file: str) -> Tuple[int, int]:
    """Return the width and height of a video file."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'json',
        video_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    streams = info.get('streams', [])
    if not streams:
        raise RuntimeError(f"No video stream found in {video_file}")
    stream = streams[0]
    return int(stream['width']), int(stream['height'])


def _summarize_stderr(stderr_text: str, max_lines: int = 20) -> str:
    lines = [line.rstrip() for line in (stderr_text or "").splitlines() if line.strip()]
    if not lines:
        return "(no stderr output)"
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines] + ["...", f"[truncated {len(lines) - max_lines} more lines]"])


def _validate_video_stream(video_file: str) -> None:
    if not os.path.exists(video_file):
        raise RuntimeError(f"Expected output file was not created: {video_file}")
    if os.path.getsize(video_file) <= 0:
        raise RuntimeError(f"Output file is empty: {video_file}")

    probe_cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_name,pix_fmt,width,height',
        '-of', 'json',
        video_file,
    ]
    probe_result = subprocess.run(
        probe_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe_result.returncode != 0:
        raise RuntimeError(
            f"ffprobe could not inspect video stream for {video_file}:\n"
            f"{_summarize_stderr(probe_result.stderr)}"
        )

    try:
        probe_info = json.loads(probe_result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {video_file}: {exc}") from exc

    streams = probe_info.get('streams', [])
    if not streams:
        raise RuntimeError(f"No video stream found in {video_file}")

    stream = streams[0]
    pix_fmt = str(stream.get('pix_fmt') or '').strip().lower()
    if not pix_fmt or pix_fmt == 'unknown':
        raise RuntimeError(
            f"Video stream in {video_file} has an invalid pixel format: {stream.get('pix_fmt')!r}"
        )

    decode_cmd = [
        'ffmpeg', '-v', 'error',
        '-i', video_file,
        '-map', '0:v:0',
        '-frames:v', '1',
        '-f', 'null',
        '-',
    ]
    decode_result = subprocess.run(
        decode_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if decode_result.returncode != 0:
        raise RuntimeError(
            f"Video stream decode validation failed for {video_file}:\n"
            f"{_summarize_stderr(decode_result.stderr)}"
        )


@lru_cache(maxsize=None)
def _get_segment_target_resolution(segment_num: str) -> Tuple[int, int]:
    """
    Pick a common output resolution for all cameras in a segment.
    Uses the largest width/height so lower-resolution cameras get normalized
    before concatenation with higher-resolution cameras.
    """
    config = get_segment_config(segment_num)
    dimensions = [
        _get_video_dimensions(camera_info['file'])
        for camera_info in config['video_files'].values()
    ]
    target_width = max(width for width, _ in dimensions)
    target_height = max(height for _, height in dimensions)
    if os.environ.get(VIDEO_DOWNSCALE_4K_ENV, '').strip().lower() in {'1', 'true', 'yes', 'on'}:
        if target_width > DOWNSCALE_WIDTH_1080P or target_height > DOWNSCALE_HEIGHT_1080P:
            return DOWNSCALE_WIDTH_1080P, DOWNSCALE_HEIGHT_1080P
    return target_width, target_height


@lru_cache(maxsize=None)
def _segment_color_match_eq(segment_num: str, camera: str) -> str:
    """
    Return an ffmpeg filter chain snippet (no labels) to color-match a camera to `wide`.

    Wide is the reference (identity). Close cameras get a mild gamma correction derived
    from sampled mean luma differences.
    """
    if camera == 'wide':
        return ''

    config = get_segment_config(segment_num)
    if not config.get('enable_color_match', True):
        return ''
    cam_cfg = config['video_files'].get(camera, {})

    vf = cam_cfg.get('color_match_vf')
    if isinstance(vf, str) and vf.strip():
        return vf.strip().rstrip(',')

    cams = config.get('video_files', {})
    if 'wide' not in cams:
        return ''

    # Only auto-match the two common close angles; leave other layouts untouched.
    if camera not in ('speaker_0', 'speaker_1'):
        return ''

    return build_color_match_vf(cams['wide']['file'], cams[camera]['file'])


def _append_vf_snippet(video_filter_chain: List[str], snippet: str) -> None:
    if not snippet:
        return
    if video_filter_chain:
        video_filter_chain[-1] = f"{video_filter_chain[-1]},{snippet}"
    else:
        video_filter_chain.append(snippet)


def _append_video_eq(video_filter_chain: List[str], eq_snippet: str) -> None:
    _append_vf_snippet(video_filter_chain, eq_snippet)


def _init_cache_db():
    """Initialize the cache database."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS cache_commands (
            hash TEXT PRIMARY KEY,
            command TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


def _store_command_in_db(cmd_hash: str, cmd: List[str]):
    """Store a command in the database for debugging."""
    _init_cache_db()
    conn = sqlite3.connect(CACHE_DB)
    c = conn.cursor()
    cmd_str = ' '.join(cmd)
    c.execute('INSERT OR REPLACE INTO cache_commands (hash, command) VALUES (?, ?)',
              (cmd_hash, cmd_str))
    conn.commit()
    conn.close()


def _get_command_from_db(cmd_hash: str) -> Optional[str]:
    """Retrieve a command from the database by hash."""
    if not os.path.exists(CACHE_DB):
        return None
    conn = sqlite3.connect(CACHE_DB)
    c = conn.cursor()
    c.execute('SELECT command FROM cache_commands WHERE hash = ?', (cmd_hash,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None


def _get_command_hash(cmd: List[str]) -> str:
    """
    Compute a hash of the FFmpeg command for caching purposes.

    Args:
        cmd: FFmpeg command as list of strings

    Returns:
        SHA256 hash of the command
    """
    # Create a normalized version of the command for hashing
    # Include the command and all arguments
    cmd_str = ' '.join(cmd)
    return hashlib.sha256(cmd_str.encode('utf-8')).hexdigest()


def _get_cached_file(cmd: List[str], extension: str = '.mp4') -> Optional[str]:
    """
    Check if a cached result exists for this command.

    Args:
        cmd: FFmpeg command to check
        extension: File extension for the cached file

    Returns:
        Path to cached file if it exists, None otherwise
    """
    cmd_hash = _get_command_hash(cmd)
    cache_file = os.path.join(CACHE_DIR, f"{cmd_hash}{extension}")

    if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
        return cache_file

    return None


def _cache_file(cmd: List[str], source_file: str, extension: str = '.mp4') -> str:
    """
    Store a file in the cache.

    Args:
        cmd: FFmpeg command that generated this file
        source_file: Path to the file to cache
        extension: File extension

    Returns:
        Path to the cached file
    """
    # Ensure cache directory exists
    os.makedirs(CACHE_DIR, exist_ok=True)

    cmd_hash = _get_command_hash(cmd)
    cache_file = os.path.join(CACHE_DIR, f"{cmd_hash}{extension}")

    # Copy file to cache
    shutil.copy2(source_file, cache_file)

    # Store command in database for debugging
    _store_command_in_db(cmd_hash, cmd)

    return cache_file


def concatenate_clips(
    clip_files: List[str],
    output_file: str,
    use_reencode: bool = False,
    *,
    muxed_reencode: bool = False,
):
    """
    Concatenate video clips with optional re-encoding to fix timestamp issues.

    Args:
        clip_files: List of video file paths to concatenate
        output_file: Output file path
        use_reencode: If True, use concat *filter* with separate video/audio chains (legacy).
        muxed_reencode: Decode each whole clip via the concat demuxer, then re-encode once.
            Keeps each segment's A/V aligned (required for reading finals and embedded-audio groups).
    """
    if len(clip_files) == 1:
        # Single file, just copy it
        import shutil
        shutil.copy2(clip_files[0], output_file)
        return

    if muxed_reencode:
        _concatenate_clips_demuxer_reencode(clip_files, output_file)
        return

    # Legacy: separate video/audio concat filters (do not use for long multi-segment reading finals).
    if use_reencode:
        _concatenate_clips_reencode(clip_files, output_file)
        return

    _concatenate_clips_demuxer(clip_files, output_file)
    return


def _filter_valid_concat_clips(clip_files: List[str]) -> List[str]:
    """Drop zero-length intermediates before concat demuxer runs."""
    import json

    valid_clips: List[str] = []
    for i, clip_file in enumerate(clip_files):
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=duration,nb_frames',
            '-of', 'json',
            clip_file,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        try:
            info = json.loads(result.stdout)
            streams = info.get('streams', [])

            if not streams:
                print(f"Warning: Skipping segment {i} - no video stream: {clip_file}", file=sys.stderr)
                continue

            stream = streams[0]
            nb_frames = int(stream.get('nb_frames', 0))
            duration = float(stream.get('duration', 0))

            if nb_frames == 0 or duration <= 0.001:
                print(
                    f"Warning: Skipping segment {i} - zero/minimal duration "
                    f"(frames={nb_frames}, duration={duration}s): {clip_file}",
                    file=sys.stderr,
                )
                continue

            valid_clips.append(clip_file)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"Warning: Could not validate segment {i}, including anyway: {e}", file=sys.stderr)
            valid_clips.append(clip_file)

    if not valid_clips:
        raise RuntimeError("No valid clips to concatenate")

    if len(valid_clips) != len(clip_files):
        print(
            f"Concatenating {len(valid_clips)} valid clips "
            f"(filtered out {len(clip_files) - len(valid_clips)} invalid clips)"
        )

    return valid_clips


def _write_concat_demuxer_list(concat_list_path: str, clip_files: List[str]) -> None:
    with open(concat_list_path, 'w', encoding='utf-8') as concat_file:
        for clip_file in clip_files:
            escaped_path = clip_file.replace('\\', '\\\\').replace("'", "'\\''")
            concat_file.write(f"file '{escaped_path}'\n")


def _concatenate_clips_reencode(clip_files: List[str], output_file: str):
    """
    Concatenate clips using concat filter with re-encoding.
    This fixes timestamp issues from re-encoded segments without overlapping audio.
    """
    # Build FFmpeg filter_complex for concatenation
    # Video: concat filter
    video_inputs = ''.join(f'[{i}:v]' for i in range(len(clip_files)))
    video_filter = f'{video_inputs}concat=n={len(clip_files)}:v=1:a=0[v]'

    # Audio: concat filter (no crossfading - just straight concatenation)
    audio_inputs = ''.join(f'[{i}:a]' for i in range(len(clip_files)))
    audio_filter = f'{audio_inputs}concat=n={len(clip_files)}:v=0:a=1[a]'

    filter_complex = video_filter + ';' + audio_filter

    # Build FFmpeg command
    cmd = _ffmpeg_cmd_base()

    # Add all input files
    for clip_file in clip_files:
        cmd.extend(['-i', clip_file])

    # Add filter_complex
    cmd.extend([
        '-filter_complex', filter_complex,
        '-map', '[v]',
        '-map', '[a]',
        '-r', '24000/1001',
        '-pix_fmt', 'yuv420p',
        '-profile:v', 'high',
        '-c:a', 'aac', '-b:a', '320k',
        '-compression_level', '5',  # Good balance of speed and compression
    ])
    _run_ffmpeg_h264_encode_with_fallback(
        cmd,
        output_file,
        stage_default_preset='ultrafast',
        quality_level=23,
        use_cache=False,
        err_context='concat reencode',
    )


def _concatenate_clips_demuxer_reencode(clip_files: List[str], output_file: str):
    """
    Concat demuxer + single re-encode pass. Each input file stays muxed (A/V together),
    which avoids cumulative drift from concatenating separate video and audio chains.
    """
    valid_clips = _filter_valid_concat_clips(clip_files)
    concat_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    try:
        _write_concat_demuxer_list(concat_file.name, valid_clips)
        concat_file.close()

        cmd = _ffmpeg_cmd_base() + [
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file.name,
            '-vf', f'fps={OUTPUT_FPS_STR}',
            '-af', 'aresample=async=1:first_pts=0',
            '-fps_mode', 'cfr',
            '-pix_fmt', 'yuv420p',
            '-profile:v', 'high',
            '-avoid_negative_ts', 'make_zero',
            '-c:a', 'aac', '-b:a', '320k',
            '-compression_level', '5',
        ]
        _run_ffmpeg_h264_encode_with_fallback(
            cmd,
            output_file,
            stage_default_preset='ultrafast',
            quality_level=23,
            use_cache=False,
            err_context='concat demuxer reencode',
        )
    finally:
        if os.path.exists(concat_file.name):
            os.unlink(concat_file.name)


def _shorten_join_from_clip(
    clip: Tuple,
) -> Optional[Tuple[float, float]]:
    """Return (padding_ms, crossfade_ms) when clip[10] marks a shorten join."""
    if len(clip) < 11:
        return None
    spec = clip[10]
    if not spec:
        return None
    if isinstance(spec, tuple):
        return spec
    return (SHORTEN_JOIN_DEFAULT_PADDING_MS, SHORTEN_JOIN_DEFAULT_CROSSFADE_MS)


def _build_muxed_shorten_audio_filter(
    num_inputs: int,
    join_specs: List[Optional[Tuple[float, float]]],
) -> Tuple[str, str]:
    """Chain acrossfade (shorten joins) and concat (other joins); return filter + audio label."""
    if num_inputs < 2:
        raise ValueError("need at least two inputs for audio join chain")

    parts: List[str] = []
    left = '[0:a]'
    for i in range(1, num_inputs):
        right = f'[{i}:a]'
        out_label = '[a]' if i == num_inputs - 1 else f'[sa{i:02d}]'
        spec = join_specs[i - 1] if i - 1 < len(join_specs) else None
        if spec:
            crossfade_sec = spec[1] / 1000.0
            parts.append(
                f'{left}{right}acrossfade=d={crossfade_sec}:c1=tri:c2=tri{out_label}'
            )
        else:
            parts.append(f'{left}{right}concat=n=2:v=0:a=1{out_label}')
        left = out_label
    return ';'.join(parts), left


def _concatenate_muxed_with_shorten_joins(
    clip_files: List[str],
    output_file: str,
    join_specs: List[Optional[Tuple[float, float]]],
) -> None:
    """
    Concat muxed camera spans: hard video concat; audio acrossfade only at shorten joins.
    """
    valid_clips = _filter_valid_concat_clips(clip_files)
    if len(valid_clips) == 1:
        shutil.copy2(valid_clips[0], output_file)
        return

    if not any(join_specs):
        _concatenate_clips_demuxer_reencode(valid_clips, output_file)
        return

    n = len(valid_clips)
    video_inputs = ''.join(f'[{i}:v]' for i in range(n))
    video_filter = f'{video_inputs}concat=n={n}:v=1:a=0[v]'
    audio_filter, _audio_label = _build_muxed_shorten_audio_filter(n, join_specs)
    filter_complex = video_filter + ';' + audio_filter

    cmd = _ffmpeg_cmd_base()
    for clip_file in valid_clips:
        cmd.extend(['-i', clip_file])
    cmd.extend([
        '-filter_complex', filter_complex,
        '-map', '[v]',
        '-map', '[a]',
        '-r', OUTPUT_FPS_STR,
        '-pix_fmt', 'yuv420p',
        '-profile:v', 'high',
        '-c:a', 'aac', '-b:a', '320k',
        '-compression_level', '5',
    ])
    _run_ffmpeg_h264_encode_with_fallback(
        cmd,
        output_file,
        stage_default_preset='ultrafast',
        quality_level=23,
        use_cache=False,
        err_context='concat muxed shorten joins',
    )


def _concatenate_clips_demuxer(clip_files: List[str], output_file: str):
    """
    Concatenate clips using ffmpeg's concat demuxer.
    This approach doesn't open all files at once, avoiding "too many open files" errors.
    Note: This uses stream copy, so no crossfading.
    """
    valid_clips = _filter_valid_concat_clips(clip_files)
    concat_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    try:
        _write_concat_demuxer_list(concat_file.name, valid_clips)
        concat_file.close()

        cmd = _ffmpeg_cmd_base() + [
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file.name,
            '-c', 'copy',
            output_file,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    finally:
        if os.path.exists(concat_file.name):
            os.unlink(concat_file.name)


def apply_volume_adjustments(video_file: str, output_file: str, volume_timeline: List[Tuple[float, float, float]]):
    """
    Apply volume adjustments at specific points in the timeline.

    Strategy: Split audio into segments, apply volume to each, then concatenate.
    This is more reliable than using conditional expressions which FFmpeg doesn't support well.

    Args:
        video_file: Input video file
        output_file: Output video file with volume adjustments
        volume_timeline: List of (start_time, end_time, volume) tuples
    """
    if not volume_timeline:
        return

    print(f"\nApplying volume adjustments...")
    for start, end, vol in volume_timeline:
        print(f"  {start:.2f}s - {end:.2f}s: {vol}x")

    # Check if all volumes are 1.0
    if all(vol == 1.0 for _, _, vol in volume_timeline):
        import shutil
        shutil.copy2(video_file, output_file)
        return

    # Create a complex filter that splits, applies volume to each segment, and recombines
    # Using FFmpeg's segment and concat approach
    filter_complex_parts = []
    concat_inputs = []

    for i, (start_time, end_time, vol) in enumerate(volume_timeline):
        duration = end_time - start_time

        # Trim segment and apply volume
        if vol == 1.0:
            # No volume change needed, just trim
            filter_complex_parts.append(
                f"[0:v]trim=start={start_time}:duration={duration},setpts=PTS-STARTPTS[v{i}]"
            )
            filter_complex_parts.append(
                f"[0:a]atrim=start={start_time}:duration={duration},asetpts=PTS-STARTPTS[a{i}]"
            )
        else:
            # Trim and apply volume
            filter_complex_parts.append(
                f"[0:v]trim=start={start_time}:duration={duration},setpts=PTS-STARTPTS[v{i}]"
            )
            filter_complex_parts.append(
                f"[0:a]atrim=start={start_time}:duration={duration},asetpts=PTS-STARTPTS,volume={vol}[a{i}]"
            )

        concat_inputs.append(f"[v{i}][a{i}]")

    # Concatenate all segments
    concat_input_str = ''.join(concat_inputs)
    filter_complex_parts.append(
        f"{concat_input_str}concat=n={len(volume_timeline)}:v=1:a=1[outv][outa]"
    )

    filter_complex = ';'.join(filter_complex_parts)

    cmd = _ffmpeg_cmd_base() + [
        '-i', video_file,
        '-filter_complex', filter_complex,
        '-map', '[outv]',
        '-map', '[outa]',
        '-pix_fmt', 'yuv420p',
        '-profile:v', 'high',
        '-c:a', 'aac',
        '-b:a', '320k',  # Higher bitrate for better quality
        '-aac_coder', 'twoloop',  # Better quality AAC encoding
    ]
    _run_ffmpeg_h264_encode_with_fallback(
        cmd,
        output_file,
        stage_default_preset='ultrafast',
        quality_level=23,
        use_cache=False,
        err_context='volume adjustments',
    )

    print(f"Volume adjustments applied successfully!\n")


def apply_audio_overlays(video_file: str, output_file: str, audio_overlays: List[Tuple[float, str, float, float]]):
    """
    Apply audio overlays to a video file.

    Args:
        video_file: Input video file
        output_file: Output video file with audio overlays
        audio_overlays: List of (timestamp_seconds, audio_file, volume, speed) tuples
            speed: 1.0 = normal, <1.0 = slower/deeper, >1.0 = faster/higher
    """
    if not audio_overlays:
        return

    # Defensive check: filter out missing audio files (should have been caught at parse time)
    valid_overlays = []
    for timestamp, audio_file, volume, speed in audio_overlays:
        if os.path.exists(audio_file):
            valid_overlays.append((timestamp, audio_file, volume, speed))
        else:
            # This should rarely trigger since parser checks file existence
            print(f"Warning: Audio file missing at render time: {audio_file}", file=sys.stderr)

    if not valid_overlays:
        print(f"\nNo valid audio overlays found, skipping...\n")
        return

    print(f"\nApplying {len(valid_overlays)} audio overlay(s)...")

    # Build ffmpeg command with audio overlays
    cmd = _ffmpeg_cmd_base() + ['-i', video_file]

    # Add all audio overlay files as inputs
    for i, (timestamp, audio_file, volume, speed) in enumerate(valid_overlays):
        cmd.extend(['-i', audio_file])
        speed_desc = f", speed={speed}x" if speed != 1.0 else ""
        print(f"  Overlay {i+1}: {os.path.basename(audio_file)} at {timestamp:.2f}s (volume={volume}{speed_desc})")

    # Build filter_complex for audio mixing
    # [0:a] is the original audio from video
    # [1:a], [2:a], etc. are the overlay audio files

    filter_parts = []

    # Apply speed shift, delay, and volume to each overlay audio
    for i, (timestamp, audio_file, volume, speed) in enumerate(valid_overlays):
        delay_ms = int(timestamp * 1000)
        input_idx = i + 1  # Input 0 is video, overlays start at 1

        # Build filter chain for this overlay
        filters = []

        # Speed shift (if not 1.0): changes both speed and pitch
        if speed != 1.0:
            # asetrate changes the sample rate interpretation, aresample converts back
            # If speed=0.5, audio plays at half speed with lower pitch
            # If speed=2.0, audio plays at double speed with higher pitch
            new_rate = int(48000 * speed)
            filters.append(f'asetrate={new_rate},aresample=48000')

        # Delay
        filters.append(f'adelay={delay_ms}|{delay_ms}')

        # Volume
        filters.append(f'volume={volume}')

        filter_chain = ','.join(filters)
        filter_parts.append(f'[{input_idx}:a]{filter_chain}[a{input_idx}]')

    # Mix all audio streams together
    # Start with original audio [0:a]
    mix_inputs = '[0:a]'
    for i in range(len(valid_overlays)):
        input_idx = i + 1
        mix_inputs += f'[a{input_idx}]'

    # Use amix filter to mix all audio streams
    # normalize=0 prevents automatic volume reduction when mixing
    filter_parts.append(f'{mix_inputs}amix=inputs={len(valid_overlays) + 1}:duration=first:dropout_transition=0:normalize=0[aout]')

    filter_complex = ';'.join(filter_parts)

    cmd.extend([
        '-filter_complex', filter_complex,
        '-map', '0:v',  # Copy video from first input
        '-map', '[aout]',  # Use mixed audio
        '-c:v', 'copy',  # Copy video without re-encoding
        '-c:a', 'aac', '-b:a', '320k',
        '-compression_level', '5',  # Good balance of speed and compression
        '-shortest',  # End when shortest stream ends (video)
        output_file
    ])

    # Run FFmpeg (show errors for debugging)
    result = subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"\nFFmpeg error output:\n{result.stderr}", file=sys.stderr)
        raise RuntimeError(f"Audio overlay failed with exit code {result.returncode}")

    print(f"Audio overlays applied successfully!\n")


def generate_black_clip(duration_ms: float, output_file: str):
    """
    Generate a black video clip with silence for the specified duration.

    Args:
        duration_ms: Duration in milliseconds
        output_file: Output file path

    Returns:
        Duration in seconds
    """
    duration_sec = duration_ms / 1000.0

    # Generate black video (640x360, 23.976fps) with silent audio to match source
    cmd = _ffmpeg_cmd_base() + [
        '-f', 'lavfi', '-i', f'color=c=black:s=640x360:r=24000/1001:d={duration_sec}',
        '-f', 'lavfi', '-i', f'anullsrc=r=48000:cl=stereo:d={duration_sec}',
        '-pix_fmt', 'yuv420p',
        '-profile:v', 'high',
        '-c:a', 'aac', '-b:a', '320k',
        '-compression_level', '5',  # Good balance of speed and compression
        '-shortest',
    ]
    _run_ffmpeg_h264_encode_with_fallback(
        cmd,
        output_file,
        stage_default_preset='ultrafast',
        quality_level=23,
        use_cache=True,
        err_context='black clip',
    )

    return duration_sec


def extract_clip_group(group: List[Tuple[str, str, str, float, float, Optional[float], Optional[float], Optional[float], Optional[float], float]],
                       output_file: str, margin: float = 0.0,
                       episode_starts_at_timeline_zero: bool = False,
                       opening_preroll_sec: Optional[float] = None):
    """
    Extract a group of clips as a single continuous clip with continuous audio.
    Renders video and audio together in a single FFmpeg call for perfect sync.

    When the segment uses ``use_video_embedded_audio`` in ``SEGMENT_CONFIG``, audio is
    taken from each camera MP4 instead of the segment ``audio_file``.

    Otherwise, for mixed-camera groups: extract audio once continuously from ``audio_file``,
    then extract and concatenate video segments for each camera.

    Args:
        group: List of (segment_id, camera, comment, cut_before, cut_after, fade_in_ms, fade_out_ms, slice_start, slice_end, volume) tuples
        output_file: Output file (complete video with audio)

    Returns:
        Duration of the extracted clip
    """
    if not group:
        return 0.0

    # Extract group info
    segment_ids = [clip[0] for clip in group]
    cameras = [clip[1] for clip in group]
    slice_starts = [clip[7] for clip in group]
    slice_ends = [clip[8] for clip in group]
    volumes = [clip[9] for clip in group]
    before_padding_ms = group[0][3]
    after_padding_ms = group[0][4]
    fade_in_ms = group[0][5]
    fade_out_ms = group[-1][6]

    # Check if all cameras are the same (simple case)
    if len(set(cameras)) == 1:
        # Simple case: all same camera, use original logic
        camera = cameras[0]
        volume = volumes[0]  # Use first volume (all should be same in a group)
        clips_info = [get_clip_info(sid, camera, slice_start, slice_end, margin)
                      for sid, slice_start, slice_end in zip(segment_ids, slice_starts, slice_ends)]
        return _extract_single_camera_group(
            segment_ids, clips_info, camera, output_file,
            before_padding_ms, after_padding_ms, fade_in_ms, fade_out_ms, volume,
            episode_starts_at_timeline_zero=episode_starts_at_timeline_zero,
            opening_preroll_sec=opening_preroll_sec,
        )
    else:
        # Complex case: camera changes within group - extract audio once, video separately
        return _extract_multi_camera_group(
            group, output_file, margin,
            episode_starts_at_timeline_zero=episode_starts_at_timeline_zero,
            opening_preroll_sec=opening_preroll_sec,
        )


def _extract_single_camera_group(segment_ids: List[str], clips_info: List[Dict], camera: str,
                                 output_file: str,
                                 before_padding_ms: float, after_padding_ms: float,
                                 fade_in_ms: Optional[float], fade_out_ms: Optional[float],
                                 volume: float = 1.0,
                                 episode_starts_at_timeline_zero: bool = False,
                                 opening_preroll_sec: Optional[float] = None):
    """
    Extract group where all clips use the same camera.
    Renders video and audio together in a single FFmpeg call for perfect sync.
    Volume is applied during extraction using FFmpeg's volume filter.
    """

    # Get the main audio file from segment config
    segment_num, _ = parse_segment_id(segment_ids[0])
    config = get_segment_config(segment_num)
    main_audio_file = config['audio_file']
    audio_offset_in_file = config.get('audio_offset', 0)
    target_width, target_height = _get_segment_target_resolution(segment_num)

    first_clip = clips_info[0]
    last_clip = clips_info[-1]

    # Convert padding from milliseconds to seconds
    before_padding = before_padding_ms / 1000.0
    after_padding = after_padding_ms / 1000.0

    # Extract from start of first clip to end of last clip (including gaps and padding).
    # Full-episode opening: include master media from timeline 0 through first utterance,
    # unless the DSL explicitly requests a preroll relative to the first spoken clip.
    if opening_preroll_sec is not None:
        opening_lead_in = max(before_padding, opening_preroll_sec)
        audio_start = max(0, first_clip['audio_start'] - opening_lead_in + audio_offset_in_file)
        video_start = max(0, first_clip['video_start'] - opening_lead_in)
    elif episode_starts_at_timeline_zero:
        audio_start = audio_offset_in_file
        video_start = max(0.0, first_clip['video_start'] - first_clip['audio_start'])
    else:
        audio_start = max(0, first_clip['audio_start'] - before_padding + audio_offset_in_file)
        video_start = max(0, first_clip['video_start'] - before_padding)
    audio_end = last_clip['audio_end'] + after_padding + audio_offset_in_file
    video_end = last_clip['video_end'] + after_padding
    use_video_embedded_audio = _use_video_embedded_audio(config)
    if use_video_embedded_audio:
        duration = max(0.0, video_end - video_start)
    else:
        duration = audio_end - audio_start

    # Build filter_complex for scaling + optional wide-referenced color match + fades
    filter_parts = []
    has_video_filters = bool(fade_in_ms or fade_out_ms)
    source_width, source_height = _get_video_dimensions(first_clip['video_file'])
    needs_scaling = (source_width, source_height) != (target_width, target_height)
    eq_snippet = _segment_color_match_eq(segment_num, camera)
    needs_color = bool(eq_snippet)

    # Video filters (fades)
    if needs_scaling or has_video_filters or needs_color:
        video_filter_chain = []
        if needs_scaling:
            video_filter_chain.append(
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
            )
        _append_vf_snippet(video_filter_chain, eq_snippet)
        if fade_in_ms:
            fade_in_sec = fade_in_ms / 1000.0
            video_filter_chain.append(f"fade=t=in:st=0:d={fade_in_sec}")
        if fade_out_ms:
            fade_out_sec = fade_out_ms / 1000.0
            fade_out_start = duration - fade_out_sec
            video_filter_chain.append(f"fade=t=out:st={fade_out_start}:d={fade_out_sec}")
        filter_parts.append(f"[0:v]{','.join(video_filter_chain)}[vout]")

    # Single FFmpeg call to extract and combine video + audio for perfect sync
    cmd = _ffmpeg_cmd_base()

    if use_video_embedded_audio:
        _append_accurate_muxed_input_seek(cmd, first_clip['video_file'], video_start, duration)
        if filter_parts:
            cmd.extend(['-filter_complex', ';'.join(filter_parts)])
            cmd.extend(['-map', '[vout]'])
            cmd.extend(['-map', '0:a'])
        else:
            cmd.extend(['-map', '0:v', '-map', '0:a'])
    else:
        # Fast seek before -i; second input is master audio (interview path).
        _append_fast_input_seek(cmd, first_clip['video_file'], video_start)
        _append_fast_input_seek(cmd, main_audio_file, audio_start)
        cmd.extend(['-t', str(duration)])
        if filter_parts:
            cmd.extend(['-filter_complex', ';'.join(filter_parts)])
            cmd.extend(['-map', '[vout]'])
            cmd.extend(['-map', '1:a'])
        else:
            cmd.extend(['-map', '0:v', '-map', '1:a'])

    # Encoding parameters
    # Use AAC for audio in intermediate segments (MP4 compatible)
    cmd.extend([
        '-r', '24000/1001',  # Preserve source frame rate
        '-pix_fmt', 'yuv420p',
        '-profile:v', 'high',
        '-c:a', 'aac', '-b:a', '320k',
        '-compression_level', '5',  # Good balance of speed and compression
        '-shortest',
    ])
    _run_ffmpeg_h264_encode_with_fallback(
        cmd,
        output_file,
        stage_default_preset='ultrafast',
        quality_level=23,
        use_cache=True,
        err_context='single-camera extract',
    )

    return duration


def _build_playback_intervals(
    clip_infos: List[dict],
    group_audio_start: float,
    group_audio_end: float,
) -> List[dict]:
    """
    Build DSL-playback-order intervals on the master-audio timeline.

    Each clip owns ``[start, end]`` in list order. A sliced ``audio_start`` on a
    later clip cannot pull a camera cut before an earlier clip has finished.
    Gaps between clips keep the previous clip's camera.
    """
    intervals: List[dict] = []
    playhead = group_audio_start

    for idx, clip in enumerate(clip_infos):
        clip_info = clip['clip_info']
        a_start = clip_info['audio_start']
        a_end = clip_info['audio_end']

        if idx == 0:
            int_start = group_audio_start
        else:
            if a_start > playhead + 1e-9:
                prev = clip_infos[idx - 1]
                intervals.append({
                    't_start': playhead,
                    't_end': a_start,
                    'clip_idx': idx - 1,
                    'camera': prev['camera'],
                    'clip_info': prev['clip_info'],
                    'is_gap': True,
                })
            int_start = max(playhead, a_start)

        if a_end > int_start + 1e-9:
            intervals.append({
                't_start': int_start,
                't_end': a_end,
                'clip_idx': idx,
                'camera': clip['camera'],
                'clip_info': clip_info,
                'is_gap': False,
            })
        playhead = max(playhead, a_end)

    if playhead < group_audio_end - 1e-9 and clip_infos:
        last = clip_infos[-1]
        intervals.append({
            't_start': playhead,
            't_end': group_audio_end,
            'clip_idx': len(clip_infos) - 1,
            'camera': last['camera'],
            'clip_info': last['clip_info'],
            'is_gap': True,
        })

    return intervals


def _snap_playback_interval_boundaries(
    intervals: List[dict],
    *,
    half_window_sec: float = 0.05,
    min_gap_sec: float = 1.0 / 30.0,
) -> None:
    """Snap interior junctions between playback intervals to zero crossings."""
    if len(intervals) < 2:
        return

    for i in range(len(intervals) - 1):
        left = intervals[i]
        right = intervals[i + 1]
        nominal = left['t_end']
        if abs(nominal - right['t_start']) > 1e-6:
            nominal = max(left['t_start'], min(left['t_end'], right['t_start']))

        lo = left['t_start'] + min_gap_sec
        hi = right['t_end'] - min_gap_sec
        if lo >= hi:
            continue

        snapped = snap_boundary_group_time(
            nominal,
            left['clip_info'],
            right['clip_info'],
            half_window_sec,
        )
        snapped = max(lo, min(hi, snapped))
        left['t_end'] = snapped
        right['t_start'] = snapped


def _build_camera_spans(group: List[Tuple[str, str, str, float, float, Optional[float], Optional[float], Optional[float], Optional[float], float]],
                        margin: float,
                        group_audio_start: float,
                        group_audio_end: float,
                        segment_num: str) -> List[Dict]:
    """
    Build a camera timeline for a grouped clip extraction.

    Intervals follow DSL clip order on the continuous master-audio timeline so
    ``slice()`` lead-ins cannot reorder camera cuts relative to earlier clips.
    """
    clip_infos = []
    for segment_id, camera, _, _, _, _, _, slice_start, slice_end, _, *_ in group:
        clip_infos.append({
            'camera': camera,
            'clip_info': get_clip_info(segment_id, camera, slice_start, slice_end, margin)
        })

    intervals = _build_playback_intervals(clip_infos, group_audio_start, group_audio_end)

    config = get_segment_config(segment_num)
    if snap_enabled() and _use_video_embedded_audio(config):
        _snap_playback_interval_boundaries(intervals)

    pad_before = []
    for idx in range(len(clip_infos)):
        spec = _shorten_join_from_clip(group[idx]) if idx < len(group) else None
        pad_before.append((spec[0] / 1000.0) if spec else 0.0)

    spans = []
    for interval in intervals:
        clip_idx = interval['clip_idx']
        clip_info = interval['clip_info']
        camera = interval['camera']
        t_start = interval['t_start']
        t_end = interval['t_end']

        if not interval['is_gap']:
            if clip_idx > 0:
                t_start -= pad_before[clip_idx]
            if clip_idx + 1 < len(pad_before):
                t_end += pad_before[clip_idx + 1]

        rel_start = t_start - group_audio_start
        rel_end = t_end - group_audio_start
        start_frame = round(rel_start * OUTPUT_FPS)
        end_frame = round(rel_end * OUTPUT_FPS)
        frame_count = end_frame - start_frame
        if frame_count <= 0:
            continue

        span_audio_start = group_audio_start + (start_frame / OUTPUT_FPS)
        span_audio_end = group_audio_start + (end_frame / OUTPUT_FPS)
        span_duration = frame_count / OUTPUT_FPS

        # Translate the snapped timeline start back into the source camera's clock.
        video_start = max(0, clip_info['video_start'] + (span_audio_start - clip_info['audio_start']))

        shorten_spec = None if interval['is_gap'] else _shorten_join_from_clip(group[clip_idx])

        if spans and spans[-1]['camera'] == camera:
            spans[-1]['duration'] += span_duration
            spans[-1]['frame_count'] += frame_count
            spans[-1]['audio_end'] = span_audio_end
            continue

        spans.append({
            'segment_num': segment_num,
            'camera': camera,
            'video_file': clip_info['video_file'],
            'video_start': video_start,
            'duration': span_duration,
            'frame_count': frame_count,
            'audio_start': span_audio_start,
            'audio_end': span_audio_end,
            'shorten_join_spec': shorten_spec,
        })

    return spans


def _extract_camera_segment(args):
    """
    Extract a single camera span from a multi-camera group.

    When ``use_video_embedded_audio`` is true, ``-frames:v`` snaps video to the
    frame grid and ``-shortest`` trims audio to the same muxed length so concat
    does not show the next camera while the previous span's audio still plays.

    Args:
        args: Tuple of (span, fade_in_ms, fade_out_ms, is_first, is_last,
                       target_width, target_height, use_video_embedded_audio)

    Returns:
        Path to the temporary segment file
    """
    (span, fade_in_ms, fade_out_ms, is_first, is_last,
     target_width, target_height, use_video_embedded_audio) = args

    video_file = span['video_file']
    video_start = span['video_start']
    segment_duration = span['duration']
    frame_count = span['frame_count']
    segment_num = span['segment_num']
    camera = span['camera']

    # Create temp file for this segment. By default strip audio so one master WAV
    # can be muxed after concatenation; with use_video_embedded_audio, keep each
    # camera's embedded track for concat + final output.
    temp_fd, temp_path = tempfile.mkstemp(suffix='.mp4')
    os.close(temp_fd)  # Close the file descriptor
    os.unlink(temp_path)  # Delete the empty file - FFmpeg will create it

    # Build filter_complex for seek-based extraction plus any scaling/fades.
    filter_parts = []
    source_width, source_height = _get_video_dimensions(video_file)
    needs_scaling = (source_width, source_height) != (target_width, target_height)

    video_filter_chain = []
    if needs_scaling:
        video_filter_chain.append(
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )
    _append_vf_snippet(video_filter_chain, _segment_color_match_eq(segment_num, camera))
    if is_first and fade_in_ms:
        fade_in_sec = fade_in_ms / 1000.0
        video_filter_chain.append(f"fade=t=in:st=0:d={fade_in_sec}")
    if is_last and fade_out_ms:
        fade_out_sec = fade_out_ms / 1000.0
        fade_out_start = segment_duration - fade_out_sec
        video_filter_chain.append(f"fade=t=out:st={fade_out_start}:d={fade_out_sec}")

    if video_filter_chain:
        filter_parts.append(f"[0:v]{','.join(video_filter_chain)}[vout]")

    cmd = _ffmpeg_cmd_base()
    if use_video_embedded_audio:
        _append_accurate_muxed_input_seek(cmd, video_file, video_start, segment_duration)
    else:
        _append_fast_input_seek(cmd, video_file, video_start)
        cmd.extend(['-t', str(segment_duration)])

    # Add filter_complex if we have any filters
    if filter_parts:
        cmd.extend(['-filter_complex', ';'.join(filter_parts)])
        cmd.extend(['-map', '[vout]'])
    else:
        cmd.extend(['-map', '0:v'])

    if use_video_embedded_audio:
        cmd.extend(['-map', '0:a'])

    # Encoding parameters
    enc_tail = [
        '-r', OUTPUT_FPS_STR,  # Preserve source frame rate
        '-pix_fmt', 'yuv420p',
        '-profile:v', 'high',
        '-frames:v', str(frame_count),
    ]
    cmd.extend(enc_tail)
    if use_video_embedded_audio:
        cmd.extend([
            '-c:a', 'aac', '-b:a', '320k', '-compression_level', '5',
            '-shortest',  # Match audio length to -frames:v (same as single-camera path)
        ])
    else:
        cmd.extend(['-an'])
    try:
        _run_ffmpeg_h264_encode_with_fallback(
            cmd,
            temp_path,
            stage_default_preset='ultrafast',
            quality_level=23,
            use_cache=True,
            err_context=f'temp segment ({temp_path})',
        )
    except RuntimeError as exc:
        # Stable message for callers; FFmpeg details were already printed by the helper.
        raise RuntimeError(f"Temp segment creation failed: {temp_path}") from exc

    return temp_path


def _extract_multi_camera_group(group: List[Tuple[str, str, str, float, float, Optional[float], Optional[float], Optional[float], Optional[float], float]],
                                output_file: str, margin: float = 0.0,
                                episode_starts_at_timeline_zero: bool = False,
                                opening_preroll_sec: Optional[float] = None):
    """
    Extract group with camera changes, using a continuous group audio timeline.

    Strategy when ``use_video_embedded_audio`` is false (typical interview segments):
    1. Build camera spans over the grouped timeline
    2. Render each camera span as video-only media
    3. Concatenate the video-only spans
    4. Mux the concatenated video with one continuous audio extract from ``audio_file``

    When ``use_video_embedded_audio`` is true (reading segments by default):
    1–3 as above, but each span keeps embedded audio; concat (re-encoded) yields the final output.
    """
    segment_ids = [clip[0] for clip in group]
    cameras = [clip[1] for clip in group]
    slice_starts = [clip[7] for clip in group]
    slice_ends = [clip[8] for clip in group]
    volumes = [clip[9] for clip in group]
    before_padding_ms = group[0][3]
    after_padding_ms = group[0][4]
    fade_in_ms = group[0][5]
    fade_out_ms = group[-1][6]
    volume = volumes[0]  # Use first volume (all should be same in a group)

    before_padding = before_padding_ms / 1000.0
    after_padding = after_padding_ms / 1000.0

    # Get the segment number (all segments in group should be from same segment)
    segment_num, _ = parse_segment_id(segment_ids[0])
    config = get_segment_config(segment_num)
    main_audio_file = config['audio_file']
    audio_offset_in_file = config.get('audio_offset', 0)
    use_video_embedded_audio = _use_video_embedded_audio(config)
    target_width, target_height = _get_segment_target_resolution(segment_num)

    # Calculate the continuous group audio range.
    first_clip = get_clip_info(segment_ids[0], cameras[0], slice_starts[0], slice_ends[0], margin)
    last_clip = get_clip_info(segment_ids[-1], cameras[-1], slice_starts[-1], slice_ends[-1], margin)
    if opening_preroll_sec is not None:
        opening_lead_in = max(before_padding, opening_preroll_sec)
        group_audio_start = max(0, first_clip['audio_start'] - opening_lead_in)
    elif episode_starts_at_timeline_zero:
        group_audio_start = 0.0
    else:
        group_audio_start = max(0, first_clip['audio_start'] - before_padding)
    group_audio_end = last_clip['audio_end'] + after_padding
    group_duration = group_audio_end - group_audio_start

    camera_spans = _build_camera_spans(group, margin, group_audio_start, group_audio_end, segment_num)
    group_duration = sum(span['duration'] for span in camera_spans)

    camera_segment_tasks = []
    for idx, span in enumerate(camera_spans):
        task = (
            span,
            fade_in_ms,
            fade_out_ms,
            idx == 0,
            idx == len(camera_spans) - 1,
            target_width,
            target_height,
            use_video_embedded_audio,
        )
        camera_segment_tasks.append(task)
    # Extract all camera segments in parallel.
    # Check if we're already in a worker process (can't nest pools)
    import multiprocessing
    current_proc = multiprocessing.current_process()
    is_daemon = current_proc.daemon if hasattr(current_proc, 'daemon') else False

    if len(camera_segment_tasks) == 1 or is_daemon:
        # Single camera segment or already in a worker process - use sequential processing
        combined_segments = [_extract_camera_segment(task) for task in camera_segment_tasks]
    else:
        # Multiple camera segments - use parallel processing
        num_workers = min(len(camera_segment_tasks), cpu_count())
        with Pool(processes=num_workers) as pool:
            combined_segments = pool.map(_extract_camera_segment, camera_segment_tasks)

    audio_start_in_main_file = group_audio_start + audio_offset_in_file

    try:
        temp_fd, temp_video_path = tempfile.mkstemp(suffix='.mp4')
        os.close(temp_fd)
        os.unlink(temp_video_path)

        # Keep each span muxed when re-encoding (never split video/audio concat chains).
        if use_video_embedded_audio:
            join_specs = [
                camera_spans[i].get('shorten_join_spec')
                for i in range(1, len(camera_spans))
            ]
            if any(join_specs):
                _concatenate_muxed_with_shorten_joins(
                    combined_segments, temp_video_path, join_specs,
                )
            else:
                concatenate_clips(
                    combined_segments,
                    temp_video_path,
                    muxed_reencode=True,
                )
        else:
            concatenate_clips(
                combined_segments,
                temp_video_path,
                muxed_reencode=False,
            )

        if use_video_embedded_audio:
            shutil.copy2(temp_video_path, output_file)
        else:
            cmd = _ffmpeg_cmd_base() + [
                '-i', temp_video_path,
                '-ss', str(audio_start_in_main_file), '-i', main_audio_file,
                '-t', str(group_duration),
                '-map', '0:v',
                '-map', '1:a',
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', '320k',
                '-compression_level', '5',
                '-shortest',
                output_file,
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        for seg in combined_segments:
            if os.path.exists(seg):
                os.unlink(seg)
        if 'temp_video_path' in locals() and os.path.exists(temp_video_path):
            os.unlink(temp_video_path)

    return group_duration


def _render_segment_wrapper(args):
    """Wrapper function for parallel segment rendering"""
    group, output_file, group_idx, total_groups, margin, skip_clips, opening_preroll_sec = args

    # Check if this is a black clip (special segment)
    segment_id = group[0][0]
    if segment_id.startswith('__BLACK__'):
        # Extract duration from special segment ID format: __BLACK__:{duration_ms}
        duration_ms = float(segment_id.split(':')[1])
        print(f"Rendering segment {group_idx+1}/{total_groups}: BLACK ({duration_ms}ms)")
        duration = generate_black_clip(duration_ms, output_file)
        print(f"  Segment {group_idx+1} complete: {duration:.2f}s (black frames)")
        return output_file, duration

    # Regular video clip rendering
    # Get camera info and volume for display
    cameras_in_group = list(set(clip[1] for clip in group))
    volumes_in_group = list(set(clip[9] for clip in group))
    camera_desc = cameras_in_group[0] if len(cameras_in_group) == 1 else f"{len(cameras_in_group)} cameras"
    volume = volumes_in_group[0]  # All clips in group should have same volume

    if len(group) == 1:
        segment_id, camera, _, _, _, _, _, _, _, _, *_ = group[0]
        desc = f"{segment_id} [{camera}]"
    else:
        segment_id_first = group[0][0]
        segment_id_last = group[-1][0]
        desc = f"{segment_id_first} - {segment_id_last} [{camera_desc}]"

    if volume != 1.0:
        desc += f" @{volume}x"

    print(f"Rendering segment {group_idx+1}/{total_groups}: {desc}")

    # Use .mp4 extension for AAC audio (all intermediate files)

    # Extract clip (volume is applied during extraction now)
    use_opening_preroll = skip_clips == 0 and group_idx == 0 and opening_preroll_sec is not None
    episode_starts_at_timeline_zero = skip_clips == 0 and group_idx == 0 and not use_opening_preroll
    duration = extract_clip_group(
        group, output_file, margin,
        episode_starts_at_timeline_zero=episode_starts_at_timeline_zero,
        opening_preroll_sec=opening_preroll_sec if use_opening_preroll else None,
    )

    # Get fade and cut info for display
    fade_in = group[0][5]
    fade_out = group[-1][6]
    cut_before = group[0][3]
    cut_after = group[0][4]

    fade_desc = ""
    if fade_in and fade_out:
        fade_desc = f", fade in {fade_in}ms, fade out {fade_out}ms"
    elif fade_in:
        fade_desc = f", fade in {fade_in}ms"
    elif fade_out:
        fade_desc = f", fade out {fade_out}ms"

    vol_desc = f", volume {volume}x" if volume != 1.0 else ""
    print(f"  Segment {group_idx+1} complete: {duration:.2f}s (with {cut_before}ms before, {cut_after}ms after padding{fade_desc}{vol_desc})")

    return output_file, duration


def render_all_cams(dsl_file: str, output_file: str, dry_run: bool = False,
                    skip_clips: int = 0, limit_clips: Optional[int] = None,
                    max_seconds: Optional[float] = None,
                    debug: bool = False,
                    num_workers: int = 8, margin: float = 0.0):
    """
    Render separate output files for each camera feed.

    Instead of cutting between cameras, this renders the complete timeline from each camera's perspective.

    Args:
        dsl_file: DSL file to parse
        output_file: Base output file (will be modified to include camera name)
        dry_run: If True, only calculate durations
        skip_clips: Skip first N clips
        limit_clips: Render only M clips
        debug: Save intermediate segments
        num_workers: Number of parallel workers
    """
    from .parser import parse_dsl_file
    from .commands import CameraCommand, SegmentCommand
    import os

    print(f"\n{'='*70}")
    print(f"RENDER ALL CAMS MODE")
    print(f"Rendering separate output for each camera feed")
    print(f"{'='*70}\n")

    # Parse DSL file
    print("Parsing DSL file...")
    commands = parse_dsl_file(dsl_file)
    print(f"Found {len(commands)} commands\n")
    _run_dsl_sanity_check(dsl_file)

    # Extract all segment IDs to determine available cameras
    segment_ids = []
    for cmd in commands:
        if type(cmd).__name__ == 'SegmentCommand':
            segment_ids.append(cmd.segment_id)

    # Determine all unique cameras from the segments
    cameras = set()
    for segment_id in segment_ids:
        # Skip black segments
        if segment_id.startswith('__BLACK__'):
            continue

        segment_num, _ = parse_segment_id(segment_id)
        config = get_segment_config(segment_num)
        cameras.update(config['video_files'].keys())

    cameras = sorted(cameras)  # Sort for consistent ordering

    print(f"Found {len(cameras)} cameras: {', '.join(cameras)}")
    print(f"Will render {len(cameras)} separate output files\n")

    # Generate output file pattern
    base_name = os.path.splitext(output_file)[0]
    extension = os.path.splitext(output_file)[1]

    # Render each camera
    for i, camera in enumerate(cameras, 1):
        print(f"\n{'='*70}")
        print(f"RENDERING CAMERA {i}/{len(cameras)}: {camera}")
        print(f"{'='*70}\n")

        # Create modified command list with camera forced to this one
        # Remove ALL camera commands from the original DSL and set one at the start
        modified_commands = [CameraCommand(camera)]
        for cmd in commands:
            # Skip any camera commands - we're forcing a single camera
            if type(cmd).__name__ != 'CameraCommand':
                modified_commands.append(cmd)

        # Generate output filename
        camera_output = f"{base_name}_{camera}{extension}"

        # Call render_dsl_from_commands with the modified commands
        _render_dsl_from_commands(
            modified_commands,
            camera_output,
            dsl_file=dsl_file,
            dry_run=dry_run,
            auto_cuts=False,  # Don't use auto-cuts in render-all-cams mode
            skip_clips=skip_clips,
            limit_clips=limit_clips,
            max_seconds=max_seconds,
            debug=debug,
            num_workers=num_workers,
            margin=margin
        )

        if not dry_run:
            print(f"\nCamera '{camera}' output saved to: {camera_output}")

    print(f"\n{'='*70}")
    print(f"ALL CAMERAS RENDERED SUCCESSFULLY")
    print(f"Output files:")
    for camera in cameras:
        camera_output = f"{base_name}_{camera}{extension}"
        print(f"  - {camera_output}")
    print(f"{'='*70}\n")


def render_dsl(dsl_file: str, output_file: str, dry_run: bool = False, auto_cuts: bool = False,
               auto_cuts_legacy: bool = False,
               skip_clips: int = 0, limit_clips: Optional[int] = None,
               max_seconds: Optional[float] = None,
               debug: bool = False,
               num_workers: int = 8, margin: float = 0.0):
    """Render a DSL file to video"""
    from .parser import parse_dsl_file

    # Parse DSL
    print("Parsing DSL file...")
    commands = parse_dsl_file(dsl_file)
    print(f"Found {len(commands)} commands\n")
    _run_dsl_sanity_check(dsl_file)

    # Apply auto-cuts if requested
    if auto_cuts:
        from auto_cuts import insert_auto_cuts
        if auto_cuts_legacy:
            print("Applying legacy auto-cut heuristics (random wide, 5s minimum)...")
            commands = insert_auto_cuts(commands, min_clip_duration=5.0, legacy=True)
        else:
            print(
                "Applying auto-cut heuristics (Ben open, intro/crosstalk wide, "
                "5s min / random wide / <1s hold)..."
            )
            commands = insert_auto_cuts(commands, legacy=False)
        print(f"After auto-cuts: {len(commands)} commands\n")

    _render_dsl_from_commands(
        commands,
        output_file,
        dsl_file=dsl_file,
        dry_run=dry_run,
        auto_cuts=auto_cuts,
        skip_clips=skip_clips,
        limit_clips=limit_clips,
        max_seconds=max_seconds,
        debug=debug,
        num_workers=num_workers,
        margin=margin
    )


def _render_dsl_from_commands(commands: List, output_file: str, dsl_file: str = None,
                              dry_run: bool = False, auto_cuts: bool = False,
                              skip_clips: int = 0, limit_clips: Optional[int] = None,
                              max_seconds: Optional[float] = None,
                              debug: bool = False, num_workers: int = 8, margin: float = 0.0):
    """
    Internal function to render DSL commands to video.
    Extracted from render_dsl to allow reuse by render_all_cams.
    """
    from .clip_processing import apply_shorten_join_clip_bounds, group_consecutive_clips
    import shutil

    _set_render_dsl_context(dsl_file)

    print(f"\n{'='*70}")
    if dry_run:
        print(f"DRY RUN MODE - Calculating duration only")
    if auto_cuts:
        print(f"AUTO-CUTS MODE - Generating camera cuts automatically")
    if skip_clips > 0 or limit_clips is not None:
        skip_msg = f"skip first {skip_clips}" if skip_clips > 0 else ""
        limit_msg = f"limit to {limit_clips} clips" if limit_clips is not None else ""
        sep = ", " if skip_msg and limit_msg else ""
        print(f"TEST MODE - {skip_msg}{sep}{limit_msg}")
    if dsl_file and dsl_file == "-":
        print(f"DSL source: stdin")
    elif dsl_file:
        print(f"DSL source: {dsl_file}")
    print(f"{'='*70}\n")

    # Process commands
    current_camera = 'wide'  # Default camera
    current_cut_before = 50.0  # Default 50ms before
    current_cut_after = 50.0   # Default 50ms after
    current_volume = 1.0  # Default volume (1.0 = 100%)
    opening_preroll_ms = None  # Optional preroll for the first content clip/group
    pending_fade_in = None  # Fade in duration for next clip
    pending_shorten_join = None  # (padding_ms, crossfade_ms) for next segment
    clips_to_render = []  # (segment_id, camera, comment, cut_before, cut_after, fade_in_ms, fade_out_ms, slice_start, slice_end, volume, shorten_join)
    audio_overlays = []  # Each item: (clip_index, audio_file, volume, speed)

    for cmd in commands:
        cmd_type = type(cmd).__name__
        if cmd_type == 'ShortenJoinCommand':
            pending_shorten_join = (cmd.padding_ms, cmd.crossfade_ms)
        elif cmd_type == 'CameraCommand':
            current_camera = cmd.camera_name
        elif cmd_type == 'CutCommand':
            current_cut_before = cmd.before_ms
            current_cut_after = cmd.after_ms
        elif cmd_type == 'OpeningPrerollCommand':
            opening_preroll_ms = cmd.preroll_ms
        elif cmd_type == 'VolumeCommand':
            if cmd.volume != 1.0:
                raise NotImplementedError(
                    f"Volume command is not implemented. Requested volume: {cmd.volume}x. "
                    f"The !volume command was removed due to reliability issues with multi-camera segments. "
                    f"Please remove !volume commands from your DSL file."
                )
            current_volume = cmd.volume
        elif cmd_type == 'FadeFromBlackCommand':
            pending_fade_in = cmd.duration_ms
        elif cmd_type == 'FadeToBlackCommand':
            if clips_to_render:
                # Apply fade out to the previous clip
                prev_clip = clips_to_render[-1]
                # Update the last clip to include fade out
                clips_to_render[-1] = (prev_clip[0], prev_clip[1], prev_clip[2],
                                       prev_clip[3], prev_clip[4], prev_clip[5], cmd.duration_ms,
                                       prev_clip[7], prev_clip[8], prev_clip[9],
                                       prev_clip[10] if len(prev_clip) > 10 else None)
        elif cmd_type == 'BlackCommand':
            # Add black clip as a special segment
            # Format: __BLACK__:{duration_ms}
            black_segment_id = f"__BLACK__:{cmd.duration_ms}"
            clips_to_render.append((black_segment_id, 'black', '', 0.0, 0.0, None, None, None, None, 1.0, None))
        elif cmd_type == 'AudioCommand':
            # Store audio overlay for later processing (we'll calculate timeline position after grouping)
            # For now, just store the command with its position in the clips_to_render list
            audio_overlays.append((len(clips_to_render), cmd.audio_file, cmd.volume, cmd.speed))
        elif cmd_type == 'SegmentCommand':
            # Apply pending fade in (if any) and store clip
            fade_in = pending_fade_in
            pending_fade_in = None  # Reset after applying

            clips_to_render.append((cmd.segment_id, current_camera, cmd.comment,
                                   current_cut_before, current_cut_after, fade_in, None,
                                   cmd.slice_start, cmd.slice_end, current_volume,
                                   pending_shorten_join))
            pending_shorten_join = None

    print(f"\nTotal clips to render: {len(clips_to_render)}\n")

    clips_to_render = apply_shorten_join_clip_bounds(clips_to_render)

    # Apply skip/limit for testing (as late as possible in pipeline)
    if skip_clips > 0 or limit_clips is not None:
        original_count = len(clips_to_render)
        start_idx = skip_clips
        end_idx = start_idx + limit_clips if limit_clips is not None else len(clips_to_render)
        clips_to_render = clips_to_render[start_idx:end_idx]
        print(f"Applied skip/limit: {original_count} clips -> {len(clips_to_render)} clips (skipped {skip_clips}, limited to {limit_clips if limit_clips else 'all'})\n")

    # Apply max-seconds truncation for testing (from the already-generated full DSL).
    # Important: truncate at CLIP boundaries (not grouped extractions), otherwise a single
    # giant group could exceed the requested duration by a lot.
    if max_seconds is not None:
        if max_seconds <= 0:
            raise ValueError(f"--max-seconds must be > 0 (got {max_seconds})")

        def _clip_duration_seconds(clip, *, clip_idx: int) -> float:
            segment_id, camera, _, cut_before, cut_after, _, _, slice_start, slice_end, _, *_ = clip

            if segment_id.startswith('__BLACK__'):
                duration_ms = float(segment_id.split(':')[1])
                return duration_ms / 1000.0

            info = get_clip_info(segment_id, camera, slice_start, slice_end, margin)
            before_padding = cut_before / 1000.0
            after_padding = cut_after / 1000.0

            use_opening_preroll = skip_clips == 0 and clip_idx == 0 and opening_preroll_ms is not None
            if use_opening_preroll:
                opening_lead_in = max(before_padding, opening_preroll_ms / 1000.0)
                audio_start = max(0, info['audio_start'] - opening_lead_in)
            elif skip_clips == 0 and clip_idx == 0:
                audio_start = 0.0
            else:
                audio_start = max(0, info['audio_start'] - before_padding)
            audio_end = info['audio_end'] + after_padding
            return audio_end - audio_start

        elapsed = 0.0
        kept = []
        for clip_idx, clip in enumerate(clips_to_render):
            d = _clip_duration_seconds(clip, clip_idx=clip_idx)
            kept.append(clip)
            elapsed += d
            if elapsed >= max_seconds:
                break

        original_count = len(clips_to_render)
        clips_to_render = kept
        print(
            f"Applied max-seconds: {original_count} clips -> {len(clips_to_render)} clips "
            f"(target {max_seconds:.2f}s, actual ~{elapsed:.2f}s)\n"
        )

    # Group consecutive transcript clips and preserve long pauses between them.
    # This avoids unintentionally compressing timeline silence.
    clip_groups = group_consecutive_clips(clips_to_render, max_gap=None)
    print(f"Grouped into {len(clip_groups)} extraction(s):")
    for i, group in enumerate(clip_groups):
        seg_id = group[0][0]

        # Special handling for black clips
        if seg_id.startswith('__BLACK__'):
            duration_ms = seg_id.split(':')[1]
            print(f"  Group {i+1}: BLACK ({duration_ms}ms)")
            continue

        cameras_in_group = list(set(clip[1] for clip in group))
        camera_desc = cameras_in_group[0] if len(cameras_in_group) == 1 else f"{', '.join(cameras_in_group)}"

        if len(group) == 1:
            seg_id, cam, _, _, _, _, _, _, _, _, *_ = group[0]
            print(f"  Group {i+1}: {seg_id} [{cam}]")
        else:
            seg_id_first = group[0][0]
            seg_id_last = group[-1][0]
            print(f"  Group {i+1}: {seg_id_first} - {seg_id_last} [{camera_desc}] ({len(group)} clips)")
    print()

    # Dry run mode - calculate durations without rendering
    if dry_run:
        print("Calculating durations...\n")
        total_duration = 0.0

        for i, group in enumerate(clip_groups):
            segment_id_first = group[0][0]

            # Special handling for black clips
            if segment_id_first.startswith('__BLACK__'):
                duration_ms = float(segment_id_first.split(':')[1])
                duration = duration_ms / 1000.0
                total_duration += duration
                print(f"  Clip {i+1}: BLACK - {duration:.2f}s")
                continue

            segment_ids = [clip[0] for clip in group]
            slice_starts = [clip[7] for clip in group]
            slice_ends = [clip[8] for clip in group]
            # Use first camera for timing info (audio timing is same for all cameras)
            camera = group[0][1]
            cut_before = group[0][3]
            cut_after = group[0][4]
            cameras_in_group = list(set(clip[1] for clip in group))
            camera_desc = cameras_in_group[0] if len(cameras_in_group) == 1 else f"{', '.join(cameras_in_group)}"

            # Calculate duration from transcript
            clips_info = [get_clip_info(sid, camera, slice_start, slice_end, margin)
                          for sid, slice_start, slice_end in zip(segment_ids, slice_starts, slice_ends)]
            first_clip = clips_info[0]
            last_clip = clips_info[-1]

            before_padding = cut_before / 1000.0
            after_padding = cut_after / 1000.0

            use_opening_preroll = skip_clips == 0 and i == 0 and opening_preroll_ms is not None
            episode_starts_at_timeline_zero = skip_clips == 0 and i == 0 and not use_opening_preroll
            if use_opening_preroll:
                opening_lead_in = max(before_padding, opening_preroll_ms / 1000.0)
                audio_start = max(0, first_clip['audio_start'] - opening_lead_in)
            elif episode_starts_at_timeline_zero:
                audio_start = 0.0
            else:
                audio_start = max(0, first_clip['audio_start'] - before_padding)
            audio_end = last_clip['audio_end'] + after_padding
            duration = audio_end - audio_start

            total_duration += duration

            if len(group) == 1:
                seg_id = segment_ids[0]
                print(f"  Clip {i+1}: {seg_id} [{camera_desc}] - {duration:.2f}s")
            else:
                print(f"  Clip {i+1}: {segment_ids[0]} - {segment_ids[-1]} [{camera_desc}] - {duration:.2f}s ({len(group)} segments)")

        print(f"\n{'='*70}")
        print(f"TOTAL DURATION: {total_duration:.2f}s ({total_duration/60:.2f} minutes)")
        print(f"{'='*70}\n")
        return

    # Prepare debug directory if needed
    debug_dir = None
    if debug:
        debug_dir = os.path.join(os.path.dirname(output_file), 'debug_segments')
        os.makedirs(debug_dir, exist_ok=True)
        print(f"Debug mode: segments will be saved to {debug_dir}\n")

    # Extract and combine clips using parallel processing
    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"Rendering {len(clip_groups)} segments in parallel (up to {num_workers} workers)...\n")

        # Prepare arguments for parallel processing
        # Sort groups by size (descending) for better load balancing
        groups_with_metadata = []
        for i, group in enumerate(clip_groups):
            segment_file = os.path.join(tmpdir, f'segment_{i:04d}.mp4')
            group_size = len(group)
            groups_with_metadata.append((group, segment_file, i, group_size))

        # Sort by size descending (largest first) for better load balancing
        groups_with_metadata.sort(key=lambda x: x[3], reverse=True)

        render_args = []
        for group, segment_file, original_idx, size in groups_with_metadata:
            render_args.append(
                (
                    group,
                    segment_file,
                    original_idx,
                    len(clip_groups),
                    margin,
                    skip_clips,
                    opening_preroll_ms / 1000.0 if opening_preroll_ms is not None else None,
                ),
            )

        # Render segments in parallel
        actual_workers = min(num_workers, len(clip_groups), cpu_count())

        if len(clip_groups) == 1:
            # Single segment - no need for parallelization
            segment_file, duration = _render_segment_wrapper(render_args[0])
            results = [(segment_file, duration, 0)]
        else:
            # Multiple segments - use parallel processing
            # Start largest segments first for better load balancing
            with Pool(processes=actual_workers) as pool:
                results_unsorted = pool.map(_render_segment_wrapper, render_args)

            # Results are in sorted order (by size), need to restore original order
            # Each result is (segment_file, duration) and render_args[i][2] is original_idx
            results = []
            for i, (segment_file, duration) in enumerate(results_unsorted):
                original_idx = render_args[i][2]
                results.append((segment_file, duration, original_idx))

            # Sort back to original order for concatenation
            results.sort(key=lambda x: x[2])

        # Collect results in original order
        combined_clips = [segment_file for segment_file, duration, _ in results]

        print(f"\nAll segments rendered successfully!\n")

        # Save debug copies if requested
        if debug:
            print("Saving debug copies of segments...")
            for i, segment_file in enumerate(combined_clips):
                debug_file = os.path.join(debug_dir, f'segment_{i:04d}.mp4')
                shutil.copy2(segment_file, debug_file)
                print(f"  Saved: {debug_file}")
            print()

        # Concatenate segments
        # Use MP4 with AAC audio throughout
        temp_video_file = output_file + '.concat.mp4'

        if len(combined_clips) > 1:
            final_reencode = _final_concat_use_reencode()
            if final_reencode:
                print(
                    f"Concatenating {len(combined_clips)} segments via concat demuxer + re-encode "
                    f"(muxed A/V per segment, NLE-safe)..."
                )
            else:
                print(f"Concatenating {len(combined_clips)} segments using stream copy...")
            concatenate_clips(
                combined_clips,
                temp_video_file,
                muxed_reencode=final_reencode,
            )
        else:
            # Single segment - just copy to output
            print("Single segment - copying to output...")
            shutil.copy2(combined_clips[0], temp_video_file)

        # Track the current working file through the pipeline
        current_file = temp_video_file
        files_to_cleanup = []

        # Note: Volume is now applied per-extraction, not as post-processing

        # Apply audio overlays if any
        if audio_overlays:
            # Calculate timeline positions for audio overlays at the CLIP level
            # We need to map clip indices to actual timeline positions

            cumulative_durations = [0.0]  # Start at 0
            for clip_i, (segment_id, camera, comment, cut_before, cut_after, fade_in, fade_out, slice_start, slice_end, volume, *_) in enumerate(clips_to_render):
                # Calculate duration for this individual clip
                if segment_id.startswith('__BLACK__'):
                    # Black clip
                    duration_ms = float(segment_id.split(':')[1])
                    duration = duration_ms / 1000.0
                else:
                    # Regular clip - calculate from transcript timing
                    clip_info = get_clip_info(segment_id, camera, slice_start, slice_end, margin)
                    before_padding = cut_before / 1000.0
                    after_padding = cut_after / 1000.0
                    if clip_i == 0 and skip_clips == 0:
                        audio_start = 0.0
                    else:
                        audio_start = max(0, clip_info['audio_start'] - before_padding)
                    audio_end = clip_info['audio_end'] + after_padding
                    duration = audio_end - audio_start

                cumulative_durations.append(cumulative_durations[-1] + duration)

            # Convert audio overlays from (clip_index, file, volume, speed) to (timestamp, file, volume, speed)
            audio_overlays_with_timestamps = []
            for clip_idx, audio_file, volume, speed in audio_overlays:
                if clip_idx < len(cumulative_durations):
                    timestamp = cumulative_durations[clip_idx]
                    audio_overlays_with_timestamps.append((timestamp, audio_file, volume, speed))
                else:
                    print(f"Warning: Audio overlay index {clip_idx} out of range (max {len(cumulative_durations)-1}), skipping", file=sys.stderr)

            # Apply the overlays to a new temp file
            overlays_output = output_file + '.overlays.mp4'
            apply_audio_overlays(current_file, overlays_output, audio_overlays_with_timestamps)

            # Mark previous file for cleanup and update current file
            files_to_cleanup.append(current_file)
            current_file = overlays_output

        # Move final result to intermediate file
        intermediate_file = current_file
        if current_file != output_file:
            # Already have the intermediate file
            pass
        else:
            # Rename to intermediate
            intermediate_file = output_file + '.intermediate.mp4'
            shutil.move(current_file, intermediate_file)

        # Final conversion to AAC/MP4
        print(f"\nConverting to final AAC/MP4 format...")
        final_output_tmp = output_file + '.finalizing.mp4'
        if os.path.exists(final_output_tmp):
            try:
                os.unlink(final_output_tmp)
            except PermissionError:
                print(
                    f"Warning: could not delete existing temp file (in use): {final_output_tmp}",
                    file=sys.stderr,
                )
        cmd = _ffmpeg_cmd_base() + [
            '-i', intermediate_file,
            '-map', '0:v:0',
            '-map', '0:a:0?',
            '-dn',
            '-sn',
            # Intermediates may be H.264 4:4:4 / yuv444p depending on source filters.
            # Re-encode here for broad player compatibility (Windows Movies & TV, etc.).
            '-profile:v', 'high',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            '-c:a', 'aac',
            '-b:a', '320k',  # High bitrate for quality
            '-aac_coder', 'twoloop',  # Better quality AAC encoding
        ]
        try:
            _run_ffmpeg_h264_encode_with_fallback(
                cmd,
                final_output_tmp,
                stage_default_preset='fast',
                quality_level=18,
                use_cache=False,
                err_context='final AAC/MP4 conversion',
            )
        except RuntimeError as exc:
            raise RuntimeError(
                'Final AAC/MP4 conversion failed.\n'
                f'Input: {intermediate_file}\n'
                f'Output: {final_output_tmp}'
            ) from exc

        try:
            _validate_video_stream(final_output_tmp)
        except Exception as final_exc:
            print(
                "Warning: final compatibility transcode produced an invalid video stream; "
                "falling back to the validated intermediate file.",
                file=sys.stderr,
            )
            _validate_video_stream(intermediate_file)
            if os.path.exists(output_file):
                os.unlink(output_file)
            shutil.copy2(intermediate_file, output_file)
        else:
            if os.path.exists(output_file):
                os.unlink(output_file)
            shutil.move(final_output_tmp, output_file)
        finally:
            if os.path.exists(final_output_tmp):
                try:
                    os.unlink(final_output_tmp)
                except PermissionError:
                    print(
                        f"Warning: could not delete temp file (in use): {final_output_tmp}",
                        file=sys.stderr,
                    )

        # Clean up intermediate file
        if os.path.exists(intermediate_file):
            os.unlink(intermediate_file)

        # Clean up temporary files
        for temp_file in files_to_cleanup:
            if os.path.exists(temp_file):
                os.unlink(temp_file)

    print(f"\n{'='*70}")
    print(f"Done! Output: {output_file}")
    print(f"{'='*70}\n")
