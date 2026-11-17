---
name: highlight-reel
description: Generic best-moments cut from any long video when no more specific skill fits.
---
Defaults, not limits: the user's explicit request always wins over anything below.
- Candidates: `get_scenes` highlight_score ≥ 4, plus `search_archive` with the user's goal; verify with `look`.
- Respect the content profile: speech-led → sentence-snapped quotes; visual-led → shot-snapped visuals.
- 3–8 moments for a 30–60 s short. Strongest moment first (hook), second-strongest last.
- If more than ~10 good candidates exist and the user's intent is unclear, `ask_user` kind "clips" with the top 6.
