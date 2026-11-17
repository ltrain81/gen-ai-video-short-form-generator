---
name: vertical-reframe
description: How to convert wide footage to 9:16 / 1:1 / 4:5 well.
---
- Call `analyze_framing` for each kept range with a precise intent (what must stay visible). Apply its `reframe`
  via `edit_timeline` update {itemId, set: {reframe}}.
- Layout guide: one subject → crop; speaker + slide/board → split; wide scenery/groups → fit_blur; text-heavy
  graphics → letterbox.
- Never reframe items the user pinned. If the user drags the frame, keep their choice.
