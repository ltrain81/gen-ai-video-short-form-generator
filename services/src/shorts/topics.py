"""Topic series planning: where in one video is each topic best covered? (one short per topic)

All signals are fused onto a 1-second timeline, weighted by the video's content profile:
  - Marengo vector search inside this video: transcription, visual, scene-description and audio embeddings
  - exact keyword hits in transcript sentences
  - Pegasus analysis: chapter and scene titles / summaries / on-screen text
Then: best window of the target length per topic (no overlap between topics) → snap to sentence boundaries
(speech-led) or shot changes (visual-led) → Pegasus confirms and refines the range.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from . import models, search, storage, vectors

log = logging.getLogger(__name__)

# signal weights per content profile (speech-led, visual-led, mixed)
WEIGHTS = {
    "speech": {"transcription": 1.3, "keyword": 1.4, "analysis": 0.9, "description": 0.7, "visual": 0.35, "audio": 0.2, "pegasus": 1.2},
    "visual": {"visual": 1.3, "description": 1.1, "analysis": 1.0, "keyword": 0.5, "transcription": 0.5, "audio": 0.5, "pegasus": 2.0},
    "mixed": {"transcription": 1.0, "keyword": 1.0, "analysis": 1.0, "description": 1.0, "visual": 0.9, "audio": 0.4, "pegasus": 1.6},
}

SCAN_WINDOW_SEC = 180.0
MAX_SCAN_WINDOWS = 40  # ≈ 2 h of video; longer videos rely on the other signals beyond that

SCAN_SCHEMA = {"type": "object", "properties": {"matches": {"type": "array", "items": {"type": "object", "properties": {
    "topic_index": {"type": "integer"}, "start_sec": {"type": "number"}, "end_sec": {"type": "number"},
    "confidence": {"type": "number"}, "evidence": {"type": "string"}},
    "required": ["topic_index", "start_sec", "end_sec", "confidence", "evidence"]}}}, "required": ["matches"]}


def pegasus_scan(chunks: list[dict], topics: list[str], lang: str) -> dict[int, list[dict]]:
    """Video understanding as a candidate source: one Pegasus pass per chunk lists every range where each topic
    happens (what is shown or said). Essential for visual topics ("a slam dunk") that embeddings can miss."""
    listing = "\n".join(f"{i}: {t}" for i, t in enumerate(topics))
    prompt = ("Watch this clip and list every time range where any of these topics happens (shown on screen, or "
              f"explained by a speaker):\n{listing}\nReturn matches with topic_index, start_sec and end_sec relative to the "
              f"clip start, confidence 0-1 and short evidence in {lang}. Only include real occurrences; an empty list is fine.")
    out: dict[int, list[dict]] = {i: [] for i in range(len(topics))}

    # Short windows scanned in parallel: one whole long chunk per call is far too slow.
    windows: list[tuple[dict, float, float]] = []
    for c in chunks[:24]:
        length = float(c["endSec"]) - float(c["startSec"])
        a = 0.0
        while a < length - 1:
            windows.append((c, a, min(length, a + SCAN_WINDOW_SEC)))
            a += SCAN_WINDOW_SEC
    windows = windows[:MAX_SCAN_WINDOWS]

    def one(w: tuple[dict, float, float]) -> list[dict]:
        c, a, b = w
        try:
            r = models.pegasus_ask(storage.s3_uri(c["key"]), prompt, a, b, SCAN_SCHEMA)
        except Exception:  # noqa: BLE001
            log.warning("pegasus scan failed for chunk %s", c.get("startSec"), exc_info=True)
            return []
        res = []
        for m in (r or {}).get("matches", []) if isinstance(r, dict) else []:
            try:
                # Pegasus may answer relative to the window or to the clip: normalize to the window.
                ms, me = float(m["start_sec"]), float(m["end_sec"])
                if ms < a - 1 or ms > b + 1:
                    ms, me = ms + a, me + a
                s0, e0 = ms + c["startSec"], me + c["startSec"]
                if e0 > s0 and 0 <= int(m["topic_index"]) < len(topics):
                    res.append({"i": int(m["topic_index"]), "startSec": s0, "endSec": e0,
                                "confidence": max(0.0, min(1.0, float(m.get("confidence", 0.5)))), "evidence": str(m.get("evidence", ""))[:160]})
            except (KeyError, TypeError, ValueError):
                continue
        return res

    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(one, windows):
            for m in res:
                out[m.pop("i")].append(m)
    return out
VECTOR_MODALITIES = ("transcription", "visual", "description", "audio")


def _terms(topic: str) -> list[str]:
    toks = [t.lower() for t in re.findall(r"[\w\-]+", topic) if len(t) > 1]
    return toks or [topic.lower()]


def _add(score: list[float], start: float, end: float, value: float) -> None:
    n = len(score)
    a, b = max(0, int(start)), min(n, max(int(start) + 1, int(end + 0.999)))
    for i in range(a, b):
        score[i] += value


def evidence_timeline(asset: dict, topic: str, profile: dict, helpers, scan: list[dict] | None = None) -> tuple[list[float], list[dict]]:
    """Per-second relevance of `topic` in this asset, plus the strongest pieces of evidence."""
    dur = int(float(asset.get("durationSec") or 0)) + 1
    score = [0.0] * dur
    ev: list[dict] = []
    w = WEIGHTS.get(profile.get("primarySignal", "mixed"), WEIGHTS["mixed"])
    ck = asset["contentKey"]
    # 1) Marengo: query embedding against this video's segments, per modality (normalized within modality)
    try:
        emb = search.embed_query(topic)
        for m in VECTOR_MODALITIES:
            hits = vectors.query(asset["tenantId"], emb, top_k=30,
                                 filter={"$and": [{"modality": {"$eq": m}}, {"contentKey": {"$eq": ck}}]})
            if not hits:
                continue
            sims = [1 - float(h.get("distance", 1)) for h in hits]
            lo, hi = min(sims), max(sims)
            for h, sim in zip(hits, sims):
                md = h.get("metadata", {})
                rel = (sim - lo) / (hi - lo) if hi > lo else 1.0
                v = w.get(m, 0.5) * (0.3 + rel) * sim
                _add(score, float(md.get("startSec", 0)), float(md.get("endSec", 0)), v)
                if rel > 0.8:
                    ev.append({"source": f"marengo:{m}", "t": md.get("startSec"), "text": (md.get("text") or "")[:120], "w": round(v, 3)})
    except Exception:  # noqa: BLE001 - other signals still work
        log.warning("vector search failed for topic %s", topic, exc_info=True)
    terms = _terms(topic)
    # 2) transcript keyword hits (exact terms; strong for named concepts like "NAT Gateway")
    for s in helpers["sentences"]:
        txt = (s.get("text") or "").lower()
        n = sum(1 for t in terms if t in txt)
        if n:
            v = w["keyword"] * (n / len(terms))
            _add(score, float(s["s"]) - 2, float(s["e"]) + 2, v)
            if n == len(terms):
                ev.append({"source": "transcript", "t": s["s"], "text": s["text"][:120], "w": round(v, 3)})
    # 3) Pegasus analysis: chapters and scenes whose title / summary / on-screen text mention the topic
    for kind, items in (("chapter", helpers["chapters"]), ("scene", helpers["scenes"])):
        for c in items:
            blob = " ".join(str(c.get(k) or "") for k in ("title", "summary", "on_screen_text", "description")).lower()
            n = sum(1 for t in terms if t in blob)
            if n:
                v = w["analysis"] * (n / len(terms)) * (0.6 if kind == "chapter" else 1.0)
                _add(score, float(c.get("start_sec", 0)), float(c.get("end_sec", 0)), v)
                ev.append({"source": f"pegasus:{kind}", "t": c.get("start_sec"), "text": str(c.get("title") or c.get("summary") or "")[:120], "w": round(v, 3)})
    # 4) Pegasus scan: occurrences found by watching the video
    for m in scan or []:
        v = w.get("pegasus", 1.5) * (0.4 + m["confidence"])
        _add(score, m["startSec"], m["endSec"], v)
        ev.append({"source": "pegasus:scan", "t": round(m["startSec"], 1), "text": m["evidence"], "w": round(v, 3)})
    ev.sort(key=lambda e: -e["w"])
    return score, ev[:8]


def best_window(score: list[float], length: float, taken: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Highest-scoring window of `length` seconds that does not overlap `taken` (sliding sum)."""
    n, L = len(score), max(5, int(length))
    if n <= L:
        return 0.0, float(n), sum(score)
    blocked = [False] * n
    for a, b in taken:
        for i in range(max(0, int(a)), min(n, int(b) + 1)):
            blocked[i] = True
    best, best_i, cur = -1.0, 0, sum(score[:L])
    bad = sum(blocked[:L])
    for i in range(0, n - L + 1):
        if i:
            cur += score[i + L - 1] - score[i - 1]
            bad += blocked[i + L - 1] - blocked[i - 1]
        if not bad and cur > best:
            best, best_i = cur, i
    if best < 0:  # everything overlaps: allow overlap rather than fail
        return best_window(score, length, [])
    return float(best_i), float(best_i + L), best


