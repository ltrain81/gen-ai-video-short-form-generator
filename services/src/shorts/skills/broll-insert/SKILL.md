---
name: broll-insert
description: Talking head / narration with supporting B-roll and archive footage on top.
---
Defaults, not limits: the user's explicit request always wins over anything below.
- Main track v1 = the speaker (speech-led cut). Overlay track o1 = B-roll that illustrates what is said.
- For each claim, `search_archive` the concept (visual + description weights) and add clips on o1 aligned to the
  sentence (`start` = sentence start in timeline time). 2–5 s per insert by default; leave the speaker visible for at least
  1/3 of the runtime.
- Images and document pages work as B-roll too (Ken Burns is added automatically).
- If nothing in the archive fits, tell the user what is missing (and in generation mode offer to generate it).
