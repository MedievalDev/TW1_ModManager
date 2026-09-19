"""The window driven through its own methods. Mods folder, registry, data
folder and cache target are redirected into a temp folder; the game folder
is only read."""
import ast
import glob
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
GAME = r'F:\SteamLibrary\steamapps\common\Two Worlds - Epic Edition'
MODS = os.path.join(os.path.expanduser('~'), 'Desktop', 'modsTW1')


class Strings(unittest.TestCase):
    def test_every_ui_string_is_translated(self):
        sys.argv = ['x']
        import mod_manager as M
        M._check_translations()
        missing = []
        for fn in ('mergeui.py',):
            tree = ast.parse(open(os.path.join(ROOT, fn), encoding='utf-8').read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, 'id', getattr(node.func, 'attr', '')) == 'tr' \
                        and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    if node.args[0].value not in M.DE:
                        missing.append(node.args[0].value[:60])
        import merger
        for g in ('Parameters', 'Quest file', 'Texts', 'Dialog trees', 'Maps', 'Whole files', 'Compiled scripts'):
            if g not in M.DE:
                missing.append(g)
        self.assertEqual(missing, [])


@unittest.skipUnless(os.path.isdir(MODS) and os.path.isdir(GAME), 'community mods or game not on this PC')
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.argv = ['x']
        from tkinter import filedialog, messagebox
        import mod_manager as M
        import levelcache
        cls.M, cls.mb = M, messagebox
        cls.tmp = tempfile.mkdtemp(prefix='mm_ui_')
        cls.mods = os.path.join(cls.tmp, 'Mods')
        os.makedirs(cls.mods)
        for p in glob.glob(os.path.join(MODS, '*.wd')):
            shutil.copy2(p, cls.mods)
        cls.reg = {}
        M.registry_mods = lambda: dict(cls.reg)
        M.registry_set = lambda n, v: cls.reg.__setitem__(n, v)
        M.registry_delete = lambda n: cls.reg.pop(n, None)
        M.game_running = lambda: False
        M.App._fetch_catalog = lambda self: None      # no network in tests
        M.App._fetch_github = lambda self: None
        M.data_dir = lambda: cls.tmp
        levelcache.target = lambda game: os.path.join(cls.tmp, 'Levels', 'Map_LevelHeaders.lhc')
        cls.asked = []
        messagebox.askyesno = lambda *a, **k: (cls.asked.append(('yesno', a[1])), True)[1]
        for n in ('showinfo', 'showwarning', 'showerror'):
            setattr(messagebox, n, lambda *a, _n=n, **k: cls.asked.append((_n, a[1])))
        cfg = M.Config(); cfg['guide_seen'] = True; cfg['update_check'] = False; cfg['game_dir'] = GAME; cfg.save()
        cls.app = M.App({'tab': 3})
        cls.app.mods_dir = cls.mods
        cls.app.data_dir = M.data_dir
        cls.app.registry_set = M.registry_set
        cls.app.root.update()
        cls.app._startup()
        cls.pump(lambda: all(cls.app.insight.info(p) is not None for p in cls.app.merge_tab.paths()), 60)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.root.destroy()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def pump(cls, done, secs=30):
        t = time.time()
        while time.time() - t < secs:
            cls.app.root.update()
            if done():
                return True
            time.sleep(0.02)
        return False

    def path(self, start):
        return next(p for p in self.app.merge_tab.paths() if os.path.basename(p).startswith(start))

    def test_1_tooltips_and_fit_colours(self):
        app = self.app
        app.refresh(); app.root.update()
        tip = app._row_tip('Elite.wd')
        self.assertIn('par', tip.lower())
        self.assertTrue('scripts' in tip.lower() or 'skripte' in tip.lower())
        app.tree.selection_set('Elite.wd'); app._colour_fit()
        tags = {i: app.tree.item(i, 'tags')[0] for i in app.tree.get_children()}
        self.assertEqual(tags['revamp.wd'], 'fit_red')
        self.assertEqual(tags['skill.wd'], 'fit_red')
        self.assertEqual(tags['Yamalin.wd'], 'fit_yellow')
        self.assertEqual(tags['Pirate.wd'], 'fit_green')
        self.assertIn('Elite', app._row_tip('revamp.wd'))
        app.tree.selection_remove('Elite.wd'); app._colour_fit()
        self.assertNotIn('fit_', ''.join(app.tree.item('revamp.wd', 'tags')))

    def test_2_merge_tab_colours(self):
        mt = self.app.merge_tab
        mt.clear(); mt._toggle(self.path('Yamalin'))
        tags = {os.path.basename(i): mt.tree.item(i, 'tags')[0] for i in mt.tree.get_children()}
        self.assertEqual(tags['revamp.wd'], 'fit_yellow')
        self.assertEqual(tags['skill.wd'], 'fit_green')
        self.assertTrue(next(v for k, v in tags.items() if k.startswith('Dream')) == 'fit_yellow')
        mt._toggle(self.path('skill')); self.app.root.update()
        self.assertIn('Gruen' if self.M._LANG == 'de' else 'Green', mt.verdict.cget('text'))
        self.assertTrue(mt._tip(self.path('revamp')))

    def test_3_green_merge_runs(self):
        mt = self.app.merge_tab
        mt.clear(); mt._toggle(self.path('revamp')); mt._toggle(self.path('skill'))
        mt.name.set('Green Test')
        mt.merge()
        self.assertTrue(self.pump(lambda: os.path.exists(os.path.join(self.mods, 'Green Test.wd'))
                                  and str(mt.btn.cget('state')) != 'disabled' and not mt.checked, 60))
        self.assertEqual(self.reg.get('Green Test.wd'), 1)
        self.assertEqual(self.reg.get('revamp.wd'), 0)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'merges', 'Green Test.txt')))
        mt.name.set('Green Test'); mt._toggle(self.path('revamp')); mt._toggle(self.path('skill'))
        self.asked.clear(); mt.merge()
        self.assertTrue(any(k == 'showerror' for k, _ in self.asked), 'same name must be refused')

    def test_4_red_needs_main(self):
        mt = self.app.merge_tab
        mt.clear(); mt._toggle(self.path('Elite')); mt._toggle(self.path('revamp'))
        mt.name.set('Red Test'); mt.set_main(None)
        self.asked.clear(); mt.merge(); self.app.root.update()
        self.assertTrue(any(k == 'showwarning' for k, _ in self.asked))
        self.assertFalse(os.path.exists(os.path.join(self.mods, 'Red Test.wd')))
        mt.set_main(self.path('revamp')); mt.merge()
        self.assertTrue(self.pump(lambda: os.path.exists(os.path.join(self.mods, 'Red Test.wd')) and not mt.checked, 90))

    def test_5_yellow_dialog(self):
        import mergeui
        mt = self.app.merge_tab
        seen = {}
        real = mergeui.ConflictDialog.__init__

        def fake(dlg, app, m, main):
            # build the real dialog without blocking: no grab, no wait
            import tkinter as tk
            wait, grab = tk.Toplevel.wait_window, tk.Toplevel.grab_set
            tk.Toplevel.wait_window = lambda self, *a: None
            tk.Toplevel.grab_set = lambda self: None
            try:
                real(dlg, app, m, main)
            finally:
                tk.Toplevel.wait_window, tk.Toplevel.grab_set = wait, grab
            seen['n'] = len(dlg.rows)
            for iid in list(dlg.rows)[:6]:
                dlg.tree.selection_set(iid); dlg._show(); app.root.update()
            dlg._all(1)
            first = next(iter(dlg.rows.values()))
            dlg._pick(first, first.mods[0])
            seen['first'] = first.cid
            dlg._ok()
        mergeui.ConflictDialog.__init__ = fake
        try:
            mt.clear(); mt._toggle(self.path('Yamalin')); mt._toggle(self.path('revamp'))
            mt.name.set('Yellow Test'); mt.set_main(None)
            mt.merge()
            self.assertTrue(self.pump(lambda: os.path.exists(os.path.join(self.mods, 'Yellow Test.wd')) and not mt.checked, 90))
        finally:
            mergeui.ConflictDialog.__init__ = real
        self.assertEqual(seen['n'], 23)
        rep = open(os.path.join(self.tmp, 'merges', 'Yellow Test.txt'), encoding='utf-8').read()
        self.assertIn('Yamalin.wd', rep)

    def test_6_menus_and_language(self):
        app = self.app
        for f in (app._fill_file, app._fill_view, app._fill_help):
            m = self.M.theme.Menu(app.root); f(m); m.destroy()
        app.show_guide('merge'); app.root.update()
        g = self.M.guidebook.GuideWindow._open
        self.assertGreater(int(g.txt.index('end-1c').split('.')[0]), 10)
        g.close()


if __name__ == '__main__':
    unittest.main()
