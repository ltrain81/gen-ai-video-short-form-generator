---
name: lecture-talk
description: Edit speech-led material: lectures, talks, keynotes, webinars, classes with slides or a board.
---
Defaults, not limits: the user's explicit request always wins over anything below.
Signals: content profile primarySignal=speech, high wordsPerMinute, onScreenTextShare > 0.3, shot types slide /
screen_recording / medium on one speaker.

How to cut
- Build from the transcript (`read_transcript`). Select complete thoughts: one claim + its explanation or example.
  By default don't cut mid-sentence: `snap` every in/out to "sentence" (unless the user wants tighter jump cuts).
- Hook = the most quotable or surprising sentence; state the topic within the first 3 s.
- Keep what is on the slide/board when the speaker refers to it ("as you see here", formulas, diagrams):
  `look` at the range; if the slide carries meaning, use layout "split" (speaker top, slide bottom) via
  `analyze_framing` with intent "keep the speaker and the slide/board readable". If there is no visible speaker,
  use "fit_blur" or "letterbox" so text is not cropped.
- Remove filler: long pauses (> 1.2 s), repeated starts, "um/uh" runs — split around them.
- Captions on (preset "clean" or "bold"); put key terms in a title text item when the slide is not shown.
- Long-form recap: one chapter per topic using get_asset_overview chapters; 1–3 min each.
- Topic series (one short per concept, e.g. "NAT Gateway", "Internet Gateway" from a 1-hour lecture): use
  `plan_topic_series` (transcript + Marengo + Pegasus fused, sentence-snapped), confirm with the user, then
  `create_series`. Inside each short: open with the term as a title, keep the definition + one example, show the slide.
