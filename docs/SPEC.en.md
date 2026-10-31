# Waass Mod Manager — Technical Specification

> This document is the source of truth. If the code disagrees with this document,
> this document wins. If this document disagrees with reality, reality wins —
> and the measured result must be written back into this document.

## 1. Directory layout

```
<game root>\                        D:\game\steam\steamapps\common\The Witcher 3
├─ bin\x64_dx12\witcher3.exe        the sole criterion for "is this The Witcher 3"
├─ bin\x64_dx12\*.dll               where injectable mods read/write (e.g. dsound.dll / w3loadfix.dll)
├─ bin\config\r4game\user_config_matrix\pc\
│    ├─ audio.xml … localization.xml   10 shipped with the game, never counted as mods
│    ├─ <ModName>.xml                  a mod menu that is in effect
│    └─ <ModName>.xml.disabled         a mod menu disabled by renaming
├─ mods\modXXX\                     the body of a content mod
│    └─ content\scripts\  content\*.w3strings  bin\config\...\pc\*.xml
├─ dlc\dlcXXX\                      a resource pack bundled with some mod
└─ ~modXXX  / ~dlcXXX               a folder name starting with ~ = disabled (the legacy method)

<user config directory>\             D:\1\The Witcher 3 (the Documents folder is redirected here)
├─ mods.settings                     the single authority for enable state and load order
├─ user.settings / input.settings    game settings
└─ gamesaves\
```

## 2. Enable / disable: three mechanisms, all must be recognized

| Mechanism | Appearance | Notes |
|---|---|---|
| Folder rename | `~modX` | **This program's write mechanism as of 1.2.6.** The engine only scans directories starting with `mod`; a `~` prefix is equivalent to "not installed" |
| `mods.settings` | `[modX]` + `Enabled=0` | Officially supported; this program only reads it and no longer uses it as a switch |
| Menu rename | `X.xml.disabled` | Only disables the settings menu; the main functionality keeps working |

This program's principles:

- When reading, recognize all three; if any one says "disabled", treat the mod as disabled.
- **When writing, only rename folders** (`actions.set_enabled`):
  - Disable = `modX` → `~modX`; enable = rename back. Each is a single `rename`,
    logged in the journal and therefore reversible.
  - **Do not write `mods.settings`** — that file has zero comments and exactly one
    blank line between sections, and Script Merger's parser tolerates no extra
    characters. If we don't have to touch it, we don't.
  - `Priority` is preserved as-is, so after disable → enable the load order is unchanged.
- The one exception: when enabling, if the entry in `mods.settings` is `Enabled=0`
  (left over from an older program version or toggled by hand), it must be flipped
  to `1` — otherwise the game still won't load the mod even though the name is back.
- A DLC's enable state follows its main mod (also via the `~` prefix).

## 3. mods.settings hard rules

The format is extremely brittle. **Script Merger's parser does not support comments**;
adding one makes it crash with a `NullReferenceException` (hit in practice on 2026-10-20).

```
[modModSettingsMenuFix]
Enabled=1
Priority=10

[modHoods]
Enabled=1
Priority=100
```

1. The first character of the file must be `[`.
2. Each section is exactly three lines: `[name]` / `Enabled=` / `Priority=`.
3. Exactly one blank line between sections.
4. Zero comments, zero headers, zero prose.
5. No BOM.

## 4. Load order

- Lower numbers load first (higher priority).
- Duplicate `Priority` values make Script Merger report "identical load order".
- Numbering convention: reserve gaps per functional group (10/20/30…); don't use
  consecutive 1/2/3.
- Suggested baseline: `mod0000_MergedFiles` = 0 (if script merging has been done).

## 5. Nexus API (v1)

> **Status at 1.0:** the UI no longer calls the API. Tracking is now a purely local
> ledger in `core/track.py` (ID / page URL / local version + a jump button);
> `core/nexus.py` is kept but not wired in. The notes below are retained as a
> reference for a future "optional online check" feature.

- Auth: request header `apikey: <Personal API Key>`
- Single mod: `GET https://api.nexusmods.com/v1/games/witcher3/mods/{mod_id}.json`
  → `version` / `updated_timestamp` / `author` / `category_id` / `picture_url`
- File list: `GET .../mods/{mod_id}/files.json`
- Tracked mods: `GET /v1/user/tracked_mods.json`
- Key validation: `GET /v1/users/validate.json`

**Download restrictions (this caps what auto-update can ever do)**

| Account | Direct download link via API? |
|---|---|
| Premium | Yes — call `files/{file_id}/download_link.json` directly |
| Free | No — must carry the `key` + `expires` from a web `nxm://` link |

So for free accounts, "auto update" is implemented as: register the `nxm://` protocol
→ the user clicks "Mod Manager Download" on the web page → this program takes over
→ exchanges the credentials in the link for a direct URL → download and install.
The user's action is "one click"; everything else is automatic.

- Rate limiting: ≥ 1.05 s between individual requests; back off and retry on 429.

## 6. Safety constraints (non-negotiable)

1. Scanning is read-only, end to end.
2. Before any write operation, snapshot the affected files to `data/snapshots/<timestamp>/`.
3. Uninstall = move to `data/quarantine/`; never truly delete.
4. `witcher3.exe` running → refuse all writes.
5. Every write goes onto the undo stack (journal); one-click rollback is supported.
6. Never touch game-owned files (`content\`, `bin\x64_dx12\*.exe`, saves).
7. The server listens only on `127.0.0.1`, with a one-time token.

## 7. Known implementation difficulties

- **w3strings**: current community tools (including this repo's
  `witcher3/tools/w3strings_zh.py`) can only do "equal-length replacement" —
  the Chinese text must be shorter than the original. Free-length Chinese requires
  fully reading and writing the string index table. This is a prerequisite for the
  M6 CN localization feature.
- **Mod attribution**: there is no standard manifest file. This program uses a
  three-tier strategy: RMMR archive → folder-name similarity → Nexus links in the readme.
- **Script conflicts**: a real conflict verdict requires parsing `.ws` logical paths
  and comparing them. RMMR delegates that to Script Merger; this program first does
  logical-path comparison (coverage detection), and genuine script merging should
  still be left to Script Merger.

## 8. Data directory and lifecycle (settled in 1.0.1)

### 8.1 Third-party data is migrated exactly once

On the first scan, read other managers' archives once, move Nexus ID / author /
category / cover into `data/legacy.json` and `data/thumbs/`, and write `migrated_at`
as the "already migrated" marker. Afterwards **the scanner never touches that
software again** (`scanner.py` only calls `legacy.load()`).

- If migration finds nothing (the other manager was never installed), no marker is
  written — so if it shows up later, migration still happens automatically.
- "Import again" in settings goes through `force=True`.
- `legacy.forget()` clears the metadata only; it never touches mods, thumbnails,
  or Chinese display names.

> Rationale: independent operation. Previously every scan read an external archive,
> which chained this program's availability to someone else's software.

### 8.2 The UI window is a browser kernel and must be tamed

The UI opens a standalone window via `msedge/chrome --app` with
`--user-data-dir=data/webview`. **Pitfall we hit**: with a brand-new user-data-dir
the browser behaves like a fresh install, performs implicit sign-in with the
Windows-connected Microsoft account, and pops "We're syncing your browsing data
across all your devices".

Required flags (`open_app_window` in `run.py`):

```
--no-first-run --no-default-browser-check --no-service-autorun
--disable-sync                       # sync off
--disable-features=msEdgeIdentityFeatures,msImplicitSignin,msEdgeSyncPromo,
                   EdgeSyncPromo,SigninInterception,msEdgeFirstRunExperience
--disable-background-networking --disable-component-update --disable-breakpad
--disk-cache-size=16777216 --media-cache-size=1048576
```

Additionally, pre-seed the `webview/First Run` marker file, and clear the cache
once before launch and once after exit (`storage.trim_browser_cache()`).
Measured: 12 MB while running → 2.2 MB after exit.

Keep `Default/Local Storage` (theme, last view) and `Network` (deleting it
re-triggers sign-in).

### 8.3 Process lifecycle

On page `pagehide`, the front end sends `navigator.sendBeacon('/api/bye?t=<token>')`
and the server records the time; a watchdog checks every 1 second, and if no new
page has appeared within 6 seconds (`GET /` clears this marker) it calls
`storage.trim_browser_cache()` and then `os._exit(0)`.

This leaves no background process behind, and also avoids killing the service on
an F5 refresh.

### 8.4 Cleanup boundaries (hard rules)

- Deletion candidates come only from relative paths hardcoded in `storage.GROUPS`;
  externally supplied paths are never accepted.
- Every path to delete must pass `_inside_data()` (inside `DATA_DIR` and not
  `DATA_DIR` itself) and `guard.assert_can_write()` (test mode forces a sandbox;
  read-only mode refuses everything).
- Core data (`config/track/names/library/installed/legacy/integrity/journal` +
  `saves/`) never enters any cleanup list; it is only size-reported.
- Automatic caps: 30 snapshots, 20 CN-localization backups, 200 journal entries,
  `crash.log` overwritten in place.

## 9. Automatic Nexus metadata (1.0.2)

### 9.1 Where the data comes from: the site's internal query endpoint

`POST https://api-router.nexusmods.com/graphql`.

**What it is**: the query endpoint the site's own web frontend uses — read-only, public
data only, no login, no Key.

**Risk**: it is not in any public documentation; it is an internal endpoint of the site.
It can be changed, rate-limited, or taken down at any time, with no version compatibility
promise.

**If it stops working**: there is no fallback to any other non-public endpoint, and no
page scraping either. No backup endpoint — when it fails, it fails: entries show
*unrecorded*, and install, scan, and uninstall are not blocked. Missing metadata only
affects what is displayed.

**How requests identify themselves**: `User-Agent: WaassModManager/<version>` plus
`Application-Name` / `Application-Version`. Honest identification, not a browser
disguise.

The shape of the interface (follow this in code, do not guess):

- the query exposes `legacyMods(ids:[{gameId,modId}])` and `mods(filter:...)`, returning
  a `Mod` type: `name / author / version / summary / category / pictureUrl /
  thumbnailUrl / downloads / endorsements / updatedAt / adultContent`;
- the witcher3 `gameId` is fixed at **952**.

> The same wording sits at the top of the module docstring in `core/nexus_meta.py`.
> Change one, change the other.

### 9.2 Filename parsing: both generations must be recognized

This is the most critical link — if the ID can't be parsed, everything downstream
is empty. Nexus download names come in two generations:

```
New: Light_Rewrite_12144_0.26.2_2026-10-23T03-19Z_864tMl89U.zip
Old: Hoods-4242-3-7-1698765432.zip
     All Quest Markers-13087-1-7-3-1698765432.7z
     UI Remastered - Next Gen-14578-1-1-2-1698765432.zip
```

**The old format has a variable number of version segments** (1–4) and mixes `-`
with `_`; no single regex can chew through it. The approach is to count from the right:

1. The last segment is 9–13 pure digits → Unix timestamp;
2. Moving left, consecutive numeric segments (up to 4) → version;
3. The next segment to the left must be **3–6 pure digits** → Nexus ID; otherwise
   the whole name is judged "unrecognized";
4. Whatever remains is the name.

The digit-count limit in step 3 is the key anti-false-positive guard: in
`Vesemir-2-1-1698765432.zip` the `2` is a single digit — better to fail to
recognize than to guess an ID, because a wrong guess pastes another mod's author
and cover onto this one.

### 9.3 Four sources for the ID (by precedence)

| Order | Source | When |
|---|---|---|
| 1 | `data/track.json` | Manually entered / search-confirmed; always highest |
| 2 | `data/legacy.json` | Archive migrated once from the old manager |
| 3 | README / `.url` in the mod folder | Contains a nexusmods link |
| 4 | Filename | See 9.2 |

### 9.4 Data flow

```
Install → parse filename for ID → refresh() → cache data/nexus/<id>.json (7 days)
                           └→ thumbnail → data/thumbs/nexus_<id>.<ext> (~30 KB)
track.apply() reads cache only (no network) → attaches to ModRecord → UI
Startup → background _bg_enrich(): batch-query expired/missing IDs once, then done
```

`track.apply()` **never touches the network** — it runs on every UI refresh.
Network happens in exactly three places: at install time, in the startup background
pass, and when the user clicks "refresh metadata".

### 9.5 Boundaries (hard rules)

- POST only to that single GraphQL endpoint, only reading public data; no login,
  no account, no mod file downloads.
- Global minimum interval 0.8 s; one batch at most 60 IDs (measured: 17 IDs in
  1.08 s per batch).
- After a failure, cool down 5 minutes and don't retry within that window; all
  failures `return {}` — never block install/scan.
- Cache 7 days; only `force=True` (manual refresh) bypasses it.
- Settings switches `nexus_auto_meta` / `nexus_auto_cover` turn the whole chain
  off, degrading to purely local data.
- Cleanup is governed by `storage`'s `nexus` group (safe tier — deleting it just
  triggers a re-fetch).

### 9.6 Search pairing offers candidates only; never auto-selects

`mods(filter:{nameStemmed:{value:<keyword>,op:MATCHES}})` is stemmed matching and
very loose: searching "Hoods" surfaces the French translation, the Chinese
translation, and "Dynamic Swords on Hip". So the program only lists candidates
(sorted by downloads), and **a human must click one to confirm**. Auto-picking the
first result has roughly even odds of attaching the wrong mod's metadata — worse
than showing nothing.

When the search finds nothing, two paths are offered: open the website and search
yourself (`/search/?gsearch=`), or type the ID manually.

## 10. The "read" ledger for UI notices (1.0.3)

The notice at the top (`N items need attention`) is a **status broadcast**, not a
todo list. Without read-state, as long as the status is unchanged it lights up on
every launch and looks like a procession of unresolved errors.

Ledger: `data/ui.json`

```json
{ "dismissed_warns": ["conflict_folder~disabled|modXXX", "..."], "dismissed_dl": "3f9a1c..." }
```

- **Warnings are recorded per item.** key = `f"{code}|{target or ''}"`, truncated
  to `_WARN_KEY_MAX`. Recorded warnings are removed from `ui.warns`; only the
  remainder reaches the UI. Warnings with `level == "info"` never enter this
  channel and don't count toward the number.
- At most 200 entries are kept (`KEEP_MAX`) so the file can't grow forever.
- Deleting `ui.json` has no side effects other than notices starting fresh.

API: `POST /api/ui/dismiss`, body `{kind:"warn", keys:[...]}`, returns the new `ui`
section; the front end replaces `S.data.ui` directly instead of re-fetching the
full state.

**Change in 1.0.7**: the `N new packages in the download folder` notice was removed
entirely — the package count represents nothing that needs handling (user's words:
"useless"). Changes:

- `server.py::_banner_state()` no longer returns a `dl` section and no longer calls
  `downloads_summary()` (saves one download-folder scan); `/api/state` no longer
  ships a `downloads` section.
- `web/static/app.js::renderStatus()` drops the whole chip rendering; `index.html`
  drops the `#btnNewArchives` element; the corresponding click listener in `app.js`
  is removed too (calling `addEventListener` on a nonexistent element crashes the
  entire script).
- The sidebar "Import archives" button is the replacement entry point and remains.
- The `dismissed_dl` bookkeeping in `core/ui.py` is **kept but unused**, so
  restoring the notice later wouldn't require a rewrite.

## 11. System dialogs must use the native API (1.0.3)

`core/picker.py`. **Do not switch back to tkinter.**

tkinter's `filedialog` is a standalone Tk toplevel window, and the caller is an
HTTP request thread (a background thread). Windows' foreground-focus rules push
such a window behind others — the user clicks "read from file", nothing seems to
happen, and the dialog is actually hiding behind the main window. Cancel produces
no callback either, so nothing happens at all.

The native `comdlg32.GetOpenFileNameW` has two decisive advantages:

1. `hwndOwner` is set to the main window (`_owner_hwnd()`, found by the title
   `Waass Mod Manager`), so the dialog always sits on top of the main window and
   minimizes/closes with it;
2. Filters are system-style and can be split by `kind` (`json` offers only
   `*.json`; `archive` offers `*.zip;*.7z;*.rar`).

Details:

- `CommDlgExtendedError() == 0` means the user cancelled → return `""`, not an
  error; only non-zero raises `OSError`.
- `OFN_NOCHANGEDIR` must be set, or the system silently changes this process's
  working directory.
- **Directory picking still uses Tk.** In practice calling `SHBrowseForFolder`
  from an HTTP thread takes half a minute to return, while Tk's folder dialog
  responds immediately. This is the one place Tk is kept; the reason is documented
  in `picker.pick`'s docstring.

Front-end counterpart: after picking, always give a receipt —
`read N entries, M already have translations` (`POST /api/localize/peek`).
Cancellation must also produce a notice, otherwise the user still can't tell
"nothing picked" from "something broke".

## 12. Icon specification (1.0.3)

