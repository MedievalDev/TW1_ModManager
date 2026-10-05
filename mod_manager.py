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
    if n < 1024:
        return f'{n} B'
    if n < 1048576:
        return f'{n / 1024:.0f} KB'
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


# Shown in the gallery when a mod has no pictures: the Two Worlds logo from Steam's CDN,
# streamed at run time (nothing is stored in the tool or in the repo).
LOGO_URL = 'https://cdn.cloudflare.steamstatic.com/steam/apps/1930/logo.png'
LANG_NAMES = {'en': 'English', 'de': 'Deutsch'}


def lang_name(code):
    return LANG_NAMES.get(code, code or '')


def mod_variants(mod):
    """Language variants of a server entry: [{lang, file, url, size, sha256}].
    Entries without "variants" (the old format) count as one variant."""
    vs = [v for v in mod.get('variants') or [] if v.get('file') and v.get('url')]
    if vs:
        return vs
    return [{'lang': '', 'file': mod.get('file', ''), 'url': mod.get('url', ''),
             'size': mod.get('size', 0), 'sha256': mod.get('sha256', '')}]


def mod_text(mod, field, lang=None):
    """Text field in the tool language (or lang): <field>_de / <field>_en, else <field>."""
    return mod.get(f'{field}_{lang or _LANG}') or mod.get(field) or ''


def mod_list(value):
    if not value:
        return []
    if isinstance(value, str):
        return [t.strip() for t in re.split(r'[\r\n,]+', value) if t.strip()]
    return [str(t).strip() for t in value if str(t).strip()]


try:
    from PIL import Image, ImageTk                      # noqa: E402
except Exception:                                       # pictures then only as PNG/GIF
    Image = ImageTk = None


from tkinter import font as tkfont                      # noqa: E402

URL_RE = re.compile(r'https?://[^\s<>"\']+')


def split_links(text):
    """[(piece, url or None)]; trailing punctuation stays out of the link."""
    out, pos = [], 0
    for m in URL_RE.finditer(text):
        url = m.group(0)
        while url and url[-1] in '.,;:!?)]}':
            url = url[:-1]
        if m.start() > pos:
            out.append((text[pos:m.start()], None))
        out.append((url, url))
        pos = m.start() + len(url)
    if pos < len(text):
        out.append((text[pos:], None))
    return out


def read_only_text(parent, fg=None, font=None, bg=None):
    """A Text that looks like a label but can be selected and copied (Ctrl+C, right-click)."""
    w = tk.Text(parent, wrap='word', relief='flat', bd=0, highlightthickness=0, height=1,
                bg=bg or theme.BG, fg=fg or theme.INK, font=font or theme.FONT, padx=0, pady=0,
                insertwidth=0, selectbackground=theme.SEL, selectforeground=theme.INK,
                inactiveselectbackground=theme.SEL, cursor='xterm')

    def key(ev):
        if ev.state & 0x4 and ev.keysym.lower() in ('c', 'insert'):
            return None
        return 'break'

    def select_all(ev):
        w.tag_add('sel', '1.0', 'end-1c')
        return 'break'
    w.bind('<Key>', key)
    w.bind('<<Paste>>', lambda e: 'break')
    w.bind('<<Cut>>', lambda e: 'break')
    w.bind('<Control-a>', select_all)
    w.bind('<Button-1>', lambda e: w.focus_set(), add='+')
    menu = tk.Menu(w, tearoff=0, bg=theme.PANEL, fg=theme.INK, activebackground=theme.SEL,
                   activeforeground=theme.GOLD_HI, bd=0)
    menu.add_command(label=tr('Copy'), command=lambda: w.event_generate('<<Copy>>'))
    menu.add_command(label=tr('Select all'), command=lambda: select_all(None))

    def popup(ev):
        w.focus_set()
        menu.tk_popup(ev.x_root, ev.y_root)
    w.bind('<Button-3>', popup)

    return w


