"""Projects and versioned timelines.

  PROJ#{p}  META        project (tenantId, ownerId, title, currentVersion, lock)   GSI2: TENANT#{t} / PROJ#{updatedAt}#{p}
  PROJ#{p}  VER#{n:06d} version record (author, message, createdAt)
  PROJ#{p}  GRANT#...   sharing (same roles as assets)
  S3: projects/{t}/{p}/timeline/{n:06d}.json   full snapshot per version (every agent turn / user save)
"""
from __future__ import annotations

import json
import uuid

from . import acl, ddb, storage
from .auth import Forbidden, Principal
from .config import get_config
from .http import Conflict, NotFound

LOCK_TTL_MS = 10 * 60 * 1000
ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350), "4:3": (1440, 1080)}


def empty_timeline(timeline_id: str, project_id: str, aspect: str = "9:16") -> dict:
    from . import timeline_ops
    try:
        aspect, w, h = timeline_ops._canvas_for(aspect, {})
    except timeline_ops.OpError:
        aspect, (w, h) = "9:16", ASPECTS["9:16"]
    return {
        "version": 1, "id": timeline_id, "projectId": project_id,
        "settings": {"aspect": aspect, "width": w, "height": h, "fps": 30, "background": "#000000", "loudness": -14,
                     "safeZone": "shorts" if w < h else "none"},
        "tracks": [
            {"id": "v1", "kind": "video", "name": "Video", "items": [], "audioRole": "dialogue"},
            {"id": "o1", "kind": "overlay", "name": "B-roll", "items": []},
            {"id": "t1", "kind": "text", "name": "Text", "items": []},
            {"id": "c1", "kind": "caption", "name": "Captions", "items": []},
            {"id": "a1", "kind": "audio", "name": "Music", "items": [], "audioRole": "music", "volume": 0.6},
            {"id": "a2", "kind": "audio", "name": "Voiceover", "items": [], "audioRole": "voiceover"},
        ],
        "markers": [], "meta": {}, "pins": [],
    }


def _key(tenant: str, pid: str, version: int) -> str:
    return f"projects/{tenant}/{pid}/timeline/{version:06d}.json"


def project_role(p: Principal, project: dict | None) -> str | None:
    if not project or project.get("tenantId") != p.tenant_id or project.get("deletedAt"):
        return None
    if project.get("ownerId") == p.sub or p.is_tenant_admin:
        return "owner"
    ids, now = set(p.principal_ids()), ddb.now_ms()
    return acl.max_role([g["role"] for g in ddb.query(f"PROJ#{project['projectId']}", "GRANT#")
                         if g["principal"] in ids and (not g.get("expiresAt") or g["expiresAt"] > now)])


def require_project(p: Principal, project_id: str, action: str) -> tuple[dict, str]:
    project = ddb.get(f"PROJ#{project_id}", "META", consistent=True)
    role = project_role(p, project)
    if role is None:
        raise NotFound("Project not found")
    need = {"view": "user", "edit": "editor", "share": "owner", "delete": "owner"}[action]
    if acl.ROLE_RANK[role] < acl.ROLE_RANK[need]:
        raise Forbidden(f"'{action}' requires role {need}")
    return project, role


def create(p: Principal, title: str, aspect: str = "9:16", synopsis: str = "", asset_ids: list[str] | None = None) -> dict:
    pid = "p" + uuid.uuid4().hex[:20]
    now = ddb.now_ms()
    timeline = empty_timeline("tl" + uuid.uuid4().hex[:12], pid, aspect)
    _write_snapshot(p.tenant_id, pid, 0, timeline)
    item = {"PK": f"PROJ#{pid}", "SK": "META", "projectId": pid, "tenantId": p.tenant_id, "ownerId": p.sub,
            "title": title, "synopsis": synopsis, "aspect": aspect, "currentVersion": 0, "createdAt": now,
            "assetIds": _usable(p, asset_ids or []),
            "updatedAt": now, "GSI2PK": f"TENANT#{p.tenant_id}", "GSI2SK": f"PROJ#{now}#{pid}"}
    ddb.put(item)
    ddb.put({"PK": f"PROJ#{pid}", "SK": f"VER#{0:06d}", "version": 0, "author": "system", "message": "created",
             "createdAt": now})
    acl.grant(f"PROJ#{pid}", f"USER#{p.sub}", "owner", p.tenant_id, p.sub)
    return item


def _usable(p: Principal, asset_ids: list[str]) -> list[str]:
    accessible = acl.accessible_asset_ids(p)
    denied = [a for a in asset_ids if a not in accessible]
    if denied:
        raise Forbidden(f"No access to assets: {', '.join(denied[:5])}")
    return list(dict.fromkeys(asset_ids))[:200]


