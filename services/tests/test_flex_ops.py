from shorts import timeline_ops as T


def _tl():
    return {"settings": {"aspect": "9:16", "width": 1080, "height": 1920, "fps": 30}, "pins": [], "markers": [], "meta": {},
            "tracks": [{"id": "v1", "kind": "video", "name": "Video", "items": []},
                       {"id": "t1", "kind": "text", "name": "Text", "items": []},
                       {"id": "c1", "kind": "caption", "name": "Captions", "items": []}]}


def test_tracks_lifecycle():
    tl, res = T.apply(_tl(), [{"op": "add_track", "kind": "overlay", "name": "PIP 2"},
                              {"op": "add_track", "kind": "video"},
                              {"op": "update_track", "trackId": "o1", "set": {"volume": 0.5, "hidden": True}}], "agent", {})
    assert all(r["ok"] for r in res), res
    kinds = [t["kind"] for t in tl["tracks"]]
    assert kinds.count("video") == 1 and kinds.count("overlay") == 2
    assert T.track(tl, "o1")["volume"] == 0.5
    tl, res = T.apply(tl, [{"op": "move_track", "trackId": "o2", "index": 1}, {"op": "remove_track", "trackId": "v1"}], "agent", {})
    assert res[0]["ok"] and not res[1]["ok"] and tl["tracks"][1]["id"] == "o2"


def test_custom_aspects():
    tl, res = T.apply(_tl(), [{"op": "settings", "set": {"aspect": "21:9"}}], "agent", {})
    assert res[0]["ok"] and tl["settings"]["width"] == 1920 and tl["settings"]["height"] == 822
    tl, res = T.apply(_tl(), [{"op": "settings", "set": {"aspect": "custom", "width": 1600, "height": 900}}], "agent", {})
    assert res[0]["ok"] and (tl["settings"]["width"], tl["settings"]["height"]) == (1600, 900)
    _, res = T.apply(_tl(), [{"op": "settings", "set": {"aspect": "1:9"}}], "agent", {})
    assert not res[0]["ok"]


def test_shapes_and_captions():
    tl, res = T.apply(_tl(), [{"op": "add_shape", "shape": "roundRect", "box": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.1},
                               "fill": "#000000", "opacity": 0.6, "start": 0, "duration": 3},
                              {"op": "add_caption", "text": "안녕 하세요 여러분", "start": 1, "duration": 3}], "agent", {})
    assert all(r["ok"] for r in res), res
    g = next(t for t in tl["tracks"] if t["name"] == "Graphics")
    assert tl["tracks"].index(g) < tl["tracks"].index(T.track(tl, "t1"))
    cap = T.track(tl, "c1")["items"][0]
    assert len(cap["words"]) == 3 and cap["start"] == 1.0


def test_move_validates_track_kind():
    tl, res = T.apply(_tl(), [{"op": "add_text", "text": "hi", "start": 0}], "agent", {})
    _, res = T.apply(tl, [{"op": "move", "itemId": res[0]["itemId"], "track": "c1", "start": 1}], "agent", {})
    assert not res[0]["ok"]


def test_non_magnetic_main_and_speed_curve():
    tl = _tl()
    assets = {"a": {"assetId": "a", "kind": "video", "durationSec": 60, "width": 1080, "height": 1920}}
    tl, res = T.apply(tl, [{"op": "update_track", "trackId": "v1", "set": {"magnetic": False}},
                           {"op": "add_clip", "assetId": "a", "srcIn": 0, "srcOut": 2, "start": 1},
                           {"op": "add_clip", "assetId": "a", "srcIn": 5, "srcOut": 9, "start": 6}], "agent", assets)
    assert all(r["ok"] for r in res), res
    items = T.track(tl, "v1")["items"]
    assert [i["start"] for i in items] == [1, 6]
    # speed ramp 1x → 2x over 4 s of source: duration between 2 s and 4 s
    tl, res = T.apply(tl, [{"op": "update", "itemId": items[1]["id"], "set": {"speedCurve": [{"t": 0, "v": 1}, {"t": 4, "v": 2}]}}], "agent", assets)
    d = T.track(tl, "v1")["items"][1]["duration"]
    assert res[0]["ok"] and 2.0 < d < 4.0


def test_static_image_kenburns():
    assets = {"i": {"assetId": "i", "kind": "image", "width": 100, "height": 100}}
    tl, res = T.apply(_tl(), [{"op": "add_clip", "assetId": "i", "kenBurns": None}], "agent", assets)
    kb = T.track(tl, "v1")["items"][0]["kenBurns"]
    assert kb["from"]["scale"] == kb["to"]["scale"] == 1.0


def test_lut_requires_accessible_asset():
    assets = {"a": {"assetId": "a", "kind": "video", "durationSec": 10}, "lut": {"assetId": "lut", "kind": "other"}}
    tl, res = T.apply(_tl(), [{"op": "add_clip", "assetId": "a", "srcIn": 0, "srcOut": 3}], "agent", assets)
    iid = res[0]["itemId"]
    _, ok = T.apply(tl, [{"op": "update", "itemId": iid, "set": {"effects": [{"type": "lut", "assetId": "lut"}]}}], "agent", assets)
    _, bad = T.apply(tl, [{"op": "update", "itemId": iid, "set": {"effects": [{"type": "lut", "assetId": "nope"}]}}], "agent", assets)
    assert ok[0]["ok"] and not bad[0]["ok"]
    assert T.asset_refs({"op": "update", "set": {"effects": [{"type": "lut", "assetId": "x"}]}}) == {"x"}


def test_src_at_mapping():
    it = {"type": "clip", "mediaKind": "video", "srcIn": 10, "srcOut": 14, "speed": 2}
    assert T.src_at(it, 1.0) == 12.0
    assert T.src_at({**it, "mode": "reverse"}, 1.0) == 12.0 and T.src_at({**it, "mode": "reverse"}, 0.5) == 13.0
    assert T.src_at({**it, "mode": "freeze"}, 1.5) == 10