def fill_text(w, text, px=None):
    """Put text into a read_only_text with clickable links; px = wrap width in pixels to size the height."""
    for tag in w.tag_names():
        if tag.startswith('link'):
            w.tag_delete(tag)
    w.delete('1.0', 'end')
    n = 0
    for piece, url in split_links(text):
        if not url:
            w.insert('end', piece)
            continue
        tag = f'link{n}'
        n += 1
        w.insert('end', piece, (tag,))
        w.tag_configure(tag, foreground=theme.PLAYER_COLOR, underline=True)
        w.tag_bind(tag, '<Enter>', lambda e: w.configure(cursor='hand2'))
        w.tag_bind(tag, '<Leave>', lambda e: w.configure(cursor='xterm'))
        w.tag_bind(tag, '<ButtonRelease-1>', lambda e, u=url: None if w.tag_ranges('sel') else webbrowser.open(u))
    if px:
        f = tkfont.Font(font=w.cget('font'))
        lines = 0
        for para in text.split('\n'):
            cur, count = 0, 1
            for word in para.split(' '):
                wpx = f.measure(word + ' ')
                if cur and cur + wpx > px:
                    count += 1
                    cur = 0
                cur += wpx
                if wpx > px:
                    count += int(wpx // px)
                    cur = wpx % px
            lines += count
        w.configure(height=max(1, lines))


class TreeFilter:
    """Search row above a list: live filter (rows that do not match are detached) and a suggestion
    list under the entry. rows = [(iid, parent, haystack, label)] in display order; a row with
    label None is a group heading, shown while one of its children matches."""
    MAX = 8

    def __init__(self, app, parent, on_change=None):
        self.app = app
        self.tree = None
        self.rows = []
        self.on_change = on_change
        self.var = tk.StringVar()
        self._quiet = False
        self.pop = self.box = None
        self.frame = ttk.Frame(parent)
        ttk.Label(self.frame, text=tr('Search'), style='Muted.TLabel').pack(side='left')
        self.entry = ttk.Entry(self.frame, textvariable=self.var)
        self.entry.pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(self.frame, text='\u2715', width=3, command=self.clear).pack(side='left')
        self.lbl = ttk.Label(self.frame, style='Muted.TLabel', width=12, anchor='e')
        self.lbl.pack(side='left')
        self.var.trace_add('write', lambda *a: self._typed())
        self.entry.bind('<Down>', lambda e: self._move(1))
        self.entry.bind('<Up>', lambda e: self._move(-1))
        self.entry.bind('<Return>', self._accept)
        self.entry.bind('<Tab>', self._tab)
        self.entry.bind('<Escape>', self._escape)
        self.entry.bind('<FocusOut>', lambda e: self.app.root.after(200, self.hide))

    def focus(self):
        self.entry.focus_set()
        self.entry.select_range(0, 'end')

    def set_rows(self, rows):
        self.rows = rows
        self.apply()

    def release(self):
        """Bring every row back (before the list is rebuilt)."""
        if self.tree is None:
            return
        for iid, parent, hay, label in self.rows:
            try:
                self.tree.reattach(iid, parent, 'end')
            except tk.TclError:
                pass

    def apply(self):
        if self.tree is None:
            return
        words = self.var.get().lower().split()
        ok = {iid: all(w in hay for w in words) for iid, parent, hay, label in self.rows if label is not None}
        kids = {}
        for iid, parent, hay, label in self.rows:
            if parent:
                kids.setdefault(parent, []).append(iid)
        shown = 0
        for iid, parent, hay, label in self.rows:
            below = any(ok.get(k) for k in kids.get(iid, []))
            if label is None:
                show = below
            else:
                show = ok[iid] or below
                shown += ok[iid]
            try:
                if show:
                    self.tree.reattach(iid, parent, 'end')
                    if words and below:
                        self.tree.item(iid, open=True)
                else:
                    self.tree.detach(iid)
            except tk.TclError:
                pass
        total = sum(1 for r in self.rows if r[3] is not None)
        self.lbl.configure(text=(tr('No match') if not shown else f'{shown} / {total}') if words else '')
        if self.on_change:
            self.on_change()

    def clear(self):
        self.var.set('')
        self.hide()

    # ---- suggestions ----
    def _typed(self):
        if self._quiet:
            return
        self.apply()
        self._suggest()

    def _suggest(self):
        q = self.var.get().strip().lower()
        words = q.split()
        hits = []
        for iid, parent, hay, label in self.rows:
            if label is None or not words:
                continue
            low = label.lower()
            if low.startswith(q):
                pri = 0
            elif q in low:
                pri = 1
            elif all(w in hay for w in words):
                pri = 2
            else:
                continue
            hits.append((pri, low, label))
        labels = [h[2] for h in sorted(hits)][:self.MAX]
        if not labels or (len(labels) == 1 and labels[0].lower() == q):
            self.hide()
        else:
            self._show(labels)

    def _show(self, labels):
        if self.pop is None:
            self.pop = tk.Toplevel(self.app.root)
            self.pop.withdraw()
            self.pop.overrideredirect(True)
            self.box = tk.Listbox(self.pop, activestyle='none', exportselection=False, bg=theme.FIELD, fg=theme.INK,
                                  selectbackground=theme.SEL, selectforeground=theme.GOLD_HI, relief='flat', bd=0,
                                  highlightthickness=1, highlightbackground=theme.LINE, font=theme.FONT)
            self.box.pack(fill='both', expand=True)
            self.box.bind('<ButtonRelease-1>', lambda e: self._choose(self.box.nearest(e.y)))
        self.box.delete(0, 'end')
        for label in labels:
            self.box.insert('end', label)
        self.box.configure(height=len(labels))
        self.pop.update_idletasks()
        x, y = self.entry.winfo_rootx(), self.entry.winfo_rooty() + self.entry.winfo_height()
        self.pop.geometry(f'{self.entry.winfo_width()}x{self.box.winfo_reqheight()}+{x}+{y}')
        self.pop.deiconify()
        self.pop.lift()

    def visible(self):
        return self.pop is not None and bool(self.pop.winfo_viewable())

    def hide(self):
        if self.pop is not None:
            try:
                self.pop.withdraw()
            except tk.TclError:
                pass

    def _move(self, d):
        if not self.visible():
            return None
        cur = self.box.curselection()
        last = self.box.size() - 1
        idx = min(max(cur[0] + d, 0), last) if cur else (0 if d > 0 else last)
        self.box.selection_clear(0, 'end')
        self.box.selection_set(idx)
        self.box.see(idx)
        return 'break'

    def _choose(self, idx):
        if idx is None or idx < 0 or idx >= self.box.size():
            return
        self._quiet = True
        self.var.set(self.box.get(idx))
        self._quiet = False
        self.entry.icursor('end')
        self.hide()
        self.apply()
        self.entry.focus_set()

    def _accept(self, ev):
        if self.visible() and self.box.curselection():
            self._choose(self.box.curselection()[0])
            return 'break'
        self.hide()
        return None

    def _tab(self, ev):
        if self.visible():
            self._choose(self.box.curselection()[0] if self.box.curselection() else 0)
            return 'break'
        return None

    def _escape(self, ev):
        if self.visible():
            self.hide()
        else:
            self.clear()
        return 'break'


class ModSidePanel:
    """Right-hand panel of the list tabs: picture gallery with arrows, mod name, description,
    collapsible readme, tags and credits (every part optional). All texts can be selected and
    copied, web links in them are clickable."""
    W = 330
    PIC_H = 200

    def __init__(self, app, parent, on_install=None):
        self.app = app
        self.mod = None
        self.images = []
        self.idx = 0
        self.cache = {}
        self.outer = ttk.Frame(parent, width=self.W + 22)
        self.outer.pack(side='right', fill='y', padx=(12, 0))
        self.outer.pack_propagate(False)
        self.canvas = tk.Canvas(self.outer, bg=theme.BG, highlightthickness=0, width=self.W)
        bar = ttk.Scrollbar(self.outer, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.body, anchor='nw', width=self.W)
        self.body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Enter>', lambda e: self.canvas.bind_all('<MouseWheel>', self._wheel))
        self.canvas.bind('<Leave>', lambda e: self.canvas.unbind_all('<MouseWheel>'))

        self.pic_box = tk.Frame(self.body, bg=theme.CANVAS_BG, width=self.W, height=self.PIC_H)
        self.pic_box.pack(fill='x')
        self.pic_box.pack_propagate(False)
        self.pic = tk.Label(self.pic_box, bg=theme.CANVAS_BG, fg=theme.MUT, font=theme.FONT)
        self.pic.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.btn_prev = tk.Button(self.pic_box, text='\u25c0', command=lambda: self.step(-1), bd=0, bg=theme.PANEL,
                                  fg=theme.GOLD, activebackground=theme.SEL, activeforeground=theme.GOLD_HI, width=2)
        self.btn_next = tk.Button(self.pic_box, text='\u25b6', command=lambda: self.step(1), bd=0, bg=theme.PANEL,
                                  fg=theme.GOLD, activebackground=theme.SEL, activeforeground=theme.GOLD_HI, width=2)
        self.lbl_count = tk.Label(self.pic_box, bg=theme.PANEL, fg=theme.MUT, font=theme.FONT_SMALL)

        self.act = ttk.Frame(self.body)                      # install button and language switch under the picture
        self.act.pack(fill='x', pady=(8, 0))
        self.btn_install = ttk.Button(self.act, text=tr('Install / update'), style='Slim.Accent.TButton',
                                     command=on_install)
        self.btn_install.pack(side='left')
        self.lang_box = ttk.Frame(self.act)
        self.lang_box.pack(side='left', padx=(12, 0))
        self.lang_labels = {}
        self.lang = None            # chosen variant language (None: the mod has one file only)
        self.text_lang = None       # language of description and readme (None: the tool language)

        self.lbl_name = read_only_text(self.body, fg=theme.GOLD, font=theme.FONT_BRAND)
        self.lbl_name.pack(fill='x', pady=(10, 0))
        self.lbl_meta = read_only_text(self.body, fg=theme.MUT)
        self.lbl_meta.pack(fill='x')
        self.lbl_desc = read_only_text(self.body)
        self.lbl_desc.pack(fill='x', pady=(8, 0))
        self.btn_readme = ttk.Button(self.body, command=self._toggle_readme, style='Slim.TButton')
        self.txt_readme = read_only_text(self.body, font=theme.FONT_SMALL, bg=theme.FIELD)
        self.txt_readme.configure(height=14, padx=6, pady=4, highlightthickness=1, highlightbackground=theme.LINE)
        self.txt_readme.bind('<MouseWheel>', self._readme_wheel)
        self.readme_open = False
        self.lbl_tags = read_only_text(self.body, fg=theme.GOLD)
        self.lbl_credits_h = ttk.Label(self.body, text=tr('Credits'), foreground=theme.GOLD, font=theme.FONT_BOLD)
        self.lbl_credits = read_only_text(self.body, fg=theme.MUT)
        self.show(None)

    @staticmethod
    def text_of(w):
        return w.get('1.0', 'end-1c')

    def _put(self, w, text):
        fill_text(w, text, self.W - 10)

    def _wheel(self, ev):
        self.canvas.yview_scroll(-1 * (ev.delta // 120), 'units')

    def _readme_wheel(self, ev):
        self.txt_readme.yview_scroll(-1 * (ev.delta // 120) * 3, 'units')
        return 'break'

    def _toggle_readme(self):
        self.readme_open = not self.readme_open
        self._layout_readme()

    def _layout_readme(self):
        self.btn_readme.configure(text=('\u25be ' if self.readme_open else '\u25b8 ') + tr('Readme'))
        if self.readme_open:
            self.txt_readme.pack(fill='x', pady=(4, 0), after=self.btn_readme)
        else:
            self.txt_readme.pack_forget()

    def show(self, mod):
        if mod is not None and mod is self.mod:      # same selection again: keep picture and readme state
            return
        self.mod = mod
        self._clear_langs()
        self.canvas.yview_moveto(0)
        for w in (self.btn_readme, self.txt_readme, self.lbl_tags, self.lbl_credits_h, self.lbl_credits):
            w.pack_forget()
        if mod is None:
            self.images = []
            self._show_image()
            self._put(self.lbl_name, tr('Select a mod'))
            self._put(self.lbl_meta, '')
            self._put(self.lbl_desc, '')
            return
        self.images = mod_list(mod.get('images'))
        self.idx = 0
        self._show_image()
        self._render_texts()

    def _clear_langs(self):
        for w in self.lang_box.winfo_children():
            w.destroy()
        self.lang_labels = {}
        self.lang = self.text_lang = None

    def set_variants(self, codes, selected):
        """DE | EN switch next to the install button (only for mods with several language files)."""
        self._clear_langs()
        if len(codes) > 1:
            for i, code in enumerate(codes):
                if i:
                    ttk.Label(self.lang_box, text='\u00b7', style='Muted.TLabel').pack(side='left')
                lbl = ttk.Label(self.lang_box, text=(code or '').upper(), padding=(4, 2), cursor='hand2')
                lbl.pack(side='left')
                lbl.bind('<Button-1>', lambda e, c=code: self.select_lang(c))
                self.lang_labels[code] = lbl
            self.lang = self.text_lang = selected
        self._paint_langs()
        if self.mod is not None:
            self._render_texts(keep_open=True)

    def _paint_langs(self):
        for code, lbl in self.lang_labels.items():
            on = code == self.lang
            lbl.configure(foreground=theme.GOLD if on else theme.MUT, font=theme.FONT_BOLD if on else theme.FONT)

    def select_lang(self, code):
        if code in self.lang_labels:
            self.lang = self.text_lang = code
            self._paint_langs()
            self._render_texts(keep_open=True)

    def _render_texts(self, keep_open=False):
        mod = self.mod
        if mod is None:
            return
        for w in (self.btn_readme, self.txt_readme, self.lbl_tags, self.lbl_credits_h, self.lbl_credits):
            w.pack_forget()
        self._put(self.lbl_name, mod.get('name', ''))
        meta = [x for x in ('v' + str(mod['version']) if mod.get('version') else '', mod.get('author', '')) if x]
        self._put(self.lbl_meta, '  \u00b7  '.join(meta))
        self._put(self.lbl_desc, mod_text(mod, 'description', self.text_lang))
        readme = mod_text(mod, 'readme', self.text_lang).strip()
        if readme:
            self.btn_readme.pack(anchor='w', pady=(10, 0))
            fill_text(self.txt_readme, readme)
            if not keep_open:
                self.readme_open = False
            self._layout_readme()
        tags = mod_list(mod.get('tags'))
        if tags:
            self._put(self.lbl_tags, '   '.join('#' + t for t in tags))
            self.lbl_tags.pack(fill='x', pady=(10, 0))
        credits = mod_list(mod.get('credits'))
        if credits:
            self._put(self.lbl_credits, '\n'.join(credits))
            self.lbl_credits_h.pack(anchor='w', pady=(10, 0))
            self.lbl_credits.pack(fill='x')

    # ---- gallery ----
    def step(self, d):
        if self.images:
            self.idx = (self.idx + d) % len(self.images)
            self._show_image()

    def _show_image(self):
        many = len(self.images) > 1
        for w, x in ((self.btn_prev, 0.0), (self.btn_next, 1.0)):
            if many:
                w.place(relx=x, rely=0.5, anchor='w' if x == 0 else 'e')
            else:
                w.place_forget()
        if many:
            self.lbl_count.configure(text=f'{self.idx + 1} / {len(self.images)}')
            self.lbl_count.place(relx=0.5, rely=1.0, anchor='s')
        else:
            self.lbl_count.place_forget()
        empty = not self.images
        url = LOGO_URL if empty else self.images[self.idx]
        gone = tr('No pictures for this mod yet.') if empty else tr('(picture not available)')
        if url in self.cache:
            img = self.cache[url]
            if img is None:
                self.pic.configure(image='', text=gone)
            else:
                self.pic.configure(image=img, text='')
                self.pic.image = img
            return
        self.pic.configure(image='', text=gone if empty else tr('loading picture...'))
        box = (self.W - 80, self.PIC_H - 60) if empty else (self.W, self.PIC_H)

        def work():
            try:
                import urllib.parse
                import io
                data = http_get(urllib.parse.urljoin(MODS_URL, url))
                if Image is not None:
                    im = Image.open(io.BytesIO(data))
                    im.thumbnail(box)
                    raw = ('pil', im.convert('RGBA'))
                else:
                    raw = ('png', data)
            except Exception:
                raw = None

            def done():
                try:
                    if raw is None:
                        self.cache[url] = None
                    elif raw[0] == 'pil':
                        self.cache[url] = ImageTk.PhotoImage(raw[1])
                    else:
                        import base64
                        self.cache[url] = tk.PhotoImage(data=base64.b64encode(raw[1]))
                except Exception:
                    self.cache[url] = None
                if (LOGO_URL if not self.images else self.images[self.idx]) == url:
                    self._show_image()
            self.app.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()


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
        w, h = 960, min(825, self.root.winfo_screenheight() - 90)
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
        self.notebook.bind('<<NotebookTabChanged>>', lambda e: self._hide_suggestions())
        self.root.bind('<Configure>', lambda e: self._hide_suggestions() if e.widget is self.root else None, add='+')

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
        self.flt_inst = TreeFilter(self, tab, on_change=lambda: self.tree is not None and self._colour_fit())
        self.flt_inst.frame.pack(fill='x', pady=(0, 6))
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
        self.flt_inst.tree = self.tree
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
        self.flt_server = TreeFilter(self, tab)
        self.flt_server.frame.pack(fill='x', pady=(0, 6))
        self.side = ModSidePanel(self, tab, on_install=self.install_server_mod)
        self.btn_install = self.side.btn_install
        left = ttk.Frame(tab)
        left.pack(side='left', fill='both', expand=True)
        self.stree = ttk.Treeview(left, columns=('ver', 'size', 'status'), show='tree headings')
        self.stree.heading('#0', text=tr('Mod'))
        self.stree.heading('ver', text=tr('Version'))
        self.stree.heading('size', text=tr('Size'))
        self.stree.heading('status', text=tr('On this PC'))
        self.stree.column('#0', width=240)
        self.stree.column('ver', width=60, anchor='center')
        self.stree.column('size', width=80, anchor='e')
        self.stree.column('status', width=150, anchor='center')
        self.stree.pack(fill='both', expand=True)
        self.flt_server.tree = self.stree
        self.stree.tag_configure('on', foreground=theme.OK)
        self.stree.tag_configure('get', foreground=theme.GOLD)
        self.stree.tag_configure('off', foreground=theme.MUT)
        self.stree.tag_configure('head', foreground=theme.GOLD_HI)
        self.stree.bind('<<TreeviewSelect>>', lambda ev: self._show_desc())
        btns = ttk.Frame(left)
        btns.pack(fill='x', pady=(10, 0))
        ttk.Button(btns, text=tr('Reload list'),
                   command=lambda: threading.Thread(target=self._fetch_catalog, daemon=True).start()
                   ).pack(side='left', padx=6)
        ttk.Label(left, style='Muted.TLabel', wraplength=520, justify='left',
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
        self.flt_gh = TreeFilter(self, tab)
        self.flt_gh.frame.pack(fill='x', pady=(0, 6))
        self.gside = ModSidePanel(self, tab, on_install=self.install_github_mod)
        self.btn_ginstall = self.gside.btn_install
        left = ttk.Frame(tab)
        left.pack(side='left', fill='both', expand=True)
        self.gtree = ttk.Treeview(left, columns=('size', 'status'), show='tree headings')
        self.gtree.heading('#0', text=tr('Mod archive'))
        self.gtree.heading('size', text=tr('Size'))
        self.gtree.heading('status', text=tr('On this PC'))
        self.gtree.column('#0', width=300)
        self.gtree.column('size', width=90, anchor='e')
        self.gtree.column('status', width=160, anchor='center')
        self.gtree.pack(fill='both', expand=True)
        self.flt_gh.tree = self.gtree
        self.gtree.tag_configure('on', foreground=theme.OK)
        self.gtree.tag_configure('get', foreground=theme.GOLD)
        self.gtree.tag_configure('off', foreground=theme.MUT)
        self.gtree.bind('<<TreeviewSelect>>', lambda ev: self._show_gh_desc())
        self.gtree.bind('<Double-1>', lambda ev: self.install_github_mod())
        btns = ttk.Frame(left)
        btns.pack(fill='x', pady=(10, 0))
        ttk.Button(btns, text=tr('Reload list'),
                   command=lambda: threading.Thread(target=self._fetch_github, daemon=True).start()
                   ).pack(side='left', padx=6)
        ttk.Button(btns, text=tr('Open on GitHub'), command=lambda: webbrowser.open(GH_PAGE)).pack(side='right')
        ttk.Label(left, style='Muted.TLabel', wraplength=520, justify='left',
                  text=tr('Community mods collected by InsideTwoWorlds. Each download is checked against the file hash GitHub stores; existing archives get a .backup copy before an update. Read the description before you install.')
                  ).pack(anchor='w', pady=(8, 0))
        return tab

    def _bind_keys(self):
        r = self.root
        r.bind('<Control-o>', lambda e: self.add_mod())
        r.bind('<F5>', lambda e: self.refresh())
        r.bind('<Control-f>', lambda e: self._focus_search())
        r.bind('<F1>', lambda e: self.show_guide())

    def _filters(self):
        return [self.flt_inst, self.flt_server, self.flt_gh]

    def _hide_suggestions(self):
        for f in self._filters():
            f.hide()

    def _focus_search(self):
        i = self.notebook.index(self.notebook.select())
        if i < 3:
            self._filters()[i].focus()
        return 'break'

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
            self.error('folder.wrong', 'Picked folder is not a Two Worlds install', tr('That folder has no WDFiles\\Update16.wd:\n{p}\n\nPick the Two Worlds install folder itself.').format(p=d))
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
        self.flt_inst.release()
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
        self.flt_inst.set_rows([(i, '', self.tree.item(i, 'text').lower(), self.tree.item(i, 'text'))
                                for i in self.tree.get_children()])
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
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
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
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
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
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
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

    def _mod_state(self, mod, reg):
        found = None
        for v in mod_variants(mod):
            local = os.path.join(self.mods_dir, v['file'])
            if os.path.exists(local):
                found = (v, local)
                if v.get('lang') == _LANG:
                    break
        if found is None:
            return tr('not installed'), 'get', None
        v, local = found
        if v.get('sha256') and sha256_of(local) != v['sha256']:
            state, tag = tr('update available'), 'get'
        elif reg.get(v['file'], 0):
            state, tag = tr('installed · enabled'), 'on'
        else:
            state, tag = tr('installed · disabled'), 'off'
        if len(mod_variants(mod)) > 1:
            state += f' ({lang_name(v.get("lang"))})'
        return state, tag, v

    def _refresh_server_states(self):
        if self.catalog is None or not hasattr(self, 'stree'):
            return
        keep = self.stree.selection()
        self.flt_server.release()
        self.stree.delete(*self.stree.get_children())
        reg = registry_mods()
        mods = sorted(self.catalog.get('mods', []), key=lambda m: m.get('name', '').lower())
        top = [m for m in mods if m.get('group') != 'merged']
        topids = {m['id'] for m in top}
        kids, orphans = {}, []
        for m in mods:                      # a merged mod hangs under the mod it builds on ("base")
            if m.get('group') == 'merged':
                if m.get('base') in topids:
                    kids.setdefault(m['base'], []).append(m)
                else:
                    orphans.append(m)
        rows = []

        def add(mod, parent):
            state, tag, v = self._mod_state(mod, reg)
            vs = mod_variants(mod)
            size = (v or vs[0]).get('size') or mod.get('size', 0)
            merged = mod.get('group') == 'merged'
            self.stree.insert(parent, 'end', iid=mod['id'],
                              text=mod['name'] + (f' ({tr("merged mod")})' if merged else ''),
                              values=(mod.get('version', ''), fmt_size(size), state), tags=(tag,), open=False)
            hay = ' '.join([mod.get('name', ''), mod.get('description', ''), mod.get('description_de', ''),
                            mod.get('author', ''), ' '.join(mod_list(mod.get('tags'))),
                            ' '.join(x['file'] for x in vs), tr('merged mod') if merged else '']).lower()
            rows.append((mod['id'], parent, hay, mod['name']))
        for mod in top:
            add(mod, '')
            for k in kids.get(mod['id'], []):
                add(k, mod['id'])
        if orphans:
            self.stree.insert('', 'end', iid='_merged', text=tr('Merged mods'), open=True, tags=('head',))
            rows.append(('_merged', '', '', None))
            for mod in orphans:
                add(mod, '_merged')
        self.flt_server.set_rows(rows)
        if keep and self.stree.exists(keep[0]):
            try:
                self.stree.selection_set(keep[0])
            except tk.TclError:
                self._show_desc()
        else:
            self._show_desc()

    def _selected_mod(self):
        sel = self.stree.selection()
        if not sel or self.catalog is None:
            return None
        return next((m for m in self.catalog.get('mods', []) if m['id'] == sel[0]), None)

    def _show_desc(self):
        mod = self._selected_mod()
        self.side.show(mod)
        vs = mod_variants(mod) if mod else []
        if len(vs) > 1:
            installed = next((v for v in vs if os.path.exists(os.path.join(self.mods_dir, v['file']))), None)
            want = installed or next((v for v in vs if v.get('lang') == _LANG), vs[0])
            self.side.set_variants([v.get('lang') for v in vs], want.get('lang'))

    def _chosen_variant(self, mod):
        vs = mod_variants(mod)
        if len(vs) > 1 and self.side.lang:
            return next((v for v in vs if v.get('lang') == self.side.lang), vs[0])
        return vs[0]

    def install_server_mod(self):
        mod = self._selected_mod()
        if mod is None:
            return
        if game_running():
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
            return
        self.btn_install.state(['disabled'])
        threading.Thread(target=self._download, args=(mod, self._chosen_variant(mod)), daemon=True).start()

    def _download(self, mod, v):
        try:
            name = mod['name'] + (f' ({lang_name(v["lang"])})' if len(mod_variants(mod)) > 1 else '')
            dest = os.path.join(self.mods_dir, v['file'])
            self.status(tr('Downloading {name}...').format(name=name))
            download_file(v['url'], dest, v.get('size') or 0, sha256=v.get('sha256'),
                          progress=lambda d, t: self.status(tr('Downloading {name}... {p}%').format(
                              name=name, p=d * 100 // t if t else 0)))
            registry_set(v['file'], 1)
            # the language variants replace the same game files: switch the other ones off
            for o in mod_variants(mod):
                if o['file'] != v['file'] and os.path.exists(os.path.join(self.mods_dir, o['file'])):
                    registry_set(o['file'], 0)
            self.status(tr('{name} installed and enabled.').format(name=name))
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
            pics = {e['name'].lower(): e['download_url'] for e in entries if e['type'] == 'file'
                    and e['name'].lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp'))}
            mods = []
            for e in entries:
                if e['type'] != 'file' or not e['name'].lower().endswith('.wd'):
                    continue
                stem = e['name'].lower()
                images = [u for n, u in sorted(pics.items()) if n.startswith(stem + '.') or n.startswith(stem[:-3] + '.')]
                mods.append({'name': e['name'], 'size': e['size'], 'sha': e['sha'], 'url': e['download_url'],
                             'txt_url': txts.get(e['name'] + '.txt') or txts.get(e['name'][:-3] + '.td.txt'),
                             'images': images})
            self.github = sorted(mods, key=lambda m: m['name'].lower())
        except Exception as exc:
            self.status(tr('GitHub list not available: {e}').format(e=exc), error=True)
            return
        self.status(tr('GitHub list loaded - {n} mod(s).').format(n=len(self.github)))
        self.root.after(0, self._refresh_github_states)
        threading.Thread(target=self._prefetch_gh_desc, daemon=True).start()

    def _refresh_github_states(self):
        if self.github is None or not hasattr(self, 'gtree'):
            return
        sel = self.gtree.selection()
        self.flt_gh.release()
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
        self._gh_search_rows()
        if sel and self.gtree.exists(sel[0]):
            try:
                self.gtree.selection_set(sel[0])
            except tk.TclError:
                pass

    def _gh_search_rows(self):
        """Search rows of the community tab: file name plus the description once it has been loaded."""
        rows = [(m['name'], '', (m['name'] + ' ' + self.gh_desc.get(m['name'], '')).lower(), m['name'])
                for m in self.github or []]
        self.flt_gh.set_rows(rows)

    def _gh_text(self, mod):
        text = http_get(mod['txt_url']).decode('utf-8', 'replace').replace('\r\n', '\n').replace('\r', '\n')
        return re.sub(r'\n{3,}', '\n\n', text).strip()[:6000]

    def _prefetch_gh_desc(self):
        """Load all descriptions in the background so the search finds words inside them."""
        for mod in list(self.github or []):
            if mod['name'] in self.gh_desc or not mod.get('txt_url'):
                continue
            try:
                self.gh_desc[mod['name']] = self._gh_text(mod)
            except Exception:
                continue
        try:
            self.root.after(0, self._gh_search_rows)
        except Exception:
            pass

    def _gh_panel(self, mod, text):
        self.gside.show({'id': mod['name'], 'name': re.sub(r'\.wd$', '', mod['name'], flags=re.I),
                         'description': text, 'images': mod.get('images', []),
                         'credits': [tr('Archive: {name}').format(name=GH_NAME)]})

    def _show_gh_desc(self):
        sel = self.gtree.selection()
        if not sel or self.github is None:
            return
        name = sel[0]
        mod = next((m for m in self.github if m['name'] == name), None)
        if mod is None:
            return
        if name in self.gh_desc:
            self._gh_panel(mod, self.gh_desc[name])
            return
        if not mod['txt_url']:
            self.gh_desc[name] = tr('(no description in the archive)')
            self._gh_panel(mod, self.gh_desc[name])
            return
        self._gh_panel(mod, tr('loading description...'))

        def work():
            try:
                text = self._gh_text(mod)
            except Exception as exc:
                text = tr('(description not available: {e})').format(e=exc)
            self.gh_desc[name] = text

            def show():
                cur = self.gtree.selection()
                if cur and cur[0] == name:
                    self._gh_panel(mod, self.gh_desc[name])
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
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
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
                self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
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


# What helps, per error key: (guide chapter, tip EN, tip DE). Shown above the
# technical text of every error, with a button to that chapter.
ERROR_TIPS = {
    'install.failed': ('install',
        'Is it a real Two Worlds .wd? Is Two Worlds closed? If the game sits under Program Files, start the Mod Manager once as the same user who installed the game.',
        'Ist es eine echte Two-Worlds-.wd? Ist Two Worlds geschlossen? Liegt das Spiel unter Programme, den Mod Manager als derselbe Benutzer starten, der das Spiel installiert hat.'),
    'game.running': ('trouble',
        'Close Two Worlds completely, the launcher too, then try again. The game reads the mod list only at start.',
        'Two Worlds ganz schliessen, auch den Launcher, dann noch einmal. Das Spiel liest die Mod-Liste nur beim Start.'),
    'folder.wrong': ('start',
        'Pick the folder that holds TwoWorlds.exe and the WDFiles folder - not WDFiles itself.',
        'Den Ordner waehlen, in dem TwoWorlds.exe und der Ordner WDFiles liegen - nicht WDFiles selbst.'),
    'merge.read.failed': ('merge',
        'One of the ticked archives could not be read. Open it once in the TW1 WD Packer: if that fails too, the file is damaged - download it again.',
        'Eines der angehakten Archive liess sich nicht lesen. Einmal im TW1 WD Packer oeffnen: klappt das auch nicht, ist die Datei beschaedigt - neu herunterladen.'),
    'merge.failed': ('merge',
        'Your mods are untouched. Tick fewer mods to find the one that breaks it, then report the bug - the merge report is attached.',
        'Deine Mods sind unveraendert. Weniger Mods anhaken, um die eine zu finden, an der es scheitert, dann den Bug melden - der Merge-Bericht haengt an.'),
    'merge.name.exists': ('merge',
        'Give the new mod another name. The merger never overwrites a mod.',
        'Der neuen Mod einen anderen Namen geben. Der Merger ueberschreibt nie eine Mod.'),
    'crash': ('trouble',
        'Please report it: the log goes with it, and nothing is sent before you have seen it.',
        'Bitte melden: das Protokoll geht mit, und nichts wird verschickt, bevor du es gesehen hast.'),
}


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
        chapter, tip_en, tip_de = ERROR_TIPS.get(key, ('trouble', '', ''))
        guide = guide or chapter
        tip = tip_de if _LANG == 'de' else tip_en
        if tip:
            ttk.Label(f, text=tr('What helps'), style='Brand.TLabel').pack(anchor='w')
            ttk.Label(f, text=tip, wraplength=620, justify='left').pack(anchor='w', pady=(2, 10))
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
    'What helps': 'Was hilft',
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
    'Tick the mods to merge. Green: no overlap inside files. Yellow: overlaps inside files, shared maps or whole files - each one is asked, however many there are. Red: both change compiled scripts - one mod has to be the main mod and keeps its scripts; everything else is still asked. The source mods are only read.':
        'Die Mods anhaken, die zusammen sollen. Gruen: keine Ueberschneidung innerhalb von Dateien. Gelb: Ueberschneidungen in Dateien, gemeinsame Karten oder ganze Dateien - jede wird gefragt, egal wie viele. Rot: beide aendern kompilierte Skripte - eine Mod muss Haupt-Mod sein und behaelt ihre Skripte; alles andere wird trotzdem gefragt. Die Quell-Mods werden nur gelesen.',
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
    'Red (experimental): both mods change compiled scripts. Choose a main mod - it keeps its scripts; everything else is asked one by one.':
        'Rot (experimentell): beide Mods aendern kompilierte Skripte. Haupt-Mod waehlen - sie behaelt ihre Skripte; alles andere wird einzeln gefragt.',
    'Both mods change compiled scripts ({n}). Scripts cannot be mixed, so choose a main mod first: it keeps its scripts. Everything else is asked one by one.':
        'Beide Mods aendern kompilierte Skripte ({n}). Skripte lassen sich nicht mischen, deshalb zuerst eine Haupt-Mod waehlen: sie behaelt ihre Skripte. Alles andere wird einzeln gefragt.',
    'Scripts: {k} go to the main mod {main}.': 'Skripte: {k} gehen an die Haupt-Mod {main}.',
    'The {k} compiled scripts stay with the main mod.': 'Die {k} kompilierten Skripte bleiben bei der Haupt-Mod.',
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
    'Merged mods': 'Zusammengefuehrte Mods', 'Readme': 'Readme', 'Credits': 'Credits', 'Select a mod': 'Mod auswaehlen',
    'No pictures for this mod yet.': 'Noch keine Bilder zu dieser Mod.', '(picture not available)': '(Bild nicht erreichbar)',
    'Search': 'Suche', 'No match': 'Keine Treffer', 'Copy': 'Kopieren', 'Select all': 'Alles markieren',
    'loading picture...': 'lade Bild...', 'Archive: {name}': 'Archiv: {name}',
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
