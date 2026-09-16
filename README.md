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

### v2.0.0 (16.09.2026)

- Standalone tool (was a module of the Quest Creator). Drag and drop of
  `.wd` files with an install dialog. Guide window, `?` marks, tour, DE/EN,
  self-update, selftest, status bar always visible, context menu with "Show
  in Explorer", Enter/Del in the list, stale "file missing" rows can be
  removed.
