#!/usr/bin/env python3
"""
CLI entry point for podcast DSL renderer.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List

from .video_renderer import (
    DEFAULT_VIDEO_ENCODER,
    VIDEO_DOWNSCALE_4K_ENV,
    VIDEO_ENCODER_ENV,
    VIDEO_HARDWARE_ENCODE_ENV,
    VIDEO_PRESET_ENV,
    render_dsl,
)


def main():
    parser = argparse.ArgumentParser(
        description='Render podcast videos using DSL',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Render from file
  python -m podcast_dsl segment_2_test.dsl -o output.mp4

  # Render from stdin
  cat segment_2_test.dsl | python -m podcast_dsl -o output.mp4
  echo '$segment2/0' | python -m podcast_dsl - -o output.mp4

  # Dry run (calculate duration without rendering)
  python -m podcast_dsl segment_2_test.dsl --dry-run

  # Auto-generate camera cuts based on speaker
  python -m podcast_dsl segment_2_test.dsl --auto-cuts -o output.mp4

  # Test rendering from middle (skip 10 clips, render next 5 clips)
  python -m podcast_dsl segment_2_test.dsl --skip 10 --limit 5 -o test.mp4

  # After a full render, also produce single-camera variants (sequential)
  # - interview DSLs: Ben/Guest/Wide
  # - reading DSLs: Front/Side
  python -m podcast_dsl interview.dsl -o outputs/Full Interview.mp4 --massive
        """
    )
    parser.add_argument('dsl_file', nargs='?', default='-',
                        help='DSL file to render (use "-" or omit for stdin)')
    parser.add_argument('--output', '-o', default='../outputs/dsl_output.mp4',
                        help='Output video file')
    parser.add_argument('--dry-run', action='store_true',
                        help='Calculate total duration without rendering')
    parser.add_argument('--auto-cuts', action='store_true',
                        help='Auto-insert cameras: Ben open, intro/crosstalk wide, 5s min / random wide / <1s hold')
    parser.add_argument('--auto-cuts-legacy', action='store_true',
                        help='Use older auto-cuts (random wide, minimum 5s per angle, short-hold)')
    parser.add_argument('--skip', type=int, default=0,
                        help='Skip first N clips (for testing)')
    parser.add_argument('--limit', type=int, default=None,
                        help='Render only M clips after skipping (for testing)')
    parser.add_argument('--max-seconds', type=float, default=None,
                        help='Render only the first N seconds of timeline (for testing)')
    parser.add_argument('--debug', action='store_true',
                        help='Save intermediate segment files to debug_segments/ folder for inspection')
    parser.add_argument('--workers', type=int, default=8,
                        help='Number of parallel workers for rendering (default: 8)')
    parser.add_argument('--render-all-cams', action='store_true',
                        help='Render separate output files for each camera feed instead of cutting between cameras')
    parser.add_argument('--margin', type=float, default=0.0,
                        help='Extra margin in seconds to add when slicing clips (default: 0.0)')
    parser.add_argument(
        '--video-encoder',
        choices=['auto', 'libx264', 'h264_nvenc', 'h264_qsv', 'h264_amf'],
        default=DEFAULT_VIDEO_ENCODER,
        help=(
            'Video encoder for re-encoded stages (default: libx264 name, with hardware '
            'auto-pick when --hardware-encode is on). '
            'Use --no-hardware-encode for software-only libx264. '
            '"auto" is legacy: same hardware auto-selection as --hardware-encode.'
        ),
    )
    parser.add_argument(
        '--hardware-encode',
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            'When --video-encoder is libx264, try NVENC / QSV / AMF in order first '
            '(default: on), falling back to libx264 if none work or if a hardware encode '
            'fails mid-render. Use --no-hardware-encode for software-only. '
            'Does not change the first encoder when you already pass a hardware '
            '--video-encoder, but libx264 fallback still runs if that hardware encode fails.'
        ),
    )
    parser.add_argument(
        '--video-preset',
        default=None,
        help='Optional preset override for the selected video encoder.',
    )
    parser.add_argument(
        '--downscale-4k-to-1080p',
        action='store_true',
        help='Downscale targets above 1080p to 1920x1080 for faster renders.',
    )
    parser.add_argument(
        '--segments-file',
        type=Path,
        default=None,
        help='Per-episode segments JSON (Temp/segments.json). Also honored via PODCAST_DSL_SEGMENTS_FILE.',
    )
    parser.add_argument(
        '--massive',
        action='store_true',
        help=(
            'After this render completes, run massive_renderer.py to write same-timeline '
            'single-camera variants into the same directory as -o. Interview DSLs produce '
            'Ben/Guest/Wide renders; reading DSLs produce Front/Side renders. '
            'Requires a real DSL file path; not compatible with stdin, --render-all-cams, or '
            '--skip/--limit/--max-seconds.'
        ),
    )

    args = parser.parse_args()

    if args.auto_cuts and args.auto_cuts_legacy:
        parser.error('Use only one of --auto-cuts and --auto-cuts-legacy')

    if args.massive:
        if args.dsl_file == '-':
            parser.error('--massive requires a DSL file path (stdin is not supported)')
        if args.render_all_cams:
            parser.error('--massive is not compatible with --render-all-cams')
        if args.skip > 0 or args.limit is not None or args.max_seconds is not None:
            parser.error(
                '--massive is not compatible with --skip, --limit, or --max-seconds '
                '(variants always use the full DSL on disk; run massive_renderer.py separately for partial tests)'
            )

    os.environ[VIDEO_ENCODER_ENV] = args.video_encoder
    # Always set explicitly so --no-hardware-encode is not treated as "env unset → default on".
    os.environ[VIDEO_HARDWARE_ENCODE_ENV] = '1' if args.hardware_encode else '0'
    if args.video_preset:
        os.environ[VIDEO_PRESET_ENV] = args.video_preset
    else:
        os.environ.pop(VIDEO_PRESET_ENV, None)
    if args.downscale_4k_to_1080p:
        os.environ[VIDEO_DOWNSCALE_4K_ENV] = '1'
    else:
        os.environ.pop(VIDEO_DOWNSCALE_4K_ENV, None)

    from .config import SEGMENTS_FILE_ENV, load_segments_overlay

    segments_path = args.segments_file or os.environ.get(SEGMENTS_FILE_ENV)
    if segments_path:
        load_segments_overlay(segments_path)
        os.environ[SEGMENTS_FILE_ENV] = str(Path(segments_path).resolve())

    # Render all cams mode takes precedence
    if args.render_all_cams:
        from .video_renderer import render_all_cams
        render_all_cams(args.dsl_file, args.output, dry_run=args.dry_run,
                       skip_clips=args.skip, limit_clips=args.limit, max_seconds=args.max_seconds, debug=args.debug,
                       num_workers=args.workers, margin=args.margin)
    else:
        def _do_render():
            render_dsl(
                args.dsl_file,
                args.output,
                dry_run=args.dry_run,
                auto_cuts=args.auto_cuts or args.auto_cuts_legacy,
                auto_cuts_legacy=args.auto_cuts_legacy,
                skip_clips=args.skip,
                limit_clips=args.limit,
                max_seconds=args.max_seconds,
                debug=args.debug,
                num_workers=args.workers,
                margin=args.margin,
            )

        try:
            _do_render()
        except BaseException as exc:
            # Default behavior: loudly surface crashes, then try one clean rerender from scratch.
            #
            # This is intentionally conservative (only one retry) to avoid infinite loops for
            # deterministic failures (bad input media, bad DSL, etc.).
            print(
                "\nERROR: Render crashed.\n"
                f"  Output target: {args.output}\n"
                f"  Exception: {type(exc).__name__}: {exc}\n"
                "\nRetrying once from scratch (this is automatic default behavior).\n",
                file=sys.stderr,
            )

            if not args.dry_run:
                out_path = Path(args.output)
                # Try to remove partial outputs so the retry is a true from-scratch render.
                for suffix in ('.finalizing.mp4', '.intermediate.mp4', '.concat.mp4'):
                    try:
                        p = Path(str(out_path) + suffix)
                        if p.exists():
                            p.unlink()
                    except Exception:
                        pass
                try:
                    if out_path.exists():
                        out_path.unlink()
                except Exception:
                    pass

            time.sleep(2.0)
            _do_render()

    if args.massive:
        repo_root = Path(__file__).resolve().parents[2]
        massive_script = repo_root / 'massive_renderer.py'
        if not massive_script.is_file():
            print(f'Error: {massive_script} not found; cannot run --massive.', file=sys.stderr)
            raise SystemExit(2)

        dsl_path = Path(args.dsl_file).resolve()
        out_path = Path(args.output).resolve()
        out_dir = out_path.parent

        cmd: List[str] = [
            sys.executable,
            str(massive_script),
            str(dsl_path),
            '--output-dir',
            str(out_dir),
            '--artifacts-dir',
            str(dsl_path.parent),
            '--repo-src',
            str(repo_root / 'src'),
            '--workers',
            str(args.workers),
        ]
        if args.downscale_4k_to_1080p:
            cmd.append('--downscale-4k-to-1080p')
        if args.video_encoder:
            cmd.extend(['--video-encoder', args.video_encoder])
        if args.hardware_encode:
            cmd.append('--hardware-encode')
        else:
            cmd.append('--no-hardware-encode')
        if args.video_preset:
            cmd.extend(['--video-preset', args.video_preset])
        if args.dry_run:
            cmd.append('--dry-run')

        print(
            '\n--massive: generating single-camera DSLs and rendering sequentially '
            '(interview: Ben/Guest/Wide; reading: Front/Side)...\n'
        )
        result = subprocess.run(cmd, cwd=str(repo_root), check=False)
        if result.returncode != 0:
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
