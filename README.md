# TW1 Mod Manager (Lite source)

Installs, enables and disables mods of **Two Worlds 1** (2007) - the `.wd`
archives in the `Mods` folder and their switches in the Windows registry.
Drop a `.wd` onto the window, answer one question, play.

![License: CC0](https://img.shields.io/badge/license-CC0-green) ![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey)

**Ready-made exe:** [latest release](https://github.com/MedievalDev/TW1_ModManager/releases/latest) - `TW1_Mod_Manager.exe` (Windows, no install needed).

The code in this repository is the **Lite edition**: the "Installed mods" tab
with install, enable, disable, remove, packages and TWSE. It has no server
mod list, no community list, no merging and no update check. The exe in the
release is the full build and is not made from this code.

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
- **Packages (.zip)** with a `.wd` and Script Extender plugins, and a TWSE installer.
- **Guide inside the tool** (F1): chapters, search, registry reference. Tour on first start.
- Dark theme, DE / EN switch.

## The registry model

The game reads `HKCU\SOFTWARE\Reality Pump\TwoWorlds\Mods` at start. Every
value is an archive name with DWORD `1` (load) or `0` (do not load). An
archive in `Mods\` **without** a value is loaded - `0` is a deliberate off
switch, not a default. A `.wd` next to `TwoWorlds.exe` loads no matter what.
Registry logic after buglord's Mod Selector.

## Drag and drop without extra packages

Windows sends `WM_DROPFILES` to a window that called `DragAcceptFiles`. The
tool subclasses the window procedure of the toplevel and of every child
HWND (Tk gives each widget its own on Windows) with plain `ctypes`, reads
the paths with `DragQueryFileW` and hands them to the Tk thread. No
`tkinterdnd2`. Drops from a normal Explorer onto an elevated tool are
blocked by Windows — run the tool without administrator rights.

## Packages (.zip), plugins and TWSE

Some mods consist of several files: a `.wd` and a plugin for the Script
Extender (`.dll` with an `.ini`). Such mods come as a `.zip`. Drop the zip
onto the window or pick it with "Add external mod".

Before anything is installed the manager checks the zip:

- no programs or scripts (`.exe`, `.bat`, `.ps1`, `.vbs` ...) - otherwise the whole package is refused
- no paths leading outside (`..`, drive letters)
- every `.wd` must be a Two Worlds archive, every `.dll` a 32-bit DLL
- no file that already belongs to another package

| In the zip | Target |
|---|---|
| `Mods/<name>.wd` | `Mods\<name>.wd` |
| `TWSEPlugins/<name>.dll`, `.ini` | `TWSEPlugins\` |
| `<name>.wd` (no folder) | `Mods\` |
| `<name>.dll`, `<name>.ini` (no folder) | `TWSEPlugins\` |
| `readme.txt`, pictures | only shown |
| `modinfo.json` | name, version, `requires: ["twse"]` |

One folder around everything is skipped. The manager remembers in
`Mods\_packages.json` which file belongs to which package.

**Enabling, disabling, removing.** A package is one row in the list, its
plugin files sit below it. A double-click switches everything together: the
`.wd` through its registry switch, the `.dll` and `.ini` move to
`TWSEPlugins\_disabled\<package>\`. TWSE loads only DLLs directly in
`TWSEPlugins`, never from subfolders. "Remove" moves every file of the
package to `Mods\_removed\<package>\`; nothing is deleted. On an update an
ini you changed stays, the new one lies next to it as `<name>.ini.new`.
Plugins without a package (for example `TWExtended.dll` from Extended
Settings) have a row of their own and switch the same way.

**TWSE** is the Two Worlds Script Extender by buglord. It loads the plugins,
but only when the game starts through `TwoWorldsExtended.exe`.

- "Install TWSE" downloads the newest release from buglord's GitHub and checks the checksum. A twse.dll we have not checked yet is only installed after you agree. Without a connection the manager uses its own checked twse.dll.
- It creates `TwoWorldsExtended.exe` (the same bytes as buglord's patcher) and `twse.dll`, plus the folder `TWSEPlugins`. `TwoWorlds.exe` is not changed.
- It points shortcuts on the desktop and in the start menu that start `TwoWorlds.exe` to `TwoWorldsExtended.exe` and adds "Two Worlds (TWSE)" to the desktop.
- Steam, GOG Galaxy and the Mod Selector always start `TwoWorlds.exe`. The switch "Replace TwoWorlds.exe" makes `TwoWorlds.exe` itself the extended exe; the original stays as `TwoWorlds.exe.twse-backup`. "Verify files" in Steam or an update brings the original back, and the manager shows it.
- "Remove TWSE" turns everything back: old shortcut targets, the original `TwoWorlds.exe`; `TwoWorldsExtended.exe` and `twse.dll` move to `Mods\_removed\TWSE_<date>\`. What was done is written to `Mods\_twse_install.json`.
- "Start game" starts `TwoWorldsExtended.exe` when TWSE is there, otherwise `TwoWorlds.exe`.

## Files

| File | Purpose |
|---|---|
| `mod_manager.py` | the tool (`--` a `.wd` path as argument opens the install dialog) |
| `guidebook.py` | guide window (F1), chapters DE/EN, reference tables from the code |
| `packages.py` | `.zip` mod packages: check, install, enable/disable, remove |
| `twse_install.py` + `twse_patch.py` + `bin\twse.dll` | TWSE installer: download with checksum, extended exe, shortcuts, restore |
| `modscan.py` | what a mod changes against the game, compare of two mods (green / yellow / red) |
| `mergeui.py` | scan thread and row tips for the compare colours of the list |
| `levelcache.py` + `tw1_lhc.py` | level header cache without the SDK |
| `tw1_par.py`, `tw1_lan.py`, `tw1_lnd.py`, `lndmap.py`, `tw1_wd.py` | format modules shared with the PAR Editor and the Quest Creator |
| `fieldnames.py` + `tw1_sdk_fields.json` | SDK names of the par fields |
| `tests/` | `python -m unittest discover -s tests -t .`  |
| `version.py` | version number |
| `theme.py` | dark theme shared by the TW1 tools |
| `build_mod_manager_exe.bat` | PyInstaller one-file build (`dist\TW1_Mod_Manager.exe`) |
| `selftest_exe.bat` | starts the exe in selftest mode (`MOD_MANAGER_SELFTEST=<file>`) |

Config: `mod_manager_settings.json` next to the script; as exe under
`%LOCALAPPDATA%\TW1ModManager\`.

## Credits

- Registry logic: [buglord's Mod Selector](https://github.com/buglord/Two-Worlds-1-Misc-Projects/tree/main/Mod%20Selector)

## License

CC0 - do what you want with it.