`tools/make_icon.py`, generated with the pure standard library, no third-party
dependencies. Artifacts land in `web/static/`.

**Sizes**: the `.ico` carries ten sizes — 16/20/24/32/40/48/64/96/128/256 — each
**rendered individually**, not downscaled.

**Storage format**: sizes below 256 use legacy BMP(DIB) entries; 256 uses PNG.
Small PNG entries have incomplete support in some Windows components, which fall
back to scaling up a large image — blurry all the same.

**Stroke thickness is not proportional scaling.** Anti-aliasing is computed from
pixel coverage; the smaller the size, the larger the edges' share, and
"proportionally correct" strokes get swallowed by gray fringes at small sizes.
So sizes 16–48 get a half-width floor:

| Size | Stroke diameter | Share of W width |
| --- | --- | --- |
| 16 | 2.10 px | 25% |
| 32 | 3.00 px | 18% |
| 48 | 3.56 px | 14% |
| 64+ | proportional (13%) | 13% |

The floor table lives in `STROKE_HALF`; the proportional factor in `STROKE_RATIO`
(0.065 = half-width as a share of the W span, i.e. a 13% diameter).
**Thickness must increase monotonically as size decreases** — when editing the
table, print it via `half_width()` and verify; never let 48 be thicker than 64.

**The page carries exactly one `.ico`** (`index.html`):

```html
<link rel="icon" type="image/x-icon" href="/static/icon.ico?v=4">
<link rel="shortcut icon" href="/static/icon.ico?v=4">
```

**Do not** additionally attach a single-size `sizes="16x16"` PNG. The moment a
small entry appears on the page, the shell may grab that small image for the
taskbar, and upscaling it is guaranteed to look soft — this was one of the causes
of the 1.0.2 blur. When the icon changes, bump `?v=N` as well, or the browser's
favicon database keeps serving the old image.

## 13. Authorization must be computed from the actual paths to be written (1.0.4)

`core/safety.py::authorize_rels(root, rels)`. **Install / uninstall / restore must
all use it.**

### The incident

On 2026-10-26, installing `SmartAutoLoot Remaster V1.1.7` failed with
`这个路径没有写入授权（写入前预检）：...\bin\config\base\autoloot.ini`
("this path has no write authorization (pre-write check)"). (The error text is
quoted verbatim; it is the string users actually see.)

The package contains three kinds of content:

| Category | Example | Classification |
| --- | --- | --- |
| mod body | `mods/modAutoLoot/` | `mod_folder` |
| menu | `bin/config/r4game/user_config_matrix/pc/modAutoLoot.xml` | `menu_xml` |
| game config | `bin/config/base/autoloot.ini` | `bin_config` |

But the installer at the time wrote:

```python
if plan.bin_binaries:          # ← only dll/exe opened up bin
    guard.allow(root / "bin")
```

`autoloot.ini` is not a binary, so `bin_binaries` was empty → the entire `bin/`
layer was never authorized → `bin/config/base/` could never be written.
**This hole existed since M3 introduced installs**, and only triggered on
"packages that ship a game config file" — which is why the earlier packages
never exposed it.

### The current rule

`authorize_rels` decides per target relative path and opens up only the necessary layer:

| Target path | Opened up |
| --- | --- |
| `bin/config/**` | Only `bin/config` (.ini/.xml, no executables) |
| Other `bin/**` | The whole `bin` (dll/exe, `risk=high`, requires `allow_risk` confirmation) |
| Everything else (mods/, dlc/) | Handled by `authorize_game()`, not repeated here |

**Never revert to "check whether the plan contains a category"** — the moment a
new category appears (as `bin_config` did), authorization leaks. Judge by target
path and new categories are covered automatically.

### Uninstall got the gate too

`uninstall()` / `restore()` used to call `shutil.move` directly, **bypassing
`guard` entirely** — write protection simply didn't apply to uninstall. Now both do:

```python
safety.authorize_rels(root, pkg["paths"])
guard.allow(DATA_DIR, QUARANTINE)
safety.assert_writable(*[_abs(root, rel) for rel in pkg["paths"]])
```

