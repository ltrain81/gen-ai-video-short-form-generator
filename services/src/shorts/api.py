"""Archive / identity / sharing REST API (API Gateway HTTP API, Cognito JWT authorizer)."""
from __future__ import annotations

import base64
import json
import math
import os
import uuid
from datetime import datetime, timezone

import boto3

from . import acl, broker, ddb, ingest, projects, recipes, search, steps, storage, timeline_ops, ui_widgets
from .auth import Forbidden, Principal
from .config import get_config
from .http import BadRequest, Conflict, NotFound, Request, Router

router = Router()

PART_SIZE = 64 * 1024 * 1024          # 64 MiB parts → 10,000 parts ≈ 640 GiB max
MAX_PART_URLS_PER_CALL = 100
URL_TTL = 3600


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:20]


def audit(p: Principal, action: str, resource: str, detail: dict | None = None) -> None:
    ts = ddb.now_ms()
    day = datetime.fromtimestamp(ts / 1000, timezone.utc).strftime("%Y-%m-%d")
    ddb.put({"PK": f"AUDIT#{p.tenant_id}#{day}", "SK": f"{ts}#{uuid.uuid4().hex[:8]}", "actor": p.sub,
             "action": action, "resource": resource, "detail": detail or {}, "ts": ts})


def _asset(asset_id: str) -> dict | None:
    return ddb.get(f"ASSET#{asset_id}", "META")


def _require_asset(p: Principal, asset_id: str, action: str) -> tuple[dict, str]:
    asset = _asset(asset_id)
    role = acl.require(p, asset, action)
    return asset, role


def _require_admin(p: Principal) -> None:
    if not p.is_tenant_admin:
        raise Forbidden("Tenant admin only")


# ================================================================== me / tenant / groups / users
@router.route("GET", "/me")
def me(req: Request):
    p = req.principal
    tenant = ddb.get(f"TENANT#{p.tenant_id}", "META") or {}
    groups = ddb.batch_get((f"TENANT#{p.tenant_id}", f"GROUP#{g}") for g in p.groups)
    return {"sub": p.sub, "email": p.email, "tenantId": p.tenant_id, "tenantName": tenant.get("name"),
            "tenantRole": p.tenant_role, "language": tenant.get("language") or ingest.DEFAULT_LANGUAGE,
            "languages": ingest.LANGUAGES,
            "groups": [{"groupId": g["SK"].removeprefix("GROUP#"), "name": g.get("name")} for g in groups],
            "features": {"generation": os.environ.get("DEPLOY_MODE", "edit") == "full"}}


@router.route("PATCH", "/tenant")
def update_tenant(req: Request):
    """Workspace settings. `language` controls the language of new AI analysis (summaries, chapters, captions)."""
    p = req.principal
    _require_admin(p)
    fields = {}
    if "name" in req.body:
        fields["name"] = str(req.body["name"])[:128]
    if "language" in req.body:
        if req.body["language"] not in ingest.LANGUAGES:
            raise BadRequest(f"language must be one of {sorted(ingest.LANGUAGES)}")
        fields["language"] = req.body["language"]
    if not fields:
        raise BadRequest("Editable fields: name, language")
    ddb.update(f"TENANT#{p.tenant_id}", "META", fields)
    audit(p, "tenant.update", f"TENANT#{p.tenant_id}", fields)
    return fields


@router.route("POST", "/assets/{assetId}/reanalyze")
def reanalyze_asset(req: Request):
    """Produce the language layer in the current workspace language (e.g. after a language change)."""
    asset, _ = _require_asset(req.principal, req.params["assetId"], "edit")
    if not asset.get("contentKey"):
        raise Conflict("Asset is still processing")
    lang = ingest.tenant_language(asset["tenantId"])
    if lang == asset.get("analysisLanguage"):
        return {"status": asset.get("status"), "language": lang}
    ddb.update(f"ASSET#{asset['assetId']}", "META", {"status": "processing", "analysisLanguage": lang})
    ingest.ensure_language(asset["contentKey"], lang)
    ingest.maybe_finalize(asset["contentKey"])
    return {"status": "processing", "language": lang}


@router.route("GET", "/users")
def list_users(req: Request):
    p = req.principal
    members = ddb.query(f"TENANT#{p.tenant_id}", "MEMBER#")
    profiles = {u["PK"].removeprefix("USER#"): u for u in
                ddb.batch_get((f"USER#{m['SK'].removeprefix('MEMBER#')}", "PROFILE") for m in members)}
    return [{"sub": sid, "email": profiles.get(sid, {}).get("email"), "name": profiles.get(sid, {}).get("name"),
             "tenantRole": m.get("role")} for m in members for sid in [m["SK"].removeprefix("MEMBER#")]]


@router.route("POST", "/users/invite")
def invite_user(req: Request):
    p = req.principal
    _require_admin(p)
    (email,) = req.require("email")
    role = req.body.get("tenantRole", "member")
    if role not in {"admin", "member"}:
        raise BadRequest("tenantRole must be admin or member")
    cognito = boto3.client("cognito-idp")
    resp = cognito.admin_create_user(
        UserPoolId=os.environ["USER_POOL_ID"], Username=email,
        UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        DesiredDeliveryMediums=["EMAIL"],
    )
    sub = next(a["Value"] for a in resp["User"]["Attributes"] if a["Name"] == "sub")
    provision_user(sub, email, p.tenant_id, role)
    audit(p, "user.invite", f"USER#{sub}", {"email": email, "role": role})
    return {"sub": sub, "email": email, "tenantRole": role}


def provision_user(sub: str, email: str, tenant_id: str, role: str, name: str = "") -> None:
    ddb.put({"PK": f"USER#{sub}", "SK": "PROFILE", "tenantId": tenant_id, "email": email, "name": name,
             "createdAt": ddb.now_ms()})
    ddb.put({"PK": f"TENANT#{tenant_id}", "SK": f"MEMBER#{sub}", "role": role, "joinedAt": ddb.now_ms()})


@router.route("GET", "/groups")
def list_groups(req: Request):
    p = req.principal
    return [{"groupId": g["SK"].removeprefix("GROUP#"), "name": g.get("name"), "member": g["SK"].removeprefix("GROUP#") in p.groups}
            for g in ddb.query(f"TENANT#{p.tenant_id}", "GROUP#")]


