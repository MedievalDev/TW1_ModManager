"""Window parts for what mods change, how they fit together, and the merger.

- ``Insight``: scans archives in a worker thread (modscan.scan, cached on
  disk) and hands results to the Tk thread through a queue.
- ``RowTips``: hover text per tree row - which kinds of files a mod changes.
- ``MergeTab``: tick mods, see green / yellow / red against the ticked ones,
  name the result, merge. ``ConflictDialog`` asks per overlap.

The strings go through ``app.tr`` (table DE in mod_manager.py).
"""

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import merger
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


class MergeTab:
    def __init__(self, app, parent):
        self.app = app
        tr = app.tr
        self.extra = []                 # archives added from outside the Mods folder
        self.checked = []               # paths, in tick order = merge order
        self.frame = tab = ttk.Frame(parent, padding=12)
        hdr = ttk.Frame(tab)
        hdr.pack(fill='x')
        ttk.Label(hdr, text=tr('Merge mods into one new mod'), style='Muted.TLabel').pack(side='left')
        app.help_mark(hdr, tr('Tick the mods to merge. Green: no overlap inside files. Yellow: overlaps inside files, shared maps or whole files - each one is asked, however many there are. Red: both change compiled scripts - one mod has to be the main mod and keeps its scripts; everything else is still asked. The source mods are only read.'), 'merge')
        # "experimental" until two people confirmed the in-game tests (untested.json)
        fb = getattr(app, 'fb', None)
        experimental = fb is None or fb.experimental('merge') or fb.experimental('merge-maps')
        warn = tr('EXPERIMENTAL - the merged mod may be buggy or keep the game from starting. Your mods are never changed; the result is a new archive.') \
            if experimental else tr('Your mods are never changed; the result is a new archive.')
        row = ttk.Frame(tab)
        row.pack(fill='x', pady=(2, 6))
        tk.Label(row, text=warn, bg=theme.BG, fg=theme.GOLD if experimental else theme.MUT, anchor='w',
                 justify='left', wraplength=700).pack(side='left', fill='x', expand=True)
        if fb is not None and experimental:
            ttk.Button(row, text=tr('Help testing it'), command=lambda: fb.show_tests('merge-yellow-ingame')
                       ).pack(side='right')
        self.tree = ttk.Treeview(tab, columns=('what', 'fit'), show='tree headings', height=9)
        self.tree.heading('#0', text=tr('Mod archive'))
        self.tree.heading('what', text=tr('Changes'))
        self.tree.heading('fit', text=tr('With the ticked mods'))
        self.tree.column('#0', width=300)
        self.tree.column('what', width=330)
        self.tree.column('fit', width=200, anchor='center')
        self.tree.pack(fill='both', expand=True)
        configure_tags(self.tree)
        self.tree.bind('<Button-1>', self._click)
        self.tree.bind('<space>', lambda e: self._toggle(self._focus()))
        RowTips(self.tree, self._tip)

        row = ttk.Frame(tab)
        row.pack(fill='x', pady=(8, 0))
        ttk.Label(row, text=tr('Name of the new mod:')).pack(side='left')
        self.name = tk.StringVar(value='MergedMod')
        ent = ttk.Entry(row, textvariable=self.name, width=28)
        ent.pack(side='left', padx=(6, 14))
        ttk.Label(row, text=tr('Main mod:')).pack(side='left')
        self.main = tk.StringVar(value='')
        self.main_box = ttk.Combobox(row, textvariable=self.main, state='readonly', width=34, values=[''])
        self.main_box.pack(side='left', padx=6)
        self.main_path = None
        self.main_box.bind('<<ComboboxSelected>>', lambda e: self._main_picked())
        app.help_mark(row, tr('The main mod is the base: its files stay as they are and it wins every clash you do not decide yourself. Needed for red combinations.'), 'merge')
        row2 = ttk.Frame(tab)
        row2.pack(fill='x', pady=(8, 0))
        self.carry = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text=tr('Carry quest markers over to the chosen map and put lost game markers back'),
                        variable=self.carry).pack(side='left')
        self.verdict = tk.Label(tab, text='', bg=theme.BG, fg=theme.MUT, anchor='w', justify='left', wraplength=860)
        self.verdict.pack(fill='x', pady=(8, 0))
        btns = ttk.Frame(tab)
        btns.pack(fill='x', pady=(8, 0))
        self.btn = ttk.Button(btns, text=tr('Merge...'), style='Accent.TButton', command=self.merge)
        self.btn.pack(side='left')
        ttk.Button(btns, text=tr('Add archive from elsewhere...'), command=self.add_external).pack(side='left', padx=6)
        ttk.Button(btns, text=tr('Untick all'), command=self.clear).pack(side='left')
        self.progress = ttk.Label(btns, text='', style='Muted.TLabel')
        self.progress.pack(side='right')
        app.insight.on_ready(self.refresh)

    # -- list ---------------------------------------------------------------

    def paths(self):
        out = []
        d = self.app.mods_dir
        if d and os.path.isdir(d):
            out += [os.path.join(d, f) for f in sorted(os.listdir(d), key=str.lower) if f.lower().endswith('.wd')]
        out += [p for p in self.extra if os.path.isfile(p) and p not in out]
        return out

    def refresh(self):
        tr = self.app.tr
        paths = self.paths()
        self.checked = [p for p in self.checked if p in paths]
        self.app.insight.ensure(paths)
        sel = self._focus()
        self.tree.delete(*self.tree.get_children())
        for p in paths:
            info = self.app.insight.info(p)
            on = p in self.checked
            if info is None:
                what, fit, tag = tr('reading...'), '', 'fit_none'
            elif info.get('error'):
                what, fit, tag = info['error'], '', 'fit_none'
            else:
                what = modscan.kinds_text(info, tr, 5)
                others = [c for c in self.checked if c != p]
                if not others:
                    fit, tag = '', ('fit_self' if on else 'fit_none')
                else:
                    level, _lines = self.app.insight.worst(p, others)
                    fit, tag = fit_text(tr, level, _lines), LEVEL_TAG[level]
            label = (CHECK_ON if on else CHECK_OFF) + '  ' + os.path.basename(p)
            if os.path.dirname(p) != (self.app.mods_dir or ''):
                label += '  ' + tr('(from elsewhere)')
            self.tree.insert('', 'end', iid=p, text=label, values=(what, fit), tags=(tag,))
        if sel and self.tree.exists(sel):
            self.tree.focus(sel)
            self.tree.selection_set(sel)
        names = [''] + [f'{n + 1}. {os.path.basename(p)}' for n, p in enumerate(self.checked)]
        keep = self.main_path if getattr(self, 'main_path', None) in self.checked else None
        self.main_box.configure(values=names)
        self.main.set(names[self.checked.index(keep) + 1] if keep else '')
        self._verdict()

    def _verdict(self):
        tr = self.app.tr
        if len(self.checked) < 2:
            self.verdict.configure(text=tr('Tick at least two mods.'), fg=theme.MUT)
            return
        rank = {'green': 0, 'unknown': 1, 'yellow': 2, 'red': 3}
        level, lines = 'green', []
        for i, p in enumerate(self.checked):
            lv, ln = self.app.insight.worst(p, self.checked[i + 1:])
            level = max(level, lv, key=rank.get)
            lines += [os.path.basename(p) + ' + ' + x for x in ln]
        text = {'green': tr('Green: no overlap inside files - merges without a question.'),
                'yellow': tr('Yellow (experimental): overlaps are asked one by one.'),
                'red': tr('Red (experimental): both mods change compiled scripts. Choose a main mod - it keeps its scripts; everything else is asked one by one.'),
                'unknown': tr('Still reading the archives...')}[level]
        colour = {'green': theme.OK, 'yellow': theme.GOLD, 'red': theme.ERR, 'unknown': theme.MUT}[level]
        self.verdict.configure(text=text + ('\n' + '\n'.join(lines[:6]) if lines else ''), fg=colour)

    def _main_picked(self):
        i = self.main_box.current()
        self.main_path = self.checked[i - 1] if i > 0 else None

    def set_main(self, path):
        self.main_path = path if path in self.checked else None
        self.refresh()

    def _focus(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _click(self, ev):
        row = self.tree.identify_row(ev.y)
        if row and self.tree.identify_region(ev.x, ev.y) in ('tree', 'cell'):
            self.tree.selection_set(row)
            self._toggle(row)
            return 'break'

    def _toggle(self, row):
        if not row:
            return
        info = self.app.insight.info(row)
        if info is None or info.get('error'):
            return
        if row in self.checked:
            self.checked.remove(row)
            if row == self.main_path:
                self.main_path = None
        else:
            self.checked.append(row)
        self.refresh()

    def clear(self):
        self.checked = []
        self.main_path = None
        self.refresh()

    def add_external(self):
        paths = filedialog.askopenfilenames(parent=self.app.root, title=self.app.tr('Choose a mod archive'),
                                            filetypes=[(self.app.tr('Two Worlds mod'), '*.wd')])
        for p in paths or ():
            p = os.path.normpath(p)
            if p not in self.extra and p not in self.paths():
                self.extra.append(p)
        self.refresh()

    def _tip(self, row):
        info = self.app.insight.info(row)
        if info is None:
            return self.app.tr('reading...')
        lines = list(modscan.summary_lines(info, tr=self.app.tr))
        others = [c for c in self.checked if c != row]
        if others:
            _level, ln = self.app.insight.worst(row, others)
            lines += [''] + (ln or [self.app.tr('no overlap inside files')])
        return '\n'.join(lines)

    # -- merging ----------------------------------------------------------------

    def merge(self):
        app, tr = self.app, self.app.tr
        if len(self.checked) < 2:
            messagebox.showinfo(app.APP_NAME, tr('Tick at least two mods.'), parent=app.root)
            return
        if not app.mods_dir:
            return
        out = os.path.join(app.mods_dir, merger.safe_name(self.name.get()))
        if os.path.exists(out) or os.path.basename(out).lower() in {os.path.basename(p).lower() for p in self.paths()}:
            app.error('merge.name.exists', 'Merge target exists already',
                      tr('{name} exists already - choose another name. The merger never overwrites a mod.').format(
                          name=os.path.basename(out)))
            return
        app.root.configure(cursor='watch')
        app.root.update_idletasks()
        try:
            m = merger.Merge(self.checked, app.insight.retail(), app.data_dir())
        except Exception as e:
            app.root.configure(cursor='')
            app.fb.log.add('merge: reading the mods failed')
            app.error('merge.read.failed', 'Reading the mods to merge failed',
                      tr('Could not read the mods: {e}').format(e=e), 'merge')
            return
        app.root.configure(cursor='')
        main = self.checked.index(self.main_path) if self.main_path in self.checked else None
        decisions = {}
        scripts = [c for c in m.conflicts if c.kind == 'script']
        rest = [c for c in m.conflicts if c.kind != 'script']
        if scripts and main is None:
            messagebox.showwarning(app.APP_NAME, tr('Both mods change compiled scripts ({n}). Scripts cannot be mixed, so choose a main mod first: it keeps its scripts. Everything else is asked one by one.').format(n=len(scripts)), parent=app.root)
            self.main_box.focus_set()
            return
        if rest:
            dlg = ConflictDialog(app, m, main, rest, len(scripts))
            if dlg.result is None:
                return
            decisions = dlg.result
        warn = tr('Merge {n} mods into {name}?\n\nThis is experimental: the result may be buggy or keep the game from starting. Your mods stay untouched.').format(
            n=len(self.checked), name=os.path.basename(out))
        if scripts:
            warn += '\n\n' + tr('Scripts: {k} go to the main mod {main}.').format(
                k=len(scripts), main=os.path.basename(self.main_path))
        if not messagebox.askyesno(app.APP_NAME, warn, icon='warning', parent=app.root):
            return
        self._run(m, out, decisions, main)

    def _run(self, m, out, decisions, main):
        app, tr = self.app, self.app.tr
        self.btn.state(['disabled'])
        name = os.path.basename(out)
        app.fb.log.add(f'merge started: {len(m.paths)} mods, level {m.level}, {len(m.conflicts)} conflicts')
        app.registry_set(name, 0)       # no switch = the game loads it: off before the file exists
        app.merging = True
        q = queue.Queue()
        result = {}
        carry = bool(self.carry.get())

        def work():
            try:
                t = time.time()
                result['rep'] = m.build(out, decisions, main, carry, lambda a, b: q.put((a, b)))
                result['secs'] = time.time() - t
            except Exception as e:
                result['err'] = e

        def poll():
            last = None
            while True:
                try:
                    last = q.get_nowait()
                except queue.Empty:
                    break
            if last:
                self.progress.configure(text=tr('{a} of {b} files').format(a=last[0], b=last[1]))
            if th.is_alive():
                app.root.after(80, poll)
                return
            app.merging = False
            self.btn.state(['!disabled'])
            self.progress.configure(text='')
            if 'err' in result:
                app.registry_delete(name)
                app.fb.log.add('merge failed: ' + type(result['err']).__name__)
                app.error('merge.failed', 'Merging mods failed',
                          tr('Merge failed: {e}').format(e=result['err']), 'merge')
                return
            self._done(m, out, result['rep'])
        th = threading.Thread(target=work, daemon=True)
        th.start()
        poll()

    def _done(self, m, out, rep):
        app, tr = self.app, self.app.tr
        name = os.path.basename(out)
        try:
            d = os.path.join(app.data_dir(), 'merges')
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, os.path.splitext(name)[0] + '.txt'), 'w', encoding='utf-8') as f:
                f.write('\n'.join(rep))
        except OSError:
            pass
        in_mods = [os.path.basename(p) for p in m.paths if os.path.dirname(p) == app.mods_dir]
        ReportWindow(app, name, rep)
        if messagebox.askyesno(app.APP_NAME, tr('{name} is in the Mods folder, switched off.\n\nEnable it now and switch the merged source mods off? (Both at once would load everything twice.)').format(name=name), parent=app.root):
            app.registry_set(name, 1)
            for n in in_mods:
                app.registry_set(n, 0)
        self.checked = []
        self.main_path = None
        app.refresh()
        note = app.sync_level_cache()
        app.status(tr('{name} merged.').format(name=name) + '  ' + note)