def attach_assets(p: Principal, project: dict, asset_ids: list[str]) -> list[str]:
    merged = list(dict.fromkeys([*project.get("assetIds", []), *_usable(p, asset_ids)]))[:200]
    ddb.update(f"PROJ#{project['projectId']}", "META", {"assetIds": merged, "updatedAt": ddb.now_ms()})
    return merged


def _write_snapshot(tenant: str, pid: str, version: int, timeline: dict) -> None:
    storage.s3().put_object(Bucket=get_config().media_bucket, Key=_key(tenant, pid, version),
                            Body=json.dumps(timeline, ensure_ascii=False).encode(), ContentType="application/json")


def load_timeline(project: dict, version: int | None = None) -> dict:
    v = project["currentVersion"] if version is None else version
    body = storage.s3().get_object(Bucket=get_config().media_bucket,
                                   Key=_key(project["tenantId"], project["projectId"], v))["Body"].read()
    return json.loads(body)


def lock_state(project: dict) -> dict | None:
    lock = project.get("lock")
    if lock and lock.get("expiresAt", 0) > ddb.now_ms():
        return lock
    return None


def save(p: Principal, project: dict, timeline: dict, base_version: int, author: str, message: str = "",
         holder: str | None = None) -> dict:
    """Optimistic-concurrency save. `author` = 'user' | 'agent'. Agent saves must hold the lock."""
    lock = lock_state(project)
    if lock and lock.get("holder") != holder:
        raise Conflict(f"Timeline is locked by {lock.get('holder')}")
    _validate(p, timeline, project["projectId"])
    new_version = base_version + 1
    _write_snapshot(project["tenantId"], project["projectId"], new_version, timeline)
    cover, dur = summary_of(timeline)
    try:
        updated = ddb.update_raw(
            f"PROJ#{project['projectId']}", "META",
            "SET currentVersion = :n, updatedAt = :now, GSI2SK = :g, coverAssetId = :c, coverAt = :ca, durationSec = :d",
            values={":n": new_version, ":base": base_version, ":now": ddb.now_ms(),
                    ":g": f"PROJ#{ddb.now_ms()}#{project['projectId']}", ":c": cover[0] or "", ":ca": cover[1], ":d": dur},
            condition="currentVersion = :base",
        )
    except Exception as exc:
        if ddb.conditional_failed(exc):
            raise Conflict("Timeline changed since your base version; reload and retry")
        raise
    ddb.put({"PK": f"PROJ#{project['projectId']}", "SK": f"VER#{new_version:06d}", "version": new_version,
             "author": author, "actor": p.sub, "message": message[:500], "createdAt": ddb.now_ms()})
    return updated


def acquire_lock(project: dict, holder: str) -> bool:
    now = ddb.now_ms()
    try:
        ddb.update_raw(f"PROJ#{project['projectId']}", "META", "SET #l = :lock",
                       names={"#l": "lock"}, values={":lock": {"holder": holder, "expiresAt": now + LOCK_TTL_MS},
                                                     ":now": now, ":h": holder},
                       condition="attribute_not_exists(#l) OR #l.expiresAt < :now OR #l.holder = :h")
        return True
    except Exception as exc:
        if ddb.conditional_failed(exc):
            return False
        raise


def release_lock(project_id: str, holder: str) -> None:
    try:
        ddb.update_raw(f"PROJ#{project_id}", "META", "REMOVE #l", names={"#l": "lock"}, values={":h": holder},
                       condition="#l.holder = :h")
    except Exception as exc:
        if not ddb.conditional_failed(exc):
            raise


def _validate(p: Principal, timeline: dict, project_id: str) -> None:
    if timeline.get("version") != 1 or timeline.get("projectId") != project_id:
        raise ValueError("Invalid timeline (version/projectId)")
    if not isinstance(timeline.get("tracks"), list):
        raise ValueError("Invalid timeline (tracks)")
    asset_ids = {i["assetId"] for t in timeline["tracks"] for i in t.get("items", []) if i.get("assetId")}
    asset_ids |= {i["sourceAssetId"] for t in timeline["tracks"] for i in t.get("items", []) if i.get("sourceAssetId")}
    if asset_ids:
        accessible = acl.accessible_asset_ids(p)
        denied = [a for a in asset_ids if a not in accessible]
        if denied:
            raise Forbidden(f"No access to assets: {', '.join(sorted(denied)[:5])}")


def summary_of(timeline: dict) -> tuple[tuple[str | None, float], float]:
    """(cover asset + source time of the first main clip, duration) — shown on project cards."""
    from . import timeline_ops
    main = next((t for t in timeline.get("tracks", []) if t.get("kind") == "video"), {"items": []})
    first = next((i for i in main["items"] if i.get("type") == "clip"), None)
    cover = (first["assetId"], round(float(first.get("srcIn", 0)) + min(1.0, float(first.get("duration", 0)) / 3), 2)) if first else (None, 0.0)
    return cover, round(timeline_ops.duration(timeline), 2)


