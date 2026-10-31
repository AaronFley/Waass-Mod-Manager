# Changelog

The version number is maintained in a single place, `core/appinfo.py`, and is written into the exe's file properties at build time.

## 1.3.0 — 2026-10-31

**This release fixes the class of bug that only appears once you package.**
The data format is still v3, so a 1.2.9 `data/` folder works as-is — just swap
the exe.

- **Fixed: "Browse for folder" was broken in the packaged exe.** The 1.2.9 build
  worked from source but returned 500 when packaged: the CPython used for building
  has no Tcl/Tk, so `import tkinter` succeeds from source and the module is simply
  missing inside the exe — and "build succeeded", "all tests green" and "the exe
  starts" were all blind to it. Folder picking now uses Windows' own dialog
  (`IFileOpenDialog`, with an address bar and search), independent of whether the
  interpreter ships Tcl/Tk; the legacy folder browser and tkinter are fallbacks.
  If none of the three works, the UI says "paste the path instead" rather than
  showing `No module named 'tkinter'`.
- **Fixed: a failed migration was recorded as done.** When the backup can't be
  written (disk full, folder occupied, read-only), the app correctly stops without
  migrating and warns — but the step that records "this build has run" also stamped
  the data format as the new version. The next launch looked healthy, the failure
  was swallowed for good, and no retry ever happened. **Only a successful migration
  may advance the data version now**; a failure stays where it is and keeps warning
  on every launch.
- **A capability check at the end of the build**: the produced exe is asked what it
  can actually do (`Waass.exe --check`) — UI assets, core modules and the picker
  backend, one by one. Missing any of them fails the build, so "built fine but half
  broken" can't be shipped.
- **Unsupported methods return JSON 405** instead of a 501 HTML page for
  `PUT /api/state` (the UI only parses JSON; an `<html>` body turns "wrong method"
  into "parse error").
- **Full scans are serialised**: when the startup scan and a manual "Rescan"
  collide, one scans and the other reuses the result — the same disk isn't read
  twice, and the scan result can't be overwritten mid-flight.
- **Fixed: starting from source crashed on an English Windows.** The very first
  GitHub Actions run came back red across the board: `windows-latest` is an English
  image with the console code page set to cp1252, so the app's Chinese log lines hit
  `UnicodeEncodeError` on the first `print` and killed the process on its startup
  path. Invisible on a Chinese-locale machine. Importing `core` now switches the
  console to UTF-8 (code page first, then the standard streams as utf-8 with
  `errors="replace"`), so a character that can't be drawn becomes a question mark
  instead of taking the program down. A cp1252 regression step is pinned in CI.
- Absolute paths from the author's machine were replaced with neutral wording in
  the public docs.
- **Update checks work out of the box**: the app asks this repository's GitHub
  Releases by default, with nothing to configure in `config.json`. "Check for
  updates" only compares version numbers and links to the release page — it
  **never downloads or replaces files** by itself. You can still override the feed
  or turn checks off entirely (`update_check: false`).
- README screenshots are real 1.3.0 captures (one set per language), plus GitHub
  Actions: all tests, both cold starts and the source-mode capability check on
  Windows.
- **The project description now states what it is**: built for Chinese players,
  developed in a Chinese-language environment, with the CN-localization feature
  other managers don't have — and a complete English UI as well. The closing
  section says it plainly: MIT, most code built with AI, and anyone is welcome to
  change it, share it or localize it into another language.

## 1.2.9 — 2026-10-30

**Upgrading is now just "swap the exe".** This release turns "will my data survive
the next version" into an actual mechanism instead of a matter of luck — and every
launch checks itself, speaking up in the top banner when something needs saying.

- **Data compatibility module** (`core/compat.py`): `data/meta.json` records which
  build wrote this data (a data format version; currently v3). An existing user
  opening 1.2.9 for the first time is recognised as "has data, no version marker"
  (v2) — the whole data set is **copied to `data/_backup/pre-schema2-<timestamp>/`
  before anything is touched**, then migrated and stamped. Migration is idempotent:
  running it twice gives the same result, and an interrupted run resumes next launch.