class ConflictDialog:
    """One row per overlap, a choice per row; the rest goes to the default."""

    def __init__(self, app, m, main, conflicts=None, scripts=0):
        self.app, self.m = app, m
        tr = app.tr
        self.result = None
        self.default = main
        self.conflicts = list(m.conflicts if conflicts is None else conflicts)
        self.choice = {c.cid: (main if main in c.mods else c.mods[0]) for c in self.conflicts}
        self.win = win = tk.Toplevel(app.root)
        win.title(tr('Overlaps - who wins?'))
        win.transient(app.root)
        win.geometry('1080x660')
        win.minsize(760, 480)
        theme.dark_titlebar(win)
        win.bind('<Escape>', lambda e: self._cancel())
        win.protocol('WM_DELETE_WINDOW', self._cancel)
        outer = ttk.Frame(win, padding=12)
        outer.pack(fill='both', expand=True)
        head = tr('{n} places are changed by more than one mod. Pick the winner per row, or give all to one mod.').format(n=len(self.conflicts))
        if scripts:
            head += ' ' + tr('The {k} compiled scripts stay with the main mod.').format(k=scripts)
        ttk.Label(outer, text=head, style='Muted.TLabel', wraplength=940, justify='left').pack(anchor='w')
        bar = ttk.Frame(outer)
        bar.pack(fill='x', pady=(8, 6))
        ttk.Label(bar, text=tr('Give all to:')).pack(side='left')
        for i, name in enumerate(m.names):
            ttk.Button(bar, text=name[:28], command=lambda i=i: self._all(i)).pack(side='left', padx=(6, 0))
        btns = ttk.Frame(outer)
        btns.pack(fill='x', side='bottom', pady=(10, 0))
        ttk.Button(btns, text=tr('Cancel'), command=self._cancel).pack(side='right')
        ttk.Button(btns, text=tr('Merge with these choices'), style='Accent.TButton', command=self._ok).pack(side='right', padx=6)
        pane = ttk.Panedwindow(outer, orient='horizontal')
        pane.pack(fill='both', expand=True)
        left = ttk.Frame(pane)
        self.tree = ttk.Treeview(left, columns=('who',), show='tree headings')
        self.tree.heading('#0', text=tr('Place'))
        self.tree.heading('who', text=tr('Winner'))
        self.tree.column('#0', width=330, minwidth=200)
        self.tree.column('who', width=210, minwidth=150)
        sb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        pane.add(left, weight=3)
        self.detail = ttk.Frame(pane, padding=(10, 0, 0, 0))
        pane.add(self.detail, weight=2)
        self.rows = {}
        groups = {}
        for n, c in enumerate(self.conflicts):
            if c.group not in groups:
                groups[c.group] = self.tree.insert('', 'end', text=tr(c.group), open=True)
            iid = self.tree.insert(groups[c.group], 'end', text=c.label, values=(m.names[self.choice[c.cid]],))
            self.rows[iid] = c
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._show())
        first = next(iter(self.rows), None)
        if first:
            self.tree.selection_set(first)
        win.grab_set()
        win.wait_window()

    def _show(self):
        for w in self.detail.winfo_children():
            w.destroy()
        sel = self.tree.selection()
        c = self.rows.get(sel[0]) if sel else None
        if c is None:
            return
        tr = self.app.tr
        ttk.Label(self.detail, text=c.label, style='Brand.TLabel', wraplength=340, justify='left').pack(anchor='w')
        var = tk.IntVar(value=self.choice[c.cid])
        for i in c.mods:
            ttk.Radiobutton(self.detail, text=self.m.names[i], variable=var, value=i,
                            command=lambda c=c, var=var: self._pick(c, var.get())).pack(anchor='w', pady=(10, 0))
            val = self.m.value(c, i)
            box = tk.Text(self.detail, height=min(7, max(2, val.count('\n') + 1 + len(val) // 60)), wrap='word',
                          bg=theme.FIELD, fg=theme.INK, relief='flat', font=theme.FONT_MONO,
                          highlightthickness=0, padx=6, pady=4)
            box.insert('1.0', val)
            box.configure(state='disabled')
            box.pack(fill='x', pady=(2, 0))
        game = self.m.game_value(c)
        if game:
            ttk.Label(self.detail, text=tr('The game itself:'), style='Muted.TLabel').pack(anchor='w', pady=(12, 0))
            box = tk.Text(self.detail, height=min(5, max(1, game.count(chr(10)) + 1 + len(game) // 60)), wrap='word',
                          bg=theme.PANEL, fg=theme.MUT, relief='flat', font=theme.FONT_MONO,
                          highlightthickness=0, padx=6, pady=4)
            box.insert('1.0', game)
            box.configure(state='disabled')
            box.pack(fill='x', pady=(2, 0))
        if c.kind == 'tile':
            ttk.Label(self.detail, text=tr('Map and physics always come from the same mod. Markers only the other mod has are carried over.'),
                      style='Muted.TLabel', wraplength=340, justify='left').pack(anchor='w', pady=(10, 0))

    def _pick(self, c, i):
        self.choice[c.cid] = i
        for iid, cc in self.rows.items():
            if cc is c:
                self.tree.set(iid, 'who', self.m.names[i])

    def _all(self, i):
        for iid, c in self.rows.items():
            if i in c.mods:
                self.choice[c.cid] = i
                self.tree.set(iid, 'who', self.m.names[i])
        self._show()

    def _ok(self):
        self.result = dict(self.choice)
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self.win.destroy()


class ReportWindow:
    def __init__(self, app, name, lines):
        tr = app.tr
        self.win = win = tk.Toplevel(app.root)
        win.title(tr('Merge report') + ' - ' + name)
        win.transient(app.root)
        win.geometry('820x560')
        theme.dark_titlebar(win)
        win.bind('<Escape>', lambda e: win.destroy())
        f = ttk.Frame(win, padding=12)
        f.pack(fill='both', expand=True)
        txt = tk.Text(f, wrap='word', bg=theme.FIELD, fg=theme.INK, relief='flat', font=theme.FONT_MONO,
                      highlightthickness=0, padx=8, pady=6)
        sb = ttk.Scrollbar(f, orient='vertical', command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        ttk.Button(f, text=tr('Close'), command=win.destroy).pack(side='bottom', anchor='e', pady=(8, 0))
        sb.pack(side='right', fill='y')
        txt.pack(fill='both', expand=True)
        txt.insert('1.0', '\n'.join(lines))
        txt.configure(state='disabled')
