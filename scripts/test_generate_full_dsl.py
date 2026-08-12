#!/usr/bin/env python3
"""Regression tests for interview DSL generation."""

from pathlib import Path
import sys
import tempfile
import unittest
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generate_full_dsl import (
    Row,
    WordToken,
    _apply_end_phrase,
    _apply_start_phrase,
    _apply_start_phrase_countdown,
    _find_latest_end_phrase_match,
    _find_wide_spans,
    _intended_camera,
    _row_segment_line,
    _start_phrase_exists,
)


class GenerateFullDslTests(unittest.TestCase):
    def test_final_row_gets_default_two_second_tail(self) -> None:
        row = Row(
            idx=12,
            start=10.0,
            end=11.5,
            text="Closing line",
            speaker_id=1,
            speaker_name="Guest",
        )

        line = _row_segment_line(
            row,
            "17",
            include_fallback_speaker=True,
            is_last=True,
            final_shot_tail_sec=2.0,
        )

        self.assertEqual(
            line,
            "$segment17/12 slice(:3.500) // Guest: Closing line",
        )

    def test_non_final_row_is_unmodified(self) -> None:
        row = Row(
            idx=11,
            start=8.0,
            end=9.0,
            text="Penultimate line",
            speaker_id=0,
            speaker_name="",
        )

        line = _row_segment_line(
            row,
            "17",
            include_fallback_speaker=True,
            is_last=False,
            final_shot_tail_sec=2.0,
        )

        self.assertEqual(line, "$segment17/11 // Speaker 0: Penultimate line")

    def test_dense_cut_wide_rule_still_applies_with_final_tail_change(self) -> None:
        rows = [
            Row(idx=0, start=0.0, end=1.0, text="a", speaker_id=0, speaker_name=""),
            Row(idx=1, start=1.0, end=2.0, text="b", speaker_id=1, speaker_name=""),
            Row(idx=2, start=2.0, end=3.0, text="c", speaker_id=0, speaker_name=""),
            Row(idx=3, start=7.5, end=8.5, text="d", speaker_id=1, speaker_name=""),
        ]

        spans = _find_wide_spans(
            rows,
            _intended_camera(rows),
            window_sec=3.0,
            min_wide_sec=3.0,
        )

        self.assertEqual(spans, [(1, 3)])


def _words(*pairs: tuple[str, float, float]) -> tuple[WordToken, ...]:
    return tuple(WordToken(text=t, start=s, end=e) for t, s, e in pairs)


class StartEndPhraseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            Row(
                idx=0,
                start=0.0,
                end=5.0,
                text="Setup chatter before we begin.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Setup", 0.0, 0.4),
                    ("chatter", 0.5, 1.0),
                    ("before", 1.1, 1.4),
                    ("we", 1.5, 1.6),
                    ("begin.", 1.7, 2.1),
                ),
            ),
            Row(
                idx=1,
                start=10.0,
                end=14.0,
                text="Hut of brown, now sit down. Jolly!",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Hut", 10.0, 10.2),
                    ("of", 10.3, 10.4),
                    ("brown,", 10.5, 10.8),
                    ("now", 11.0, 11.2),
                    ("sit", 11.3, 11.5),
                    ("down.", 11.6, 12.0),
                    ("Jolly!", 12.5, 13.0),
                ),
            ),
            Row(
                idx=2,
                start=20.0,
                end=24.0,
                text="That was fun. Hut of brown, now stand up. Goodbye.",
                speaker_id=1,
                speaker_name="Guest",
                words=_words(
                    ("That", 20.0, 20.3),
                    ("was", 20.4, 20.6),
                    ("fun.", 20.7, 21.0),
                    ("Hut", 21.2, 21.4),
                    ("of", 21.5, 21.6),
                    ("brown,", 21.7, 22.0),
                    ("now", 22.1, 22.3),
                    ("stand", 22.4, 22.7),
                    ("up.", 22.8, 23.0),
                    ("Goodbye.", 23.2, 23.8),
                ),
            ),
        ]

    def test_start_phrase_cuts_one_second_before_next_word(self) -> None:
        cut = _apply_start_phrase(
            self.rows,
            "Hut of brown, now sit down.",
            preroll_sec=1.0,
        )
        self.assertEqual([r.idx for r in cut.rows], [1, 2])
        self.assertEqual(cut.next_word_text, "jolly")
        self.assertAlmostEqual(cut.content_start_abs, 12.5)
        # Jolly starts 2.5s into row 1 -> slice_start 2.5; !opening supplies the 1s preroll.
        self.assertAlmostEqual(cut.first_slice_start or -1.0, 2.5)

    def test_start_phrase_ignores_case_and_punctuation(self) -> None:
        cut = _apply_start_phrase(
            self.rows,
            "hut of brown now sit down",
            preroll_sec=1.0,
        )
        self.assertEqual(cut.next_word_text, "jolly")
        self.assertEqual(cut.host_speaker_id, 0)

    _TRIGGER = "I solemnly swear I'm up to no good"
    _COUNTDOWN = ["five", "four", "three", "two"]
    _COUNTDOWN_SUFFIX = ["one", "zero"]

    def _oath_countdown_rows(self, *, oath_words, countdown_words, next_word) -> list[Row]:
        words = list(oath_words) + list(countdown_words) + [next_word]
        return [
            Row(
                idx=0,
                start=10.0,
                end=20.0,
                text=" ".join(w[0] for w in words),
                speaker_id=0,
                speaker_name="Host",
                words=_words(*words),
            )
        ]

    def test_start_phrase_countdown_skips_missing_numbers(self) -> None:
        rows = self._oath_countdown_rows(
            oath_words=[
                ("I", 10.0, 10.1),
                ("solemnly", 10.2, 10.5),
                ("swear", 10.6, 10.9),
                ("I", 11.0, 11.1),
                ("am", 11.2, 11.3),
                ("up", 11.4, 11.5),
                ("to", 11.6, 11.7),
                ("no", 11.8, 11.9),
                ("good,", 12.0, 12.3),
                ("in", 12.4, 12.5),
            ],
            countdown_words=[
                ("five", 12.6, 12.8),
                ("three", 12.9, 13.1),
                ("two", 13.2, 13.4),
            ],
            next_word=("Perfect.", 13.8, 14.2),
        )
        cut = _apply_start_phrase_countdown(
            rows,
            self._TRIGGER,
            countdown_tokens=self._COUNTDOWN,
            countdown_suffix_tokens=self._COUNTDOWN_SUFFIX,
            preroll_sec=1.0,
        )
        self.assertEqual(cut.next_word_text, "perfect")
        self.assertIn("five", cut.matched_phrase)
        self.assertIn("two", cut.matched_phrase)
        self.assertNotIn("four", cut.matched_phrase)

    def test_start_phrase_countdown_includes_one_zero_when_spoken(self) -> None:
        rows = self._oath_countdown_rows(
            oath_words=[
                ("I", 10.0, 10.1),
                ("solemnly", 10.2, 10.5),
                ("swear", 10.6, 10.9),
                ("I'm", 11.0, 11.2),
                ("up", 11.4, 11.5),
                ("to", 11.6, 11.7),
                ("no", 11.8, 11.9),
                ("good,", 12.0, 12.3),
                ("in", 12.4, 12.5),
            ],
            countdown_words=[
                ("five", 12.6, 12.8),
                ("four", 12.9, 13.0),
                ("three", 13.1, 13.2),
                ("two", 13.3, 13.4),
                ("one", 13.5, 13.6),
                ("zero", 13.7, 13.8),
            ],
            next_word=("Welcome.", 14.0, 14.4),
        )
        cut = _apply_start_phrase_countdown(
            rows,
            self._TRIGGER,
            countdown_tokens=self._COUNTDOWN,
            countdown_suffix_tokens=self._COUNTDOWN_SUFFIX,
            preroll_sec=1.0,
        )
        self.assertEqual(cut.next_word_text, "welcome")
        self.assertIn("one", cut.matched_phrase)
        self.assertIn("zero", cut.matched_phrase)

    def test_start_phrase_countdown_omits_in_before_countdown(self) -> None:
        rows = self._oath_countdown_rows(
            oath_words=[
                ("I", 10.0, 10.1),
                ("solemnly", 10.2, 10.5),
                ("swear", 10.6, 10.9),
                ("I'm", 11.0, 11.2),
                ("up", 11.4, 11.5),
                ("to", 11.6, 11.7),
                ("no", 11.8, 11.9),
                ("good,", 12.0, 12.3),
            ],
            countdown_words=[
                ("five", 12.6, 12.8),
                ("four", 12.9, 13.0),
                ("three", 13.1, 13.2),
                ("two", 13.3, 13.4),
            ],
            next_word=("Perfect.", 13.8, 14.2),
        )
        cut = _apply_start_phrase_countdown(
            rows,
            self._TRIGGER,
            countdown_tokens=self._COUNTDOWN,
            countdown_suffix_tokens=self._COUNTDOWN_SUFFIX,
            preroll_sec=1.0,
        )
        self.assertEqual(cut.next_word_text, "perfect")
        self.assertNotIn("in", cut.matched_phrase.split()[-4:])

    def test_start_phrase_countdown_i_am_prefix(self) -> None:
        rows = self._oath_countdown_rows(
            oath_words=[
                ("I", 10.0, 10.1),
                ("solemnly", 10.2, 10.5),
                ("swear", 10.6, 10.9),
                ("I", 11.0, 11.1),
                ("am", 11.2, 11.3),
                ("up", 11.4, 11.5),
                ("to", 11.6, 11.7),
                ("no", 11.8, 11.9),
                ("good,", 12.0, 12.3),
                ("in", 12.4, 12.5),
                ("five", 12.6, 12.8),
                ("four", 12.9, 13.0),
                ("three", 13.1, 13.2),
                ("two", 13.3, 13.4),
            ],
            countdown_words=[],
            next_word=("Go.", 13.8, 14.0),
        )
        self.assertTrue(
            _start_phrase_exists(
                rows,
                self._TRIGGER,
                countdown_tokens=self._COUNTDOWN,
                countdown_suffix_tokens=self._COUNTDOWN_SUFFIX,
            )
        )

    def test_start_phrase_countdown_skips_entire_tail_including_in(self) -> None:
        """Oath only, no ``in`` or numbers spoken (Jessiah-style)."""
        rows = [
            Row(
                idx=39,
                start=62.44,
                end=63.24,
                text="Here's the intro phrase.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(("Here's", 62.44, 62.73), ("the", 62.78, 62.8)),
            ),
            Row(
                idx=40,
                start=64.0,
                end=65.839,
                text="I solemnly swear I am up to no good.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("I", 64.0, 64.019),
                    ("solemnly", 64.08, 64.46),
                    ("swear", 64.56, 64.82),
                    ("I", 64.94, 64.96),
                    ("am", 65.0, 65.099),
                    ("up", 65.16, 65.26),
                    ("to", 65.3, 65.36),
                    ("no", 65.4, 65.48),
                    ("good.", 65.58, 65.839),
                ),
            ),
            Row(
                idx=41,
                start=68.26,
                end=68.72,
                text="Perfect.",
                speaker_id=1,
                speaker_name="Guest",
                words=_words(("Perfect.", 68.26, 68.72)),
            ),
        ]
        cut = _apply_start_phrase_countdown(
            rows,
            self._TRIGGER,
            countdown_tokens=self._COUNTDOWN,
            countdown_suffix_tokens=self._COUNTDOWN_SUFFIX,
            preroll_sec=1.0,
        )
        self.assertEqual(cut.next_word_text, "perfect")
        self.assertEqual([r.idx for r in cut.rows], [41])
        self.assertIn("good", cut.matched_phrase)
        self.assertNotIn("in", cut.matched_phrase)
        self.assertNotIn("five", cut.matched_phrase)

    def test_start_phrase_countdown_skips_in_but_keeps_spoken_numbers(self) -> None:
        rows = self._oath_countdown_rows(
            oath_words=[
                ("I", 10.0, 10.1),
                ("solemnly", 10.2, 10.5),
                ("swear", 10.6, 10.9),
                ("I'm", 11.0, 11.2),
                ("up", 11.4, 11.5),
                ("to", 11.6, 11.7),
                ("no", 11.8, 11.9),
                ("good,", 12.0, 12.3),
            ],
            countdown_words=[
                ("five", 12.6, 12.8),
                ("two", 13.2, 13.4),
            ],
            next_word=("Perfect.", 13.8, 14.2),
        )
        cut = _apply_start_phrase_countdown(
            rows,
            self._TRIGGER,
            countdown_tokens=self._COUNTDOWN,
            countdown_suffix_tokens=self._COUNTDOWN_SUFFIX,
            preroll_sec=1.0,
        )
        self.assertEqual(cut.next_word_text, "perfect")
        self.assertIn("five", cut.matched_phrase)
        self.assertIn("two", cut.matched_phrase)
        self.assertNotIn("in", cut.matched_phrase)

    def test_start_trigger_countdown_accepts_ten_through_two(self) -> None:
        rows = self._oath_countdown_rows(
            oath_words=[
                ("I", 10.0, 10.1),
                ("solemnly", 10.2, 10.5),
                ("swear", 10.6, 10.9),
                ("I'm", 11.0, 11.2),
                ("up", 11.4, 11.5),
                ("to", 11.6, 11.7),
                ("no", 11.8, 11.9),
                ("good,", 12.0, 12.3),
                ("in", 12.4, 12.5),
            ],
            countdown_words=[
                ("ten", 12.6, 12.8),
                ("nine", 12.9, 13.0),
                ("eight", 13.1, 13.2),
                ("seven", 13.3, 13.4),
                ("six", 13.5, 13.6),
                ("five", 13.7, 13.8),
                ("four", 13.9, 14.0),
                ("three", 14.1, 14.2),
                ("two", 14.3, 14.4),
            ],
            next_word=("Welcome.", 14.8, 15.2),
        )
        full_countdown = [
            "ten",
            "nine",
            "eight",
            "seven",
            "six",
            "five",
            "four",
            "three",
            "two",
        ]
        cut = _apply_start_phrase_countdown(
            rows,
            self._TRIGGER,
            countdown_tokens=full_countdown,
            countdown_suffix_tokens=self._COUNTDOWN_SUFFIX,
            preroll_sec=1.0,
        )
        self.assertEqual(cut.next_word_text, "welcome")
        self.assertIn("ten", cut.matched_phrase)
        self.assertIn("two", cut.matched_phrase)

    def test_start_trigger_countdown_can_start_mid_sequence(self) -> None:
        rows = self._oath_countdown_rows(
            oath_words=[
                ("I", 10.0, 10.1),
                ("solemnly", 10.2, 10.5),
                ("swear", 10.6, 10.9),
                ("I'm", 11.0, 11.2),
                ("up", 11.4, 11.5),
                ("to", 11.6, 11.7),
                ("no", 11.8, 11.9),
                ("good,", 12.0, 12.3),
            ],
            countdown_words=[
                ("three", 12.6, 12.8),
                ("two", 12.9, 13.0),
            ],
            next_word=("Go.", 13.4, 13.8),
        )
        cut = _apply_start_phrase_countdown(
            rows,
            self._TRIGGER,
            countdown_tokens=self._COUNTDOWN,
            countdown_suffix_tokens=self._COUNTDOWN_SUFFIX,
            preroll_sec=1.0,
        )
        self.assertEqual(cut.next_word_text, "go")

    def test_start_phrase_speaker_becomes_host_camera(self) -> None:
        from generate_full_dsl import _cam_by_speaker_with_host, _main_impl
        import sys as _sys

        self.assertEqual(
            _cam_by_speaker_with_host(1, {0: "speaker_0", 1: "speaker_1"}),
            {0: "speaker_1", 1: "speaker_0"},
        )
        self.assertEqual(
            _cam_by_speaker_with_host(0, {0: "speaker_0", 1: "speaker_1"}),
            {0: "speaker_0", 1: "speaker_1"},
        )

        # Speaker 1 says the start phrase → their lines should use !camera speaker_0.
        transcript = {
            "0": {
                "start": 10.0,
                "end": 14.0,
                "text": "Hut of brown, now sit down. Jolly!",
                "speaker_id": 1,
                "speaker_name": "Host",
                "words": [
                    {"text": "Hut", "start": 10.0, "end": 10.2},
                    {"text": "of", "start": 10.3, "end": 10.4},
                    {"text": "brown,", "start": 10.5, "end": 10.8},
                    {"text": "now", "start": 11.0, "end": 11.2},
                    {"text": "sit", "start": 11.3, "end": 11.5},
                    {"text": "down.", "start": 11.6, "end": 12.0},
                    {"text": "Jolly!", "start": 12.5, "end": 13.0},
                ],
            },
            "1": {
                "start": 20.0,
                "end": 21.0,
                "text": "Hello there.",
                "speaker_id": 0,
                "speaker_name": "Guest",
                "words": [
                    {"text": "Hello", "start": 20.0, "end": 20.4},
                    {"text": "there.", "start": 20.5, "end": 21.0},
                ],
            },
        }
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "t.json"
            out = Path(td) / "out.dsl"
            src.write_text(json.dumps(transcript), encoding="utf-8")
            argv = [
                "generate_full_dsl.py",
                str(src),
                "--segment",
                "main",
                "--output",
                str(out),
                "--start-phrase",
                "Hut of brown, now sit down.",
                "--no-camera-switch-offset",
                "--open-ben-sec",
                "0",
                "--tail-ben-sec",
                "0",
            ]
            old = _sys.argv
            try:
                _sys.argv = argv
                rc = _main_impl()
            finally:
                _sys.argv = old
            self.assertEqual(rc, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("Host = transcript speaker_id 1 -> speaker_0", text)
            self.assertIn("Speaker 1 -> speaker_0", text)
            self.assertIn("Speaker 0 -> speaker_1", text)
            # After the start cut, first kept content is still speaker_id 1 ("Jolly").
            self.assertRegex(text, r"!camera speaker_0\s*\n\$segmentmain/0")

    def test_end_phrase_keeps_one_second_after_prior_word(self) -> None:
        # End phrase starts within postroll — clamp before first end-phrase word.
        started = _apply_start_phrase(
            self.rows,
            "Hut of brown, now sit down.",
            preroll_sec=1.0,
        )
        cut = _apply_end_phrase(
            started.rows,
            "Hut of brown, now stand up.",
            postroll_sec=1.0,
        )
        self.assertEqual([r.idx for r in cut.rows], [1, 2])
        self.assertEqual(cut.last_word_text, "fun")
        # fun ends at 21.0; end phrase "Hut" starts at 21.2 (< 22.0 postroll cap).
        self.assertAlmostEqual(cut.content_end_abs, 21.2)
        self.assertAlmostEqual(cut.last_slice_end or -1.0, 1.2)

    def test_end_phrase_full_postroll_when_end_phrase_after_postroll(self) -> None:
        rows = [
            Row(
                idx=0,
                start=0.0,
                end=5.0,
                text="Intro.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(("Intro.", 0.0, 0.5)),
            ),
            Row(
                idx=1,
                start=10.0,
                end=20.0,
                text="Content ends here. Be excellent to each other and party on dudes.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Content", 10.0, 10.3),
                    ("ends", 10.4, 10.6),
                    ("here.", 10.7, 11.0),
                    ("Be", 14.0, 14.1),
                    ("excellent", 14.2, 14.5),
                    ("to", 14.6, 14.7),
                    ("each", 14.8, 14.9),
                    ("other", 15.0, 15.2),
                    ("and", 15.3, 15.4),
                    ("party", 15.5, 15.7),
                    ("on", 15.8, 15.9),
                    ("dudes", 16.0, 16.3),
                ),
            ),
        ]
        cut = _apply_end_phrase(
            rows,
            "Be excellent to each other and party on dudes",
            postroll_sec=1.0,
        )
        self.assertEqual(cut.last_word_text, "here")
        # Last content word ends 11.0; end phrase starts 14.0 — full 1s postroll applies.
        self.assertAlmostEqual(cut.content_end_abs, 12.0)
        self.assertAlmostEqual(cut.last_slice_end or -1.0, 2.0)

    def test_latest_end_phrase_wins_among_alternates(self) -> None:
        started = _apply_start_phrase(
            self.rows,
            "Hut of brown, now sit down.",
            preroll_sec=1.0,
        )
        latest = _find_latest_end_phrase_match(
            started.rows,
            [
                "Be excellent to each other and party on dudes",
                "Hut of brown, now stand up.",
            ],
        )
        self.assertIsNotNone(latest)
        phrase, _match_i = latest or ("", -1)
        self.assertEqual(phrase, "Hut of brown, now stand up.")

    def test_missing_start_phrase_errors(self) -> None:
        with self.assertRaises(ValueError):
            _apply_start_phrase(self.rows, "this phrase does not exist", preroll_sec=1.0)

    def test_main_skips_missing_start_phrase_without_error(self) -> None:
        from generate_full_dsl import _main_impl
        import sys as _sys

        transcript = {
            "0": {
                "start": 0.0,
                "end": 2.0,
                "text": "Hello world.",
                "speaker_id": 0,
                "speaker_name": "Host",
                "words": [
                    {"text": "Hello", "start": 0.0, "end": 0.5},
                    {"text": "world.", "start": 0.6, "end": 1.0},
                ],
            }
        }
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "t.json"
            out = Path(td) / "out.dsl"
            src.write_text(json.dumps(transcript), encoding="utf-8")
            argv = [
                "generate_full_dsl.py",
                str(src),
                "--segment",
                "main",
                "--output",
                str(out),
                "--start-phrase",
                "this phrase does not exist",
                "--no-camera-switch-offset",
                "--open-ben-sec",
                "0",
                "--tail-ben-sec",
                "0",
            ]
            old = _sys.argv
            try:
                _sys.argv = argv
                rc = _main_impl()
            finally:
                _sys.argv = old
            self.assertEqual(rc, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("Start trigger not found", text)
            self.assertIn("$segmentmain/0", text)

    def test_cli_emits_opening_and_slice(self) -> None:
        transcript = {
            "0": {
                "start": 10.0,
                "end": 14.0,
                "text": "Hut of brown, now sit down. Jolly!",
                "speaker_id": 0,
                "speaker_name": "Host",
                "words": [
                    {"text": "Hut", "start": 10.0, "end": 10.2},
                    {"text": "of", "start": 10.3, "end": 10.4},
                    {"text": "brown,", "start": 10.5, "end": 10.8},
                    {"text": "now", "start": 11.0, "end": 11.2},
                    {"text": "sit", "start": 11.3, "end": 11.5},
                    {"text": "down.", "start": 11.6, "end": 12.0},
                    {"text": "Jolly!", "start": 12.5, "end": 13.0},
                ],
            },
            "1": {
                "start": 20.0,
                "end": 21.0,
                "text": "Hello there.",
                "speaker_id": 1,
                "speaker_name": "Guest",
                "words": [
                    {"text": "Hello", "start": 20.0, "end": 20.4},
                    {"text": "there.", "start": 20.5, "end": 21.0},
                ],
            },
        }
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "t.json"
            out = Path(td) / "out.dsl"
            src.write_text(json.dumps(transcript), encoding="utf-8")
            from generate_full_dsl import _main_impl
            import sys as _sys

            argv = [
                "generate_full_dsl.py",
                str(src),
                "--segment",
                "main",
                "--output",
                str(out),
                "--start-phrase",
                "Hut of brown, now sit down.",
                "--no-camera-switch-offset",
                "--open-ben-sec",
                "0",
                "--tail-ben-sec",
                "0",
            ]
            old = _sys.argv
            try:
                _sys.argv = argv
                rc = _main_impl()
            finally:
                _sys.argv = old
            self.assertEqual(rc, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("!opening 1000", text)
            self.assertIn("slice(2.500:", text)


class PauseUnpauseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            Row(
                idx=0,
                start=0.0,
                end=5.0,
                text="Hello there friend.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Hello", 0.0, 0.4),
                    ("there", 0.5, 0.8),
                    ("friend.", 0.9, 1.3),
                ),
            ),
            Row(
                idx=1,
                start=5.0,
                end=12.0,
                text="Computer Freeze Program. Secret stuff here.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Computer", 5.0, 5.4),
                    ("Freeze", 5.5, 5.9),
                    ("Program.", 6.0, 6.5),
                    ("Secret", 7.0, 7.4),
                    ("stuff", 7.5, 7.9),
                    ("here.", 8.0, 8.4),
                ),
            ),
            Row(
                idx=2,
                start=12.0,
                end=20.0,
                text="Computer Resume Program. Welcome back everyone.",
                speaker_id=1,
                speaker_name="Guest",
                words=_words(
                    ("Computer", 12.0, 12.4),
                    ("Resume", 12.5, 12.9),
                    ("Program.", 13.0, 13.5),
                    ("Welcome", 14.0, 14.4),
                    ("back", 14.5, 14.8),
                    ("everyone.", 14.9, 15.5),
                ),
            ),
        ]

    def test_matched_pair_removes_middle_and_marks_seam(self) -> None:
        from generate_full_dsl import _apply_pause_unpause_to_pieces

        pieces, notes = _apply_pause_unpause_to_pieces(
            self.rows,
            pause_phrase="Computer Freeze Program.",
            unpause_phrases=["Computer Resume Program", "Computer Unfreeze Program"],
            preroll_sec=0.25,
            postroll_sec=0.7,
            first_slice_start=None,
            last_slice_end=None,
        )
        self.assertTrue(any("Pause" in n for n in notes))
        # Should keep intro + resume content; drop secret stuff.
        texts = " ".join(p.row.text for p in pieces)
        self.assertIn("Hello", texts)
        self.assertIn("Welcome", texts)
        # Seam piece is the resume side.
        self.assertTrue(any(p.seam_after_pause for p in pieces))
        seam_pieces = [p for p in pieces if p.seam_after_pause]
        self.assertEqual(len(seam_pieces), 1)
        self.assertIn("Welcome", seam_pieces[0].row.text)

    def test_dsl_emits_pause_flag_at_seam(self) -> None:
        transcript = {
            str(r.idx): {
                "start": r.start,
                "end": r.end,
                "text": r.text,
                "speaker_id": r.speaker_id,
                "speaker_name": r.speaker_name,
                "words": [
                    {"text": w.text, "start": w.start, "end": w.end} for w in r.words
                ],
            }
            for r in self.rows
        }
        with tempfile.TemporaryDirectory() as td:
            import sys as _sys

            from generate_full_dsl import _main_impl

            src = Path(td) / "t.json"
            out = Path(td) / "out.dsl"
            src.write_text(json.dumps(transcript), encoding="utf-8")
            argv = [
                "generate_full_dsl.py",
                str(src),
                "--segment",
                "main",
                "--output",
                str(out),
                "--pause-phrase",
                "Computer Freeze Program.",
                "--unpause-phrase",
                "Computer Resume Program",
                "--no-cameras",
                "--no-camera-switch-offset",
            ]
            old = _sys.argv
            try:
                _sys.argv = argv
                rc = _main_impl()
            finally:
                _sys.argv = old
            self.assertEqual(rc, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("!pause-flag", text)
            self.assertLess(
                text.index("!pause-flag"),
                text.index("Welcome"),
            )
        """Resume abs can fall before the first kept row start (postroll gap)."""
        from generate_full_dsl import _apply_pause_unpause_to_pieces

        rows = [
            Row(
                idx=0,
                start=2460.0,
                end=2469.0,
                text="This is what I got for now.",
                speaker_id=1,
                speaker_name="Guest",
                words=_words(
                    ("This", 2460.0, 2460.3),
                    ("is", 2460.4, 2460.5),
                    ("what", 2460.6, 2460.7),
                    ("I", 2460.8, 2460.9),
                    ("got", 2461.0, 2461.1),
                    ("for", 2461.2, 2461.3),
                    ("now.", 2468.452, 2468.541),
                ),
            ),
            Row(
                idx=1,
                start=2469.832,
                end=2472.0,
                text="Computer Freeze Program. Ah, that's the pause phrase.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Computer", 2469.832, 2470.192),
                    ("Freeze", 2470.312, 2470.512),
                    ("Program.", 2470.592, 2471.092),
                ),
            ),
            Row(
                idx=2,
                start=2477.152,
                end=2485.0,
                text="Computer Resume Program. Okay. Uh, you then go on.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Computer", 2477.152, 2477.452),
                    ("Resume", 2479.792, 2480.112),
                    ("Program.", 2480.192, 2480.731),
                    ("Okay.", 2482.552, 2482.812),
                    ("Uh,", 2483.172, 2483.552),
                    ("you", 2483.562, 2483.662),
                ),
            ),
        ]
        pieces, _notes = _apply_pause_unpause_to_pieces(
            rows,
            pause_phrase="Computer Freeze Program.",
            unpause_phrases=["Computer Resume Program"],
            preroll_sec=0.25,
            postroll_sec=0.7,
            first_slice_start=None,
            last_slice_end=None,
        )
        seam_pieces = [p for p in pieces if p.seam_after_pause]
        self.assertEqual(len(seam_pieces), 1)
        self.assertIn("Okay.", seam_pieces[0].row.text)
        # Postroll falls mid-row (resume phrase + Okay share a row) -> positive slice_start.
        self.assertAlmostEqual(seam_pieces[0].slice_start or 0.0, 4.7)

    def test_pause_postroll_negative_lead_in_when_row_starts_after_resume(self) -> None:
        from generate_full_dsl import _apply_pause_unpause_to_pieces

        rows = [
            Row(
                idx=0,
                start=2460.0,
                end=2469.0,
                text="This is what I got for now.",
                speaker_id=1,
                speaker_name="Guest",
                words=_words(("now.", 2468.452, 2468.541)),
            ),
            Row(
                idx=1,
                start=2469.832,
                end=2472.0,
                text="Computer Freeze Program.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Computer", 2469.832, 2470.192),
                    ("Freeze", 2470.312, 2470.512),
                    ("Program.", 2470.592, 2471.092),
                ),
            ),
            Row(
                idx=2,
                start=2477.792,
                end=2480.731,
                text="Computer Resume Program.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Computer", 2479.252, 2479.662),
                    ("Resume", 2479.792, 2480.112),
                    ("Program.", 2480.192, 2480.731),
                ),
            ),
            Row(
                idx=3,
                start=2482.552,
                end=2482.812,
                text="Okay.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(("Okay.", 2482.552, 2482.812)),
            ),
        ]
        pieces, _notes = _apply_pause_unpause_to_pieces(
            rows,
            pause_phrase="Computer Freeze Program.",
            unpause_phrases=["Computer Resume Program"],
            preroll_sec=0.25,
            postroll_sec=0.7,
            first_slice_start=None,
            last_slice_end=None,
        )
        seam_pieces = [p for p in pieces if p.seam_after_pause]
        self.assertEqual(len(seam_pieces), 1)
        self.assertEqual(seam_pieces[0].row.idx, 3)
        self.assertAlmostEqual(seam_pieces[0].slice_start or 0.0, -0.7)

    def test_pause_preroll_extends_last_pre_pause_piece(self) -> None:
        from generate_full_dsl import _apply_pause_unpause_to_pieces

        rows = [
            Row(
                idx=0,
                start=2460.0,
                end=2469.0,
                text="This is what I got for now.",
                speaker_id=1,
                speaker_name="Guest",
                words=_words(
                    ("This", 2460.0, 2460.3),
                    ("now.", 2468.452, 2468.541),
                ),
            ),
            Row(
                idx=1,
                start=2469.832,
                end=2472.0,
                text="Computer Freeze Program.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Computer", 2469.832, 2470.192),
                    ("Freeze", 2470.312, 2470.512),
                    ("Program.", 2470.592, 2471.092),
                ),
            ),
            Row(
                idx=2,
                start=2477.152,
                end=2485.0,
                text="Computer Resume Program. Okay.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Computer", 2477.152, 2477.452),
                    ("Resume", 2479.792, 2480.112),
                    ("Program.", 2480.192, 2480.731),
                    ("Okay.", 2482.552, 2482.812),
                ),
            ),
        ]
        pieces, _notes = _apply_pause_unpause_to_pieces(
            rows,
            pause_phrase="Computer Freeze Program.",
            unpause_phrases=["Computer Resume Program"],
            preroll_sec=0.25,
            postroll_sec=0.7,
            first_slice_start=None,
            last_slice_end=None,
        )
        pre_pause = [p for p in pieces if p.row.idx == 0]
        self.assertEqual(len(pre_pause), 1)
        # now. ends 2468.541; +0.25s preroll => slice_end 8.791 from row start 2460.0
        self.assertAlmostEqual(pre_pause[0].slice_end or -1.0, 8.791)

    def test_pause_path_preserves_end_postroll_on_last_piece(self) -> None:
        from generate_full_dsl import _apply_end_phrase, _apply_pause_unpause_to_pieces

        rows = [
            Row(
                idx=0,
                start=0.0,
                end=5.0,
                text="Hello friend.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Hello", 0.0, 0.4),
                    ("friend.", 0.5, 1.3),
                ),
            ),
            Row(
                idx=1,
                start=5.0,
                end=12.0,
                text="Computer Freeze Program. Secret stuff here.",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Computer", 5.0, 5.4),
                    ("Freeze", 5.5, 5.9),
                    ("Program.", 6.0, 6.5),
                    ("Secret", 7.0, 7.4),
                    ("stuff", 7.5, 7.9),
                    ("here.", 8.0, 8.4),
                ),
            ),
            Row(
                idx=2,
                start=12.0,
                end=20.0,
                text="Computer Resume Program. Welcome back everyone.",
                speaker_id=1,
                speaker_name="Guest",
                words=_words(
                    ("Computer", 12.0, 12.4),
                    ("Resume", 12.5, 12.9),
                    ("Program.", 13.0, 13.5),
                    ("Welcome", 14.0, 14.4),
                    ("back", 14.5, 14.8),
                    ("everyone.", 14.9, 15.5),
                ),
            ),
            Row(
                idx=3,
                start=20.0,
                end=24.0,
                text="Thanks friend. Hut of brown now stand up.",
                speaker_id=1,
                speaker_name="Guest",
                words=_words(
                    ("Thanks", 20.0, 20.4),
                    ("friend.", 20.5, 21.0),
                    ("Hut", 22.0, 22.2),
                    ("of", 22.3, 22.4),
                    ("brown", 22.5, 22.7),
                    ("now", 22.8, 22.9),
                    ("stand", 23.0, 23.2),
                    ("up.", 23.3, 23.5),
                ),
            ),
        ]
        ended = _apply_end_phrase(
            rows,
            "Hut of brown now stand up.",
            postroll_sec=1.0,
        )
        pieces, _notes = _apply_pause_unpause_to_pieces(
            ended.rows,
            pause_phrase="Computer Freeze Program.",
            unpause_phrases=["Computer Resume Program"],
            preroll_sec=0.25,
            postroll_sec=0.7,
            first_slice_start=None,
            last_slice_end=ended.last_slice_end,
        )
        last = pieces[-1]
        self.assertEqual(last.row.idx, 3)
        # friend. ends 21.0; +1s postroll => abs 22.0; row starts 20.0 => slice_end 2.0
        self.assertAlmostEqual(last.slice_end or -1.0, 2.0)

    def test_unmatched_pause_left_in(self) -> None:
        from generate_full_dsl import _apply_pause_unpause_to_pieces

        rows = self.rows[:2]  # no unpause
        pieces, _notes = _apply_pause_unpause_to_pieces(
            rows,
            pause_phrase="Computer Freeze Program.",
            unpause_phrases=["Computer Resume Program"],
            preroll_sec=0.25,
            postroll_sec=0.7,
            first_slice_start=None,
            last_slice_end=None,
        )
        self.assertEqual(len(pieces), 2)
        self.assertFalse(any(p.seam_after_pause for p in pieces))

    def test_abort_disables_pause(self) -> None:
        from generate_full_dsl import _phrase_exists, _main_impl
        import sys as _sys

        rows = self.rows + [
            Row(
                idx=3,
                start=30.0,
                end=35.0,
                text="Emergency override - Eject the warp core",
                speaker_id=0,
                speaker_name="Host",
                words=_words(
                    ("Emergency", 30.0, 30.5),
                    ("override", 30.6, 31.1),
                    ("Eject", 31.5, 31.9),
                    ("the", 32.0, 32.1),
                    ("warp", 32.2, 32.5),
                    ("core", 32.6, 33.0),
                ),
            )
        ]
        self.assertTrue(
            _phrase_exists(rows, "Emergency override - Eject the warp core")
        )

        transcript = {
            str(r.idx): {
                "start": r.start,
                "end": r.end,
                "text": r.text,
                "speaker_id": r.speaker_id,
                "speaker_name": r.speaker_name,
                "words": [
                    {"text": w.text, "start": w.start, "end": w.end} for w in r.words
                ],
            }
            for r in rows
        }
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "t.json"
            out = Path(td) / "out.dsl"
            src.write_text(json.dumps(transcript), encoding="utf-8")
            argv = [
                "generate_full_dsl.py",
                str(src),
                "--segment",
                "main",
                "--output",
                str(out),
                "--pause-phrase",
                "Computer Freeze Program.",
                "--unpause-phrase",
                "Computer Resume Program",
                "--unpause-phrase",
                "Computer Unfreeze Program",
                "--abort-phrase",
                "Emergency override - Eject the warp core",
                "--no-cameras",
                "--no-camera-switch-offset",
            ]
            old = _sys.argv
            try:
                _sys.argv = argv
                rc = _main_impl()
            finally:
                _sys.argv = old
            self.assertEqual(rc, 0)
            text = out.read_text(encoding="utf-8")
            # Abort keeps the paused middle content in the cut.
            self.assertIn("Secret", text)


if __name__ == "__main__":
    unittest.main()
