"""TWSE installer (2.6.0): release check, install, the replace-exe switch,
shortcuts, remove. Only temp folders; the game's TwoWorlds.exe is copied
into one, the real game folder, desktop and start menu are never touched.

    py -3.13 -m unittest tests.test_twse_install      (from the repo root)
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import twse_install as T  # noqa: E402
import twse_patch  # noqa: E402

GAME = r'F:\SteamLibrary\steamapps\common\Two Worlds - Epic Edition'
DLL = open(os.path.join(ROOT, 'bin', 'twse.dll'), 'rb').read()


class FakeShell:
    """Shortcuts as a dict {path: {target, workdir, ...}}; a path in
    ``locked`` cannot be written (no rights)."""

    def __init__(self, links=None, locked=()):
        self.links = dict(links or {})
        self.locked = set(locked)
        for p in self.links:
            open(p, 'wb').close()

    def read(self, paths):
        return {p: dict(self.links[p], path=p, ok=True) for p in paths if p in self.links}

    def write(self, items):
        out = {}
        for i in items:
            if i['path'] in self.locked:
                out[i['path']] = {'path': i['path'], 'ok': False, 'error': 'Access denied'}
                continue
            self.links[i['path']] = {'target': i['target'], 'workdir': i.get('workdir', '')}
            open(i['path'], 'wb').close()
            out[i['path']] = dict(self.links[i['path']], path=i['path'], ok=True)
        return out


def release_zip(dll=DLL):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('bin/twse.dll', dll)
        z.writestr('bin/TWSEPlugins/HeroRunMode.dll', b'example plugin, never installed')
        z.writestr('readme.txt', b'x')
    data = b.getvalue()
    return data, {'tag': 'v9', 'url': 'u', 'name': 'r.zip', 'size': len(data),
                  'sha256': hashlib.sha256(data).hexdigest()}


class Release(unittest.TestCase):
    def test_checksum_and_known(self):
        data, rel = release_zip()
        dll, known = T.dll_from_release(data, rel)
        self.assertEqual(dll, DLL)
        self.assertTrue(known)
        with self.assertRaises(T.TwseError):
            T.dll_from_release(data + b'x', rel)
        with self.assertRaises(T.TwseError):
            T.dll_from_release(data, dict(rel, sha256=''))
        other = bytearray(DLL)
        other[-16] ^= 1
        data2, rel2 = release_zip(bytes(other))
        self.assertFalse(T.dll_from_release(data2, rel2)[1], 'a dll we have not checked asks first')
        data3, rel3 = release_zip(b'not a dll')
        with self.assertRaises(T.TwseError):
            T.dll_from_release(data3, rel3)

    def test_latest_release_reads_the_api(self):
        api = json.dumps({'tag_name': 'v0.1.0', 'assets': [
            {'name': 'Two-Worlds-1-Script-Extender-Release-1.zip', 'size': 5,
             'browser_download_url': 'https://x/r.zip', 'digest': 'sha256:ab'}]}).encode()
        r = T.latest_release(lambda url: api)
        self.assertEqual((r['tag'], r['url'], r['sha256']), ('v0.1.0', 'https://x/r.zip', 'ab'))


@unittest.skipUnless(os.path.isfile(os.path.join(GAME, 'TwoWorlds.exe')), 'game not on this PC')
class Install(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(GAME, 'TwoWorlds.exe'), 'rb') as f:
            cls.stock = f.read()
        assert not twse_patch.pruefen(cls.stock)['fertig'], 'the game exe is expected to be the original'

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='mm_twse_test_')
        self.game = os.path.join(self.tmp, 'Two Worlds')
        self.desk = os.path.join(self.tmp, 'Desktop')
        self.menu = os.path.join(self.tmp, 'StartMenu', 'Programs', 'Two Worlds')
        for d in (self.game, self.desk, self.menu, os.path.join(self.game, 'Mods')):
            os.makedirs(d, exist_ok=True)
        with open(self.g(T.EXE), 'wb') as f:
            f.write(self.stock)
        self.folders = [(self.desk, False), (os.path.join(self.tmp, 'StartMenu'), True)]
        exe = self.g(T.EXE)
        self.shell = FakeShell({os.path.join(self.desk, 'Two Worlds.lnk'): {'target': exe, 'workdir': self.game},
                                os.path.join(self.menu, 'Two Worlds.lnk'): {'target': exe, 'workdir': self.game},
                                os.path.join(self.desk, 'Other.lnk'): {'target': r'C:\x\other.exe'}})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def g(self, *p):
        return os.path.join(self.game, *p)

    def install(self, **kw):
        return T.install(self.game, DLL, 'v0.1.0', shell=self.shell, folders=self.folders, desktop=self.desk, **kw)

    def test_install_and_remove(self):
        notes = self.install()
        st = T.status(self.game)
        self.assertTrue(st['installed'])
        self.assertEqual(st['exe'], 'stock', 'TwoWorlds.exe untouched')
        self.assertEqual(open(self.g(T.EXE), 'rb').read(), self.stock)
        self.assertTrue(os.path.isdir(self.g('TWSEPlugins')))
        self.assertTrue(st['dll_known'])
        ext = self.g(T.EXT)
        self.assertEqual(self.shell.links[os.path.join(self.desk, 'Two Worlds.lnk')]['target'], ext)
        self.assertEqual(self.shell.links[os.path.join(self.menu, 'Two Worlds.lnk')]['target'], ext)
        self.assertEqual(self.shell.links[os.path.join(self.desk, 'Other.lnk')]['target'], r'C:\x\other.exe')
        self.assertEqual(self.shell.links[os.path.join(self.desk, T.SHORTCUT)]['target'], ext)
        self.assertEqual(T.launch_exe(self.game), ext)
        self.assertTrue(any('created' in n for n in notes))

        notes, keep = T.uninstall(self.game, shell=self.shell)
        st = T.status(self.game)
        self.assertFalse(st['installed'])
        self.assertTrue(st['removed_by_user'])
        self.assertTrue(os.path.isfile(os.path.join(keep, T.EXT)))
        self.assertTrue(os.path.isfile(os.path.join(keep, T.DLL)))
        self.assertEqual(self.shell.links[os.path.join(self.desk, 'Two Worlds.lnk')]['target'], self.g(T.EXE))
        self.assertEqual(self.shell.links[os.path.join(self.menu, 'Two Worlds.lnk')]['target'], self.g(T.EXE))
        self.assertFalse(os.path.exists(os.path.join(self.desk, T.SHORTCUT)))
        self.assertTrue(os.path.isdir(self.g('TWSEPlugins')), 'plugins stay')
        self.assertEqual(T.launch_exe(self.game), self.g(T.EXE))

    def test_replace_switch(self):
        self.install(replace_exe=True)
        st = T.status(self.game)
        self.assertTrue(st['exe_replaced'])
        self.assertEqual(open(self.g(T.BACKUP), 'rb').read(), self.stock)
        self.assertTrue(twse_patch.pruefen(open(self.g(T.EXE), 'rb').read())['fertig'])
        T.set_replace_exe(self.game, False)
        self.assertEqual(open(self.g(T.EXE), 'rb').read(), self.stock)
        self.assertFalse(os.path.exists(self.g(T.BACKUP)))
        self.assertFalse(T.status(self.game)['exe_replaced'])
        # switch on again, then a Steam "verify" puts the original back
        T.set_replace_exe(self.game, True)
        with open(self.g(T.EXE), 'wb') as f:
            f.write(self.stock)
        self.assertTrue(T.status(self.game)['exe_restored_by_launcher'])
        # remove with the switch on restores the original
        T.set_replace_exe(self.game, True)
        T.uninstall(self.game, shell=self.shell)
        self.assertEqual(open(self.g(T.EXE), 'rb').read(), self.stock)

    def test_reinstall_after_replace_uses_the_original(self):
        self.install(replace_exe=True)
        os.remove(self.g(T.EXT))
        self.install(replace_exe=True)
        self.assertTrue(T.status(self.game)['installed'])

    def test_no_rights_on_a_shortcut(self):
        locked = os.path.join(self.menu, 'Two Worlds.lnk')
        self.shell.locked.add(locked)
        notes = self.install()
        self.assertTrue(any('administrator' in n for n in notes))
        self.assertEqual(self.shell.links[locked]['target'], self.g(T.EXE))
        self.assertEqual(len(T.load_record(self.game)['shortcuts']), 1)

    def test_shortcut_changed_meanwhile_is_left_alone(self):
        self.install()
        lnk = os.path.join(self.desk, 'Two Worlds.lnk')
        self.shell.links[lnk]['target'] = r'D:\mine.exe'
        notes, _ = T.uninstall(self.game, shell=self.shell)
        self.assertEqual(self.shell.links[lnk]['target'], r'D:\mine.exe')
        self.assertTrue(any('changed or removed' in n for n in notes))

    def test_remove_leaves_files_it_did_not_create(self):
        ext = bytearray(twse_patch.anwenden(self.stock))
        ext[0x280C8B] ^= 1                           # a whistle-patched exe from before
        with open(self.g(T.EXT), 'wb') as f:
            f.write(ext)
        self.install()
        self.assertEqual(T.foreign_files(self.game), [T.EXT])
        notes, keep = T.uninstall(self.game, shell=self.shell)
        self.assertEqual(open(self.g(T.EXT), 'rb').read(), bytes(ext), 'left in place')
        self.assertFalse(os.path.exists(self.g(T.DLL)), 'the dll the Mod Manager wrote is moved')
        self.assertTrue(any('not set up by the Mod Manager' in n for n in notes))
        # asked and agreed: moved as well
        T.uninstall(self.game, shell=self.shell, include_foreign=True)
        self.assertFalse(os.path.exists(self.g(T.EXT)))

    def test_unknown_exe_is_refused(self):
        with open(self.g(T.EXE), 'wb') as f:
            f.write(b'MZ' + b'\0' * 5000)
        with self.assertRaises(T.TwseError):
            self.install()
        self.assertFalse(os.path.exists(self.g(T.EXT)))

    def test_existing_extended_exe_is_kept(self):
        """A TwoWorldsExtended.exe that passes the check (maybe whistle-patched)
        stays as it is."""
        ext = bytearray(twse_patch.anwenden(self.stock))
        ext[0x280C8B] ^= 1                           # the whistle range byte
        with open(self.g(T.EXT), 'wb') as f:
            f.write(ext)
        self.install()
        self.assertEqual(open(self.g(T.EXT), 'rb').read(), bytes(ext))


class RealShortcuts(unittest.TestCase):
    """One round trip through PowerShell / WScript.Shell in a temp folder."""

    def test_write_and_read(self):
        tmp = tempfile.mkdtemp(prefix='mm_lnk_test_')
        try:
            p = os.path.join(tmp, 'Test Ü.lnk')
            target = os.path.join(tmp, 'Two Worlds', 'TwoWorlds.exe')
            os.makedirs(os.path.dirname(target))
            open(target, 'wb').close()
            sh = T.Shell()
            r = sh.write([{'path': p, 'target': target, 'workdir': os.path.dirname(target)}])
            self.assertTrue(r[p]['ok'], r)
            self.assertTrue(os.path.isfile(p))
            found = T.find_shortcuts(os.path.dirname(target), sh, [(tmp, False)])
            self.assertEqual(list(found), [p])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