@router.route("POST", "/groups")
def create_group(req: Request):
    p = req.principal
    _require_admin(p)
    (name,) = req.require("name")
    gid = new_id("g")
    ddb.put({"PK": f"TENANT#{p.tenant_id}", "SK": f"GROUP#{gid}", "name": name, "createdAt": ddb.now_ms()})
    audit(p, "group.create", f"GROUP#{gid}", {"name": name})
    return {"groupId": gid, "name": name}


@router.route("GET", "/groups/{groupId}/members")
def group_members(req: Request):
    p = req.principal
    if not ddb.get(f"TENANT#{p.tenant_id}", f"GROUP#{req.params['groupId']}"):
        raise NotFound("Group not found")
    return [m["SK"].removeprefix("MEMBER#") for m in ddb.query(f"GROUP#{req.params['groupId']}", "MEMBER#")]


@router.route("POST", "/groups/{groupId}/members")
def add_group_member(req: Request):
    p = req.principal
    _require_admin(p)
    gid = req.params["groupId"]
    (sub,) = req.require("sub")
    if not ddb.get(f"TENANT#{p.tenant_id}", f"GROUP#{gid}") or not ddb.get(f"TENANT#{p.tenant_id}", f"MEMBER#{sub}"):
        raise NotFound("Group or user not found")
    ddb.put({"PK": f"GROUP#{gid}", "SK": f"MEMBER#{sub}", "GSI1PK": f"USER#{sub}", "GSI1SK": f"GROUP#{gid}"})
    audit(p, "group.member.add", f"GROUP#{gid}", {"sub": sub})
    return {"ok": True}


@router.route("DELETE", "/groups/{groupId}/members/{sub}")
def remove_group_member(req: Request):
    p = req.principal
    _require_admin(p)
    ddb.delete(f"GROUP#{req.params['groupId']}", f"MEMBER#{req.params['sub']}")
    audit(p, "group.member.remove", f"GROUP#{req.params['groupId']}", {"sub": req.params["sub"]})


# ================================================================== uploads
@router.route("POST", "/assets")
def create_asset(req: Request):
    """Create an asset record and start a multipart upload."""
    p = req.principal
    filename, size = req.require("filename", "size")
    size = int(size)
    if size <= 0:
        raise BadRequest("size must be > 0")
    content_type = req.body.get("contentType") or "application/octet-stream"
    scope = req.body.get("scope", "archive")  # archive | project
    if scope not in {"archive", "project"}:
        raise BadRequest("scope must be archive or project")
    collection_id = req.body.get("collectionId")
    if collection_id:
        coll = ddb.get(f"COLL#{collection_id}", "META")
        _require_collection(p, coll, "edit")
    asset_id = new_id("a")
    key = storage.original_key(p.tenant_id, asset_id, filename)
    now = ddb.now_ms()
    upload = storage.s3().create_multipart_upload(Bucket=get_config().media_bucket, Key=key, ContentType=content_type)
    ddb.put({
        "PK": f"ASSET#{asset_id}", "SK": "META", "assetId": asset_id, "tenantId": p.tenant_id, "ownerId": p.sub,
        "filename": filename, "contentType": content_type, "sizeBytes": size, "status": "uploading",
        "scope": scope, "projectId": req.body.get("projectId"), "collectionIds": [collection_id] if collection_id else [],
        "title": req.body.get("title"), "tags": req.body.get("tags", []), "uploadId": upload["UploadId"],
        "originalKey": key, "createdAt": now, "updatedAt": now,
        "GSI2PK": f"TENANT#{p.tenant_id}", "GSI2SK": f"ASSET#{now}#{asset_id}",
    })
    acl.grant(f"ASSET#{asset_id}", f"USER#{p.sub}", "owner", p.tenant_id, p.sub)
    if collection_id:
        ddb.put({"PK": f"COLL#{collection_id}", "SK": f"ASSET#{asset_id}", "addedAt": now})
    part_size = max(PART_SIZE, math.ceil(size / 10_000))
    audit(p, "asset.create", f"ASSET#{asset_id}", {"filename": filename, "size": size})
    return {"assetId": asset_id, "uploadId": upload["UploadId"], "partSize": part_size,
            "partCount": max(1, math.ceil(size / part_size))}


def _uploading_asset(req: Request) -> dict:
    asset, _ = _require_asset(req.principal, req.params["assetId"], "edit")
    if asset.get("status") != "uploading" or not asset.get("uploadId"):
        raise Conflict("Asset is not awaiting upload")
    return asset


@router.route("POST", "/assets/{assetId}/upload/parts")
def presign_parts(req: Request):
    asset = _uploading_asset(req)
    numbers = [int(n) for n in req.body.get("partNumbers", [])][:MAX_PART_URLS_PER_CALL]
    if not numbers or any(n < 1 or n > 10_000 for n in numbers):
        raise BadRequest("partNumbers must be 1..10000")
    urls = {n: storage.s3().generate_presigned_url(
        "upload_part", ExpiresIn=URL_TTL,
        Params={"Bucket": get_config().media_bucket, "Key": asset["originalKey"], "UploadId": asset["uploadId"],
                "PartNumber": n}) for n in numbers}
    return {"urls": urls}


@router.route("POST", "/assets/{assetId}/upload/complete")
def complete_upload(req: Request):
    asset = _uploading_asset(req)
    parts = sorted(({"PartNumber": int(x["partNumber"]), "ETag": x["etag"]} for x in req.body.get("parts", [])),
                   key=lambda x: x["PartNumber"])
    if not parts:
        raise BadRequest("parts required")
    storage.s3().complete_multipart_upload(Bucket=get_config().media_bucket, Key=asset["originalKey"],
                                           UploadId=asset["uploadId"], MultipartUpload={"Parts": parts})
    ddb.update(f"ASSET#{asset['assetId']}", "META", {"status": "queued", "uploadId": None, "updatedAt": ddb.now_ms()})
    return {"assetId": asset["assetId"], "status": "queued"}


