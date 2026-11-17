"""E2E: transparent logo upload → brand kit → batch of N shorts (recipe + brand kit) → wait → summary.

  E2E_PASSWORD=... uv run python tests/e2e_features.py <videoAssetId> [count]
"""
import math
import os
import subprocess
import sys
import time
import urllib.request

import e2e_ingest as e


def upload(tok, path, ctype):
    size = os.path.getsize(path)
    st, c = e.call("POST", "/assets", {"filename": os.path.basename(path), "size": size, "contentType": ctype}, tok)
    aid, part = c["assetId"], c["partSize"]
    urls = e.call("POST", f"/assets/{aid}/upload/parts", {"partNumbers": list(range(1, math.ceil(size / part) + 1))}, tok)[1]["urls"]
    parts = []
    with open(path, "rb") as f:
        for n in range(1, math.ceil(size / part) + 1):
            r = urllib.request.urlopen(urllib.request.Request(urls[str(n)], data=f.read(part), method="PUT"))
            parts.append({"partNumber": n, "etag": r.headers["ETag"]})
    e.call("POST", f"/assets/{aid}/upload/complete", {"parts": parts}, tok)
    return aid


def wait_ready(tok, aid, limit=900):
    t0 = time.time()
    while time.time() - t0 < limit:
        a = e.call("GET", f"/assets/{aid}", tok=tok)[1]
        if a["status"] in ("ready", "failed"):
            return a
        time.sleep(15)
    return a


def main(video, count):
    tok = e.token()
    logo = "/tmp/shorts-logo.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=black@0.0:s=600x200,format=rgba,drawbox=x=0:y=0:w=600:h=200:color=0xFF9900@0.9:t=fill,"
                    "drawbox=x=20:y=20:w=560:h=160:color=0x232F3E@1:t=fill", "-frames:v", "1", logo], check=True)
    lid = upload(tok, logo, "image/png")
    print("logo", lid, wait_ready(tok, lid)["status"])
    st, kit = e.call("POST", "/brand-kits", {"name": "E2E kit", "logoAssetId": lid, "logoCorner": "tr", "logoScale": 0.22,
                                             "textColor": "#FFFFFF", "accentColor": "#FF9900", "captionPreset": "bold"}, tok)
    print("kit", st, kit.get("brandKitId"))
    st, b = e.call("POST", "/batches", {"assetId": video, "count": count, "recipe": "sports", "lengthSec": 25,
                                        "brandKitId": kit["brandKitId"], "instruction": "브랜드 킷을 적용해줘"}, tok)
    print("batch", st, b.get("batchId"), [(i["title"], [round(x) for x in i["range"]]) for i in b.get("items", [])])
    t0 = time.time()
    while time.time() - t0 < 1500:
        time.sleep(30)
        b = e.call("GET", f"/batches/{b['batchId']}", tok=tok)[1]
        print(f"  [{int(time.time() - t0)}s]", [(i["version"], i["busy"]) for i in b["items"]])
        if not any(i["busy"] for i in b["items"]):
            break
    for i in b["items"]:
        tl = e.call("GET", f"/projects/{i['projectId']}", tok=tok)[1]["timeline"]
        main = tl["tracks"][0]["items"]
        logo_items = [x for x in tl["tracks"][1]["items"] if x.get("assetId") == lid]
        srcs = [(round(x["srcIn"]), round(x["srcOut"])) for x in main]
        print(f"- {i['title']} range={[round(x) for x in i['range']]} v{i['version']} dur={sum(x['duration'] for x in main):.1f}s "
              f"clips={srcs} logo={'yes' if logo_items else 'no'} meta={tl['meta'].get('title')}")
    print("BATCH", b["batchId"])


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
