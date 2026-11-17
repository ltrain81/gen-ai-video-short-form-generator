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
