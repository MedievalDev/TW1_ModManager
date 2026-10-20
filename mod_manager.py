"""TW1 Mod Manager - install, enable and disable Two Worlds mods.

    py mod_manager.py              window
    py mod_manager.py <file.wd>    window, then the install dialog for that file

A mod manager for a 2007 game that never had one:

* every archive in Mods\\ with its registry switch, toggled per mod
* .wd files dropped onto the window (or picked with Ctrl+O) go through one
  dialog: what is inside, install and enable / install only / cancel
* zip packages with several files (.wd, TWSE plugin DLLs and inis) go to
  their places and are switched and removed together (packages.py)
* TWSE, buglord's Script Extender, set up from his GitHub release and taken
  back again, shortcuts included (twse_install.py)
* nothing is ever deleted - disabling flips the registry DWORD, and
  "remove" moves the archive into Mods\\_removed\\

Layout after PY_TOOL_DESIGN.md: theme.py, dark menu bar, DE/EN top right,
tour on first start, guide window (F1) with ?-marks, config next to the script or under
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
from version import VERSION                             # noqa: E402
import packages as pkgs                                 # noqa: E402
import twse_install                                     # noqa: E402

APP_NAME = 'TW1 MOD MANAGER'
GITHUB_URL = 'https://github.com/MedievalDev/TW1_ModManager'
SITE_URL = 'https://alchemy-fox.de/'
GUIDE_URL = 'https://alchemy-fox.de/game/TW1_ModManager/'
COMMUNITY_URL = 'https://twmp.alchemy-fox.de/'
LINKS = (('GitHub-Repo', GITHUB_URL), ('Alchemy Fox', SITE_URL),
         ('Guide-Seite', GUIDE_URL), ('Community', COMMUNITY_URL))
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


def http_get(url, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers={'User-Agent': 'TW1-Mod-Manager/' + VERSION,
                                               'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


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
     'which of them are on. This window shows both, switches them and installs new ones. '
     'Nothing is ever deleted.'},
    {'title': 'Installed mods', 'widget': 'tree', 'text':
     'Every .wd in the Mods folder with its switch, size and date. Double-click or Enter toggles '
     'a mod. Red rows are archives in the game folder itself - those always load.'},
    {'title': 'Drop a mod here', 'widget': 'drop_hint', 'text':
     'Drag a .wd from Explorer anywhere onto this window. A dialog shows what is inside and asks: '
     'install and enable, install only, or cancel. Ctrl+O does the same through a file dialog.'},
    {'title': 'Buttons', 'widget': 'btn_toggle', 'text':
     'Enable / disable flips the registry switch. Remove moves the archive to Mods\\_removed and '
     'keeps it. Changes count from the next game start.'},
    {'title': 'Help', 'widget': 'menubar', 'text':
     'F1 opens the guide with chapters, search and the registry reference. The gold ? marks jump '
     'straight to the matching chapter.'},
]


# Shown in the gallery when a mod has no pictures: the Two Worlds logo from Steam's CDN,
# streamed at run time (nothing is stored in the tool or in the repo).
LOGO_URL = 'https://cdn.cloudflare.steamstatic.com/steam/apps/1930/logo.png'
LANG_NAMES = {'en': 'English', 'de': 'Deutsch'}


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


class PackageDialog:
    """The dialog for a zip package: where every file goes, what is refused,
    a warning for program code (DLL), TWSE when it is missing."""

    def __init__(self, app, path, plan):
        self.app = app
        self.plan = plan
        self.result = None
        self.twse_var = tk.BooleanVar(value=True)
        self.win = tk.Toplevel(app.root)
        self.win.title(tr('Load this mod package?'))
        self.win.transient(app.root)
        self.win.resizable(False, False)
        theme.dark_titlebar(self.win)
        self.win.bind('<Escape>', lambda e: self.close(None))
        f = ttk.Frame(self.win, padding=18)
        f.pack(fill='both', expand=True)
        title = plan['name'] + (f'  {plan["version"]}' if plan['version'] else '')
        ttk.Label(f, text=title, style='Brand.TLabel').pack(anchor='w')
        sub = os.path.basename(path) + '  ·  ' + fmt_size(os.path.getsize(path))
        ttk.Label(f, text=sub, style='Muted.TLabel').pack(anchor='w', pady=(0, 10))

        box = ttk.Frame(f, style='Panel.TFrame', padding=(12, 8))
        box.pack(fill='x')
        blocked = bool(plan['errors'])
        clash = []
        if plan['ok']:
            clash = app.pk.conflicts(plan)
            what = {'new': tr('new'), 'unchanged': tr('unchanged'), 'updated': tr('updated'),
                    'yours kept, new one as .ini.new': tr('yours kept, new one as .ini.new'),
                    'replaces a file of no package (kept as .backup)': tr('replaces a file of no package (kept as .backup)')}
            lines = [f'{d.replace("/", SEP):<44} {what.get(w, w)}' for d, w in app.pk.preview(plan)]
            ttk.Label(box, text=chr(10).join(lines), style='PanelMuted.TLabel', font=theme.FONT_MONO,
                      justify='left').pack(anchor='w')
            extra = []
            if plan['shown']:
                extra.append(tr('Shown, not installed: ') + ', '.join(plan['shown'][:6]))
            if plan['ignored']:
                extra.append(tr('Ignored: ') + ', '.join(plan['ignored'][:6]) + (' …' if len(plan['ignored']) > 6 else ''))
            if extra:
                ttk.Label(box, text=chr(10).join(extra), style='PanelMuted.TLabel', wraplength=460,
                          justify='left').pack(anchor='w', pady=(6, 0))
        else:
            ttk.Label(box, text=tr('Refused - nothing will be installed:') + chr(10) + chr(10).join(
                '- ' + e for e in plan['errors'][:8]), style='Panel.TLabel', foreground=theme.ERR,
                wraplength=460, justify='left').pack(anchor='w')
        for d, why in clash:
            ttk.Label(f, text=tr('{dest}: {why} - remove that package first.').format(dest=d.replace('/', SEP), why=why),
                      foreground=theme.ERR, wraplength=460, justify='left').pack(anchor='w', pady=(6, 0))
            blocked = True
        if plan['ok'] and any(x['kind'] == 'dll' for x in plan['files']):
            ttk.Label(f, text=tr('Contains a game plugin (DLL): program code that runs inside Two Worlds. Install it only from a source you trust.'),
                      foreground=theme.GOLD, wraplength=460, justify='left').pack(anchor='w', pady=(10, 0))
        if plan['readme']:
            t = read_only_text(f)
            t.configure(height=6, width=64)
            fill_text(t, plan['readme'][:4000])
            t.pack(fill='x', pady=(10, 0))
        if plan['ok'] and plan['needs_twse'] and not twse_install.status(app.game_dir)['installed']:
            ttk.Label(f, text=tr('This mod needs the TW1 Script Extender (TWSE), and it is not installed. You are asked about it when you install.'),
                      foreground=theme.GOLD, wraplength=460, justify='left').pack(anchor='w', pady=(10, 0))
        if game_running():
            ttk.Label(f, text=tr('Two Worlds is running - close it first, it reads the mod list only at start.'),
                      foreground=theme.ERR, wraplength=460, justify='left').pack(anchor='w', pady=(6, 0))
            blocked = True

        b = ttk.Frame(f)
        b.pack(fill='x', pady=(16, 0))
        ttk.Button(b, text=tr('Cancel'), command=lambda: self.close(None)).pack(side='right')
        only = ttk.Button(b, text=tr('Install only'), command=lambda: self.close('install'))
        only.pack(side='right', padx=8)
        go = ttk.Button(b, text=tr('Install and enable'), style='Accent.TButton', command=lambda: self.close('enable'))
        go.pack(side='right')
        if blocked:
            go.state(['disabled'])
            only.state(['disabled'])
        else:
            self.win.bind('<Return>', lambda e: self.close('enable'))
            go.focus_set()
        self.win.update_idletasks()
        r = app.root
        self.win.geometry(f'+{r.winfo_rootx() + 120}+{r.winfo_rooty() + 100}')
        self.win.grab_set()
        self.win.wait_window()

    def close(self, result):
        self.result = result
        self.win.grab_release()
        self.win.destroy()


class TwseNeededDialog:
    """"This mod needs the TW1 Script Extender" - [Install] [Cancel]. result: True / False."""

    def __init__(self, app, plan):
        self.result = False
        self.win = tk.Toplevel(app.root)
        self.win.title(tr('TWSE needed'))
        self.win.transient(app.root)
        self.win.resizable(False, False)
        theme.dark_titlebar(self.win)
        self.win.bind('<Escape>', lambda e: self.close(False))
        f = ttk.Frame(self.win, padding=18)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text=tr('This mod needs the TW1 Script Extender (TWSE)'), style='Brand.TLabel').pack(anchor='w')
        dlls = [x['dest'].split('/', 1)[1] for x in plan['files'] if x['kind'] == 'dll']
        why = (tr('Game plugins in the package: ') + ', '.join(dlls)) if dlls else tr('The package says it requires TWSE.')
        ttk.Label(f, text=why, style='Muted.TLabel', wraplength=420, justify='left').pack(anchor='w', pady=(6, 0))
        ttk.Label(f, text=tr('Install: TWSE is set up first (patched exe, shortcuts), then the mod {name}.\nCancel: nothing is installed or written.').format(name=plan['name']),
                  wraplength=420, justify='left').pack(anchor='w', pady=(12, 0))
        b = ttk.Frame(f)
        b.pack(fill='x', pady=(16, 0))
        ttk.Button(b, text=tr('Cancel'), command=lambda: self.close(False)).pack(side='right')
        go = ttk.Button(b, text=tr('Install'), style='Accent.TButton', command=lambda: self.close(True))
        go.pack(side='right', padx=8)
        self.win.bind('<Return>', lambda e: self.close(True))
        go.focus_set()
        self.win.protocol('WM_DELETE_WINDOW', lambda: self.close(False))
        self.win.update_idletasks()
        r = app.root
        self.win.geometry(f'+{r.winfo_rootx() + 140}+{r.winfo_rooty() + 120}')
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
        self.cache_var = tk.BooleanVar(value=bool(self.cfg.get('auto_level_cache', True)))
        self.guide = Guide(self)
        self.tr = tr
        self.APP_NAME = APP_NAME
        self.data_dir = data_dir
        self.registry_set = lambda name, value: registry_set(name, value)
        self.registry_delete = lambda name: registry_delete(name)
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
        """The Lite edition sends nothing anywhere: the log calls stay as no-ops."""
        class _Log:
            def add(self, *a, **k):
                pass

        class _Quiet:
            log = _Log()

            def start(self):
                pass

            def add_menu_items(self, menu):
                pass
        self.fb = _Quiet()
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
        if not self.selftest:
            self.root.after(1500, self._startup_cache)
            self.root.after(2500, self.fb.start)
        self.root.protocol('WM_DELETE_WINDOW', self._close)
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
        if self.selftest:
            self._run_selftest()
            return
        c = self._carry
        if c.get('tab'):
            self.notebook.select(c['tab'])
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
            try:
                twse = 'known' if twse_install.sha256(twse_install.bundled_dll(getattr(sys, '_MEIPASS', HERE))) in twse_install.KNOWN_DLLS else 'unknown'
            except OSError as e:
                twse = f'missing:{e.__class__.__name__}'
            with open(self.selftest, 'w', encoding='utf-8') as f:
                f.write(f'version={VERSION} game={bool(self.game_dir)} mods={len(self.tree.get_children()) if self.game_dir else 0} '
                        f'twse_dll={twse} '
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
        m.add_command(label=tr('About'), command=self.show_about)

    def _tab_installed(self, parent):
        tab = ttk.Frame(parent, padding=12)
        hdr = ttk.Frame(tab)
        hdr.pack(fill='x', pady=(0, 4))
        ttk.Label(hdr, text=tr('Archives in the Mods folder'), style='Muted.TLabel').pack(side='left')
        help_mark(hdr, tr('One row per .wd with its registry switch. Double-click toggles. Red rows sit in the game folder and always load.'), 'switch', self)
        tw = ttk.Frame(tab, style='Panel.TFrame', padding=(10, 6))
        tw.pack(fill='x', pady=(0, 6))
        self.lbl_twse = ttk.Label(tw, text='', style='Panel.TLabel')
        self.lbl_twse.pack(side='left')
        self.help_mark(tw, tr('TWSE is the Two Worlds Script Extender by buglord. It loads the DLL plugins in TWSEPlugins, but only when the game starts through TwoWorldsExtended.exe. The Mod Manager sets it up from his GitHub release, points your Two Worlds shortcuts at it, and takes all of it back on "Remove TWSE".'), 'twse')
        self.btn_twse_off = ttk.Button(tw, text=tr('Remove TWSE'), command=self.remove_twse)
        self.btn_twse_off.pack(side='right')
        self.btn_twse = ttk.Button(tw, text=tr('Install TWSE'), command=self.install_twse)
        self.btn_twse.pack(side='right', padx=6)
        self.replace_var = tk.BooleanVar(value=False)
        self.chk_replace = ttk.Checkbutton(tw, text=tr('Replace TwoWorlds.exe (for Steam / GOG Galaxy)'),
                                           variable=self.replace_var, command=self.toggle_replace_exe)
        self.chk_replace.pack(side='right', padx=10)
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
        ttk.Button(btns, text=tr('Start game'), style='Accent.TButton', command=self.start_game).pack(side='right', padx=6)
        self.drop_hint = ttk.Label(tab, style='PanelMuted.TLabel', padding=(10, 8), anchor='center',
                                   text=tr('Drop a .wd or a .zip mod package anywhere on this window to install it.'))
        self.drop_hint.pack(fill='x', pady=(10, 0))
        ttk.Label(tab, style='Muted.TLabel', wraplength=860, justify='left',
                  text=tr('Double-click toggles a mod. Disabling keeps the file and sets its registry switch to 0 - exactly what the in-game Mod Selector does. Changes apply on the next game start.')
                  ).pack(anchor='w', pady=(8, 0))
        return tab

    def _bind_keys(self):
        r = self.root
        r.bind('<Control-o>', lambda e: self.add_mod())
        r.bind('<F5>', lambda e: self.refresh())
        r.bind('<Control-f>', lambda e: self._focus_search())
        r.bind('<F1>', lambda e: self.show_guide())

    def _filters(self):
        return [self.flt_inst]

    def _hide_suggestions(self):
        for f in self._filters():
            f.hide()

    def _focus_search(self):
        i = self.notebook.index(self.notebook.select())
        if i < 1:
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
        self.insight.stop()
        self.root.destroy()

    def set_lang(self, code):
        global _LANG
        if code == _LANG:
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
        self.pk = pkgs.Packages(self.game_dir, set_switch=registry_set, drop_switch=registry_delete)
        owners = self.pk.owners_by_wd()
        self._vortex_wd = self.pk.vortex_files()
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
            text = name + ('  ' + tr('(package {p})').format(p=owners[name]) if name in owners else '')
            if ('mods/' + name.lower()) in getattr(self, '_vortex_wd', set()):
                text += '  ' + tr('(Vortex)')
            self.tree.insert('', 'end', iid=name, text=text, values=(state, size, date, ''), tags=(tag,))
        for name in [f for f in os.listdir(self.game_dir) if f.lower().endswith('.wd')]:
            self.tree.insert('', 'end', iid='ROOT::' + name, text=name + '  ' + tr('(in the game folder!)'),
                             values=(tr('ALWAYS loads'), '', '', ''), tags=('warn',))
        self._fill_packages()
        self._row_tags = {iid: self.tree.item(iid, 'tags') for iid in self.tree.get_children()}
        self.insight.ensure([p for p in (self._row_path(i) for i in self.tree.get_children()) if p])
        self.flt_inst.set_rows([(i, '', self.tree.item(i, 'text').lower(), self.tree.item(i, 'text'))
                                for i in self.tree.get_children()])
        on = sum(1 for n in files if reg.get(n, 1))
        self.status(tr('{n} archives, {m} enabled').format(n=len(files), m=on))

    def _fill_packages(self):
        """Package rows (their plugin files below them), loose plugins, and
        the TWSE bar."""
        import datetime
        word = {'on': tr('enabled'), 'off': tr('disabled'), 'incomplete': tr('file missing')}
        for name in sorted(self.pk.all, key=str.lower):
            p = self.pk.all[name]
            st = self.pk.state(name)
            size = sum(os.path.getsize(self.pk.where(name, f)) for f in p['files']
                       if os.path.exists(self.pk.where(name, f)))
            iid = 'PKG::' + name
            label = tr('Package: {name}').format(name=name) + (f'  {p["version"]}' if p.get('version') else '')
            self.tree.insert('', 'end', iid=iid, text=label, open=False,
                             values=(word[st], fmt_size(size), p.get('installed', ''), ''),
                             tags=({'on': 'on', 'off': 'off'}.get(st, 'warn'),))
            for f in p['files']:
                there = os.path.exists(self.pk.where(name, f))
                self.tree.insert(iid, 'end', iid=f'PKGF::{name}::{f["dest"]}', text=f['dest'].replace('/', SEP),
                                 values=((tr('enabled') if p.get('enabled', True) else tr('disabled'))
                                         if there else tr('file missing'), '', '', ''),
                                 tags=('off' if not there or not p.get('enabled', True) else 'on',))
        self._vortex = self.pk.vortex_files()
        for dll, on in self.pk.loose_plugins():
            tag = tr('(TWSE plugin, Vortex)') if ('twseplugins/' + dll.lower()) in self._vortex else tr('(TWSE plugin)')
            self.tree.insert('', 'end', iid='PLUG::' + dll, text=dll + '  ' + tag,
                             values=(tr('enabled') if on else tr('disabled'), '', '', ''), tags=('on' if on else 'off',))
        st = twse_install.status(self.game_dir)
        if st['installed']:
            text = tr('TWSE: installed') + (f' ({st["dll_label"]})' if st['dll_label'] else '')
            if st['exe_restored_by_launcher']:
                text += '  ·  ' + tr('Steam or an update put the original TwoWorlds.exe back')
        elif st['removed_by_user']:
            text = tr('TWSE: removed')
        else:
            text = tr('TWSE: not installed - DLL plugins do not load')
        needs = [n for n, p in self.pk.all.items() if p.get('needs_twse') and p.get('enabled', True)]
        if needs and not st['installed']:
            text += '  ·  ' + tr('needed by: {names}').format(names=', '.join(needs[:3]))
        self.lbl_twse.configure(text=text, foreground=theme.OK if st['installed'] else (theme.ERR if needs else theme.MUT))
        self.btn_twse.configure(text=tr('Update TWSE') if st['installed'] else tr('Install TWSE'))
        self.btn_twse_off.state(['!disabled'] if (st['installed'] or st['record'].get('shortcuts')) else ['disabled'])
        self.replace_var.set(st['exe_replaced'])
        self.chk_replace.state(['!disabled'] if st['installed'] else ['disabled'])

    def _row_path(self, iid):
        if iid.startswith(('PKG::', 'PKGF::', 'PLUG::')):
            return ''
        if iid.startswith('ROOT::'):
            return os.path.join(self.game_dir, iid[6:])
        return os.path.join(self.mods_dir, iid)

    def _row_tip(self, iid):
        """Hover text: what the mod changes, and how it fits the selected one."""
        import modscan
        path = self._row_path(iid)
        if not path or not os.path.isfile(path):
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
            if not sel or iid == sel or not self._row_path(sel) or not os.path.isfile(self._row_path(sel)):
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
        if iid.startswith(('PKG::', 'PKGF::', 'PLUG::')):
            p = self._special_path(iid)
            if p and os.path.exists(p):
                subprocess.Popen(['explorer', '/select,', p])
            return
        name = iid[6:] if iid.startswith('ROOT::') else iid
        folder = self.game_dir if iid.startswith('ROOT::') else self.mods_dir
        p = os.path.join(folder, name)
        if os.path.exists(p):
            subprocess.Popen(['explorer', '/select,', p])

    def _package_of(self, iid):
        """Package name for a package row, one of its files, or one of its .wd rows."""
        if iid.startswith('PKG::'):
            return iid[5:]
        if iid.startswith('PKGF::'):
            return iid[6:].split('::', 1)[0]
        return self.pk.owners_by_wd().get(iid) if getattr(self, 'pk', None) else None

    def _special_path(self, iid):
        if iid.startswith('PLUG::'):
            dll = iid[6:]
            on = os.path.join(self.game_dir, pkgs.PLUGINS, dll)
            return on if os.path.exists(on) else os.path.join(self.game_dir, pkgs.PLUGINS, pkgs.DISABLED, dll)
        name = self._package_of(iid)
        if not name or name not in self.pk.all:
            return ''
        p = self.pk.all[name]
        if iid.startswith('PKGF::'):
            dest = iid.split('::', 2)[2]
            f = next((x for x in p['files'] if x['dest'] == dest), None)
            return self.pk.where(name, f) if f else ''
        return self.pk.where(name, p['files'][0]) if p['files'] else ''

    def _vortex_owned(self, iid):
        v = getattr(self, '_vortex_wd', set())
        if iid.startswith('PLUG::'):
            return ('twseplugins/' + iid[6:].lower()) in v
        return ('mods/' + iid.lower()) in v

    def toggle(self):
        name = self._selected()
        if not name:
            return
        if self._vortex_owned(name):
            self.status(tr('Vortex manages this file - switch it in Vortex.'), error=True)
            return
        pkg = self._package_of(name)
        if pkg or name.startswith('PLUG::'):
            if game_running():
                self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
                return
            try:
                if pkg:
                    on = not self.pk.all[pkg].get('enabled', True)
                    self.pk.set_enabled(pkg, on)
                    msg = tr('Package {name} {state} - takes effect on the next game start.').format(
                        name=pkg, state=tr('enabled') if on else tr('disabled'))
                else:
                    dll = name[6:]
                    on = not os.path.exists(os.path.join(self.game_dir, pkgs.PLUGINS, dll))
                    self.pk.set_loose(dll, on)
                    msg = tr('Plugin {name} {state} - takes effect on the next game start.').format(
                        name=dll, state=tr('enabled') if on else tr('disabled'))
            except (pkgs.PackageError, OSError) as e:
                self.error('package.switch', 'Switching a package or plugin failed', str(e), 'twse')
                return
            self.refresh()
            sel = 'PKG::' + pkg if pkg else name
            if self.tree.exists(sel):
                self.tree.selection_set(sel)
            self.status(msg + '  ' + self.sync_level_cache(checked=True))
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
                                            filetypes=[(tr('Two Worlds mod or package'), '*.wd;*.zip'),
                                                       (tr('Two Worlds mod'), '*.wd'), (tr('Mod package'), '*.zip')])
        if paths:
            self.offer_install(list(paths))

    def on_drop(self, files):
        wds = [f for f in files if f.lower().endswith(('.wd', '.zip')) and os.path.isfile(f)]
        others = [f for f in files if f not in wds]
        if others and not wds:
            self.status(tr('Only .wd archives and .zip packages can be installed ({n} other file(s) ignored).').format(n=len(others)), error=True)
        self.root.lift()
        self.root.focus_force()
        if wds:
            self.offer_install(wds)

    def offer_install(self, paths):
        """The one dialog for dropped, picked or command-line archives."""
        for path in paths:
            if path.lower().endswith('.zip'):
                self.install_package(path)
                continue
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
        if self._vortex_owned(name):
            self.status(tr('Vortex manages this file - remove it in Vortex.'), error=True)
            return
        pkg = self._package_of(name)
        if pkg or name.startswith('PLUG::'):
            self._remove_package_or_plugin(pkg, name)
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

    def _remove_package_or_plugin(self, pkg, iid):
        if game_running():
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
            return
        if pkg:
            n = len(self.pk.all[pkg]['files'])
            if not messagebox.askyesno(APP_NAME, tr('Move all {n} files of the package {name} to Mods\\_removed and forget the package?\nNothing is deleted.').format(n=n, name=pkg), parent=self.root):
                return
            try:
                keep = self.pk.remove(pkg)
            except OSError as e:
                self.error('package.remove', 'Removing a package failed', str(e), 'twse')
                return
            self.refresh()
            self.status(tr('Package {name} moved to {folder} - nothing was deleted.').format(
                name=pkg, folder=os.path.relpath(keep, self.game_dir)) + '  ' + self.sync_level_cache())
            return
        dll = iid[6:]
        if not messagebox.askyesno(APP_NAME, tr('Move the plugin {name} to Mods\\_removed?\nNothing is deleted.').format(name=dll), parent=self.root):
            return
        keep = os.path.join(self.mods_dir, pkgs.REMOVED, 'plugins')
        os.makedirs(keep, exist_ok=True)
        stem = os.path.splitext(dll)[0]
        for folder in (os.path.join(self.game_dir, pkgs.PLUGINS), os.path.join(self.game_dir, pkgs.PLUGINS, pkgs.DISABLED)):
            for n in (dll, stem + '.ini'):
                src = os.path.join(folder, n)
                if os.path.exists(src):
                    dst = os.path.join(keep, n)
                    i = 2
                    while os.path.exists(dst):
                        dst = os.path.join(keep, f'{stem}_{i}{os.path.splitext(n)[1]}')
                        i += 1
                    shutil.move(src, dst)
        self.refresh()
        self.status(tr('Plugin {name} moved to Mods\\_removed\\plugins - nothing was deleted.').format(name=dll))

    # ---- packages ----
    def install_package(self, path):
        def check(p):
            r = wd_peek(p)
            return r['ok'], r['error']
        plan = pkgs.inspect(path, check)
        self.pk = pkgs.Packages(self.game_dir, set_switch=registry_set, drop_switch=registry_delete)
        dlg = PackageDialog(self, path, plan)
        if not dlg.result:
            return
        if game_running():
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
            return
        # Marco 19.10.2026: a mod with a DLL (or "requires": ["twse"]) secures TWSE itself. Not installed: ask first;
        # "Install" sets TWSE up and then the mod, "Cancel" writes nothing at all. Already installed: no question.
        if plan['ok'] and plan['needs_twse'] and not twse_install.status(self.game_dir)['installed']:
            if not self.ask_twse_needed(plan):
                self.status(tr('Cancelled - neither TWSE nor the mod were installed.'))
                return
            self.install_twse(after=lambda: self._install_package_now(path, plan, dlg.result))
            return
        self._install_package_now(path, plan, dlg.result)

    def ask_twse_needed(self, plan):
        """True = install TWSE and the mod, False = cancel and write nothing."""
        return TwseNeededDialog(self, plan).result is True

    def _install_package_now(self, path, plan, result):
        if game_running():
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
            return
        try:
            notes = self.pk.install(path, plan, enable=(result == 'enable'))
        except (pkgs.PackageError, OSError) as e:
            self.fb.log.add('package install failed')
            self.error('package.install', 'Installing a mod package failed', tr('Install failed: {e}').format(e=e), 'install')
            return
        self.refresh()
        if self.tree.exists('PKG::' + plan['name']):
            self.tree.selection_set('PKG::' + plan['name'])
            self.tree.see('PKG::' + plan['name'])
        self.notebook.select(0)
        self.status(tr('Package {name} installed ({n} files).').format(name=plan['name'], n=len(plan['files']))
                    + '  ' + self.sync_level_cache())
        if notes:
            messagebox.showinfo(APP_NAME, chr(10).join(notes), parent=self.root)

    # ---- TWSE ----
    def install_twse(self, after=None):
        if game_running():
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
            return
        self._twse_after = after
        self.btn_twse.state(['disabled'])
        self.status(tr('Looking for the newest TWSE release on GitHub...'))
        self._twse_got = None
        threading.Thread(target=self._twse_fetch, daemon=True).start()
        self.root.after(100, self._twse_poll)

    def _twse_poll(self):
        # the download runs in a thread; Tk is only touched here, on the main thread
        if self._twse_got is None:
            self.root.after(100, self._twse_poll)
            return
        self._twse_apply(*self._twse_got)

    def _twse_fetch(self):
        try:
            rel = twse_install.latest_release(http_get)
            data = http_get(rel['url'], timeout=60)
            dll, known = twse_install.dll_from_release(data, rel)
            got = ('github', dll, known, rel['tag'], '')
        except Exception as e:                       # no connection, rate limit, checksum
            got = ('bundled', None, True, '', str(e))
        self._twse_got = got

    def _twse_apply(self, kind, dll, known, tag, why):
        try:
            if kind == 'github' and not known:
                if not messagebox.askyesno(APP_NAME, tr('GitHub offers TWSE {tag}, a twse.dll we have not checked yet. The download matches the checksum GitHub publishes.\n\nInstall it? "No" installs the checked version that comes with the Mod Manager.').format(tag=tag), parent=self.root):
                    kind = 'bundled'
            if kind == 'bundled':
                dll = twse_install.bundled_dll(getattr(sys, '_MEIPASS', HERE))
                source = 'v0.1.0 (Mod Manager)'
                if why:
                    self.status(tr('GitHub not reachable ({e}) - using the checked twse.dll that comes with the Mod Manager.').format(e=why[:80]))
            else:
                source = f'{tag} (GitHub)'
            notes = twse_install.install(self.game_dir, dll, source, replace_exe=self.replace_var.get())
        except twse_install.TwseError as e:
            self._twse_failed()
            self.error('twse.failed', 'Setting up TWSE failed', str(e), 'twse')
            return
        except PermissionError as e:
            self._twse_failed()
            self.error('twse.rights', 'No write access to the game folder', tr('No write access: {e}').format(e=e), 'twse')
            return
        except OSError as e:
            self._twse_failed()
            self.error('twse.failed', 'Setting up TWSE failed', str(e), 'twse')
            return
        finally:
            self.btn_twse.state(['!disabled'])
            self.refresh()
        self.fb.log.add('twse installed ' + source)
        self.status(tr('TWSE set up ({source}). Start the game with "Start game" or a Two Worlds shortcut.').format(source=source))
        messagebox.showinfo(APP_NAME, tr('TWSE is set up:') + chr(10) + chr(10).join('- ' + n for n in notes), parent=self.root)
        after, self._twse_after = getattr(self, '_twse_after', None), None
        if after:
            after()                                   # the mod that asked for TWSE follows now

    def _twse_failed(self):
        # a mod was waiting for TWSE: it stays uninstalled, nothing was written for it
        if getattr(self, '_twse_after', None):
            self._twse_after = None
            self.status(tr('TWSE could not be set up - the mod was not installed.'))

    def remove_twse(self):
        if game_running():
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
            return
        if not messagebox.askyesno(APP_NAME, tr('Remove TWSE?\n\nShortcuts get their old targets back, a replaced TwoWorlds.exe returns, TwoWorldsExtended.exe and twse.dll move to Mods\\_removed. Plugins stay in TWSEPlugins but do not load without TWSE. Nothing is deleted.'), parent=self.root):
            return
        foreign = twse_install.foreign_files(self.game_dir)
        include = bool(foreign) and messagebox.askyesno(APP_NAME, tr('{files} was not set up by the Mod Manager (by hand, by Extended Settings, or a patched copy). Move it to Mods\\_removed as well?\n\n"No" leaves it where it is - TWSE then stays usable.').format(files=', '.join(foreign)), parent=self.root)
        try:
            notes, _keep = twse_install.uninstall(self.game_dir, include_foreign=include)
        except (twse_install.TwseError, OSError) as e:
            self.error('twse.remove', 'Removing TWSE failed', str(e), 'twse')
            return
        finally:
            self.refresh()
        self.fb.log.add('twse removed')
        self.status(tr('TWSE removed - nothing was deleted.'))
        messagebox.showinfo(APP_NAME, chr(10).join('- ' + n for n in notes) or tr('Nothing to do.'), parent=self.root)

    def toggle_replace_exe(self):
        on = self.replace_var.get()
        if game_running():
            self.replace_var.set(not on)
            self.error('game.running', 'Two Worlds is running', tr('Close Two Worlds first - it reads the mod list only at start.'))
            return
        if on and not messagebox.askyesno(APP_NAME, tr('Replace TwoWorlds.exe by the extended exe?\n\nThen Steam, GOG Galaxy and the Mod Selector start the game with TWSE too. The original is kept as TwoWorlds.exe.twse-backup. "Verify files" in Steam or a game update puts the original back - the Mod Manager shows it.'), parent=self.root):
            self.replace_var.set(False)
            return
        try:
            notes = twse_install.set_replace_exe(self.game_dir, on)
        except (twse_install.TwseError, OSError) as e:
            self.replace_var.set(not on)
            self.error('twse.replace', 'Replacing TwoWorlds.exe failed', str(e), 'twse')
            return
        self.refresh()
        self.status(' '.join(notes) or tr('Nothing to do.'))

    def start_game(self):
        if not self.game_dir:
            return
        if game_running():
            self.status(tr('Two Worlds is already running.'), error=True)
            return
        exe = twse_install.launch_exe(self.game_dir)
        try:
            try:
                subprocess.Popen([exe], cwd=self.game_dir)
            except OSError as e:
                if getattr(e, 'winerror', None) != 740:      # exe asks for administrator rights
                    raise
                import ctypes                                 # let the shell show the UAC prompt
                rc = ctypes.windll.shell32.ShellExecuteW(None, 'runas', exe, None, self.game_dir, 1)
                if rc <= 32:
                    raise OSError('administrator start refused or cancelled (code %s)' % rc)
        except OSError as e:
            self.error('game.start', 'Starting the game failed', str(e), 'trouble')
            return
        self.status(tr('Started {exe}.').format(exe=os.path.basename(exe)))

    # ---- server ----
    # ---- GitHub archive (InsideTwoWorlds/MODs) ----
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
    'package.install': ('install',
        'Is Two Worlds closed? A file of the package may be open in another program. If the game sits under Program Files, start the Mod Manager as administrator once.',
        'Ist Two Worlds geschlossen? Eine Datei des Pakets ist vielleicht in einem anderen Programm offen. Liegt das Spiel unter Programme, den Mod Manager einmal als Administrator starten.'),
    'package.switch': ('twse',
        'Is Two Worlds closed? A running game locks its plugins. If a file is "in the way", a file with that name already sits where it should go - move one of them.',
        'Ist Two Worlds geschlossen? Ein laufendes Spiel sperrt seine Plugins. Steht eine Datei "im Weg", liegt dort schon eine Datei mit dem Namen - eine davon wegschieben.'),
    'package.remove': ('twse',
        'Close Two Worlds and every program that has a file of the package open, then try again.',
        'Two Worlds und jedes Programm schliessen, das eine Datei des Pakets offen hat, dann noch einmal.'),
    'twse.failed': ('twse',
        'TWSE needs Two Worlds 1.7 (Epic Edition on Steam or GOG). Without a connection the Mod Manager uses its own checked twse.dll. If it keeps failing, report the bug - the log goes with it.',
        'TWSE braucht Two Worlds 1.7 (Epic Edition bei Steam oder GOG). Ohne Verbindung nimmt der Mod Manager seine eigene gepruefte twse.dll. Klappt es weiter nicht, den Bug melden - das Protokoll geht mit.'),
    'twse.rights': ('twse',
        'The game folder is write-protected (often under Program Files). Close the Mod Manager, right-click it, "Run as administrator", and install TWSE once.',
        'Der Spielordner ist schreibgeschuetzt (oft unter Programme). Mod Manager schliessen, Rechtsklick, "Als Administrator ausfuehren" und TWSE einmal einrichten.'),
    'twse.replace': ('twse',
        'Only the original TwoWorlds.exe 1.7 can be replaced. If Steam updated the game, install TWSE again first.',
        'Nur die originale TwoWorlds.exe 1.7 laesst sich ersetzen. Hat Steam das Spiel aktualisiert, TWSE zuerst neu einrichten.'),
    'twse.remove': ('twse',
        'Close Two Worlds and try again. Shortcuts in the shared start menu need administrator rights.',
        'Two Worlds schliessen und noch einmal. Verknuepfungen im gemeinsamen Startmenue brauchen Administratorrechte.'),
    'game.start': ('trouble',
        'Check that TwoWorlds.exe (or TwoWorldsExtended.exe) is in the game folder. File > Change game path if the folder is wrong.',
        'Pruefen, ob TwoWorlds.exe (oder TwoWorldsExtended.exe) im Spielordner liegt. Datei > Spielpfad aendern, wenn der Ordner falsch ist.'),
    'crash': ('trouble',
        'Please report it: the log goes with it, and nothing is sent before you have seen it.',
        'Bitte melden: das Protokoll geht mit, und nichts wird verschickt, bevor du es gesehen hast.'),
}


class ErrorDialog:
    """An error: message, OK, optional guide."""

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
        if guide:
            ttk.Button(btns, text=tr('Read in the guide'),
                       command=lambda: app.show_guide(guide)).pack(side='left')
        win.update_idletasks()
        win.geometry(f'+{app.root.winfo_rootx() + 80}+{app.root.winfo_rooty() + 80}')
        win.focus_set()


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
    'Two Worlds loads mods from the Mods folder next to the game and reads in the registry which of them are on. This window shows both, switches them and installs new ones. Nothing is ever deleted.':
        'Two Worlds laedt Mods aus dem Mods-Ordner neben dem Spiel und liest in der Registry, welche davon an sind. Dieses Fenster zeigt beides, schaltet um und legt neue ein. Nichts wird je geloescht.',
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
    'F1 opens the guide with chapters, search and the registry reference. The gold ? marks jump straight to the matching chapter.':
        'F1 oeffnet den Guide mit Kapiteln, Suche und der Registry-Referenz. Die goldenen ?-Marken springen direkt ins passende Kapitel.',
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
    'Other versions': 'Weitere Versionen', 'Search': 'Suche', 'No match': 'Keine Treffer', 'Copy': 'Kopieren', 'Select all': 'Alles markieren',
    'loading picture...': 'lade Bild...', 'Archive: {name}': 'Archiv: {name}',
    # --- Mod Manager 2.6.0: Pakete (.zip), Plugins, TWSE
    '{files} was not set up by the Mod Manager (by hand, by Extended Settings, or a patched copy). Move it to Mods\\_removed as well?\n\n"No" leaves it where it is - TWSE then stays usable.': '{files} hat nicht der Mod Manager angelegt (von Hand, von Extended Settings oder eine gepatchte Kopie). Auch nach Mods\\_removed verschieben?\n\n"Nein" laesst es liegen - TWSE bleibt dann nutzbar.',
    '(TWSE plugin, Vortex)': '(TWSE-Plugin, Vortex)',
    '(Vortex)': '(Vortex)',
    'Vortex manages this file - switch it in Vortex.': 'Vortex verwaltet diese Datei - in Vortex schalten.',
    'Vortex manages this file - remove it in Vortex.': 'Vortex verwaltet diese Datei - in Vortex entfernen.',
    'Load this mod package?': 'Dieses Mod-Paket einlegen?',
    'TWSE is the Two Worlds Script Extender by buglord. It loads the DLL plugins in TWSEPlugins, but only when the game starts through TwoWorldsExtended.exe. The Mod Manager sets it up from his GitHub release, points your Two Worlds shortcuts at it, and takes all of it back on "Remove TWSE".': 'TWSE ist der Two Worlds Script Extender von buglord. Er laedt die DLL-Plugins aus TWSEPlugins, aber nur, wenn das Spiel ueber TwoWorldsExtended.exe startet. Der Mod Manager richtet ihn aus seinem GitHub-Release ein, stellt deine Two-Worlds-Verknuepfungen darauf um und macht alles mit "TWSE entfernen" wieder rueckgaengig.',
    'Looking for the newest TWSE release on GitHub...': 'Suche das neueste TWSE-Release auf GitHub...',
    'TWSE removed - nothing was deleted.': 'TWSE entfernt - nichts wurde geloescht.',
    'new': 'neu',
    'unchanged': 'unveraendert',
    'updated': 'aktualisiert',
    'yours kept, new one as .ini.new': 'deine bleibt, die neue als .ini.new',
    'replaces a file of no package (kept as .backup)': 'ersetzt eine Datei ohne Paket (bleibt als .backup)',
    'Remove TWSE': 'TWSE entfernen',
    'Install TWSE': 'TWSE installieren',
    'Replace TwoWorlds.exe (for Steam / GOG Galaxy)': 'TwoWorlds.exe ersetzen (fuer Steam / GOG Galaxy)',
    'Drop a .wd or a .zip mod package anywhere on this window to install it.': 'Zieh eine .wd oder ein .zip-Mod-Paket irgendwo auf dieses Fenster, um es zu installieren.',
    'This mod needs the TW1 Script Extender (TWSE), and it is not installed. You are asked about it when you install.': 'Diese Mod braucht den TW1 Script Extender (TWSE), und er ist nicht installiert. Beim Installieren wirst du danach gefragt.',
    'Cancelled - neither TWSE nor the mod were installed.': 'Abgebrochen - weder TWSE noch die Mod wurden installiert.',
    'TWSE could not be set up - the mod was not installed.': 'TWSE konnte nicht eingerichtet werden - die Mod wurde nicht installiert.',
    'TWSE needed': 'TWSE noetig',
    'This mod needs the TW1 Script Extender (TWSE)': 'Diese Mod braucht den TW1 Script Extender (TWSE)',
    'Game plugins in the package: ': 'Spiel-Plugins im Paket: ',
    'The package says it requires TWSE.': 'Das Paket gibt an, dass es TWSE braucht.',
    'Install: TWSE is set up first (patched exe, shortcuts), then the mod {name}.\nCancel: nothing is installed or written.': 'Installieren: erst wird TWSE eingerichtet (gepatchte Exe, Verknuepfungen), dann die Mod {name}.\nAbbrechen: es wird nichts installiert oder geschrieben.',
    'Install': 'Installieren',
    'TWSE: installed': 'TWSE: installiert',
    'TWSE: removed': 'TWSE: entfernt',
    'TWSE: not installed - DLL plugins do not load': 'TWSE: nicht installiert - DLL-Plugins laden nicht',
    'Remove TWSE?\n\nShortcuts get their old targets back, a replaced TwoWorlds.exe returns, TwoWorldsExtended.exe and twse.dll move to Mods\\_removed. Plugins stay in TWSEPlugins but do not load without TWSE. Nothing is deleted.': 'TWSE entfernen?\n\nDie Verknuepfungen bekommen ihre alten Ziele zurueck, eine ersetzte TwoWorlds.exe kommt wieder, TwoWorldsExtended.exe und twse.dll wandern nach Mods\\_removed. Plugins bleiben in TWSEPlugins, laden aber ohne TWSE nicht. Nichts wird geloescht.',
    'Nothing to do.': 'Nichts zu tun.',
    'Two Worlds is already running.': 'Two Worlds laeuft schon.',
    'Steam or an update put the original TwoWorlds.exe back': 'Steam oder ein Update hat die originale TwoWorlds.exe zurueckgebracht',
    'Update TWSE': 'TWSE aktualisieren',
    'Plugin {name} moved to Mods\\_removed\\plugins - nothing was deleted.': 'Plugin {name} nach Mods\\_removed\\plugins verschoben - nichts wurde geloescht.',
    'TWSE set up ({source}). Start the game with "Start game" or a Two Worlds shortcut.': 'TWSE eingerichtet ({source}). Starte das Spiel mit "Spiel starten" oder ueber eine Two-Worlds-Verknuepfung.',
    'TWSE is set up:': 'TWSE ist eingerichtet:',
    'Replace TwoWorlds.exe by the extended exe?\n\nThen Steam, GOG Galaxy and the Mod Selector start the game with TWSE too. The original is kept as TwoWorlds.exe.twse-backup. "Verify files" in Steam or a game update puts the original back - the Mod Manager shows it.': 'TwoWorlds.exe durch die erweiterte exe ersetzen?\n\nDann starten auch Steam, GOG Galaxy und der Mod Selector das Spiel mit TWSE. Das Original bleibt als TwoWorlds.exe.twse-backup erhalten. "Dateien ueberpruefen" in Steam oder ein Spiel-Update bringt das Original zurueck - der Mod Manager zeigt es an.',
    'Started {exe}.': '{exe} gestartet.',
    'Shown, not installed: ': 'Nur angezeigt, nicht installiert: ',
    'Start game': 'Spiel starten',
    'Package: {name}': 'Paket: {name}',
    '(TWSE plugin)': '(TWSE-Plugin)',
    'needed by: {names}': 'gebraucht von: {names}',
    'Two Worlds mod or package': 'Two-Worlds-Mod oder -Paket',
    'Mod package': 'Mod-Paket',
    'Only .wd archives and .zip packages can be installed ({n} other file(s) ignored).': 'Nur .wd-Archive und .zip-Pakete lassen sich installieren ({n} andere Datei(en) ignoriert).',
    'Move the plugin {name} to Mods\\_removed?\nNothing is deleted.': 'Das Plugin {name} nach Mods\\_removed verschieben?\nNichts wird geloescht.',
    'Ignored: ': 'Ignoriert: ',
    'Contains a game plugin (DLL): program code that runs inside Two Worlds. Install it only from a source you trust.': 'Enthaelt ein Spiel-Plugin (DLL): Programmcode, der in Two Worlds laeuft. Installiere es nur aus einer Quelle, der du vertraust.',
    'Also install TWSE (the plugins need it)': 'TWSE auch installieren (die Plugins brauchen es)',
    'Package {name} {state} - takes effect on the next game start.': 'Paket {name}: {state} - gilt ab dem naechsten Spielstart.',
    'Plugin {name} {state} - takes effect on the next game start.': 'Plugin {name}: {state} - gilt ab dem naechsten Spielstart.',
    'Move all {n} files of the package {name} to Mods\\_removed and forget the package?\nNothing is deleted.': 'Alle {n} Dateien des Pakets {name} nach Mods\\_removed verschieben und das Paket vergessen?\nNichts wird geloescht.',
    'Package {name} installed ({n} files).': 'Paket {name} installiert ({n} Dateien).',
    'No write access: {e}': 'Kein Schreibzugriff: {e}',
    '(package {p})': '(Paket {p})',
    'Package {name} moved to {folder} - nothing was deleted.': 'Paket {name} nach {folder} verschoben - nichts wurde geloescht.',
    'GitHub offers TWSE {tag}, a twse.dll we have not checked yet. The download matches the checksum GitHub publishes.\n\nInstall it? "No" installs the checked version that comes with the Mod Manager.': 'GitHub bietet TWSE {tag} an, eine twse.dll, die wir noch nicht geprueft haben. Der Download passt zur Pruefsumme, die GitHub veroeffentlicht.\n\nInstallieren? "Nein" installiert die gepruefte Version, die mit dem Mod Manager kommt.',
    'GitHub not reachable ({e}) - using the checked twse.dll that comes with the Mod Manager.': 'GitHub nicht erreichbar ({e}) - nehme die gepruefte twse.dll, die mit dem Mod Manager kommt.',
    'merged mod': 'zusammengefuehrte Mod',
    'Refused - nothing will be installed:': 'Abgelehnt - es wird nichts installiert:',
    '{dest}: {why} - remove that package first.': '{dest}: {why} - entferne zuerst dieses Paket.',
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
    run_gui([a for a in sys.argv[1:] if a.lower().endswith(('.wd', '.zip')) and os.path.isfile(a)])
