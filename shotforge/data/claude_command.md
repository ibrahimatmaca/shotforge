---
description: Create App Store and Google Play images with shotforge (separate image per page, per language; 2D/3D templates)
argument-hint: [screens_folder] ["App name"] ["what it does"] [2d|3d|template]
allowed-tools: Bash(shotforge:*), Read, Write, AskUserQuestion
---
The user wants store images made from their real app screenshots. Arguments: $ARGUMENTS
Reply in the user's language. All arguments are optional: this conversation is where the user is guided, so ask here instead of stopping or telling them to use the terminal.

0. Start with what is missing from the arguments, before running anything else:
   - Screenshots: use AskUserQuestion with "Where do the screenshots come from?": a folder or files I have, or capture them with Maestro. Then ask in a plain message for the path; the user can paste it or drag the folder in. For Maestro also ask for the flows folder and the folder to write into.
   - App name, and what the app does in 1-2 sentences (blank means inferred from the screens).
   If `shotforge analyze` can't read the path, say why and ask again. Tell the user in one line what comes next: a few short question groups, then the images.
1. Run `shotforge doctor` and tell the user about missing dependencies. Then run
   `shotforge analyze <folder> --about "<description>"` to read the detected colour / UI theme / density and the rule-based 2D/3D recommendation.
2. Ask these BEFORE generating anything. Use AskUserQuestion in the groups below (it takes at most 4 questions per call; list your default
   first, and at most 4 options per question: offer other values through "Other"); without it, ask in one message. Skip what is already known or doesn't apply. A blank or "auto" answer means the default.
   - **Group 1**
     - Store: App Store, Google Play or both (default both).
     - If App Store: create Header and Search Results images? 5244x2950 (16:9, header + search), 3840x2560 and 1920x1280 (3:2, search only), or no (default all).
     - Languages, multi-select, English and Turkish first; other codes via "Other" (default en,tr).
     - Template family: 2D, 3D or auto (default auto). If 2D or 3D, offer names from `shotforge templates`.
   - **Group 2**
     - If App Store: device sizes, multi-select: iphone-6.9, iphone-6.5, ipad-13 (default iphone-6.9).
     - If Google Play: device sizes, multi-select: android-phone, android-tablet-7, android-tablet-10 (default android-phone).
     - Separate screenshots per language or platform? Layout: `<folder>/tr/`, `<folder>/android/`, `<folder>/android/tr/`, `ipad/`, `android-tablet/`, same file names (default one set; shotforge warns).
     - Number of pages 1-10 (default 6-7; Google Play takes at most 8).
   - **Group 3**
     - Headlines: you write them, the user writes them, or from a file; and the tone. Search keywords to target (default none).
     - Main background, text and accent colours as #hex (default derived from the screenshots).
     - App icon file for the header and feature graphic (default none).
     - App Preview screen recording, shared or per language (default skip; App Store only, needs ffmpeg; Google Play takes a YouTube link instead).
   - **Group 4**
     - Output: folder, zip or both (default both).
     - Where to write it (default `./shotforge-out/<app>`).
     - Folder layout: store folders or fastlane deliver/supply folders (default store folders).
     - Store test variants: 1, 2 or 3 sets (default 1), differing by copy, design or both.
   A `shotforge.toml` in the project already sets brand colours, font, logo, tone, languages or template: don't ask for what it sets.
3. If the user writes their own text, save it to `texts.txt` and pass `--texts texts.txt` (missing parts are completed for them):
   ```
   # slide 1
   shot: 01_home.png          (optional)
   en: Title *accent* | Subtitle
   tr: Başlık *vurgu* | Alt başlık
   ```
4. Run (only add the flags the user actually decided; shotforge/Claude decides the rest):
   `shotforge create <folder> --app "<name>" --about "<description>" --no-ask --sheet`
   plus any of `--store appstore|playstore|both --header-sizes 5244x2950,3840x2560,1920x1280|no --langs en,tr --devices … --preview [lang=]file --icon file --no-zip --zip-only -o <dir> --bg-color #… --text-color #… --accent #… --count N --dimension 2d|3d --template <name> --texts texts.txt --keywords a,b --layout fastlane --variants 2|3 --variant-by copy|design|both --brand file --maestro <flows>`.
   `--maestro <flows>` first runs the user's Maestro flows per language on an already booted device, writing into `<folder>`. Use it only if the user asks for it.
   The deliverables are SEPARATE image files: `<out>/<lang>/app-store/iphone-6.9/01_….png`, `<out>/<lang>/app-store/header-search/…`, `<out>/<lang>/google-play/phone/…`, `<out>/<lang>/google-play/feature-graphic/…`, plus `<out>/manifest.json` and `<out>/<app>.zip`.
   With `--layout fastlane`: `<out>/fastlane/screenshots/<locale>/…` and `<out>/fastlane/metadata/android/<locale>/images/…`; App Store header images go to `<out>/app-store-extra/`. With `--variants`, each set is under `<out>/variant-a/`, `<out>/variant-b/`, ….
   `--sheet` is only a review aid.
5. Summarise the printed "design decision" (template, 2D/3D reason, colours and their source, page count) and repeat every warning. Open `<out>/_review/overview_*.jpg` with Read and check
   text overflow, wrong translations, screenshot/headline mismatch and unreadable contrast. Fix problems in `<out>/shotforge.copy.json` and
   redraw with `shotforge render --copy <out>/shotforge.copy.json -o <out> --sheet`.
6. Do not upload anything to App Store Connect or Google Play unless the user asks.
