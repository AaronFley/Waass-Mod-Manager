# Nexus page copy (English)

Intro text for the Nexus Mods page. **The body is plain text with no markup at all** — paste it into the Nexus Description editor, GitHub, or any text box and it stays exactly as written, with no stray [b] or ## showing. To add emphasis, select the key phrases in the Nexus editor and apply bold manually.

---

Waass Mod Manager

A mod manager for The Witcher 3: Wild Hunt — Remastered, built with Chinese players in mind: developed in a Chinese-language environment, with a mod localization feature no comparable tool offers (language tables, scripts, and menu XML), while shipping a complete English interface for English-speaking players.

A single 9 MB executable. Double-click and it works. No installation, no .NET, no other mod manager.

· WHAT IT SOLVES

In The Witcher 3, what actually decides which mod wins comes down to three things: the folder name under mods, the priority recorded in mods.settings, and whether scripts have been merged by Script Merger. Most tools treat these as a black box, leaving you to guess from the game log when something breaks.

Waass puts them in plain view: which mods are active, which one loads first, which function is rewritten by two mods at once, and which menu file is no longer in use. It shows you a list before changing anything, and lets you step back through every change afterwards.

· FEATURES

Enable / disable. Disabling adds a ~ to the folder name (modHoods becomes ~modHoods). The engine only scans folders starting with "mod", so anything starting with ~ is invisible to it — the same as not installed, without moving a single file. The upside: the unforgiving mods.settings file is never touched, load order stays exactly as it was, and one step switches it back.

Installing. Drop in any of 8 archive formats including zip, 7z, and rar, and review a preview first: which files will be written to the game folder, which will overwrite existing ones. Nothing is written until you confirm. Packages that place DLLs into bin are stopped and asked about separately. Uninstalling never deletes: files go to a quarantine folder and can be restored at any time.

Load order. One rule: a smaller number loads first and wins a conflict. Drag rows directly, or set everything at once in the load order panel.

Conflict checks. Only enabled mods are examined. At the script level it distinguishes @wrapMethod (usually coexists, notice only) from @replaceMethod (replaces the former entirely, warning); at the resource level it reports only interface file collisions, since texture and mesh overlaps are normal. Groups already merged by Script Merger are recognized and marked green instead of being flagged again.

Mod update checks. No Nexus API key, no login. The program infers each mod's Nexus ID from local evidence first (local records, install history, original archive names in your download folder, links in mod readmes). When it cannot be certain, it leaves the field blank for you to choose — it never guesses, because a wrong ID makes version comparison point at someone else's mod indefinitely.

Localization. Mod text can live in three places: language tables, strings embedded in scripts, and menu XML. The program reports which of them still contains untranslated text, translates it, and writes back with same-length replacements (a translation can never exceed the original's size; anything that does not fit is reported as "N bytes over", never truncated). Menu pages only by default — mod names and item names are left alone. Original files are kept permanently, with one-click restore. Built primarily for Chinese localization; ignore it if you do not need it.

Save backups. A full backup runs automatically before installing mods, and incremental backups are available afterwards. Backups live in the program's data directory, not in the game folder.

· WHAT IT NEVER TOUCHES

Scanning is read-only throughout. The write gate refuses everything by default and opens only four locations: mods/, dlc/, the menu config directory, and the single mods.settings file. Affected files are snapshotted to data/snapshots/ before every write; writes are refused while witcher3.exe is running; and every write goes onto the undo stack.

The local service listens on 127.0.0.1 only, the UI and backend authenticate with a one-time token, and the Host header is validated with cross-site requests refused.

· SYSTEM REQUIREMENTS

Windows 10 / 11 (64-bit). The Witcher 3: Wild Hunt — Remastered v5.x, Steam / GOG / Epic all work. The interface uses the bundled Microsoft Edge or an installed Chrome — no separate browser needed.

· GETTING STARTED

1. Download the executable and double-click it. Windows may warn about an unknown publisher, which is normal for unsigned programs: choose "More info → Run anyway".
2. The program finds the game folder through Steam library manifests, the GOG registry, and Epic install manifests. If it cannot, select the folder that contains both bin and witcher3.exe.
3. The first scan fills in covers and authors, usually in under a minute. Settings → Guide has a walkthrough organized by feature.

· A NOTE IN CLOSING

This program will not decide which mods you should install, and it will not "optimize" your game automatically. What it does is put every change on the table: what changed, what it looked like before, and how to go back.

· LICENSE

MIT. This is an open-source project, and most of its code was built with AI. Anyone is welcome to download it, change it, share it, or localize it into another language — the interface strings are two JSON files, and adding one more adds a language. For significant bugs, please open an issue on GitHub; I keep improving the project.

---

(The text above is the page body. What follows is a note to self — do not paste into Nexus.)

Suggested images: main window docs/screenshots/1.3.0/01-main-zh.png, English UI docs/screenshots/1.3.0/02-main-en-light.png, guide docs/screenshots/1.3.0/04-guide-en.png.

The Nexus Description editor accepts BBCode; reference images with [img] using image-host URLs — upload each image to the Nexus Images section first, then copy the link.

GitHub uses README.md, which carries both languages in one file (English first), rather than this file. This one is meant to be short and render-independent: plain text that survives pasting anywhere without deforming.