- **A broken data file never disappears**: an unparseable `xxx.json` is renamed to
  `xxx.json.broken-<timestamp>` and kept. The app carries on as if that record were
  missing — **nothing is ever deleted**.
- **Downgrade protection**: if `data/` was written by a newer build, 1.2.9 switches
  to **read-only** (`WAASS_READONLY`). Everything stays browsable and searchable, but
  not a single byte is written, so an older build can't damage fields it doesn't
  understand. A red banner explains why, and it deliberately has no "dismiss" button.
- **Startup self-check** (`core/selfcheck.py`): every launch verifies whether the
  version just changed, whether this build understands the data format, whether the
  bookkeeping files parse, whether the data folder is writable, and whether the update
  source looks like a real URL. The result is written to `data/selfcheck.json` (last
  10 launches kept, so you can see when the version changed).
- **Top self-check banner**: green (just updated / data migrated), amber (a file was
  quarantined, the configured game folder is gone), red (data unreadable, read-only
  downgrade). Green and amber can be dismissed with "Got it"; **red never can** —
  dismissing it would hide the only explanation the user would ever get. Text is
  assembled from `code + params`, so it follows a language switch immediately.
- **After a version change it answers the question that matters**: "existing data
  checked out (N files read fine)" or "{n} data items could not be read" — not just
  a friendly welcome to the new version.
- Settings → About now shows "Data format: vX (program supports vY)" and a
  "Run check" button. The update-check text is assembled from `code` too, and its
  `window.open` (which produced an address-bar-less window inside `--app`) now goes
  through the server.
- The allow-list for opening links grew to include `github.com` (previously
  nexusmods only); every other domain is still refused.
- **Unfamiliar-machine audit (done before going public)**:
  - New startup check for "can the game folder be written". It only asks about
    permission and **writes nothing into the game folder** (it opens the directory
    itself with `CreateFileW` and lets Windows evaluate the ACL). Steam installs the
    game under `C:\Program Files (x86)` by default, which a normal user cannot write
    to — previously the user only hit `WinError 5` after clicking install; now an
    amber banner says "run as administrator" up front.
  - Permission errors on every endpoint are now one plain sentence
    (`srv.need_admin`) instead of a raw `PermissionError: [WinError 5]`; a missing
    file gets its own message too.
  - Browser detection now also covers Edge / Chrome installed under the user profile
    (`%LOCALAPPDATA%`).
  - Re-verified: the packaged exe contains only the Python standard library and
    `web/`, **no `data/` of any kind**; no author-machine paths in source, frontend or
    docs; the configuration ships no built-in defaults (everything is detected live);
    Chinese / space-containing paths, a clean install with no `mods` folder, a machine
    without any archive tool, and a dead network all have working fallbacks.
- New `tests/test_compat.py` (35 checks: fresh install / legacy migration / broken
  file quarantine / idempotency / read-only downgrade / just-updated / post-update
  data problems / unwritable data folder / unwritable game folder).
- **Policy**: from this release on, any feature addition, removal or behaviour change
  **must update both the Chinese and the English documentation** (built-in guide,
  README, CHANGELOG, SPEC). Recorded in `docs/SPEC.md` §31.

- **Wording fix**: the English UI called the remastered edition "(next-gen)",
  which contradicted the Chinese "重制版" and read like a different product.
  It is now **The Witcher 3 Remastered** throughout — settings, README, the
  built-in guide, the Nexus page copy and the SPEC. `NEXT-GEN` inside real mod
  package names is untouched, because that is the mod's own title.

## 1.2.8 — 2026-10-30

**The interface now speaks English.** A new button in the top bar ("EN" while in
Chinese, "中" while in English — it always shows what clicking it will switch to)
toggles the language; the first launch follows the system language (Chinese
systems get Chinese, everything else gets English). Both languages share one
catalog (`web/i18n/`), so the UI, dialogs and toasts never mix the two.

- Three-state language setting in `data/config.json`: `auto` (follow the system,
  default) / `zh` / `en`. Switching writes only the `lang` key — game folder,
  Nexus settings and everything else stay untouched.
