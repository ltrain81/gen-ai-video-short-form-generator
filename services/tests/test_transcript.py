from shorts.tasks import normalize_transcript


def test_normalize_transcript_offsets_and_sentences():
    raw = {"results": {"language_codes": [{"language_code": "ko-KR"}], "items": [
        {"type": "pronunciation", "start_time": "0.5", "end_time": "0.9", "alternatives": [{"content": "안녕하세요"}]},
        {"type": "punctuation", "alternatives": [{"content": "."}]},
        {"type": "pronunciation", "start_time": "1.0", "end_time": "1.3", "alternatives": [{"content": "Next"}]},
        {"type": "pronunciation", "start_time": "1.4", "end_time": "1.8", "alternatives": [{"content": "step"}]},
    ]}}
    out = normalize_transcript(raw, offset=100)
    assert out["languages"] == ["ko-KR"]
    assert out["words"][0] == {"s": 100.5, "e": 100.9, "w": "안녕하세요.", "spk": None}
    assert [s["text"] for s in out["sentences"]] == ["안녕하세요.", "Next step"]
    assert out["sentences"][1]["s"] == 101.0