def snap(start: float, end: float, profile: dict, sentences: list[dict], shots: list[float], dur: float) -> tuple[float, float]:
    if profile.get("primarySignal") in ("speech", "mixed") and sentences:
        s0 = max((s["s"] for s in sentences if s["s"] <= start + 1.5), default=start)
        e0 = min((s["e"] for s in sentences if s["e"] >= end - 1.5), default=end)
        if e0 - s0 >= 3:
            return round(max(0.0, s0 - 0.15), 2), round(min(dur, e0 + 0.25), 2)
    if shots:
        s0 = min(shots, key=lambda x: abs(x - start)) if any(abs(x - start) < 3 for x in shots) else start
        e0 = min(shots, key=lambda x: abs(x - end)) if any(abs(x - end) < 3 for x in shots) else end
        if e0 - s0 >= 3:
            return round(s0, 2), round(e0, 2)
    return round(start, 2), round(end, 2)


PEGASUS_SCHEMA = {"type": "object", "properties": {
    "found": {"type": "boolean"}, "start_sec": {"type": "number"}, "end_sec": {"type": "number"},
    "confidence": {"type": "number"}, "evidence": {"type": "string"}},
    "required": ["found", "start_sec", "end_sec", "confidence", "evidence"]}


def pegasus_refine(asset: dict, chunks: list[dict], topic: str, start: float, end: float, length: float, lang: str) -> dict | None:
    """Ask Pegasus for the exact range inside [start-20, end+20] where the topic is explained/shown."""
    lo, hi = max(0.0, start - 20), end + 20
    chunk = next((c for c in chunks if c["startSec"] <= lo < c["endSec"]), None) or next(
        (c for c in chunks if c["startSec"] < hi and c["endSec"] > lo), None)
    if not chunk:
        return None
    off = chunk["startSec"]
    a, b = max(0.0, lo - off), min(chunk["endSec"], hi) - off
    prompt = (f"Within this clip, find the single best continuous segment (about {int(length)} seconds) where '{topic}' is "
              "explained or shown — a self-contained piece that would work as a short on its own. Return start_sec and "
              "end_sec relative to the clip start, confidence 0-1, and the evidence (what is said/shown) in "
              f"{lang}. If the topic is not present, found=false.")
    try:
        r = models.pegasus_ask(storage.s3_uri(chunk["key"]), prompt, a, b, PEGASUS_SCHEMA)
    except Exception:  # noqa: BLE001
        log.warning("pegasus refine failed", exc_info=True)
        return None
    if not isinstance(r, dict) or not r.get("found"):
        return {"found": False}
    s0, e0 = float(r["start_sec"]) + off, float(r["end_sec"]) + off
    if e0 - s0 < 3 or s0 < lo - 5 or e0 > hi + 5:
        return None
    return {"found": True, "startSec": s0, "endSec": e0, "confidence": float(r.get("confidence", 0.5)), "evidence": r.get("evidence", "")}