@router.route("POST", "/assets/{assetId}/upload/abort")
def abort_upload(req: Request):
    asset = _uploading_asset(req)
    storage.s3().abort_multipart_upload(Bucket=get_config().media_bucket, Key=asset["originalKey"],
                                        UploadId=asset["uploadId"])
    ddb.update(f"ASSET#{asset['assetId']}", "META", {"status": "aborted", "deletedAt": ddb.now_ms()})


# ================================================================== assets
@router.route("GET", "/assets")
def list_assets(req: Request):
    p = req.principal
    accessible = acl.accessible_asset_ids(p)
    kind = req.query.get("kind")
    collection = req.query.get("collectionId")
    project = req.query.get("projectId")
    trash = req.query.get("trash") == "1"
    if collection:
        _require_collection(p, ddb.get(f"COLL#{collection}", "META"), "view")
        ids = {m["SK"].removeprefix("ASSET#") for m in ddb.query(f"COLL#{collection}", "ASSET#")}
        accessible = {a: r for a, r in accessible.items() if a in ids}
    items = ddb.batch_get((f"ASSET#{aid}", "META") for aid in accessible)
    out = []
    for a in items:
        if bool(a.get("deletedAt")) != trash or a.get("status") == "aborted":
            continue
        if kind and a.get("kind") != kind:
            continue
        if project:
            if a.get("projectId") != project:
                continue
        elif a.get("scope") == "project" and not collection:
            continue
        out.append(_with_thumb(search.public_asset(a, accessible[a["assetId"]]), a))
    out.sort(key=lambda a: -a.get("createdAt", 0))
    return out


def _with_thumb(public: dict, asset: dict) -> dict:
    if asset.get("thumbnailKey"):
        public["thumbnailUrl"] = storage.presign_get(asset["thumbnailKey"], URL_TTL)
    return public


@router.route("GET", "/assets/{assetId}")
def get_asset(req: Request):
    asset, role = _require_asset(req.principal, req.params["assetId"], "view")
    out = _with_thumb(search.public_asset(asset, role), asset)
    if asset.get("contentKey"):
        content = ddb.get(f"CONTENT#{asset['contentKey']}", "META") or {}
        out["processing"] = {"status": content.get("status"), "stages": content.get("stages", {}),
                             "error": content.get("lastError")}
        summary = ddb.get(f"CONTENT#{asset['contentKey']}",
                          f"ANALYSIS#summary#{asset.get('analysisLanguage') or ingest.DEFAULT_LANGUAGE}")
        if summary:
            out["analysisSummary"] = {k: v for k, v in summary.items() if k not in {"PK", "SK"}}
    out["grants"] = _grants(f"ASSET#{asset['assetId']}") if role == "owner" else None
    return out


@router.route("GET", "/assets/{assetId}/media")
def asset_media(req: Request):
    """Presigned URLs for proxies, sprites, waveform, pages — usable by the preview compositor."""
    asset, _ = _require_asset(req.principal, req.params["assetId"], "use")
    content = ddb.get(f"CONTENT#{asset.get('contentKey', '')}", "META") if asset.get("contentKey") else None
    if not content or not content.get("manifest"):
        raise Conflict("Asset is still processing")
    m = content["manifest"]
    sign = lambda k: storage.presign_get(k, URL_TTL) if k else None  # noqa: E731
    out = {"kind": m["kind"], "durationSec": m.get("durationSec"), "width": m.get("width"), "height": m.get("height"),
           "fps": m.get("fps"), "proxyUrl": sign(m.get("proxyKey")), "scrubUrl": sign(m.get("scrubKey")),
           "audioUrl": sign(m.get("audioKey")), "waveformUrl": sign(m.get("waveformKey")),
           "thumbnailUrl": sign(m.get("thumbnailKey")), "imageUrl": sign(m.get("imageAlphaKey") or m.get("imageKey")),
           "expiresIn": URL_TTL}
    if m.get("sprites"):
        out["sprites"] = {**{k: v for k, v in m["sprites"].items() if k != "sheets"},
                          "sheets": [{**s, "url": sign(s["key"])} for s in m["sprites"]["sheets"]]}
    if m.get("images") and m["kind"] == "document":
        out["pages"] = [{"index": i["index"], "url": sign(i["key"]), "width": i["width"], "height": i["height"]}
                        for i in m["images"]]
    audit(req.principal, "asset.use", f"ASSET#{asset['assetId']}")
    return out


@router.route("GET", "/assets/{assetId}/analysis")
def asset_analysis(req: Request):
    asset, _ = _require_asset(req.principal, req.params["assetId"], "use")
    ck = asset.get("contentKey")
    if not ck:
        raise Conflict("Asset is still processing")
    prefix = storage.content_prefix(ck)
    lang = asset.get("analysisLanguage") or ingest.DEFAULT_LANGUAGE
    out = {"language": lang}
    for name, key in (("scenes", f"analysis/{lang}/scenes.json"), ("chapters", f"analysis/{lang}/chapters.json"),
                      ("summary", f"analysis/{lang}/summary.json")):
        try:
            out[name] = json.loads(storage.s3().get_object(Bucket=get_config().media_bucket,
                                                           Key=f"{prefix}/{key}")["Body"].read())
        except storage.s3().exceptions.NoSuchKey:
            out[name] = None
    words, sentences = [], []
    listing = storage.s3().list_objects_v2(Bucket=get_config().media_bucket, Prefix=f"{prefix}/transcribe/0")
    for obj in sorted(listing.get("Contents", []), key=lambda o: o["Key"]):
        t = json.loads(storage.s3().get_object(Bucket=get_config().media_bucket, Key=obj["Key"])["Body"].read())
        words.extend(t.get("words", []))
        sentences.extend(t.get("sentences", []))
    out["transcript"] = {"words": words, "sentences": sentences} if words else None
    return out


EDITABLE = {"title", "tags", "people", "description"}


