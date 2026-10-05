"""Server tab: groups, language variants, side panel (gallery, readme, tags, credits).
A small HTTP server on localhost delivers pictures and archives; mods folder and
registry are redirected into a temp folder."""
import hashlib
import http.server
import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
GAME = r'F:\SteamLibrary\steamapps\common\Two Worlds - Epic Edition'


@unittest.skipUnless(os.path.isdir(GAME), 'game not on this PC')
class ServerTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.argv = ['x']
        import mod_manager as M
        from PIL import Image
        cls.M = M
        cls.tmp = tempfile.mkdtemp(prefix='mm_srv_')
        cls.mods = os.path.join(cls.tmp, 'Mods')
        os.makedirs(cls.mods)
        web = os.path.join(cls.tmp, 'web')
        os.makedirs(web)
        cls.blobs = {'a_en.wd': b'EN-archive' * 50, 'a_de.wd': b'DE-archive' * 60, 'old.wd': b'old' * 40,
                     'desc.txt': b'Community description line 1' + bytes([10]) + b'line 2'}
        for n, b in cls.blobs.items():
            open(os.path.join(web, n), 'wb').write(b)
        for n, col in (('p1.png', (200, 40, 40)), ('p2.png', (40, 200, 40)), ('p3.png', (40, 40, 200))):
            Image.new('RGB', (640, 360), col).save(os.path.join(web, n))

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=web, **k)

            def log_message(self, *a):
                pass
        cls.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Quiet)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{cls.httpd.server_address[1]}/'

        def var(lang, name):
            b = cls.blobs[name]
            return {'lang': lang, 'file': name, 'url': base + name, 'size': len(b),
                    'sha256': hashlib.sha256(b).hexdigest()}
        cls.catalog = {'updated': '2026-10-05', 'mods': [
            {'id': 'old-mod', 'name': 'Old Format Mod', 'file': 'old.wd', 'url': base + 'old.wd',
             'version': '1.0', 'size': len(cls.blobs['old.wd']),
             'sha256': hashlib.sha256(cls.blobs['old.wd']).hexdigest(), 'description': 'plain entry'},
            {'id': 'multi', 'name': 'Multi Mod', 'group': 'own', 'version': '1.1', 'author': 'Someone',
             'description': 'English text', 'description_de': 'Deutscher Text',
             'readme': 'README EN', 'readme_de': 'README DE', 'tags': ['magic', 'spells'],
             'credits': ['A did this', 'B did that'], 'images': [base + 'p1.png', base + 'p2.png', 'p3.png'],
             'variants': [var('en', 'a_en.wd'), var('de', 'a_de.wd')],
             'file': 'a_en.wd', 'url': base + 'a_en.wd'},
            {'id': 'merged-one', 'name': 'Merged One', 'group': 'merged', 'version': '1.0',
             'variants': [var('en', 'a_en.wd')]},
        ]}

        M.LOGO_URL = base + 'p2.png'             # stands in for the Steam logo
        M.MODS_URL = base + 'mods.json'          # relative picture urls resolve against it
        M.game_running = lambda: False
        cls.reg = {}
        M.registry_mods = lambda: dict(cls.reg)
        M.registry_set = lambda n, v: cls.reg.__setitem__(n, v)
        M.registry_delete = lambda n: cls.reg.pop(n, None)
        M.App._fetch_catalog = lambda self: None
        M.App._fetch_github = lambda self: None
        M.data_dir = lambda: cls.tmp
        cfg = M.Config()
        cfg['guide_seen'] = True
        cfg['update_check'] = False
        cfg['game_dir'] = GAME
        cfg['lang'] = 'en'
        cfg.save()
        cls.app = M.App({'tab': 1})
        cls.app.mods_dir = cls.mods
        cls.app.registry_set = M.registry_set
        cls.app.root.update()
        cls.app.catalog = cls.catalog
        cls.app._refresh_server_states()
        cls.app.root.update()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.root.destroy()
        except Exception:
            pass
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def pump(self, done, secs=15):
        """Run the real main loop (worker threads call root.after), stop when done() or on timeout."""
        root, end, hit = self.app.root, time.time() + secs, []

        def tick():
            if done():
                hit.append(1)
                root.quit()
            elif time.time() > end:
                root.quit()
            else:
                root.after(50, tick)
        root.after(50, tick)
        root.mainloop()
        return bool(hit)

    def select(self, iid):
        self.app.stree.selection_set(iid)
        self.app.root.update()

    def test_1_groups(self):
        t = self.app.stree
        self.assertEqual(t.parent('merged-one'), '_merged')
        self.assertEqual(t.parent('multi'), '')
        self.assertEqual(t.parent('old-mod'), '')
        self.assertEqual(t.item('_merged', 'text'), 'Merged mods')

    def test_2_language_box_only_with_variants(self):
        a = self.app
        self.select('old-mod')
        self.assertEqual(a.cmb_lang.winfo_manager(), '')
        self.select('multi')
        self.assertEqual(a.cmb_lang.winfo_manager(), 'pack')
        self.assertEqual(list(a.cmb_lang['values']), ['English', 'Deutsch'])
        self.assertEqual(a.cmb_lang.get(), 'English')          # tool language

    def test_3_side_panel_text(self):
        a, sp = self.app, self.app.side
        self.select('multi')
        self.assertEqual(sp.lbl_name['text'], 'Multi Mod')
        self.assertIn('Someone', sp.lbl_meta['text'])
        self.assertEqual(sp.lbl_desc['text'], 'English text')
        self.assertIn('#magic', sp.lbl_tags['text'])
        self.assertIn('B did that', sp.lbl_credits['text'])
        self.assertEqual(sp.txt_readme.winfo_manager(), '')    # collapsed
        sp._toggle_readme()
        a.root.update()
        self.assertEqual(sp.txt_readme.winfo_manager(), 'pack')
        self.assertEqual(sp.txt_readme.get('1.0', 'end').strip(), 'README EN')
        self.select('old-mod')
        self.assertEqual(sp.btn_readme.winfo_manager(), '')    # no readme, no tags, no credits
        self.assertEqual(sp.lbl_tags.winfo_manager(), '')
        self.assertEqual(sp.lbl_credits.winfo_manager(), '')
        self.assertEqual(sp.lbl_desc['text'], 'plain entry')

    def test_4_gallery(self):
        a, sp = self.app, self.app.side
        self.select('multi')
        self.assertEqual(len(sp.images), 3)
        self.assertTrue(self.pump(lambda: sp.cache.get(sp.images[0]) is not None), 'first picture loads')
        self.assertTrue(sp.pic['image'])
        sp.step(1)
        self.assertEqual(sp.idx, 1)
        self.assertEqual(sp.lbl_count['text'], '2 / 3')
        sp.step(1)
        self.assertTrue(self.pump(lambda: sp.cache.get(sp.images[2]) is not None), 'relative url resolves')
        sp.step(1)
        self.assertEqual(sp.idx, 0)                            # wraps around
        self.select('old-mod')
        self.assertEqual(sp.btn_next.winfo_manager(), '')
        self.assertTrue(self.pump(lambda: bool(sp.pic['image'])), 'logo streamed in as placeholder')
        self.assertEqual(sp.pic['text'], '')
        self.assertEqual(sp.lbl_count.winfo_manager(), '')

    def test_5_install_chosen_language(self):
        a = self.app
        self.select('multi')
        a.cmb_lang.current(1)                                   # Deutsch
        a.install_server_mod()
        self.assertTrue(self.pump(lambda: os.path.exists(os.path.join(self.mods, 'a_de.wd'))), 'file downloaded')
        self.assertTrue(self.pump(lambda: str(a.btn_install.cget('state')) != 'disabled' and
                                  'Deutsch' in a.stree.set('multi', 'status')))
        self.assertEqual(self.reg.get('a_de.wd'), 1)
        self.assertEqual(open(os.path.join(self.mods, 'a_de.wd'), 'rb').read(), self.blobs['a_de.wd'])
        self.assertIn('enabled', a.stree.set('multi', 'status'))
        self.select('multi')
        self.assertEqual(a.cmb_lang.get(), 'Deutsch')           # remembers the installed variant

    def test_6_switching_language_turns_the_other_off(self):
        a = self.app
        self.select('multi')
        a.cmb_lang.current(0)
        a.install_server_mod()
        self.assertTrue(self.pump(lambda: os.path.exists(os.path.join(self.mods, 'a_en.wd'))))
        self.assertTrue(self.pump(lambda: self.reg.get('a_de.wd') == 0))
        self.assertEqual(self.reg.get('a_en.wd'), 1)

    def test_7_old_entry_installs(self):
        a = self.app
        self.select('old-mod')
        a.install_server_mod()
        self.assertTrue(self.pump(lambda: self.reg.get('old.wd') == 1))
        self.assertTrue(self.pump(lambda: 'enabled' in a.stree.set('old-mod', 'status')))

    def test_8_german_texts(self):
        M = self.M
        old, M._LANG = M._LANG, 'de'
        try:
            self.assertEqual(M.mod_text(self.catalog['mods'][1], 'description'), 'Deutscher Text')
            self.assertEqual(M.mod_text(self.catalog['mods'][1], 'readme'), 'README DE')
            self.assertEqual(M.mod_text(self.catalog['mods'][0], 'description'), 'plain entry')
        finally:
            M._LANG = old

    def test_9_sizes(self):
        f = self.M.fmt_size
        self.assertEqual(f(500), '500 B')
        self.assertEqual(f(39921), '39 KB')
        self.assertEqual(f(2466819), '2.4 MB')

    def test_91_community_panel(self):
        a = self.app
        base = self.catalog['mods'][0]['url'].rsplit('/', 1)[0] + '/'
        a.github = [{'name': 'Cool_Mod.wd', 'size': 1234, 'sha': 'x', 'url': base + 'old.wd',
                     'txt_url': base + 'desc.txt', 'images': [base + 'p1.png']},
                    {'name': 'Plain.WD', 'size': 99, 'sha': 'y', 'url': base + 'old.wd', 'txt_url': None, 'images': []}]
        a._refresh_github_states()
        a.root.update()
        a.gtree.selection_set('Cool_Mod.wd')
        sp = a.gside
        self.assertTrue(self.pump(lambda: 'line 2' in sp.lbl_desc['text']), 'description streamed from the .txt')
        self.assertEqual(sp.lbl_name['text'], 'Cool_Mod')
        self.assertIn('InsideTwoWorlds', sp.lbl_credits['text'])
        self.assertTrue(self.pump(lambda: bool(sp.pic['image'])), 'repo picture shown')
        a.gtree.selection_set('Plain.WD')
        a.root.update()
        self.assertEqual(sp.lbl_name['text'], 'Plain')
        self.assertIn('no description', sp.lbl_desc['text'])


if __name__ == '__main__':
    unittest.main()
