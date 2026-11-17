---
name: nature-visual
description: Edit visual-led footage: nature, travel, landscapes, aerials, cityscapes, product beauty shots, b-roll reels.
---
Defaults, not limits: the user's explicit request always wins over anything below.
Signals: primarySignal=visual, low wordsPerMinute, many wide/b_roll shots, little on-screen text.

How to cut
- Search and rank by visuals: `search_archive` weights {visual: 2.5, description: 1.5, transcription: 0.2}.
- Prefer shots with clear motion or a reveal; check with `look`. Use `get_scenes` highlight scores as a hint only.
- Cut on shot changes (`snap` "shot") or on music beats (`snap` "beat" against the music asset) — keep a steady
  rhythm (e.g. 1.5–3 s per shot for 9:16 reels; longer, 4–8 s, for calm long-form).
- Ignore speech unless the user asks for narration; mute dialogue (`update` volume 0) if it distracts.
- Vertical from wide: `analyze_framing` with intent describing the subject (animal, waterfall, horizon). Wide
  scenery that loses meaning when cropped → "fit_blur".
- Add music (a1) and fades; no captions unless there is narration or the user asks for them. Optional title card at the start.