A side benefit: while the game is running, uninstall is now blocked by
`assert_writable` (previously it wasn't).

### UI

`bin_config` entries are not listed in the main manifest (it would get too long);
instead a separate yellow notice says "this will modify the game's own config
files" and explains that uninstall will restore them together. It does **not**
count toward `risk=high` — editing an ini can't stop the game from launching, which
is a different thing from a dll; lumping them together devalues genuine high-risk
warnings.

## 14. Merged writing for game-owned configs (1.0.5)

`core/config_merge.py` (pure functions) + `core/config_patches.py` (baseline +
ledger). **Wire it into all three places in `core/install.py` — install /
uninstall / restore. Missing any one means it isn't done.**

### Why

On 2026-10-26 the user asked that overwriting game-owned config files should
merge rather than replace, and that uninstall should remove only the lines the mod
wrote rather than the whole file. A sample of his download folder (377 archives,
33 Witcher 3 mods):

| Writes to | Packages | Notes |
| --- | --- | --- |
| `mods/modXXX/` | all | one folder each; conflict-free by construction |
| `dlc/` | 8 | same |
| `user_config_matrix/pc/modXXX.xml` | 21 | **all create their own menu files**, don't touch stock files |
| `bin/config/base/*.ini` | 1 | RemasteredBloom → `visuals.ini` |
| `bin/x64_dx12/*.dll` | 3 | W3LoadFix / PT Optimization / winmm-type |

So the odds of actually stepping on a stock file are low — but when it happens
it's a real mess. The clearest evidence is `Debug Console`'s `general.ini`:

```ini
; In game          ; In package
[General]         [General]
ConfigVersion=7   ConfigVersion=5
                  DBGConsoleOn=true
```

A wholesale replacement would downgrade the config format from 7 to 5, while all
the mod actually needs is to add one line, `DBGConsoleOn=true`. That is the
empirical case for "must merge".

### Two hard rules (read before touching the code)

1. **Only add and modify; never delete a key because "the package doesn't have it".**
   Most mods ship fragment files; a few ship complete files. In neither case may a
   missing key in the package mean "the game should drop it" — that would replace
   your game's entire tuning with the mod author's version.
2. **On restore, if the current value != the value we wrote, do not force-restore;
   report a conflict.** It means another mod or the user has since changed it, and
   a hard restore would eat someone else's change. When force is truly needed, use
   `revert(..., force=True)` — only for tests and genuine "back to stock" intents.

### Protected fields

`PROTECTED_KEYS = {"configversion"}` — the config-format version the game manages
itself. If a package wants to change it, mark the change `skip` and explain why in
the preview; never write the file.
**Do not grow this set just because "some mod wants to change X"** — before adding
a key, confirm it isn't game-managed state.

### Data structures

`data/config_patches.json`

```json
{ "packages": { "<pkg_id>": { "name": "...", "files": {
    "bin/config/base/general.ini": {
      "format": "ini",
      "changes": [{"op":"add","section":"general","key":"dbgconsoleon",
                   "value":"true","old":"","line":"DBGConsoleOn=true",
                   "section_raw":"General"}],
      "applied_at": "...", "baseline": "data/baseline/bin/config/base/general.ini"
} } } }
```

- `changes` also stores `skip` entries — uninstall needs them to explain
  "why this one wasn't reverted".
- `line` stores the package's original line, copied verbatim on insert, preserving
  the key's casing.
- `section_raw` stores the section name's original spelling. **INI section names
  are case-sensitive**; writing back `[autoLoot]` is equivalent to not taking
  effect. This pit has been stepped on once.

`data/baseline/<relative path>` — the file's content before we first touched it,
never overwritten. It exists as the safety net for full-file restore and **must
stay in `core/storage.py`'s `KEEP_DIRS`**; it must never enter a cleanup list.

### Uninstall semantics

| File type | Uninstall action |
| --- | --- |
| mod folders / dlc / menu xml / dll | Move into `data/quarantine/<id>/` (recoverable) |
| Merged `bin/config/*.ini` | **Not moved**. Reverse the changes: rows added are deleted; `mod`-type changes restore the `old` value |

Therefore `pkg["paths"]` excludes merged files, which live in `pkg["merged"]`.
`uninstall` / `restore` must pass `pkg["paths"] + _merged_of(pkg)` together into
`authorize_rels` and `assert_writable`, or the write gate will block the revert.

**Packages that only merge configs and ship no mod folder** (e.g. Debug Console)
end up with an empty quarantine. `restore()` must not error just because `q_dir`
doesn't exist — the criterion is `if not restored and not merge_changed: raise`.

### Empty-section cleanup

`_drop_empty_sections()`: after a revert, if a section has no keys left and all
its recorded changes are `add` (i.e. the whole section was created by the mod),
delete the section header too. Otherwise the file keeps an empty `[AutoLoot]`
that looks like a failed uninstall, and reinstalling creates two same-named
sections. **Sections that already existed (with `mod`-type changes) must never
have their header deleted.**

### Types that are not merged

- `.xml` (menu definitions / `config_description.xml` /
  `keyboard_layouts_description.xml`): deeply nested structures; a bad node-level
  merge can leave the game's settings UI unable to open. Currently **no** known
  package needs it.
- `.dll` / `.exe`: binaries, no merge semantics — whole-file replacement + keep
  the original only.
- `.json`: no cases so far. If support is ever needed, first confirm the game
  actually reads that file.

### Line endings and encoding

- Read: `utf-8-sig` → `utf-8` → `gbk` → `cp1252` → `latin-1`; remember which one
  was used and write back with the same.
- Line endings follow the **current file** (mostly CRLF); new lines must not
  introduce bare LF.
- If the file's last line originally had no trailing newline, don't add one.

---

## 15. Collaboration boundary with Script Merger (1.0.6)

### Why we don't build our own script merger

`.ws` is WitcherScript; three-way merging requires syntax-aware judgment
(`@wrapMethod` annotations, class inheritance, `state` blocks), and text-diff
merges produce code that won't compile. Script Merger uses the REDkit script
parser, and its author still keeps up with the remastered edition. Building our
own means reinventing a wheel that will be wrong.

**Conclusion: merging is always Script Merger's job.** The manager does only three
things it cannot do.

### The boundary: read-only ledger + process launch only

Everything in `core/merger.py` is read-only:

| Action | Do it? |
| --- | --- |
| Read `Merger/MergeInventory.xml` | Yes |
| Read `ScriptMergerRemastered.settings.xml`'s `MergedModName` | Yes (the user may have renamed the output) |
| Scan the output folder's `.ws` files (fallback empty-shell detection) | Yes |
| Merge scripts on the user's behalf | **No** |
| Write `MergeInventory.xml` | **No** |
| Add a `mods.settings` entry for `mod0000_MergedFiles` | **No** (a missing entry = Priority 0 = highest precedence — that is its deliberate design) |

The only action that touches the game directory is `POST /api/merger/open`:
`subprocess.Popen([exe], cwd=exe.parent)` — no arguments, no waiting, no output
capture. Any file changes are made by Script Merger itself. The call passes
`safety.assert_writable()` first; if the game is running it is refused with 409.

### Conflict verdicts: the merged output is the referee, not a contestant

The old rule was "the same relative path appears ≥ 2 times". The merged output
itself contains that file, so it got counted as a third party. This had been true
since before 1.0.5; what the user saw was "after merging, 2 conflicts became 3".

`_judge_scripts(rel, keys, inv, prod_names)` now produces four verdicts:

| Case | level | Output |
| --- | --- | --- |
| No merged output involved | `merge` | `script_overlap` |
| Every non-output member of the group ∈ ledger sources | `ok` | `script_merged` (green) |
| The group still has non-source members | `merge` | `script_overlap`, participants = the remainder + the output |
| The output has files but the ledger lacks the entry | `merge` | `script_overlap`, with copy explaining "the ledger has no record" |

The aggregation key changed from "participant set" to `(level, participants,
covered sources)`; otherwise, within the same batch of mods, merged and unmerged
ones would collapse into one card.

### Two more pits nobody was watching

- `merge_stale`: recorded in the ledger's `IncludedMod` but no longer present on
  disk under `mods/` → `warn`. The merged bundle then holds code referencing a
  class that no longer exists; the game logs "Could not find XXX" at startup and
  the user has zero visible clue that uninstalling a mod caused it.
- `merge_empty`: the ledger has an entry, the output folder exists, but it contains
  no `.ws` at all → `warn`. The typical scenario: Script Merger cleaned old merges
  and then crashed before rerunning.

### Degradation

Without Script Merger installed (`exe_path()` is None and no
`MergeInventory.xml`) → `inventory()` returns `found: False`, conflict detection
falls back entirely to the pre-1.0.5 logic, and the UI hides the launch entry.
This path has a dedicated test (`tests/test_merger.py`, scenario 1).

### The pit fixed in 1.0.7

In `conflicts.analyze()`'s return value, the `merger` section lives **inside
`stats`** (`stats.merger`), but the 1.0.6 front end read `rep.merger` (top level)
→ `undefined` → permanently displayed "Script Merger not detected". Changing it to
`st.merger` fixed it.
**Lesson: when the backend nests a section inside a substructure, the front end's
access path must change with it. This class of error throws no exception — it
silently shows a default value. Assert against the real return value; never
review code alone.**

### Known gaps

- Whether the merged output's **content** really includes both sides' changes —
  requires parsing `.ws`; not done. For now the `MergeInventory.xml` record is trusted.
- Node-level merging of menu xml (`input.xml` etc.) — see §14, "types that are not merged".
- Same-named ini contention at the `bin/config` level entering `conflicts.py` —
  merged writing supports it (§14), but the conflict panel does not yet flag
  "two mods changed the same key".

## 16. Merged files do not appear in the mod list (1.0.8)

### The rule

Bundles like `mod0000_MergedFiles` look like ordinary mods on disk, but they are
Script Merger's **working output**, not mods the user installed. As of 1.0.8:

- `ModRecord.is_merged` is stamped by the scanner (`core/scanner.py`, §3.5) based
  on `merger.inventory()`'s output list (including a user-renamed `MergedModName`).
- **Invisible in the UI**: `ScanResult.visible_mods()` filters before
  serialization (`/api/state`, `/api/track`, `/api/localize`, `/api/about`,
  group auto-classification, and `unknown_mods` all use the visible list).
- **Kept in internal logic**: `conflicts.analyze()` receives the full `res.mods` —
  the bundle is the referee for "already merged"; filtering it out breaks the
  entire verdict.

### Two server-side hazards fixed in passing (outcome of a full-stack retrospective)

1. `_file()`'s static-path whitelist used `startswith` — a sibling directory like
   `data/thumbs_evil` could ride past the prefix. Changed to `Path.relative_to`.
2. `do_POST` had no `Content-Length` cap and unguarded `int()` — one malformed
   header could kill a handler thread or blow up memory. Now >64MB returns 413,
   and parse failures are treated as 0.

---

## 17. "Latest version" follows the main file's version (1.0.9)

### The symptom

The user reported "several local version numbers are higher than the Nexus
'latest'". Measured: 3 of 20 mods.

| Mod | Local | "Nexus latest" shown in UI | Actual latest (MAIN file) |
| --- | --- | --- | --- |
| Switchable Cloaks (10172) | 1.9.2.1 | 1.9.2 | **1.9.2.1** |
| More Skill Slots (13357) | 1.1.3 | 1.1.1 | **1.1.3** |
| UI Remastered Edition (14578) | 1.1.2 | 1.1 | **1.1.2** |

All three tilted the same direction: **the local number was right; our "latest"
was taken from the wrong place.**

### Root cause

`/mods/{id}.json`'s `version` is the author's hand-typed **display value**, not
necessarily the `mod_version` of the uploaded main file. In our sample, 3 of 17
mods differed by 1–2 hotfix versions. Comparing against it makes "latest" conclude
"local is ahead of online".

The reverse is more dangerous: local 1.9.2, main file 1.9.2.1, page says 1.9.2 →
verdict `current`, and the user never sees that hotfix.

### The fix

1. `nexus_meta.mod_files()` goes through GraphQL `modFiles(modId, gameId)`
   (Key-free, same route as metadata) and records the main-file version in the
   cache: `main_version / main_file / main_date / main_files`.
2. `_pick_main()`'s selection order:
   - Only `category == "MAIN"` counts. `OPTIONAL / MISCELLANEOUS` are optional
     translations and compatibility patches; `OLD_VERSION / ARCHIVED` are history —
     mixing them in produces verdicts like "the latest is the 1.10 German
     translation pack".
   - When MAIN has multiple versions, take **the highest version number**, not the
     newest upload: re-uploading an old file refreshes its timestamp without
     changing its version.
   - Only if no MAIN exists, fall back to `primary == 1`, then to everything.
3. `nexus.py` (the Key-requiring REST route) likewise switches to the MAIN entry's
   `mod_version` from `files.json`, and queries every mod — previously it only
   queried when the local version was unknown.
4. `track.apply()` prefers `main_version`, storing the page value in
   `page_version` for reference.

### Three version comparators unified in passing

`track._ver_tuple` / `nexus.vkey` / `nexus.is_newer` were three copies of the same
rules with slight divergences. Consolidated into `core/version.py`: one copy only,
with same-named entry points kept in both modules.

A new rule: strings with `alpha/beta/rc/pre/dev` suffixes count as older at equal
numbers (`0.3beta < 0.3`). Previously `0.3beta` and `0.3` compared exactly equal —
when picking the newest of three downloads of the same mod (0.2 / 0.3beta / 0.3)
by mtime, correctness was pure luck.

### The new `ahead` tier

When local > online, it no longer folds into `current` (the old `else` catch-all);
it gets its own `ahead` tier: neither "update available" nor "up to date", sorted
after updates and before unknowns, with the reason spelled out in the UI.

### Cost

One extra GraphQL request per mod, `MIN_GAP` 0.8 s → about 13 seconds for 17 mods.
It only happens on the refresh that needs it (metadata expires after 7 days) and
runs in a background thread after startup. Old caches (metadata present but no
`main_version`) take `refresh()`'s `fill` branch, which only backfills the file
list rather than refetching all metadata.

### Still not covered

- The local version's **origin** is itself inferred: manual entry first, then
  install records, then old-manager records, and only as a last resort the
  download-folder zip's filename. So `version_cn` (the Chinese note) may say
  "downloaded package" — it means "this version was downloaded", not "this is
  what's installed".
- Reading self-describing files inside mod folders to determine the real version
  is not done (The Witcher 3 has no standard format).

---

## 18. The merged bundle must be registered and sorted first (1.0.10)

### The incident

On 2026-10-27 the user reordered load order with this program; in game, 22 errors:

```
Error [modautoloot]game\gameplay\containers\container.ws(562):
    Could not find function 'GetAutoLootManager'
```

`GetAutoLootManager()` is a method of `W3PlayerWitcher`, defined in
`playerWitcher.ws`. Three mods each shipped a **complete** copy of the same file:

| File | Lines | Has the method |
|---|---|---|
| `modMoreSkillSlots/.../playerwitcher.ws` | 12970 | No |
| `modAutoLoot/.../playerWitcher.ws` | 13013 | Yes |
| `mod0000_MergedFiles/.../playerWitcher.ws` (bundle) | 13013 | Yes |

The copy that ultimately took effect was MoreSkillSlots'; AutoLoot's method
vanished and every call site failed to compile.

**The key clue in the errors**: there were compile warnings for
`[modmoreskillslots]...playerwitcher.ws`, but not a single one for
`[modautoloot]...playerwitcher.ws` — AutoLoot's copy never took part in compilation.

### Root cause

`mod0000_MergedFiles` **had never been entered into `mods.settings`**.

- The `0000` prefix only works when **no mod has an entry at all**: then loading
  follows alphabetical folder order, and `0000` naturally sorts first.
- Once any mod has an entry, `Priority` takes over ordering; **unregistered mods
  sort last**.
- So the bundle lost to every source mod, voiding the merge on the spot.

This was not "lost during the reorder" — the bundle had never been registered
since Script Merger generated it on 10-26 (the 10-20-era table did have
`[mod0000_MergedFiles] Priority=0`, copied from someone else's template).

### Priority semantics (nailed down empirically)

**Lower numbers load first = win on conflict.** The same incident supplies
two-way evidence:

| State | MoreSkillSlots | AutoLoot | Result |
|---|---|---|---|
| Before change | 70 | 60 | Fine (AutoLoot wins) |
| After change | 40 | 70 | Broken (MoreSkillSlots wins) |

Official (CDPR REDkit wiki): `Priority` ranges 1–9999, and the documented example
is exactly `[mod0000_MergedFiles] Enabled = 1 Priority = 1`. A community comment
puts it more bluntly: *"This mod always goes first, otherwise we'd lose all the
changes we introduced by merging the files in the first place."*

### Four hard rules

| # | Rule | Where |
|---|---|---|
| R1 | If the bundle exists → the table must have its entry, `Enabled=1`, `Priority` strictly lower than every other mod | `merger.ensure_registered()` |
| R2 | Every path that writes `mods.settings` (reorder / priority change / enable-disable / install / uninstall / restore) re-checks R1 after writing | `actions.*`, `install.*` |
| R3 | When R1 is unmet, the scan warns + a red bar in the conflict panel says so plainly | `scanner`, `conflicts`, `app.js` |
| R4 | The bundle never enters the draggable sort list; it is fixed first | `order.js` uses `visible_mods()` |

**Boundary**: scanning is always read-only — it warns but never writes. Adding
the entry happens only when the user actively triggers a write. The check is
"strictly the minimum", not "equals 1" — whether the table says 0 or 1 is legal;
we don't beautify it.

### An old bug fixed in passing

`merger.script_files_of()` built paths with `relative_to(base.parent)`, producing
an extra `content/scripts/scripts/...` level that never matched ledger paths.
1.0.9 was saved by the ledger fallback, but `detail()`'s "is this file in the
output" judged wrong every time. Changed to `relative_to(base)`.

### The "merged" category

After the bundle vanished from "all mods" (1.0.8), the user could no longer see it
anywhere — half the reason the incident produced zero signals. 1.0.10 gives it a
read-only corner:

- Left sidebar "Status → Merged", count 1;
- The card carries a "read-only" badge; no toggle, not draggable;
- Details show: state, Priority, which scripts were merged, whether each source
  mod still exists on disk, and "why this cannot be touched".

### Removing the "legacy archive" entry point

The data (`data/legacy.json`) is kept and remains the source of Nexus IDs /
authors / covers; what was removed is the settings-page card and the two write
endpoints `/api/legacy/import` and `/api/legacy/forget`. Rationale: there is no
reason to "import again" what is already migrated, and re-importing would
overwrite IDs the user later confirmed by hand with stale values. To re-migrate,
delete `legacy.json`.
Also removed: the scan-time line "N legacy records were saved into this program's
directory" — with the entry point gone, the sentence only confuses.

### Enable/disable coloring in load order

Both labels were gray-on-gray, indistinguishable at a glance. Now green (`--ok`)
/ red (`--danger`) with matching soft backgrounds, reserved exclusively for these
two states.

### Still not covered

- The ledger's `IncludedMod Hash` is a fingerprint of source files at merge time
  and could tell us "the source mod changed later; the bundle is stale". The
  algorithm resisted reverse engineering (CRC32 / CRC32C / Adler32 / MD5-first-8
  and last-8 all wrong); the next version switches to our own fingerprint table
  and stops depending on it.

---

## 19. Eleven error-prevention fixes + Win11 alignment (1.1.0)

### Where this came from

After 1.0.10 shipped, the user brought in competitive research on others
(Vortex / MO2 / Script Merger / RMMR) and asked "which of these should we do?"
While evaluating, three claims in the report didn't match reality, and scope was
adjusted first:

| Report's claim | Reality | Consequence |
|---|---|---|
| §10 "P-1 bundle not registered" was the top fix | Already fixed in 1.0.10, and stricter than the report demanded (not just an entry — it must be **strictly first**) | Item dropped from the queue |
| Designed around 187 mods (multi-select, partial re-render, three density tiers) | This machine really has 19 visible mods | Multi-select deferred to 1.1.1; density tiers not built |
| Sidebar 218px → 236px | Window is fixed at 1280×820; widening leaves 2px of slack for the details column | Not widened |

### What this version did

**1. Global read-only while the game is running (C4)**
Previously only the "Undo" button was disabled. Now when `game_running` is true:
a persistent orange bar at the top explains "why you shouldn't change things
now", and every disk-write entry point (enable-disable / save order / install /
uninstall / restore / cleanup / path settings / undo) is intercepted at the click
capture phase with the same explanation. **Interception sits at the click capture
phase rather than per-button `disabled`**: buttons that still look clickable but
explain themselves when clicked are easier to understand than a sea of gray.
The status-bar line was changed to the same wording — during acceptance the top
said "don't change anything" while the bottom still said "safe to deploy",
contradicting itself.

**2. Post-uninstall config conflicts become a blocking dialog (C1)**
On uninstall, if a config the package had written was since changed by someone
else, the old behavior was a 7-second toast. The consequence is "in game, a key is
still bound to the previous mod" — the user genuinely loses something without
knowing. It is now a dialog that must be clicked through, listing every case
(file / `[section] key` / expected vs current value / why it wasn't restored),
with three exits: undo this uninstall (via the existing journal), open the config
folder, or acknowledge. The details panel also gained an "uninstall this mod…"
entry at the bottom that jumps to "Installed packages" and highlights the package
(a package carrying multiple mods gets an extra "will be moved together" notice).

**3. Load order: don't lose edits + get the semantics right**
Closing the window right after dragging silently discarded changes (that was the
behavior before 1.1.0). Now closing prompts, with a persistent footer "you have
unsaved changes — closing loses them".
Also fixed an inverted explanation: it used to say "earlier-loaded mods get
overwritten by later ones", but the measured rule is **lower numbers load first
and win conflicts** (two-way evidence in §18). The user already paid the price of
that inverted sentence once (22 × `Could not find function`); such sentences
cannot rely on memory.

**4. Automatic merge-ledger detection**
Script Merger is a separate process; when it finishes merging, this program has
no idea. While the conflict panel is open, a read-only probe hits
`GET /api/merger/state` every 3 seconds (ledger mtime + entry count + bundle
registration state); on change it silently redraws and shows "pending merges
3 → 0". **Polls only while the panel is open**, stops when closed; the decision
uses a five-part summary `mtime|count|registration|priority|file count` and never
compares file contents, so the cost is negligible.

**5. Feedback loops**
- After every enable/disable/reorder/priority-save, the notice bar itself carries
  "Undo" (via the existing `/api/undo`) — no need to open the log and hunt for the entry.
- The status bar permanently shows the latest operation (`✓ Disabled modAlpha 20:10`);
  clicking it opens the log panel.
- Selecting a mod co-highlights the ones it conflicts with (yellow bars on the
  left), so you don't have to remember names and search.
- Inputs like creating groups and pasting paths moved from `prompt()` to in-page
  input boxes (C2). **This does not overturn §11's "must use native dialogs"
  conclusion** — that one targets file/folder picking (which needs system paths
  and drag-and-drop); for text input, a native `prompt` merely gets buried under
  the browser shell and its styling is uncontrollable.
- Empty states went from one sentence to "explain why it's empty + the next step" (C3).

**6. Visuals: follow the system + Win11 metrics (A1 / A3)**
- Three-state theme: follow system / light / dark; defaults to following the
  system and listens for system switches. The first paint sets the theme inside
  `<head>` before styles load, avoiding a light flash.
- Corner radii split into two tiers: cards and panels 8px, controls 4px
  (previously 7/12/14 mixed, making panels and buttons equally soft).
- Control heights unified at 32px (`select` / segmented / buttons / sidebar items).
- Font sizes raised only for "reading text": card subtitles, paths, status bar,
  panel descriptions moved to about 11.5px. **Not** the report's blanket 12px —
  cards are 162px minimum width, and blanket 12px lets ellipsis eat the subtitle
  (`Standardised Naming Con...`, visible to the naked eye). Badges and pills, the
  "glance is enough" category, stay as they were.

### The only new server endpoint

`GET /api/merger/state` (read-only). `core/guard.py` unchanged by a single line;
1.1.0 added no write paths. `/api/open-folder` also gained an `@userconfig` branch
(only `startfile`s the directory; no arbitrary suffixes accepted).

### Deliberate deviations from the research document

| Report asked | This version | Why |
|---|---|---|
| B multi-select + batch toggling (P0 headline) | Deferred to 1.1.1 | 19 mods don't need batching; what's really needed is error prevention |
| D three density tiers | Not built | The three tiers combined save scroll distance; 19 mods fit one screen, and an extra mode is one more place to break |
| A2 system accent color | Not built | The accent anchors the entire visual system; following the system would simultaneously change selection states/links/progress colors — less benefit than the risk of a site-wide recolor |
| C3's third exit = import archives | Changed to "search Nexus for this name" | The usual reason a search comes up empty is "not installed", not "installed wrong"; the import entry has always been in the top bar |

### Acceptance record (2026-10-27)

- All 16 test suites green (`test_install` / `test_config_install` already cover
  the uninstall-conflict backend contract).
- UI screenshots checked item by item: main view, dark mode, load order, conflict
  panel, merged, empty states, read-only state, installed packages (with
  focus-follow), details panel.
- Read-only state plus toggling/undo exercised in a **sandbox**: temporary game
  directory + temporary config directory + temporary data directory; a toggle
  writes `mods.settings`, the bar shows "Undo", and undo restores both the file
  and the journal. The real game directory stayed read-only throughout
  (`tools/doctor.py check` fingerprint unchanged).

### Still not covered

- "Clicking is intercepted" in read-only mode is a unified capture-phase
  interception in code; screenshots can only prove button appearance — the last
  centimeter of feel needs on-machine confirmation.
- Whether the bundle's content is stale remains undetectable (same open item as
  at the end of §18).
- The uninstall-conflict dialog's real data requires "installed, then config
  changed by someone else"; the sandbox couldn't produce that scenario. The
  backend contract is guarded by `test_config_install`; watch for the first real
  trigger in the UI.

## 20. Three-layer conflict detection + batch enable/disable (1.1.1)

### Origin: one real incident exposed a detection blind spot

On 2026-10-27 the user reported "Hood Slots' extra slots don't show, and More
Skill Slots' legend hint is wrong". Troubleshooting (in a separate session) traced
the cause: **three mods' `.bundle` files contain the same UI resource**,
`gameplay\gui_new\swf\inventory\panel_inventory.redswf`. The game loads only one
copy; the other's UI changes vanish wholesale — while script functionality keeps
running fine.

The 1.1.0 Waass had nothing to say about this class of problem, because its
conflict detection had exactly one layer: **same-named script files**. The real
world has three:

| Layer | What collides | Consequence | Visibility |
|---|---|---|---|
| 1 | Same `.ws` file path | The later overwrites the earlier; changes vanish wholesale | Filename visible, easy to find |
| 2 | The same function annotated by two mods | `@wrapMethod` can coexist but is order-sensitive; `@replaceMethod` silently overwrites | **Completely invisible** |
| 3 | Same resource path in different `.bundle`s | Only one copy takes effect; the rest vanish | **Completely invisible** |

Layers 2 and 3 share the signature "scripts still run, the game logs nothing";
the user sees only "the feature works, the UI is wrong", with no leads at all.
1.1.1 adds both layers.

### Three read-only probes

Three new modules, all read-only, all outside `core/guard.py` (no write paths involved):

**`core/bundle_entries.py` — read-only POTATO70 name table**

The resource-pack format is called POTATO70. Public documentation only mentions
"compression byte 0 = raw / 1 = zlib", so a parser written from public docs fails
on every pack produced by REDkit — **compression byte 5 = LZ4 block** (measured;
see `witcher3/mods/panel-and-bundle-notes.md` (Chinese only)).

The key trade-off: **read the name table only; decompress nothing**. The name
table starts at file offset `0x20`; each record is a fixed 304 bytes (256 name +
16 hash + 32 metadata); the entry count is at `0x08`. Deciding "do two packs
provide the same resource" only needs path strings, so no LZ4 decompression is
needed (the workspace has `lz4blk.py` that can do it, but dragging it in is
unnecessary).

```python
MAGIC = b"POTATO70"   # 0x00
REC = 304             # fixed record length
NAME_SLOT = 272       # name slot (the rest is metadata)
HEADER = 0x20         # name table start
MAX_TABLE = 64 << 20  # defense: the name table can't be this big
MAX_ENTRIES = 60000
```

`classify()` sorts resources into three classes; only the first two are reported:

| Class | Test | Reported |
|---|---|---|
| `ui` | `.redswf/.swf/.gfx/.redflash` | Yes (warn) |
| `data` | `.xml/.reddlc/.w2quest/.w2phase`, or path contains `\definitions\` | Yes (info) |
| `asset` | everything else (textures/models/sounds) | **No** |

Never reporting `asset` is deliberate: texture mods colliding on textures is the
norm, and reporting it is pure noise — users would learn to ignore the whole
panel. The cache is keyed by `(path, mtime, size)`, capped at 400 entries;
a full library takes 0.01 s in practice.

**`core/scripts_meta.py` — annotation targets + encoding**

```python
_ANNO = re.compile(r"@(wrapMethod|replaceMethod)\s*\(\s*([A-Za-z_][\w.]*)\s*\)")
```

Only `@wrapMethod` / `@replaceMethod` are collected: `@addMethod` / `@addField`
are pure additions that coexist naturally; including them only manufactures
false conflicts.

Encoding detection is this version's other practical catch. Patch 5.00 moved
script resources from UTF-16 to UTF-8; old 4.x-era packages copied in verbatim
fail to compile, and the game reports only the filename — the user cannot
possibly tell it's an encoding problem. BOMs are easy; **BOM-less UTF-16 can only
be detected by NUL ratio**:

```python
probe = head[:64]
if len(probe) >= 8 and probe.count(0) * 10 >= len(probe) * 4:
    return "utf-16-le" if probe[1:2] == b"\x00" else "utf-16-be"
```

(The first draft was `count(0) >= max(8, len//4)`; a 12-byte probe can never
reach 8 NULs, so all short scripts escaped detection. `test_mod_inspect` caught it.)

Files over 4MB get encoding checks only, no annotation extraction — large files
are almost always merge outputs, where extraction is both meaningless and slow.

**`core/w3strings.py` gains version reading**

Header is `RTSW` + u32 version. The remastered edition 5.00 = **164**. When the
table is older than the game, newly added strings can't be found and the UI shows
`##` or blanks. `game_baseline()` prefers `content/content0/cn.w3strings` and
falls back to `en`.

### Detection scope

**Only enabled mods are checked.** This is not an optimization; it's correctness:
`~modSmoothUIRemastered` (disabled) colliding with UI Remastered on 20 panel
files is meaningless to report — it is never loaded. All five loops switched from
`mods` to `active`.

**`@wrapMethod` reports info, not warn.** It is an officially supported layering
mechanism and not an error in itself. But stacking order is decided by load
order, which makes it the only lead when investigating "after I touched the
order, some feature changed". `@replaceMethod` is warn: whole-function
replacement drops other people's wrappers too, and logs nothing.

**The wording accuses no one.** The UI copy reads "don't rush to uninstall:
sometimes the upstream mod has already integrated the other's changes — decide by
actual results". The HoodSlots episode is the counterexample: UI Remastered's
panel **already contains** HoodSlots' slot UI; the real problem is that it hides
the slots by default. Acting on "collision = uninstall one of them" would
uninstall the wrong one.

### New detections at a glance

| code | Level | Meaning |
|---|---|---|
| `anno_replace` | warn | The same function is wholly replaced by ≥ 2 mods via `@replaceMethod` |
| `anno_wrap` | info | The same function is layered by ≥ 2 mods via `@wrapMethod`; order-sensitive |
| `ws_encoding` | warn | The script is UTF-16 / another encoding; won't compile on 5.00 |
| `bundle_ui` | warn | The same UI resource in packs is provided by multiple mods |
| `bundle_data` | info | The same definition resource in packs is provided by multiple mods |
| `w3strings_stale` | warn | The CN language table's version is lower than the game's |

`stats` gains accordingly: `bundle_mods / bundle_ui / bundle_data / anno_targets /
anno_wrap / anno_replace / ws_bad / w3_stale / w3_base / disabled`.

On the real library (19 visible mods): `1 script_merged(ok)` / `2 anno_wrap(info)`
/ `2 bundle_ui(warn)` / `1 w3strings_stale(warn)`, in 0.11 s.
`panel_inventory.redswf` is provided by 3 mods, and HoodSlots' language table is
v162 against the game's v164 — the incident the user reported is now stated by
the program itself, unprompted.

### Multi-select and batch enable/disable

19 mods don't need batching, but "disable 5 at once to isolate a conflict" is a
real debugging move, so it was built. There is exactly one key design point:
**the whole batch is one `Operation`**.

If each key opened its own Operation, the log would grow 5 entries and undo would
need 5 clicks — that's no undo at all. `set_enabled_many()` therefore:

1. deduplicates;
2. **validates everything up front** — if any target is missing, or a `~`-renamed
   target already exists, nothing is touched at all. Rather change nothing than
   stop halfway (a half-applied state is the hardest to explain);
3. one `op.backup(settings_path)`;
4. loops the renames + `Enabled` flips;
5. runs `_ensure_merged_entry()` once (§18's R2: every path writing
   `mods.settings` passes through);
6. one `op.commit()`.

`POST /api/mods/batch_toggle` shares the single toggle's error mapping
(`GameRunning`→409).

The UI enters picking mode via the "Batch select" button: checkboxes appear on
cards, toggles are hidden for the duration (clicking a card then means select,
not toggle — two semantics stacked on the same spot guarantees misfires), and an
action bar appears at the top.

### Details panel re-layout

The research report said "information density"; the actual problem was **order**:
conflict cards pushed "Disable / Open folder" off-screen. The fix:

- The top keeps only 5 rows of identity (status / local version / Nexus version /
  author / Nexus ID);
- directly below, a row of quick actions (Disable-Enable / Open folder / Mod page /
  Download page);
- the conflict summary collapses to a title + at most 3 items; `info` items are
  hidden, with "Open conflict check (N more notes)" at the bottom;
- "Load order" moves up; file count / size / install time / downloads move into
  `<details>Technical details</details>`;
- Uninstall sits alone in a danger zone at the bottom, with "uninstall operates
  per package".

### Pre-existing bug fixed in passing

`core/safety.py::undo_last()` fails on **cold start**: the write whitelist is
in-process, so install → close Waass → reopen → undo → `1 path has no write
authorization`. The fix derives authorization from the journal record itself
(`_authorize_for_undo`), with no dependence on process state.
A second problem fixed alongside: a failed undo also dropped the journal entry,
i.e. forgetting "what was even changed" — now a failed attempt keeps the entry intact.

### Acceptance record (2026-10-28)

- **All 17 test suites green.** `test_conflicts` grew from 7 to 26 items, adding
  [7]–[11] covering `bundle_ui` / `anno_wrap` / `ws_encoding` /
  `w3strings_stale`, plus "disabled mods appear in none of the findings".
- `test_actions` added [7b]–[7d]: batch disable produces exactly 1 journal entry;
  one undo restores everything; when up-front validation fails, even the legal
  members of the batch are untouched; duplicate keys deduplicate; `~` rename counts.
- `test_mod_inspect.py` runs 32 items over the probes' edges (bad magic /
  absurd name-table lengths / truncated files / cache invalidation / fake
  annotations inside comments / BOM-less UTF-16).
