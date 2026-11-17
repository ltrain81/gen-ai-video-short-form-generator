from unittest import mock

from shorts import batches


def test_plan_prefers_chapters_then_spaced_highlights():
    asset = {"contentKey": "ck", "durationSec": 1000}
    chapters = [{"start_sec": 0, "end_sec": 60, "title": "A"}, {"start_sec": 60, "end_sec": 500, "title": "B"},
                {"start_sec": 500, "end_sec": 1000, "title": "C"}]
    with mock.patch.object(batches, "_analysis", side_effect=lambda a, n: chapters if n == "chapters" else []):
        assert [p["title"] for p in batches.plan(asset, 2)] == ["B", "C"]  # two longest, in order
    scenes = [{"start_sec": t, "end_sec": t + 5, "highlight_score": 5 - (i % 3), "summary": f"s{t}"}
              for i, t in enumerate(range(0, 1000, 40))]
    with mock.patch.object(batches, "_analysis", side_effect=lambda a, n: scenes if n == "scenes" else []):
        picks = batches.plan(asset, 4)
        mids = [(p["startSec"] + p["endSec"]) / 2 for p in picks]
        assert len(picks) == 4 and all(b - a > 100 for a, b in zip(mids, mids[1:]))  # spaced, non-overlapping
