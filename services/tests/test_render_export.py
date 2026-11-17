import json
from xml.dom import minidom

from shorts import export, projects, render, timeline_ops as T

ASSETS = {"v": {"assetId": "v", "kind": "video", "durationSec": 100, "width": 1920, "height": 1080, "filename": "match.mp4"}}


def _tl():
    tl, _ = T.apply(projects.empty_timeline("tl", "p", "9:16"), [
        {"op": "add_clip", "assetId": "v", "srcIn": 10, "srcOut": 14},
        {"op": "add_clip", "assetId": "v", "srcIn": 30, "srcOut": 36, "speed": 1.5},
        {"op": "add_text", "text": "Hello", "start": 0, "duration": 2},
        {"op": "captions", "transcripts": {"v": {"words": [{"s": 10.5, "e": 11, "w": "hi"}]}}},
    ], "agent", ASSETS)
    return tl


def test_crop_expression_follows_path():
    f = render.crop_filter(1920, 1080, 1080, 1920, [{"t": 10, "x": 0.3, "y": 0.5}, {"t": 12, "x": 0.7, "y": 0.5}], 10)
    assert f.startswith("crop=608:1080:") and "if(lt(t,2.000),0.3000,0.7000)" in f and f.endswith("scale=1080:1920:flags=lanczos")


def test_ass_and_subtitles(tmp_path):
    r = render.Renderer(_tl(), {}, tmp_path, "preview")
    assert r.ass(tmp_path / "x.ass")
    text = (tmp_path / "x.ass").read_text()
    assert "PlayResX: 540" in text and "Hello" in text and "hi" in text
    srt, vtt = r.subtitles()
    assert "00:00:00,500 --> " in srt and vtt.startswith("WEBVTT")


def test_exports_are_well_formed():
    tl = _tl()
    otio = json.loads(export.to_otio(tl, ASSETS, "t"))
    clips = otio["tracks"]["children"][0]["children"]
    assert [c["OTIO_SCHEMA"] for c in clips] == ["Clip.2", "Clip.2"] and clips[1]["effects"][0]["time_scalar"] == 1.5
    minidom.parseString(export.to_fcpxml(tl, ASSETS, "t").split("\n", 2)[2])  # skip XML decl + doctype
    edl = export.to_edl(tl, ASSETS, "t")
    assert "001  AX001" in edl and "* FROM CLIP NAME: match.mp4" in edl