- The real game directory stayed read-only; `tools/doctor.py check` fingerprint unchanged.

### Still not covered

- `bundle_data` reports only "provided by multiple parties", not which copy is
  newer. Item XMLs may contain entries existing in only one copy; such
  "post-merge unions" require actually parsing CR2W, beyond a read-only probe's scope.
- `@wrapMethod` stacking order only says "depends on load order"; it doesn't
  predict outcomes — different mods may wrap different phases of the same
  function, and order semantics depend on each implementation.
- Asset-class (texture/model/sound) collisions still go entirely unreported.
  If ever attempted, the noise problem must be solved first.

## 21. One-click CN localization (1.2.0)

### Why the old flow was painful

The old flow was five steps: click "extract" per mod → export JSON → hand to AI →
paste the result back → click apply. Fifteen mods meant fifteen rounds. But the
real library had **only 37 strings pending**, ten minutes of work.

So the problem wasn't volume — it was that the middle three steps were things the
program couldn't do itself and hard-shoved onto the user. 1.2.0 pulls those three
steps inside the program.

### Three steps

**① Select**: a mod checklist + content categories (menu pages / mod names / item names).
Only "menu pages" is checked by default — the user's 2026-10-28 requirement: don't
touch mod names by default, they're bug-prone. No mods are pre-checked (scope is
the user's call; the program doesn't choose for him); there is a single
"select all translatable" button.

**② Translate**: runs on a background thread, polled every 600 ms, stoppable at any time.

**③ Apply**: edit translations directly in the results table; the "will it fit"
calculation updates immediately; one write in the bottom-right.

### Three channels, one writable

| Channel | Form | Count (this machine) | Handled in 1.2.0 |
|---|---|---|---|
| A language table | plaintext entries in `cn/zh.w3strings` | 15 mods | **Rewrite** (equal-length replacement) |
| B script-embedded | `.ws` containing `if (lang == "ZH") return "…"` | AutoLoot 941 chars, UIRE teleport 343 chars, etc. | **Read-only detection**, never modified |
| C menu XML | literal text in `bin/config/.../pc/*.xml` | 0 entries | Legacy logic carries over |

Channel B was added on 2026-10-28 after being corrected by the user on the spot:
AutoLoot was fully Chinese in game, while the health check reported "no Chinese
language table". Investigation showed its Chinese lives in `modAutoLootLoc.ws`,
returned by language branch — it never goes through the language table at all.
Recognizing only channel A misreports.

The same self-review fixed two wording errors:
- "the language table is encrypted" means **we can't read the plaintext**, which
  is not the same claim as "this mod lacks Chinese"; the two must not merge into
  one sentence;
- the `Swords On Hip` English entries all live in **code comments**, not UI text,
  and should never have entered the to-translate list.

### Classification: menu pages only, by default

```python
CAT_MENU / CAT_MOD / CAT_ITEM   # menu pages / mod names / item names
CAT_DEFAULT = (CAT_MENU,)
```

Classification uses only two hard signals; everything else defaults to menu
pages — better to translate one extra menu string than to mistake an item name
for a menu string (the latter genuinely breaks things):

1. Language-table entry 0, or the text equals the mod's display name / folder
   name → mod name;
2. Possessive (`Deadly Ronin's Kimono`) and no menu markers (`<font`,
   Auto/Enable/Show…) → item name.

On this machine: 37 menu strings (hoods Switchable Cloak 36 + Hood Slots 1);
the 4 mod names and 3 item names are all excluded.

### Translation engine chain

| Tier | Engine | Config | Notes |
|---|---|---|---|
| L0 | translation memory + glossary | none | Hits return instantly; repeated strings and terms stay consistent |
| L1 | Index-Translate (Bilibili) / MyMemory | none | Two key-free public services; the first is the default lead |
| L2 | Baidu / others requiring keys | keys required | one per line, rotated |

`core/translate.py` uses only the standard-library `urllib`; no third-party
dependencies. An engine that fails 3 times in a row is skipped for the rest of
the run, so a batch never stalls on one flaky endpoint.

**Multiple-key rotation** is an explicit user request ("configure several keys so
localization doesn't fail when an endpoint has problems"): `eng.used` tracks how
often each key was used, and the least-used one is picked each time.

### 1.3.2: Index-Translate (Bilibili) and first-run consent

**Endpoint**: `POST https://index-translate.bilibili.com/v1/chat/completions`
(OpenAI-compatible).

- The model name is fixed at `Index-Translate-9B`. A wrong name returns 400; the same
  service also offers `2B` / `35B-A3B` / `Index-Nailong-9B`, and this program uses only
  9B.
- **Anonymous request**: no `Authorization` header, no `Origin`. Sending an auth header
  returns 412 — found by testing, so do not add one "for tidiness".
- Measured at 321 ms per call. The reply is a chat completion: take
  `choices[0].message.content` and clean it — strip surrounding quotes and a
  `译文:`-style prefix, keep the first non-empty line of a multi-line reply. If the
  result is empty or still the English original, raise `EngineError` and let the next
  engine take over.
- **Its status is preview**: free and public, but with no stated quota, SLA, or end date,
  and it may rate-limit (429). Failure only yields the slot — the existing timeout and
  "skip after 3 consecutive failures" cover exactly this case.
- On by default, first in `order`. An existing config that lacks it gets it appended at
  read time: the user's order is respected, never re-sorted.

**First-run consent is a server-side gate, not a button hidden by the frontend.**

- The switch is `consent` in `data/translate/config.json`, default `false`.
- `POST /api/localize/run` sees no consent and returns only
  `{need_consent: true, engines: [...]}`: no job, no thread, no request. Hiding a button
  in the UI cannot stop a direct call to the endpoint.
- Consent is remembered after one confirmation. `POST /api/localize/consent` writes that
  single switch.
- The list is generated from the engine chain (`translate.targets()`), not hard-coded:
  engines that are off or lack a key never appear on it, and never receive anything
  anyway. Adding an engine needs no copy change.

**The same release removed another key-free channel**: the gtx endpoint at
`translate.googleapis.com`. It is in no public documentation, makes no quota promise, can
be shut off at any time, and would still send the user's menu text out. "It translates"
does not outweigh "text leaves the machine with no explanation" and "one day it all
breaks". Re-adding it means solving those two first.

### Translation quality: three pieces of engineering

Machine-translation quality itself is out of reach, but three classes of problems
are fixable in engineering:

**① Tag pairs must be treated as slots.** Chinese word order moves tags around:
`Auto unequip <font>IN INTERIORS</font>` translated literally gives
`在室内 [1] 中自动卸下 [0]` (literal sample kept; original strings quoted as-is) —
the closing tag lands before the opening tag, which in game renders as misaligned
color blocks. The fix: translate the skeleton `Auto unequip {0}` first → validate
slot order → translate tag-internal text separately → reassemble by position.

**② Short phrases go through the glossary, not the engine.** `IN INTERIORS`
alone becomes "在室内装饰" (interior decoration); `IN ANY STATE` becomes
"在任何州" (any US state — `state` misread). Such words are always wrong out of
context, and **wrong translations tend to be the longest** — length and quality
are the same problem here.

**③ The same English must land as the same Chinese.** The inevitable result of
translating entry by entry:

```
Auto unequip → 自动卸下 / 自动解除装备 / 自动取消装备
Equip back   → 装备回来 / 恢复装备 / 返回装备
during X weather → 在X天气期间 / 在X时 / X时
```

Each is fine alone; arranged in one menu it screams machine translation.
`_FIX_WORDS` converges the frequent cases onto one vocabulary.

> Normalization rules must **normalize whitespace before matching**. Engines
> often emit `在 <font>理发</font> 次访问期间` (literal sample); spaces in the
> middle defeat checks like "次 immediately adjacent", and the same source
> sentence comes back in different sentence shapes across runs — matching cannot
> depend on one particular shape.

### Equal-length writing and "doesn't fit"

Writing back is still equal-length replacement: only the text block's bytes
change; offsets/lengths/indexes are untouched; the file size is identical.
The price: a translation may not be longer than the original, and one Chinese
character is 3 bytes.

`shorten()` escalates in four levels: strip spaces (lossless) → apply
compression rules → drop parenthetical qualifiers → if it still doesn't fit,
mark it honestly. **Never truncate** — truncation means a screen of garbage.

Of the real library's 37 strings, 35 wrote in one pass; 2 didn't fit:
`Other` → 其他 and `Hoods` → 兜帽, both 5 bytes vs 6 bytes — short by exactly
1 byte. The results table marks them honestly as "1 byte short (about one Chinese
character); please shorten it in the table", letting the user decide between
keeping English or a single-character abbreviation.

> **Why variable-length writing wasn't done**: technically feasible (a prototype
> verified 114 lengthened strings round-trip byte-identical). The real reason is
> not impossibility but that the UI's column width is designed around the
> original's byte length, and lengthened Chinese could be visually truncated.
> Equal-length isn't mere conservatism — it is more correct on "don't break the UI".

### Backup and restore

The user required "every localized mod must be backed up and restorable; if
something breaks, one click restores it". `safety.Operation` snapshots aren't
enough: only the latest 30 are kept and auto-pruned — a restore point from three
months ago effectively doesn't exist. So a separate system exists
(`core/localize_backup.py`):

```
data/localize/orig/<relative path>         original copy, saved on first sight, never overwritten
data/localize/history/<batch>/<relative path>  one copy before each localization run, last 20 batches kept
data/localize/index.json                   records of every run, with before/after sha256
```

