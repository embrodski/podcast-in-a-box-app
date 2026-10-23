#!/usr/bin/env python3
"""Build a local HTML contact sheet of middle frames from MultiCorder dumps."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path


DEFAULT_PREFIX = "MultiCorder3 - DeckLink Quad HDMI Recorder (3) 3"
MIN_DURATION_SEC = 300.0
THUMB_WIDTH = 480


def name_regex(prefix: str) -> re.Pattern[str]:
    return re.compile(
        rf"^{re.escape(prefix)} - "
        r"(?P<day>\d{2}) (?P<month>[A-Za-z]+) (?P<year>\d{4}) - "
        r"(?P<time>.+)\.mp4$",
        re.IGNORECASE,
    )


def _which(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"{name} was not found on PATH.")


def parse_name_datetime(name: str, pattern: re.Pattern[str]) -> datetime | None:
    match = pattern.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(
            f"{match.group('day')} {match.group('month')} {match.group('year')} "
            f"{match.group('time')}",
            "%d %B %Y %I-%M-%S %p",
        )
    except ValueError:
        return None


def parse_name_date(name: str, pattern: re.Pattern[str]) -> date | None:
    parsed = parse_name_datetime(name, pattern)
    return parsed.date() if parsed else None


def probe_duration(ffprobe: str, path: Path) -> float:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    text = (result.stdout or "").strip()
    if not text or text.upper() == "N/A":
        raise RuntimeError("ffprobe returned no duration")
    return float(text)


def extract_mid_frame(ffmpeg: str, src: Path, dest: Path, duration: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    mid = max(0.0, duration / 2.0)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{mid:.3f}",
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-vf",
        f"scale={THUMB_WIDTH}:-2",
        "-q:v",
        "5",
        "-y",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg frame grab failed")


def fmt_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def fmt_size(nbytes: int) -> str:
    gb = nbytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.2f} GB"
    mb = nbytes / (1024 ** 2)
    return f"{mb:.1f} MB"


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_html(
    path: Path,
    items: list[dict],
    skipped_short: int,
    failed: list[dict],
    title: str,
) -> None:
    by_date: dict[str, list[dict]] = {}
    for item in items:
        by_date.setdefault(item["date"], []).append(item)

    parts = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head>",
        "<meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title>",
        "<style>",
        "body { font-family: Segoe UI, sans-serif; background: #111; color: #eee;",
        "  margin: 24px auto; max-width: 720px; }",
        "h1 { font-size: 22px; }",
        "h2 { margin-top: 36px; color: #9cf; border-bottom: 1px solid #333;",
        "  padding-bottom: 6px; }",
        ".meta { color: #aaa; }",
        ".item { margin: 0 0 28px; padding-bottom: 20px; border-bottom: 1px solid #2a2a2a; }",
        "img { width: 480px; max-width: 100%; height: auto; display: block;",
        "  background: #000; }",
        ".name { font-family: Consolas, monospace; font-size: 13px;",
        "  margin: 10px 0 6px; user-select: all; word-break: break-all; }",
        "button { margin-right: 8px; }",
        ".fail { color: #f88; }",
        "</style>",
        "<script>",
        "function copyName(id) {",
        "  const el = document.getElementById(id);",
        "  navigator.clipboard.writeText(el.dataset.name);",
        "}",
        "</script>",
        "</head>",
        "<body>",
        f"<h1>{html.escape(title)}</h1>",
        (
            f"<p class='meta'>{len(items)} clips ≥ 5 minutes"
            f" (skipped {skipped_short} shorter)."
            f" Approximate midpoint, 480px wide."
            f" Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}.</p>"
        ),
    ]
    for day in sorted(by_date.keys(), reverse=True):
        parts.append(f"<h2>{html.escape(day)}</h2>")
        for index, item in enumerate(by_date[day]):
            uid = f"n-{day}-{index}"
            parts.extend(
                [
                    "<div class='item'>",
                    (
                        f"<img src='{html.escape(item['thumb'])}' "
                        f"alt='{html.escape(item['name'])}'>"
                    ),
                    (
                        f"<div class='name' id='{uid}' "
                        f"data-name='{html.escape(item['name'])}'>"
                        f"{html.escape(item['name'])}</div>"
                    ),
                    (
                        f"<div class='meta'>{html.escape(item['duration'])}"
                        f" · {html.escape(item['size'])}</div>"
                    ),
                    f"<button type='button' onclick=\"copyName('{uid}')\">Copy filename</button>",
                    "</div>",
                ]
            )
    if failed:
        parts.append("<h2>Failed</h2>")
        for item in failed:
            parts.append(
                "<p class='fail'>"
                f"{html.escape(item['name'])}: {html.escape(item['error'])}</p>"
            )
    parts.extend(["</body>", "</html>", ""])
    path.write_text("\n".join(parts), encoding="utf-8")


def collect_candidates(root: Path, cutoff: date, pattern: re.Pattern[str]) -> list[Path]:
    found: list[Path] = []
    for path in root.iterdir():
        if not path.is_file() or path.suffix.lower() != ".mp4":
            continue
        parsed = parse_name_date(path.name, pattern)
        if parsed is None or parsed < cutoff:
            continue
        found.append(path)
    found.sort(
        key=lambda p: parse_name_datetime(p.name, pattern) or datetime.min,
        reverse=True,
    )
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(r"D:\PodcastRoom"))
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(r"E:\PodcastRoom\PodcastInABox\Temp\multicorder3-mids"),
    )
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--title", default="")
    parser.add_argument("--cutoff", type=date.fromisoformat, default=date(2026, 5, 13))
    parser.add_argument("--min-duration", type=float, default=MIN_DURATION_SEC)
    parser.add_argument("--warmup-sec", type=float, default=0.0)
    args = parser.parse_args()

    ffmpeg = _which("ffmpeg")
    ffprobe = _which("ffprobe")
    pattern = name_regex(args.prefix)
    title = args.title or f"{args.prefix.split(' - ', 1)[0]} middle frames"
    out_dir = args.out
    thumbs_dir = out_dir / "thumbs"
    out_dir.mkdir(parents=True, exist_ok=True)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    print(f"Pinging {args.root} ...", flush=True)
    if not args.root.is_dir():
        print(f"ERROR: scan root not found: {args.root}", file=sys.stderr)
        return 1
    _ = list(args.root.iterdir())
    if args.warmup_sec > 0:
        print(f"Drive warmup {args.warmup_sec:.0f}s ...", flush=True)
        time.sleep(args.warmup_sec)

    candidates = collect_candidates(args.root, args.cutoff, pattern)
    print(f"Name/date matches since {args.cutoff.isoformat()}: {len(candidates)}", flush=True)

    items: list[dict] = []
    failed: list[dict] = []
    skipped_short = 0
    html_path = out_dir / "index.html"
    manifest_path = out_dir / "manifest.json"

    for index, src in enumerate(candidates, start=1):
        label = f"[{index}/{len(candidates)}]"
        try:
            duration = probe_duration(ffprobe, src)
        except Exception as exc:
            print(f"{label} FAIL probe {src.name}: {exc}", flush=True)
            failed.append({"name": src.name, "error": f"probe: {exc}"})
            continue
        if duration < args.min_duration:
            skipped_short += 1
            print(
                f"{label} SKIP short {fmt_duration(duration)} {src.name}",
                flush=True,
            )
            continue

        thumb_name = src.stem + ".jpg"
        thumb_path = thumbs_dir / thumb_name
        try:
            if not (thumb_path.is_file() and thumb_path.stat().st_size > 0):
                print(
                    f"{label} GRAB mid={fmt_duration(duration / 2)} {src.name}",
                    flush=True,
                )
                extract_mid_frame(ffmpeg, src, thumb_path, duration)
            else:
                print(f"{label} HAVE {src.name}", flush=True)
        except Exception as exc:
            print(f"{label} FAIL grab {src.name}: {exc}", flush=True)
            failed.append({"name": src.name, "error": f"grab: {exc}"})
            continue

        items.append(
            {
                "name": src.name,
                "date": parse_name_date(src.name, pattern).isoformat(),
                "duration_sec": duration,
                "duration": fmt_duration(duration),
                "size": fmt_size(src.stat().st_size),
                "thumb": f"thumbs/{thumb_name}",
            }
        )
        write_html(html_path, items, skipped_short, failed, title)
        write_json(
            manifest_path,
            {
                "kept": len(items),
                "skipped_short": skipped_short,
                "failed": failed,
                "items": items,
            },
        )

    write_html(html_path, items, skipped_short, failed, title)
    write_json(
        manifest_path,
        {
            "kept": len(items),
            "skipped_short": skipped_short,
            "failed": failed,
            "items": items,
        },
    )
    print(
        f"DONE kept={len(items)} skipped_short={skipped_short} failed={len(failed)}",
        flush=True,
    )
    print(f"HTML {html_path}", flush=True)
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
