"""TW1 Mod Manager - install, enable and disable Two Worlds mods.

    py mod_manager.py              window
    py mod_manager.py <file.wd>    window, then the install dialog for that file

A mod manager for a 2007 game that never had one:

* every archive in Mods\\ with its registry switch, toggled per mod
* .wd files dropped onto the window (or picked with Ctrl+O) go through one
  dialog: what is inside, install and enable / install only / cancel
* a curated "My Mods" list fetched from the community server, with
  on-demand download and checksum verification
* nothing is ever deleted - disabling flips the registry DWORD, and
  "remove" moves the archive into Mods\\_removed\\

Layout after PY_TOOL_DESIGN.md: theme.py, dark menu bar, DE/EN top right,
tour on first start, guide window (F1) with ?-marks, update check and
self-update from GitHub (updater.py), config next to the script or under
%LOCALAPPDATA%\\TW1ModManager as exe.

Registry model (measured against the game's own Mod Selector): the key
HKCU\\SOFTWARE\\Reality Pump\\TwoWorlds\\Mods holds one DWORD per archive
name; 1 loads, 0 is a deliberate off switch, no value loads too. Archives in
the game ROOT load unconditionally.
"""

import ctypes
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import webbrowser
import zlib
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tkinter as tk                                   # noqa: E402
from tkinter import ttk, filedialog, messagebox        # noqa: E402

import theme                                            # noqa: E402
import guidebook                                        # noqa: E402
import updater                                          # noqa: E402
from version import VERSION                             # noqa: E402

APP_NAME = 'TW1 MOD MANAGER'
GITHUB_URL = 'https://github.com/MedievalDev/TW1_ModManager'
SITE_URL = 'https://alchemy-fox.de/'
GUIDE_URL = 'https://alchemy-fox.de/game/TW1_ModManager/'
COMMUNITY_URL = 'https://twmp.alchemy-fox.de/'
LINKS = (('GitHub-Repo', GITHUB_URL), ('Alchemy Fox', SITE_URL),
         ('Guide-Seite', GUIDE_URL), ('Community', COMMUNITY_URL))
MODS_URL = 'https://alchemy-fox.de/game/TW1_DialogAndQuestCreator/mods/mods.json'
REG_MODS = r'SOFTWARE\Reality Pump\TwoWorlds\Mods'
GAME_EXES = ('TwoWorlds.exe', 'TwoWorldsExtended.exe', 'TwoWorlds_RADEON.exe')
WD_MAGIC = bytes([0xFF, 0xA1, 0xD0, 0x31, 0x57, 0x44, 0x00, 0x02])
SEP = chr(92)


# ------------------------------------------------------------------ Sprache --

_LANG = 'en'


def system_is_german():
    try:
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x07
    except Exception:
        return False


def tr(text):
    if _LANG == 'de':
        return DE.get(text, text)
    return text


# ------------------------------------------------------------------- Konfig --

def data_dir():
    if getattr(sys, 'frozen', False):
        d = os.path.join(os.environ.get('LOCALAPPDATA', HERE), 'TW1ModManager')
        os.makedirs(d, exist_ok=True)
        return d
    return HERE


class Config(dict):
    def __init__(self):
        super().__init__()
        self.path = os.path.join(data_dir(), 'mod_manager_settings.json')
        try:
            self.update(json.load(open(self.path, encoding='utf-8')))
        except Exception:
            pass

    def save(self):
        try:
            json.dump(self, open(self.path, 'w', encoding='utf-8'), indent=2)
        except Exception:
            pass


# -------------------------------------------------------------- Spiel finden --

def valid_game_dir(path):
    if not path or not os.path.isdir(path):
        return False
    wd = os.path.join(path, 'WDFiles')
    return (os.path.exists(os.path.join(wd, 'Update16.wd'))
            or os.path.exists(os.path.join(wd, 'Language.wd')))


def _steam_libraries():
    libs = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam'),
                          (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Valve\Steam')):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ('InstallPath', 'SteamPath'):
                        try:
                            v, _ = winreg.QueryValueEx(k, name)
                            libs.append(v.replace('/', SEP))
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass
    out = []
    for root in libs:
        out.append(root)
        vdf = os.path.join(root, 'steamapps', 'libraryfolders.vdf')
        if os.path.exists(vdf):
            for m in re.finditer(r'"path"\s+"([^"]+)"', open(vdf, encoding='utf-8', errors='ignore').read()):
                out.append(m.group(1).replace(SEP + SEP, SEP))
    return out


def find_game_dir(hint=None):
    """Saved choice, then DataDir in the registry, then the Steam libraries."""
    if valid_game_dir(hint):
        return hint
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'SOFTWARE\Reality Pump\TwoWorlds') as k:
            path = winreg.QueryValueEx(k, 'DataDir')[0].rstrip(SEP)
            if valid_game_dir(path):
                return path
    except OSError:
        pass
    for lib in _steam_libraries():
        c = os.path.join(lib, 'steamapps', 'common', 'Two Worlds - Epic Edition')
        if valid_game_dir(c):
            return c
    for c in (r'C:\Program Files (x86)\Steam\steamapps\common\Two Worlds - Epic Edition',
              r'C:\Program Files (x86)\Reality Pump\Two Worlds'):
        if valid_game_dir(c):
            return c
    return None


def game_running():
    try:
        out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                             creationflags=0x08000000).stdout.lower()
    except Exception:
        return False
    return any(e.lower().encode() in out for e in GAME_EXES)


# -------------------------------------------------------------- Registry --

def registry_mods():
    """{archive name: 0/1} for every DWORD under the Mods key."""
    out = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_MODS) as k:
            i = 0
            while True:
                try:
                    name, value, _typ = winreg.EnumValue(k, i)
                except OSError:
                    break
                out[name] = value
                i += 1
    except OSError:
        pass
    return out


def registry_set(name, value):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_MODS) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))


def registry_delete(name):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_MODS, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- WD peek --

def wd_peek(path):
    """What a .wd holds: {'ok', 'error', 'guid', 'paths', 'folders'}.

    Reads the zlib-packed 24-byte head (magic + GUID) and the directory at
    the file end; the data part is never touched, so this is fast even for
    a 100 MB archive."""
    out = {'ok': False, 'error': '', 'guid': None, 'paths': [], 'folders': []}
    try:
        with open(path, 'rb') as f:
            head_raw = f.read(4096)
            d = zlib.decompressobj()
            head = d.decompress(head_raw)
            if head[:8] != WD_MAGIC:
                out['error'] = tr('not a Two Worlds WD archive (wrong header)')
                return out
            out['guid'] = head[8:24].hex()
            f.seek(-4, 2)
            dir_off = struct.unpack('<I', f.read(4))[0]
            f.seek(-dir_off, 2)
            raw = f.read()
        t = zlib.decompress(raw[:-4])
        off = 8
        n = struct.unpack_from('<H', t, off)[0]
        off += 2
        paths = []
        for _ in range(n):
            nl = t[off]; off += 1
            name = t[off:off + nl].decode('latin-1'); off += nl
            flags = t[off]; off += 13
            if flags & 0x08:
                xl = t[off]; off += 1 + xl
            if flags & 0x10:
                off += 4
            if flags & 0x20:
                off += 16
            paths.append(name)
        out['paths'] = paths
        seen = []
        for p in paths:
            top = p.split(SEP)[0] if SEP in p else p
            if top not in seen:
                seen.append(top)
        out['folders'] = seen
        out['ok'] = True
    except Exception as e:
        out['error'] = tr('archive could not be read: {e}').format(e=e)
    return out


# ------------------------------------------------------------ Drag & Drop --

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)