LIST_FIELDS = ("projectId", "title", "aspect", "currentVersion", "createdAt", "updatedAt", "ownerId", "synopsis",
               "assetIds", "folderId", "starred", "archived", "batchId", "recipe", "durationSec", "coverAssetId",
               "coverAt", "lastMessage", "lastMessageAt", "duplicatedFrom")


def list_for(p: Principal) -> list[dict]:
    out = []
    for proj in ddb.query(f"TENANT#{p.tenant_id}", "PROJ#", index="GSI2", forward=False):
        role = project_role(p, proj)
        if role:
            out.append({k: proj.get(k) for k in LIST_FIELDS} | {"role": role})
    return out


def duplicate(p: Principal, project: dict, title: str | None = None, version: int | None = None) -> dict:
    """A new project starting from (any version of) this one — e.g. a 16:9 cut, an A/B alternative."""
    tl = load_timeline(project, version)
    new = create(p, (title or f"{project.get('title', 'Untitled')} (copy)")[:200], tl["settings"].get("aspect", "9:16"),
                 project.get("synopsis", ""), [a for a in project.get("assetIds", [])])
    tl = {**tl, "projectId": new["projectId"], "pins": []}
    _write_snapshot(p.tenant_id, new["projectId"], 1, tl)
    cover, dur = summary_of(tl)
    ddb.update(f"PROJ#{new['projectId']}", "META", {
        "currentVersion": 1, "folderId": project.get("folderId"), "duplicatedFrom": {
            "projectId": project["projectId"], "version": version or project.get("currentVersion")},
        "coverAssetId": cover[0], "coverAt": cover[1], "durationSec": dur,
        **{k: project[k] for k in ("recipe", "recipeParams", "brandKitId") if project.get(k)}})
    ddb.put({"PK": f"PROJ#{new['projectId']}", "SK": f"VER#{1:06d}", "version": 1, "author": "user", "actor": p.sub,
             "message": f"duplicated from {project.get('title', '')} v{version or project.get('currentVersion')}",
             "createdAt": ddb.now_ms()})
    return {"projectId": new["projectId"]}


# ------------------------------------------------------------------ folders (workspace-wide, nestable)
def folders(tenant_id: str) -> list[dict]:
    return [{k: f.get(k) for k in ("folderId", "name", "parentId", "ownerId", "createdAt", "color")}
            for f in ddb.query(f"TENANT#{tenant_id}", "FOLDER#")]


def save_folder(p: Principal, body: dict, folder_id: str | None = None) -> dict:
    fid = folder_id or "f" + uuid.uuid4().hex[:12]
    existing = ddb.get(f"TENANT#{p.tenant_id}", f"FOLDER#{fid}") if folder_id else None
    if folder_id and not existing:
        raise ValueError("Unknown folder")
    parent = body.get("parentId", (existing or {}).get("parentId"))
    if parent:
        all_ = {f["folderId"]: f for f in folders(p.tenant_id)}
        if parent not in all_:
            raise ValueError("Unknown parent folder")
        cur = parent
        while cur:  # no cycles
            if cur == fid:
                raise ValueError("A folder cannot be moved into itself")
            cur = all_.get(cur, {}).get("parentId")
    item = {"PK": f"TENANT#{p.tenant_id}", "SK": f"FOLDER#{fid}", "folderId": fid,
            "name": str(body.get("name", (existing or {}).get("name", "New folder")))[:80],
            "parentId": parent or None, "color": body.get("color", (existing or {}).get("color")),
            "ownerId": (existing or {}).get("ownerId", p.sub), "createdAt": (existing or {}).get("createdAt", ddb.now_ms())}
    ddb.put(item)
    return {k: item[k] for k in ("folderId", "name", "parentId", "color", "ownerId", "createdAt")}


def delete_folder(p: Principal, folder_id: str) -> None:
    """Contents move up to the parent folder (nothing is deleted)."""
    f = ddb.get(f"TENANT#{p.tenant_id}", f"FOLDER#{folder_id}")
    if not f:
        return
    parent = f.get("parentId")
    for sub in folders(p.tenant_id):
        if sub.get("parentId") == folder_id:
            ddb.update(f"TENANT#{p.tenant_id}", f"FOLDER#{sub['folderId']}", {"parentId": parent})
    for proj in ddb.query(f"TENANT#{p.tenant_id}", "PROJ#", index="GSI2"):
        if proj.get("folderId") == folder_id:
            ddb.update(f"PROJ#{proj['projectId']}", "META", {"folderId": parent})
    ddb.delete(f"TENANT#{p.tenant_id}", f"FOLDER#{folder_id}")


def versions(project_id: str) -> list[dict]:
    return [{k: v.get(k) for k in ("version", "author", "actor", "message", "createdAt")}
            for v in ddb.query(f"PROJ#{project_id}", "VER#", forward=False, limit=200)]