@router.route("PATCH", "/assets/{assetId}")
def update_asset(req: Request):
    asset, _ = _require_asset(req.principal, req.params["assetId"], "edit")
    fields = {k: v for k, v in req.body.items() if k in EDITABLE}
    if not fields:
        raise BadRequest(f"Editable fields: {sorted(EDITABLE)}")
    if "title" in fields:
        fields["titleEdited"] = True
    if "people" in fields:  # manual person tags: [{name, segments?:[{startSec,endSec}]}]
        if not isinstance(fields["people"], list):
            raise BadRequest("people must be a list")
    fields["updatedAt"] = ddb.now_ms()
    updated = ddb.update(f"ASSET#{asset['assetId']}", "META", fields)
    audit(req.principal, "asset.update", f"ASSET#{asset['assetId']}", {"fields": sorted(fields)})
    return search.public_asset(updated)


@router.route("DELETE", "/assets/{assetId}")
def delete_asset(req: Request):
    asset, _ = _require_asset(req.principal, req.params["assetId"], "delete")
    ddb.update(f"ASSET#{asset['assetId']}", "META", {"deletedAt": ddb.now_ms(), "deletedBy": req.principal.sub})
    audit(req.principal, "asset.delete", f"ASSET#{asset['assetId']}")


@router.route("POST", "/assets/{assetId}/restore")
def restore_asset(req: Request):
    p = req.principal
    asset = _asset(req.params["assetId"])
    if not asset or asset.get("tenantId") != p.tenant_id or not asset.get("deletedAt"):
        raise NotFound("Asset not found")
    if asset.get("ownerId") != p.sub and not p.is_tenant_admin:
        raise NotFound("Asset not found")
    ddb.update_raw(f"ASSET#{asset['assetId']}", "META", "REMOVE deletedAt, deletedBy")
    audit(p, "asset.restore", f"ASSET#{asset['assetId']}")
    return {"ok": True}


@router.route("GET", "/assets/{assetId}/download")
def download_asset(req: Request):
    asset, _ = _require_asset(req.principal, req.params["assetId"], "download")
    audit(req.principal, "asset.download", f"ASSET#{asset['assetId']}")
    return {"url": storage.presign_get(asset["originalKey"], 900, asset.get("filename"))}


# ================================================================== sharing
def _grants(resource: str) -> list[dict]:
    return [{"principal": g["principal"], "role": g["role"], "expiresAt": g.get("expiresAt"),
             "grantedBy": g.get("grantedBy")} for g in ddb.query(resource, "GRANT#")]


def _set_grant(req: Request, resource: str) -> dict:
    principal, role = req.require("principal", "role")
    expires = req.body.get("expiresAt")
    g = acl.grant(resource, principal, role, req.principal.tenant_id, req.principal.sub,
                  int(expires) if expires else None)
    audit(req.principal, "share.grant", resource, {"principal": principal, "role": role, "expiresAt": expires})
    return {"principal": g["principal"], "role": g["role"], "expiresAt": g.get("expiresAt")}


@router.route("GET", "/assets/{assetId}/grants")
def asset_grants(req: Request):
    asset, _ = _require_asset(req.principal, req.params["assetId"], "share")
    return _grants(f"ASSET#{asset['assetId']}")


@router.route("PUT", "/assets/{assetId}/grants")
def put_asset_grant(req: Request):
    asset, _ = _require_asset(req.principal, req.params["assetId"], "share")
    return _set_grant(req, f"ASSET#{asset['assetId']}")


@router.route("DELETE", "/assets/{assetId}/grants/{principal}")
def delete_asset_grant(req: Request):
    asset, _ = _require_asset(req.principal, req.params["assetId"], "share")
    principal = req.params["principal"].replace("%23", "#")
    if principal == f"USER#{asset['ownerId']}":
        raise BadRequest("Cannot revoke the owner")
    acl.revoke(f"ASSET#{asset['assetId']}", principal)
    audit(req.principal, "share.revoke", f"ASSET#{asset['assetId']}", {"principal": principal})


# ================================================================== collections
def _collection_role(p: Principal, coll: dict | None) -> str | None:
    if not coll or coll.get("tenantId") != p.tenant_id:
        return None
    if coll.get("ownerId") == p.sub or p.is_tenant_admin:
        return "owner"
    ids, now = set(p.principal_ids()), ddb.now_ms()
    return acl.max_role([g["role"] for g in ddb.query(f"COLL#{coll['collectionId']}", "GRANT#")
                         if g["principal"] in ids and (not g.get("expiresAt") or g["expiresAt"] > now)])


def _require_collection(p: Principal, coll: dict | None, action: str) -> str:
    role = _collection_role(p, coll)
    if role is None:
        raise NotFound("Collection not found")
    if acl.ROLE_RANK[role] < acl.ROLE_RANK[acl.ACTION_MIN_ROLE[action]]:
        raise Forbidden(f"'{action}' requires role {acl.ACTION_MIN_ROLE[action]}")
    return role


@router.route("GET", "/collections")
def list_collections(req: Request):
    p = req.principal
    out = []
    for c in ddb.query(f"TENANT#{p.tenant_id}", "COLL#", index="GSI2"):
        role = _collection_role(p, c)
        if role:
            out.append({"collectionId": c["collectionId"], "name": c.get("name"), "ownerId": c.get("ownerId"),
                        "role": role, "createdAt": c.get("createdAt")})
    return out


@router.route("POST", "/collections")
def create_collection(req: Request):
    p = req.principal
    (name,) = req.require("name")
    cid = new_id("c")
    now = ddb.now_ms()
    ddb.put({"PK": f"COLL#{cid}", "SK": "META", "collectionId": cid, "name": name, "tenantId": p.tenant_id,
             "ownerId": p.sub, "createdAt": now, "GSI2PK": f"TENANT#{p.tenant_id}", "GSI2SK": f"COLL#{cid}"})
    acl.grant(f"COLL#{cid}", f"USER#{p.sub}", "owner", p.tenant_id, p.sub)
    audit(p, "collection.create", f"COLL#{cid}", {"name": name})
    return {"collectionId": cid, "name": name, "role": "owner"}