def plan(asset: dict, topics: list[str], length: float, helpers: dict, verify: bool = True, lang: str = "Korean") -> list[dict]:
    profile = helpers["profile"]
    dur = float(asset.get("durationSec") or 0)
    scans = pegasus_scan(helpers["chunks"], topics, lang) if helpers.get("chunks") else {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        timelines = list(ex.map(lambda i: evidence_timeline(asset, topics[i], profile, helpers, scans.get(i)), range(len(topics))))
    # Strongest topics claim their windows first; later topics avoid overlap.
    order = sorted(range(len(topics)), key=lambda i: -max(timelines[i][0] or [0]))
    taken: list[tuple[float, float]] = []
    picks: dict[int, dict] = {}
    for i in order:
        score, ev = timelines[i]
        s, e, total = best_window(score, length, taken)
        s, e = snap(s, e, profile, helpers["sentences"], helpers["shots"], dur)
        picks[i] = {"topic": topics[i], "startSec": s, "endSec": e, "score": round(total / max(1.0, e - s), 3),
                    "peak": round(max(score) if score else 0, 3), "evidence": ev,
                    "signals": sorted({x["source"].split(":")[0] for x in ev})}
        taken.append((s, e))
    if verify and helpers.get("chunks"):
        def refine(i: int) -> tuple[int, dict | None]:
            p = picks[i]
            hits = [m for m in scans.get(i, []) if m["startSec"] < p["endSec"] and m["endSec"] > p["startSec"] and m["confidence"] >= 0.7]
            if hits:  # the scan already saw it inside this window — no extra Pegasus call
                best = max(hits, key=lambda m: m["confidence"])
                return i, {"found": True, "startSec": p["startSec"], "endSec": p["endSec"], "confidence": best["confidence"],
                           "evidence": best["evidence"]}
            r = pegasus_refine(asset, helpers["chunks"], p["topic"], p["startSec"], p["endSec"], length, lang)
            if r and r.get("found") is False:
                # Not there: try the next-best window once (excluding the rejected one and the other topics).
                others = [(q["startSec"], q["endSec"]) for j, q in picks.items() if j != i]
                s2, e2, _ = best_window(timelines[i][0], length, others + [(p["startSec"], p["endSec"])])
                r2 = pegasus_refine(asset, helpers["chunks"], p["topic"], s2, e2, length, lang)
                if r2 and r2.get("found"):
                    return i, r2
            return i, r
        with ThreadPoolExecutor(max_workers=4) as ex:
            for i, r in ex.map(refine, list(picks)):
                p = picks[i]
                if r and r.get("found") and r.get("confidence", 0) >= 0.5:
                    s, e = snap(r["startSec"], r["endSec"], profile, helpers["sentences"], helpers["shots"], dur)
                    # Never collide with another topic's final range.
                    if not any(s < q["endSec"] and e > q["startSec"] for j, q in picks.items() if j != i):
                        p.update(startSec=s, endSec=e)
                    p.update(confidence=round(r["confidence"], 2), pegasus=r.get("evidence", "")[:200])
                    p["signals"] = sorted(set(p["signals"]) | {"pegasus"})
                elif r and r.get("found") is False:
                    p["confidence"] = 0.2
                    p["warning"] = "Pegasus could not confirm this topic — review the range"
    out = [picks[i] for i in range(len(topics))]
    for p in out:
        p.setdefault("confidence", round(min(0.9, 0.3 + p["peak"] / 3), 2))
        p["durationSec"] = round(p["endSec"] - p["startSec"], 2)
    return out
