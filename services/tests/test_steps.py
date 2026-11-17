from shorts import steps


def test_summaries():
    assert steps.summarize("search_archive", {"text": "래시포드 골"}, {"results": [1, 2, 3]}) == "“래시포드 골” → 3"
    s = steps.summarize("edit_timeline", '{"ops":[{"op":"add_clip"},{"op":"add_clip"},{"op":"captions"}],"message":"hook"}',
                        [{"type": "text", "text": '{"durationSec": 41.7, "results": [{"ok": true}]}'}])
    assert s.startswith("add_clip×2, captions") and s.endswith("→ 41.7s")
    assert steps.summarize("look", {"times": [1, 61, 125, 200, 300]}) == "0:01, 1:01, 2:05, 3:20 (+1)"
    assert steps.summarize("unknown_tool", {"x": 1}) == ""
