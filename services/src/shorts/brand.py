"""Brand kits (per workspace or per group): TENANT#{t} / BRAND#{id}.

  {name, groupId?, logoAssetId, logoCorner(tl|tr|bl|br), logoScale, logoOpacity, introAssetId, outroAssetId,
   font, textColor, accentColor, captionPreset}
Visible to the whole workspace (groupId=None) or to members of that group; managed by admins and the creator.
"""
from __future__ import annotations

import uuid

from . import acl, ddb
from .auth import Forbidden, Principal
from .http import NotFound

FIELDS = ("name", "groupId", "logoAssetId", "logoCorner", "logoScale", "logoOpacity", "introAssetId", "outroAssetId",
          "font", "textColor", "accentColor", "captionPreset")


def _visible(p: Principal, kit: dict) -> bool:
    return not kit.get("groupId") or kit["groupId"] in p.groups or p.is_tenant_admin or kit.get("createdBy") == p.sub


def list_kits(p: Principal) -> list[dict]:
    return [_public(k) for k in ddb.query(f"TENANT#{p.tenant_id}", "BRAND#") if _visible(p, k)]


def _public(k: dict) -> dict:
    return {"brandKitId": k["SK"].removeprefix("BRAND#"), **{f: k.get(f) for f in FIELDS}, "createdBy": k.get("createdBy")}


def resolve(p: Principal, kit_id: str | None) -> dict:
    if not kit_id:
        raise ValueError("No brand kit set on the project; pass brandKitId")
    k = ddb.get(f"TENANT#{p.tenant_id}", f"BRAND#{kit_id}")
    if not k or not _visible(p, k):
        raise NotFound("Brand kit not found")
    return _public(k)


def put(p: Principal, kit_id: str | None, body: dict) -> dict:
    kit_id = kit_id or "b" + uuid.uuid4().hex[:12]
    existing = ddb.get(f"TENANT#{p.tenant_id}", f"BRAND#{kit_id}")
    if existing and existing.get("createdBy") != p.sub and not p.is_tenant_admin:
        raise Forbidden("Only the creator or an admin can edit this brand kit")
    accessible = acl.accessible_asset_ids(p)
    for f in ("logoAssetId", "introAssetId", "outroAssetId"):
        if body.get(f) and body[f] not in accessible:
            raise Forbidden(f"No access to asset {body[f]}")
    if body.get("groupId") and body["groupId"] not in p.groups and not p.is_tenant_admin:
        raise Forbidden("You are not a member of that group")
    if body.get("logoCorner") and body["logoCorner"] not in {"tl", "tr", "bl", "br"}:
        raise ValueError("logoCorner must be tl|tr|bl|br")
    item = {"PK": f"TENANT#{p.tenant_id}", "SK": f"BRAND#{kit_id}", "createdBy": (existing or {}).get("createdBy", p.sub),
            "updatedAt": ddb.now_ms(), **{f: body.get(f, (existing or {}).get(f)) for f in FIELDS}}
    item["name"] = str(item.get("name") or "Brand kit")[:80]
    ddb.put(item)
    return _public(item)


def delete(p: Principal, kit_id: str) -> None:
    k = ddb.get(f"TENANT#{p.tenant_id}", f"BRAND#{kit_id}")
    if not k:
        raise NotFound("Brand kit not found")
    if k.get("createdBy") != p.sub and not p.is_tenant_admin:
        raise Forbidden("Only the creator or an admin can delete")
    ddb.delete(f"TENANT#{p.tenant_id}", f"BRAND#{kit_id}")
