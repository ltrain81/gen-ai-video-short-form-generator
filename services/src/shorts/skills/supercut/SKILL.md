---
name: supercut
description: Collect the same kind of moment across one or many videos (every time X is said or shown).
---
Defaults, not limits: the user's explicit request always wins over anything below.
- `search_archive` for the phrase/visual across the archive; for spoken phrases use transcription weight high and
  confirm exact word timing with `read_transcript`; for visuals use visual weight high and `look`.
- Tight cuts: 0.3 s before to 0.5 s after the moment. Keep framing consistent unless the user wants variety.
- Order by time, by video, or by build-up (calm → intense) — pick one and say which.
