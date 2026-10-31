# Nexus page copy (English)

Intro text for the Nexus Mods page. **Plain text throughout, no markup of any kind** — paste it into the Nexus Description editor, GitHub, or anywhere else and it looks exactly like this, with no stray [b] or ## showing. To add emphasis, select the key phrases in Nexus' editor and hit bold manually.

---

Waass Mod Manager

A mod manager for The Witcher 3 Remastered, **built for Chinese players**: developed in a Chinese-language environment, with a feature most managers don't have — CN localization of mods (language tables, scripts and menu XML, all three handled). The whole interface is also available in English, so English-speaking players can use it too.

A single 9 MB exe, double-click and it works. No installation, no .NET, no other manager required.

· WHAT IT IS

Mod Organizer and RMMR can both install Witcher 3 mods, but this game has a quirk: what actually decides "what takes effect" is three things in the mods folder — the folder names, the priorities in mods.settings, and whether Script Merger has merged it. Most managers treat those three as a black box, and when something breaks all you can do is stare at the game log and guess.

Waass lays all of it out in front of you: which mods are active, who overrides whom, which function two mods are both hooking, which menu file is actually no longer in use. It shows you the plan before writing, and lets you step back through every change afterwards.

· WHAT IT DOES

Enable / disable. Disabling just adds a ~ to the folder name (modHoods becomes ~modHoods). The game engine only scans folders starting with "mod" — it never sees anything starting with ~. Same as not installed, but not a single file moved. The upside: mods.settings, a file with an unforgiving format, is never touched, and the load order stays exactly as it was — one click to switch back anytime.

Install packages. Drop in zip / 7z / rar / tar-based archives and see a preview first: which files will be written to the game folder, which will overwrite existing ones. Nothing is written until you confirm. Packages that want to put DLLs into bin are stopped and asked about separately. Uninstalling never truly deletes — files go to quarantine, and can be brought back anytime.

Load order. One rule: a smaller priority number wins when two mods ship the same file. Drag rows directly in the list, or open the load order panel and set everything in one pass.

Conflict checks. Only active mods are examined. At the script level it distinguishes @wrapMethod (usually coexists fine — notice only) from @replaceMethod (the latter replaces the former entirely — warning); at the resource level it only reports UI file collisions — texture and mesh overlaps are normal and not reported. Groups already merged by Script Merger are recognized and marked green instead of being flagged as conflicts again.

Check for mod updates. No Nexus API key, no login. The program infers each mod's Nexus ID from local evidence first (what it remembered before, its own install records, the original archive names in your download folder, links in mod readmes). When it can't be sure, it leaves the field blank for you to pick — it never guesses. A wrong ID means "check for updates" forever compares you against someone else's version.

Menu translation. Mod text can live in three places: language tables, strings hardcoded in scripts, and menu XML files. The program tells you which category still has untranslated text, translates it through public translation engines, and writes back with same-length replacements (the translation can never exceed the original's size; anything that doesn't fit is reported honestly as "N bytes over", never truncated). Defaults to menu pages only — mod names and item names are left alone, since changing those tends to break matching against scripts and save games. Original files are kept permanently, with one-click restore. (Built primarily for non-English translations; ignore it if you don't need it.)

Save game backups. A full backup runs automatically before you install mods, and you can take incrementals afterwards. Backups live in the program's own data directory, not in the game folder.

· WHAT IT NEVER TOUCHES

Scanning is read-only throughout. The write gate refuses everything by default and only opens four locations: mods/, dlc/, the menu config directory, and the single mods.settings file. Affected files are snapshotted to data/snapshots/ before every write; writes are refused outright while the game is running (witcher3.exe detected); and every write goes onto the undo stack.

The background server listens on 127.0.0.1 only, the UI and backend authenticate with a one-time token, and the Host header is validated with cross-site requests refused.

· SYSTEM REQUIREMENTS

Windows 10 / 11 (64-bit). The Witcher 3 Remastered v5.x, Steam / GOG / Epic all accepted. The UI uses the bundled Microsoft Edge or an installed Chrome — no separate browser needed.

· GETTING STARTED

1. Download the exe and double-click it. Windows may warn about an "unknown publisher" — normal for unsigned programs. Click "More info → Run anyway".
2. The program finds your game folder on its own, from Steam's library index, GOG's registry, or Epic's install manifests. If it can't, it tells you which level to select — the folder that contains both bin and witcher3.exe.
3. The first scan fills in covers and authors, takes under a minute. Settings → Guide has a walkthrough organized by feature; start there whenever something is unclear.

· A NOTE IN CLOSING

This program will not decide which mods you should install, and it will not "optimize" your game automatically. What it does is put every change on the table: what was changed, what it looked like before, and how to go back.

· LICENSE

MIT. This is an open-source project, and most of its code was built with AI. Anyone is welcome to download it, change it, share it, or localize it into another language — the UI strings are just two JSON files, so adding one more makes a new language. For significant bugs, please open an issue on GitHub; I keep improving it.

---

(The text above is the page body. What follows is a note to self — do not paste into Nexus.)

Suggested images: main window docs/screenshots/1.3.0/01-main-zh.png,
English UI docs/screenshots/1.3.0/02-main-en-light.png,
guide docs/screenshots/1.3.0/04-guide-en.png.
Nexus' Description editor takes BBCode; reference images with [img] using image-host URLs —
upload each image to Nexus' Images section first, then copy the link.

GitHub does not use this file: README.md (and README.en.md) is the public documentation.
This one is for "short" and "render-independent" — plain text that survives pasting anywhere without deforming.
