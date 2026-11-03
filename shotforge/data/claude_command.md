---
description: Create App Store screenshots with shotforge (separate image per page, per language; 2D/3D templates)
argument-hint: <screens_folder> "<App name>" "<what it does>" [2d|3d|template]
allowed-tools: Bash(shotforge:*), Read, Write, AskUserQuestion
---
The user wants App Store screenshots made from their real app screenshots. Arguments: $ARGUMENTS
Reply in the user's language.

1. Run `shotforge doctor` and tell the user about missing dependencies. Then run
   `shotforge analyze <folder> --about "<description>"` to read the detected colour / UI theme / density and the rule-based 2D/3D recommendation.
2. In ONE message ask the user what they want to fix (skip anything already given; "auto" = you / shotforge decide):
   - main background colour (#hex), text colour, accent colour
   - number of pages (1-10; auto = 6-7)
   - 2D or 3D, or a specific template (`shotforge templates`)
   - languages (default en,tr) and whether they will write the headline text or you should
3. If the user writes their own text, save it to `texts.txt` and pass `--texts texts.txt` (missing parts are completed for them):
   ```
   # slide 1
   shot: 01_home.png          (optional)
   en: Title *accent* | Subtitle
   tr: Başlık *vurgu* | Alt başlık
   ```
4. Run (only add the flags the user actually decided; shotforge/Claude decides the rest):
   `shotforge create <folder> --app "<name>" --about "<description>" --langs en,tr --no-ask --sheet -o out`
   plus any of `--bg-color #… --text-color #… --accent #… --count N --dimension 2d|3d --template <name> --texts texts.txt`.
   The deliverables are SEPARATE image files per page per language (`out/en/01_….png` …); `--sheet` is only a review aid.
5. Summarise the printed "design decision" (template, 2D/3D reason, colours, page count). Open `out/_review/overview_*.jpg` with Read and check
   text overflow, wrong translations, screenshot/headline mismatch and unreadable contrast. Fix problems in `shotforge.copy.json` and
   redraw with `shotforge render -o out --sheet`.
6. Do not upload anything to App Store Connect unless the user asks.
