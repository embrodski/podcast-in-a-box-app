#!/usr/bin/env python3
"""Crop the PIAB source PNG to a square and write a multi-size Windows .ico."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "assets"
DEFAULT_SOURCE = ASSETS_DIR / "PIAB Icon.png"
DEFAULT_ICO = ASSETS_DIR / "piab.ico"
ICON_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def _ensure_gui_app() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv[:1])
    return app


def center_square_crop(image: QImage) -> QImage:
    side = min(image.width(), image.height())
    x = (image.width() - side) // 2
    y = (image.height() - side) // 2
    return image.copy(x, y, side, side)


def scale_icon(image: QImage, size: int) -> QImage:
    current = image
    while current.width() > size * 2:
        next_size = max(size, current.width() // 2)
        current = current.scaled(
            next_size,
            next_size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return current.scaled(
        size,
        size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def qimage_png_bytes(image: QImage) -> bytes:
    blob = QByteArray()
    buffer = QBuffer(blob)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Failed to encode PNG icon face.")
    return bytes(blob)


def write_png_ico(path: Path, faces: dict[int, bytes]) -> None:
    sizes = sorted(faces)
    offset = 6 + 16 * len(sizes)
    entries = bytearray()
    payload = bytearray()
    for size in sizes:
        data = faces[size]
        stored = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", stored, stored, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    path.write_bytes(header + entries + payload)


def build_app_icon(
    *,
    source: Path = DEFAULT_SOURCE,
    dest: Path = DEFAULT_ICO,
    preview_dir: Path | None = None,
) -> dict[str, Path]:
    _ensure_gui_app()
    image = QImage(str(source))
    if image.isNull():
        raise FileNotFoundError(f"Could not load icon source: {source}")
    square = center_square_crop(image.convertToFormat(QImage.Format.Format_ARGB32))
    faces = {size: qimage_png_bytes(scale_icon(square, size)) for size in ICON_SIZES}
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_png_ico(dest, faces)
    written = {"ico": dest}
    if preview_dir is not None:
        preview_dir.mkdir(parents=True, exist_ok=True)
        for size in (16, 256):
            preview = preview_dir / f"piab-icon-{size}.png"
            preview.write_bytes(faces[size])
            written[f"preview_{size}"] = preview
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dest", type=Path, default=DEFAULT_ICO)
    parser.add_argument(
        "--preview-dir",
        type=Path,
        default=None,
        help="Optional folder for 16px and 256px PNG previews.",
    )
    args = parser.parse_args(argv)
    written = build_app_icon(
        source=args.source,
        dest=args.dest,
        preview_dir=args.preview_dir,
    )
    for label, path in written.items():
        print(f"{label}: {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