def enable_drop(root, callback):
    """Accept files dropped from Explorer onto the window (WM_DROPFILES via
    a subclassed window procedure - plain ctypes, no extra package).

    Tk gives every widget its own HWND on Windows, so the toplevel and all
    of its children are registered; a drop anywhere in the window lands in
    ``callback(list_of_paths)`` on the Tk thread."""
    user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.CallWindowProcW.restype = ctypes.c_ssize_t
    user32.CallWindowProcW.argtypes = (ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM)
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragQueryFileW.argtypes = (wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT)
    shell32.DragAcceptFiles.argtypes = (wintypes.HWND, wintypes.BOOL)
    shell32.DragFinish.argtypes = (wintypes.HANDLE,)
    root.update_idletasks()
    top = user32.GetParent(root.winfo_id()) or root.winfo_id()
    hwnds = [top]
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(h, _lp):
        hwnds.append(h)
        return True
    user32.EnumChildWindows(top, enum_proc(collect), 0)
    olds = {}
    procs = []

    def make(h):
        def proc(hwnd, msg, wp, lp):
            if msg == WM_DROPFILES:
                n = shell32.DragQueryFileW(wp, 0xFFFFFFFF, None, 0)
                files = []
                for i in range(n):
                    ln = shell32.DragQueryFileW(wp, i, None, 0)
                    buf = ctypes.create_unicode_buffer(ln + 1)
                    shell32.DragQueryFileW(wp, i, buf, ln + 1)
                    files.append(buf.value)
                shell32.DragFinish(wp)
                root.after(0, callback, files)
                return 0
            return user32.CallWindowProcW(olds[h], hwnd, msg, wp, lp)
        return _WNDPROC(proc)
    for h in hwnds:
        shell32.DragAcceptFiles(h, True)
        cb = make(h)
        procs.append(cb)                       # keep the callbacks alive
        olds[h] = user32.SetWindowLongPtrW(h, GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p).value)
    root._drop_procs = procs
    return len(hwnds)


# ---------------------------------------------------------------- Helpers --

def help_mark(parent, text, chapter, app, panel=False):
    lbl = ttk.Label(parent, text='?', style='Panel.TLabel' if panel else 'TLabel',
                    foreground=theme.GOLD, cursor='hand2')
    lbl.pack(side='left', padx=(6, 0))
    theme.Tooltip(lbl, text)
    lbl.bind('<Button-1>', lambda ev: app.show_help(text, chapter))
    return lbl


def fmt_size(n):
    return f'{n / 1048576:.1f} MB'


# ------------------------------------------------------------------ Tour --

GUIDE_STEPS = [
    {'title': 'Welcome', 'widget': None, 'text':
     'Two Worlds loads mods from the Mods folder next to the game and reads in the registry '
     'which of them are on. This window shows both, switches them, installs new ones and '
     'fetches verified mods from the community server. Nothing is ever deleted.'},
    {'title': 'Installed mods', 'widget': 'tree', 'text':
     'Every .wd in the Mods folder with its switch, size and date. Double-click or Enter toggles '
     'a mod. Red rows are archives in the game folder itself - those always load.'},
    {'title': 'Drop a mod here', 'widget': 'drop_hint', 'text':
     'Drag a .wd from Explorer anywhere onto this window. A dialog shows what is inside and asks: '
     'install and enable, install only, or cancel. Ctrl+O does the same through a file dialog.'},
    {'title': 'Buttons', 'widget': 'btn_toggle', 'text':
     'Enable / disable flips the registry switch. Remove moves the archive to Mods\\_removed and '
     'keeps it. Changes count from the next game start.'},
    {'title': 'Server', 'widget': 'notebook', 'text':
     'The second tab lists verified mods from alchemy-fox.de. Install downloads, checks the '
     'SHA-256 and enables the mod; an existing archive is kept as .backup.'},
    {'title': 'Help', 'widget': 'menubar', 'text':
     'F1 opens the guide with chapters, search and the registry reference. The gold ? marks jump '
     'straight to the matching chapter. Help also checks for updates.'},
]


class Guide:
    def __init__(self, app):
        self.app, self.i, self.frames, self.win = app, 0, [], None

    def start(self):
        self.i = 0
        if self.win:
            self.win.destroy()
        self.win = tk.Toplevel(self.app.root)
        self.win.title(tr('Tour'))
        self.win.configure(background=theme.PANEL)
        self.win.transient(self.app.root)
        self.win.attributes('-topmost', True)
        self.win.protocol('WM_DELETE_WINDOW', lambda: self.finish(False))
        theme.dark_titlebar(self.win)
        f = ttk.Frame(self.win, style='Panel.TFrame', padding=14)
        f.pack(fill='both', expand=True)
        self.head = ttk.Label(f, style='PanelTitle.TLabel')
        self.head.pack(anchor='w')
        self.title = ttk.Label(f, style='Panel.TLabel', font=theme.FONT_H2, foreground=theme.GOLD)
        self.title.pack(anchor='w', pady=(4, 6))
        self.text = ttk.Label(f, style='Panel.TLabel', wraplength=360, justify='left')
        self.text.pack(anchor='w')
        self.dont = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text=tr("Don't show at startup"), variable=self.dont,
                        style='Panel.TCheckbutton').pack(anchor='w', pady=(14, 8))
        b = ttk.Frame(f, style='Panel.TFrame')
        b.pack(fill='x')
        self.back = ttk.Button(b, text=tr('Back'), command=self.prev)
        self.back.pack(side='left')
        self.next = ttk.Button(b, text=tr('Next'), style='Accent.TButton', command=self.nxt)
        self.next.pack(side='left', padx=8)
        ttk.Button(b, text=tr('Quit tour'), command=lambda: self.finish(self.dont.get())).pack(side='right')
        self.show()
        r = self.app.root
        self.win.geometry(f'+{r.winfo_rootx() + r.winfo_width() - 440}+{r.winfo_rooty() + 120}')

    def show(self):
        s = GUIDE_STEPS[self.i]
        self.head.configure(text=tr('Step {n} of {m}').format(n=self.i + 1, m=len(GUIDE_STEPS)))
        self.title.configure(text=tr(s['title']))
        self.text.configure(text=tr(s['text']))
        self.back.state(['!disabled'] if self.i > 0 else ['disabled'])
        self.next.configure(text=tr('Next') if self.i < len(GUIDE_STEPS) - 1 else tr('Finish'))
        self.highlight(getattr(self.app, s['widget'], None) if s['widget'] else None)

    def prev(self):
        if self.i > 0:
            self.i -= 1
            self.show()

    def nxt(self):
        if self.i < len(GUIDE_STEPS) - 1:
            self.i += 1
            self.show()
        else:
            self.finish(True)

    def highlight(self, widget):
        for f in self.frames:
            f.destroy()
        self.frames = []
        if widget is None:
            return
        root = self.app.root
        root.update_idletasks()
        x = widget.winfo_rootx() - root.winfo_rootx()
        y = widget.winfo_rooty() - root.winfo_rooty()
        w, h, t = widget.winfo_width(), widget.winfo_height(), 3
        for fx, fy, fw, fh in ((x, y, w, t), (x, y + h - t, w, t), (x, y, t, h), (x + w - t, y, t, h)):
            f = tk.Frame(root, background=theme.GOLD)
            f.place(x=fx, y=fy, width=fw, height=fh)
            self.frames.append(f)

    def finish(self, dont_show):
        self.highlight(None)
        if dont_show or self.i == len(GUIDE_STEPS) - 1:
            self.app.cfg['guide_seen'] = True
            self.app.cfg.save()
        if self.win:
            self.win.destroy()
            self.win = None


# ---------------------------------------------------------- Install dialog --

