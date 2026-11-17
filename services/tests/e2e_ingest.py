"""End-to-end smoke test against a deployed stack (not run by pytest).

  uv run python tests/e2e_ingest.py /path/to/video.mp4
Creates/uses a test user, uploads via the public API (presigned multipart), waits for ingest,
then runs a search. Needs AWS admin creds for the dev account (Cognito admin auth).
"""
import json
import math
import os
import sys
import time
import urllib.request

import boto3

OUT = json.load(open(os.environ.get("CDK_OUTPUTS", "/tmp/cdk-outputs.json")))
REGION = os.environ.get("AWS_REGION", "us-east-1")
POOL = OUT["Shorts-Core"]["UserPoolId"]
CLIENT = OUT["Shorts-Core"]["UserPoolClientId"]
API = OUT["Shorts-Api"]["ApiUrl"]
EMAIL = os.environ.get("E2E_EMAIL", "e2e-test@example.com")
PASSWORD = os.environ["E2E_PASSWORD"]  # never commit credentials; set in your shell

idp = boto3.client("cognito-idp", region_name=REGION)


def token() -> str:
    # Provision with scripts/create_user.py --password first (pools may be invite-only).
    r = idp.admin_initiate_auth(UserPoolId=POOL, ClientId=CLIENT, AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                                AuthParameters={"USERNAME": EMAIL, "PASSWORD": PASSWORD})
    return r["AuthenticationResult"]["AccessToken"]


def call(method, path, body=None, tok=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {tok}"} if tok else {})})
    try:
        with urllib.request.urlopen(req) as r:
            data = r.read()
            return r.status, json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def main(path: str):
    print("anonymous /me →", call("GET", "/me")[0], "(expect 401)")
    tok = token()
    st, me = call("GET", "/me", tok=tok)
    print("/me →", st, me)
    size = os.path.getsize(path)
    st, created = call("POST", "/assets", {"filename": os.path.basename(path), "size": size, "contentType": "video/mp4"}, tok)
    print("create →", st, created)
    aid, part = created["assetId"], created["partSize"]
    n = math.ceil(size / part)
    urls = call("POST", f"/assets/{aid}/upload/parts", {"partNumbers": list(range(1, n + 1))}, tok)[1]["urls"]
    parts = []
    with open(path, "rb") as f:
        for i in range(1, n + 1):
            r = urllib.request.urlopen(urllib.request.Request(urls[str(i)], data=f.read(part), method="PUT"))
            parts.append({"partNumber": i, "etag": r.headers["ETag"]})
            print(f"  part {i}/{n}")
    print("complete →", call("POST", f"/assets/{aid}/upload/complete", {"parts": parts}, tok)[0])
    t0 = time.time()
    while True:
        st, a = call("GET", f"/assets/{aid}", tok=tok)
        stages = (a.get("processing") or {}).get("stages", {})
        print(f"  [{int(time.time() - t0)}s] {a.get('status')} {stages}")
        if a.get("status") in ("ready", "failed"):
            break
        time.sleep(20)
    print(json.dumps(a, ensure_ascii=False, indent=1)[:1500])
    for q in ["sports highlight", "crowd cheering", "interview"]:
        st, res = call("POST", "/search", {"text": q}, tok)
        print(f"search '{q}' →", st, [(r["asset"]["displayTitle"], r["score"], [(s["startSec"], s["modality"]) for s in r["segments"][:3]]) for r in res] if st == 200 else res)


if __name__ == "__main__":
    main(sys.argv[1])
