---
name: sports
description: Edit sports and game footage: match highlights, goals, plays, athlete moments.
---
Defaults, not limits: the user's explicit request always wins over anything below.
- Find events: `ask_video` for "list every goal/score/key play with timestamps" on each chunk, plus
  `search_archive` ("goal celebration", "save", "crowd") with visual + audio weights.
- A highlight = build-up (3–6 s) + the event + reaction/replay (2–4 s). Do not start at the exact moment of the
  goal; include the pass/run before it.
- Order chronologically unless the user wants a ranking (then use the top-n skill).
- Score graphics: if the broadcast shows a scoreboard, keep it readable or add a small text item with the score.
- Vertical: `analyze_framing` intent "follow the ball and the main player"; replays from a wide angle → "fit_blur".
- Crowd audio matters: keep original audio; music only under transitions or at low volume.
