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
# Community archive on GitHub: one .wd per mod, a .wd.txt next to it with the
# description. The API lists the folder with size and git blob sha per file,
# which doubles as the checksum after download.
GH_API = 'https://api.github.com/repos/InsideTwoWorlds/MODs/contents/Two%20Worlds'
GH_PAGE = 'https://github.com/InsideTwoWorlds/MODs/tree/main/Two%20Worlds'
GH_NAME = 'InsideTwoWorlds / MODs'
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


def git_blob_sha1(path):
    """The sha GitHub shows for a file: sha1 of 'blob <size>\\0' + content."""
    h = hashlib.sha1()
    h.update(f'blob {os.path.getsize(path)}'.encode() + b'\x00')
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def http_get(url, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers={'User-Agent': updater.USER_AGENT,
                                               'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download_file(url, dest, size=0, sha256=None, blob_sha1=None, progress=None):
    """Download to dest + '.download', verify (SHA-256 or git blob sha1), then
    move into place; an existing dest is kept as .backup once. Raises on a
    checksum mismatch and removes the download."""
    import urllib.request
    tmp = dest + '.download'
    req = urllib.request.Request(url, headers={'User-Agent': updater.USER_AGENT})
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, 'wb') as f:
        while True:
            chunk = r.read(1 << 18)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, size)
    if sha256 and sha256_of(tmp) != sha256:
        os.remove(tmp)
        raise ValueError(tr('checksum mismatch - download discarded'))
    if blob_sha1 and git_blob_sha1(tmp) != blob_sha1:
        os.remove(tmp)
        raise ValueError(tr('checksum mismatch - download discarded'))
    if os.path.exists(dest):
        backup = dest + '.backup'
        if not os.path.exists(backup):
            shutil.copy2(dest, backup)
    os.replace(tmp, dest)
    return dest


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
    ``callback(list_of_paths)`` on the Tk thread.

    A window procedure runs inside Windows' message handling and therefore
    does no Tk call at all: it only fills an inbox that Tk empties from its
    own event loop. Calling Tk from inside it ended the process without a
    word on a real drop from Explorer (measured on the WD Packer,
    19.09.2026); a simulated message never hits that, because it comes from
    the Tk thread itself."""
    if getattr(root, '_drop_procs', None):
        # Registering twice would free the first callbacks while the windows
        # still point at them - the second proc then calls freed memory.
        return len(root._drop_procs)
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
    inbox = []                                 # paths a window procedure collected

    def pump():
        while inbox:
            callback(inbox.pop(0))
        try:
            root.after(120, pump)
        except Exception:
            pass                               # window gone

    def make(h):
        def proc(hwnd, msg, wp, lp):
            if msg == WM_DROPFILES:
                try:
                    n = shell32.DragQueryFileW(wp, 0xFFFFFFFF, None, 0)
                    files = []
                    for i in range(n):
                        ln = shell32.DragQueryFileW(wp, i, None, 0)
                        buf = ctypes.create_unicode_buffer(ln + 1)
                        shell32.DragQueryFileW(wp, i, buf, ln + 1)
                        files.append(buf.value)
                    shell32.DragFinish(wp)
                    inbox.append(files)     # no Tk call inside a window procedure
                except Exception:
                    pass
                return 0
            return user32.CallWindowProcW(olds[h], hwnd, msg, wp, lp)
        return _WNDPROC(proc)
    for h in hwnds:
        shell32.DragAcceptFiles(h, True)
        cb = make(h)
        procs.append(cb)                       # keep the callbacks alive
        olds[h] = user32.SetWindowLongPtrW(h, GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p).value)
    root._drop_procs = procs
    _DROP_KEEP.append(procs)                   # and never let them be collected
    root.after(120, pump)
    return len(hwnds)


_DROP_KEEP = []


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
     'The second tab lists verified mods from alchemy-fox.de, the third the community archive '
     'of InsideTwoWorlds on GitHub with a description per mod. Install downloads, checks the '
     'checksum and enables the mod; an existing archive is kept as .backup.'},
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
        self.github = None            # [{name, size, sha, url, txt_url}] once fetched
        self.gh_desc = {}             # name -> description text (from <name>.txt)
        self.update_var = tk.BooleanVar(value=bool(self.cfg.get('update_check', True)))
        self.cache_var = tk.BooleanVar(value=bool(self.cfg.get('auto_level_cache', True)))
        self.guide = Guide(self)
        self.tr = tr
        self.APP_NAME = APP_NAME
        self.data_dir = data_dir
        self.registry_set = lambda name, value: registry_set(name, value)
        self.registry_delete = lambda name: registry_delete(name)
        self.merging = False
        import mergeui
        self.insight = mergeui.Insight(self)

        self.game_dir = find_game_dir(self.cfg.get('game_dir'))
        self.mods_dir = os.path.join(self.game_dir, 'Mods') if self.game_dir else None
        if self.mods_dir:
            os.makedirs(self.mods_dir, exist_ok=True)

        self._init_feedback()
        self.build()
        self.place_window()
        self.root.deiconify()
        self.root.after(200, self._startup)

    def _init_feedback(self):
        """Help > Test what is untested / Known issues / Report a bug, and the
        "new in this version" window (skill tw1-testfenster). Nothing is
        sent without the preview window and its button."""
        import foxfeedback_ui
        base = getattr(sys, '_MEIPASS', HERE)

        def cfg_set(key, value):
            self.cfg[key] = value
            self.cfg.save()
        self.fb = foxfeedback_ui.FeedbackUI(
            self.root, 'modmanager', VERSION,
            cfg_get=lambda k, d=None: self.cfg.get(k, d), cfg_set=cfg_set,
            lang=_LANG, tests_file=os.path.join(base, 'untested.json'),
            open_guide=self.show_guide, tool_name='TW1 Mod Manager',
            launcher=foxfeedback_ui.tw1_launcher(self.game_dir) if self.game_dir else None)
        self.root.report_callback_exception = self._crash

    def error(self, key, message, shown, guide=None):
        """Error window with "Report a bug". ``key`` is a stable id, ``message``
        the fixed English text of the error - both become the public title,
        so they never carry names or paths. ``shown`` is what the user reads."""
        ErrorDialog(self, key, message, shown, guide)

    def _crash(self, exc, val, tb):
        import traceback
        frames = traceback.extract_tb(tb)
        mine = [f for f in frames if os.path.dirname(os.path.abspath(f.filename)) in (HERE, getattr(sys, '_MEIPASS', HERE))]
        where = mine[-1] if mine else (frames[-1] if frames else None)
        spot = f'{os.path.basename(where.filename)}:{where.lineno}' if where else '?'
        shown = ''.join(traceback.format_exception(exc, val, tb))[-3000:]
        try:
            self.fb.log.add(f'crash {exc.__name__} at {spot}')
            ErrorDialog(self, 'crash', f'{exc.__name__} at {spot}', shown, None,
                        title='crash: ' + exc.__name__)
        except Exception:
            sys.__excepthook__(exc, val, tb)

    def help_mark(self, parent, text, chapter):
        return help_mark(parent, text, chapter, self)

    def _icon(self):
        base = getattr(sys, '_MEIPASS', HERE)
        ico = os.path.join(base, 'mod_manager.ico')
        if os.path.exists(ico):
            try:
                self.root.iconbitmap(default=ico)       # every window of the tool, not only the first
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
        if getattr(self, '_started', False):
            return
        self._started = True
        updater.cleanup_old()
        if not self.selftest:
            self.root.after(1500, self._startup_cache)
            self.root.after(2500, self.fb.start)
        self.root.protocol('WM_DELETE_WINDOW', self._close)
        try:                                   # a merge that was killed half way
            for f in os.listdir(self.mods_dir or ''):
                if f.lower().endswith('.mmtmp'):
                    os.remove(os.path.join(self.mods_dir, f))
        except OSError:
            pass
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
        threading.Thread(target=self._fetch_github, daemon=True).start()
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

    def _selftest_merge(self):
        """Frozen build probe: two made-up mods merged in a temp folder, the
        game's files read, the SDK field table found, a cache planned."""
        if not self.game_dir:
            return 'nogame'
        import tempfile
        import zlib
        import fieldnames
        import levelcache
        import merger
        import modscan
        tmp = tempfile.mkdtemp(prefix='mm_selftest_')
        try:
            retail = modscan.Retail(self.game_dir)
            qtx = retail.get(modscan.QTX)
            if not qtx:
                return 'noqtx'
            meta = {'flags': 1, 'res': None, 'id': None, 'guid': None}
            mods = []
            end = bytes([10]) + b'END' + bytes([10])
            for n, extra in enumerate((b'QUEST Q_398 0 (null) (null) 0 True' + end,
                                       b'QUEST Q_399 0 (null) (null) 0 True' + end)):
                data = qtx + extra
                path = os.path.join(tmp, f'm{n}.wd')
                merger.write_wd(path, [(dict(meta, path=SEP.join(('Scripts', 'Quests', 'TwoWorldsQuests.qtx')), rlen=len(data)),
                                        zlib.compress(data))])
                mods.append(path)
            m = merger.Merge(mods, retail)
            out = os.path.join(tmp, 'out.wd')
            m.build(out)
            units = modscan.qtx_units(modscan.archive(out).get(modscan.QTX).decode('latin-1'))[1]
            ok = 'QUEST Q_398' in units and 'QUEST Q_399' in units and m.level == 'green'
            names = fieldnames.field_name('MO_WOLF_01', 6)
            maps = levelcache.plan(self.game_dir, [])[1]['maps']
            return f"{'ok' if ok else 'WRONG'}/field6={names}/maps={maps}"
        except Exception as e:
            return f'failed:{type(e).__name__}:{e}'
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _run_selftest(self, note=''):
        try:
            https = 'ok'
            try:
                import http.client  # noqa: F401
                import ssl  # noqa: F401
                import urllib.request  # noqa: F401
            except ImportError as e:
                https = f'missing:{e.name}'
            merge = self._selftest_merge()
            with open(self.selftest, 'w', encoding='utf-8') as f:
                f.write(f'version={VERSION} game={bool(self.game_dir)} mods={len(self.tree.get_children()) if self.game_dir else 0} '
                        f'merge={merge} '
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
        self.notebook.add(self._tab_github(self.notebook), text='  ' + tr('Community (GitHub)') + '  ')
        import mergeui
        self.merge_tab = mergeui.MergeTab(self, self.notebook)
        self.notebook.add(self.merge_tab.frame, text='  ' + tr('Merge mods') + '  ')

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
        m.add_command(label=tr('Rebuild level cache now'), command=lambda: self.sync_level_cache(manual=True),
                      state='normal' if self.mods_dir else 'disabled')
        m.add_checkbutton(label=tr('Keep level cache in step with the mods'), variable=self.cache_var,
                          command=self._toggle_auto_cache)
        m.add_separator()
        m.add_command(label=tr('Change game path...'), command=self.change_game_path)
        m.add_separator()
        m.add_command(label=tr('Exit'), accelerator='Alt+F4', command=self.root.destroy)

    def _fill_view(self, m):
        m.add_command(label=tr('Installed mods'), command=lambda: self.notebook.select(0))
        m.add_command(label=tr('My Mods (server)'), command=lambda: self.notebook.select(1))
        m.add_command(label=tr('Community (GitHub)'), command=lambda: self.notebook.select(2))
        m.add_command(label=tr('Merge mods'), command=lambda: self.notebook.select(3))
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
        self.fb.add_menu_items(m)
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
        self.tree = ttk.Treeview(tab, columns=('state', 'size', 'date', 'fit'), show='tree headings')
        self.tree.heading('#0', text=tr('Mod archive'))
        self.tree.heading('state', text=tr('State'))
        self.tree.heading('size', text=tr('Size'))
        self.tree.heading('date', text=tr('Changed'))
        self.tree.column('#0', width=300)
        self.tree.column('state', width=120, anchor='center')
        self.tree.column('size', width=90, anchor='e')
        self.tree.column('date', width=130, anchor='center')
        self.tree.heading('fit', text=tr('With the selected mod'))
        self.tree.column('fit', width=170, anchor='center')
        self.tree.pack(fill='both', expand=True)
        import mergeui
        mergeui.configure_tags(self.tree)
        mergeui.RowTips(self.tree, self._row_tip)
        self.tree.bind('<<TreeviewSelect>>', lambda ev: self._colour_fit(), add='+')
        self.insight.on_ready(self._colour_fit)
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

    def _tab_github(self, parent):
        tab = ttk.Frame(parent, padding=12)
        hdr = ttk.Frame(tab)
        hdr.pack(fill='x', pady=(0, 4))
        ttk.Label(hdr, text=tr('Mods from the InsideTwoWorlds archive on GitHub'), style='Muted.TLabel').pack(side='left')
        help_mark(hdr, tr('The folder "Two Worlds" of github.com/InsideTwoWorlds/MODs: every .wd with its description. Install downloads the file and checks it against the checksum GitHub stores for it.'), 'server', self)
        lnk = ttk.Label(hdr, text=GH_NAME, style='Link.TLabel', cursor='hand2')
        lnk.pack(side='right')
        lnk.bind('<Button-1>', lambda e: webbrowser.open(GH_PAGE))
        self.gtree = ttk.Treeview(tab, columns=('size', 'status'), show='tree headings')
        self.gtree.heading('#0', text=tr('Mod archive'))
        self.gtree.heading('size', text=tr('Size'))
        self.gtree.heading('status', text=tr('On this PC'))
        self.gtree.column('#0', width=360)
        self.gtree.column('size', width=90, anchor='e')
        self.gtree.column('status', width=160, anchor='center')
        self.gtree.pack(fill='both', expand=True)
        self.gtree.tag_configure('on', foreground=theme.OK)
        self.gtree.tag_configure('get', foreground=theme.GOLD)
        self.gtree.tag_configure('off', foreground=theme.MUT)
        self.gtree.bind('<<TreeviewSelect>>', lambda ev: self._show_gh_desc())
        self.gtree.bind('<Double-1>', lambda ev: self.install_github_mod())
        self.lbl_gdesc = ttk.Label(tab, style='Muted.TLabel', wraplength=860, justify='left')
        self.lbl_gdesc.pack(anchor='w', pady=(8, 0))
        btns = ttk.Frame(tab)
        btns.pack(fill='x', pady=(10, 0))
        self.btn_ginstall = ttk.Button(btns, text=tr('Install / update'), style='Accent.TButton',
                                       command=self.install_github_mod)
        self.btn_ginstall.pack(side='left')
        ttk.Button(btns, text=tr('Reload list'),
                   command=lambda: threading.Thread(target=self._fetch_github, daemon=True).start()
                   ).pack(side='left', padx=6)
        ttk.Button(btns, text=tr('Open on GitHub'), command=lambda: webbrowser.open(GH_PAGE)).pack(side='right')
        ttk.Label(tab, style='Muted.TLabel', wraplength=860, justify='left',
                  text=tr('Community mods collected by InsideTwoWorlds. Each download is checked against the file hash GitHub stores; existing archives get a .backup copy before an update. Read the description before you install.')
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
    def _close(self):
        if self.merging and not messagebox.askyesno(APP_NAME, tr('A merge is running. Close anyway? The unfinished file is removed at the next start.'), parent=self.root):
            return
        self.insight.stop()
        self.root.destroy()

    def set_lang(self, code):
        global _LANG
        if code == _LANG:
            return
        if self.merging:
            self.status(tr('Wait until the merge is done.'), error=True)
            return
        self.cfg['lang'] = code
        self.cfg.save()
        _LANG = code
        self.restart = True
        self.insight.stop()
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
            enabled = reg.get(name, 1)      # no switch in the registry = the game loads it
            if not exists:
                state, tag, size, date = tr('file missing'), 'off', '—', '—'
            else:
                st = os.stat(path)
                size = fmt_size(st.st_size)
                import datetime
                date = datetime.datetime.fromtimestamp(st.st_mtime).strftime('%d.%m.%Y %H:%M')
                state, tag = (tr('enabled'), 'on') if enabled else (tr('disabled'), 'off')
            self.tree.insert('', 'end', iid=name, text=name, values=(state, size, date, ''), tags=(tag,))
        for name in [f for f in os.listdir(self.game_dir) if f.lower().endswith('.wd')]:
            self.tree.insert('', 'end', iid='ROOT::' + name, text=name + '  ' + tr('(in the game folder!)'),
                             values=(tr('ALWAYS loads'), '', '', ''), tags=('warn',))
        self._row_tags = {iid: self.tree.item(iid, 'tags') for iid in self.tree.get_children()}
        self.insight.ensure([self._row_path(i) for i in self.tree.get_children()])
        if hasattr(self, 'merge_tab'):
            self.merge_tab.refresh()
        self._refresh_server_states()
        self._refresh_github_states()
        on = sum(1 for n in files if reg.get(n, 1))
        self.status(tr('{n} archives, {m} enabled').format(n=len(files), m=on))

    def _row_path(self, iid):
        if iid.startswith('ROOT::'):
            return os.path.join(self.game_dir, iid[6:])
        return os.path.join(self.mods_dir, iid)

    def _row_tip(self, iid):
        """Hover text: what the mod changes, and how it fits the selected one."""
        import modscan
        path = self._row_path(iid)
        if not os.path.isfile(path):
            return ''
        info = self.insight.info(path)
        if info is None:
            return tr('reading...')
        lines = list(modscan.summary_lines(info, tr=tr))
        sel = self._selected()
        if sel and sel != iid:
            c = self.insight.compare(path, self._row_path(sel))
            if c:
                lines += ['', tr('With {name}:').format(name=sel.replace('ROOT::', ''))] + modscan.compare_lines(c, tr)
        return chr(10).join(lines)

    def _colour_fit(self):
        """Rows take the colour of how they fit the selected mod: green no
        overlap inside files, yellow overlaps, red clashes."""
        import mergeui
        sel = self._selected()
        for iid in self.tree.get_children():
            base = getattr(self, '_row_tags', {}).get(iid, ())
            if not sel or iid == sel or not os.path.isfile(self._row_path(sel)):
                self.tree.item(iid, tags=base)
                self.tree.set(iid, 'fit', '')
                continue
            c = self.insight.compare(self._row_path(iid), self._row_path(sel)) \
                if os.path.isfile(self._row_path(iid)) else None
            if c is None:
                self.tree.item(iid, tags=base)
                self.tree.set(iid, 'fit', '')
                continue
            self.tree.item(iid, tags=(mergeui.LEVEL_TAG[c['level']],))
            self.tree.set(iid, 'fit', mergeui.fit_text(tr, c['level'], None))

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
        new = 0 if registry_mods().get(name, 1) else 1
        registry_set(name, new)
        self.refresh()
        self.tree.selection_set(name)
        self.status(tr('{name} {state} - takes effect on the next game start.').format(
            name=name, state=tr('enabled') if new else tr('disabled')) + '  ' + self.sync_level_cache(checked=True))

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
            self.fb.log.add('install failed')
            self.error('install.failed', 'Installing a mod archive failed', tr('Install failed: {e}').format(e=e), 'install')
            return
        self.refresh()
        if self.tree.exists(name):
            self.tree.selection_set(name)
            self.tree.see(name)
        self.notebook.select(0)
        self.status((tr('{name} installed and enabled.').format(name=name) if enable
                     else tr('{name} installed, switch off.').format(name=name)) + '  ' + self.sync_level_cache())

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
        self.status(tr('{name} moved to Mods\\_removed and disabled - nothing was deleted.').format(name=name)
                    + '  ' + self.sync_level_cache())

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
            dest = os.path.join(self.mods_dir, mod['file'])
            self.status(tr('Downloading {name}...').format(name=mod['name']))
            download_file(mod['url'], dest, mod.get('size') or 0, sha256=mod.get('sha256'),
                          progress=lambda d, t: self.status(tr('Downloading {name}... {p}%').format(
                              name=mod['name'], p=d * 100 // t if t else 0)))
            registry_set(mod['file'], 1)
            self.status(tr('{name} installed and enabled.').format(name=mod['name']))
        except Exception as exc:
            self.status(tr('Install failed: {e}').format(e=exc), error=True)
        finally:
            self.root.after(0, lambda: (self.btn_install.state(['!disabled']), self.refresh()))

    # ---- GitHub archive (InsideTwoWorlds/MODs) ----
    def _fetch_github(self):
        self.status(tr('Loading the GitHub list...'))
        try:
            entries = json.loads(http_get(GH_API).decode('utf-8'))
            if not isinstance(entries, list):
                raise ValueError(entries.get('message', 'unexpected answer'))
            txts = {e['name']: e['download_url'] for e in entries if e['type'] == 'file' and e['name'].lower().endswith('.txt')}
            mods = []
            for e in entries:
                if e['type'] != 'file' or not e['name'].lower().endswith('.wd'):
                    continue
                mods.append({'name': e['name'], 'size': e['size'], 'sha': e['sha'], 'url': e['download_url'],
                             'txt_url': txts.get(e['name'] + '.txt') or txts.get(e['name'][:-3] + '.td.txt')})
            self.github = sorted(mods, key=lambda m: m['name'].lower())
        except Exception as exc:
            self.status(tr('GitHub list not available: {e}').format(e=exc), error=True)
            return
        self.status(tr('GitHub list loaded - {n} mod(s).').format(n=len(self.github)))
        self.root.after(0, self._refresh_github_states)

    def _refresh_github_states(self):
        if self.github is None or not hasattr(self, 'gtree'):
            return
        sel = self.gtree.selection()
        self.gtree.delete(*self.gtree.get_children())
        reg = registry_mods()
        for mod in self.github:
            local = os.path.join(self.mods_dir, mod['name'])
            if not os.path.exists(local):
                state, tag = tr('not installed'), 'get'
            elif os.path.getsize(local) != mod['size'] or git_blob_sha1(local) != mod['sha']:
                state, tag = tr('update available'), 'get'
            elif reg.get(mod['name'], 0):
                state, tag = tr('installed · enabled'), 'on'
            else:
                state, tag = tr('installed · disabled'), 'off'
            self.gtree.insert('', 'end', iid=mod['name'], text=mod['name'],
                              values=(fmt_size(mod['size']), state), tags=(tag,))
        if sel and self.gtree.exists(sel[0]):
            self.gtree.selection_set(sel[0])

    def _show_gh_desc(self):
        sel = self.gtree.selection()
        if not sel or self.github is None:
            return
        name = sel[0]
        mod = next((m for m in self.github if m['name'] == name), None)
        if mod is None:
            return
        if name in self.gh_desc:
            self.lbl_gdesc.configure(text=self.gh_desc[name])
            return
        if not mod['txt_url']:
            self.gh_desc[name] = tr('(no description in the archive)')
            self.lbl_gdesc.configure(text=self.gh_desc[name])
            return
        self.lbl_gdesc.configure(text=tr('loading description...'))

        def work():
            try:
                text = http_get(mod['txt_url']).decode('utf-8', 'replace').strip()
            except Exception as exc:
                text = tr('(description not available: {e})').format(e=exc)
            self.gh_desc[name] = text[:1200]

            def show():
                cur = self.gtree.selection()
                if cur and cur[0] == name:
                    self.lbl_gdesc.configure(text=self.gh_desc[name])
            self.root.after(0, show)
        threading.Thread(target=work, daemon=True).start()

    def install_github_mod(self):
        sel = self.gtree.selection()
        if not sel or self.github is None:
            return
        mod = next((m for m in self.github if m['name'] == sel[0]), None)
        if mod is None:
            return
        if game_running():
            messagebox.showerror(APP_NAME, tr('Close Two Worlds first - it reads the mod list only at start.'), parent=self.root)
            return
        self.btn_ginstall.state(['disabled'])

        def work():
            try:
                dest = os.path.join(self.mods_dir, mod['name'])
                self.status(tr('Downloading {name}...').format(name=mod['name']))
                download_file(mod['url'], dest, mod['size'], blob_sha1=mod['sha'],
                              progress=lambda d, t: self.status(tr('Downloading {name}... {p}%').format(
                                  name=mod['name'], p=d * 100 // t if t else 0)))
                registry_set(mod['name'], 1)
                self.status(tr('{name} installed and enabled.').format(name=mod['name']))
            except Exception as exc:
                self.status(tr('Install failed: {e}').format(e=exc), error=True)
            finally:
                self.root.after(0, lambda: (self.btn_ginstall.state(['!disabled']), self.refresh()))
        threading.Thread(target=work, daemon=True).start()

    # ---- updates ----
    def _toggle_auto_cache(self):
        self.cfg['auto_level_cache'] = bool(self.cache_var.get())
        self.cfg.save()
        if self.cache_var.get():
            self.sync_level_cache()

    def loaded_archives(self):
        """Archives the game will load: Mods\\*.wd unless their registry
        switch is 0 (no value = loaded), plus every .wd in the game folder."""
        reg = {k.lower(): v for k, v in registry_mods().items()}      # Windows names: case does not matter
        out = []
        if self.mods_dir and os.path.isdir(self.mods_dir):
            out += [os.path.join(self.mods_dir, f) for f in os.listdir(self.mods_dir)
                    if f.lower().endswith('.wd') and reg.get(f.lower(), 1)]
        if self.game_dir and os.path.isdir(self.game_dir):
            out += [os.path.join(self.game_dir, f) for f in os.listdir(self.game_dir)
                    if f.lower().endswith('.wd')]
        return out

    def _startup_cache(self):
        """The mod list may have changed outside the tool (in-game Mod Selector)."""
        try:
            note = self.sync_level_cache()
            if note:
                self.status(note)
        except tk.TclError:
            pass

    def sync_level_cache(self, manual=False, checked=False):
        """Rebuild <game>\\Levels\\Map_LevelHeaders.lhc for the archives the
        game will load (levelcache.py - what the SDK's LevelHeadersCacheGen.bat
        does, without the SDK). Returns the note for the status bar."""
        if not self.game_dir or not (manual or self.cfg.get('auto_level_cache', True)):
            return ''
        if not checked and game_running():
            if manual:
                messagebox.showerror(APP_NAME, tr('Close Two Worlds first - it reads the mod list only at start.'), parent=self.root)
            return tr('Level cache not rebuilt while the game runs.')
        try:
            import levelcache
            mods = self.loaded_archives()
            if not manual and not os.path.isfile(levelcache.target(self.game_dir)):
                # no loose cache yet: only make one when a loaded mod brings maps
                _blob, rep = levelcache.plan(self.game_dir, mods)
                if not rep['mod_maps']:
                    return ''
            changed, rep = levelcache.sync(self.game_dir, mods, os.path.join(data_dir(), 'backup'))
        except Exception as e:
            note = tr('Level cache not rebuilt: {e}').format(e=e)
            if manual:
                self.status(note, error=True)
            return note
        n = sum(len(v) for v in rep['mod_maps'].values())
        if rep.get('unreadable'):
            self._cache_unreadable = rep['unreadable']
        note = (tr('Level cache rebuilt ({maps} maps, {n} from mods).') if changed
                else tr('Level cache is up to date ({maps} maps, {n} from mods).')).format(maps=rep['maps'], n=n)
        if rep.get('broken'):
            note += ' ' + tr('Not readable, left out: {names}.').format(names=', '.join(rep['broken'][:3]))
        if rep.get('unreadable'):
            note += ' ' + tr('{k} map(s) of mods could not be read - the game keeps its own markers there.').format(
                k=len(rep['unreadable']))
        if rep['clashes']:
            note += ' ' + tr('{k} map(s) come from two mods - which one the game takes is not measured.').format(k=len(rep['clashes']))
        if manual:
            self.status(note)
        return note if (changed or manual or rep.get('broken')) else ''

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


class ErrorDialog:
    """An error the user can report: message, OK, "Report a bug", optional guide."""

    def __init__(self, app, key, message, shown, guide=None, title=None):
        self.win = win = tk.Toplevel(app.root)
        win.title(tr('Error'))
        win.transient(app.root)
        theme.dark_titlebar(win)
        win.bind('<Escape>', lambda e: win.destroy())
        f = ttk.Frame(win, padding=16)
        f.pack(fill='both', expand=True)
        box = tk.Text(f, wrap='word', height=min(14, max(3, shown.count(chr(10)) + 2 + len(shown) // 90)),
                      width=86, bg=theme.FIELD, fg=theme.INK, relief='flat', font=theme.FONT_MONO,
                      highlightthickness=0, padx=8, pady=6)
        box.insert('1.0', shown)
        box.configure(state='disabled')
        box.pack(fill='both', expand=True)
        btns = ttk.Frame(f)
        btns.pack(fill='x', pady=(12, 0))
        ttk.Button(btns, text='OK', style='Accent.TButton', command=win.destroy).pack(side='right')
        ttk.Button(btns, text=tr('Report a bug...'),
                   command=lambda: app.fb.report_bug(parent=win, error_text=shown, error_key=key,
                                                     title=title or f'{key}: {message}', fp_text=message)
                   ).pack(side='right', padx=6)
        if guide:
            ttk.Button(btns, text=tr('Read in the guide'),
                       command=lambda: app.show_guide(guide)).pack(side='left')
        win.update_idletasks()
        win.geometry(f'+{app.root.winfo_rootx() + 80}+{app.root.winfo_rooty() + 80}')
        win.focus_set()


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
    'Your mods are never changed; the result is a new archive.': 'Deine Mods werden nie veraendert; das Ergebnis ist ein neues Archiv.',
    'Help testing it': 'Beim Testen helfen',
    'Error': 'Fehler', 'Report a bug...': 'Bug melden...', 'Read in the guide': 'Im Guide nachlesen',
    'Level cache not rebuilt while the game runs.': 'Level-Cache nicht neu gebaut, solange das Spiel laeuft.',
    'Not readable, left out: {names}.': 'Nicht lesbar, ausgelassen: {names}.',
    'Wait until the merge is done.': 'Warte, bis das Zusammenfuehren fertig ist.',
    'A merge is running. Close anyway? The unfinished file is removed at the next start.':
        'Ein Zusammenfuehren laeuft. Trotzdem schliessen? Die unfertige Datei wird beim naechsten Start entfernt.',
    '{k} map(s) of mods could not be read - the game keeps its own markers there.':
        '{k} Karte(n) aus Mods nicht lesbar - dort behaelt das Spiel seine eigenen Marker.',
    'The game itself:': 'Das Spiel selbst:',
    'par: {a} fields changed, {b} entries new or rebuilt': 'Par: {a} Felder geaendert, {b} Eintraege neu oder umgebaut',
    'quest file: ': 'Questdatei: ', 'texts: {a} keys, {b} dialog trees': 'Texte: {a} Schluessel, {b} Dialogbaeume',
    'maps: ': 'Karten: ', "{n} file(s) identical to the game's": '{n} Datei(en) identisch mit dem Spiel',
    'not analysed': 'nicht untersucht', 'par: {n} fields changed by both': 'Par: {n} Felder von beiden geaendert',
    'quest file: {n} blocks changed by both': 'Questdatei: {n} Bloecke von beiden geaendert',
    'texts: {a} keys, {b} dialog trees changed by both': 'Texte: {a} Schluessel, {b} Dialogbaeume von beiden geaendert',
    'maps in both: ': 'Karten in beiden: ', '{n} other file(s) in both: ': '{n} weitere Datei(en) in beiden: ',
    'compiled scripts in both (cannot be merged): ': 'kompilierte Skripte in beiden (nicht teilbar): ',
    'quests': 'Quests', 'texts': 'Texte', 'par': 'Par', 'scripts': 'Skripte', 'maps': 'Karten', 'physics': 'Physik',
    'level cache': 'Level-Cache', 'meshes': 'Modelle', 'textures': 'Texturen', 'materials': 'Materialien',
    'sounds': 'Klaenge', 'images': 'Bilder', 'console': 'Konsole', 'animations': 'Animationen',
    'text files': 'Textdateien', 'characters': 'Charaktere', 'archives': 'Archive', 'minimap': 'Minimap',
    'Merge mods': 'Mods zusammenfuehren', 'With the selected mod': 'Mit der gewaehlten Mod',
    'With {name}:': 'Mit {name}:', 'reading...': 'lese...', 'fits': 'passt', 'overlaps': 'ueberschneidet sich',
    'clashes': 'kollidiert', 'no overlap inside files': 'keine Ueberschneidung innerhalb von Dateien',
    'Merge mods into one new mod': 'Mods zu einer neuen Mod zusammenfuehren',
    'Tick the mods to merge. Green: no overlap inside files. Yellow: a few overlaps or shared maps, each one is asked. Red: too many overlaps or compiled scripts - one mod has to be the main mod and wins every clash. The source mods are only read.':
        'Die Mods anhaken, die zusammen sollen. Gruen: keine Ueberschneidung innerhalb von Dateien. Gelb: wenige Ueberschneidungen oder gemeinsame Karten, jede wird gefragt. Rot: zu viele Ueberschneidungen oder kompilierte Skripte - eine Mod muss Haupt-Mod sein und gewinnt jede Kollision. Die Quell-Mods werden nur gelesen.',
    'EXPERIMENTAL - the merged mod may be buggy or keep the game from starting. Your mods are never changed; the result is a new archive.':
        'EXPERIMENTELL - die zusammengefuehrte Mod kann verbugt sein oder das Spiel am Starten hindern. Deine Mods werden nie veraendert; das Ergebnis ist ein neues Archiv.',
    'Changes': 'Aendert', 'With the ticked mods': 'Mit den angehakten Mods',
    'Name of the new mod:': 'Name der neuen Mod:', 'Main mod:': 'Haupt-Mod:',
    'The main mod is the base: its files stay as they are and it wins every clash you do not decide yourself. Needed for red combinations.':
        'Die Haupt-Mod ist die Grundlage: ihre Dateien bleiben wie sie sind, und sie gewinnt jede Kollision, die du nicht selbst entscheidest. Bei roten Kombinationen noetig.',
    'Carry quest markers over to the chosen map and put lost game markers back':
        'Questmarker auf die gewaehlte Karte uebertragen und verlorene Spielmarker zuruecksetzen',
    'Merge...': 'Zusammenfuehren...', 'Add archive from elsewhere...': 'Archiv von woanders hinzufuegen...',
    'Untick all': 'Alle Haken entfernen', '(from elsewhere)': '(von woanders)',
    'Tick at least two mods.': 'Mindestens zwei Mods anhaken.',
    'Green: no overlap inside files - merges without a question.': 'Gruen: keine Ueberschneidung innerhalb von Dateien - laeuft ohne Rueckfrage.',
    'Yellow (experimental): overlaps are asked one by one.': 'Gelb (experimentell): Ueberschneidungen werden einzeln gefragt.',
    'Red (experimental): choose a main mod - it wins every clash.': 'Rot (experimentell): Haupt-Mod waehlen - sie gewinnt jede Kollision.',
    'Still reading the archives...': 'Archive werden noch gelesen...',
    '{name} exists already - choose another name. The merger never overwrites a mod.':
        '{name} gibt es schon - anderen Namen waehlen. Das Zusammenfuehren ueberschreibt nie eine Mod.',
    'Could not read the mods: {e}': 'Mods nicht lesbar: {e}',
    'Red combination ({n} overlaps). Choose a main mod first: it wins every clash, the other mods add what does not clash.':
        'Rote Kombination ({n} Ueberschneidungen). Erst eine Haupt-Mod waehlen: sie gewinnt jede Kollision, die anderen Mods steuern bei, was nicht kollidiert.',
    'Merge {n} mods into {name}?\n\nThis is experimental: the result may be buggy or keep the game from starting. Your mods stay untouched.':
        '{n} Mods zu {name} zusammenfuehren?\n\nDas ist experimentell: das Ergebnis kann verbugt sein oder das Spiel am Starten hindern. Deine Mods bleiben unberuehrt.',
    'RED: {k} clashes go to the main mod {main}.': 'ROT: {k} Kollisionen gehen an die Haupt-Mod {main}.',
    '{a} of {b} files': '{a} von {b} Dateien', 'Merge failed: {e}': 'Zusammenfuehren fehlgeschlagen: {e}',
    '{name} is in the Mods folder, switched off.\n\nEnable it now and switch the merged source mods off? (Both at once would load everything twice.)':
        '{name} liegt im Mods-Ordner, ausgeschaltet.\n\nJetzt einschalten und die zusammengefuehrten Quell-Mods ausschalten? (Beides zugleich wuerde alles doppelt laden.)',
    '{name} merged.': '{name} zusammengefuehrt.',
    'Overlaps - who wins?': 'Ueberschneidungen - wer gewinnt?',
    '{n} places are changed by more than one mod. Pick the winner per row, or give all to one mod.':
        '{n} Stellen werden von mehr als einer Mod geaendert. Je Zeile den Gewinner waehlen, oder alles einer Mod geben.',
    'Give all to:': 'Alles an:', 'Cancel': 'Abbrechen', 'Merge with these choices': 'Mit dieser Auswahl zusammenfuehren',
    'Place': 'Stelle', 'Winner': 'Gewinner', 'Parameters': 'Parameter', 'Quest file': 'Questdatei', 'Texts': 'Texte',
    'Dialog trees': 'Dialogbaeume', 'Maps': 'Karten', 'Whole files': 'Ganze Dateien', 'Compiled scripts': 'Kompilierte Skripte',
    'Map and physics always come from the same mod. Markers only the other mod has are carried over.':
        'Karte und Physik kommen immer aus derselben Mod. Marker, die nur die andere Mod hat, werden uebertragen.',
    'Merge report': 'Bericht', 'Close': 'Schliessen',
    'Rebuild level cache now': 'Level-Cache jetzt neu bauen',
    'Keep level cache in step with the mods': 'Level-Cache mit den Mods mitfuehren',
    'Level cache not rebuilt: {e}': 'Level-Cache nicht neu gebaut: {e}',
    'Level cache rebuilt ({maps} maps, {n} from mods).': 'Level-Cache neu gebaut ({maps} Karten, {n} aus Mods).',
    'Level cache is up to date ({maps} maps, {n} from mods).': 'Level-Cache ist aktuell ({maps} Karten, {n} aus Mods).',
    '{k} map(s) come from two mods - which one the game takes is not measured.':
        '{k} Karte(n) kommen aus zwei Mods - welche das Spiel nimmt, ist nicht vermessen.',
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
    'The second tab lists verified mods from alchemy-fox.de, the third the community archive of InsideTwoWorlds on GitHub with a description per mod. Install downloads, checks the checksum and enables the mod; an existing archive is kept as .backup.':
        'Der zweite Reiter zeigt gepruefte Mods von alchemy-fox.de, der dritte das Community-Archiv von InsideTwoWorlds auf GitHub mit einer Beschreibung je Mod. Install laedt, prueft die Pruefsumme und schaltet ein; ein vorhandenes Archiv bleibt als .backup.',
    'Community (GitHub)': 'Community (GitHub)', 'Mods from the InsideTwoWorlds archive on GitHub': 'Mods aus dem InsideTwoWorlds-Archiv auf GitHub',
    'The folder "Two Worlds" of github.com/InsideTwoWorlds/MODs: every .wd with its description. Install downloads the file and checks it against the checksum GitHub stores for it.':
        'Der Ordner "Two Worlds" von github.com/InsideTwoWorlds/MODs: jede .wd mit ihrer Beschreibung. Install laedt die Datei und prueft sie gegen die Pruefsumme, die GitHub dafuer fuehrt.',
    'Open on GitHub': 'Auf GitHub oeffnen',
    'Community mods collected by InsideTwoWorlds. Each download is checked against the file hash GitHub stores; existing archives get a .backup copy before an update. Read the description before you install.':
        'Community-Mods, gesammelt von InsideTwoWorlds. Jeder Download wird gegen den Datei-Hash von GitHub geprueft; vorhandene Archive bekommen vor einem Update eine .backup-Kopie. Vor dem Einlegen die Beschreibung lesen.',
    'Loading the GitHub list...': 'Lade die GitHub-Liste...', 'GitHub list not available: {e}': 'GitHub-Liste nicht erreichbar: {e}',
    'GitHub list loaded - {n} mod(s).': 'GitHub-Liste geladen - {n} Mod(s).', '(no description in the archive)': '(keine Beschreibung im Archiv)',
    'loading description...': 'lade Beschreibung...', '(description not available: {e})': '(Beschreibung nicht erreichbar: {e})',
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