- Roughly 960 UI strings are bilingual: main view, install preview, the CN
  localization panel, conflicts, update tracking, settings, load order and batch
  selection. Backend API messages come out in the current language too — server
  and frontend read the same JSON.
- Documentation ships in native English (not a literal translation): built-in
  guide `guide.en.html` (loaded automatically in the English UI), `README.en.md`,
  `CHANGELOG.en.md` and `docs/SPEC.en.md`.
- Fixed a hidden class of bug: three places in the frontend matched backend
  responses **by Chinese text** (install warning de-duplication, restore drift
  detection, the is-live highlight in update tracking). Under the English UI
  they would silently stop working — the backend now sends a stable `code`
  field and the frontend matches on that.
- Fixed a fatal race reproduced during testing: the full page reload triggered by
  switching languages lets the old page's "goodbye" beacon arrive **after** the new
  page's requests, so the server believed everyone had left and killed itself 6
  seconds later. The Edge window is a separate process and stayed open — the UI
  looked frozen (panels would not open, language switching did nothing). Every
  page now carries an identity token: late beacons from old pages are discarded,
  and the heartbeat reports the same token as a second line of defense.
- Also suppressed Edge's "translate this page?" prompt in the English UI (a
  `notranslate` declaration — the app is bilingual on purpose).
- New `tools/i18n_merge.py` (fragment merge + placeholder checks) and
  `tests/test_i18n.py` (key parity, no empty values, no CJK leftovers, plural
  pairs).

## 1.2.7 — 2026-10-30

This release fixes a real bug caught during acceptance testing: the window was closed, but the background process kept running.

Test record: at the 18:09 check the UI was already gone (0 visible windows), the process had been alive for 48 minutes, and it had to be killed by hand. This wasn't a fluke — the old exit path had only one route: the page fires a `sendBeacon` on `pagehide`, and the server waits 6 seconds for a new page to take over before exiting. **At the moment the window closes, that signal may never get out** (the renderer process is killed outright by the kernel while the packet is still queued), so the server waits forever for a hello that never comes, and the process is left behind in the background. The cost isn't just memory: the next double-click hits the single-instance lock and pops up "Waass is already running".

### Fixes

- **Exit no longer relies solely on that one front-end signal** (new `core/winwatch.py`). The server asks the OS directly whether a visible window titled "Waass Mod Manager" still exists: once the page has been loaded for 15 seconds, if no window is visible for 3 consecutive seconds and no requests have arrived in the last 8 seconds, it exits on its own. Measured (with `WAASS_KEEP` unset and no browser launched at all): exit in 18.8 seconds.
- **Why the "8 seconds without requests" check is still needed**: window-only detection misfires easily (the window may be repainting, or the user is dragging it back from a minimized state). Both conditions must hold before acting — better to wait a few extra seconds.
- **The check only applies to the standalone window the app opens itself.** The `--browser` fallback mode uses ordinary browser tabs — switching to another tab changes the title and would kill a program that's still in use.
- **Exit even when the shell never came up.** If the standalone window was requested but no page has loaded within 60 seconds → treat it as "window never started" and exit, instead of lying in the background all day.
- **A fuse on the cleanup step before exit.** The browser-cache cleanup can get stuck on antivirus software or file locks; a 3-second timer is now armed first and calls `os._exit` unconditionally — cleanup is just best-effort.
- **The front end pings `/api/health` every 15 seconds** (`web/static/app.js`). The "quiet" criterion above relies on it to tell "open but idle" apart from "already closed".
- The exit reason is written as one line to `data/lifecycle.log` (only the last 8KB kept), so next time this symptom shows up you won't have to guess which criterion triggered it.

### Documentation audit

At the user's request, every operating instruction in `web/static/guide.html` was checked against the code. **Three wrong statements were fixed**:

- The banner colors were described backwards. The text said "gold = update available, blue = not matched on Nexus", but there is actually a single amber banner showing both things side by side, separated by a vertical bar (the side-by-side layout landed in 1.2.6), plus an **orange** "N things need attention" banner that is the real warning.
- The load-order wording smuggled in unverified engine behavior ("earlier-loaded wins conflicts"). It now states only the program's own semantics: **lower number = higher priority; when two mods touch the same file, the smaller number wins**; ties are flagged by the program.
- The import formats omitted tar / gz (`SUPPORTED_EXT` in `core/archive.py` has 8 entries in total).
- The game-folder rule is now stated precisely: pick the level that contains **both `bin` and `witcher3.exe`**.

