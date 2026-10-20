"""The window with a zip package and TWSE (2.6.0), driven through its own
methods in a made-up game folder (temp). Registry, dialogs and the network
are stubbed; shortcuts go through the real PowerShell path into a temp
desktop. The real game folder is only read (its TwoWorlds.exe is copied)."""
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
GAME = r'F:\SteamLibrary\steamapps\common\Two Worlds - Epic Edition'


@unittest.skipUnless(os.path.isfile(os.path.join(GAME, 'TwoWorlds.exe')), 'game not on this PC')
class PackageWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.argv = ['x']
        from tkinter import messagebox
        import mod_manager as M
        from tests import wdwrite as merger
        import twse_install
        cls.M, cls.T = M, twse_install
        cls.tmp = tempfile.mkdtemp(prefix='mm_ui_pkg_')
        cls.game = os.path.join(cls.tmp, 'Two Worlds')
        os.makedirs(os.path.join(cls.game, 'WDFiles'))
        os.makedirs(os.path.join(cls.game, 'Mods'))
        open(os.path.join(cls.game, 'WDFiles', 'Update16.wd'), 'wb').close()
        shutil.copy2(os.path.join(GAME, 'TwoWorlds.exe'), cls.game)
        cls.desk = os.path.join(cls.tmp, 'Desktop')
        os.makedirs(cls.desk)
        cls.reg = {}
        M.registry_mods = lambda: dict(cls.reg)
        M.registry_set = lambda n, v: cls.reg.__setitem__(n, v)
        M.registry_delete = lambda n: cls.reg.pop(n, None)
        M.game_running = lambda: False
        M.data_dir = lambda: cls.tmp

        def offline(url, timeout=20):
            raise OSError('offline in the test')
        M.http_get = offline
        twse_install.shortcut_folders = lambda: [(cls.desk, False)]
        twse_install.user_desktop = lambda: cls.desk
        cls.asked = []
        messagebox.askyesno = lambda *a, **k: (cls.asked.append(('yesno', a[1])), True)[1]
        for n in ('showinfo', 'showwarning', 'showerror'):
            setattr(messagebox, n, lambda *a, _n=n, **k: cls.asked.append((_n, a[1])))
        M.ErrorDialog = lambda app, key, message, shown, guide=None, title=None: cls.asked.append(('error', key, shown))

        class AutoDialog:
            def __init__(self, app, path, plan):
                cls.plan = plan
                self.result = 'enable'
                self.twse_var = type('V', (), {'get': lambda s: True})()
        M.PackageDialog = AutoDialog
        cls.twse_asked = []
        cls.twse_answer = True

        class AutoAsk:                      # "This mod needs TWSE" - [Install] [Cancel]
            def __init__(self, app, plan):
                cls.twse_asked.append(plan['name'])
                self.result = cls.twse_answer
        M.TwseNeededDialog = AutoAsk
        cfg = M.Config()
        cfg.update(guide_seen=True, update_check=False, game_dir=cls.game, auto_level_cache=False)
        cfg.save()
        wd = os.path.join(cls.tmp, 'x.wd')
        merger.write_wd(wd, [({'path': 'Scripts\\a.txt', 'flags': 0x01, 'rlen': 5, 'res': None, 'id': None,
                               'guid': None}, zlib.compress(b'hello'))])
        cls.zip = os.path.join(cls.tmp, 'SpellRework_v2.zip')
        with zipfile.ZipFile(cls.zip, 'w') as z:
            z.write(wd, 'SpellRework_v2/Mods/SpellRework_v2.wd')
            z.write(os.path.join(ROOT, 'bin', 'twse.dll'), 'SpellRework_v2/TWSEPlugins/SpellRework.dll')
            z.writestr('SpellRework_v2/TWSEPlugins/SpellRework.ini', '[Spell]\nPower=1\n')
        # a desktop shortcut to TwoWorlds.exe, made the real way
        cls.lnk = os.path.join(cls.desk, 'Two Worlds.lnk')
        twse_install.Shell().write([{'path': cls.lnk, 'target': os.path.join(cls.game, 'TwoWorlds.exe'),
                                     'workdir': cls.game}])
        cls.app = M.App()
        cls.app.root.update()
        cls.app._startup()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.root.destroy()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def pump(self, cond, seconds=30):
        end = time.time() + seconds
        while time.time() < end and not cond():
            self.app.root.update()
            time.sleep(0.05)
        return cond()

    def g(self, *p):
        return os.path.join(self.game, *p)

    def test_a_cancel_writes_nothing(self):
        # Marco 19.10.2026: DLL mod, TWSE missing -> question; Cancel installs neither TWSE nor the mod
        app, T = self.app, self.T
        self.assertFalse(T.status(self.game)['installed'])
        type(self).twse_answer = False
        before = set(os.listdir(self.g('Mods')))
        try:
            app.offer_install([self.zip])
            app.root.update()
        finally:
            type(self).twse_answer = True
        self.assertEqual(self.twse_asked, ['SpellRework_v2'])
        self.assertFalse(T.status(self.game)['installed'])
        self.assertFalse(os.path.exists(self.g('TWSEPlugins', 'SpellRework.dll')))
        self.assertFalse(os.path.exists(self.g('Mods', 'SpellRework_v2.wd')))
        self.assertFalse(os.path.exists(self.g('Mods', '_packages.json')))
        self.assertEqual(self.reg, {})
        self.assertEqual(set(os.listdir(self.g('Mods'))), before)
        self.assertEqual(T.Shell().read([self.lnk])[self.lnk]['target'], self.g('TwoWorlds.exe'))
        self.assertFalse(app.tree.exists('PKG::SpellRework_v2'))

    def test_package_and_twse_round_trip(self):
        app, T = self.app, self.T
        self.assertEqual(app.game_dir, self.game)
        self.assertIn(self.M.tr('TWSE: not installed - DLL plugins do not load'), app.lbl_twse.cget('text'))
        asked_before = len(self.twse_asked)
        app.offer_install([self.zip])
        # Install: TWSE comes FIRST (offline: the bundled twse.dll), the mod follows
        self.assertEqual(len(self.twse_asked), asked_before + 1)
        self.assertTrue(self.pump(lambda: os.path.isfile(self.g('TWSEPlugins', 'SpellRework.dll'))), self.asked)
        self.assertTrue(T.status(self.game)['installed'], 'TWSE must be there before the mod is written')
        self.assertEqual(self.reg.get('SpellRework_v2.wd'), 1)
        self.assertTrue(app.tree.exists('PKG::SpellRework_v2'))
        self.assertIn(self.M.tr('(package {p})').format(p='SpellRework_v2'), app.tree.item('SpellRework_v2.wd', 'text'))
        self.assertTrue(self.pump(lambda: T.status(self.game)['installed']), self.asked)
        self.pump(lambda: app.lbl_twse.cget('text').startswith(self.M.tr('TWSE: installed')))
        st = T.status(self.game)
        self.assertTrue(st['dll_known'])
        self.assertEqual(T.Shell().read([self.lnk])[self.lnk]['target'], self.g('TwoWorldsExtended.exe'))
        self.assertTrue(os.path.isfile(os.path.join(self.desk, T.SHORTCUT)))
        self.assertTrue(app.tree.exists('PLUG::SpellRework.dll') is False, 'package plugins are no loose rows')

        # off and on through the row of one of its files
        app.tree.selection_set('PKGF::SpellRework_v2::TWSEPlugins/SpellRework.dll')
        app.toggle()
        self.assertEqual(self.reg['SpellRework_v2.wd'], 0)
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', '_disabled', 'SpellRework_v2', 'SpellRework.dll')))
        app.tree.selection_set('SpellRework_v2.wd')     # the .wd row switches the whole package
        app.toggle()
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', 'SpellRework.dll')))

        # replace switch on and off
        app.replace_var.set(True)
        app.toggle_replace_exe()
        self.assertTrue(T.status(self.game)['exe_replaced'])
        app.replace_var.set(False)
        app.toggle_replace_exe()
        self.assertFalse(T.status(self.game)['exe_replaced'])

        # TWSE is there now: a second DLL package installs without asking
        asked_before = len(self.twse_asked)
        app.offer_install([self.zip])
        app.root.update()
        self.assertEqual(len(self.twse_asked), asked_before)

        # a loose plugin
        shutil.copy2(os.path.join(ROOT, 'bin', 'twse.dll'), self.g('TWSEPlugins', 'Loose.dll'))
        app.refresh()
        self.assertTrue(app.tree.exists('PLUG::Loose.dll'))
        app.tree.selection_set('PLUG::Loose.dll')
        app.toggle()
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', '_disabled', 'Loose.dll')))

        # remove TWSE, then the package
        app.remove_twse()
        self.assertFalse(T.status(self.game)['installed'])
        self.assertEqual(T.Shell().read([self.lnk])[self.lnk]['target'], self.g('TwoWorlds.exe'))
        self.assertFalse(os.path.exists(os.path.join(self.desk, T.SHORTCUT)))
        self.assertIn(self.M.tr('TWSE: removed'), app.lbl_twse.cget('text'))
        app.tree.selection_set('PKG::SpellRework_v2')
        app.remove_mod()
        self.assertFalse(os.path.exists(self.g('TWSEPlugins', 'SpellRework.dll')))
        self.assertFalse(os.path.exists(self.g('Mods', 'SpellRework_v2.wd')))
        self.assertFalse(app.tree.exists('PKG::SpellRework_v2'))
        self.assertEqual([a for a in self.asked if a[0] == 'error'], [])


if __name__ == '__main__':
    unittest.main()