class InstallDialog:
    """One window for every .wd that arrives - dropped, picked or passed on
    the command line: what it is, what is inside, install and enable /
    install only / cancel."""

    def __init__(self, app, path):
        self.app = app
        self.path = path
        self.result = None
        name = os.path.basename(path)
        info = wd_peek(path)
        self.win = tk.Toplevel(app.root)
        self.win.title(tr('Load this mod?'))
        self.win.transient(app.root)
        self.win.resizable(False, False)
        theme.dark_titlebar(self.win)
        self.win.bind('<Escape>', lambda e: self.close(None))
        f = ttk.Frame(self.win, padding=18)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text=name, style='Brand.TLabel').pack(anchor='w')
        size = os.path.getsize(path)
        sub = fmt_size(size)
        if info['ok']:
            sub += '  ·  ' + tr('{n} files').format(n=len(info['paths']))
        ttk.Label(f, text=sub, style='Muted.TLabel').pack(anchor='w', pady=(0, 10))

        box = ttk.Frame(f, style='Panel.TFrame', padding=(12, 8))
        box.pack(fill='x')
        if info['ok']:
            folders = ', '.join(info['folders'][:8]) + (' …' if len(info['folders']) > 8 else '')
            ttk.Label(box, text=tr('Contains: ') + folders, style='Panel.TLabel',
                      wraplength=420, justify='left').pack(anchor='w')
            sample = [p for p in info['paths'] if p.lower().endswith(('.par', '.lnd', '.eco', '.qtx', '.lan', '.dds'))][:6]
            if sample:
                ttk.Label(box, text=chr(10).join(sample), style='PanelMuted.TLabel',
                          font=theme.FONT_MONO, justify='left').pack(anchor='w', pady=(4, 0))
        else:
            ttk.Label(box, text=info['error'], style='Panel.TLabel', foreground=theme.ERR,
                      wraplength=420, justify='left').pack(anchor='w')

        dest = os.path.join(app.mods_dir, name)
        self.dest = dest
        note = ''
        self.same_file = os.path.abspath(path) == os.path.abspath(dest)
        if self.same_file:
            note = tr('This file already sits in the Mods folder - only the switch changes.')
        elif os.path.exists(dest):
            if os.path.getsize(dest) == size and sha256_of(dest) == sha256_of(path):
                note = tr('Identical to the copy already in the Mods folder.')
            else:
                note = tr('Replaces the existing {name} - the old one is kept as .backup.').format(name=name)
        elif not info['ok']:
            note = tr('Not a Two Worlds mod archive. Nothing will be installed.')
        self.note = ttk.Label(f, text=note, style='Muted.TLabel', wraplength=440, justify='left')
        self.note.pack(anchor='w', pady=(10, 0))
        if game_running():
            ttk.Label(f, text=tr('Two Worlds is running - close it first, it reads the mod list only at start.'),
                      foreground=theme.ERR, wraplength=440, justify='left').pack(anchor='w', pady=(6, 0))

        b = ttk.Frame(f)
        b.pack(fill='x', pady=(16, 0))
        ttk.Button(b, text=tr('Cancel'), command=lambda: self.close(None)).pack(side='right')
        self.only = ttk.Button(b, text=tr('Install only'), command=lambda: self.close('install'))
        self.only.pack(side='right', padx=8)
        self.go = ttk.Button(b, text=tr('Install and enable'), style='Accent.TButton',
                             command=lambda: self.close('enable'))
        self.go.pack(side='right')
        if not info['ok'] or game_running():
            self.go.state(['disabled'])
            self.only.state(['disabled'])
        else:
            self.win.bind('<Return>', lambda e: self.close('enable'))
            self.go.focus_set()
        self.win.update_idletasks()
        r = app.root
        self.win.geometry(f'+{r.winfo_rootx() + 120}+{r.winfo_rooty() + 120}')
        self.win.grab_set()
        self.win.wait_window()

    def close(self, result):
        self.result = result
        self.win.grab_release()
        self.win.destroy()


# -------------------------------------------------------------------- App --