Why both: `orig` answers "what did it originally look like" (one-click restore
uses this); `history` answers "what did it look like before this batch"
(localizing twice in a row should roll back only the second run, not erase the
first run's work).

Before restoring, the current file's sha256 is compared against the recorded
`sha_after`: a mismatch means the mod was updated — prompt first, then ask
whether to force-overwrite. Never silently overwrite; that would swap new content
for an old backup. Restore also goes through `safety.Operation`, so "restore"
itself can be undone.

**`data/localize/` is in `KEEP_DIRS`; the cleanup panel never lists or deletes it.**
This was previously a hole: the new originals directory wasn't registered, while
the cleanup list still carried the 1.1-era directory `localize_backups` — it
looked like "backups can be cleaned" but cleaned a pile of useless old
intermediate files, while the real originals were classified as "other files"
(size-counted only, not clickable — but miscategorized all the same). Both are
now corrected: `localize` is protected at the same level as `saves`/`baseline`,
and the old `localize_backups` entry is renamed "localization intermediate files
(legacy)" with a clear note "cleaning it does not affect restore". The boundary is
pinned by `tests/test_storage_safety.py` (19 items) — including "after cleaning
every listed id by name, the originals' bytes are unchanged".

### API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/localize/panel` | mod list + categories + engine status + history |
| POST | `/api/localize/run` | `{mods, cats}` → job, translating starts in the background |
| GET | `/api/localize/job?id=` | progress / results |
| POST | `/api/localize/job/cancel` | stop |
| POST | `/api/localize/write` | `{id, edits, only}` one write |
| POST | `/api/localize/restore` | `{record, mode, force}` |
| GET | `/api/localize/history` | record list |
| POST | `/api/localize/config` | engine switches and keys |
| POST | `/api/localize/probe` | send one real request to test connectivity |

Error mapping follows the global rules: `GameRunning`→409, `WriteDenied`→403.
`probe` is a separate endpoint because it really waits on the network — opening
the panel must not stall three seconds first.

### Acceptance record (2026-10-28)

- **All 20 test suites green**, four new:
  - `test_translate.py`, 46 items: tag protection, structure validation, slot
    splitting, wording normalization, whitespace normalization, sentence
    compression, byte budget minus tags, glossary/memory, classification, engine
    registry. All offline.
  - `test_localize_backup.py`, 34 items: originals never overwritten, batch
    number collisions, drift detection, `force` semantics, restore is
    undoable, history keeps 20 batches, clearing requires confirmation.
  - `test_localize_job.py`, 23 items: equal-length writes, no leftover original
    sentences, `only` rewrites one entry, re-judging after manual length edits,
    one-click restore, boundary errors.
  - `test_storage_safety.py`, 19 items: `localize` in KEEP_DIRS, absent from the
    cleanup list, originals byte-identical after full and named cleanup,
    history/index intact — and verification that cleanup isn't a no-op
    (thumbs/quarantine really deleted).
- Real-library end-to-end (read-only game directory): 37 strings → 35 written /
  2 didn't fit / 0 failures, about 10 seconds.
- No write was ever executed against the real game directory — per the red line,
  writes happen only in the sandbox and under user authorization.

### Still not covered

- Channel B is detection-only. Editing `.ws` literals has no length limit, but
  guaranteeing unchanged syntax and encoding plus a single-step undo is a
  different risk class than editing language tables; deferred to 1.2.1 for evaluation.
- Variable-length writing (tier B) postponed.
- Machine translation is "readable" quality; terminology is corrected by
  glossary, not official-localization grade. Better requires configuring L2 keys.
- Text baked into textures / models is entirely out of scope.

---

## 22. 1.2.1: verdict fixes, entry consolidation, icon & startup sync

This version added no features; all seven items are "what felt wrong in the last
version". By nature they fall into three buckets: **wrong verdicts** (1),
**superfluous things** (2, 8), and **missing things** (3, 4, 5, 6, 7).

### 22.1 Localization verdicts: two rules were missing

User's field report: "the two mods you show as translatable — I already did
unconventional localizations on them by hand, and you didn't detect it."
Investigation confirmed two entirely different misses.

**(a) Hand-edited menu-XML localization was not recognized.**

The user's method was editing `bin/config/.../pc/SwitchableCloak.xml` directly:

```xml
<Var id="ModEnabled" displayName="启用模组" tags="customNames;nonLocalizedName"/>
```

Of 45 `displayName` attributes, 32 were Chinese and 13 still held author keys
(`Mods.priler_*` / `njs_*`). The UI text now comes from XML literals, so the 36
English entries in the language table **are superseded and never displayed**.
1.2.0 counted only the language table and reported this mod as "36 English
entries remain" — the number was right; the conclusion was wrong.

New `localize.xml_takeover()`, judging "taken over by the menu" only when both hold:

1. At least 1 Chinese literal `displayName` in the XML — proof someone edited it;
2. The language table's **translated** entry count >= the number of **distinct
   keys** still referenced in key form by the XML.

Distinct keys rather than reference counts: one key like `njs_all` is used by
several Presets; counting references inflates the number and marks a genuinely
unfinished mod as done. In practice this mod had 13 references but only 9
distinct keys, with 10 translated → takeover confirmed.

Taken-over English no longer enters the task list, but **the information is not
lost**: `build_jobs()` additionally returns `stale`; the health panel marks
"hand-localized (via menu XML)" and shows the count as "stale N" (gray
strikethrough). Consequently `extract_xml()` also returns `cjk` and `key_names`,
and previously-skipped `Mods.*`-prefixed keys now count toward "rows still relying
on the language table" — undercounting biases the verdict loose.

**(b) A single category word treated as a menu item.**

Hood Slots had one string left, `Hoods` — an armor category name. 5 bytes can't
hold 兜帽 (6 bytes); listing it as pending just makes the user stare at an entry
that can never be written.

`classify()` gains a rule: single word + initial capital + not in `_MENU_WORDS` →
`CAT_ITEM`. `_MENU_WORDS` collects Enable / Disable / Settings / Options /
Comments / Other / Debug — words that look like nouns but are menus. Missing a
word only skips one translation; adding one wrongly turns a category name into a
to-translate item. Between the two harms, the lesser is chosen.

Result: the real library's pending menu strings fell from 37 to **0**. That
conclusion is correct: the library genuinely has no menu pages left to fill; the
remaining 9 are all mod and item names (not translated by default).

### 22.2 Removing "Chinese names", removing the "Installed packages" entry

The "Chinese names" feature is removed in full: the sidebar entry,
`web/static/names.js`, both `/api/names` endpoints, and the `@names` folder-open.
**`data/names.json` is not deleted**, but from 1.2.1 it is no longer applied to
`mod.name_cn` — cards and details always show the mod's own name, consistent
with Nexus and with the game. Restoring is one un-comment away: the
`names.apply_to(...)` line in `server.py`.
(Rationale: display without an editing entry is hidden state that cannot be
corrected — worse than either extreme.)

"Installed packages" loses only its entry point. It is tied to "details panel →
uninstall this mod" (`/api/packages` still feeds package archives to the
uninstall flow), so the backend and the dialog remain, and the `?dialog=packages`
debug entry still works.

### 22.3 The top-bar "Nexus" button

Click → the server's `os.startfile()` opens `https://www.nexusmods.com/games/witcher3`
in the system default browser. **It must not be `target=_blank` in the page** —
the `--app` window traps external links into another address-bar-less window, and
the user can never find their way back to the manager. `/api/open-url` already
whitelisted nexusmods.com; it is reused as-is.

### 22.4 Blurry taskbar icon: the small-size W was mush

Not a thumbnail problem — the **taskbar button's icon**. Inspecting
`icon-16.png`'s pixels: the W occupied 6 pixels of height in the middle, purple
background all around, plus four-step grayscale anti-aliasing → a small blur.

Three changes:
1. `W_PTS` enlarged from x 62..194 / y 84..176 to x 50..206 / y 66..190
   (52%×36% → 61%×48%);
2. `STROKE_HALF` thickened at small sizes (16px: 1.05 → 1.10; 32px: 1.50 → 1.95);
3. **≤24px skips feathering entirely**: the W's stroke coverage is binarized at
   0.46 — solid white or pure purple. At 16px a diagonal stroke is 2 pixels wide;
   four-step grayscale on top is guaranteed mush. Hard edges with jaggies beat no
   outline. The outer frame (rounded square) stays anti-aliased.

The favicon `?v=4` bumps to `?v=5`, or Edge keeps serving the cached old image.

### 22.5 Startup auto-sync of Nexus

`POST /api/nexus/autosync`: backfills missing covers + checks for new versions,
fully silent. `nexus_meta.refresh()` gains a `days` parameter; startup sync uses
`days=1` — the first launch of each day really queries; further launches the same
day hit the cache, so Nexus isn't hammered. (The server is a
`ThreadingHTTPServer`; a 10-second sync never blocks the UI.)

Results are booked via `core/ui.py`'s `dismissed_upd`: a blue bar at the top,
"N mods have updates", with "see which" (jumps to the update-available filter of
"Check MOD updates") and "ignore". The fingerprint is computed from the
update-available list only, **with no timestamp** — 1.0.3 was bitten by an
mtime-based fingerprint re-lighting its own dismissed notice. No IDs configured,
offline, or Nexus misbehaving → silently skipped, leaving no trace in the UI.

### 22.6 Three-state theme icon

The three states (follow system / light / dark) previously had only two icons:
`auto` reused whichever sun/moon the system resolved to. A new `.ic-auto`
(left half a solid sun + right half a crescent) is added, keyed off
`data-theme-mode` rather than `data-theme` — the latter is identical for
"follow system + system is dark" and "manually dark", indistinguishable. The
first-paint inline script also sets this attribute, avoiding one wrong-icon frame.

### 22.7 Two more

- "Batch select" used `.btn-sm` (28px / 11.5px), visibly shorter between the 32px
  sort dropdown and view switch. Now `.btn`, with `.content-tools .btn` unified
  at 32px / 12px.
- Sidebar "Nexus tracking" → "Check MOD updates", dialog title too. Nine out of
  ten people opening it want to see new versions, not maintain a ledger.

### 22.8 Acceptance record (2026-10-28)

- **All 22 test suites green**, new `test_localize_detect.py` with 28 items:
  takeover verdict (enough keys / not enough keys, both directions), partial
  localization not misjudged, no-XML unaffected, 12 category-word cases,
  entry 0 still counts as the mod name.
- Real library: pending menu strings 37 → 0; `hoods Switchable Cloak` marked
  "hand-localized · stale 36"; Hood Slots' `Hoods` classified as an item name.
- Auto-sync measured: 17 mods with IDs, 14.5 s (cold cache), 6 updateable,
  fingerprint stable.
- Light/dark screenshots checked: bar text readable, buttons un-wrapped, the
  three toolbar controls equal-height.

---

## 23. 1.2.2: three things that "didn't look right"

No new features; all three are look-and-feel: the icon was ugly, the notice
couldn't be dismissed, and disabled mods sat in the middle being an eyesore. The
third one surfaces a real risk — **"reordering load order to make it look tidy"
should never be the user's job in the first place**.

### 23.1 Three-state theme icon: "follow system" becomes a half-lit circle

1.2.1's `auto` icon was a composite "left half sun + right half crescent"; at
16px it looked like two unrelated shapes squeezed together, and the user said
"ugly".

Redesigned as derived from the light icon: **the same circle, left half solid
(dark), right half outlined only (lit), rays on the right only**. The solid left
half is "the night side", the open right half "the day side" — more self-consistent
logic than compositing two icons.

**A 16px pit was stepped in here; recorded.** The first draft drew "solid left +
outlined right + three rays"; zoomed screenshots showed the three rays degrade
into three isolated dots at 16px — a 1.2-long, 1.5-wide round-capped line segment
is just 2×2 pixels on a 16px canvas. The light icon gets away with same-length
rays because 8 of them form a circular pattern; with 3 there is no pattern left.

Three rounds of experiments (ray length/radius/count, inner-outer dual radius,
thicker left arc) concluded: **at this size, "half-solid circle + rays" is not
achievable**. The rays were dropped, leaving the half-solid circle — itself the
conventional "light/dark contrast" symbol, read as "half lit, half dark", exactly
"follow system". The circle radius went 3.1 → 4.2 so both halves are voluminous
enough not to blur at the edges.

> To add rays back: in `web/templates/index.html`'s `.ic-auto`, add
> `<path d="M13.7 8h1.2M12.1 3.9l.9-.9M12.1 12.1l.9.9"></path>` — but at 16px it
> renders three dots; not recommended.

### 23.2 "N updateable" gains one-shot dismissal

The red pill in the top bar used to be pure display: always there, unclickable,
no way to close. Now it splits into two clickable areas — clicking the text opens
"Check MOD updates" filtered to updateable; clicking × dismisses this batch.

**What is dismissed is "this batch", not the feature forever**: it writes
`data/ui.json`'s `dismissed_upd`; the fingerprint is computed from the
updateable-mods list only, no timestamp. When the list truly changes (another new
version appears), the fingerprint changes and the notice returns on its own. It
**shares the same fingerprint** with the startup-sync blue bar, so dismissing on
either side removes both — no "closed one, the other still hanging".

Two fixes in passing: the pill's outer element changed from `<button>` to
`<span>` (a button inside a button is invalid HTML; clicking × would also trigger
"open the list"); and × is drawn as SVG rather than the character `×` (the
character aligns to the font baseline, sits high inside the pill, and its weight
varies across font sizes).

### 23.3 Disabled mods sort last (display only)

**Problem**: disabled mods sit inside the load order and look messy. The user's
workaround was dragging their load order to the end by hand — but that is **real
data**: once disabled-then-enabled again, it has to be dragged back.

**Fix**: after sorting by "load order", `visibleMods()` applies one stable
regroup: enabled always first, disabled always last. **Not a single priority
changes**; this is purely a display layer.

#### The real problem this uncovered: drag-reordering silently renumbered disabled mods

Display alone wasn't enough. Dragging a card used to `order.forEach` re-issue the
**whole table** as 10, 20, 30 — disabled mods included — pushing their numbers to
the end, reviving the old "looks low while disabled, position lost when enabled"
problem verbatim.

Changed to: **re-number only the enabled ones, and only hand out the numbers
they already occupied.** `/api/reorder` therefore gains the optional `prios`
parameter (1:1 assignment; must be equal in length, unique, non-negative).
Three benefits:

1. Disabled mods don't move a single number — exactly the "disable then enable,
   order unchanged" requirement;
2. No number collisions — a number belongs to exactly one mod, and the reorder is
   a permutation within the enabled set;
3. No invented numbers — after dragging, `mods.settings` still holds the same
   digits, so its diff is smaller.

Without `prios`, `/api/reorder` behaves exactly as before (whole table 10, 20,
30…) — the "Load order" dialog takes that path deliberately: it is the
**dedicated** place to adjust order, shows the true order (disabled not hidden),
and renumbering the whole table is precisely its job. A line was added at the top
of the dialog explaining why the two lists differ.

Dragging itself got a guard: dragging a disabled card is `preventDefault`ed with
"disabled mods hold no load-order slot; enable first to place them", instead of a
silent no-op.

### 23.4 Acceptance record (2026-10-28)

- **All 22 test suites green**; `test_actions.py` adds [7e], six items:
  renumbering only the specified mods leaves others' numbers untouched; the
  bundle still sorts first; three invalid inputs (length mismatch / duplicate
  numbers / negatives) are all rejected.
- Real library (19 mods, 4 disabled): the last four rows are exactly the disabled
  four (Priority 160/170/180/190), with every enabled row unmoved.
- "N updateable + ×" measured: clicking × returns `dismissed: true`; refreshing
  the UI removes pill and blue bar simultaneously; after reset both return.
- All four icon states screenshotted (follow-system light/dark, manual light,
  manual dark), sharp under both themes.

---

## 24. 1.2.3: new visual identity (2026-10-29)

### 24.1 Source and boundaries

The design files live in a local `waass-icon` folder (with `WAASS-ICON-SPEC.md`
attached, Chinese only; kept on the development machine, not in the repo): dark-gray ground + gold W wordmark,
three gold tiers `#FEEAAA / #E9B45F / #D99139`, three dark tiers
`#1E1E1E / #343636 / #0A0A0A`. The user set two boundaries explicitly: **use the
icon as-is** (no redrawing, no changes to the `waass.ico` container) and
**dual-theme but dark by default**.

### 24.2 Token layer

Colors are defined only in the `:root` and `[data-theme="dark"]` blocks;
components always reference variables. Two iron rules (from the spec's §3, written
into the stylesheet comments):

1. `#E9B45F` is for backgrounds and strokes only — **never text on light
   backgrounds** (1.9:1 on white);
2. In the light theme gold drops a tier: text uses `--gold-ink: #8F5A0E` (5.6:1),
   fills use `--accent: #B87A1E`.

New tokens: `--on-gold` (ink-on-gold `#1A1206`), `--on-accent` (white in light /
ink in dark, for text and checks on `--accent` fills), `--gold-1/2/3`,
`--gold-edge`, `--gold-ink`. Radii move 8/4 → 10/6.

Dark grounds take the spec's `#1E1E1E` as base, four steps up:
`--panel #262626` → `--panel-2 #2D2F2F` (cards) → `--panel-3 #343636`
(hover/pressed) → `--panel-4 #3D4040`. The spec labels `#343636` as "cards", but
in practice it fits hover better — using it for all 19 cards grays out the page.

### 24.3 Component-level changes (where tokens can't reach)

| Place | Change | Reason |
|---|---|---|
| Primary button | three-tier gold gradient + ink text + inner highlight | the only place on the site allowed a gradient |
| Top bar / status bar | 1px solid border → 1px shadow + long shadow | content reads as "embedded in the frame", not sliced into three bands |
| List view | transparent row backgrounds, hover-only fill, `transform` off | a block behind every row reads as "the whole column is selected" |
| Cards | white in light / `panel-2` in dark, hover lifts 1px | shadows are invisible on dark; card/ground difference + 1px inner highlight carry it |
| Selected card | gold stroke + faint outer glow | a 1px inner stroke disappears on dark grounds |
| Conflict panel `cf-info` | gold fill removed, gold left bar kept | a gold wash confuses with the warning background of "needs attention" |
| Toggles (dark) | `#5FBF8F` → `#4C9A77` | 15 mint-green pills stole the whole screen's attention |
| Scrollbar (dark) | `#3A3D3D`, hover `#5A5E5E` | the old `--border-strong` was too bright |
| `:focus-visible` | unified gold 2px ring | keyboard accessibility; mouse clicks don't trigger it |

