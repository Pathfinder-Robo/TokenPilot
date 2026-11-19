from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from chat_usage_trend import build_usage_trend
from pricing import estimate_usage_usd


def _event(
    *,
    ts=None,
    chat_key=None,
    input_tokens=0,
    cached_tokens=0,
    output_tokens=0,
    total_tokens=None,
):
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
    ev = {
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if ts is not None:
        ev["timestamp"] = ts
    if chat_key is not None:
        ev["chat_key"] = chat_key
    return ev


class ChatUsageTrendTests(unittest.TestCase):
    def test_bucket_steps_and_zero_length_window(self):
        t0 = datetime(2026, 10, 27, 0, 0, 0, tzinfo=timezone.utc).timestamp()
        ev_at_point = _event(
            ts="2026-10-27T00:00:00Z",
            chat_key="chat_a",
            input_tokens=1_000_000,
        )
        same_res = build_usage_trend(
            t0,
            t0,
            {"codex": [ev_at_point], "gemini": []},
            [{"id": "chat_a", "title": "A", "codex": {"estimated_usd": 2.0}}],
            False,
        )
        self.assertEqual(same_res["bucket_seconds"], 3600)
        self.assertFalse(same_res["partial"])
        self.assertEqual(len(same_res["buckets"]), 1)
        self.assertEqual(same_res["buckets"][0]["start_at"], "2026-10-27T00:00:00Z")
        self.assertEqual(same_res["buckets"][0]["end_at"], "2026-10-27T00:00:00Z")
        self.assertAlmostEqual(same_res["buckets"][0]["total_usd"], 2.0)

        res_48h = build_usage_trend(t0, t0 + 48 * 3600, {"codex": [], "gemini": []}, [], False)
        self.assertEqual(res_48h["bucket_seconds"], 3600)
        self.assertEqual(len(res_48h["buckets"]), 48)

        res_7d = build_usage_trend(t0, t0 + 7 * 86400, {"codex": [], "gemini": []}, [], False)
        self.assertEqual(res_7d["bucket_seconds"], 21600)
        self.assertEqual(len(res_7d["buckets"]), 28)

        res_45d = build_usage_trend(t0, t0 + 45 * 86400, {"codex": [], "gemini": []}, [], True)
        self.assertEqual(res_45d["bucket_seconds"], 86400)
        self.assertEqual(len(res_45d["buckets"]), 45)
        self.assertTrue(res_45d["partial"])

    def test_boundaries_top_chats_exclude_gemini_and_do_not_mutate(self):
        start = datetime(2026, 10, 27, 10, 0, 0, tzinfo=timezone.utc).timestamp()
        end = start + 5400  # 1.5h -> [10:00..11:00), [11:00..11:30]

        rows = [
            {"id": "unattributed", "title": None, "codex": {"estimated_usd": 99.0}},
            {"id": "c_tie_b", "title": "Tie B", "codex": {"estimated_usd": 5.0}},
            {"id": "c_tie_a", "title": "Tie A", "codex": {"estimated_usd": 5.0}},
            {"id": "c_top", "title": "Top", "codex": {"estimated_usd": 12.5}, "gemini": {}},
            {"id": "c_fourth", "title": "Fourth", "codex": {"estimated_usd": 1.0}},
            {"id": "c_gemini_only", "title": "Gem", "gemini": {"total_tokens": 500}},
            {"id": "c_unknown", "title": None, "codex": {"estimated_usd": None}},
        ]

        e_before = _event(ts="2026-10-27T09:59:59Z", total_tokens=500, input_tokens=0, output_tokens=0)
        e_start = _event(ts="2026-10-27T10:00:00Z", chat_key="c_top", input_tokens=500_000)
        e_offset_b0 = _event(ts="2026-10-27T12:30:00+02:00", chat_key="c_tie_a", output_tokens=100_000)
        e_edge_b1 = _event(ts="2026-10-27T11:00:00Z", chat_key="c_top", output_tokens=200_000)
        e_end_b1 = _event(ts="2026-10-27T11:30:00Z", input_tokens=250_000)  # unattributed
        e_other_chat = _event(ts="2026-10-27T11:15:00Z", chat_key="c_fourth", input_tokens=250_000)
        e_after = _event(ts="2026-10-27T11:30:01Z", total_tokens=500, input_tokens=0, output_tokens=0)

        providers = {
            "codex": [e_before, e_start, e_offset_b0, e_edge_b1, e_end_b1, e_after],
            "gemini": [e_other_chat, _event(ts="2026-10-27T10:15:00Z", chat_key="c_top", input_tokens=1_000_000),
                       _event(chat_key="c_top", total_tokens=1)],
        }
        providers_snapshot = deepcopy(providers)
        rows_snapshot = deepcopy(rows)

        result = build_usage_trend(start, end, providers, rows, False)

        self.assertEqual(providers, providers_snapshot)
        self.assertEqual(rows, rows_snapshot)
        self.assertFalse(result["partial"])
        self.assertEqual(
            result["top_chats"],
            [
                {"id": "c_top", "title": "Top", "codex_usd": 12.5},
                {"id": "c_tie_a", "title": "Tie A", "codex_usd": 5.0},
                {"id": "c_tie_b", "title": "Tie B", "codex_usd": 5.0},
            ],
        )

        buckets = result["buckets"]
        self.assertEqual(len(buckets), 2)
        self.assertEqual(buckets[0]["start_at"], "2026-10-27T10:00:00Z")
        self.assertEqual(buckets[0]["end_at"], "2026-10-27T11:00:00Z")
        self.assertEqual(buckets[1]["start_at"], "2026-10-27T11:00:00Z")
        self.assertEqual(buckets[1]["end_at"], "2026-10-27T11:30:00Z")

        # Bucket 0: e_start ($1.00 on c_top) + e_offset_b0 ($1.00 on c_tie_a)
        self.assertAlmostEqual(
            buckets[0]["total_usd"],
            estimate_usage_usd(e_start) + estimate_usage_usd(e_offset_b0),
        )
        self.assertEqual(list(buckets[0]["chat_usd"].keys()), ["c_top", "c_tie_a", "c_tie_b"])
        self.assertAlmostEqual(buckets[0]["chat_usd"]["c_top"], 1.0)
        self.assertAlmostEqual(buckets[0]["chat_usd"]["c_tie_a"], 1.0)
        self.assertEqual(buckets[0]["chat_usd"]["c_tie_b"], 0.0)

        # Bucket 1 contains $2.00 on c_top and $0.50 of unattributed Codex usage.
        self.assertAlmostEqual(buckets[1]["total_usd"], 2.5)
        self.assertAlmostEqual(buckets[1]["chat_usd"]["c_top"], 2.0)
        self.assertEqual(buckets[1]["chat_usd"]["c_tie_a"], 0.0)
        self.assertEqual(buckets[1]["chat_usd"]["c_tie_b"], 0.0)

    def test_partial_unpriced_mixing_zero_skip_and_invalid_timestamps(self):
        start = datetime(2026, 10, 27, 0, 0, 0, tzinfo=timezone.utc).timestamp()
        end = start + 3 * 3600  # 3 hourly buckets

        rows = [
            {"id": "c1", "title": "Chat 1", "codex": {"estimated_usd": 4.0}},
            {"id": "c2", "title": "Chat 2", "codex": {"estimated_usd": 2.0}},
        ]

        # Bucket 0: only unpriced event on c1 + all-zero event (must be skipped so bucket stays None)
        b0_unpriced = _event(
            ts="2026-10-27T00:10:00Z",
            chat_key="c1",
            total_tokens=400,
            input_tokens=0,
            output_tokens=0,
        )
        b0_zero_in_bucket = _event(ts="2026-10-27T00:20:00Z", chat_key="c1", total_tokens=0)
        zero_missing_ts = _event(total_tokens=0)

        # Bucket 1: priced ($1.00) + unpriced on c1; only unpriced on c2
        b1_priced_c1 = _event(
            ts="2026-10-27T01:15:00Z",
            chat_key="c1",
            input_tokens=500_000,
            total_tokens=0,  # incomplete total_tokens=0 but valid categories -> priced
        )
        b1_unpriced_c1 = _event(
            ts="2026-10-27T01:20:00Z",
            chat_key="c1",
            input_tokens=100,
            cached_tokens=200,  # inconsistent cached > input -> unpriced
            output_tokens=50,
        )
        b1_unpriced_c2 = _event(
            ts="2026-10-27T01:40:00Z",
            chat_key="c2",
            total_tokens=900,
            input_tokens=0,
            output_tokens=0,
        )

        # Bucket 2 remains empty -> 0.0
        res = build_usage_trend(
            start,
            end,
            {
                "codex": [b0_unpriced, b0_zero_in_bucket, zero_missing_ts, b1_priced_c1,
                          b1_unpriced_c1, b1_unpriced_c2],
                "gemini": [],
            },
            rows,
            False,
        )
        self.assertTrue(res["partial"])
        b0, b1, b2 = res["buckets"]

        self.assertIsNone(b0["total_usd"])
        self.assertIsNone(b0["chat_usd"]["c1"])
        self.assertEqual(b0["chat_usd"]["c2"], 0.0)

        self.assertAlmostEqual(b1["total_usd"], 1.0)
        self.assertAlmostEqual(b1["chat_usd"]["c1"], 1.0)
        self.assertIsNone(b1["chat_usd"]["c2"])

        self.assertEqual(b2["total_usd"], 0.0)
        self.assertEqual(b2["chat_usd"], {"c1": 0.0, "c2": 0.0})

        # Naive timestamp on non-zero event flags overall partial without corrupting empty buckets
        naive_res = build_usage_trend(
            start,
            start + 3600,
            {"codex": [_event(ts="2026-10-27T00:30:00", input_tokens=100)], "gemini": []},
            [],
            False,
        )
        self.assertTrue(naive_res["partial"])
        self.assertEqual(naive_res["buckets"][0]["total_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
