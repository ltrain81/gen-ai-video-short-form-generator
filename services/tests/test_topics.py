from shorts import topics as T


def _helpers():
    sents = []
    t = 0.0
    lines = (["intro to networking today"] * 10 + ["a NAT Gateway lets private subnets reach the internet"] * 6
             + ["filler talk"] * 10 + ["the Internet Gateway attaches to the VPC for public traffic"] * 6 + ["outro"] * 10)
    for line in lines:
        sents.append({"s": t, "e": t + 9.5, "text": line})
        t += 10
    return {"profile": {"primarySignal": "speech"}, "sentences": sents, "chapters": [], "scenes": [], "shots": [], "chunks": []}


def test_plan_fuses_keywords_snaps_and_does_not_overlap(monkeypatch):
    monkeypatch.setattr(T.search, "embed_query", lambda text: (_ for _ in ()).throw(RuntimeError("offline")))
    h = _helpers()
    asset = {"assetId": "a", "contentKey": "ck", "tenantId": "t", "durationSec": 420}
    plan = T.plan(asset, ["NAT Gateway", "Internet Gateway"], 45, h, verify=False)
    nat, igw = plan
    assert 95 <= nat["startSec"] <= 115 and nat["endSec"] <= 170, nat   # NAT sentences live at 100–160 s
    assert 255 <= igw["startSec"] <= 275, igw                           # IGW sentences live at 260–320 s
    assert nat["endSec"] <= igw["startSec"]                              # no overlap
    starts = {s["s"] for s in h["sentences"]}
    assert round(nat["startSec"] + 0.15, 2) in starts                    # snapped to a sentence start
    assert "transcript" in nat["signals"]


def test_best_window_respects_taken_ranges():
    score = [0.0] * 100
    for i in range(10, 30):
        score[i] = 1.0
    s, e, _ = T.best_window(score, 15, [(0, 25)])
    assert s >= 25