## 1.2.6 — 2026-10-30

This release fixes one thing: **mods couldn't tell which entry they correspond to on Nexus**.

It started after cleaning out RMMR: covers, authors, and update checks all stopped working, and clicking "Refresh metadata" did nothing at all. Digging in showed that sync wasn't broken — the start of the chain was gone. Nexus IDs used to come from only two sources: the old manager's records, and links written in readmes inside mod folders. Clean out the old manager and start with a fresh `data/`, and both sources drop to zero — 21 mods, not a single ID among them. **Any new user who never installed RMMR sees exactly this state the first time they open the app.**

### Added

- **Automatic ID identification** (`core/identify.py`), four tiers ordered by evidence strength: IDs you recorded yourself > this app's install records > the original package filename in the downloads folder (`Hoods 4242 3.7 …zip` — that ID is generated by Nexus itself, hard evidence) > links in the readme inside the mod folder. In testing on this machine, 16 of 21 mods matched automatically with **0 wrong matches**; of the remaining 4, 2 are self-made/ported mods that can't be found by search in the first place.
- **When it can't be sure, it doesn't guess.** The decision is based on "how many words differ", not a similarity score: `Hoods` vs `Hoods` at 0 difference is safe to bind; `Swords On Hip` vs `Swords On Hip When Cloaked` differs by 2 words, so the app would rather leave it blank and let you click. Same-name versions by other authors (`AutoLoot` has three implementations by different authors on Nexus) and translated versions (`Hoods - Chinese`) are never auto-bound.
- **For the ones it can't identify, candidates are ready and waiting for your click.** At startup the app searches for candidates in the background (merging the local downloads folder + Nexus search, marking "you have this package in your downloads folder"), the top of the UI shows "N mods not yet matched on Nexus", and inside, each mod has a "might be" button — one click completes the binding, and the cover and author fill in right away.
- **A "not on Nexus" flag.** Self-made and ported mods can't be found by search to begin with; flag them and the reminders stop — otherwise the banner would hang on them forever, and over time you'd learn to ignore all banners.

### Fixes

- **After cleaning out the old manager, covers/authors/update checks all broke.** ID sources grew from 2 to 5, and each identified ID is written to `data/nexus_ids.json` immediately, so deleting the original package from the downloads folder later loses nothing.
- **Clicking "Refresh metadata" said "everything is up to date".** With zero IDs, the server returned `requested: 0`, which the UI read as "already latest". It now honestly says "N mods still have no Nexus ID" and points you to the entry point.
- **Automatic sync at startup was silently skipped.** The entry point had `if (!S.mods.some(m => m.nexus_id)) return;` — a new user has no IDs, so no request was even sent, making it look like "this feature was never implemented". Now candidates are fetched even with no IDs, and the UI says so clearly.
- **Disabled mods (folders with the `~` prefix) couldn't see candidates.** The server keyed by `folder`, while the UI looked up by the `~`-stripped `key` — the two never matched.
- **Some mods never found any candidates.** Nexus's name search is "basically all words must hit"; the more words, the likelier nothing comes back: `auto loot enhanced` finds nothing, but relaxed to `auto loot` it returns six. The old code sent only one all-words query, so names like `AutoLoot Enhanced` ("feature word + adjective") always came back empty. Now the query relaxes tier by tier — full name → drop filler words → first two words → first word — and uses whichever tier finds results first.
- **The real match was kept out of the candidates.** Filler words and version prefixes like `enhanced`, `plus`, `redux`, `V1.1.7` were counted as "extra words" and penalized; `AutoLoot Enhanced` against `SmartAutoLoot Remaster V1.1.7` was penalized below the threshold. "Extra words" now ignores filler words, but **"differs by words" still counts `remaster`** — the Remastered edition sometimes really does get a separate page, and that difference must not be erased.
- **Candidate buttons were squeezed into a vertical stack.** Candidates used to live in the main content column (which is only 230px wide), so four buttons stacked one per row with names truncated into ellipses. They now get a full-width row laid out horizontally.
- The top banner used to show either "new version available" or "not matched on Nexus" — the latter was always covered. Now they show side by side.