### 24.4 Default theme

Without `localStorage['waass.theme']`, take `dark` — `index.html`'s inline script
and `app.js`'s `S.theme` must agree, or the first frame flashes light. The
three-state cycle, `data-theme-mode`, and the follow-system listener are
untouched. Users who ever toggled are unaffected — dark applies only when "no
record exists".

### 24.5 Icon integration

`waass.ico → web/static/icon.ico`, `-256 → icon.png`, `16/32/48/64/128` archived
under the same names; favicon version `?v=5 → ?v=6`; the top bar's and About's
`W` text boxes become `<img>` (top bar 26px from the 64px source; About 34px from
the 128px source).

**The single deliberate deviation from the design file**: `border-radius: 22%`.
Outside the source image's rounded corners the pixels are **pure black, not
transparent** (measured: 64px corners RGBA = `0,0,0,255`); unclipped, a darker
square shows on the dark top bar.

`tools/make_icon.py` draws the old purple-ground white W; it now has a gate: if
`icon.ico` already exists it won't write; pass `--force` to genuinely regenerate.

**Known issue (user chose to ship as-is, unfixed)**: all six frames of
`waass.ico` are PNG-compressed, while this project's convention is <256 as
BMP/DIB (some Windows components don't support small-size PNG and fall back to an
upscaled image). Plus the 16/32 frames come from AI-bitmap downsampling — the
taskbar may be less crisp than 1.2.1's hand-drawn version. If it's ever fixed:
`make_icon.py` already has a reusable BMP encoder.

### 24.6 Debug parameters (new)

`?dialog=settings | localize | order | track` opens the corresponding dialog
directly, combined with the existing `?theme= / view= / select= / pick= /
nosync=1` for headless screenshot verification.

### 24.7 Acceptance record (2026-10-29)

- All 22 test suites green (run one by one:
  `for f in tests/test_*.py; do python $f; done` — `unittest discover` can't pick
  up these script-style suites).
- Screenshot matrix: dark/light × cards/list, details panel, conflicts, batch,
  settings (incl. About), localization, load order, check updates. Three issues
  fixed: list-row fills, `cf-info` gold wash, over-bright dark toggles.
- When starting the server with `WAASS_MULTI=1 python run.py --serve-only`, do
  not use a Bash background job — it gets reaped; use a managed background task.

---

## 25. 1.2.4: version determination and some cleanup (2026-10-29)

### 25.1 Reporting "update available" for something already updated

User's field test: after updating 3 mods and rechecking, local versions hadn't
changed and all 3 still showed as "update available". Three independent causes,
each with data evidence (real library: 20 mods, 380 packages in the download folder):

| # | Cause | Evidence | Fix |
|---|---|---|---|
| a | Package-name separator only accepted underscores | The new package is `ModSettingsMenuFix 13284 1.5.0 2026-…Z token.zip`; parsing returned `None` | `registry._NEXUS_RE` separator widened to `[_ ]`; fallback regex likewise |
| b | `content/info.json` never read | Hoods' `info.json` says `"version": "3.7"`; the old logic only looked at `version.txt/modinfo` | `scanner._collect` gains an info.json branch; `ModRecord.version_file` marks the source |
| c | Install archives (snapshots) always outrank newer packages | LightRewrite archived 0.27.1 (installed 10-25); 0.28.0 downloaded 10-29 | When a later-dated package exists for the same ID, the archive doesn't participate in comparison |

### 25.2 New rule for the local version: take the highest

The old rule took the first hit by "source trustworthiness" (manual > archive >
history > download package > bundled file). The problem: **after an update, the
source that wrote the file changes** — info.json refreshes, the package name
refreshes, but the archive stays at the old value; taking the first by
trustworthiness systematically favors stale snapshots.

Now: manual entry still wins; every other source contributes a candidate and
**the highest version number is taken** (compared via `core/version.vkey`);
source precedence decides only the "provided by" annotation when versions tie.

Known trade-off: a package that was **downloaded but never installed** also counts
as "installed", which could hide one genuine update. The reverse error (treating a
stale snapshot as installed) is exactly the symptom the user reported, so this
side is chosen. The line is documented in a `track.py` comment.

### 25.3 Game version

`core/gamever.py`: searches `bin/<x64_dx12|x64>/witcher3.exe` for
`v X.YZ<letter>\0` (the same string the main-menu corner badge uses). Measured:
hit in 20 ms on a 90 MB file, cached by `(path, mtime, size)`. If extraction
fails it falls back to the PE file version (`5.0.16.41566`); if that's missing
too it returns empty — the UI hides the badge rather than showing a version that
matches nothing.

The root-level `BUILD-INFO.json` says `game_version: 5.00c` and **must not be
trusted**: it is some mod's own build manifest (`mod_version: 1.9.2`). The real
game version is at 5.01c.

Displayed in the sidebar under "Game folder", next to the path text: "Game
version 5.01c", in the gold-text tier (`--gold-ink`, 5.6:1 on light) — not
gold-as-background.

### 25.4 Localization: `name + version number` isn't translatable

Mod Settings Menu Fix's `cn.w3strings` has 7 entries: 5 Chinese, plus
`Mod Settings Menu Fix` and `Mod Settings Menu Fix 1.5.0`. The old rule treated
only entry 0 as the mod name and took the versioned one as a menu sentence;
meanwhile `inspect()` did no classification at all and counted both as pending.
In game it is entirely Chinese.

Two fixes:

1. `classify()`: `_is_name_with_version()` — after stripping punctuation, equal
   to "mod name + pure-numeric version" → `mod_name`;
2. `inspect()`'s todo count now goes through `classify()`, **sharing one judgment
   with `build_jobs()`**. The panel and the task pack each telling their own
   story was the structural cause of this false positive.

The panel copy changed too: when only mod names / item names remain, it shows the
computed `why` (e.g. "already fully Chinese") instead of a fixed "only mod and
item names remain".

### 25.5 Two removals

- **Settings → Install behavior** (copy the original package after install): UI
  block, `/api/settings` whitelist, `install.py`'s copy logic, `ARCHIVES_DIR`,
  and test [11] all removed. The `data/archives` cleanup item is kept, renamed
  "original package copies left by older versions", purely to sweep leftovers.
- **The search box's `/` hint**: the `<kbd>`, `.search kbd` styles, and the `/`
  focus branch in `keydown` all removed (the user asked for the shortcut to go too).

### 25.6 Acceptance record (2026-10-29)

- **All 23 test suites green** (new `tests/test_gamever.py` 10 items;
  `test_track.py` adds [9b] 8 items; `test_localize_detect.py` adds [5b] 4 items).