@router.route("POST", "/collections/{collectionId}/assets")
def add_to_collection(req: Request):
    p = req.principal
    cid = req.params["collectionId"]
    _require_collection(p, ddb.get(f"COLL#{cid}", "META"), "edit")
    added = []
    for aid in req.body.get("assetIds", [])[:500]:
        asset, _ = _require_asset(p, aid, "edit")
        ddb.put({"PK": f"COLL#{cid}", "SK": f"ASSET#{aid}", "addedAt": ddb.now_ms()})
        ddb.update(f"ASSET#{aid}", "META", {"collectionIds": sorted(set(asset.get("collectionIds", [])) | {cid})})
        added.append(aid)
    audit(p, "collection.add", f"COLL#{cid}", {"assetIds": added})
    return {"added": added}


@router.route("DELETE", "/collections/{collectionId}/assets/{assetId}")
def remove_from_collection(req: Request):
    p = req.principal
    cid, aid = req.params["collectionId"], req.params["assetId"]
    _require_collection(p, ddb.get(f"COLL#{cid}", "META"), "edit")
    asset = _asset(aid)
    ddb.delete(f"COLL#{cid}", f"ASSET#{aid}")
    if asset:
        ddb.update(f"ASSET#{aid}", "META", {"collectionIds": [c for c in asset.get("collectionIds", []) if c != cid]})


@router.route("GET", "/collections/{collectionId}/grants")
def collection_grants(req: Request):
    _require_collection(req.principal, ddb.get(f"COLL#{req.params['collectionId']}", "META"), "share")
    return _grants(f"COLL#{req.params['collectionId']}")


@router.route("PUT", "/collections/{collectionId}/grants")
def put_collection_grant(req: Request):
    _require_collection(req.principal, ddb.get(f"COLL#{req.params['collectionId']}", "META"), "share")
    return _set_grant(req, f"COLL#{req.params['collectionId']}")


@router.route("DELETE", "/collections/{collectionId}/grants/{principal}")
def delete_collection_grant(req: Request):
    _require_collection(req.principal, ddb.get(f"COLL#{req.params['collectionId']}", "META"), "share")
    acl.revoke(f"COLL#{req.params['collectionId']}", req.params["principal"].replace("%23", "#"))


# ================================================================== access requests
@router.route("POST", "/access-requests")
def request_access(req: Request):
    """Request access to an asset/collection whose ID was shared out-of-band (e.g. a link)."""
    p = req.principal
    resource, role = req.require("resource", "role")
    kind, _, rid = resource.partition("#")
    item = ddb.get(resource, "META") if kind in {"ASSET", "COLL"} else None
    if not item or item.get("tenantId") != p.tenant_id or role not in acl.ROLE_RANK or role == "owner":
        raise NotFound("Resource not found")
    ts = ddb.now_ms()
    ddb.put({"PK": resource, "SK": f"REQUEST#{p.sub}", "GSI1PK": f"INBOX#{item['ownerId']}",
             "GSI1SK": f"REQUEST#{ts}#{rid}#{p.sub}", "requester": p.sub, "role": role,
             "message": str(req.body.get("message", ""))[:500], "createdAt": ts, "resource": resource,
             "title": item.get("title") or item.get("name") or item.get("filename")})
    return {"ok": True}


@router.route("GET", "/access-requests")
def list_access_requests(req: Request):
    return [{k: r.get(k) for k in ("resource", "requester", "role", "message", "createdAt", "title")}
            for r in ddb.query(f"INBOX#{req.principal.sub}", "REQUEST#", index="GSI1", forward=False)]


@router.route("POST", "/access-requests/decide")
def decide_access_request(req: Request):
    p = req.principal
    resource, requester, decision = req.require("resource", "requester", "decision")
    item = ddb.get(resource, "META")
    request = ddb.get(resource, f"REQUEST#{requester}")
    if not item or not request or (item.get("ownerId") != p.sub and not p.is_tenant_admin):
        raise NotFound("Request not found")
    if decision == "approve":
        acl.grant(resource, f"USER#{requester}", request["role"], p.tenant_id, p.sub, req.body.get("expiresAt"))
    ddb.delete(resource, f"REQUEST#{requester}")
    audit(p, f"access.{decision}", resource, {"requester": requester})
    return {"ok": True}


# ================================================================== search
@router.route("POST", "/search")
def search_archive(req: Request):
    p = req.principal
    text = (req.body.get("text") or "").strip() or None
    image = None
    if req.body.get("imageAssetId"):
        asset, _ = _require_asset(p, req.body["imageAssetId"], "use")
        content = ddb.get(f"CONTENT#{asset.get('contentKey', '')}", "META") or {}
        key = (content.get("manifest") or {}).get("imageKey") or (content.get("manifest") or {}).get("thumbnailKey")
        if not key:
            raise Conflict("Image asset not processed yet")
        image = storage.s3().get_object(Bucket=get_config().media_bucket, Key=key)["Body"].read()
    elif req.body.get("imageBase64"):
        image = base64.b64decode(req.body["imageBase64"])
        if len(image) > 5 * 1024 * 1024:
            raise BadRequest("Image must be <= 5 MB")
    if not text and not image:
        raise BadRequest("Provide text and/or an image")
    results = search.search(p, text=text, image=image, modalities=req.body.get("modalities"),
                            kinds=req.body.get("kinds"), asset_ids=req.body.get("assetIds"),
                            limit=min(int(req.body.get("limit", 20)), 50))
    for r in results:
        a = _asset(r["asset"]["assetId"])
        if a:
            _with_thumb(r["asset"], a)
    return results


# ================================================================== recipes & skills
@router.route("GET", "/recipes")
def get_recipes(req: Request):
    return recipes.list_recipes(req.principal.tenant_id)


@router.route("PUT", "/recipes/{recipeId}")
def put_recipe(req: Request):
    item = recipes.put_recipe(req.principal.tenant_id, req.params["recipeId"], req.body, req.principal.sub)
    audit(req.principal, "recipe.put", f"RECIPE#{req.params['recipeId']}")
    return item


@router.route("DELETE", "/recipes/{recipeId}")
def delete_recipe(req: Request):
    r = ddb.get(f"TENANT#{req.principal.tenant_id}", f"RECIPE#{req.params['recipeId']}")
    if not r:
        raise NotFound("Recipe not found")
    if r.get("createdBy") != req.principal.sub and not req.principal.is_tenant_admin:
        raise Forbidden("Only the creator or an admin can delete")
    ddb.delete(f"TENANT#{req.principal.tenant_id}", f"RECIPE#{req.params['recipeId']}")


