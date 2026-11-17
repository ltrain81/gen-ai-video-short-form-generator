"""Archive search: Marengo query embedding → S3 Vectors (tenant index) → ACL post-filter → group by asset."""
from __future__ import annotations

from collections import defaultdict

from . import acl, ddb, models, vectors
from .auth import Principal

MODALITIES = {"visual", "audio", "transcription", "description", "image", "page"}


def embed_query(text: str | None = None, image: bytes | None = None) -> list[float]:
    if text and image:
        return models.embed_text_image(text, data=image)
    if image:
        return models.embed_image(data=image)
    if text:
        return models.embed_text(text)
    raise ValueError("Provide text and/or image")


def search(p: Principal, text: str | None = None, image: bytes | None = None, modalities: list[str] | None = None,
           kinds: list[str] | None = None, asset_ids: list[str] | None = None, limit: int = 20,
           per_asset: int = 5) -> list[dict]:
    embedding = embed_query(text, image)
    flt: dict = {}
    mods = [m for m in (modalities or []) if m in MODALITIES]
    if mods:
        flt["modality"] = {"$in": mods}
    hits = vectors.query(p.tenant_id, embedding, top_k=100, filter=flt or None)

    accessible = acl.accessible_asset_ids(p)
    if asset_ids:
        accessible = {a: r for a, r in accessible.items() if a in set(asset_ids)}
    ck_assets: dict[str, list[str]] = {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        md = h.get("metadata", {})
        ck = md.get("contentKey")
        if not ck:
            continue
        if ck not in ck_assets:
            ck_assets[ck] = [a["assetId"] for a in ddb.query(f"CONTENT#{ck}", f"TENANT#{p.tenant_id}#", index="GSI3")
                             if a["assetId"] in accessible]
        score = round(1 - float(h.get("distance", 1)), 4)  # cosine distance → similarity
        for aid in ck_assets[ck]:
            grouped[aid].append({"startSec": md.get("startSec", 0), "endSec": md.get("endSec", 0),
                                 "modality": md.get("modality"), "scope": md.get("scope"), "page": md.get("page"),
                                 "score": score, "text": md.get("text")})
    if not grouped:
        return []
    assets = {a["assetId"]: a for a in ddb.batch_get((f"ASSET#{aid}", "META") for aid in grouped)}
    results = []
    for aid, segs in grouped.items():
        asset = assets.get(aid)
        if not asset or asset.get("deletedAt") or (kinds and asset.get("kind") not in kinds):
            continue
        segs.sort(key=lambda s: -s["score"])
        results.append({"asset": public_asset(asset, accessible[aid]), "score": segs[0]["score"],
                        "segments": _dedupe_segments(segs)[:per_asset]})
    results.sort(key=lambda r: -r["score"])
    return results[:limit]


def _dedupe_segments(segs: list[dict]) -> list[dict]:
    """Merge overlapping hits from different modalities of the same moment."""
    out: list[dict] = []
    for s in segs:
        dup = next((o for o in out if o.get("page") == s.get("page")
                    and s["startSec"] < o["endSec"] and o["startSec"] < s["endSec"]), None)
        if dup:
            dup.setdefault("modalities", [dup["modality"]])
            if s["modality"] not in dup["modalities"]:
                dup["modalities"].append(s["modality"])
            dup["text"] = dup.get("text") or s.get("text")
        else:
            out.append(dict(s))
    return out


PUBLIC_FIELDS = ("assetId", "kind", "status", "title", "aiTitle", "aiSummary", "filename", "contentType",
                 "durationSec", "width", "height", "fps", "pageCount", "hasAudio", "sizeBytes", "tags", "people",
                 "collectionIds", "ownerId", "createdAt", "updatedAt", "scope", "projectId", "aiGenerated",
                 "deletedAt", "error")


def public_asset(asset: dict, role: str | None = None) -> dict:
    out = {k: asset[k] for k in PUBLIC_FIELDS if k in asset}
    out["displayTitle"] = asset.get("title") or asset.get("aiTitle") or asset.get("filename")
    if role:
        out["role"] = role
    return out
