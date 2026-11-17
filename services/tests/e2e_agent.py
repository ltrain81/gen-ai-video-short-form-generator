"""E2E: create a project with an asset, send one chat turn, wait, print the reply and resulting timeline.

  uv run python tests/e2e_agent.py <assetId> "make a 45s vertical highlight of the goals"
"""
import json
import sys
import time

import e2e_ingest as e


def main(asset_id: str, prompt: str):
    tok = e.token()
    st, proj = e.call("POST", "/projects", {"title": prompt[:60], "synopsis": prompt, "aspect": "9:16",
                                            "assetIds": [asset_id]}, tok)
    print("project", st, proj)
    pid = proj["projectId"]
    print("send", e.call("POST", f"/projects/{pid}/chat", {"text": prompt}, tok))
    t0 = time.time()
    while True:
        time.sleep(10)
        st, chat = e.call("GET", f"/projects/{pid}/chat", tok=tok)
        if st != 200:
            print(st, chat)
            break
        print(f"  [{int(time.time() - t0)}s] busy={chat['busy']} messages={len(chat['messages'])}")
        if not chat["busy"] and len(chat["messages"]) >= 2:
            break
        if time.time() - t0 > 900:
            break
    for m in chat["messages"]:
        print(f"--- {m['role']}: {m['text'][:1500]}\n    tools={m.get('tools')} error={m.get('error')}")
    st, p = e.call("GET", f"/projects/{pid}", tok=tok)
    tl = p["timeline"]
    print("version", p["project"]["currentVersion"], "aspect", tl["settings"]["aspect"], "meta", tl["meta"])
    for tr in tl["tracks"]:
        for it in tr["items"]:
            print(f"  {tr['id']:3} {it['type']:7} {it['start']:7.2f}+{it['duration']:5.2f}",
                  it.get("srcIn"), it.get("srcOut"), (it.get("text") or " ".join(w["w"] for w in it.get("words", [])))[:50],
                  "| why:", (it.get("provenance") or {}).get("reason", "")[:80], "| reframe:", json.dumps(it.get("reframe"))[:80])
    print("PROJECT", pid)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
