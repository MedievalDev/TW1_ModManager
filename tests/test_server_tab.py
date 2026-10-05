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
            {'id': 'child', 'name': 'Child Merge', 'group': 'merged', 'base': 'multi', 'version': '1.0',
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
        self.assertEqual(t.parent('merged-one'), '_merged')        # no base: collected under a heading
        self.assertEqual(t.parent('child'), 'multi')               # with base: unfolds under its mod
        self.assertEqual(t.parent('multi'), '')
        self.assertEqual(t.parent('old-mod'), '')
        self.assertEqual(t.item('_merged', 'text'), 'Merged mods')
        self.assertEqual(t.item('child', 'text'), 'Child Merge (merged mod)')
        self.assertFalse(t.item('multi', 'open'))                  # folded until the user unfolds it
        self.assertEqual(list(t.get_children('')), ['multi', 'old-mod', '_merged'])   # sorted by name

    def test_11_window_is_taller(self):
        src = open(self.M.__file__, encoding='utf-8').read()
        self.assertIn('min(825, self.root.winfo_screenheight() - 90)', src)

    def test_2_language_box_only_with_variants(self):
        a = self.app
        self.select('old-mod')
        self.assertEqual(a.side.lang_labels, {})
        self.assertIsNone(a.side.lang)
        self.select('multi')
        self.assertEqual(list(a.side.lang_labels), ['en', 'de'])
        self.assertEqual([w['text'] for w in a.side.lang_box.winfo_children() if w['text'] != '\u00b7'], ['EN', 'DE'])
        self.assertEqual(a.side.lang, 'en')
        self.assertEqual(a.side.btn_install.master, a.side.act)   # install button sits in the panel under the picture

    def test_3_side_panel_text(self):
        a, sp = self.app, self.app.side
        self.select('multi')
        self.assertEqual(sp.text_of(sp.lbl_name), 'Multi Mod')
        self.assertIn('Someone', sp.text_of(sp.lbl_meta))
        self.assertEqual(sp.text_of(sp.lbl_desc), 'English text')
        self.assertIn('#magic', sp.text_of(sp.lbl_tags))
        self.assertIn('B did that', sp.text_of(sp.lbl_credits))
        self.assertEqual(sp.txt_readme.winfo_manager(), '')    # collapsed
        sp._toggle_readme()
        a.root.update()
        self.assertEqual(sp.txt_readme.winfo_manager(), 'pack')
        self.assertEqual(sp.text_of(sp.txt_readme).strip(), 'README EN')
        self.select('old-mod')
        self.assertEqual(sp.btn_readme.winfo_manager(), '')    # no readme, no tags, no credits
        self.assertEqual(sp.lbl_tags.winfo_manager(), '')
        self.assertEqual(sp.lbl_credits.winfo_manager(), '')
        self.assertEqual(sp.text_of(sp.lbl_desc), 'plain entry')

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
        a.side.select_lang('de')
        self.assertEqual(a.side.text_of(a.side.lbl_desc), 'Deutscher Text')   # the switch changes the texts too
        a.install_server_mod()
        self.assertTrue(self.pump(lambda: os.path.exists(os.path.join(self.mods, 'a_de.wd'))), 'file downloaded')
        self.assertTrue(self.pump(lambda: str(a.btn_install.cget('state')) != 'disabled' and
                                  'Deutsch' in a.stree.set('multi', 'status')))
        self.assertEqual(self.reg.get('a_de.wd'), 1)
        self.assertEqual(open(os.path.join(self.mods, 'a_de.wd'), 'rb').read(), self.blobs['a_de.wd'])
        self.assertIn('enabled', a.stree.set('multi', 'status'))
        self.select('multi')
        self.assertEqual(a.side.lang, 'de')                     # remembers the installed variant

    def test_6_switching_language_turns_the_other_off(self):
        a = self.app
        self.select('multi')
        a.side.select_lang('en')
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
        self.assertTrue(self.pump(lambda: 'line 2' in sp.text_of(sp.lbl_desc)), 'description streamed from the .txt')
        self.assertEqual(sp.text_of(sp.lbl_name), 'Cool_Mod')
        self.assertIn('InsideTwoWorlds', sp.text_of(sp.lbl_credits))
        self.assertTrue(self.pump(lambda: bool(sp.pic['image'])), 'repo picture shown')
        a.gtree.selection_set('Plain.WD')
        a.root.update()
        self.assertEqual(sp.text_of(sp.lbl_name), 'Plain')
        self.assertIn('no description', sp.text_of(sp.lbl_desc))

    def test_92_links_and_copy(self):
        M, sp = self.M, self.app.side
        self.assertEqual(M.split_links('see https://a.b/c, and http://x.y/z).'),
                         [('see ', None), ('https://a.b/c', 'https://a.b/c'), (', and ', None),
                          ('http://x.y/z', 'http://x.y/z'), (').', None)])
        opened = []
        M.webbrowser.open = lambda u: opened.append(u)
        M.fill_text(sp.lbl_desc, 'Docs: https://example.org/page. Done', 300)
        self.assertEqual(sp.text_of(sp.lbl_desc), 'Docs: https://example.org/page. Done')
        ranges = sp.lbl_desc.tag_ranges('link0')
        self.assertEqual(len(ranges), 2)
        self.assertEqual(sp.lbl_desc.get(*ranges), 'https://example.org/page')
        sp.lbl_desc.tag_add('sel', '1.0', 'end-1c')                 # a selection is not a click
        sp.lbl_desc.event_generate('<ButtonRelease-1>')
        sp.lbl_desc.tag_remove('sel', '1.0', 'end')
        self.assertEqual(opened, [])
        # text can be selected and copied, typing does not change it
        sp.lbl_desc.tag_add('sel', '1.0', 'end-1c')
        sp.lbl_desc.event_generate('<<Copy>>')
        self.assertEqual(self.app.root.clipboard_get(), 'Docs: https://example.org/page. Done')
        sp.lbl_desc.focus_force()
        sp.lbl_desc.event_generate('<Key>', keysym='x')
        self.assertEqual(sp.text_of(sp.lbl_desc), 'Docs: https://example.org/page. Done')
        self.app.side.mod = None                                    # force the next show() to redraw

    def test_93_search_my_mods(self):
        a, f = self.app, self.app.flt_server
        a.notebook.select(1)
        a.root.update()
        a._refresh_server_states()
        def walk(parent=''):
            out = []
            for i in a.stree.get_children(parent):
                out.append(i)
                out += walk(i)
            return out
        vis = lambda: [i for i in walk() if i != '_merged']
        everything = ['child', 'merged-one', 'multi', 'old-mod']
        self.assertEqual(sorted(vis()), everything)
        f.var.set('multi')
        a.root.update()
        self.assertEqual(sorted(vis()), ['multi'])
        self.assertNotIn('_merged', a.stree.get_children(''))       # heading hides with its last child
        self.assertEqual(f.lbl['text'], '1 / 4')
        f.var.set('child')                                           # a hit on the child shows and opens its parent
        a.root.update()
        self.assertEqual(sorted(vis()), ['child', 'multi'])
        self.assertTrue(a.stree.item('multi', 'open'))
        self.assertEqual(f.lbl['text'], '1 / 4')
        a.stree.item('multi', open=False)
        f.var.set('multi')
        a.root.update()
        f.var.set('Deutscher')                                       # finds words inside descriptions
        a.root.update()
        self.assertEqual(vis(), ['multi'])
        f.var.set('merged')                                          # "merged mod" counts as a word as well
        a.root.update()
        self.assertEqual(sorted(vis()), ['child', 'merged-one', 'multi'])
        self.assertIn('_merged', a.stree.get_children(''))
        f.var.set('zzz')
        a.root.update()
        self.assertEqual(vis(), [])
        self.assertEqual(f.lbl['text'], 'No match')
        f.clear()
        a.root.update()
        self.assertEqual(sorted(vis()), everything)

    def test_94_suggestions_and_keyboard(self):
        a, f = self.app, self.app.flt_server
        a.notebook.select(1)
        a.root.update()
        f.entry.focus_force()
        f.var.set('mu')
        a.root.update()
        self.assertTrue(f.visible())
        self.assertEqual(list(f.box.get(0, 'end')), ['Multi Mod'])
        self.assertEqual(f._move(1), 'break')
        self.assertEqual(f.box.curselection(), (0,))
        self.assertEqual(f._accept(None), 'break')
        self.assertEqual(f.var.get(), 'Multi Mod')
        self.assertFalse(f.visible())
        self.assertEqual(a.stree.get_children(''), ('multi',))
        self.assertEqual(a.stree.get_children('multi'), ())
        f.var.set('o')                                               # prefix hits come before substring hits
        a.root.update()
        labels = list(f.box.get(0, 'end'))
        self.assertEqual(labels[0], 'Old Format Mod')
        f._escape(None)
        self.assertFalse(f.visible())
        self.assertEqual(f.var.get(), 'o')
        f._escape(None)
        self.assertEqual(f.var.get(), '')

    def test_95_search_installed_and_community(self):
        a = self.app
        a.refresh()
        a.root.update()
        names = [i for i in a.tree.get_children() if not i.startswith('ROOT::')]
        self.assertTrue(names, 'some archives are installed by the earlier tests')
        f = a.flt_inst
        f.var.set(names[0][:3].lower())
        a.root.update()
        shown = [i for i in a.tree.get_children() if not i.startswith('ROOT::')]
        self.assertTrue(shown and all(names[0][:3].lower() in i.lower() for i in shown))
        a.refresh()                                                  # a rebuild keeps the filter and does not crash
        a.root.update()
        self.assertEqual([i for i in a.tree.get_children() if not i.startswith('ROOT::')], shown)
        f.clear()
        a.root.update()
        self.assertEqual([i for i in a.tree.get_children() if not i.startswith('ROOT::')], names)
        g = a.flt_gh                                                 # community: name and description
        a.gh_desc['Cool_Mod.wd'] = 'Adds dragons and Katana swords'
        a._gh_search_rows()
        g.var.set('katana')
        a.root.update()
        self.assertEqual(a.gtree.get_children(''), ('Cool_Mod.wd',))
        g.clear()
        a.root.update()
        self.assertEqual(len(a.gtree.get_children('')), 2)


if __name__ == '__main__':
    unittest.main()
