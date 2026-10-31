# Changelog

User-visible changes in each release. Installers and checksums are on [Releases](https://github.com/AaronFley/Waass-Mod-Manager/releases).
Chinese version: [CHANGELOG.md](CHANGELOG.md).

The version number is maintained in one place, `core/appinfo.py`.

## 1.3.0 — 2026-10-31

Data format remains v3, so a 1.2.9 `data/` folder carries over unchanged. Replace the executable to upgrade.

**Fixed**

- Folder picking was unavailable in the packaged build: clicking **Browse** returned 500. Folder selection now uses the native Windows file dialog, with an address bar and search, and no longer depends on Tk. When the dialog is unavailable, the UI asks you to paste a path instead.
- A failed data migration was recorded as completed, so later launches neither warned nor retried. Only a successful migration advances the data format version now.

**Added**

- Build-time capability check: after packaging, `Waass.exe --check` verifies UI assets, core modules, and the dialog backend, and fails the build if anything is missing.

**Improved**

- Update checks target this repository's releases by default, with no configuration required. The address can still be replaced or the check disabled in `config.json`.
- Unsupported HTTP methods now return a JSON 405 with an `Allow` header instead of an HTML error page.
- Startup scanning and a manual rescan now run serially, avoiding duplicate disk reads and overlapping results.
- Console output is standardized on UTF-8, so a non-Chinese code page no longer interrupts startup on a log line.
- Documentation screenshots refreshed for 1.3.0 (Chinese and English sets); GitHub Actions CI added.

## 1.2.9 — 2026-10-30

**Upgrading is now just swapping the executable.** This release adds data compatibility and a startup self-check, so cross-version upgrades no longer rely on luck.

**Added**

- Data compatibility layer: `data/meta.json` records the data format version (currently v3). Older data is copied in full to `data/_backup/pre-schema<old>-<timestamp>/` before migration, then migrated and registered. Migration is idempotent and resumes after an interruption.
- Corrupted data files are renamed to `xxx.json.broken-<timestamp>` and kept; the app skips them and continues rather than deleting anything.
- Downgrade protection: if the data was written by a newer version, the older version switches to read-only, with the reason shown at the top and no way to dismiss it.
- Startup self-check: version change, data format, record file readability, data directory writability, and update source validity. Results are stored in `data/selfcheck.json` (latest 10 runs).
- Top status bar: green for a fresh upgrade or completed migration, yellow for quarantined files or a missing game folder, red for unreadable data or read-only mode.
- An explicit answer after a version change — how many records are intact, or which ones are not — instead of a welcome message alone.
- Game folder write-permission pre-check, prompting for administrator rights at startup rather than failing during installation.
- **Settings → About** now shows the data format version and offers a re-check.

**Improved**

- Permission and missing-file errors are reported in plain language rather than raw system exceptions.
- Browser detection now covers Edge and Chrome installed per user.
- The update panel builds its text from status codes, and no longer opens a chromeless window in app mode.
- External-link allowlist extended to `github.com`.
- The English UI now says *The Witcher 3 Remastered*, matching the Chinese UI.

**Documentation**

- From this release on, any feature or behavior change must update both the Chinese and English documentation (in-app guide, README, CHANGELOG, SPEC).

## 1.2.8 — 2026-10-30

**English interface.** Switch from the top-right corner; the first launch follows the system language (Chinese on Chinese systems, English otherwise).

**Added**

- Language button: `EN` in the Chinese UI, 「中」 in the English UI — the label shows what you will get.
- Three-state language setting: `auto` (follow system, default), `zh`, `en`. Systems in other languages default to English.
- Full bilingual coverage of interface text, including the main window, install preview, localization panel, conflict checks, update tracking, settings, load order, and batch selection. Backend messages use the same string table.
- English documentation: `guide.en.html`, `README.en.md`, `CHANGELOG.en.md`, `SPEC.en.md`.

**Fixed**

- Unresponsive UI after switching languages: on a full reload, the old page's exit signal could arrive after the new page loaded, so the service shut down while the browser window stayed open. Each page now carries an identifier and late exit signals are discarded.
- Three frontend checks compared translated text against backend responses (install warning deduplication, restore drift detection, update panel highlight), which silently failed in English. The backend now returns stable status codes.
- The browser offered to translate the English page; this is now suppressed by a page declaration.

## 1.2.7 — 2026-10-30

**Fixed: the process stayed alive after the window closed.**

- Exit detection no longer relies only on the page-close signal. It also checks whether the window still exists: 15 seconds after page load, three consecutive misses with no requests in the last 8 seconds trigger shutdown.
- The 8-second quiet period is kept so window redraws or minimization are not mistaken for a closed window.
- This check applies only to the app's own window, not to a regular browser tab, where a title change could cause a false exit.
- If the app window loads no page within 60 seconds, it exits as a failed start.
- A 3-second forced-exit timer runs before cleanup, so cleanup cannot block shutdown.
- The frontend sends a heartbeat every 15 seconds, distinguishing "open but idle" from "closed".
- The exit reason is written to `data/lifecycle.log` (latest 8 KB).

## 1.2.6 — 2026-10-30

**Mods can now identify their own Nexus pages.**

**Added**

- Nexus ID detection ordered by evidence strength: manual entry > local records > install history > original archive name in the download folder > links in the mod's README.
- Uncertain matches are not bound automatically; candidates are listed under "missing ID" and bind on click. Candidates are withheld when the name differs substantially or several same-name versions exist.
- "Not on Nexus" flag for self-made or ported mods, which then stop appearing in prompts.
- Script Merger onboarding: a download link when it is not installed, and manual selection of its executable when installed elsewhere.
- Built-in guide (**Settings → Guide**) explaining the reasoning behind each behavior and common problems.

**Improved**

- Disabling now renames the folder (`modHoods` → `~modHoods`) instead of writing `mods.settings`; load order is preserved and one step restores it.
- If the config still marks the mod as disabled when enabling, it is synchronized automatically so the game loads it.
- The top bar shows "updates available" and "missing Nexus ID" side by side instead of one hiding the other.

**Fixed**

- Covers, authors, and update checks failing after removing an older manager: ID sources expanded from 2 to 5, and detected IDs are stored locally right away.
- "Refresh metadata" reporting everything up to date when no IDs existed.
- Startup synchronization being skipped entirely when no IDs were present.
- Candidates not appearing for disabled mods, due to mismatched keys between server and UI.
- Long mod names returning no candidates: search terms now relax through "all words → without wrapper words → first two words → first word".
- Candidate buttons collapsing into a vertical stack with truncated names.

## 1.2.5 — 2026-10-30

First public release. The focus was on making the app usable for someone else's machine.

**Added**

- Game folder detection via Steam library manifests (multiple drives and libraries), the GOG registry, and Epic install manifests, with a fallback to common default paths.
- Proper onboarding when no game is found, with "Select game folder" and "Search again" actions.
- A pre-release check script that runs every read-only endpoint against an empty data folder and a stub game folder, including a no-game environment.

**Fixed**

- Reordering wrote a config section for a mod that no longer existed; the folder is now verified first.
- Author names were not escaped in the install preview.
- White flash on the first frame in dark theme.
- Secondary text contrast below WCAG AA.
- The version in the response header did not follow the app version.

**Security**

- `Host` header validation and rejection of cross-site requests, preventing token theft through DNS rebinding.
- Path traversal in `/api/open-folder`.

**Removed**

- Leftover code and tests from the retired "Chinese name" feature.
- The "install behavior" option in Settings.

## 1.2.4 — 2026-10-29

- Local version is now the highest among all sources, fixing "update available" after updating.
- "Name + version" lines in language tables are no longer reported as untranslated.
- The sidebar shows the game version, read from a literal inside the executable.
- Nexus archive names are recognized with both underscore and space separators.

## 1.2.3 — 2026-10-29

- New visual identity: gold W mark on dark grey, with matching icons, taskbar icon, favicon, and executable icon.
- Dark theme by default on first launch; light / dark / follow-system remain available.
- List view flattened into rows; informational conflict entries no longer highlighted as a block.
- Toggles use a muted green in dark theme to reduce visual noise.

## 1.2.2 — 2026-10-28

- The "N updates available" bar can be dismissed for the current set and returns when a new version appears.
- Disabled mods sort to the end of the list (display only; load order is unchanged).
- New icon for "follow system" theme mode.

## 1.2.1 — 2026-10-28

- Localization detection recognizes hand-edited menu files, avoiding duplicate reports.
- Nexus shortcut added to the top bar.
- A background synchronization runs after startup and reports only when there is a result.
- Removed the "Chinese name" and "Installed packages" entries.

## 1.2.0 — 2026-10-28

- Localization reduced to one click: select mods → translate → review in a table → write once.
- Keyless translation services by default, with support for your own rotating keys.
- Menu pages only by default; mod and item names are left untouched.
- Originals are backed up before writing, with whole-batch or single-batch restore.
- Writes preserve file length; oversized translations are shortened or reported, never truncated.

## 1.1.1 — 2026-10-28

- Conflict detection extended to three layers: duplicate scripts, identical interface files inside resource bundles, and functions annotated by several mods.
- Added checks for legacy script encodings and outdated Chinese language tables.
- Only enabled mods are checked; asset collisions are not reported.
- Batch selection added.

## 1.1.0 — 2026-10-28

- The app is read-only while the game is running, with the reason shown.
- Uninstall with unrestored config now explains the situation and offers one-click undo.
- Closing the window with unsaved load-order changes prompts first.
- Visual alignment with Windows 11.

## 1.0.10 — 2026-10-27

- The merged output `mod0000_MergedFiles` must be registered in `mods.settings` with the highest priority; every write path now verifies this.
- **Status → Merged** shows which scripts were merged, from which mods, and whether they are registered correctly.

## 1.0.9 — 2026-10-26

- "Latest version" now reads the Nexus main file version instead of the display version on the page.