@router.route("GET", "/skills")
def get_skills(req: Request):
    return [{k: v for k, v in sk.items() if k != "body" or not sk.get("builtin")} | {"preview": sk["body"][:300]}
            for sk in recipes.list_skills(req.principal.tenant_id)]


@router.route("GET", "/skills/{name}")
def get_skill(req: Request):
    sk = recipes.get_skill(req.principal.tenant_id, req.params["name"])
    if not sk:
        raise NotFound("Skill not found")
    return sk


@router.route("PUT", "/skills/{name}")
def put_skill(req: Request):
    """Workspace skill (editing playbook) — used by the agent via list_skills/read_skill."""
    desc, body = req.require("description", "body")
    item = recipes.put_skill(req.principal.tenant_id, req.params["name"], str(desc), str(body), req.principal.sub)
    audit(req.principal, "skill.put", f"SKILL#{req.params['name']}")
    return item


@router.route("DELETE", "/skills/{name}")
def delete_skill(req: Request):
    sk = ddb.get(f"TENANT#{req.principal.tenant_id}", f"SKILL#{req.params['name']}")
    if not sk:
        raise NotFound("Only workspace skills can be deleted")
    if sk.get("createdBy") != req.principal.sub and not req.principal.is_tenant_admin:
        raise Forbidden("Only the creator or an admin can delete")
    ddb.delete(f"TENANT#{req.principal.tenant_id}", f"SKILL#{req.params['name']}")


# ================================================================== brand kits
@router.route("GET", "/brand-kits")
def get_brand_kits(req: Request):
    from . import brand
    return brand.list_kits(req.principal)


@router.route("POST", "/brand-kits")
def create_brand_kit(req: Request):
    from . import brand
    return brand.put(req.principal, None, req.body)


@router.route("PUT", "/brand-kits/{kitId}")
def update_brand_kit(req: Request):
    from . import brand
    return brand.put(req.principal, req.params["kitId"], req.body)


@router.route("DELETE", "/brand-kits/{kitId}")
def delete_brand_kit(req: Request):
    from . import brand
    brand.delete(req.principal, req.params["kitId"])


# ================================================================== projects / timelines
@router.route("GET", "/projects")
def list_projects(req: Request):
    return projects.list_for(req.principal)


@router.route("POST", "/projects")
def create_project(req: Request):
    recipe = recipes.get_recipe(req.principal.tenant_id, req.body["recipe"]) if req.body.get("recipe") else None
    if req.body.get("recipe") and not recipe:
        raise BadRequest("Unknown recipe")
    aspect = req.body.get("aspect") or (recipe or {}).get("aspect") or "9:16"
    try:
        timeline_ops._canvas_for(aspect, {})
    except timeline_ops.OpError as exc:
        raise BadRequest(str(exc)) from exc
    item = projects.create(req.principal, str(req.body.get("title") or "Untitled")[:200], aspect,
                           str(req.body.get("synopsis", ""))[:4000], [str(a) for a in req.body.get("assetIds", [])])
    extra = {}
    if recipe:
        extra = {"recipe": recipe["id"], "recipeParams": {k: req.body.get(k) for k in ("lengthSec", "captions", "music")
                                                           if req.body.get(k) is not None}}
    if req.body.get("brandKitId"):
        extra["brandKitId"] = str(req.body["brandKitId"])
    if extra:
        ddb.update(f"PROJ#{item['projectId']}", "META", extra)
    audit(req.principal, "project.create", f"PROJ#{item['projectId']}")
    return {"projectId": item["projectId"], "currentVersion": 0}


@router.route("GET", "/projects/{projectId}")
def get_project(req: Request):
    project, role = projects.require_project(req.principal, req.params["projectId"], "view")
    version = int(req.query["version"]) if req.query.get("version") else None
    return {"project": {k: project.get(k) for k in ("projectId", "title", "aspect", "synopsis", "currentVersion",
                                                      "createdAt", "updatedAt", "ownerId", "assetIds", "agentModel",
                                                      "agentEffort", "orchestrator")},
            "role": role, "lock": projects.lock_state(project), "timeline": projects.load_timeline(project, version)}


@router.route("GET", "/agent/models")
def agent_models(req: Request):
    default_model = os.environ.get("BMA_MODEL_ID", "openai.gpt-6.1-sol")
    return {"models": [{"id": k, "label": v[0], "tier": v[1]} for k, v in steps.AGENT_MODELS.items()],
            "efforts": list(steps.EFFORTS), "default": {"model": default_model,
                                                        "effort": os.environ.get("BMA_REASONING_EFFORT", "high")}}


@router.route("PATCH", "/projects/{projectId}")
def update_project(req: Request):
    project, _ = projects.require_project(req.principal, req.params["projectId"], "edit")
    fields = {k: str(v)[:4000] for k, v in req.body.items() if k in {"title", "synopsis"}}
    if "orchestrator" in req.body:
        if req.body["orchestrator"] not in {"bma", "strands"}:
            raise BadRequest("orchestrator must be bma or strands")
        fields["orchestrator"] = req.body["orchestrator"]
    if "agentModel" in req.body:
        if req.body["agentModel"] not in steps.AGENT_MODELS:
            raise BadRequest(f"agentModel must be one of {sorted(steps.AGENT_MODELS)}")
        fields["agentModel"] = req.body["agentModel"]
    if "agentEffort" in req.body:
        if req.body["agentEffort"] not in steps.EFFORTS:
            raise BadRequest(f"agentEffort must be one of {list(steps.EFFORTS)}")
        fields["agentEffort"] = req.body["agentEffort"]
    if not fields:
        raise BadRequest("Editable fields: title, synopsis, orchestrator, agentModel, agentEffort")
    ddb.update(f"PROJ#{project['projectId']}", "META", {**fields, "updatedAt": ddb.now_ms()})
    return {"ok": True}


@router.route("POST", "/projects/{projectId}/assets")
def attach_project_assets(req: Request):
    project, _ = projects.require_project(req.principal, req.params["projectId"], "edit")
    return {"assetIds": projects.attach_assets(req.principal, project, [str(a) for a in req.body.get("assetIds", [])])}


