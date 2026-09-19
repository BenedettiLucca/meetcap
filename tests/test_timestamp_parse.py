"""#26 — parser accepts total-minutes and H:MM:SS timestamps past 99 minutes.

The SEGMENT_PATTERN in exporter/transcript_parser.py must stay backward
compatible with old 2-digit transcripts while accepting the wider forms the
shared formatter (transcript_segments) emits for meetings over an hour.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exporter.transcript_parser import parse_transcript_segments
from transcript_segments import parse_timestamp


class ParseTimestampTests(unittest.TestCase):
    """parse_timestamp is the symmetric inverse of the shared formatter."""

    def test_mm_ss_under_one_hour(self):
        self.assertEqual(parse_timestamp("00:05"), 5)
        self.assertEqual(parse_timestamp("09:59"), 599)

    def test_total_minutes_beyond_99(self):
        self.assertEqual(parse_timestamp("100:00"), 6000)
        self.assertEqual(parse_timestamp("100:07"), 6007)
        self.assertEqual(parse_timestamp("1234:59"), 1234 * 60 + 59)

    def test_h_mm_ss(self):
        self.assertEqual(parse_timestamp("1:00:00"), 3600)
        self.assertEqual(parse_timestamp("1:02:05"), 3725)

    def test_rejects_non_timestamp_strings(self):
        for bad in ("", "abc", "1", "1:2:3:4", "1:xx"):
            with self.assertRaises(ValueError, msg=bad):
                parse_timestamp(bad)


class SegmentParserWideTimestampTests(unittest.TestCase):
    """SEGMENT_PATTERN must accept MM:SS, total-minutes, and H:MM:SS."""

    def test_total_minutes_line_parses(self):
        segments = parse_transcript_segments("[100:00 → 100:07] text")
        self.assertEqual(segments, [
            {"index": 0, "start": "100:00", "end": "100:07", "text": "text"},
        ])

    def test_h_mm_ss_line_parses(self):
        segments = parse_transcript_segments("[1:00:00 → 1:00:07] late")
        self.assertEqual(segments, [
            {"index": 0, "start": "1:00:00", "end": "1:00:07", "text": "late"},
        ])

    def test_arrow_lines_parse_identically_before_and_after_one_hour(self):
        short = parse_transcript_segments("[00:59 → 01:05] x")
        long = parse_transcript_segments("[1:00:00 → 1:00:07] x")
        self.assertEqual([list(seg) for seg in short], [list(seg) for seg in long])
        self.assertEqual(short[0]["text"], long[0]["text"])

    def test_mixed_transcript_parses_all_lines(self):
        text = "\n".join([
            "[00:00 → 00:10] early",
            "[59:50 → 60:10] crossing the hour in total minutes",
            "[100:00 → 100:07] total-minutes",
            "[1:40:00 → 1:40:07] hours form",
        ])
        segments = parse_transcript_segments(text)
        self.assertEqual(len(segments), 4)
        self.assertEqual([seg["start"] for seg in segments],
                         ["00:00", "59:50", "100:00", "1:40:00"])

    def test_old_two_digit_transcripts_unchanged(self):
        text = "\n".join([
            "[00:00 → 00:05] a",
            "[99:59 → 99:59] b",
            "[05:00:10 → 05:01:00] old h:mm:ss form",
        ])
        segments = parse_transcript_segments(text)
        self.assertEqual([seg["start"] for seg in segments],
                         ["00:00", "99:59", "05:00:10"])
        self.assertEqual([seg["text"] for seg in segments], ["a", "b", "old h:mm:ss form"])


if __name__ == "__main__":
    unittest.main()
