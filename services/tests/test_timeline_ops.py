from shorts import projects, timeline_ops as T

ASSETS = {"v": {"kind": "video", "durationSec": 100, "width": 1920, "height": 1080},
          "img": {"kind": "image"}, "m": {"kind": "audio", "durationSec": 60}}


def base():
    return projects.empty_timeline("tl", "p", "9:16")


def test_magnetic_track_split_trim_remove():
    tl, r = T.apply(base(), [
        {"op": "add_clip", "assetId": "v", "srcIn": 10, "srcOut": 20, "reason": "hook"},
        {"op": "add_clip", "assetId": "img", "duration": 2},
        {"op": "add_clip", "assetId": "v", "srcIn": 50, "srcOut": 55, "index": 0},
    ], "agent", ASSETS)
    assert all(x["ok"] for x in r)
    v = T.main_track(tl)["items"]
    assert [(i["start"], i["duration"]) for i in v] == [(0.0, 5.0), (5.0, 10.0), (15.0, 2.0)]
    assert v[0]["reframe"] == {"mode": "auto"}  # 16:9 source on 9:16 canvas
    tl, r = T.apply(tl, [{"op": "split", "itemId": v[1]["id"], "at": 8}], "agent", ASSETS)
    v = T.main_track(tl)["items"]
    assert [(i["srcIn"], i["srcOut"]) for i in v[1:3]] == [(10, 13), (13, 20)]
    tl, r = T.apply(tl, [{"op": "remove", "itemId": v[0]["id"]}, {"op": "trim", "itemId": v[1]["id"], "srcIn": 11}], "agent", ASSETS)
    v = T.main_track(tl)["items"]
    assert [(i["start"], i["duration"]) for i in v] == [(0.0, 2.0), (2.0, 7.0), (9.0, 2.0)]
    assert T.duration(tl) == 11.0


def test_pins_block_agent_but_not_user():
    tl, r = T.apply(base(), [{"op": "add_clip", "assetId": "v", "srcIn": 0, "srcOut": 10}], "user", ASSETS)
    iid = r[0]["itemId"]
    tl, _ = T.apply(tl, [{"op": "pin", "path": f"items/{iid}/srcIn"}], "user", ASSETS)
    tl2, r = T.apply(tl, [{"op": "trim", "itemId": iid, "srcIn": 3}, {"op": "trim", "itemId": iid, "srcOut": 8}], "agent", ASSETS)
    assert [x["ok"] for x in r] == [False, True]
    assert T.main_track(tl2)["items"][0]["srcIn"] == 0 and T.main_track(tl2)["items"][0]["srcOut"] == 8
    tl3, r = T.apply(tl, [{"op": "trim", "itemId": iid, "srcIn": 3}], "user", ASSETS)
    assert r[0]["ok"] and T.main_track(tl3)["items"][0]["srcIn"] == 3
    _, r = T.apply(tl, [{"op": "pin", "path": "settings/aspect"}], "agent", ASSETS)
    assert not r[0]["ok"]


def test_captions_map_source_words_to_timeline():
    tl, _ = T.apply(base(), [{"op": "add_clip", "assetId": "v", "srcIn": 10, "srcOut": 14},
                             {"op": "add_clip", "assetId": "v", "srcIn": 30, "srcOut": 32}], "agent", ASSETS)
    words = [{"s": 10.5, "e": 11.0, "w": "hello"}, {"s": 11.1, "e": 11.5, "w": "world."}, {"s": 12.0, "e": 12.4, "w": "next"},
             {"s": 20, "e": 21, "w": "dropped"}, {"s": 30.2, "e": 30.6, "w": "end"}]
    tl, r = T.apply(tl, [{"op": "captions", "preset": "bold", "transcripts": {"v": {"words": words}}}], "agent", ASSETS)
    caps = T.track(tl, "c1")["items"]
    assert r[0]["captions"] == 3
    assert [" ".join(w["w"] for w in c["words"]) for c in caps] == ["hello world.", "next", "end"]
    assert caps[2]["start"] == 4.2  # 4s first clip + 0.2s into second


def test_bad_op_is_rejected_atomically():
    tl, r = T.apply(base(), [{"op": "add_clip", "assetId": "nope"}, {"op": "explode"}], "agent", ASSETS)
    assert [x["ok"] for x in r] == [False, False]
    assert T.duration(tl) == 0
