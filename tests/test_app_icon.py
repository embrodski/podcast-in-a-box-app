"""App icon assets and Windows identity helpers."""

from __future__ import annotations

import struct
import unittest

from app.controller.paths import APP_ICON_ICO, APP_USER_MODEL_ID
from app.gui.app_icon import apply_process_app_user_model_id


class AppIconTests(unittest.TestCase):
    def test_ico_exists_with_expected_faces(self) -> None:
        data = APP_ICON_ICO.read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual(reserved, 0)
        self.assertEqual(kind, 1)
        self.assertEqual(count, 8)
        sizes = []
        offset = 6
        for _ in range(count):
            width, height, _colors, _resv, _planes, bits, nbytes, imgoff = struct.unpack_from(
                "<BBBBHHII", data, offset
            )
            sizes.append(width or 256)
            self.assertEqual(height or 256, width or 256)
            self.assertEqual(bits, 32)
            self.assertGreater(nbytes, 0)
            self.assertEqual(data[imgoff : imgoff + 4], b"\x89PNG")
            offset += 16
        self.assertEqual(sizes, [16, 20, 24, 32, 48, 64, 128, 256])

    def test_app_user_model_id_is_stable(self) -> None:
        self.assertEqual(APP_USER_MODEL_ID, "Lighthaven.PodcastInABox")
        apply_process_app_user_model_id()
