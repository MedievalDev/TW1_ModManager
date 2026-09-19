"""Cases from debug round 1 (adversarial run, 2026-09-19)."""
import os
import shutil
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import levelcache  # noqa: E402
import merger  # noqa: E402
import modscan as S  # noqa: E402
import tw1_lan  # noqa: E402

GAME = r'F:\SteamLibrary\steamapps\common\Two Worlds - Epic Edition'
BS = chr(92)
QTX = BS.join(('Scripts', 'Quests', 'TwoWorldsQuests.qtx'))
LND = BS.join(('Levels', 'Map_E01.lnd'))
PHX = BS.join(('Levels', 'physic', 'Map_E01.phx'))
LAN = BS.join(('Language', 'TwoWorldsQuests.lan'))
NL = chr(10)


def wd(path, files):
    merger.write_wd(path, [({'path': k, 'flags': 0x01, 'rlen': len(v), 'res': None, 'id': None, 'guid': None},
                            zlib.compress(v)) for k, v in files.items()])
    return path


@unittest.skipUnless(os.path.isdir(GAME), 'game not on this PC')
class Round1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='mm_r1_')
        cls.R = S.Retail(GAME)
        cls.qtx = cls.R.get(S.QTX).decode('latin-1')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def p(self, name):
        return os.path.join(self.tmp, name)

    def test_half_tiles_are_one_tile(self):
        c = wd(self.p('only_phx.wd'), {PHX: b'PHX-C'})
        both = wd(self.p('both.wd'), {LND: self.R.get(LND.lower()), PHX: b'PHX-A'})
        m = merger.Merge([c, both], self.R)
        self.assertEqual([(x.kind, x.key) for x in m.conflicts], [('tile', 'E1')])
        self.assertEqual(S.compare(c, m.infos[0], both, m.infos[1])['level'], 'yellow')
        m.build(self.p('half.wd'), {}, 1)
        self.assertEqual(S.archive(self.p('half.wd')).get(PHX.lower()), b'PHX-A')
        rep = m.build(self.p('half2.wd'), {}, 0)
        out = S.archive(self.p('half2.wd'))
        self.assertEqual(out.get(PHX.lower()), b'PHX-C')
        self.assertNotIn(LND.lower(), out.by_low)
        self.assertTrue(any('ships no .lnd' in x for x in rep))

    def test_phx_only_against_lnd_only(self):
        c = wd(self.p('c_phx.wd'), {PHX: b'P'})
        d = wd(self.p('d_lnd.wd'), {LND: self.R.get(LND.lower())[:-1] + b'x'})
        m = merger.Merge([c, d], self.R)
        self.assertEqual([x.kind for x in m.conflicts], ['tile'])

    def test_unreadable_map_does_not_stop_the_merge(self):
        g = wd(self.p('gmap.wd'), {LND: b'garbage-' * 50})
        x = wd(self.p('other.wd'), {BS.join(('Dummy', 'x.txt')): b'hi'})
        rep = merger.Merge([g, x], self.R).build(self.p('gm.wd'))
        self.assertTrue(any('not readable as a map' in r for r in rep))

    def test_cache_survives_a_broken_archive(self):
        empty = self.p('e.wd')
        open(empty, 'wb').close()
        _blob, rep = levelcache.plan(GAME, [empty])
        self.assertEqual(len(rep['broken']), 1)
        self.assertEqual(rep['maps'], 160)

    def test_small_things(self):
        z = wd(self.p('zero.wd'), {})
        self.assertIsNone(S.scan(z, self.R)['error'])
        self.assertEqual(S.par_key('12|SND|PIPE|3'), ('12', 'SND|PIPE', '3'))
        self.assertLessEqual(len(merger.safe_name('x' * 300)), 83)
        keep = self.p('keep.wd.tmp')
        with open(keep, 'wb') as f:
            f.write(b'mine')
        wd(self.p('keep.wd'), {BS.join(('Data', 'a.txt')): b'x'})
        with open(keep, 'rb') as f:
            self.assertEqual(f.read(), b'mine')

    def test_garbage_par_does_not_hide_the_quest_file(self):
        blk = S.qtx_units(self.qtx)[1]['NPC NPC_26']
        changed = self.qtx.replace(blk, blk.rstrip(NL) + NL + '  OBJECTS False Y' + NL, 1)
        mod = wd(self.p('gp.wd'), {BS.join(('Parameters', 'TwoWorlds.par')): b'garbage',
                                   QTX: changed.encode('latin-1')})
        info = S.scan(mod, self.R)
        self.assertIsNone(info['par'])
        self.assertEqual(list(info['qtx']), ['NPC NPC_26'])
        self.assertTrue(info['notes'])

    def test_quest_file_keeps_its_layout(self):
        head = '  ; a comment before everything' + NL + NL
        odd = head + self.qtx.replace('END' + NL + 'NPC NPC_19', 'END' + NL * 3 + 'NPC NPC_19', 1)
        a = wd(self.p('odd.wd'), {QTX: odd.encode('latin-1')})
        b = wd(self.p('odd2.wd'), {QTX: (self.qtx + 'QUEST Q_395 0 (null) (null) 0 True' + NL + 'END' + NL).encode('latin-1')})
        self.assertEqual(list(S.scan(a, self.R)['qtx']), [S.HEAD])
        merger.Merge([a, b], self.R).build(self.p('odd_out.wd'))
        text = S.archive(self.p('odd_out.wd')).get(S.QTX).decode('latin-1')
        self.assertTrue(text.startswith(head))
        self.assertIn('END' + NL * 3 + 'NPC NPC_19', text)
        self.assertIn('QUEST Q_395', text)
        order, units = S.qtx_units(self.qtx)
        self.assertEqual(S.qtx_text(order, units), self.qtx)          # the game's file comes back byte for byte
        binary = wd(self.p('bin.wd'), {QTX: bytes(range(256)) * 40})
        self.assertIsNone(S.scan(binary, self.R)['qtx'])

    def test_removed_text_key(self):
        tr, al, rest = tw1_lan.read(self.R.get(LAN.lower()))
        key = next(k for k in tr if k.startswith('translate'))
        less = dict(tr)
        less.pop(key)
        a = wd(self.p('r1.wd'), {LAN: tw1_lan.build(less, al, rest)})
        b = wd(self.p('r2.wd'), {LAN: tw1_lan.build(dict(tr, **{key: 'B'}), al, rest)})
        m = merger.Merge([a, b], self.R)
        self.assertEqual([c.key[1] for c in m.conflicts], [key])
        m.build(self.p('rk0.wd'), {}, 0)
        self.assertNotIn(key, tw1_lan.read(S.archive(self.p('rk0.wd')).get(LAN.lower()))[0])
        m.build(self.p('rk1.wd'), {}, 1)
        self.assertEqual(tw1_lan.read(S.archive(self.p('rk1.wd')).get(LAN.lower()))[0][key], 'B')

    def test_same_file_name_from_two_folders(self):
        os.makedirs(self.p('x'), exist_ok=True)
        os.makedirs(self.p('y'), exist_ok=True)
        a = wd(os.path.join(self.p('x'), 'Same.wd'), {BS.join(('Data', 'a.txt')): b'1'})
        b = wd(os.path.join(self.p('y'), 'Same.wd'), {BS.join(('Data', 'b.txt')): b'2'})
        m = merger.Merge([a, b], self.R)
        self.assertEqual(len(set(m.names)), 2)


if __name__ == '__main__':
    unittest.main()
