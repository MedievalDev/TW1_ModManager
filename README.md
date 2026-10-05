# TW1 Mod Manager

Installs, enables and disables mods of **Two Worlds 1** (2007) — the `.wd`
archives in the `Mods` folder and their switches in the Windows registry.
Drop a `.wd` onto the window, answer one question, play.

![License: CC0](https://img.shields.io/badge/license-CC0-green) ![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey)

## What it does

- **One list** of every archive in `Mods\` with its registry switch, size and
  date. Double-click or Enter toggles. Archives in the game folder itself
  (which always load) are shown in red.
- **Drag and drop:** drag a `.wd` from Explorer anywhere onto the window. A
  dialog shows what is inside (file count, top folders, a few paths) and
  asks: *Install and enable*, *Install only*, *Cancel*. Same dialog for
  Ctrl+O and for a `.wd` passed on the command line.
- **Nothing is deleted:** disabling sets the registry DWORD to 0, removing
  moves the archive to `Mods\_removed\`, replacing keeps a `.backup`.
- **Verified mods from the community server** (`mods.json` on
  alchemy-fox.de): download, SHA-256 check, enable.
- **Community archive on GitHub:** the folder `Two Worlds` of
  [InsideTwoWorlds/MODs](https://github.com/InsideTwoWorlds/MODs/tree/main/Two%20Worlds)
  as a third tab — every `.wd` with its description, download checked
  against the git blob hash GitHub keeps for the file.
- **Guide inside the tool** (F1): chapters, search, registry reference. Gold
  `?` marks open the matching chapter. Tour on first start.
- **Self-update from GitHub:** check on start (switchable), SHA-256
  verified download, swap after the tool has closed.
- Dark theme, DE · EN switch, same look as the other TW1 tools.

## The registry model

The game reads `HKCU\SOFTWARE\Reality Pump\TwoWorlds\Mods` at start. Every
value is an archive name with DWORD `1` (load) or `0` (do not load). An
archive in `Mods\` **without** a value is loaded — `0` is a deliberate off
switch, not a default. A `.wd` next to `TwoWorlds.exe` loads no matter what.
Registry logic after buglord's Mod Selector.

## Drag and drop without extra packages

Windows sends `WM_DROPFILES` to a window that called `DragAcceptFiles`. The
tool subclasses the window procedure of the toplevel and of every child
HWND (Tk gives each widget its own on Windows) with plain `ctypes`, reads
the paths with `DragQueryFileW` and hands them to the Tk thread. No
`tkinterdnd2`. Drops from a normal Explorer onto an elevated tool are
blocked by Windows — run the tool without administrator rights.

## Files

| File | Purpose |
|---|---|
| `mod_manager.py` | the tool (`--` a `.wd` path as argument opens the install dialog) |
| `guidebook.py` | guide window (F1), chapters DE/EN, reference tables from the code |
| `modscan.py` | what a mod changes against the game, compare of two mods (green / yellow / red) |
| `merger.py` + `mergeui.py` | the merger and its tab, conflict dialog, report |
| `levelcache.py` + `tw1_lhc.py` | level header cache without the SDK |
| `tw1_par.py`, `tw1_lan.py`, `tw1_lnd.py`, `lndmap.py`, `tw1_wd.py` | format modules shared with the PAR Editor and the Quest Creator |
| `fieldnames.py` + `tw1_sdk_fields.json` | SDK names of the par fields |
| `tests/` | `python -m unittest discover -s tests -t .` (real mods from `Desktop\modsTW1` when present) |
| `updater.py` + `version.py` | update check and self-update from GitHub Releases |
| `theme.py` | dark theme shared by the TW1 tools |
| `build_mod_manager_exe.bat` | PyInstaller one-file build (`dist\TW1_Mod_Manager.exe`) |
| `selftest_exe.bat` | starts the exe in selftest mode (`MOD_MANAGER_SELFTEST=<file>`) |

Config: `mod_manager_settings.json` next to the script; as exe under
`%LOCALAPPDATA%\TW1ModManager\`.

## Download

Latest exe: `https://github.com/MedievalDev/TW1_ModManager/releases/latest/download/TW1_Mod_Manager.exe`

## Credits

- Registry logic: [buglord's Mod Selector](https://github.com/buglord/Two-Worlds-1-Misc-Projects/tree/main/Mod%20Selector)
- Predecessor: the Mod Manager module of the TW1 Dialog & Quest Creator (2026-07)

## License

CC0 — do what you want with it.

## Changelog

### v2.4.0 (05.10.2026)

- **Search row in all three list tabs** (Installed mods, My Mods, Community):
  live filter while you type, suggestions under the entry (arrow keys, Enter
  or Tab to take one, Esc to close), Ctrl+F jumps into the row. On the
  server tab the search also reads description, author and tags, on the
  community tab the descriptions (loaded in the background).
- **Texts in the side panel can be selected and copied** (Ctrl+C or
  right-click), **web links are clickable**.
- **Install button and language switch in the side panel**, under the
  picture (slimmer buttons). Mods with two language files get a DE / EN
  switch that also changes description and readme.
- **My Mods sorted by name; merged mods sit under the mod they build on**
  (unfold SpellRework to see "Spell Elite Revamp (merged mod)").
- **Window 25 % higher** (825 px) so the lists read better.

### v2.3.1 (05.10.2026)

- **Sizes under 1 MB show in KB** (before: "0.0 MB" for a 40 KB mod).
- **Side panel in the Community (GitHub) tab** too: name, the description
  from the `.txt` next to the archive, a picture if the archive has one
  (otherwise the Two Worlds logo).

### v2.3.0 (05.10.2026)

- **Side panel in "My Mods (server)".** Click a mod: picture gallery with
  arrows, name, description, the readme to unfold, tags and credits.
- **Language variants.** One entry can carry several language files; a
  language box next to "Install / update" picks one (default: the tool
  language). The other version is switched off, it replaces the same files.
- **Merged mods group.** Mods that bundle other mods (with their credits)
  get their own section in the list.
- `mods.json` stays readable for older versions: entries without
  `variants` work as before.

### v2.2.2 (22.09.2026)

- **No overlap limit in the merger.** Before, more than 40 overlaps turned a
  merge red and the choice window never opened - the main mod simply won
  everything. Now red means only one thing: both mods change compiled
  scripts, which cannot be mixed. Every other overlap is asked in the choice
  window, however many there are; "Give all to" settles the bulk in one
  click, then you switch the exceptions. Scripts stay with the main mod.
- **Errors say what helps.** Every error window shows a short tip, a button
  to the matching guide chapter and "Report a bug".
- The guide chapter "Merging mods" is now a full step-by-step walk-through,
  including the choice window, the report and how to undo a merge.

### v2.2.0 (19.09.2026)

- **What a mod changes.** Rest the mouse on a row: kinds of files, number of
  changed par fields, quest blocks, texts, and the map tiles. Measured
  against the game's own files, so "changed" means changed by the mod.
- **Compatibility colours.** Click a mod: the other rows turn green (no
  overlap inside files), yellow (up to 40 overlaps, or shared maps / whole
  files) or red (more, or compiled scripts both change).
- **Merge mods (experimental).** New tab: tick mods, name the result, merge.
  par per field, quest file per block, `.lan` per key, alias and dialog
  tree; maps per tile with `.lnd` + `.phx` always from the same mod, quest
  markers of the other mod carried over onto the chosen ground, lost game
  markers put back; a fresh level header cache inside the new mod. Yellow
  asks per overlap (with the SDK field name, both values and the game's
  value), red needs a main mod that wins every clash. Sources are only
  read, an existing name is refused, the new mod arrives switched off.
  A report of every merge is kept under `merges` in the data folder.
- **Drag and drop fixed.** A real drop from Explorer ended the tool without
  a message: the window procedure called Tk while Windows was dispatching
  the drop. It now only collects the paths and Tk picks them up from its own
  event loop (measured and fixed on the WD Packer, 19.09.2026).
- Rows without a registry switch are shown as enabled - that is what the
  game does.
- **Help testing.** Help > *Test what is untested* lists what is measured
  but not yet confirmed in the game (merging, map merging, level cache,
  colours, dropping with the mouse): steps to tick, what it must look like,
  *Start* launches the game and notes what is visible from outside, then
  *Works* / *Does not work*. Two confirmations and the test is passed for
  everyone, "experimental" disappears from the merge tab. Help > *Report a
  bug* and *Known issues*; every error window has *Report a bug*. A preview
  shows exactly what is sent - no paths, no user names, nothing without the
  button. Server: alchemy-fox.de/game/_feedback.
- **Level cache follows the mods.** The game reads the markers of every map
  from `Levels\Map_LevelHeaders.lhc`. A mod with maps needs a cache that
  knows them, and a cache built while a map mod was installed keeps its maps
  after the mod is gone. Until now that meant running the SDK's
  `LevelHeadersCacheGen.bat` by hand. The manager rebuilds the cache after
  every change of the mod list (enable, disable, install, remove), without
  the SDK: `tw1_lhc.py` writes the same bytes - measured byte-identical to
  the SDK exe's output for the game plus a map mod, 0.15 s. The old cache
  goes to the tool's backup folder. File > *Rebuild level cache now*, and a
  switch to turn the automatic off. Maps that two enabled mods bring are
  reported; which one the game takes is not measured.
- Format of the cache: `"LC\0\0"`, u32 map count, u32 0, then per map
  (sorted by path) u32 path length, path, and the map body up to the end of
  the marker block.
- Map reader: the tail of a marker is a length-prefixed text plus a u32,
  not 8 fixed bytes (Dream Worlds' Map_E03 carries a text there).

### v2.1.0 (16.09.2026)

- Third tab **Community (GitHub)**: the mods of `InsideTwoWorlds/MODs`
  (folder `Two Worlds`, 40+ archives) with size, description from the
  `.wd.txt` next to each file, "installed / update available / not
  installed" by comparing the git blob SHA-1, install or update with one
  click or a double-click. Downloads are verified against GitHub's hash.
- One download routine for both lists (`download_file`).

### v2.0.0 (16.09.2026)

- Standalone tool (was a module of the Quest Creator). Drag and drop of
  `.wd` files with an install dialog. Guide window, `?` marks, tour, DE/EN,
  self-update, selftest, status bar always visible, context menu with "Show
  in Explorer", Enter/Del in the list, stale "file missing" rows can be
  removed.
