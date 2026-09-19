import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exporter.transcript_parser import parse_transcript_segments
from transcript_segments import aggregate_segments, format_transcript_lines, parse_timestamp


def segs(*pairs):
    return [{"start": s, "end": e, "text": t} for (s, e, t) in pairs]


class AggregateSegmentsTests(unittest.TestCase):
    def test_gap_under_threshold_joins(self):
        out = aggregate_segments(segs((0.0, 2.0, "a"), (2.3, 4.0, "b")))
        self.assertEqual(out, [{"start": 0.0, "end": 4.0, "text": "a b"}])

    def test_exact_boundary_gap_splits(self):
        # gap exactly 0.5 must NOT merge (merge is strictly < gap_s)
        out = aggregate_segments(segs((0.0, 2.0, "a"), (2.5, 4.0, "b")))
        self.assertEqual(out, segs((0.0, 2.0, "a"), (2.5, 4.0, "b")))

    def test_gap_over_threshold_splits(self):
        out = aggregate_segments(segs((0.0, 2.0, "a"), (3.0, 4.0, "b")))
        self.assertEqual(out, segs((0.0, 2.0, "a"), (3.0, 4.0, "b")))

    def test_overlap_merges(self):
        # negative gap (overlap) also merges
        out = aggregate_segments(segs((0.0, 2.0, "a"), (1.5, 4.0, "b")))
        self.assertEqual(out, [{"start": 0.0, "end": 4.0, "text": "a b"}])

    def test_chained_merges_carry_end_forward(self):
        out = aggregate_segments(segs((0.0, 2.0, "a"), (2.1, 4.0, "b"), (4.2, 5.0, "c")))
        self.assertEqual(out, [{"start": 0.0, "end": 5.0, "text": "a b c"}])

    def test_empty_input(self):
        self.assertEqual(aggregate_segments([]), [])
        self.assertEqual(format_transcript_lines([]), [])

    def test_single_segment_is_itself(self):
        self.assertEqual(
            aggregate_segments(segs((1.0, 2.0, "hello"))),
            [{"start": 1.0, "end": 2.0, "text": "hello"}],
        )

    def test_text_preserved_losslessly_and_in_order(self):
        inputs = segs(
            (0.0, 1.0, " first"),
            (1.1, 2.0, "second "),
            (2.2, 3.0, " third "),
            (10.0, 11.0, "fourth"),
        )
        out = aggregate_segments(inputs)
        joined_in = " ".join(s["text"].strip() for s in inputs)
        joined_out = " ".join(s["text"] for s in out)
        self.assertEqual(joined_out, joined_in)
        self.assertEqual(out, [
            {"start": 0.0, "end": 3.0, "text": "first second third"},
            {"start": 10.0, "end": 11.0, "text": "fourth"},
        ])

    def test_custom_gap_threshold(self):
        out = aggregate_segments(segs((0.0, 2.0, "a"), (3.5, 5.0, "b")), gap_s=2.0)
        self.assertEqual(out, [{"start": 0.0, "end": 5.0, "text": "a b"}])


class FormatterTests(unittest.TestCase):
    def test_emits_meetcap_line_format(self):
        lines = format_transcript_lines(segs((0.0, 2.0, " hello "), (10.0, 12.0, "world")))
        self.assertEqual(lines, [
            "[00:00 → 00:02] hello",
            "[00:10 → 00:12] world",
        ])

    def test_minute_padding_matches_meetcap_semantics(self):
        lines = format_transcript_lines(segs((5.0, 65.0, "x")))
        self.assertEqual(lines, ["[00:05 → 01:05] x"])

    def test_hour_long_timestamps_format_h_mm_ss(self):
        lines = format_transcript_lines(segs((3600.0, 3725.0, "late")))
        self.assertEqual(lines, ["[1:00:00 → 1:02:05] late"])

    def test_merged_spans_use_first_start_last_end(self):
        lines = format_transcript_lines(segs((0.0, 2.0, "a"), (2.4, 90.0, "b")))
        self.assertEqual(lines, ["[00:00 → 01:30] a b"])


class RoundTripTests(unittest.TestCase):
    """#26 — parse_timestamp must round-trip every form the formatter emits."""

    def test_round_trip_total_minutes_and_hour_branches(self):
        for seconds in (5, 3599, 3600, 3725, 6000, 6007, 132459):
            line = format_transcript_lines([{"start": float(seconds),
                                              "end": float(seconds + 1),
                                              "text": "t"}])[0]
            parsed = parse_transcript_segments(line)
            self.assertEqual(len(parsed), 1, msg=f"line {line!r} must parse back")
            self.assertEqual(parsed[0]["text"], "t")
            self.assertEqual(parse_timestamp(parsed[0]["start"]), seconds, msg=line)
            self.assertEqual(parse_timestamp(parsed[0]["end"]), seconds + 1, msg=line)

    def test_round_trip_under_one_hour(self):
        line = format_transcript_lines(segs((0.0, 599.0, "x")))[0]
        parsed = parse_transcript_segments(line)
        self.assertEqual(parse_timestamp(parsed[0]["start"]), 0)
        self.assertEqual(parse_timestamp(parsed[0]["end"]), 599)

    def test_round_trip_at_and_over_one_hour(self):
        for start_s in (3600.0, 6007.0, 7200.0):
            line = format_transcript_lines(segs((start_s, start_s + 5.0, "y")))[0]
            parsed = parse_transcript_segments(line)
            self.assertEqual(parse_timestamp(parsed[0]["start"]), int(start_s))
            self.assertEqual(parse_timestamp(parsed[0]["end"]), int(start_s) + 5)


class CallSiteContractTests(unittest.TestCase):
    """Both whisper-export writer paths must go through the shared aggregator."""

    def test_both_meetcap_call_sites_reference_shared_formatter(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "meetcap.py").read_text()
        n = source.count("transcript_segments.format_transcript_lines(")
        self.assertGreaterEqual(
            n, 2,
            "contracts #52: both router and faster-whisper writer paths must call "
            "transcript_segments.format_transcript_lines; found %d call site(s)" % n,
        )


if __name__ == "__main__":
    unittest.main()
