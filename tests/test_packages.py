"""Mod packages (2.6.0): zip check, where files go, on/off, remove, update
with a changed ini, loose plugins. Everything in temp folders; the registry
switch is a dict here.

    py -3.13 -m unittest tests.test_packages      (from the repo root)
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tests import wdwrite as merger  # noqa: E402
import mod_manager as M  # noqa: E402
import packages as P  # noqa: E402

DLL32 = open(os.path.join(ROOT, 'bin', 'twse.dll'), 'rb').read()
DLL64_PATH = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'version.dll')


def wd_bytes(tmp, files):
    p = os.path.join(tmp, 'x.wd')
    merger.write_wd(p, [({'path': k, 'flags': 0x01, 'rlen': len(v), 'res': None, 'id': None, 'guid': None},
                         zlib.compress(v)) for k, v in files.items()])
    data = open(p, 'rb').read()
    os.remove(p)
    return data


def wd_check(path):
    r = M.wd_peek(path)
    return r['ok'], r['error']


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='mm_pkg_test_')
        self.game = os.path.join(self.tmp, 'game')
        os.makedirs(os.path.join(self.game, 'Mods'))
        self.switch = {}
        self.pk = P.Packages(self.game, set_switch=self.switch.__setitem__,
                             drop_switch=lambda n: self.switch.pop(n, None))
        self.wd = wd_bytes(self.tmp, {'Scripts\\a.txt': b'hello'})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def zip(self, name, files):
        p = os.path.join(self.tmp, name)
        with zipfile.ZipFile(p, 'w', zipfile.ZIP_DEFLATED) as z:
            for k, v in files.items():
                z.writestr(k, v)
        return p

    def g(self, *parts):
        return os.path.join(self.game, *parts)

    def spell(self, ini=b'[Spell]\nPower=1\n', name='SpellRework_v2.zip', extra=None):
        files = {'Mods/SpellRework_v2.wd': self.wd, 'TWSEPlugins/SpellRework.dll': DLL32,
                 'TWSEPlugins/SpellRework.ini': ini, 'readme.txt': b'Read me'}
        files.update(extra or {})
        return self.zip(name, files)


class Check(Base):
    def test_routes_by_folder_and_extension(self):
        plan = P.inspect(self.spell(), wd_check)
        self.assertTrue(plan['ok'], plan['errors'])
        self.assertEqual(sorted(f['dest'] for f in plan['files']),
                         ['Mods/SpellRework_v2.wd', 'TWSEPlugins/SpellRework.dll', 'TWSEPlugins/SpellRework.ini'])
        self.assertTrue(plan['needs_twse'])
        self.assertEqual(plan['shown'], ['readme.txt'])
        self.assertEqual(plan['readme'], 'Read me')
        loose = P.inspect(self.zip('loose.zip', {'a.wd': self.wd, 'b.dll': DLL32, 'b.ini': b'x'}), wd_check)
        self.assertEqual(sorted(f['dest'] for f in loose['files']), ['Mods/a.wd', 'TWSEPlugins/b.dll', 'TWSEPlugins/b.ini'])

    def test_wrapper_folder_is_ignored(self):
        plan = P.inspect(self.zip('w.zip', {'SpellRework_v2/Mods/s.wd': self.wd,
                                            'SpellRework_v2/TWSEPlugins/s.dll': DLL32}), wd_check)
        self.assertTrue(plan['ok'], plan['errors'])
        self.assertEqual(sorted(f['dest'] for f in plan['files']), ['Mods/s.wd', 'TWSEPlugins/s.dll'])

    def test_programs_refuse_the_whole_zip(self):
        for bad in ('setup.exe', 'install.bat', 'x/run.ps1', 'a.vbs', 'go.cmd'):
            plan = P.inspect(self.zip('bad.zip', {'a.wd': self.wd, bad: b'MZ'}), wd_check)
            self.assertFalse(plan['ok'], bad)
            self.assertIn('not allowed', plan['errors'][0])
            self.assertEqual(plan['files'], [])

    def test_unsafe_paths(self):
        for bad in ('../evil.wd', 'a/../../evil.wd', '/abs.wd', 'C:/x.wd'):
            p = os.path.join(self.tmp, 'u.zip')
            with zipfile.ZipFile(p, 'w') as z:
                z.writestr(zipfile.ZipInfo(bad), self.wd)
            plan = P.inspect(p, wd_check)
            self.assertFalse(plan['ok'], bad)

    def test_wd_and_dll_are_checked(self):
        plan = P.inspect(self.zip('f.zip', {'broken.wd': b'not a wd'}), wd_check)
        self.assertFalse(plan['ok'])
        plan = P.inspect(self.zip('f.zip', {'fake.dll': b'hello'}), wd_check)
        self.assertFalse(plan['ok'])
        self.assertIn('32-bit', plan['errors'][0])
        if os.path.isfile(DLL64_PATH):
            plan = P.inspect(self.zip('f.zip', {'x64.dll': open(DLL64_PATH, 'rb').read()}), wd_check)
            self.assertFalse(plan['ok'])
            self.assertIn('x64', plan['errors'][0])

    def test_modinfo_and_misc(self):
        plan = P.inspect(self.zip('m.zip', {'modinfo.json': json.dumps({'name': 'Spell Rework', 'version': '2.0',
                                                                         'requires': ['twse']}),
                                            'a.wd': self.wd, 'Textures/x.dds': b'dds'}), wd_check)
        self.assertTrue(plan['ok'], plan['errors'])
        self.assertEqual((plan['name'], plan['version'], plan['needs_twse']), ('Spell Rework', '2.0', True))
        self.assertEqual(plan['ignored'], ['Textures/x.dds'])
        self.assertFalse(P.inspect(self.zip('e.zip', {'readme.txt': b'x'}), wd_check)['ok'])
        self.assertFalse(P.inspect(self.zip('d.zip', {'a/x.wd': self.wd, 'b/x.wd': self.wd}), wd_check)['ok'])
        self.assertFalse(P.inspect(self.zip('s.zip', {'TWSEPlugins/sub/x.dll': DLL32}), wd_check)['ok'])
        not_zip = os.path.join(self.tmp, 'n.zip')
        open(not_zip, 'wb').write(b'nope')
        self.assertFalse(P.inspect(not_zip, wd_check)['ok'])


class Lifecycle(Base):
    def install(self, path, enable=True):
        plan = P.inspect(path, wd_check)
        self.assertTrue(plan['ok'], plan['errors'])
        return plan, self.pk.install(path, plan, enable)

    def test_install_off_on_remove(self):
        plan, _ = self.install(self.spell())
        name = plan['name']
        self.assertTrue(os.path.isfile(self.g('Mods', 'SpellRework_v2.wd')))
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', 'SpellRework.dll')))
        self.assertEqual(self.switch['SpellRework_v2.wd'], 1)
        self.assertEqual(self.pk.state(name), 'on')
        self.assertTrue(os.path.isfile(self.g('Mods', '_packages.json')))

        self.pk.set_enabled(name, False)
        self.assertEqual(self.switch['SpellRework_v2.wd'], 0)
        self.assertFalse(os.path.exists(self.g('TWSEPlugins', 'SpellRework.dll')))
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', '_disabled', name, 'SpellRework.dll')))
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', '_disabled', name, 'SpellRework.ini')))
        self.assertEqual(self.pk.state(name), 'off')
        self.assertTrue(os.path.isfile(self.g('Mods', 'SpellRework_v2.wd')), 'the .wd stays, only its switch')

        again = P.Packages(self.game)                 # read back from the file
        self.assertFalse(again.get(name)['enabled'])

        self.pk.set_enabled(name, True)
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', 'SpellRework.dll')))
        self.assertFalse(os.path.exists(self.g('TWSEPlugins', '_disabled')), 'empty off folder is cleaned up')

        keep = self.pk.remove(name)
        for rel in (('Mods', 'SpellRework_v2.wd'), ('TWSEPlugins', 'SpellRework.dll'), ('TWSEPlugins', 'SpellRework.ini')):
            self.assertFalse(os.path.exists(self.g(*rel)))
            self.assertTrue(os.path.isfile(os.path.join(keep, *rel)), rel)
        self.assertTrue(keep.startswith(self.g('Mods', '_removed')))
        self.assertIsNone(self.pk.get(name))
        self.assertEqual(self.switch['SpellRework_v2.wd'], 0)

    def test_install_switched_off(self):
        plan, _ = self.install(self.spell(), enable=False)
        self.assertEqual(self.switch['SpellRework_v2.wd'], 0)
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', '_disabled', plan['name'], 'SpellRework.dll')))
        self.assertFalse(os.path.exists(self.g('TWSEPlugins', 'SpellRework.dll')))

    def test_update_keeps_a_changed_ini(self):
        plan, _ = self.install(self.spell(ini=b'v1'))
        with open(self.g('TWSEPlugins', 'SpellRework.ini'), 'wb') as f:
            f.write(b'mine')
        prev = self.pk.preview(P.inspect(self.spell(ini=b'v2'), wd_check))
        self.assertIn(('TWSEPlugins/SpellRework.ini', 'yours kept, new one as .ini.new'), prev)
        _, notes = self.install(self.spell(ini=b'v2'))
        self.assertEqual(open(self.g('TWSEPlugins', 'SpellRework.ini'), 'rb').read(), b'mine')
        self.assertEqual(open(self.g('TWSEPlugins', 'SpellRework.ini.new'), 'rb').read(), b'v2')
        self.assertTrue(any('.ini.new' in n for n in notes))
        _, notes = self.install(self.spell(ini=b'v3'))           # still the player's
        self.assertEqual(open(self.g('TWSEPlugins', 'SpellRework.ini'), 'rb').read(), b'mine')

    def test_update_replaces_an_untouched_ini_and_drops_old_files(self):
        self.install(self.spell(ini=b'v1', extra={'TWSEPlugins/Old.dll': DLL32}))
        plan, notes = self.install(self.spell(ini=b'v2'))
        self.assertEqual(open(self.g('TWSEPlugins', 'SpellRework.ini'), 'rb').read(), b'v2')
        self.assertFalse(os.path.exists(self.g('TWSEPlugins', 'Old.dll')))
        self.assertTrue(any('old version' in n for n in notes))
        self.assertEqual(len(self.pk.get(plan['name'])['files']), 3)

    def test_foreign_file_is_backed_up_and_other_package_blocks(self):
        os.makedirs(self.g('TWSEPlugins'))
        with open(self.g('TWSEPlugins', 'SpellRework.dll'), 'wb') as f:
            f.write(b'someone elses')
        _, notes = self.install(self.spell())
        self.assertEqual(open(self.g('TWSEPlugins', 'SpellRework.dll.backup'), 'rb').read(), b'someone elses')
        other = P.inspect(self.zip('Other.zip', {'TWSEPlugins/SpellRework.dll': DLL32}), wd_check)
        self.assertTrue(self.pk.conflicts(other))
        with self.assertRaises(P.PackageError):
            self.pk.install(os.path.join(self.tmp, 'Other.zip'), other)

    def test_loose_plugins(self):
        os.makedirs(self.g('TWSEPlugins'))
        for n in ('TWExtended.dll', 'TWTrainer.dll', 'TWTrainer.ini'):
            open(self.g('TWSEPlugins', n), 'wb').write(b'x')
        self.install(self.spell())
        self.assertEqual(self.pk.loose_plugins(), [('TWExtended.dll', True), ('TWTrainer.dll', True)])
        self.pk.set_loose('TWTrainer.dll', False)
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', '_disabled', 'TWTrainer.dll')))
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', '_disabled', 'TWTrainer.ini')))
        self.assertIn(('TWTrainer.dll', False), self.pk.loose_plugins())
        self.pk.set_loose('TWTrainer.dll', True)
        self.assertTrue(os.path.isfile(self.g('TWSEPlugins', 'TWTrainer.dll')))

    def test_vortex_manifests(self):
        os.makedirs(self.g('TWSEPlugins'))
        with open(self.g('Mods', 'vortex.deployment.json'), 'w', encoding='utf-8') as f:
            json.dump({'files': [{'relPath': 'Vortex.wd', 'source': 'm1'}]}, f)
        with open(self.g('vortex.deployment.twoworlds-gameroot.json'), 'w', encoding='utf-8') as f:
            json.dump({'files': [{'relPath': 'TWSEPlugins\\VPlug.dll', 'source': 'm2'},
                                 {'relPath': 'Mods\\Pack.wd', 'source': 'm2'}]}, f)
        self.assertEqual(self.pk.vortex_files(), {'mods/vortex.wd', 'twseplugins/vplug.dll', 'mods/pack.wd'})

    def test_missing_file_shows_incomplete(self):
        plan, _ = self.install(self.spell())
        os.remove(self.g('TWSEPlugins', 'SpellRework.dll'))
        self.assertEqual(self.pk.state(plan['name']), 'incomplete')


if __name__ == '__main__':
    unittest.main()
