---
name: vertical-reframe
description: How to fit wide (or any) footage into 9:16 / 1:1 / 4:5 / custom frames — crop, box, split, PIP, blur.
---
These are defaults, not limits — if the user describes a different composition, build exactly that.
- Call `analyze_framing` for each kept range with a precise intent (what must stay visible) and the aspect of the area
  the clip will fill (the canvas, or the box). Apply its `reframe` via `edit_timeline` update {itemId, set: {reframe}}.
- Layout guide: one subject → crop; speaker + slide/board → split; wide scenery/groups → fit_blur or box;
  text-heavy graphics → letterbox or box (fit contain).
- `box` puts the clip in any rectangle of the canvas, e.g. "1:1 game footage in a 9:16 frame with titles above and
  captions below" = layout box, box {x:0, y:0.2188, w:1, h:0.5625}, background black|blur|#hex; then place titles in
  the top band (y≈0.11) and captions in the bottom band (y≈0.89). Analyze framing with aspect "1:1" for that box.
- Speaker card (lecture/talk, "the person 1:1 in the middle, title above, captions below"): main clip layout box,
  box {x:0, y:0.22, w:1, h:0.5625}, `source` = the square around the speaker read from `look` (16:9 source → w = 0.5625,
  h = 1, x so the speaker is centred), background `gradient:#0b2a4a,#1fbf8f@160` or `{type: blur, amount: 40}`
  (or put a generated / uploaded image on the main track and the speaker on an overlay layer). Push-ins = keyframes
  scale 1 → 1.25 on the same clip (zooms inside the box). Title in the top band, captions under the box.
- Exact crops: `reframe.source {x,y,w,h}` cuts any region of the source (a slide, a face, a scoreboard) before the
  layout — combine with box (any rectangle) or an overlay layer with layout `native` (own aspect, sized/moved/animated by
  transform + keyframes) to build multi-panel layouts, picture-in-picture or zoom callouts of the same footage.
- Picture-in-picture / multi-layer: put clips on overlay tracks with transform {x, y, scale, rotation, crop}; add
  more overlay tracks with add_track if you need several layers.
- Always check the result with `preview_frames` before saying it is done.
- Never reframe items the user pinned. If the user drags the frame, keep their choice.
