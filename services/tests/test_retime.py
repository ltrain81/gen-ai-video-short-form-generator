from shorts import timeline_ops as T


def _tl():
    return {"settings": {"aspect": "9:16", "width": 1080, "height": 1920, "fps": 30}, "pins": [],
            "tracks": [{"id": "v1", "kind": "video", "name": "Video", "items": []},
                       {"id": "c1", "kind": "caption", "name": "Captions", "items": [
                           {"id": "c1a", "type": "caption", "start": 2.0, "duration": 2.0, "preset": "clean", "style": {},
                            "position": {"y": 0.7}, "words": [{"s": 2.0, "e": 3.0, "w": "a"}, {"s": 3.0, "e": 4.0, "w": "b"}]}]}]}


def test_move_shifts_caption_words():
    tl = _tl()
    T.op_move(tl, {"itemId": "c1a", "start": 5.0}, "user", {})
    it = tl["tracks"][1]["items"][0]
    assert it["start"] == 5.0 and it["words"][0]["s"] == 5.0 and it["words"][1]["e"] == 7.0


def test_stretch_scales_caption_words():
    tl = _tl()
    T.op_trim(tl, {"itemId": "c1a", "duration": 4.0}, "user", {})
    it = tl["tracks"][1]["items"][0]
    assert it["duration"] == 4.0 and it["words"][1]["s"] == 4.0 and it["words"][1]["e"] == 6.0


def test_update_head_trim_retimes():
    tl = _tl()
    T.op_update(tl, {"itemId": "c1a", "set": {"start": 1.0, "duration": 3.0}}, "user", {})
    it = tl["tracks"][1]["items"][0]
    assert it["words"][0]["s"] == 1.0 and it["words"][1]["e"] == 4.0


def test_style_update_on_legacy_string_style():
    tl = _tl()
    tl["tracks"].append({"id": "t1", "kind": "text", "name": "Text", "items": [
        {"id": "x1", "type": "text", "text": "hi", "start": 0, "duration": 2, "style": "bold", "position": "center"}]})
    T.op_update(tl, {"itemId": "x1", "set": {"style": {"size": 0.08}, "position": {"x": 0.5, "y": 0.3}}}, "user", {})
    it = tl["tracks"][2]["items"][0]
    assert it["style"] == {"size": 0.08} and it["position"]["y"] == 0.3