class App:
    def __init__(self, carry=None):
        self.cfg = Config()
        self._carry = carry or {}
        self.selftest = os.environ.get('MOD_MANAGER_SELFTEST')
        global _LANG
        _LANG = self.cfg.get('lang') or ('de' if system_is_german() else 'en')
        self.root = tk.Tk()
        self.root.withdraw()
        theme.apply_dark_theme(self.root)
        self.root.title(f'TW1 Mod Manager {VERSION}')
        self._icon()
        self.restart = False
        self.catalog = None
        self.update_var = tk.BooleanVar(value=bool(self.cfg.get('update_check', True)))
        self.guide = Guide(self)

        self.game_dir = find_game_dir(self.cfg.get('game_dir'))
        self.mods_dir = os.path.join(self.game_dir, 'Mods') if self.game_dir else None
        if self.mods_dir:
            os.makedirs(self.mods_dir, exist_ok=True)

        self.build()
        self.place_window()
        self.root.deiconify()
        self.root.after(200, self._startup)

    def _icon(self):
        base = getattr(sys, '_MEIPASS', HERE)
        ico = os.path.join(base, 'mod_manager.ico')
        if os.path.exists(ico):
            try:
                self.root.iconbitmap(ico)
            except Exception:
                pass

    def place_window(self):
        if self._carry.get('geometry'):
            self.root.geometry(self._carry['geometry'])
            return
        w, h = 960, 660
        self.root.update_idletasks()
        x = max(0, (self.root.winfo_screenwidth() - w) // 2)
        y = max(0, (self.root.winfo_screenheight() - h) // 2 - 30)
        self.root.geometry(f'{w}x{h}+{x}+{y}')
        self.root.minsize(780, 540)

    # ---- start-up ----
    def _startup(self):
        updater.cleanup_old()
        try:
            n = enable_drop(self.root, self.on_drop)
            self.set_hint(tr('Drop a .wd anywhere on this window to install it.'))
            self._drop_ok = n > 0
        except Exception as e:                    # not Windows, or blocked
            self._drop_ok = False
            self.set_hint(tr('Drag and drop not available: {e}').format(e=e))
        if not self.game_dir:
            if self.selftest:
                self._run_selftest('no game dir')
                return
            messagebox.showinfo(APP_NAME, tr('Two Worlds install not found automatically.\nPlease pick your Two Worlds folder (the one with WDFiles).'), parent=self.root)
            self.change_game_path()
            if not self.game_dir:
                self.root.destroy()
                return
        self.refresh()
        threading.Thread(target=self._fetch_catalog, daemon=True).start()
        if self.selftest:
            self._run_selftest()
            return
        c = self._carry
        if c.get('tab'):
            self.notebook.select(c['tab'])
        if not c and self.cfg.get('update_check', True):
            self.root.after(1500, self.check_updates)
        if not c and not self.cfg.get('guide_seen'):
            self.root.after(700, self.guide.start)
        for p in c.get('pending', []):
            self.root.after(300, lambda p=p: self.offer_install([p]))

    def _run_selftest(self, note=''):
        try:
            https = 'ok'
            try:
                import http.client  # noqa: F401
                import ssl  # noqa: F401
                import urllib.request  # noqa: F401
            except ImportError as e:
                https = f'missing:{e.name}'
            with open(self.selftest, 'w', encoding='utf-8') as f:
                f.write(f'version={VERSION} game={bool(self.game_dir)} mods={len(self.tree.get_children()) if self.game_dir else 0} '
                        f'drop={getattr(self, "_drop_ok", False)} chapters={len(guidebook.CHAPTERS)} https={https} '
                        f'frozen={getattr(sys, "frozen", False)} {note}'.strip() + chr(10))
        except Exception as e:
            with open(self.selftest, 'a', encoding='utf-8') as f:
                f.write(f'selftest failed: {e!r}' + chr(10))
        finally:
            self.root.after(50, self.root.destroy)

    # ---- build ----
    def build(self):
        self.build_menubar()
        # status bar first, so it never gets squeezed out (design 7.5)
        self.statusbar = ttk.Frame(self.root, style='Status.TFrame')
        self.statusbar.pack(fill='x', side='bottom')
        self.lbl_status = ttk.Label(self.statusbar, text=tr('ready'), style='Status.TLabel')
        self.lbl_status.pack(side='left')
        self.hint_label = ttk.Label(self.statusbar, text='', style='Status.TLabel')
        self.hint_label.pack(side='right')

        head = ttk.Frame(self.root, padding=(12, 10))
        head.pack(fill='x')
        ttk.Label(head, text=tr('Mods of Two Worlds'), style='Brand.TLabel').pack(side='left')
        self.lbl_game = ttk.Label(head, text=self.game_dir or tr('no game folder'), style='Muted.TLabel')
        self.lbl_game.pack(side='left', padx=14)
        help_mark(head, tr('The game folder: saved choice, then the registry, then Steam. File > Change game path if it is wrong.'), 'trouble', self)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=12, pady=(0, 6))
        self.notebook.add(self._tab_installed(self.notebook), text='  ' + tr('Installed mods') + '  ')
        self.notebook.add(self._tab_server(self.notebook), text='  ' + tr('My Mods (server)') + '  ')

    def build_menubar(self):
        bar = ttk.Frame(self.root, style='Menubar.TFrame')
        bar.pack(fill='x')
        self.menubar = bar
        for key, filler in ((tr('File'), self._fill_file), (tr('View'), self._fill_view),
                            (tr('Help'), self._fill_help)):
            item = ttk.Label(bar, text=key, style='Menubar.TLabel')
            item.pack(side='left')
            item.bind('<Button-1>', lambda ev, f=filler, w=item: self._popup(f, w))
            item.bind('<Enter>', lambda ev, w=item: w.state(['active']))
            item.bind('<Leave>', lambda ev, w=item: w.state(['!active']))
        ttk.Label(bar, text=APP_NAME, style='Menubar.TLabel').pack(side='right', padx=(0, 6))
        box = ttk.Frame(bar, style='Menubar.TFrame')
        self.lang_labels = {}
        for i, code in enumerate(('de', 'en')):
            if i:
                ttk.Label(box, text='·', style='Menubar.TLabel', padding=(2, 5)).pack(side='left')
            lbl = ttk.Label(box, text=code.upper(), style='Menubar.TLabel', padding=(4, 5), cursor='hand2')
            lbl.pack(side='left')
            lbl.bind('<Button-1>', lambda ev, c=code: self.set_lang(c))
            self.lang_labels[code] = lbl
        for code, lbl in self.lang_labels.items():
            lbl.configure(foreground=theme.GOLD if code == _LANG else theme.MUT)
        box.pack(side='right', padx=(0, 10))

    def _popup(self, filler, widget):
        menu = theme.Menu(self.root)
        filler(menu)
        try:
            menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            menu.grab_release()

    def _fill_file(self, m):
        m.add_command(label=tr('Add external mod...'), accelerator='Ctrl+O', command=self.add_mod)
        m.add_command(label=tr('Open Mods folder'), command=lambda: os.startfile(self.mods_dir),
                      state='normal' if self.mods_dir else 'disabled')
        m.add_command(label=tr('Refresh'), accelerator='F5', command=self.refresh)
        m.add_separator()
        m.add_command(label=tr('Change game path...'), command=self.change_game_path)
        m.add_separator()
        m.add_command(label=tr('Exit'), accelerator='Alt+F4', command=self.root.destroy)

    def _fill_view(self, m):
        m.add_command(label=tr('Installed mods'), command=lambda: self.notebook.select(0))
        m.add_command(label=tr('My Mods (server)'), command=lambda: self.notebook.select(1))
        m.add_separator()
        sub = theme.Menu(m)
        for code, name in (('de', 'Deutsch'), ('en', 'English')):
            sub.add_radiobutton(label=name, value=code, variable=tk.StringVar(value=_LANG),
                                command=lambda c=code: self.set_lang(c))
        m.add_cascade(label=tr('Language'), menu=sub)

    def _fill_help(self, m):
        m.add_command(label=tr('Guide'), accelerator='F1', command=self.show_guide)
        m.add_command(label=tr('Start tour'), command=self.guide.start)
        m.add_command(label=tr('Documentation'), command=lambda: webbrowser.open(GUIDE_URL))
        m.add_separator()
        for name, url in LINKS:
            m.add_command(label=f'{name}  ({url})', command=lambda u=url: webbrowser.open(u))
        m.add_separator()
        m.add_command(label=tr('Check for updates'), command=lambda: self.check_updates(manual=True))
        m.add_checkbutton(label=tr('Check for updates on start'), variable=self.update_var,
                          command=self._toggle_update_check)
        m.add_command(label=tr('Latest version on GitHub'), command=lambda: webbrowser.open(updater.LATEST_PAGE))
        m.add_separator()
        m.add_command(label=tr('About'), command=self.show_about)

    def _tab_installed(self, parent):
        tab = ttk.Frame(parent, padding=12)
        hdr = ttk.Frame(tab)
        hdr.pack(fill='x', pady=(0, 4))
        ttk.Label(hdr, text=tr('Archives in the Mods folder'), style='Muted.TLabel').pack(side='left')
        help_mark(hdr, tr('One row per .wd with its registry switch. Double-click toggles. Red rows sit in the game folder and always load.'), 'switch', self)
        self.tree = ttk.Treeview(tab, columns=('state', 'size', 'date'), show='tree headings')
        self.tree.heading('#0', text=tr('Mod archive'))
        self.tree.heading('state', text=tr('State'))
        self.tree.heading('size', text=tr('Size'))
        self.tree.heading('date', text=tr('Changed'))
        self.tree.column('#0', width=300)
        self.tree.column('state', width=120, anchor='center')
        self.tree.column('size', width=90, anchor='e')
        self.tree.column('date', width=130, anchor='center')
        self.tree.pack(fill='both', expand=True)
        self.tree.tag_configure('on', foreground=theme.OK)
        self.tree.tag_configure('off', foreground=theme.MUT)
        self.tree.tag_configure('warn', foreground=theme.ERR)
        self.tree.bind('<Double-1>', lambda ev: self.toggle())
        self.tree.bind('<Return>', lambda ev: self.toggle())
        self.tree.bind('<Delete>', lambda ev: self.remove_mod())
        self.tree.bind('<Button-3>', self._tree_menu)

        btns = ttk.Frame(tab)
        btns.pack(fill='x', pady=(10, 0))
        self.btn_toggle = ttk.Button(btns, text=tr('Enable / disable'), style='Accent.TButton', command=self.toggle)
        self.btn_toggle.pack(side='left')
        ttk.Button(btns, text=tr('Add external mod...'), command=self.add_mod).pack(side='left', padx=6)
        ttk.Button(btns, text=tr('Remove (keeps a copy)'), command=self.remove_mod).pack(side='left')
        ttk.Button(btns, text=tr('Refresh'), command=self.refresh).pack(side='right')
        self.drop_hint = ttk.Label(tab, style='PanelMuted.TLabel', padding=(10, 8), anchor='center',
                                   text=tr('Drop a .wd file anywhere on this window to install it.'))
        self.drop_hint.pack(fill='x', pady=(10, 0))
        ttk.Label(tab, style='Muted.TLabel', wraplength=860, justify='left',
                  text=tr('Double-click toggles a mod. Disabling keeps the file and sets its registry switch to 0 - exactly what the in-game Mod Selector does. Changes apply on the next game start.')
                  ).pack(anchor='w', pady=(8, 0))
        return tab

    def _tab_server(self, parent):
        tab = ttk.Frame(parent, padding=12)
        hdr = ttk.Frame(tab)
        hdr.pack(fill='x', pady=(0, 4))
        ttk.Label(hdr, text=tr('Verified mods from alchemy-fox.de'), style='Muted.TLabel').pack(side='left')
        help_mark(hdr, tr('mods.json from the community server: name, version, SHA-256. Install downloads, verifies and enables.'), 'server', self)
        self.stree = ttk.Treeview(tab, columns=('ver', 'size', 'status'), show='tree headings')
        self.stree.heading('#0', text=tr('Mod'))
        self.stree.heading('ver', text=tr('Version'))
        self.stree.heading('size', text=tr('Size'))
        self.stree.heading('status', text=tr('On this PC'))
        self.stree.column('#0', width=300)
        self.stree.column('ver', width=70, anchor='center')
        self.stree.column('size', width=90, anchor='e')
        self.stree.column('status', width=160, anchor='center')
        self.stree.pack(fill='both', expand=True)
        self.stree.tag_configure('on', foreground=theme.OK)
        self.stree.tag_configure('get', foreground=theme.GOLD)
        self.stree.tag_configure('off', foreground=theme.MUT)
        self.lbl_desc = ttk.Label(tab, style='Muted.TLabel', wraplength=860, justify='left')
        self.lbl_desc.pack(anchor='w', pady=(8, 0))
        self.stree.bind('<<TreeviewSelect>>', lambda ev: self._show_desc())
        btns = ttk.Frame(tab)
        btns.pack(fill='x', pady=(10, 0))
        self.btn_install = ttk.Button(btns, text=tr('Install / update'), style='Accent.TButton',
                                      command=self.install_server_mod)
        self.btn_install.pack(side='left')
        ttk.Button(btns, text=tr('Reload list'),
                   command=lambda: threading.Thread(target=self._fetch_catalog, daemon=True).start()
                   ).pack(side='left', padx=6)
        ttk.Label(tab, style='Muted.TLabel', wraplength=860, justify='left',
                  text=tr('Downloads are checksum-verified; existing archives get a .backup copy before an update.')
                  ).pack(anchor='w', pady=(8, 0))
        return tab

    def _bind_keys(self):
        r = self.root
        r.bind('<Control-o>', lambda e: self.add_mod())
        r.bind('<F5>', lambda e: self.refresh())
        r.bind('<F1>', lambda e: self.show_guide())

    # ---- status / hints ----
    def status(self, text, error=False):
        def do():
            self.lbl_status.configure(text=text, style='StatusErr.TLabel' if error else 'Status.TLabel')
        self.root.after(0, do)

    def set_hint(self, text):
        self.hint_label.configure(text=(text or '')[:140])

    def show_guide(self, chapter='start'):
        guidebook.GuideWindow.show(self, chapter)

    def show_help(self, text, chapter):
        self.set_hint(text.split(chr(10))[0])
        self.show_guide(chapter)

    # ---- language ----
    def set_lang(self, code):
        global _LANG
        if code == _LANG:
            return
        self.cfg['lang'] = code
        self.cfg.save()
        _LANG = code
        self.restart = True
        self.carry_out = {'tab': self.notebook.index(self.notebook.select()), 'geometry': self.root.geometry()}
        self.root.destroy()

    # ---- game path ----
    def change_game_path(self):
        d = filedialog.askdirectory(parent=self.root, title=tr('Select your Two Worlds folder (the one containing WDFiles)'),
                                    initialdir=self.game_dir or 'C:' + SEP)
        if not d:
            return
        d = os.path.normpath(d)
        if not valid_game_dir(d):
            messagebox.showerror(APP_NAME, tr('That folder has no WDFiles\\Update16.wd:\n{p}\n\nPick the Two Worlds install folder itself.').format(p=d), parent=self.root)
            return
        self.game_dir = d
        self.mods_dir = os.path.join(d, 'Mods')
        os.makedirs(self.mods_dir, exist_ok=True)
        self.cfg['game_dir'] = d
        self.cfg.save()
        self.lbl_game.configure(text=d)
        self.refresh()
        self.status(tr('Game path set: {p}').format(p=d))

    # ---- installed ----
    def refresh(self):
        if not self.mods_dir:
            return
        self.tree.delete(*self.tree.get_children())
        reg = registry_mods()
        files = {f for f in os.listdir(self.mods_dir) if f.lower().endswith('.wd')}
        for name in sorted(files | set(reg), key=str.lower):
            path = os.path.join(self.mods_dir, name)
            exists = name in files
            enabled = reg.get(name, 0)
            if not exists:
                state, tag, size, date = tr('file missing'), 'off', '—', '—'
            else:
                st = os.stat(path)
                size = fmt_size(st.st_size)
                import datetime
                date = datetime.datetime.fromtimestamp(st.st_mtime).strftime('%d.%m.%Y %H:%M')
                state, tag = (tr('enabled'), 'on') if enabled else (tr('disabled'), 'off')
            self.tree.insert('', 'end', iid=name, text=name, values=(state, size, date), tags=(tag,))
        for name in [f for f in os.listdir(self.game_dir) if f.lower().endswith('.wd')]:
            self.tree.insert('', 'end', iid='ROOT::' + name, text=name + '  ' + tr('(in the game folder!)'),
                             values=(tr('ALWAYS loads'), '', ''), tags=('warn',))
        self._refresh_server_states()
        on = sum(1 for n in files if reg.get(n, 0))
        self.status(tr('{n} archives, {m} enabled').format(n=len(files), m=on))

    def _selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _tree_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        m = theme.Menu(self.root)
        m.add_command(label=tr('Enable / disable'), command=self.toggle)
        m.add_command(label=tr('Remove (keeps a copy)'), command=self.remove_mod)
        m.add_separator()
        m.add_command(label=tr('Show in Explorer'), command=lambda: self._reveal(iid))
        m.tk_popup(event.x_root, event.y_root)

    def _reveal(self, iid):
        name = iid[6:] if iid.startswith('ROOT::') else iid
        folder = self.game_dir if iid.startswith('ROOT::') else self.mods_dir
        p = os.path.join(folder, name)
        if os.path.exists(p):
            subprocess.Popen(['explorer', '/select,', p])

    def toggle(self):
        name = self._selected()
        if not name:
            return
        if name.startswith('ROOT::'):
            messagebox.showwarning(APP_NAME, tr('Archives in the game folder ignore the registry - the game loads them no matter what. Move the file out of the game folder to disable it.'), parent=self.root)
            return
        if game_running():
            messagebox.showerror(APP_NAME, tr('Close Two Worlds first - it reads the mod list only at start.'), parent=self.root)
            return
        new = 0 if registry_mods().get(name, 0) else 1
        registry_set(name, new)
        self.refresh()
        self.tree.selection_set(name)
        self.status(tr('{name} {state} - takes effect on the next game start.').format(
            name=name, state=tr('enabled') if new else tr('disabled')))

    def add_mod(self):
        paths = filedialog.askopenfilenames(parent=self.root, title=tr('Choose a mod archive'),
                                            filetypes=[(tr('Two Worlds mod'), '*.wd')])
        if paths:
            self.offer_install(list(paths))

    def on_drop(self, files):
        wds = [f for f in files if f.lower().endswith('.wd') and os.path.isfile(f)]
        others = [f for f in files if f not in wds]
        if others and not wds:
            self.status(tr('Only .wd archives can be installed ({n} other file(s) ignored).').format(n=len(others)), error=True)
        self.root.lift()
        self.root.focus_force()
        if wds:
            self.offer_install(wds)

    def offer_install(self, paths):
        """The one dialog for dropped, picked or command-line archives."""
        for path in paths:
            dlg = InstallDialog(self, path)
            if dlg.result:
                self._install(path, enable=(dlg.result == 'enable'), same=dlg.same_file)

    def _install(self, path, enable, same=False):
        if game_running():
            messagebox.showerror(APP_NAME, tr('Close Two Worlds first - it reads the mod list only at start.'), parent=self.root)
            return
        name = os.path.basename(path)
        dest = os.path.join(self.mods_dir, name)
        try:
            if not same:
                if os.path.exists(dest):
                    backup = dest + '.backup'
                    if not os.path.exists(backup):
                        shutil.copy2(dest, backup)
                shutil.copy2(path, dest)
            registry_set(name, 1 if enable else 0)
        except OSError as e:
            self.status(tr('Install failed: {e}').format(e=e), error=True)
            return
        self.refresh()
        if self.tree.exists(name):
            self.tree.selection_set(name)
            self.tree.see(name)
        self.notebook.select(0)
        self.status(tr('{name} installed and enabled.').format(name=name) if enable
                    else tr('{name} installed, switch off.').format(name=name))

    def remove_mod(self):
        name = self._selected()
        if not name or name.startswith('ROOT::'):
            return
        if game_running():
            messagebox.showerror(APP_NAME, tr('Close Two Worlds first - it reads the mod list only at start.'), parent=self.root)
            return
        if not messagebox.askyesno(APP_NAME, tr('Move {name} to Mods\\_removed and switch it off?\nNothing is deleted.').format(name=name), parent=self.root):
            return
        path = os.path.join(self.mods_dir, name)
        if os.path.exists(path):
            keep = os.path.join(self.mods_dir, '_removed')
            os.makedirs(keep, exist_ok=True)
            target = os.path.join(keep, name)
            if os.path.exists(target):
                base, ext = os.path.splitext(name)
                i = 2
                while os.path.exists(os.path.join(keep, f'{base}_{i}{ext}')):
                    i += 1
                target = os.path.join(keep, f'{base}_{i}{ext}')
            shutil.move(path, target)
            registry_set(name, 0)
        else:
            registry_delete(name)          # "file missing" row: drop the stale switch
        self.refresh()
        self.status(tr('{name} moved to Mods\\_removed and disabled - nothing was deleted.').format(name=name))

    # ---- server ----
    def _fetch_catalog(self):
        self.status(tr('Loading mod list from the server...'))
        try:
            import urllib.request
            req = urllib.request.Request(MODS_URL, headers={'User-Agent': updater.USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as r:
                self.catalog = json.load(r)
        except Exception as exc:
            self.status(tr('Server list not available: {e}').format(e=exc), error=True)
            return
        self.status(tr('Mod list loaded - {n} mod(s).').format(n=len(self.catalog.get('mods', []))))
        self.root.after(0, self._refresh_server_states)

    def _refresh_server_states(self):
        if self.catalog is None or not hasattr(self, 'stree'):
            return
        self.stree.delete(*self.stree.get_children())
        reg = registry_mods()
        for mod in self.catalog.get('mods', []):
            local = os.path.join(self.mods_dir, mod['file'])
            if not os.path.exists(local):
                state, tag = tr('not installed'), 'get'
            elif mod.get('sha256') and sha256_of(local) != mod['sha256']:
                state, tag = tr('update available'), 'get'
            elif reg.get(mod['file'], 0):
                state, tag = tr('installed · enabled'), 'on'
            else:
                state, tag = tr('installed · disabled'), 'off'
            self.stree.insert('', 'end', iid=mod['id'], text=mod['name'],
                              values=(mod.get('version', ''), fmt_size(mod.get('size', 0)), state), tags=(tag,))

    def _show_desc(self):
        sel = self.stree.selection()
        if not sel or self.catalog is None:
            return
        mod = next((m for m in self.catalog['mods'] if m['id'] == sel[0]), None)
        if mod:
            author = f'  —  {mod["author"]}' if mod.get('author') else ''
            self.lbl_desc.configure(text=mod.get('description', '') + author)

    def install_server_mod(self):
        sel = self.stree.selection()
        if not sel or self.catalog is None:
            return
        mod = next((m for m in self.catalog['mods'] if m['id'] == sel[0]), None)
        if mod is None:
            return
        if game_running():
            messagebox.showerror(APP_NAME, tr('Close Two Worlds first - it reads the mod list only at start.'), parent=self.root)
            return
        self.btn_install.state(['disabled'])
        threading.Thread(target=self._download, args=(mod,), daemon=True).start()

    def _download(self, mod):
        try:
            import urllib.request
            dest = os.path.join(self.mods_dir, mod['file'])
            tmp = dest + '.download'
            self.status(tr('Downloading {name}...').format(name=mod['name']))
            req = urllib.request.Request(mod['url'], headers={'User-Agent': updater.USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, 'wb') as f:
                total = mod.get('size') or 0
                done = 0
                while True:
                    chunk = r.read(1 << 18)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        self.status(tr('Downloading {name}... {p}%').format(name=mod['name'], p=done * 100 // total))
            if mod.get('sha256'):
                got = sha256_of(tmp)
                if got != mod['sha256']:
                    os.remove(tmp)
                    raise ValueError(tr('checksum mismatch - download discarded'))
            if os.path.exists(dest):
                backup = dest + '.backup'
                if not os.path.exists(backup):
                    shutil.copy2(dest, backup)
            os.replace(tmp, dest)
            registry_set(mod['file'], 1)
            self.status(tr('{name} installed and enabled.').format(name=mod['name']))
        except Exception as exc:
            self.status(tr('Install failed: {e}').format(e=exc), error=True)
        finally:
            self.root.after(0, lambda: (self.btn_install.state(['!disabled']), self.refresh()))

    # ---- updates ----
    def _toggle_update_check(self):
        self.cfg['update_check'] = bool(self.update_var.get())
        self.cfg.save()

    def check_updates(self, manual=False):
        results = []
        updater.check_async(lambda info, err: results.append((info, err)))

        def poll():
            try:
                if not self.root.winfo_exists():
                    return
            except tk.TclError:
                return
            if not results:
                self.root.after(200, poll)
                return
            info, err = results[0]
            if err is not None or info is None:
                if manual:
                    messagebox.showwarning(tr('Update'), tr('GitHub was not reachable: {err}').format(err=err), parent=self.root)
                return
            if not updater.is_newer(info['tag']):
                if manual:
                    messagebox.showinfo(tr('Update'), tr('You have the latest version ({version}).').format(version=VERSION), parent=self.root)
                return
            if not manual and self.cfg.get('update_skip') == info['tag']:
                return
            self.set_hint(tr('Update available: version {version}').format(version=info['version']))
            UpdateWindow(self, info)
        self.root.after(200, poll)

    def show_about(self):
        win = tk.Toplevel(self.root)
        win.title(tr('About'))
        win.configure(background=theme.BG)
        win.transient(self.root)
        theme.dark_titlebar(win)
        win.bind('<Escape>', lambda e: win.destroy())
        f = ttk.Frame(win, padding=16)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text=f'TW1 Mod Manager {VERSION}', style='Brand.TLabel').pack(anchor='w')
        ttk.Label(f, text=tr('Installs, enables and disables mods of Two Worlds 1 - the .wd archives in\nthe Mods folder and their switches in the registry. Nothing is ever deleted.\nRegistry logic after buglord\'s Mod Selector.'),
                  style='Muted.TLabel', justify='left').pack(anchor='w', pady=(6, 10))
        for name, url in LINKS:
            lnk = ttk.Label(f, text=f'{name}: {url}', style='Link.TLabel', cursor='hand2')
            lnk.pack(anchor='w', padx=(12, 0))
            lnk.bind('<Button-1>', lambda e, u=url: webbrowser.open(u))
        ttk.Button(f, text=tr('Close'), command=win.destroy).pack(anchor='e', pady=(12, 0))

    def run(self):
        self._bind_keys()
        self.root.mainloop()


class UpdateWindow:
    """A newer release exists: notes, update now, later, skip (design 9)."""

    def __init__(self, app, info):
        self.app = app
        self.info = info
        self.win = tk.Toplevel(app.root)
        self.win.title(tr('Update'))
        self.win.transient(app.root)
        self.win.geometry('620x480')
        theme.dark_titlebar(self.win)
        self.win.bind('<Escape>', lambda e: self.win.destroy())
        f = ttk.Frame(self.win, padding=16)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text=tr('Version {version} is out').format(version=info['version']), style='Brand.TLabel').pack(anchor='w')
        ttk.Label(f, text=tr('You have {current}. The update downloads the exe from GitHub, checks its SHA-256 checksum, closes the tool and starts version {version}. The old exe stays as .old until the next start.').format(current=VERSION, version=info['version']),
                  style='Muted.TLabel', wraplength=580, justify='left').pack(anchor='w', pady=(2, 8))
        txt = tk.Text(f, wrap='word', font=theme.FONT, height=12)
        txt.pack(fill='both', expand=True)
        txt.insert('1.0', info['notes'].split(chr(10) + '---')[0].strip() or info['page'])
        txt.configure(state='disabled')
        self.status = ttk.Label(f, text='', style='Muted.TLabel', wraplength=580, justify='left')
        self.status.pack(anchor='w', pady=(8, 0))
        self.bar = ttk.Progressbar(f, maximum=100)
        btns = ttk.Frame(f)
        btns.pack(fill='x', side='bottom', pady=(10, 0))
        ttk.Button(btns, text=tr('Later'), command=self.win.destroy).pack(side='right')
        ttk.Button(btns, text=tr('Skip this version'), command=self.skip).pack(side='right', padx=6)
        self.exe = updater.frozen_exe()
        self.go = ttk.Button(btns, text=tr('Update now') if self.exe else tr('Open release page'),
                             style='Accent.TButton', command=self.start)
        self.go.pack(side='right')
        ttk.Button(btns, text=tr('View on GitHub'), command=lambda: webbrowser.open(info['page'])).pack(side='left')
        if self.exe and not info.get('sha256'):
            self.status.configure(text=tr('This release has no checksum. Without one the tool installs nothing; Update now opens the release page.'))

    def skip(self):
        self.app.cfg['update_skip'] = self.info['tag']
        self.app.cfg.save()
        self.win.destroy()

    def start(self):
        if not self.exe or not self.info.get('sha256') or not self.info.get('url'):
            webbrowser.open(self.info['page'])
            if not self.exe:
                self.win.destroy()
            return
        self.go.state(['disabled'])
        self.bar.pack(fill='x', pady=(6, 0), before=self.status)
        self.status.configure(text=tr('Downloading ...'))
        new = self.exe + '.new'
        state = {}

        def progress(done, total):
            state['p'] = (done, total)

        def work():
            try:
                updater.download(self.info, new, progress)
                state['ok'] = True
            except Exception as e:
                state['err'] = e
        threading.Thread(target=work, daemon=True).start()

        def poll():
            try:
                if not self.win.winfo_exists():
                    return
            except tk.TclError:
                return
            done, total = state.get('p', (0, 0))
            if total:
                self.bar.configure(value=100 * done / total)
                self.status.configure(text=tr('Downloading {done} of {total} MB ...').format(done=done // 1048576, total=max(1, total // 1048576)))
            if 'err' in state:
                self.go.state(['!disabled'])
                self.status.configure(text=tr('Update failed, nothing was changed: {err}').format(err=state['err']))
                return
            if not state.get('ok'):
                self.win.after(150, poll)
                return
            self.status.configure(text=tr('Checksum matches. The tool closes and starts the new version.'))
            try:
                updater.start_swap(self.exe, new)
            except OSError as e:
                self.status.configure(text=tr('Update failed, nothing was changed: {err}').format(err=e))
                self.go.state(['!disabled'])
                return
            self.win.after(600, self.app.root.destroy)
        poll()


# --------------------------------------------------------------- Deutsch --

DE = {
    'File': 'Datei', 'View': 'Ansicht', 'Help': 'Hilfe', 'Add external mod...': 'Mod-Datei einlegen...',
    'Open Mods folder': 'Mods-Ordner oeffnen', 'Refresh': 'Neu lesen', 'Change game path...': 'Spielordner aendern...',
    'Exit': 'Beenden', 'Installed mods': 'Eingelegte Mods', 'My Mods (server)': 'Mods vom Server', 'Language': 'Sprache',
    'Guide': 'Guide', 'Start tour': 'Rundgang starten', 'Documentation': 'Dokumentation',
    'Check for updates': 'Nach Updates suchen', 'Check for updates on start': 'Beim Start nach Updates suchen',
    'Latest version on GitHub': 'Neueste Version auf GitHub', 'About': 'Ueber', 'Close': 'Schliessen',
    'ready': 'bereit', 'Mods of Two Worlds': 'Mods von Two Worlds', 'no game folder': 'kein Spielordner',
    'The game folder: saved choice, then the registry, then Steam. File > Change game path if it is wrong.':
        'Der Spielordner: gespeicherte Wahl, dann Registry, dann Steam. Datei > Spielordner aendern, wenn er falsch ist.',
    'Archives in the Mods folder': 'Archive im Mods-Ordner',
    'One row per .wd with its registry switch. Double-click toggles. Red rows sit in the game folder and always load.':
        'Eine Zeile je .wd mit ihrem Registry-Schalter. Doppelklick schaltet um. Rote Zeilen liegen im Spielordner und laden immer.',
    'Mod archive': 'Mod-Archiv', 'State': 'Zustand', 'Size': 'Groesse', 'Changed': 'Geaendert',
    'Enable / disable': 'Ein / aus', 'Remove (keeps a copy)': 'Entfernen (Kopie bleibt)',
    'Drop a .wd file anywhere on this window to install it.': 'Eine .wd-Datei irgendwo auf dieses Fenster ziehen, um sie einzulegen.',
    'Double-click toggles a mod. Disabling keeps the file and sets its registry switch to 0 - exactly what the in-game Mod Selector does. Changes apply on the next game start.':
        'Doppelklick schaltet eine Mod um. Ausschalten behaelt die Datei und setzt ihren Registry-Schalter auf 0 - genau wie der Mod Selector des Spiels. Aenderungen gelten ab dem naechsten Spielstart.',
    'Verified mods from alchemy-fox.de': 'Gepruefte Mods von alchemy-fox.de',
    'mods.json from the community server: name, version, SHA-256. Install downloads, verifies and enables.':
        'mods.json vom Community-Server: Name, Version, SHA-256. Install laedt, prueft und schaltet ein.',
    'Mod': 'Mod', 'Version': 'Version', 'On this PC': 'Auf diesem PC', 'Install / update': 'Installieren / aktualisieren',
    'Reload list': 'Liste neu laden',
    'Downloads are checksum-verified; existing archives get a .backup copy before an update.':
        'Downloads werden per Pruefsumme geprueft; vorhandene Archive bekommen vor einem Update eine .backup-Kopie.',
    'Drop a .wd anywhere on this window to install it.': 'Eine .wd irgendwo auf dieses Fenster ziehen, um sie einzulegen.',
    'Drag and drop not available: {e}': 'Ziehen und Ablegen nicht verfuegbar: {e}',
    'Two Worlds install not found automatically.\nPlease pick your Two Worlds folder (the one with WDFiles).':
        'Two Worlds wurde nicht automatisch gefunden.\nBitte den Two-Worlds-Ordner waehlen (der mit WDFiles).',
    'Select your Two Worlds folder (the one containing WDFiles)': 'Two-Worlds-Ordner waehlen (der mit WDFiles)',
    'That folder has no WDFiles\\Update16.wd:\n{p}\n\nPick the Two Worlds install folder itself.':
        'In diesem Ordner liegt keine WDFiles\\Update16.wd:\n{p}\n\nDen Spielordner selbst waehlen.',
    'Game path set: {p}': 'Spielordner gesetzt: {p}', 'file missing': 'Datei fehlt', 'enabled': 'ein', 'disabled': 'aus',
    '(in the game folder!)': '(im Spielordner!)', 'ALWAYS loads': 'laedt IMMER', '{n} archives, {m} enabled': '{n} Archive, {m} eingeschaltet',
    'Show in Explorer': 'Im Explorer zeigen',
    'Archives in the game folder ignore the registry - the game loads them no matter what. Move the file out of the game folder to disable it.':
        'Archive im Spielordner ignorieren die Registry - das Spiel laedt sie immer. Zum Ausschalten die Datei aus dem Spielordner bewegen.',
    'Close Two Worlds first - it reads the mod list only at start.': 'Erst Two Worlds beenden - es liest die Mod-Liste nur beim Start.',
    '{name} {state} - takes effect on the next game start.': '{name} {state} - gilt ab dem naechsten Spielstart.',
    'Choose a mod archive': 'Mod-Archiv waehlen', 'Two Worlds mod': 'Two-Worlds-Mod',
    'Only .wd archives can be installed ({n} other file(s) ignored).': 'Nur .wd-Archive lassen sich einlegen ({n} andere Datei(en) ignoriert).',
    'Install failed: {e}': 'Einlegen fehlgeschlagen: {e}', '{name} installed and enabled.': '{name} eingelegt und eingeschaltet.',
    '{name} installed, switch off.': '{name} eingelegt, Schalter aus.',
    'Move {name} to Mods\\_removed and switch it off?\nNothing is deleted.': '{name} nach Mods\\_removed verschieben und ausschalten?\nNichts wird geloescht.',
    '{name} moved to Mods\\_removed and disabled - nothing was deleted.': '{name} nach Mods\\_removed verschoben und ausgeschaltet - nichts wurde geloescht.',
    'Loading mod list from the server...': 'Lade Mod-Liste vom Server...', 'Server list not available: {e}': 'Serverliste nicht erreichbar: {e}',
    'Mod list loaded - {n} mod(s).': 'Mod-Liste geladen - {n} Mod(s).', 'not installed': 'nicht eingelegt',
    'update available': 'Update verfuegbar', 'installed · enabled': 'eingelegt · ein', 'installed · disabled': 'eingelegt · aus',
    'Downloading {name}...': 'Lade {name}...', 'Downloading {name}... {p}%': 'Lade {name}... {p}%',
    'checksum mismatch - download discarded': 'Pruefsumme falsch - Download verworfen',
    'Load this mod?': 'Diese Mod laden?', '{n} files': '{n} Dateien', 'Contains: ': 'Enthaelt: ',
    'This file already sits in the Mods folder - only the switch changes.': 'Diese Datei liegt schon im Mods-Ordner - nur der Schalter aendert sich.',
    'Identical to the copy already in the Mods folder.': 'Identisch mit der Kopie im Mods-Ordner.',
    'Replaces the existing {name} - the old one is kept as .backup.': 'Ersetzt die vorhandene {name} - die alte bleibt als .backup.',
    'Not a Two Worlds mod archive. Nothing will be installed.': 'Kein Two-Worlds-Mod-Archiv. Es wird nichts eingelegt.',
    'Two Worlds is running - close it first, it reads the mod list only at start.': 'Two Worlds laeuft - erst beenden, es liest die Mod-Liste nur beim Start.',
    'Cancel': 'Abbrechen', 'Install only': 'Nur einlegen', 'Install and enable': 'Einlegen und einschalten',
    'not a Two Worlds WD archive (wrong header)': 'kein WD-Archiv von Two Worlds (falscher Kopf)',
    'archive could not be read: {e}': 'Archiv nicht lesbar: {e}',
    'Tour': 'Rundgang', "Don't show at startup": 'Beim Start nicht mehr anzeigen', 'Back': 'Zurueck', 'Next': 'Weiter',
    'Finish': 'Fertig', 'Quit tour': 'Rundgang beenden', 'Step {n} of {m}': 'Schritt {n} von {m}',
    'Welcome': 'Willkommen', 'Drop a mod here': 'Mod hier ablegen', 'Buttons': 'Knoepfe', 'Server': 'Server',
    'Two Worlds loads mods from the Mods folder next to the game and reads in the registry which of them are on. This window shows both, switches them, installs new ones and fetches verified mods from the community server. Nothing is ever deleted.':
        'Two Worlds laedt Mods aus dem Mods-Ordner neben dem Spiel und liest in der Registry, welche davon an sind. Dieses Fenster zeigt beides, schaltet um, legt neue ein und holt gepruefte Mods vom Community-Server. Nichts wird je geloescht.',
    'Every .wd in the Mods folder with its switch, size and date. Double-click or Enter toggles a mod. Red rows are archives in the game folder itself - those always load.':
        'Jede .wd im Mods-Ordner mit Schalter, Groesse und Datum. Doppelklick oder Enter schaltet um. Rote Zeilen sind Archive im Spielordner selbst - die laden immer.',
    'Drag a .wd from Explorer anywhere onto this window. A dialog shows what is inside and asks: install and enable, install only, or cancel. Ctrl+O does the same through a file dialog.':
        'Eine .wd aus dem Explorer irgendwo auf dieses Fenster ziehen. Ein Fenster zeigt, was drin ist, und fragt: einlegen und einschalten, nur einlegen, oder abbrechen. Strg+O macht dasselbe ueber den Dateidialog.',
    'Enable / disable flips the registry switch. Remove moves the archive to Mods\\_removed and keeps it. Changes count from the next game start.':
        'Ein / aus kippt den Registry-Schalter. Entfernen verschiebt das Archiv nach Mods\\_removed und behaelt es. Aenderungen gelten ab dem naechsten Spielstart.',
    'The second tab lists verified mods from alchemy-fox.de. Install downloads, checks the SHA-256 and enables the mod; an existing archive is kept as .backup.':
        'Der zweite Reiter zeigt gepruefte Mods von alchemy-fox.de. Install laedt, prueft die SHA-256 und schaltet ein; ein vorhandenes Archiv bleibt als .backup.',
    'F1 opens the guide with chapters, search and the registry reference. The gold ? marks jump straight to the matching chapter. Help also checks for updates.':
        'F1 oeffnet den Guide mit Kapiteln, Suche und der Registry-Referenz. Die goldenen ?-Marken springen direkt ins passende Kapitel. Hilfe sucht auch nach Updates.',
    'Update': 'Update', 'GitHub was not reachable: {err}': 'GitHub war nicht erreichbar: {err}',
    'You have the latest version ({version}).': 'Du hast die neueste Version ({version}).',
    'Update available: version {version}': 'Update verfuegbar: Version {version}', 'Version {version} is out': 'Version {version} ist da',
    'You have {current}. The update downloads the exe from GitHub, checks its SHA-256 checksum, closes the tool and starts version {version}. The old exe stays as .old until the next start.':
        'Du hast {current}. Das Update laedt die Exe von GitHub, prueft ihre SHA-256-Pruefsumme, schliesst das Tool und startet Version {version}. Die alte Exe bleibt bis zum naechsten Start als .old liegen.',
    'Later': 'Spaeter', 'Skip this version': 'Diese Version ueberspringen', 'Update now': 'Jetzt aktualisieren',
    'Open release page': 'Release-Seite oeffnen', 'View on GitHub': 'Auf GitHub ansehen', 'Downloading ...': 'Lade ...',
    'Downloading {done} of {total} MB ...': 'Lade {done} von {total} MB ...',
    'Update failed, nothing was changed: {err}': 'Update fehlgeschlagen, nichts wurde geaendert: {err}',
    'Checksum matches. The tool closes and starts the new version.': 'Pruefsumme stimmt. Das Tool schliesst sich und startet die neue Version.',
    'This release has no checksum. Without one the tool installs nothing; Update now opens the release page.':
        'Dieses Release hat keine Pruefsumme. Ohne Pruefsumme installiert das Tool nichts; Jetzt aktualisieren oeffnet die Release-Seite.',
    'Installs, enables and disables mods of Two Worlds 1 - the .wd archives in\nthe Mods folder and their switches in the registry. Nothing is ever deleted.\nRegistry logic after buglord\'s Mod Selector.':
        'Legt Mods von Two Worlds 1 ein, schaltet sie ein und aus - die .wd-Archive im\nMods-Ordner und ihre Schalter in der Registry. Nichts wird je geloescht.\nRegistry-Logik nach buglords Mod Selector.',
}


def _check_translations():
    guidebook.check_sources()
    for k in list(DE):
        assert set(re.findall(r'\{\w+\}', k)) == set(re.findall(r'\{\w+\}', DE[k])), k
    for s in GUIDE_STEPS:
        assert s['text'] in DE and s['title'] in DE, s['title']


def run_gui(pending=None):
    carry = {'pending': pending} if pending else None
    while True:
        app = App(carry)
        app.run()
        if not app.restart:
            break
        carry = getattr(app, 'carry_out', None) or {}


if __name__ == '__main__':
    _check_translations()
    run_gui([a for a in sys.argv[1:] if a.lower().endswith('.wd') and os.path.isfile(a)])
