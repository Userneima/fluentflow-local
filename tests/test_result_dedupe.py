"""Storing one copy must be invisible to every reader.

A three-hour transcription stored its transcript three times and its segments
three times: 1,011 KB per record, of which 583 KB was either an exact duplicate
or a field nothing reads. Storage cost is the visible half. The real hazard is
that duplicates drift, and then which copy is "the" transcript depends on who
asks.

So the contract these tests hold is not "smaller" — it is that everything read
through the canonical accessors is byte-identical before and after.
"""

import json
from unittest import TestCase

from backend.core.result_schema import (
    canonical_display_segments,
    canonical_raw_segments,
    normalize_result_for_read,
    normalize_result_for_storage,
)

SEGMENTS = [{"text": f"第 {i} 句。", "start": float(i), "end": float(i + 1)} for i in range(40)]
TRANSCRIPT = "\n".join(segment["text"] for segment in SEGMENTS)
BILINGUAL = [{**segment, "text_zh": f"译文 {index}"} for index, segment in enumerate(SEGMENTS)]


def fat_record(**overrides):
    """A record shaped the way the pipeline used to write one."""
    return {
        "task_id": "task-a",
        "result_schema_version": "2",
        "summary_markdown": "# 笔记",
        "transcript_text": TRANSCRIPT,
        "cleaned_transcript_text": TRANSCRIPT,
        "raw_transcript_text": TRANSCRIPT + "\n多余的一句。",
        "raw_segments": SEGMENTS,
        "display_segments": SEGMENTS,
        "stt_raw_segments": SEGMENTS,
        "transcript_cleanup": {"applied_count": 1, "issues": [{"original": "啊啊啊", "cleaned": "啊"}]},
        **overrides,
    }


def readable(result):
    """Everything a consumer can observe through the canonical accessors."""
    read = normalize_result_for_read(result)
    return {
        "summary_markdown": read.get("summary_markdown"),
        "transcript_text": read.get("transcript_text"),
        "raw_transcript_text": read.get("raw_transcript_text"),
        "raw_segments": canonical_raw_segments(read),
        "display_segments": canonical_display_segments(read),
        "subtitle_mode": read.get("subtitle_mode"),
        "transcript_cleanup": read.get("transcript_cleanup"),
    }


class ReadEquivalenceTests(TestCase):
    def test_nothing_a_reader_can_see_changes(self):
        fat = fat_record()
        self.assertEqual(readable(fat), readable(normalize_result_for_storage(fat)))

    def test_the_transcript_and_its_segments_survive_intact(self):
        stored = normalize_result_for_storage(fat_record())
        self.assertEqual(stored["transcript_text"], TRANSCRIPT)
        self.assertEqual(canonical_raw_segments(stored), SEGMENTS)
        self.assertEqual(canonical_display_segments(stored), SEGMENTS)

    def test_normalizing_twice_changes_nothing_further(self):
        once = normalize_result_for_storage(fat_record())
        self.assertEqual(normalize_result_for_storage(dict(once)), once)


class WhatGetsDroppedTests(TestCase):
    def test_the_duplicate_display_copy_goes(self):
        stored = normalize_result_for_storage(fat_record())
        self.assertNotIn("display_segments", stored)

    def test_the_duplicate_cleaned_transcript_goes(self):
        stored = normalize_result_for_storage(fat_record())
        self.assertNotIn("cleaned_transcript_text", stored)

    def test_the_segment_copy_nothing_reads_goes(self):
        stored = normalize_result_for_storage(fat_record())
        self.assertNotIn("stt_raw_segments", stored)

    def test_what_cleanup_actually_changed_is_still_recorded(self):
        # Dropping the pre-cleanup segments is only acceptable because this is
        # kept: the raw text plus an itemised list of every edit.
        stored = normalize_result_for_storage(fat_record())
        self.assertEqual(stored["raw_transcript_text"], TRANSCRIPT + "\n多余的一句。")
        self.assertEqual(stored["transcript_cleanup"]["issues"][0]["original"], "啊啊啊")


class WhatMustBeKeptTests(TestCase):
    def test_a_translated_display_copy_is_not_a_duplicate_and_stays(self):
        stored = normalize_result_for_storage(fat_record(display_segments=BILINGUAL))
        self.assertEqual(stored["display_segments"], BILINGUAL)
        self.assertEqual(stored["subtitle_mode"], "bilingual_zh")
        self.assertEqual(canonical_display_segments(stored), BILINGUAL)

    def test_a_cleaned_transcript_that_really_differs_stays(self):
        cleaned = TRANSCRIPT.replace("第 0 句。", "第零句。")
        stored = normalize_result_for_storage(fat_record(cleaned_transcript_text=cleaned))
        self.assertEqual(stored["cleaned_transcript_text"], cleaned)

    def test_a_record_without_segments_is_left_alone(self):
        stored = normalize_result_for_storage({
            "task_id": "t", "result_schema_version": "2", "transcript_text": TRANSCRIPT,
        })
        self.assertEqual(stored["transcript_text"], TRANSCRIPT)
        self.assertNotIn("raw_segments", stored)


# Compaction is a storage decision. Letting it reach the read path turned it
# into an API change: `GET /jobs/{id}` answered `display_segments: []` for a
# record holding 3,886 segments, because read and storage shared one normalizer.
class StorageAndReadAreDifferentDirectionsTests(TestCase):
    def test_a_reader_gets_the_derived_copies_back(self):
        stored = normalize_result_for_storage(fat_record())
        self.assertNotIn("display_segments", stored)

        read = normalize_result_for_read(stored)
        self.assertEqual(read["display_segments"], SEGMENTS)
        self.assertEqual(read["cleaned_transcript_text"], TRANSCRIPT)

    def test_a_reader_sees_the_same_record_whether_it_was_compacted_or_not(self):
        fat = normalize_result_for_read(fat_record())
        lean = normalize_result_for_read(normalize_result_for_storage(fat_record()))
        # The field nothing reads is the one legitimate difference.
        fat.pop("stt_raw_segments", None)
        self.assertEqual(fat, lean)

    def test_reading_then_storing_does_not_re_inflate_the_row(self):
        once = normalize_result_for_storage(fat_record())
        round_tripped = normalize_result_for_storage(normalize_result_for_read(once))
        self.assertEqual(round_tripped, once)


class SizeTests(TestCase):
    def test_the_record_gets_materially_smaller(self):
        fat = json.dumps(fat_record(), ensure_ascii=False)
        lean = json.dumps(normalize_result_for_storage(fat_record()), ensure_ascii=False)
        self.assertLess(len(lean), len(fat) * 0.55)
