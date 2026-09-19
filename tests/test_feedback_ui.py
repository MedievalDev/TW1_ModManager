"""The feedback windows inside the Mod Manager: menu, test window, bug window,
error dialog, crash handler, experimental label. Nothing is sent: the submit
function is stubbed, the server state is set by hand."""
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
GAME = r'F:\SteamLibrary\steamapps\common\Two Worlds - Epic Edition'


@unittest.skipUnless(os.path.isdir(GAME), 'game not on this PC')
class FeedbackInTheTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.argv = ['x']
        import foxfeedback
        import mod_manager as M
        cls.M = M
        cls.tmp = tempfile.mkdtemp(prefix='mm_fb_')
        cls.sent = []
        foxfeedback.submit = lambda payload, *a, **k: (cls.sent.append(payload), 'test-id')[1]
        M.data_dir = lambda: cls.tmp
        M.game_running = lambda: False
        M.App._fetch_catalog = lambda self: None
        M.App._fetch_github = lambda self: None
        M.registry_mods = lambda: {}
        cfg = M.Config(); cfg['guide_seen'] = True; cfg['update_check'] = False; cfg['game_dir'] = GAME; cfg.save()
        cls.app = M.App({'tab': 0})
        cls.app.root.lower()
        cls.app.root.update()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.root.destroy()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_tests_are_loaded_and_offered(self):
        fb = self.app.fb
        ids = [x['id'] for x in fb.tests]
        self.assertIn('merge-yellow-ingame', ids)
        fb.summary = {'tests': {i: {'since': '2.2.0', 'pass': 0, 'fail': 0, 'status': 'open'} for i in ids},
                      'issues': []}          # the tool keeps only its own part of summary.json
        self.assertEqual(len(fb.untested()), len(ids))
        self.assertTrue(fb.experimental('merge'))
        fb.summary['tests']['merge-yellow-ingame']['status'] = 'confirmed'
        fb.summary['tests']['merge-maps-ingame']['status'] = 'confirmed'
        self.assertFalse(fb.experimental('merge'))

    def test_menu_and_windows_open(self):
        import tkinter as tk
        m = self.M.theme.Menu(self.app.root)
        self.app._fill_help(m)
        labels = [m.entrycget(i, 'label') for i in range(m.index('end') + 1) if m.type(i) == 'command']
        self.assertTrue(any('test' in x.lower() for x in labels), labels)
        m.destroy()
        w = self.app.fb.show_tests('merge-yellow-ingame')
        self.app.root.update()
        self.assertIsNotNone(w)
        w.close()
        self.app.error('merge.failed', 'Merging mods failed', 'Merge failed: X', 'merge')
        self.app.root.update()
        dlgs = [c for c in self.app.root.winfo_children() if isinstance(c, tk.Toplevel)]
        self.assertTrue(dlgs)
        for d in dlgs:
            d.destroy()

    def test_crash_handler_opens_a_dialog(self):
        import tkinter as tk
        try:
            raise KeyError('x')
        except KeyError:
            self.app._crash(*sys.exc_info())
        self.app.root.update()
        dlgs = [c for c in self.app.root.winfo_children() if isinstance(c, tk.Toplevel)]
        self.assertTrue(dlgs)
        for d in dlgs:
            d.destroy()
        self.assertTrue(any('crash KeyError' in x for x in self.app.fb.log.lines))

    def test_nothing_was_sent(self):
        self.assertEqual(self.sent, [])


if __name__ == '__main__':
    unittest.main()
