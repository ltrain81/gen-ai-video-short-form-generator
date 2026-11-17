"""Format recipes (L1) and workspace skills (L5).

Recipe = a reusable format: default aspect/length/caption style + an instruction that is composed into the agent
prompt + the skill to apply. Built-ins ship here; workspaces can add their own (TENANT#{t} / RECIPE#{id}).
Skill = an editing playbook (SKILL.md). Built-ins are files in shorts/skills; workspaces can add their own
(TENANT#{t} / SKILL#{name}); both orchestrators read them through the list_skills / read_skill tools.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import ddb

SKILLS_DIR = Path(__file__).parent / "skills"

BUILTIN_RECIPES: dict[str, dict] = {
    "highlight": {"name": {"ko": "하이라이트", "en": "Highlight"}, "skill": "highlight-reel", "aspect": "9:16", "lengthSec": 45,
                  "captions": "bold", "icon": "star",
                  "instruction": "Make a highlight reel of the strongest moments. Hook first, strongest-but-one last."},
    "recap": {"name": {"ko": "요약본", "en": "Recap"}, "skill": "recap-summary", "aspect": "16:9", "lengthSec": 180,
              "captions": "clean", "icon": "file",
              "instruction": "Condense the material into a chronological recap that keeps each chapter's key point."},
    "supercut": {"name": {"ko": "슈퍼컷", "en": "Supercut"}, "skill": "supercut", "aspect": "9:16", "lengthSec": 40,
                 "captions": "pop", "icon": "search",
                 "instruction": "Collect every instance of the subject the user names, tightly cut, in a clear order."},
    "broll": {"name": {"ko": "토킹헤드 + B-roll", "en": "Talking head + B-roll"}, "skill": "broll-insert", "aspect": "9:16",
              "lengthSec": 60, "captions": "clean", "icon": "video-camera-on",
              "instruction": "Keep the speaker as the spine and illustrate key claims with archive B-roll on the overlay track."},
    "topn": {"name": {"ko": "Top N", "en": "Top N"}, "skill": "top-n-list", "aspect": "9:16", "lengthSec": 50,
             "captions": "bold", "icon": "list-view",
             "instruction": "Make a ranked countdown (default Top 5) with a number card per entry."},
    "slideshow": {"name": {"ko": "슬라이드쇼", "en": "Slideshow"}, "skill": "slideshow-photos", "aspect": "9:16", "lengthSec": 30,
                  "captions": "minimal", "icon": "grid-view",
                  "instruction": "Build a slideshow from the attached images/pages with gentle motion and music."},
    "lecture": {"name": {"ko": "강의 클립", "en": "Lecture clip"}, "skill": "lecture-talk", "aspect": "9:16", "lengthSec": 60,
                "captions": "clean", "icon": "contact",
                "instruction": "Cut one complete idea from the talk; keep slides/board readable (split layout when needed)."},
    "nature": {"name": {"ko": "풍경·자연", "en": "Nature / scenic"}, "skill": "nature-visual", "aspect": "9:16", "lengthSec": 30,
               "captions": "none", "icon": "view-full",
               "instruction": "Visual-led reel: strongest shots cut on shot changes or beats; no captions unless narrated."},
    "sports": {"name": {"ko": "스포츠 하이라이트", "en": "Sports highlights"}, "skill": "sports", "aspect": "9:16", "lengthSec": 45,
               "captions": "bold", "icon": "flag",
               "instruction": "Each event = build-up + moment + reaction; chronological; keep the scoreboard readable."},
    "interview": {"name": {"ko": "인터뷰 클립", "en": "Interview clip"}, "skill": "interview-podcast", "aspect": "9:16",
                  "lengthSec": 45, "captions": "karaoke", "icon": "user-profile",
                  "instruction": "Pick the strongest question → answer; open on the best line; show the question as text."},
    "tutorial": {"name": {"ko": "튜토리얼", "en": "Tutorial"}, "skill": "tutorial-howto", "aspect": "9:16", "lengthSec": 60,
                 "captions": "clean", "icon": "script",
                 "instruction": "Structure as numbered steps; speed up waiting; end with the result and a CTA."},
}
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


# ------------------------------------------------------------------ recipes
def list_recipes(tenant_id: str) -> list[dict]:
    out = [{"id": k, "builtin": True, **v} for k, v in BUILTIN_RECIPES.items()]
    for r in ddb.query(f"TENANT#{tenant_id}", "RECIPE#"):
        out.append({"id": r["SK"].removeprefix("RECIPE#"), "builtin": False,
                    **{k: r.get(k) for k in ("name", "skill", "aspect", "lengthSec", "captions", "icon", "instruction",
                                             "createdBy")}})
    return out


def get_recipe(tenant_id: str, rid: str) -> dict | None:
    if rid in BUILTIN_RECIPES:
        return {"id": rid, "builtin": True, **BUILTIN_RECIPES[rid]}
    r = ddb.get(f"TENANT#{tenant_id}", f"RECIPE#{rid}")
    return {"id": rid, "builtin": False, **{k: v for k, v in r.items() if k not in {"PK", "SK"}}} if r else None


def put_recipe(tenant_id: str, rid: str, body: dict, sub: str) -> dict:
    if not SLUG.match(rid) or rid in BUILTIN_RECIPES:
        raise ValueError("Recipe id must be a new lowercase slug (a-z, 0-9, -)")
    name = body.get("name")
    if isinstance(name, str):
        name = {"ko": name, "en": name}
    from . import timeline_ops
    try:
        timeline_ops._canvas_for(body.get("aspect", "9:16"), {})
    except timeline_ops.OpError as exc:
        raise ValueError(str(exc)) from exc
    item = {"PK": f"TENANT#{tenant_id}", "SK": f"RECIPE#{rid}", "name": name or {"ko": rid, "en": rid},
            "skill": str(body.get("skill", ""))[:64], "aspect": body.get("aspect", "9:16"),
            "lengthSec": int(body.get("lengthSec", 45)), "captions": str(body.get("captions", "clean"))[:20],
            "icon": str(body.get("icon", "settings"))[:32], "instruction": str(body.get("instruction", ""))[:4000],
            "createdBy": sub, "updatedAt": ddb.now_ms()}
    ddb.put(item)
    return get_recipe(tenant_id, rid)


def compose_prompt(recipe: dict, user_text: str, params: dict, lang: str = "ko") -> str:
    length = params.get("lengthSec") or recipe.get("lengthSec")
    captions = params.get("captions") or recipe.get("captions")
    name = (recipe.get("name") or {}).get(lang) or recipe["id"]
    parts = [f"[Format recipe: {name}]",
             f"Apply skill: {recipe.get('skill')}." if recipe.get("skill") else "",
             recipe.get("instruction", ""),
             f"Target length: about {length} seconds." if length else "",
             "No captions." if captions == "none" else (f"Caption preset: {captions}." if captions else ""),
             "Add background music if a suitable music asset exists." if params.get("music") else "",
             f"User request: {user_text}" if user_text else ""]
    return "\n".join(p for p in parts if p)


# ------------------------------------------------------------------ skills
def _parse_skill(text: str) -> tuple[str, str]:
    desc = next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("description:")), "")
    return desc, text.split("---", 2)[-1].strip()


def builtin_skills() -> dict[str, dict]:
    out = {}
    for f in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        desc, body = _parse_skill(f.read_text())
        out[f.parent.name] = {"name": f.parent.name, "description": desc, "body": body, "builtin": True}
    return out


def list_skills(tenant_id: str) -> list[dict]:
    skills = builtin_skills()
    for s in ddb.query(f"TENANT#{tenant_id}", "SKILL#"):
        name = s["SK"].removeprefix("SKILL#")
        skills[name] = {"name": name, "description": s.get("description", ""), "body": s.get("body", ""),
                        "builtin": False, "createdBy": s.get("createdBy")}
    return list(skills.values())


def get_skill(tenant_id: str, name: str) -> dict | None:
    custom = ddb.get(f"TENANT#{tenant_id}", f"SKILL#{name}")
    if custom:
        return {"name": name, "description": custom.get("description", ""), "body": custom.get("body", ""), "builtin": False}
    return builtin_skills().get(name)


def put_skill(tenant_id: str, name: str, description: str, body: str, sub: str) -> dict:
    if not SLUG.match(name):
        raise ValueError("Skill name must be a lowercase slug (a-z, 0-9, -)")
    if not description.strip() or not body.strip():
        raise ValueError("description and body are required")
    ddb.put({"PK": f"TENANT#{tenant_id}", "SK": f"SKILL#{name}", "description": description[:500], "body": body[:20000],
             "createdBy": sub, "updatedAt": ddb.now_ms()})
    return get_skill(tenant_id, name)