### Changed: disabling now renames the folder

Disabling used to write `Enabled=0` into `mods.settings`. Now it renames the folder instead: `mods/<mod>` → `mods/~<mod>`. The Witcher 3 only loads folders starting with `mod`; anything starting with `~` it simply ignores — equivalent to not installed, but not a single file is touched.

- **`mods.settings` is no longer touched.** That file has zero comments and exactly one blank line between sections; Script Merger's parser tolerates no extra characters (adding a single comment line crashes it). It's left alone whenever possible.
- **One-step switch back, load order unchanged.** `Priority` is left as is, so after disable + re-enable the mod is back in its original slot — the old approach of editing `Enabled` lost that after re-sorting.
- The only case that still writes to it: on enable, if the entry is still `Enabled=0` (written by an older version or toggled off by hand), it is flipped to `1` — otherwise the game won't load the mod even after the folder is renamed back.
- The reminder "this mod was disabled the old way (folder rename); consider switching to `Enabled=0`" is gone — the old way is now the right way.

### Added: Script Merger setup guidance

The conflict panel only recognized `Merger` in the game root folder. New users who installed it elsewhere just saw "not found", with no idea what to do.

- When it isn't installed, two buttons appear: **Download from Nexus** (pointing to [mods/13076](https://www.nexusmods.com/witcher3/mods/13076)) and **I've installed it, pick the location** (native file picker, select its exe).
- The chosen path is saved as `merger_exe` in `config.json` and used directly next time; the button becomes "back to auto-detect".
- A note is included: **mods built for the Remastered version generally don't need merging**; you only need it when running many legacy mods, or when the game reports script errors at launch.
- If the chosen exe's name doesn't contain `merger` / `script`, a gentle "doesn't look quite right" note appears, but it isn't blocked.

### Added: user guide (Settings → User guide)

