"""Window parts for what mods change and how they fit together.

- ``Insight``: scans archives in a worker thread (modscan.scan, cached on
  disk) and hands results to the Tk thread through a queue.
- ``RowTips``: hover text per tree row - which kinds of files a mod changes.



The strings go through ``app.tr`` (table DE in mod_manager.py).
"""

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import modscan
import theme

CHECK_ON, CHECK_OFF = '☑', '☐'
LEVEL_TAG = {'green': 'fit_green', 'yellow': 'fit_yellow', 'red': 'fit_red', 'unknown': 'fit_none'}


def configure_tags(tree):
    tree.tag_configure('fit_green', foreground=theme.OK)
    tree.tag_configure('fit_yellow', foreground=theme.GOLD)
    tree.tag_configure('fit_red', foreground=theme.ERR)
    tree.tag_configure('fit_none', foreground=theme.MUT)
    tree.tag_configure('fit_self', foreground=theme.INK)


class Insight:
    """Scan results for archives, made off the Tk thread."""

    def __init__(self, app):
        self.app = app
        self.infos = {}
        self.pairs = {}
        self._retail = None
        self._retail_dir = None
        self._q = queue.Queue()
        self._busy = set()
        self._listeners = []
        self._polling = False
        self._stopped = False
        self._after = None
        self._keys, self._keys_at = {}, 0.0

    def retail(self):
        if self._retail is None or self._retail_dir != self.app.game_dir:
            self._retail = modscan.Retail(self.app.game_dir)
            self._retail_dir = self.app.game_dir
            self.infos.clear()
            self.pairs.clear()
        return self._retail

    def _key(self, path):
        now = time.monotonic()
        if now - self._keys_at > 0.4:
            self._keys, self._keys_at = {}, now
        k = self._keys.get(path)
        if k is None:
            try:
                st = os.stat(path)
                k = (os.path.normcase(os.path.abspath(path)), int(st.st_mtime), st.st_size)
            except OSError:
                k = (os.path.normcase(os.path.abspath(path)), 0, 0)
            self._keys[path] = k
        return k

    def info(self, path):
        return self.infos.get(self._key(path))

    def on_ready(self, fn):
        self._listeners.append(fn)

    def ensure(self, paths):
        """Scan what is not known yet; listeners fire when something arrived."""
        todo = [p for p in paths if self._key(p) not in self.infos and self._key(p) not in self._busy
                and os.path.isfile(p)]
        if not todo or not self.app.game_dir:
            return
        try:
            retail = self.retail()                 # on the Tk thread: it clears the shared dicts
        except Exception as e:
            for p in todo:
                self.infos[self._key(p)] = {'error': str(e), 'files': {}, 'kinds': {}}
            return
        jobs = [(self._key(p), p) for p in todo]   # the key as it was when the scan was ordered
        for key, _p in jobs:
            self._busy.add(key)
        ddir = self.app.data_dir()

        def work():
            for key, p in jobs:
                try:
                    info = modscan.scan(p, retail, ddir)
                except Exception as e:                       # never kill the worker
                    info = {'error': f'{type(e).__name__}: {e}', 'files': {}, 'kinds': {}}
                self._q.put((key, info))
        threading.Thread(target=work, daemon=True).start()
        if not self._polling:
            self._polling = True
            self._after = self.app.root.after(60, self._poll)

    def _poll(self):
        got = False
        while True:
            try:
                key, info = self._q.get_nowait()
            except queue.Empty:
                break
            self.infos[key] = info
            self._busy.discard(key)
            got = True
        if got:
            for fn in list(self._listeners):
                try:
                    fn()
                except tk.TclError:
                    pass
        if self._busy and not self._stopped:
            self._after = self.app.root.after(80, self._poll)
        else:
            self._polling = False

    def stop(self):
        """Before the window goes away (language switch, close)."""
        self._stopped = True
        if getattr(self, '_after', None):
            try:
                self.app.root.after_cancel(self._after)
            except tk.TclError:
                pass

    def compare(self, a, b):
        ia, ib = self.info(a), self.info(b)
        if ia is None or ib is None:
            return None
        ka, kb = self._key(a), self._key(b)
        key = (ka, kb) if ka <= kb else (kb, ka)
        if key not in self.pairs:
            try:
                self.pairs[key] = modscan.compare(a, ia, b, ib) if ka <= kb else modscan.compare(b, ib, a, ia)
            except Exception:
                self.pairs[key] = {'level': 'unknown'}
        return self.pairs[key]

    def worst(self, path, others):
        """Worst level of ``path`` against every path in ``others`` and the texts."""
        rank = {'green': 0, 'unknown': 1, 'yellow': 2, 'red': 3}
        level, lines = 'green', []
        for o in others:
            if self._key(o) == self._key(path):
                continue
            c = self.compare(path, o)
            if c is None:
                level = max(level, 'unknown', key=rank.get)
                continue
            if c['level'] != 'green':
                lines.append(os.path.basename(o) + ': ' + '; '.join(modscan.compare_lines(c, self.app.tr)))
            level = max(level, c['level'], key=rank.get)
        return level, lines


class RowTips:
    """Hover over a tree row: what the mod changes (and how it fits)."""

    def __init__(self, tree, text_of):
        self.tree = tree
        self.text_of = text_of
        self.tip = theme.FloatTip(tree)
        self._row = None
        self._after = None
        tree.bind('<Motion>', self._move, add='+')
        tree.bind('<Leave>', lambda e: self._off(), add='+')
        tree.bind('<ButtonPress>', lambda e: self._off(), add='+')

    def _off(self):
        if self._after:
            self.tree.after_cancel(self._after)
            self._after = None
        self._row = None
        self.tip.hide()

    def _move(self, ev):
        row = self.tree.identify_row(ev.y)
        if row != self._row:
            self._off()
            self._row = row
            if row:
                self._after = self.tree.after(450, lambda: self._show(row, ev.x_root, ev.y_root))
        elif self.tip.tip is not None:
            self.tip.show(self.tip.text, ev.x_root, ev.y_root)

    def _show(self, row, x, y):
        self._after = None
        if row == self._row:
            self.tip.show(self.text_of(row), x, y)


def fit_text(tr, level, comparison_lines):
    if level == 'green':
        return tr('fits')
    if level == 'yellow':
        return tr('overlaps')
    if level == 'red':
        return tr('clashes')
    return ''
