"""Small made-up mods for the rules the real mods do not exercise."""
import os
import shutil
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import merger  # noqa: E402
import modscan as S  # noqa: E402
import tw1_lan  # noqa: E402

GAME = r'F:\SteamLibrary\steamapps\common\Two Worlds - Epic Edition'
QTX = 'Scripts\\Quests\\TwoWorldsQuests.qtx'


def wd(path, files):
    """files: {inner: bytes}"""
    merger.write_wd(path, [({'path': k, 'flags': 0x01, 'rlen': len(v), 'res': None, 'id': None, 'guid': None},
                            zlib.compress(v)) for k, v in files.items()])
    return path


@unittest.skipUnless(os.path.isdir(GAME), 'game not on this PC')
class Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='mm_syn_')
        cls.R = S.Retail(GAME)
        cls.qtx = cls.R.get(S.QTX).decode('latin-1')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def p(self, name):
        return os.path.join(self.tmp, name)

    def _block(self, key):
        return S.qtx_units(self.qtx)[1][key]

    def test_removed_against_changed_is_a_conflict(self):
        blk = self._block('NPC NPC_26')
        a = wd(self.p('del.wd'), {QTX: self.qtx.replace(blk + '\n', '').encode('latin-1')})
        b = wd(self.p('chg.wd'), {QTX: self.qtx.replace(blk, blk.replace(' 0\n', ' 1\n', 1) + '\n  OBJECTS False X', 1).encode('latin-1')})
        for order in ([a, b], [b, a]):
            m = merger.Merge(order, self.R)
            self.assertEqual([c.key for c in m.conflicts], ['NPC NPC_26'])
            self.assertEqual(m.level, 'yellow')
            c = S.compare(order[0], m.infos[0], order[1], m.infos[1])
            self.assertEqual(c['level'], 'yellow')
            for main in (0, 1):
                out = self.p(f'o_{os.path.basename(order[0])}_{main}.wd')
                m.build(out, {}, main)
                units = S.qtx_units(S.archive(out).get(S.QTX).decode('latin-1'))[1]
                removed_wins = order[main] == a
                self.assertEqual('NPC NPC_26' not in units, removed_wins)

    def test_both_add_blocks_and_crlf(self):
        new1 = 'QUEST Q_390 0 (null) (null) 0 True\n  GIVER NPC NPC_16\nEND'
        new2 = 'QUEST Q_391 0 (null) (null) 0 True\n  GIVER NPC NPC_19\nEND'
        a = wd(self.p('a1.wd'), {QTX: (self.qtx + new1 + '\n').encode('latin-1')})
        b = wd(self.p('a2.wd'), {QTX: (self.qtx + new2 + '\n').replace('\n', '\r\n').encode('latin-1')})
        m = merger.Merge([a, b], self.R)
        self.assertEqual(m.conflicts, [])
        out = self.p('adds.wd')
        m.build(out)
        text = S.archive(out).get(S.QTX).decode('latin-1')
        self.assertNotIn('\r', text)
        units = S.qtx_units(text)[1]
        self.assertIn('QUEST Q_390', units)
        self.assertIn('QUEST Q_391', units)
        self.assertEqual(len(units), len(S.qtx_units(self.qtx)[1]) + 2)

    def test_file_hash_sees_a_replaced_archive(self):
        p = self.p('h.wd')
        wd(p, {'Data\\x.bin': b'AAAA'})
        h1 = S.file_hash(p, 'data\\x.bin')
        os.remove(p)
        wd(p, {'Data\\x.bin': b'BBBB'})
        self.assertNotEqual(S.file_hash(p, 'data\\x.bin'), h1)

    def test_aliases_are_units(self):
        low = 'language\\twoworldsquests.lan'
        tr, al, rest = tw1_lan.read(self.R.get(low))
        a = wd(self.p('l1.wd'), {'Language\\TwoWorldsQuests.lan': tw1_lan.build(dict(tr, translateQ_1='AAA'), al, rest)})
        b = wd(self.p('l2.wd'), {'Language\\TwoWorldsQuests.lan': tw1_lan.build(tr, al + [('NEW_ALIAS', 'target')], rest)})
        m = merger.Merge([a, b], self.R)
        self.assertEqual(m.conflicts, [])
        out = self.p('lan.wd')
        m.build(out)
        tr2, al2, rest2 = tw1_lan.read(S.archive(out).get(low))
        self.assertEqual(tr2['translateQ_1'], 'AAA')
        self.assertIn(('NEW_ALIAS', 'target'), al2)
        self.assertEqual(rest2, rest)

    def test_other_levels_pair_too(self):
        self.assertEqual(S.tile_of('levels\\net_arena.lnd'), 'net_arena')
        self.assertEqual(S.tile_of('levels\\physic\\net_arena.phx'), 'net_arena')
        self.assertEqual(S.tile_of('levels\\map_e01.lnd'), 'E1')
        a = wd(self.p('n1.wd'), {'Levels\\Net_X.lnd': b'one', 'Levels\\physic\\Net_X.phx': b'p1'})
        b = wd(self.p('n2.wd'), {'Levels\\Net_X.lnd': b'two', 'Levels\\physic\\Net_X.phx': b'p2'})
        m = merger.Merge([a, b], self.R)
        self.assertEqual([(c.kind, c.key) for c in m.conflicts], [('tile', 'net_x')])
        out = self.p('net.wd')
        m.build(out, {}, 1)
        o = S.archive(out)
        self.assertEqual((o.get('levels\\net_x.lnd'), o.get('levels\\physic\\net_x.phx')), (b'two', b'p2'))

    def test_quest_marker_filter(self):
        self.assertTrue(merger.quest_marker('MARKER_QUEST_CREATE_ENEMY'))
        self.assertTrue(merger.quest_marker('MARKER_CHEST'))
        self.assertFalse(merger.quest_marker('MARKER_CHAIR'))

    def test_broken_input_fails_cleanly(self):
        bad = self.p('bad.wd')
        open(bad, 'wb').write(b'not an archive')
        good = wd(self.p('good.wd'), {'Data\\a.txt': b'x'})
        with self.assertRaises(ValueError):
            merger.Merge([good, bad], self.R)
        empty = self.p('empty.wd')
        open(empty, 'wb').close()
        self.assertIsNotNone(S.scan(empty, self.R)['error'])
        garbage = wd(self.p('garb.wd'), {'Parameters\\TwoWorlds.par': b'garbage', QTX: bytes(range(256)),
                                         'Language\\TwoWorldsQuests.lan': b'junk'})
        info = S.scan(garbage, self.R)
        self.assertIsNone(info['error'])
        self.assertTrue(info['notes'])
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith('.tmp')], [])


if __name__ == '__main__':
    unittest.main()