@router.route("PUT", "/projects/{projectId}/timeline")
def save_timeline(req: Request):
    project, _ = projects.require_project(req.principal, req.params["projectId"], "edit")
    timeline, base = req.require("timeline", "baseVersion")
    updated = projects.save(req.principal, project, timeline, int(base), "user", str(req.body.get("message", "")))
    return {"currentVersion": updated["currentVersion"]}


@router.route("POST", "/projects/{projectId}/ops")
def apply_ops(req: Request):
    """Manual edits (L3/L4). Same engine as the agent; touched paths are pinned so the AI keeps them."""
    from . import timeline_ops
    p = req.principal
    project, _ = projects.require_project(p, req.params["projectId"], "edit")
    ops, base = req.require("ops", "baseVersion")
    if int(base) != project["currentVersion"]:
        raise Conflict("Timeline changed; reload")
    tl = projects.load_timeline(project)
    ids = set().union(set(), *[timeline_ops.asset_refs(o) for o in ops if isinstance(o, dict)]) | {
        i["assetId"] for t in tl["tracks"] for i in t["items"] if i.get("assetId")}
    accessible = acl.accessible_asset_ids(p)
    assets = {a["assetId"]: a for a in ddb.batch_get((f"ASSET#{i}", "META") for i in ids if i in accessible)}
    for o in ops:
        if isinstance(o, dict) and o.get("op") == "brand":
            from . import brand
            o["kit"] = brand.resolve(p, o.get("brandKitId") or project.get("brandKitId"))
            ids |= {o["kit"].get(k) for k in ("logoAssetId", "introAssetId", "outroAssetId") if o["kit"].get(k)}
    assets = {a["assetId"]: a for a in ddb.batch_get((f"ASSET#{i}", "META") for i in ids if i in accessible)}
    for o in ops:
        if isinstance(o, dict) and o.get("op") == "captions" and "transcripts" not in o:
            from . import agent_tools
            src = timeline_ops.track(tl, o["source"]) if o.get("source") not in (None, "main") else timeline_ops.main_track(tl)
            o["transcripts"] = {aid: agent_tools._transcript(assets[aid]) for aid in
                                {i.get("assetId") for i in src["items"]} if aid in assets}
    new_tl, results = timeline_ops.apply(tl, list(ops)[:200], "user", assets, bool(req.body.get("autoPin", True)))
    version = project["currentVersion"]
    if any(r["ok"] for r in results) and new_tl != tl:  # no-op edits (e.g. drop in place) create no version
        version = projects.save(p, project, new_tl, version, "user", str(req.body.get("message", "manual edit")))["currentVersion"]
    return {"currentVersion": version, "results": results, "timeline": new_tl}


@router.route("POST", "/projects/{projectId}/renders")
def request_render(req: Request):
    from . import renders
    project, _ = projects.require_project(req.principal, req.params["projectId"], "edit")
    quality = req.body.get("quality", "final")
    if quality not in {"final", "preview"}:
        raise BadRequest("quality must be final or preview")
    tl = projects.load_timeline(project)
    if not any(i for t in tl["tracks"] if t["kind"] == "video" for i in t["items"]):
        raise BadRequest("Nothing to render: the main track is empty")
    item = renders.request(project, quality, req.principal.sub)
    audit(req.principal, "project.render", f"PROJ#{project['projectId']}", {"quality": quality})
    return {"renderId": item["renderId"], "status": item["status"]}


@router.route("GET", "/projects/{projectId}/renders")
def list_renders(req: Request):
    from . import renders
    project, _ = projects.require_project(req.principal, req.params["projectId"], "view")
    return renders.list_for(project)


@router.route("GET", "/projects/{projectId}/export")
def export_project(req: Request):
    """OTIO / FCPXML / EDL for desktop NLEs. Returns the document text (the client saves it as a file)."""
    from . import export
    project, _ = projects.require_project(req.principal, req.params["projectId"], "view")
    fmt = req.query.get("format", "otio")
    if fmt not in export.FORMATS:
        raise BadRequest(f"format must be one of {sorted(export.FORMATS)}")
    audit(req.principal, "project.export", f"PROJ#{project['projectId']}", {"format": fmt})
    return export.export_project(req.principal, project, fmt)


@router.route("GET", "/projects/{projectId}/versions")
def project_versions(req: Request):
    project, _ = projects.require_project(req.principal, req.params["projectId"], "view")
    return projects.versions(project["projectId"])


@router.route("POST", "/projects/{projectId}/restore")
def restore_version(req: Request):
    project, _ = projects.require_project(req.principal, req.params["projectId"], "edit")
    (version,) = req.require("version")
    timeline = projects.load_timeline(project, int(version))
    updated = projects.save(req.principal, project, timeline, project["currentVersion"], "user",
                            f"restore v{int(version)}")
    return {"currentVersion": updated["currentVersion"]}


@router.route("DELETE", "/projects/{projectId}")
def delete_project(req: Request):
    project, _ = projects.require_project(req.principal, req.params["projectId"], "delete")
    ddb.update(f"PROJ#{project['projectId']}", "META", {"deletedAt": ddb.now_ms()})
    audit(req.principal, "project.delete", f"PROJ#{project['projectId']}")


@router.route("PUT", "/projects/{projectId}/grants")
def put_project_grant(req: Request):
    project, _ = projects.require_project(req.principal, req.params["projectId"], "share")
    return _set_grant(req, f"PROJ#{project['projectId']}")


# ================================================================== chat
@router.route("GET", "/projects/{projectId}/chat")
def chat_history(req: Request):
    project, _ = projects.require_project(req.principal, req.params["projectId"], "view")
    busy = project.get("agentBusy")
    return {"messages": broker.messages(project["projectId"]), "widgets": ui_widgets.history(project["projectId"]),
            "busy": bool(busy and busy.get("until", 0) > ddb.now_ms())}