- Real-library rerun: "update available" dropped from 5 to 3 (All Quest Markers
  1.7.0→1.7.3, Seamless HUD 2.8.0→2.11.0, Smooth UI 0.3→0.3.2 — the three the
  user genuinely hadn't updated); Hoods 3.7 / Mod Settings Menu Fix 1.5.0 /
  Light Rewrite 0.28.0 all became "up to date / local ahead". Localization
  pending fell from 2 to 0; the panel shows "already fully Chinese".
- Parsing regression: across all 380 packages, old vs new parsing differs
  **only on those 3 new packages**; not a single old-format result moved.

---

## 26. 1.2.5: fit for other people (2026-10-30)

This version's goal is not features; it is "published on GitHub, cloned by
someone else, and it runs as their own clean program".

### 26.1 Game-directory detection: three routes + fallback

The old `_CANDIDATES` was 7 hardcoded paths, the first being the dev machine's
`D:\game\steam\...` — a guaranteed first-hit on the author's machine and an
almost-guaranteed miss on anyone else's. "Automatic detection" was an empty claim
in a release.

Now (`core/paths.py`):

```
WAASS_GAME_DIR  →  data/config.json  →  registry / launcher manifests  →  public default locations
```

| Source | What is read | Why read it this way |
|---|---|---|
| Steam | `HKCU\Software\Valve\Steam\SteamPath` → every `"path"` in `steamapps\libraryfolders.vdf` | Multi-drive, multi-library is the norm for this game. No VDF parser is built: the file is one level deep, a regex over `"path"` is the cheapest thing that works, and one missing unrelated field can't fail the whole read |
| GOG | `HKLM\SOFTWARE\WOW6432Node\GOG.com\Games\1495134320`'s `path` | Galaxy is a 32-bit program writing to WOW6432Node; both `KEY_WOW64_32KEY` and direct read are attempted |
| Epic | `%PROGRAMDATA%\Epic\EpicGamesLauncher\Data\Manifests\*.item`'s `InstallLocation` | Manifests are JSON; one corrupt file is skipped. **`DisplayName` is never trusted** — it may be localized; paths only, then through `is_game_root()` |

Three hard constraints:

1. **Every source must be skippable.** Locked registry, corrupt-JSON manifest,
   nonexistent path — each wrapped in `except OSError`; a failed probe should
   promote the next candidate, not crash startup.
2. **Candidates must deduplicate.** Steam's library list lists itself, the
   registry adds another, and casing disagrees (4 duplicates measured on one
   machine). `_dedupe()` normalizes with `normcase`.
3. **Cache detections, but never cache a dead path.** `_DETECT_TTL = 20s`; on a
   cache hit `is_game_root()` still re-verifies — if the game was uninstalled or
   moved, the next call must return `None`, not a path with no exe.
   `save_config()` clears the cache so config changes take effect immediately.

`detect_game_root(refresh=True)` is the "search again" button's path;
`forget_detected()` only drops the cache.

### 26.2 Read-only means read-only

"A clean delivery" means more than no `data/` in the repo: **when the user has
clicked nothing that writes, the disk must gain nothing**. One measured pass
caught two cases:

| Observation | Verdict |
|---|---|
| Opening the localization panel generates a pending-translation task pack | **Keep**. It's the panel's display artifact, produced only when the user opens it; `storage.GROUPS`'s `jobs` group can clean it |
| The `--no-game` scenario still wrote `legacy.json` + `thumbs/` | A test-design flaw: the dev machine really has the game installed and detection found it. Simulating a new PC requires cutting detection and fallback together |

`tools/coldstart.py`: empty `data/` + a fake game directory, runs 21 read-only
endpoints, asserts "all 200", "no `data/` created", "nothing new inside the game
directory". Then `--no-game` reruns it for "this machine has no game". Mandatory
before release.

### 26.3 Host / cross-site validation on write endpoints

`GET /` requires no auth and writes the token straight into the HTML. Attack
chain: a malicious page rebinds its domain to `127.0.0.1` → the browser requests
`/` as **same-origin** → obtains the token → `install` / `uninstall-all` both go
through.

`X-Waass-Token` cannot stop this (after rebinding it *is* same-origin; custom
headers are legal). The effective breakpoint is **validating the Host**:

```python
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
```

`do_GET` / `do_POST` each call `_forbidden()` at the top; failure returns 403
JSON. `Sec-Fetch-Site: cross-site` is also refused (browsers send it
automatically; curl and local scripts don't, so absence is allowed through).

**`/api/bye` must stay exempt**: `navigator.sendBeacon` cannot send custom
headers — all it can offer is the query-string token plus the browser's automatic
Host / `Sec-Fetch-Site: same-origin`. Host validation doesn't affect it, but if
anyone later adds "write endpoints must carry an `X-Waass` header", this chain
breaks first: window won't close = background process left behind = next double
click hits the single-instance lock.

`/api/open-folder`'s `key` additionally requires that "`resolve()`'s parent
directory must be exactly `mods`". `is_dir()` alone is not enough:
`key = "..\.."` also hits a real directory, equivalent to allowing any folder on
the machine to be opened.

### 26.4 Small fixes

| Place | Problem | Fix |
|---|---|---|
| `actions.reorder` | `set_state()` defaults `create=True`; an unknown key would materialize as a fresh `[xxx]`, and the UI returned `ok` | First collect keys `_resolve_folder()` can't find, **touch nothing**, raise `ValueError` → 400 |
| `install.js` `metaLine` | author / version injected into `innerHTML` without `esc()` | escape each segment |
| `run.py` `background_color` | hardcoded light `#F4F4F7` while 1.2.3 defaults dark → white flash on first frame | hardcode `#1E1E1E`. **It cannot "follow the setting"**: the theme lives in the browser profile's localStorage, unreadable server-side |
| `style.css` `--text-3` | light `#8A8478` = 3.3:1, dark `#807C74` = 4.0:1, both below AA's 4.5 | `#6E695F` (5.0:1) / `#9A958B` (5.6:1) |
| `server.py` `server_version` | hardcoded `Waass/0.1`, decoupled from `APP_VERSION` | `f"Waass/{appinfo.APP_VERSION}"` |

**The focus ring needed no change**: `:focus-visible { outline: 2px solid
var(--accent) }` has been at the end of `style.css` since 1.2.3. The audit report
judged it missing by "occurrence count = 2" — it counted strings without reading
them.

### 26.5 Dead code

`core/names.py` + `core/nexus.py` + `tests/test_names.py` deleted.
`nexus.py` was referenced only by `names.py` (`nexus_meta.py` is a different
module for automatic metadata and remains in use), and `names.apply_to()` had
been commented out since 1.2.1 — the whole chain was unreachable.

`storage.GROUPS`'s `jobs` group keeps the `names.json` / `names` path entries:
users may still have those files locally, and staying on the cleanup list
(rather than in KEEP) is what makes them cleanable.

### 26.6 Release preparation

- `.gitignore`: `build/`, `dist/`, `*.spec`, `data/`, `_backup/`, `_shots/` all
  excluded. No `.gitkeep` in `data/` — verified that an empty directory runs fine.
- `CHANGELOG.md`: version history moved out of the README; the README's first
  screen keeps one line + a link.
- `Waass.bat` rewritten: uses only `pythonw` / `python` from PATH; no one's
  install path is written in. **The file must be GBK-encoded** — saved as UTF-8
  with `chcp 65001`, `if` blocks containing parentheses misparse; in testing it
  reported "command syntax incorrect".
- README first screen: system requirements (Windows 10/11 x64 only + game v5.x +
  Edge/Chrome), three 1.2.5 screenshots, exe / source install paths, SmartScreen note.

### 26.7 Verification record

- **All 24 test suites green** (new `test_http_guard.py` 20 items,
  `test_paths_detect.py` 25 items; `test_names.py` deleted; `test_actions.py`
  adds [7f] 3 items).
- Real detection on this machine: `_probe_candidates()` read 4 candidates from
  the Steam registry, the first matching the real game directory; cold probe
  2 ms, cached 0.1 ms.
- Smoke test (packaged exe): exactly **1** visible window added (1792×1097), no
  stray popups; after `WM_CLOSE`, all processes exited within 9 seconds and
  visible windows returned to baseline.
- Cold start: both with-game and without-game scenarios pass, nothing written
  into `data/` or the game directory.
- `doctor.py check`: `mods/` gained `modUIRE_AlwaysQuestMarks` (installed by the
  user), `mods.settings`'s mtime was touched by an external tool but its content
  is 961 bytes / 21 sections / no ghost entries, and `ModSettingsMenuFix.xml` is
  the user's own 10-29 install. None of it was changed by this program.
- Package: `dist/Waass-1.2.5.exe`, 8.9 MB.


## 27. 1.2.6: where a mod's "identity" comes from (2026-10-30)

### Origin

After the user wiped `RMMR Mod Manager`, covers, authors, and update checks all
went dead; clicking "refresh metadata" did nothing. Digging in showed the sync
wasn't broken — **the entire chain's starting point was gone**.

### Root cause: the ID had exactly two sources, and neither was ours

`nexus_id` decides whether author, cover, and latest version can be fetched.
Through 1.2.5 it came from only two places:

1. `data/legacy.json` — the RMMR archive (frozen once, itself independent of RMMR)
2. `scanner._find_nexus_id()` — `nexusmods.com/...mods/12345` in readme/txt/md
   files inside the mod folder

Wiping RMMR **while also** using a brand-new `data/` (the exe was copied to a
different folder for testing) zeroed both sources at once. Measured: 21 such mods
on the machine, readme hits **0**.

This isn't only "an old user cleaned out the old software" — **any new user who
never installed RMMR starts in exactly this state on first launch**. "Check for
updates" is entirely unusable for them.

### Current state: five tiers of evidence

| Tier | Source | Trust | When it fails |
|---|---|---|---|
| 1 | `data/track.json`, manual entry | Highest, always first | — |
| 2 | `data/nexus_ids.json` (recorded by this module) | High | Only if `data/` is deleted |
| 3 | `data/registry.json` install archives | High (parsed from the package name at install) | Absent if the mod wasn't installed via this program |
| 4 | Original package name in the download folder | High (the ID is Nexus-generated) | When the original package is deleted |
| 5 | Links in the readme | Medium (hand-written; may point at a sibling mod) | When the mod ships no readme |

Tier 2 is tier 4 **made persistent**: once recognized it is written to disk
immediately, so deleting the original package later loses nothing. This matters —
the download folder is a place users clean at will and must not become a
long-term dependency.

### Matching rule: count word differences, not similarity scores

`_word_diff()` counts the symmetric difference of two names' word bags.
Automatic binding requires:

1. 0–1 differing words (`Hoods`↔`Hoods` differs by 0; `RoninArmor`↔`dlcRoninArmor` by 1)
2. Exactly one ID satisfies the condition
3. Score passes `_BIND_SCORE = 62`

Two or more differing words never binds. Example: `Swords On Hip` vs
`Swords On Hip When Cloaked` differs by 2; vs `Dynamic Swords (and Crossbow) On
Hip` by 3 — neither is the counterpart. **A wrong guess is worse than an empty
slot** ("check updates" would keep reporting someone else's versions).

Measured scores and verdicts (21 mods on the machine):

- Auto-recognized **16**, **0** misbound
- 4 await a manual click, 2 of which are self-made/port-overs
  (`UIRE_Travel`, `UIRE_AlwaysQuestMarks`)
- The other 2 (the merged bundle, and HoodSlots which reuses its upstream) don't
  exist on Nexus as separate pages either

### Five recorded pitfalls (each took one revision to settle)

1. **Character-level matching fails across the board.** The folder is
   `modSwordsOnHip` (run together); the Nexus title is `Swords On Hip` (spaced);
   `mod_hoodsSwitchableCloak` and `Switchable Cloaks - Hoods` even reverse word
   order. Camel-case splitting + word-bag comparison is mandatory.
2. **Loose word scoring misbinds.** `UIRE_AlwaysQuestMarks` and `All Quest
   Markers On Map` share only the word `quest`; even Jaccard gave 59 and it was
   the sole top score → misbind. A "coverage ≥ 60%" guard was added to block it.
3. **`_LANG_WORDS` must never contain `remaster`.** Most Remastered body mods carry
   that word (`UI Remastered Edition` / `SeamlessAdaptiveHUDRemastered`);
   including it scores every body mod −45 as a "translation", recognizing
   nothing. Two-letter codes keep only `cn/ru/pl/fr`; `it`/`de`/`es` are normal
   English words (`Everything is on it`).
4. **Nexus's by-name search is multi-word AND; more words, fewer hits.** Measured:

   | Query | Results |
   |---|---|
   | `autoloot` | 12 |
   | `auto loot` | 6 |
   | `auto loot enhanced` | **0** |

   Originally only one query of "all split words" was sent, so any
   "feature word + qualifier" naming (`AutoLoot Enhanced`, an extremely common
   shape) eternally returned no candidates. Now `identify.search_queries()`
   produces tiers ("all words → strip wrapper words → first two words → first
   word"), and `nexus_meta.search_multi()` tries tier by tier, stopping at the
   first tier with results. Note: the `name` field (non-stemmed) with
   `WILDCARD` returns 0 across the board, and `nameStemmed` only accepts
   `MATCHES` — any other op errors on schema. Exactly one spelling works on
   this route.
5. **Wrapper words push the true match below the candidate line.**
   `AutoLoot Enhanced` vs `SmartAutoLoot Remaster V1.1.7`: coverage 2/3, three
   extra words `smart`/`remaster`/`v1` → 66 − 60 = 6 points, below even the
   40-point candidate line.
   - `_FILLER` (enhanced / plus / redux / edition / `v` …) is ignored only when
     **counting extra words**, never when computing **coverage** — otherwise
     everything "covers" everything.
   - Version prefixes like `V1.1.7` are dropped inside `tokenize()`
     (`isdigit()` only matches pure digits; `V1` would survive otherwise) so
     they aren't taken as real words.
   - But when **counting differing words, `remaster` still counts**
     (`_FILLER_BIND` excludes it from filler): the Remastered edition sometimes
     really do get a separate page; erasing this difference auto-binds wrongly.

### Ordering: evidence tier before score

`merge_candidates()` sorts by `src` (both > local > nexus) first, then score.
Pure score ordering fails: AutoLoot's true match `SmartAutoLoot Remaster` scores
only 46 and was pushed to third by `Auto Loot Herbs` (13228 — more downloads,
higher score), and a human skips right past. The original package name in the
download folder is hard evidence from this very machine and belongs ahead of a
same-named mod found online.

### "Not yet matched to Nexus" must not be permanently dismissable

Self-made / ported mods will never be found; if the bar keeps showing those 2
forever, the user quickly learns to ignore every notice. `data/track.json` gains
an `ignored` list (parallel to `manual`; `save_manual()` touches only the
`manual` field, so they can't clobber each other), and each ID-less row in the UI
gets a "not on Nexus" button.

### Online candidates: prep the dish, never order it

`/api/nexus/guess` merges the two routes' candidates (local package name + Nexus
search) into `data/nexus_guesses.json`, **not re-searching within three days**.
Nothing binds automatically — too many same-named mods by other authors exist
(searching `AutoLoot` returns four or five implementations by different authors;
the true one merely ranks first). The UI's `merge_candidates()` tags
`src: local / nexus / both`, and a local hit adds "this package is in your
download folder".

### Tests

- New `tests/test_identify.py` (12 groups, 48 items): tokenization, word
  difference, auto-binding, ambiguity stays silent, translations never bind,
  archives take precedence, no crash when the download folder is unreadable,
  remember/forget, candidate filtering, two-route merge, search tiers, wrapper
  words and version prefixes.
- All 25 test suites green.
- Cold start: with and without game both pass, `data/` still not created (the
  fake game has no mods, so the recognition cache never writes — this assertion
  covers only the "no mods" case).
- On the machine: 16 automatic / 4 awaiting confirmation (2 of them self-made) /
  0 misbindings; portable mode (exe copied to an empty folder) verified `data/`
  lands beside the exe, all 16 IDs' metadata and covers fetched, and after
  closing the window all processes exited within 9 seconds with nothing left.

## 28. 1.2.6 second batch: disable switches mechanism, two false positives, the manual moves in (2026-10-30)

### Disabling switches to folder renaming (see §2)

`actions.set_enabled()` switches wholesale from "write `mods.settings`'s
`Enabled=`" to "`rename()` adding/removing the `~` prefix". Four constraints:

1. If the target folder already exists (`~modX` and `modX` both present),
   **error out, never overwrite**.
2. Renames go through `safety.Operation.record_rename`; `undo_last()` moves them
   straight back.
3. **`mods.settings` is written only when "enabling + the record still says
   `Enabled=0`"**, and still `backup()`s first. Disabling never writes it.
4. Batch toggling `set_enabled_many()` is isomorphic: compute all target names
   and run `safety.assert_writable()` on each first, then rename one by one, and
   only then consider writing settings once. **Validate everything before
   touching anything** — no being refused halfway through.

Two `scanner.py` warnings of the form "this one was renamed the old way; consider
`Enabled=0`" were removed — the old way is now the official path; further hints
would mislead.

### Menu-XML attribution: coverage, not containment

Symptom: `hoodsSwitchableCloak` was clearly localized, yet the panel still said
"4 strings pending".

Two bugs stacked:

- **Attribution failed**: `mod_hoodsSwitchableCloak`'s stem is
  `hoodsswitchablecloak`, the filename is `switchablecloak`; `stem in flat`
  doesn't hold → the XML was treated as an orphan and its Chinese unclaimed.
- **Two paths, two stories**: `localize.inspect()` (the panel) never called
  `xml_takeover()`; only `build_jobs()` (task-pack generation) did. So the panel
  said "pending" while the task pack said "none".

The attribution algorithm is now (`scanner.py`):

```
Flatten: strip mod / _ / - from both, lowercase
Identical                      → 1.0
One is a substring of the other → len(short) / len(long)
Neither                        → 0
Only the highest coverage with >= 0.5 counts as claimed
```

Why 0.5: `modCloak` vs `SwitchableCloak.xml` is 5/15 = 33% (same root word but
genuinely not it — hard-claiming would steal someone else's menu), while
`mod_hoodsSwitchableCloak` is 15/21 = 71%. **Take the highest score, not the
first hit**, or alphabetically-early `modCloak` would claim first.

`inspect()` now also calls `xml_takeover()`, sharing one judgment with
`build_jobs()`. Taken-over English is recorded in `stale`, and the UI says
"hand-localized (via menu XML); N language-table entries are stale leftovers" —
strictly separated from "pending".

### Dialog close button misaligned: `button`'s default padding

`.icon-btn` is a 32px square holding a 16px SVG, yet X hugged the top-right
corner.

The cause wasn't flex but `padding`: the browser default for `button` is
`padding: 1px 6px`; under `box-sizing: border-box` a 32px width loses 6px on
each side, leaving an 18px content area (16px icon + 1px borders ×2 already
doesn't fit), shoving the icon aside.

The fix is **explicit `padding: 0` + `inline-flex` centering**, never relying on
defaults. The dialog title bar's instance is separately tightened to 26px / 14px
icon (`.modal-head .icon-btn`).

Rule: **any centering that leans on "the default is probably right" must be
pinned down explicitly.**

### Script Merger: usable even when installed elsewhere

`merger.py` gains a custom path (stored as `merger_exe` in `config.json`):

- `configured_exe()` returns the configured path (whether or not it still exists)
- `_custom_exe()` accepts it only when the file truly exists; otherwise it falls
  back to auto-discovery of `<root>/Merger/`
- `exe_path()` order: custom → `<root>/Merger/Merger.exe`
- `set_exe()` validates existence; `clear_exe()` drops the custom path

`POST /api/merger/locate` goes through `picker.pick("exe")` (the native dialog);
`clear:true` drops the custom path. If the filename contains neither `merger`
nor `script`, it returns `suspicious` — the UI notes it but doesn't block; the
user may have renamed their Merger.

Three UI states: missing (download link + pick location + explainer) / bundle
not in effect / ready (marked "your chosen location" + "back to auto-discovery").

⚠️ `data-open` is not a global convention; only `track.js` binds it. Other
panels must use explicit ids with event bindings, or clicks do nothing.

### The manual: content is a file, not JS strings

`web/static/guide.html` is a **pure fragment** (no `html/head/body`);
`settings.js::openGuide()` fetches it into the dialog.

- No separate browser window: the `--app` window has no address bar; once opened
  out, there's no way back.
- The nine sections are not embedded as JS strings: editing copy then means
  touching code.
- If the fetch fails only this page is missing; the `catch` states clearly
  "other features are unaffected".
- All styling goes through CSS variables (`.gd-*` in `style.css`); dark/light
  share one palette; no second color set is maintained.
- Every asserted fact was checked against source: snapshots keep 30
  (`storage.prune_snapshots(30)`), metadata cache 7 days
  (`nexus_meta.TTL_DAYS`), localization history 20 batches
  (`localize_backup.KEEP_HISTORY`). **Writing copy from memory is an incident
  generator.**

Debug entry: `?dialog=guide`.

## 29. 1.2.7: closing the window must exit (2026-10-30)

### Symptom and evidence

Found during acceptance: the UI window was long gone, yet `python run.py` kept
running in the background. The records — at 18:09 `inspect_windows.py msedge`
reported "no visible window", while on the same machine the run.py process had
been alive 48 minutes; it ended only via a manual kill. This drags along a more
annoying symptom: the next double click hits the single-instance lock and pops
"Waass is already running; close that window first".

### Root cause

Exit had exactly one path, all riding on one front-end shot:

```
pagehide → sendBeacon('/api/bye?t=<TOKEN>') → server bye_at=now → os._exit after 6 s
```

**That shot at window-close is unreliable.** The renderer may be reaped by the
kernel before the beacon is even queued; the browser also drops the packet while
tearing down its network service. If the packet never leaves, the server waits
forever for a greeting that never comes — and `_watch_close_loop` only checks
`bye_at`, which stays 0, so it `continue`s until the end of time.

### Fix: three criteria in parallel; any one triggers exit

| Criterion | Condition | Purpose |
|---|---|---|
| `bye` | The page said goodbye, the 6-second grace elapsed, and no new page loaded in between (`GET /` clears it) | Fast path; a normal close exits in about 6 s |
| `no-window` | App-window mode AND the page loaded ≥15 s ago AND no visible window titled `Waass Mod Manager` found 3 checks in a row AND no requests in the last 8 s | Backstop; exits even when the beacon was lost |
| `no-page` | App-window mode AND not a single page load in 60 s | The shell never came up; don't lie around |

- `core/winwatch.py`: `EnumWindows` + `GetWindowTextW` + `IsWindowVisible`.
  A minimized window still has `IsWindowVisible` true, so minimized does not
  count as "window gone". On non-Windows it returns `-1` (unknown); callers must
  treat it as unknown, **never as 0**.
- Why also "no requests in the last 8 s": window counts have transient error
  (repaints, restore animations); on that signal alone it misfires. Both must
  hold before acting.
- Why only when `app_window`: the `--browser` fallback is an ordinary tab; its
  title changes with every tab switch, and window-based judgment would misfire.
- Heartbeat: the front end pings `GET /api/health` every 15 seconds (no token
  required), and the server calls `touch_page()` at every request entry to
  record `last_seen`. Without a heartbeat there's no way to tell "open but idle"
  from "already closed".
- Fuse: before exiting, `threading.Timer(3.0, os._exit)` fires first, then
  browser cache is cleared — cleanup can be held up by antivirus/file locks and
  must not get to decide when the process dies.
- `WAASS_KEEP=1` disables the whole auto-exit (for running endpoints and
  watching windows).
- The exit reason is appended to `data/lifecycle.log` (last 8 KB kept), so
  afterwards nobody guesses which criterion fired.

### The criteria are a pure function

`server.exit_reason(now, life, win_count, ...)` touches no clock and queries no
windows; everything is passed in, and it returns `'bye' / 'no-window' /
'no-page' / ''`. `tests/test_lifecycle.py` feeds fake timestamps to cover 11
scenarios; real windows are left to smoke testing (if it can't be constructed,
say so — **never substitute "looks right" for a real measurement**).

Measured: browser never started, "window gone" made to hold — exit in 18.8 s
(warmup 15 + 3 samples).

### The manual: three errors found this time

In `guide.html`, "notice bar colors", "load order", and "import formats" all
disagree with the code:

1. The text said "gold = update available, blue = unmatched to Nexus" — in
   reality there is **one** amber `.syncbar`, with the two matters separated by
   `<i class="bar-div">` (changed in 1.2.6); warnings are a different, **orange**
   `.warnbar`.
2. The text said "lower numbers load first, and earlier-loaded wins conflicts" —
   the second half is unverified engine behavior. Only the program's own
   semantics are kept: lower numbers mean higher priority; on the same file, the
   lower number wins; equal numbers are reported by `conflicts.py` as "who
   overwrites whom is luck".
3. The text listed only zip/7z/rar; `archive.SUPPORTED_EXT` actually has 8.

**Lesson (second occurrence)**: every sentence in the manual must trace to a
line of code — colors, numbers, and orderings above all. Last time (1.2.6) it
was numbers that mismatched; this time, colors and semantics.





## 30. 1.2.8: Bilingual UI (2026-10-30)

### Decisions

- No gettext, no frontend i18n framework. The whole catalog is two JSON files:
  `web/i18n/zh.json` and `web/i18n/en.json`, read by both the backend
  (`core/i18n.py`) and the frontend (`web/static/i18n.js`) — server and client
  always draw from the same source, so the UI never shows English chrome with
  Chinese popups (or the reverse).
- The catalog is inlined into the index page (`%%WAASS_I18N%%`), not fetched at
  runtime. Static text fills in the moment the script loads; one step later and
  the user sees a Chinese flash first. `</` is always escaped to `<\/` so a
  stray `</script>` inside a string cannot close the tag early.
- Switching language = save the preference + full page reload. No hot-swap:
  hot-swapping means every panel must know how to re-render itself, and the one
  that forgets ends up half-translated. A reload takes 0.3 s and misses nothing.
  Confirmed with the user.
- The language setting is three-state in `config.json`: `auto` (default, follow
  the system) / `zh` / `en`. Any non-Chinese system gets English
  (`GetUserDefaultUILanguage`, falling back to environment variables off
  Windows). No Russian or Polish translations — those users get English.

### Rules (for all future strings)

1. Keys are `namespace.use`, one full sentence per key, **never concatenation** —
   Chinese and English word order differ.
2. Placeholders are `{name}`; `t()` uses replace, not format (braces in the text
   cannot crash anything).
3. Plurals are 1 / other only: `tn(one_key, many_key, n)`. There are 50 plural
   pairs; `tests/test_i18n.py` checks they stay paired.
4. Never call `t()` at module top level (language switches at runtime).
   Constant tables become read-only Mappings that translate on read (see
   `localize._CatText`, `storage.GROUPS`).
5. **The frontend must never match backend responses by translated text.**
   Before 1.2.8 three places matched on Chinese strings (install warning
   de-dup / restore drift / is-live highlight); under the English UI they would
   silently break — all three now use a stable `code` field from the backend.
   Any new cross-boundary check gets a code first.
6. New keys go through `tools/i18n_merge.py`: conflicting keys are an error, an
   English placeholder missing from Chinese is an error, an English omission of
   a Chinese placeholder is a warning. `tests/test_i18n.py` checks key parity,
   empty values and CJK leftovers in English.
7. **Proper nouns must line up across the two languages** — one side may not use a
   second, informal name for the same thing. Settled: the game edition is 中文
   「重制版」/ English **The Witcher 3 Remastered** (before 1.2.9 the English said
   `(next-gen)`, which names a different thing and has been corrected).
   Careful: **`NEXT-GEN` inside real mod package names is the mod's own title and
   must not be touched** — the `remaster` matching logic in `core/identify.py`
   exists precisely because of those names.

### Documentation

Native English, not a literal translation: `guide.en.html` (loaded automatically
in the English UI), the English half of `README.md`, `CHANGELOG.en.md` and this file's English
counterpart. English users do not install localization packs, so "CN
localization" is presented as a feature (translating English mods into the
community's Chinese), not a tutorial.
## 31. 1.2.9: data compatibility and startup self-check (2026-10-30)

### Goal (the user's own acceptance criterion)

> Iterating a new version must be seamless for users: replacing the main program
> is all it should take.

The test: **keep `data/`, swap only the exe, and everything is still in place when
the program opens** — mod list, groups, Nexus IDs, install records, localization
originals, save game backups, operation log. Nobody should have to export, import or
move folders to upgrade, and nobody should have to guess whether their data survived.

### Data version

`data/meta.json`:

```json
{"schema": 3, "app": "1.2.9", "first_seen": "…", "last_seen": "…",
 "history": [{"schema": 3, "app": "1.2.9", "at": "…"}]}
```

- `schema` is the data format version. 1 = early 1.0.x; 2 = 1.2.8 and earlier (a few
  JSON files, no version marker); 3 = 1.2.9 onward.
- `app` means **the version that ran last** and is owned solely by `compat.touch()`.
  `prepare()` must never write it — writing it there makes the self-check conclude
  "version unchanged" forever.
- No `meta.json` but a `config.json` or any core ledger → treated as legacy (v2).
  No data at all → fresh. Neither path may crash.

### Migration

- **Back up before touching anything**: the core file set is copied to
  `data/_backup/pre-schema<old>-<timestamp>/` with a `README.txt` explaining how to
  roll back. If the backup fails, **nothing is migrated** (better not to upgrade than
  to edit data unprotected).
- **Idempotent**: `_STEPS = {2: _migrate_2_to_3}`; every step must produce the same
  result when run twice. After an interruption (power loss, kill) the next launch
  resumes from the schema recorded in meta.
- The 2→3 step deliberately **changes no data format at all**: it only creates the
  common folders, quarantines unreadable files and stamps the version. Real format
  changes belong to a future 3→4; historical steps are never edited.
- Folders are only filled in for existing users with data; a fresh install creates no
  empty directories (`tools/coldstart.py` verifies that a launch adds no files).

### Damaged files

An unparseable `xxx.json` is renamed to `xxx.json.broken-<timestamp>` and kept; the
app carries on as if the record were missing. **Never deleted** — a missing brace from
a hand edit must not mean data loss.

### Downgrade protection

When `schema > SCHEMA` (the data was written by a newer build):

- `WAASS_READONLY=1` is set and `guard` refuses every write from then on;
- **meta is not rewritten, no migration, no quarantine**;
- a red banner explains "data is vX, this build only understands vY, switched to
  read-only" and **offers no dismiss button**.

### Startup self-check (`core/selfcheck.py`)

Runs once per launch *before* the server starts (`run.py`), because "does this build
understand the user's data" must be answered before anyone reads it. The self-check
swallowing its own exceptions is intentional — a check that locks the user out is
worse than no check.

Checks: did the version change (`meta.app` comparison), schema compatibility, can the
core JSON files be parsed, is the configured game folder still there, does
`update_feed` look like a URL, is the data folder writable (a real temp file is written
and removed).

The result goes to `data/selfcheck.json` with the last 10 launches kept (so you can see
when the version changed). **In read-only mode the result is still kept in memory** even
though it can't be written to disk — otherwise the user would be told "your data comes
from a newer version" by means of exactly zero words.

### Top banner (iteration requirement 3: updates or self-check problems must surface at the top)

- Position: top of the content area (`#selfBar`), ahead of the "needs attention" and
  "N updates available" banners. It goes first because it answers "is my data still
  there".
- Three levels: green (just updated and data verified / data migrated), amber (a file
  was quarantined, game folder gone, update source misconfigured), red (data
  unreadable, read-only downgrade).
- Green and amber can be dismissed (`dismissed_self` in `ui.json`, fingerprint built
  from the items only, never from time); **red has no × button** — dismissing it would
  hide the only explanation the user gets.
- **Messages carry `code + params` only**; the frontend assembles the sentence in the
  current language. This extends §30 discipline 5: the backend no longer emits
  pre-translated sentences, so even an already visible banner follows a language switch.
- "Just updated" must answer "is my old data fine": `updated_data_ok` with
  `{from,to,n}` (n = files parsed successfully), or `updated_data_issue` with the number
  of failures. A welcome message alone says nothing.

### Settings page

The About section now shows "Data format: vX (program supports vY)" and a "Run check"
button (`POST /api/selfcheck`). When something is wrong, those two numbers are the only
facts a user can report precisely.

### Policy (in force from 1.2.9 onward)

> **Any feature addition, removal or behaviour change must update both the Chinese
> and the English documentation.**

Scope: `web/static/guide.html` + `guide.en.html`, `README.md` (bilingual single file),
`CHANGELOG.md` + `CHANGELOG.en.md`, `docs/SPEC.md` + `docs/SPEC.en.md`.
Updating only one side leaves half the users with a wrong description — worse than
no description at all.

## 32. Stranger-machine audit (before going public, 2026-10-30)

Criterion: **hand the exe to a player with no connection to you, and their machine must
not be subject to a single assumption that only holds on the author's machine.** Every
item below was checked; the outcome and the fix are listed with it.

### 32.1 "Did you bake my data into the program?" (the user's challenge — must be proven, not asserted)

- **The build contains only the Python standard library and `web/`.** How to verify:
  look at `build/Waass-<version>/PKG-00.toc`; every entry coming from the project
  directory is `web\...`, and there is no `data\` anywhere.
- **The repo does not track runtime data**: the only JSON files in `git ls-files` are
  `web/i18n/*.json`; `data/` is entirely in `.gitignore`.
- **The configuration ships no built-in defaults**: `load_config()` returns an empty
  dict when nothing is on disk, and the game folder, download folders and Script Merger
  location are all detected live.
- **Counter-experiment**: build a fake game folder containing only `modFakeAlpha` and
  `modFakeBeta`, point `WAASS_GAME_DIR` at it and cold start → the API returns exactly
  2 mods. None of the 20 real ones appear, which proves the list comes from scanning.
- **Why copying the exe into an empty folder on the same machine instantly shows every
  mod**: because the game is on that machine, and both detection and scanning happen on
  the spot. That is the design, not leftover data.

### 32.2 Permissions (the most likely thing to break on someone else's PC)

Steam installs the game under `C:/Program Files (x86)` by default, which a normal user
can only read. Previously the first symptom was `PermissionError: [WinError 5]` at the
moment the user clicked install — which is meaningless to a non-technical user.

- `paths.dir_writable(dir)`: opens the directory itself with `CreateFileW` using
  `FILE_ADD_FILE` (plus `FILE_FLAG_BACKUP_SEMANTICS`, without which directories cannot
  be opened at all) and lets Windows evaluate the ACL. **No file is ever created** — the
  game folder is not touched in any way.
- The self-check gained `game_readonly` (amber banner) with `params.path`, raised only
  when writing really is impossible.
- The API layer gained `_err(exc)`: `PermissionError` → `srv.need_admin`,
  `FileNotFoundError` → `srv.file_gone`, both with a stable `code`. **A raw
  `type: str(e)` is never returned to the UI again.**

### 32.3 Everything else that was checked

| Item | Result |
| --- | --- |
| Chinese / space-containing paths | Pass (built `中文 路径\巫师3` under `_work`; scanning works) |
| Clean install with no `mods\` folder | Pass (0 mods, no error) |
| No 7-Zip / WinRAR / Bandizip | `.zip` uses the standard library; `.7z`/`.rar` reports "unsupported" and points at the missing tool |
| Network unavailable | Every `urlopen` has a timeout (4 call sites); Nexus / translation failures only affect their own panel |
| Edge / Chrome outside the standard paths | Detection also probes `%LOCALAPPDATA%`; if none is found it falls back to the system default browser |
| No pywebview | Falls back to a `--app` window; then to the default browser |
| exe in an unwritable location (Program Files) | `data/` automatically moves to `%LOCALAPPDATA%\WaassModManager` |
| Read-only data folder / data from a newer build | Read-only mode, red banner, not a single byte written |

### 32.4 Policy

The pre-release checklist must contain these three lines: **no `data/` in the build**,
**no author-machine paths in source or docs**, **plain language when permission is
denied**.

---

## 33. 1.3.0: bugs that only happen once packaged (2026-10-31)

No new features. This release deals with a class of bug that works perfectly from
source and breaks only inside the exe — where "build succeeded", "all tests green"
and "the exe starts" were all blind to it.

### 33.1 Folder picking must not depend on tkinter

In the 1.2.9 exe, "Browse for folder" returned 500: `No module named 'tkinter'`.
The code was not wrong; a comment in `build.py` — "keep tkinter, the folder picker
needs it" — was never verified. The CPython used for building has no Tcl/Tk, and
PyInstaller only packages what the import graph contains: present from source,
absent in the exe.

The rules now:

* `core/picker.py` opens folders with `IFileOpenDialog` (`CLSID_FileOpenDialog` +
  `FOS_PICKFOLDERS`), calling vtables through plain ctypes — no third-party library;
* order: native dialog → `SHBrowseForFolderW` → tkinter. **"Unavailable" and
  "cancelled" must stay distinct** (`None` vs `""`), otherwise cancelling pops a
  second Tk dialog;
* if none of the three works, `PickError(code="picker_unavailable")` is raised and
  the UI says "paste the path into the field" instead of showing the raw `ImportError`;
* `picker.probe()` creates the object without showing it, for the build check.

### 33.2 Only one writer may advance the data version

`compat.touch()` used to run `setdefault("schema", SCHEMA)` while recording "this
build has run". Consequence: the backup fails → migration correctly stops and warns
→ but the schema is stamped as new → the next launch reports `ok`, the warning
disappears, and the failed migration is never retried.

Rule: **only a successful `prepare()` may change `meta["schema"]`**. `touch()` writes
`app` / `last_seen` and nothing else. On failure `prepare()` returns early without
touching meta, so the next launch retries the backup and warns again. Regression
test: `tests/test_compat.py` section [10].

### 33.3 Ask the exe what it can do

After packaging, `build.py` runs
`Waass-x.y.z.exe --check --check-out build/execheck.json`:

| Check | Failure code |
| --- | --- |
| UI assets (`templates/index.html`, `app.js`, `style.css`, `settings.js`, both `i18n` files) | `web_assets` |
| The 8 core modules really import | `modules_import_failed` |
| Picker backend (`probe()`) | `no_picker_backend` |

The verdict goes to a file (a `--windowed` exe has no stdout); a non-zero exit code
fails the build. `--check` does not start the server, open a window or take the
single-instance mutex.

### 33.4 Two smaller closures

* **Unsupported methods return JSON 405** with `Allow: GET, POST`. The default
  implementation answers 501 with an HTML page, and the UI only parses JSON — that
  turns "wrong method" into "parse error".
* **Full scans are serialised**: `State.refresh()` queues on `_scan_lock` and
  de-duplicates via `_scan_gen` — if another scan finished while this caller was
  waiting, its result is reused. The disk is not read twice and `scan_result` cannot
  be overwritten mid-flight.

### 33.5 Console encoding is another "only breaks on another machine"

The first real CI run (an English `windows-latest` image) turned 20+ of 28 test
suites red at once, all with `UnicodeEncodeError: 'charmap' codec can't encode ...`.
No logic was wrong: Windows consoles default to the local code page, cp1252 on an
English system, which cannot encode Chinese.

The development machine is a Chinese-locale system (cp936), so this class of error
is **never visible locally** — and it sits on the startup path (plain `print`), so
an English-Windows user running from source dies on the first log line.

The rule (`core/console.py`, invoked once by `core/__init__.py` on import):

* switch the console code page to 65001 first (`SetConsoleOutputCP` / `SetConsoleCP`);
* then reconfigure `stdout/stderr/stdin` as `utf-8` with `errors="replace"` — on a
  terminal that still can't cope, one character becomes a question mark instead of
  taking the process down;
* with no console (a `--windowed` exe) it does nothing and leaves the devnull
  streams from `_silence_streams()` alone.

One more gap: `tools/coldstart.py`, `build.py` and `tools/i18n_merge.py` print
**before** importing core, so the package entry point hasn't run yet. Each of
them calls it explicitly — `core.console.enable_utf8()` where core is imported,
a bare `sys.stdout.reconfigure` in scripts that stand alone.

It lives in the package entry point rather than being called at each entry point:
`run.py`, `tests/*` and `tools/*` all go through `core`, and relying on people to
remember eventually fails (`test_lifecycle` was the one file that imports no core
module; it now calls it explicitly).

Verified by `tests/test_console.py`, which spawns subprocesses under cp1252 for four
contrasts (crashes without the fix / prints with it / `import core` alone suffices /
repeated calls are harmless), plus a pinned CI step running `test_console`,
`test_lifecycle` and `run.py --check` with `PYTHONIOENCODING=cp1252`.

## 34. 1.3.1: program update checks (2026-10-31)

One check ten seconds after launch, results cached for a day. The parts that are easy
to get wrong:

* **Cache file** `data/update.json`: `{latest, url, notes, etag, ts, checked_at}`.
  `ts` is the last time the network was actually used, so anything within 24 hours is
  served from cache; `etag` drives the conditional request.
* **The verdict is never cached.** `newer` is recomputed from `track.compare` every
  time — after the app itself is upgraded, the cached `latest` may no longer be newer,
  and a stored verdict would keep the banner alive forever.
* **304 does not mean "no updates"**, it means the body is unchanged. Reuse the cache
  and refresh `ts`; never parse a 304 body (it is empty).
* **Two failure modes.** An automatic check (`force=False`) falls back to the previous
  result and marks it `stale`, saying nothing in the UI; a manual check (`force=True`)
  must return `ok=False` plus a plain-language message — the user pressed a button and
  is waiting, and "you are up to date" while offline is a lie.
* **No prompt when the published version is older than the local one**: `compare`
  returning -1 leaves `newer=False`, so downgrades and test builds are not misreported.
* The frontend calls `setTimeout(checkAppUpdate, 10000)` in `app.js` without awaiting,
  so the first screen is never blocked; the manual check in Settings sends
  `force: true` and renders that release's notes.
* `tests/test_appinfo.py` uses a local HTTP server to verify: first network call,
  no repeat within 24 hours, manual checks bypassing the cache, `If-None-Match` once
  stale, an unchanged result on 304, no prompt for an older published version, both
  failure modes, and no traffic at all once the check is disabled.

## 35. 1.3.2: outbound traffic list (2026-10-31)

The program has exactly three outbound points. This table is the single source of
truth; change any line and update it here.

| Point | Trigger | Default | What is sent | On failure | How to turn it off |
|---|---|---|---|---|---|
| Nexus metadata | installing a mod / topping up after launch / manual refresh | on | a list of mod IDs (read-only query) | silent; entries show *unrecorded* | in Settings, or go offline |
| Program update check | ten seconds after launch, and on manual check | on | nothing (reads the version number from GitHub Releases) | automatic check stays silent; a manual check reports the reason | `update_check: false` (config only; there is no switch in the UI) |
| Localization translation | the user starts a translation | consent required once | the menu text being translated | a failed string moves to the next engine; if all fail, it is reported | turn off every engine, or decline consent |

Nothing else exists: no telemetry, no account system, no usage data collected, no crash
reports. That is not a promise, it is the current state — none of it is in the code.

Two details:

- **Every outbound request identifies itself honestly.** Translation sends
  `Waass/<APP_VERSION>`; metadata sends `WaassModManager/<APP_VERSION>` together with
  `Application-Name` / `Application-Version`. The version number comes only from
  `core/appinfo.py`, never written out again elsewhere. Before 1.3.2 translation had
  `Waass/1.2` hard-coded and metadata posed as a browser; both are gone.
- **"Off" means the whole path is off**, not just the notice in the UI: no requests are
  made at all, that feature degrades to local-only, and everything else keeps working.
  With no network, all three are absent and nothing else changes.

Section 9 (metadata), section 21 (translation) and this section must agree. The public
counterpart is `docs/network-access.md`: different reader, same statements.
