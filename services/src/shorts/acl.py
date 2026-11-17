"""Asset / collection access control.

Roles (high → low): owner > editor > user
  user   : find in search, preview proxies, use in edits/renders. Cannot download originals.
  editor : + download original, edit metadata/tags, add to collections
  owner  : + share/revoke, delete
Tenant admins act as owner on every asset of their tenant.
Tenants are fully isolated: a grant can never cross tenants.
"""
from __future__ import annotations

from . import ddb
from .auth import Forbidden, Principal

ROLE_RANK = {"user": 1, "editor": 2, "owner": 3}
ACTION_MIN_ROLE = {
    "view": "user",
    "use": "user",
    "download": "editor",
    "edit": "editor",
    "share": "owner",
    "delete": "owner",
}
PRINCIPAL_TYPES = {"USER", "GROUP", "TENANT"}


def _alive(grant: dict, now: int) -> bool:
    exp = grant.get("expiresAt")
    return not exp or exp > now


def max_role(roles: list[str]) -> str | None:
    roles = [r for r in roles if r in ROLE_RANK]
    return max(roles, key=ROLE_RANK.__getitem__) if roles else None


def effective_role(p: Principal, asset: dict) -> str | None:
    if asset.get("tenantId") != p.tenant_id:
        return None
    if asset.get("ownerId") == p.sub or p.is_tenant_admin:
        return "owner"
    ids = set(p.principal_ids())
    now = ddb.now_ms()
    roles: list[str] = []
    resources = [f"ASSET#{asset['assetId']}", *(f"COLL#{c}" for c in asset.get("collectionIds", []))]
    for res in resources:
        for g in ddb.query(res, "GRANT#"):
            if g["principal"] in ids and _alive(g, now):
                roles.append(g["role"])
    return max_role(roles)


def require(p: Principal, asset: dict | None, action: str) -> str:
    role = effective_role(p, asset) if asset and not asset.get("deletedAt") else None
    if role is None:
        # Invisible when not permitted: never reveal existence.
        raise NotFound("Asset not found")
    if ROLE_RANK[role] < ROLE_RANK[ACTION_MIN_ROLE[action]]:
        raise Forbidden(f"'{action}' requires role {ACTION_MIN_ROLE[action]}")
    return role


class NotFound(Exception):
    status = 404


def accessible_asset_ids(p: Principal) -> dict[str, str]:
    """Return {assetId: role} for every asset the principal can at least `use`."""
    now = ddb.now_ms()
    roles: dict[str, list[str]] = {}
    coll_roles: dict[str, list[str]] = {}
    for pid in p.principal_ids():
        for g in ddb.query(f"PRINCIPAL#{pid}", "RES#", index="GSI1"):
            if not _alive(g, now) or g.get("tenantId") != p.tenant_id:
                continue
            kind, _, rid = g["GSI1SK"].removeprefix("RES#").partition("#")
            if kind == "ASSET":
                roles.setdefault(rid, []).append(g["role"])
            elif kind == "COLL":
                coll_roles.setdefault(rid, []).append(g["role"])
    for cid, rs in coll_roles.items():
        for member in ddb.query(f"COLL#{cid}", "ASSET#"):
            roles.setdefault(member["SK"].removeprefix("ASSET#"), []).extend(rs)
    # Owners hold an explicit owner grant (written at asset creation), so they are covered
    # above. Tenant admins see every asset of the tenant.
    if p.is_tenant_admin:
        for item in ddb.query(f"TENANT#{p.tenant_id}", "ASSET#", index="GSI2"):
            roles.setdefault(item["assetId"], []).append("owner")
    return {aid: max_role(rs) for aid, rs in roles.items() if max_role(rs)}


def grant(resource: str, principal: str, role: str, tenant_id: str, granted_by: str,
          expires_at: int | None = None) -> dict:
    ptype, _, pid = principal.partition("#")
    if ptype not in PRINCIPAL_TYPES or not pid or role not in ROLE_RANK:
        raise ValueError("Invalid principal or role")
    if ptype == "TENANT" and pid != tenant_id:
        raise Forbidden("Cross-tenant sharing is not allowed")
    if ptype == "USER":
        profile = ddb.get(f"USER#{pid}", "PROFILE")
        if not profile or profile.get("tenantId") != tenant_id:
            raise Forbidden("Cross-tenant sharing is not allowed")
    if ptype == "GROUP" and not ddb.get(f"TENANT#{tenant_id}", f"GROUP#{pid}"):
        raise Forbidden("Unknown group")
    kind, _, rid = resource.partition("#")
    item = {
        "PK": resource,
        "SK": f"GRANT#{principal}",
        "GSI1PK": f"PRINCIPAL#{principal}",
        "GSI1SK": f"RES#{kind}#{rid}",
        "principal": principal,
        "role": role,
        "tenantId": tenant_id,
        "grantedBy": granted_by,
        "grantedAt": ddb.now_ms(),
        "expiresAt": expires_at,
    }
    ddb.put(item)
    return item


def revoke(resource: str, principal: str) -> None:
    ddb.delete(resource, f"GRANT#{principal}")