@router.route("POST", "/projects/{projectId}/chat")
def chat_send(req: Request):
    """Start one agent turn. Events stream to /projects/{projectId}/{sub} on AppSync Events."""
    p = req.principal
    project, _ = projects.require_project(p, req.params["projectId"], "edit")
    text = str(req.body.get("text", "")).strip()[:8000]
    answer = req.body.get("answer")  # {questionId, value}
    if not text and not answer:
        raise BadRequest("text or answer required")
    if answer and answer.get("questionId"):
        ui_widgets.answer(project["projectId"], str(answer["questionId"]), answer.get("value"))
    attached: list[str] = []
    if req.body.get("attachAssetIds"):
        requested = [str(a) for a in req.body["attachAssetIds"]][:50]
        merged = projects.attach_assets(p, project, requested)
        attached = [a for a in requested if a in merged]
        project = {**project, "assetIds": merged}
    turn = uuid.uuid4().hex[:10]
    agent_text = text
    if attached:  # tell the agent which footage the user just handed it, in the order they picked it
        agent_text = f"{text}\n\n[User attached these assets to the project, in this order: {', '.join(attached)}]"
    if project.get("recipe") and not ddb.query(f"PROJ#{project['projectId']}", "MSG#", limit=1):
        recipe = recipes.get_recipe(p.tenant_id, project["recipe"])
        if recipe:  # first turn: expand the chosen format recipe into the agent instruction
            lang = ingest.tenant_language(p.tenant_id)
            agent_text = recipes.compose_prompt(recipe, agent_text, project.get("recipeParams") or {}, lang)
    if not broker.try_start(project["projectId"], turn):
        raise Conflict("The agent is still working on the previous message")
    broker.save_message(project["projectId"], "user", text, selection=req.body.get("selection"),
                        questionId=(answer or {}).get("questionId"), turn=turn)
    try:
        boto3.client("lambda").invoke(FunctionName=os.environ["BROKER_FUNCTION"], InvocationType="Event",
                                      Payload=json.dumps({"projectId": project["projectId"], "sub": p.sub, "text": agent_text,
                                                          "selection": req.body.get("selection"), "answer": answer,
                                                          "turn": turn}).encode())
    except Exception:
        broker.finish(project["projectId"])
        raise
    return {"turn": turn, "channel": f"/projects/{project['projectId']}/{p.sub}"}


# ================================================================== batches & metadata
@router.route("POST", "/batches")
def create_batch(req: Request):
    from . import batches
    asset_id, count = req.require("assetId", "count")
    out = batches.create(req.principal, str(asset_id), int(count), req.body)
    audit(req.principal, "batch.create", f"ASSET#{asset_id}", {"count": int(count)})
    return out


@router.route("GET", "/batches")
def list_batches(req: Request):
    from . import batches
    return batches.list_for(req.principal)


@router.route("GET", "/batches/{batchId}")
def get_batch(req: Request):
    from . import batches
    return batches.get(req.principal, req.params["batchId"])


@router.route("POST", "/projects/{projectId}/metadata/suggest")
def suggest_metadata(req: Request):
    from . import models
    project, _ = projects.require_project(req.principal, req.params["projectId"], "view")
    tl = projects.load_timeline(project)
    texts = [i.get("text") or " ".join(w["w"] for w in i.get("words", [])) for t in tl["tracks"] for i in t["items"]
             if i.get("type") in {"text", "caption"}]
    reasons = [(i.get("provenance") or {}).get("reason") for t in tl["tracks"] for i in t["items"] if i.get("provenance")]
    ids = {i["assetId"] for t in tl["tracks"] for i in t["items"] if i.get("assetId")}
    summaries = [a.get("aiSummary") for a in ddb.batch_get((f"ASSET#{i}", "META") for i in ids) if a.get("aiSummary")]
    ctx = json.dumps({"project": project.get("title"), "request": project.get("synopsis"), "recipe": project.get("recipe"),
                      "sources": summaries[:5], "onScreenText": texts[:80], "editorNotes": [r for r in reasons if r][:30],
                      "durationSec": round(sum(i["duration"] for i in tl["tracks"][0]["items"]), 1)}, ensure_ascii=False)
    return models.suggest_metadata(ctx, ingest.LANGUAGES.get(ingest.tenant_language(req.principal.tenant_id), "Korean"))


# ================================================================== generation (mode=full)
@router.route("GET", "/generation/estimate")
def generation_estimate(req: Request):
    from . import generation
    if not generation.enabled():
        return {"available": False}
    return generation.estimate(req.query.get("kind", "image"), dict(req.query))


@router.route("POST", "/projects/{projectId}/generate")
def generate(req: Request):
    from . import generation
    (kind,) = req.require("kind")
    params = {k: v for k, v in req.body.items() if k != "kind"}
    out = generation.start(req.principal, req.params["projectId"], kind, params)
    audit(req.principal, "generation.start", f"PROJ#{req.params['projectId']}", {"kind": kind})
    return out


@router.route("GET", "/projects/{projectId}/generations")
def generations(req: Request):
    from . import generation
    projects.require_project(req.principal, req.params["projectId"], "view")
    return generation.list_jobs(req.params["projectId"])


@router.route("GET", "/workspace/keys")
def workspace_keys(req: Request):
    from . import generation
    _require_admin(req.principal)
    return {pv: generation.has_byo_key(req.principal.tenant_id, pv) for pv in ("elevenlabs",)}


@router.route("PUT", "/workspace/keys/{provider}")
def put_workspace_key(req: Request):
    """Bring-your-own third-party API key (stored in AWS Secrets Manager, never returned)."""
    from . import generation
    _require_admin(req.principal)
    if req.params["provider"] not in {"elevenlabs"}:
        raise BadRequest("Unsupported provider")
    (value,) = req.require("value")
    generation.set_byo_key(req.principal.tenant_id, req.params["provider"], str(value).strip())
    audit(req.principal, "workspace.key.set", f"TENANT#{req.principal.tenant_id}", {"provider": req.params["provider"]})
    return {"ok": True}


# ================================================================== audit
@router.route("GET", "/audit")
def get_audit(req: Request):
    p = req.principal
    _require_admin(p)
    day = req.query.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return ddb.query(f"AUDIT#{p.tenant_id}#{day}", forward=False, limit=500)


def handler(event, _context):
    return router.handle(event)