Nine sections, from "first launch" to "when things go wrong", **organized around reasoning** (the content lives in `web/static/guide.html`, loaded into a dialog for rendering — no separate browser window, because an `--app` window has no address bar and once you leave you can't come back).

- It explains the reasoning, not a button list: why disabling renames the folder, why lower `Priority` wins, why the app would rather leave a Nexus ID blank than guess.
- If the guide file is missing, only this page is lost; everything else keeps working.

### Fixes

- **The close button X on every dialog sat off-center in the top-right corner.** The browser default for `button` is `padding: 1px 6px`; under `box-sizing: border-box`, a 32px square loses 6px on each side, leaving an 18px content area that squeezes the 16px icon aside. `.icon-btn` now sets `padding: 0` + centering, and the one in the dialog title bar is shrunk to 26px.
- **`hoodsSwitchableCloak` was flagged as "pending translation" despite being fully CN-localized.** Two bugs stacked up: ① ownership of the menu XML was computed wrong — `mod_hoodsSwitchableCloak` vs `SwitchableCloak.xml` was judged an orphan (the extra `mod_` prefix in the key prevented matching), so the Chinese text in the XML had no owner; ② the panel path (`localize.inspect()`) never consulted `xml_takeover()` — only task-package generation did — so the two sides disagreed. Ownership is now "highest score by coverage", shared by the panel and the task package. This mod's pending count went from 4 to 0.
- A lurking variant of the same problem was fixed along the way: short names like `modCloak` used to grab the XML via "who contains whom"; now anything with coverage below 0.5 is rejected outright.

## 1.2.5 — 2026-10-30

The first release published externally. The focus of this version isn't features but "can someone else actually use it".

### Added

- **Automatic game-folder detection that actually works.** There used to be 7 hardcoded paths, one of which was the dev machine's own `D:\game\steam\...` — it hit on the author's machine by definition and almost never on anyone else's. Detection now probes three routes: Steam's `libraryfolders.vdf` (multi-drive, multi-library aware), the GOG registry (`gameID 1495134320`), and Epic's install manifests (`InstallLocation` in `*.item`), falling back to the public default locations only when nothing is readable.
- **Proper onboarding in the main area when no game is found.** It used to be a lone "not set" in the bottom-left corner while new users stared at an empty list. It now explains to pick the level containing the `bin` folder, with two buttons: choose game folder / search again.
- `tools/coldstart.py`: runs every read-only API against an empty `data/` + a fake game folder, verifying the "brand-new machine, first launch" path. `--no-game` runs it again for "game not installed on this computer".

### Fixes

- **Reordering wrote config for a mod that no longer exists.** `set_state()` creates the section by default, blindly writing out a `[xxx]` with no validation — in-game it becomes "mod not found", while the UI still returns `ok`. Every key is now verified against an actual folder under `mods/`; otherwise nothing is written and an error is reported.
- **Author names in the install preview weren't escaped.** `metaLine` in `install.js` was the one interpolation missing `esc()`; the value comes from a field returned by the Nexus API.
- **Dark theme flashed white on the first frame.** `run.py` hardcoded `background_color: #F4F4F7`, while dark has been the default since 1.2.3. The theme preference lives in the browser profile's localStorage, unreadable from the server, so it now uses dark `#1E1E1E` directly.
- **11px gray text lacked contrast.** `--text-3` was `#8A8478` in light mode (3.3:1) and `#807C74` in dark (4.0:1), both below WCAG AA's 4.5. Now `#6E695F` / `#9A958B` (5.0:1 / 5.6:1). The onboarding hint "tap + to create a group" in the sidebar was the most visible case.
- The version in the `Server` response header was stuck at `Waass/0.1`; it now follows `APP_VERSION`.

### Security

- **The `Host` header is now validated.** The token is written straight into the homepage HTML, and the homepage itself requires no auth — a malicious page could rebind its domain to `127.0.0.1`, and the browser would then request `/` as same-origin, read the token, and `install` / `uninstall` would both go through. Only `127.0.0.1` / `localhost` / `[::1]` are accepted now, and `Sec-Fetch-Site: cross-site` is rejected. The close-window `sendBeacon` (which can't carry custom headers) still works.
- The `key` parameter of `/api/open-folder` can no longer escape `mods/` via `..\..`: the resolved parent directory must be `mods` itself.

### Removed

- `core/names.py`, `core/nexus.py`, `tests/test_names.py`. The "CN names" feature was pulled from the UI back in 1.2.1; these files were its leftovers (`nexus.py` was referenced only by `names.py`, unreachable as a whole).
- The entire "install behavior / copy the original package after install" setting is gone (1.2.4).

### Release prep

- `.gitignore`: `build/`, `dist/`, `*.spec`, `data/`, `_backup/`, `_shots/` stay out of the repo. `data/` holds this machine's mod list, cover cache, snapshots, and CN-localization originals.
- The version log moved from README to this file; the README's first screen gained system requirements, screenshots, and two install paths.
- New `tests/test_http_guard.py` (Host validation, cross-site rejection, auth, path traversal) and `tests/test_paths_detect.py` (vdf parsing, Epic manifests, probe priority, cache invalidation).

## 1.2.4 — 2026-10-29

- **Fixed "it still says you haven't updated after you updated".** The local version used to take the first entry by "source trustworthiness", but after an update the source that wrote the file changes, so a freshly updated mod stayed stuck in "update available". All four sources (hand-entered / the mod's own `content/info.json` / install records / history) now count equally and **the highest version number wins**; an install record is voided once a package downloaded more than 60 seconds later supersedes it.
- Fixed a CN-localization false positive: the two rows for `name + version number` in the language table used to be counted as pending-translation English, so the panel would nag you to translate a mod that is already fully Chinese in-game.
- The sidebar shows the game version next to "game folder" (`5.01c`, read from an embedded literal inside `witcher3.exe` — not the PE version, which is `5.0.16.41566` and doesn't match).
- Nexus package names now recognize both separators: `X_13284_1.5.0_2026-…Z_token.zip` and `X 13284 1.5.0 2026-…Z token.zip`.
- "Install behavior" removed from settings; the `/` hint box to the right of the search box (and its shortcut) removed.

## 1.2.3 — 2026-10-29

A complete visual overhaul. Gold W logo + dark gray base; icon / taskbar / favicon / exe all refreshed.

- **Dark is the default on first launch**; light / dark / follow-system switching works as before.
- The list view became flat rows (no more per-row background — the whole column used to look selected).
- In the conflict panel, purely informational entries no longer glow gold — gold is the only accent color, and spreading it around made it impossible to tell which entries needed action.
- In dark mode, toggles calm down to ink-green `#4C9A77` (15 mint-green pills used to grab all the attention).
- Status bar text moved up to `--text-2`; contrast was insufficient before.

## 1.2.2 — 2026-10-28

- The top bar's "N updatable" gained an ×, so you can dismiss that batch; if the list actually changes (a new version appears), it comes back on its own. "Same batch" is judged by the list only, not by time.
- Disabled mods sort to the end of "all mods" — **display only; load order is untouched**. Disable and re-enable, and the mod is back in its original slot.
- The "follow system" icon in the theme switcher was redrawn as a half-light, half-dark circle.

## 1.2.1 — 2026-10-28

- CN-localization detection now recognizes the crude approach of directly editing menu files; previously, mods that were already fully CN-localized were reported again.
- The top bar gained a button for browsing Nexus any time.
- After startup, a sync runs automatically in the background (fetching covers + checking for new versions); the top banner appears only when there are results.
- Two dead entries removed: "CN names" and "installed packages" (the uninstall entry lives in the mod details).

## 1.2.0 — 2026-10-28

**CN localization becomes one click.** It used to mean per-mod extraction, exporting JSON, handing it to AI, copying the result back, and pasting it in — five round trips for 37 lines of menu text.

- Check mods → one-click translation → edit directly in the table → one write; the app does the translating itself, in about ten seconds.
- Translations use a key-free public API by default; you can also enter your own keys (one per line, used in rotation, so one key acting up doesn't sink the whole batch).
- **By default only menu pages are translated** — mod names and item names are left alone, because changing those tends to break alignment with scripts and save games.
- Originals are saved automatically before writing, so you can restore to a never-localized state at any time; if you localize twice in a row, you can undo just the second pass without wiping out the first.
- Writes keep the file size unchanged, so translations can't be longer than the original; text that doesn't fit is auto-shortened, and if it still doesn't fit, the entry is honestly marked "still a few bytes over" — never truncated.

## 1.1.1 — 2026-10-28

**Make invisible conflicts visible.**

- Conflict detection grew from one layer to three: same-named scripts, two mods' archives shipping the same UI file (the classic cause of "feature works, UI looks wrong"), and two mods annotating the same function (order-sensitive).
- Plus "the script is UTF-16 from the 4.x era and won't compile on 5.00" and "the CN language table version is outdated, so the UI shows `##`".
- Only enabled mods are checked; texture / model collisions inside archives are never reported (that's noise).
- New batch selection: check several mods and toggle them together; one undo restores them all.

## 1.1.0 — 2026-10-28

**Never let the user break things without knowing.**

- While the game is running, everything is read-only, with an explanation of why.
- If the config isn't restored after an uninstall, a must-click dialog explains it (three ways out, including one-click undo).
- Closing the window right after changing load order now gets a confirmation prompt.
- After Script Merger does its bookkeeping, the conflict panel refreshes itself.
- Visual alignment with Win11: theme follows the system, card radius 8px / control radius 4px, control height 32px.

## 1.0.10 — 2026-10-27

- Merged files (Script Merger's output) must have an entry in `mods.settings` with the strictly smallest `Priority`. Without it they sort behind every registered mod, the source mod ends up overriding the merged result, and the game fails at launch with "Could not find function XXX". Every code path that writes `mods.settings` now goes through this guard.
- "Status → merged" is a read-only view of which scripts were merged, from which mods, and whether they are registered correctly.

## 1.0.9 — 2026-10-26

"Latest version" now reads the **main file version** on Nexus instead of the author-entered number on the page — the latter is often stale, showing "already up to date" as "local is newer than online".
