"""Cases from debug round 2 (2026-09-19)."""
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import levelcache  # noqa: E402
import lndmap  # noqa: E402
import merger  # noqa: E402
import modscan as S  # noqa: E402
import tw1_lnd  # noqa: E402
import tw1_par  # noqa: E402

GAME = r'F:\SteamLibrary\steamapps\common\Two Worlds - Epic Edition'
BS = chr(92)
LND = BS.join(('Levels', 'Map_E01.lnd'))
PHX = BS.join(('Levels', 'physic', 'Map_E01.phx'))
PAR = BS.join(('Parameters', 'TwoWorlds.par'))


def wd(path, files):
    merger.write_wd(path, [({'path': k, 'flags': 0x01, 'rlen': len(v), 'res': None, 'id': None, 'guid': None},
                            zlib.compress(v)) for k, v in files.items()])
    return path


@unittest.skipUnless(os.path.isdir(GAME), 'game not on this PC')
class Round2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='mm_r2_')
        cls.R = S.Retail(GAME)
        cls.lnd = tw1_lnd.unwrap(cls.R.get(LND.lower()))
        cls.phx = cls.R.get(PHX.lower())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def p(self, name):
        return os.path.join(self.tmp, name)

    def test_a_copy_of_the_games_file_is_no_change(self):
        a = wd(self.p('copy.wd'), {LND: self.lnd, PHX: self.phx})
        changed = lndmap.add_markers(self.lnd, [('MARKER_QUEST_CREATE_ENEMY', 77, 1000, 1000, 0, 0)])
        b = wd(self.p('real.wd'), {LND: changed, PHX: self.phx})
        info = S.scan(a, self.R)
        self.assertIn(LND.lower(), info['same'])
        m = merger.Merge([a, b], self.R)
        self.assertEqual(m.conflicts, [])
        m.build(self.p('nochange.wd'), {}, 0)                  # even with the copy as main mod
        self.assertEqual(tw1_lnd.unwrap(S.archive(self.p('nochange.wd')).get(LND.lower())), changed)

    def _markers(self, body):
        return {(n, i): p for n, lst in tw1_lnd.markers_full(body).items() for i, p in lst}

    def test_put_back_only_quest_markers_and_heights(self):
        game = self._markers(self.lnd)
        spawn = next(k for k in game if k[0].startswith('MARKER_ENEMY'))
        # mod A removed nothing but ships the tile with one more quest marker far below ground
        x, y = 8000, 8000
        ground = lndmap.Terrain(self.lnd).height(x, y)
        a_body = lndmap.add_markers(self.lnd, [('MARKER_CHEST', 900, x, y, ground - 2000, 0)])
        b_body = lndmap.add_markers(self.lnd, [('MARKER_QUEST_TELEPORT', 901, 9000, 9000, 0, 0)])
        a = wd(self.p('cellar.wd'), {LND: a_body, PHX: self.phx + b'a'})
        b = wd(self.p('tele.wd'), {LND: b_body, PHX: self.phx + b'b'})
        m = merger.Merge([a, b], self.R)
        rep = m.build(self.p('cellar_out.wd'), {}, 1)          # B wins the tile, A's chest is carried over
        got = self._markers(tw1_lnd.unwrap(S.archive(self.p('cellar_out.wd')).get(LND.lower())))
        self.assertIn(('MARKER_CHEST', 900), got)
        self.assertEqual(got[('MARKER_CHEST', 900)][2], ground - 2000, 'same ground: the cellar chest stays below')
        self.assertIn(spawn, got)
        self.assertEqual(len(got), len(game) + 2)
        self.assertTrue(any('MARKER_CHEST 900 from' in r for r in rep))

    def test_whole_entry_winner_is_not_blended(self):
        par = tw1_par.read_par(self.R.get(S.PAR))
        li, ent = next((li, e) for li, pl in enumerate(par.lists) for e in pl.entries
                       if sum(1 for f in e.fields if f.dtype == tw1_par.TYPE_INT32) >= 2)
        ints = [i for i, f in enumerate(ent.fields) if f.dtype == tw1_par.TYPE_INT32][:2]

        def variant(change):
            p = tw1_par.read_par(self.R.get(S.PAR))
            lst = p.lists[li].entries
            e = next(x for x in lst if x.name == ent.name)
            change(lst, e)
            return tw1_par.write_par(p)
        a = wd(self.p('pa.wd'), {PAR: variant(lambda l, e: setattr(e.fields[ints[0]], 'value', 111))})
        b = wd(self.p('pb.wd'), {PAR: variant(lambda l, e: setattr(e.fields[ints[1]], 'value', 222))})
        c = wd(self.p('pc.wd'), {PAR: variant(lambda l, e: l.remove(e))})
        m = merger.Merge([a, b, c], self.R)
        self.assertEqual(len(m.conflicts), 1)
        cid = m.conflicts[0].cid
        m.build(self.p('pw.wd'), {cid: 1})
        out = tw1_par.read_par(S.archive(self.p('pw.wd')).get(S.PAR))
        e = next(x for x in out.lists[li].entries if x.name == ent.name)
        self.assertEqual(e.fields[ints[1]].value, 222)
        self.assertEqual(e.fields[ints[0]].value, ent.fields[ints[0]].value, "B's entry as B has it, not A's 111")
        m.build(self.p('pw2.wd'), {cid: 2})
        out = tw1_par.read_par(S.archive(self.p('pw2.wd')).get(S.PAR))
        self.assertFalse(any(x.name == ent.name for x in out.lists[li].entries))

    def test_minimap_follows_the_tile(self):
        mm = BS.join(('Levels', 'MipMaps', 'Map_E01@0.dds'))
        a = wd(self.p('ma.wd'), {LND: self.lnd + b'', PHX: self.phx + b'1', mm: b'MINI-A'})
        a2 = wd(self.p('ma2.wd'), {LND: lndmap.add_markers(self.lnd, [('MARKER_GATE', 800, 5, 5, 0, 0)]), PHX: self.phx + b'2'})
        m = merger.Merge([a, a2], self.R)
        self.assertEqual([(c.kind, c.key) for c in m.conflicts], [('tile', 'E1')])
        rep = m.build(self.p('mini.wd'), {}, 1)
        self.assertNotIn(mm.lower(), S.archive(self.p('mini.wd')).by_low)
        self.assertTrue(any('left out' in r for r in rep))
        m.build(self.p('mini0.wd'), {}, 0)
        self.assertEqual(S.archive(self.p('mini0.wd')).get(mm.lower()), b'MINI-A')

    def test_cache_on_a_read_only_file(self):
        target = self.p('ro.lhc')
        with open(target, 'wb') as f:
            f.write(b'old')
        os.chmod(target, stat.S_IREAD)
        real = levelcache.target
        levelcache.target = lambda g: target
        try:
            for _ in range(3):
                with self.assertRaises(OSError):
                    levelcache.sync(GAME, [], self.p('bak'))
        finally:
            levelcache.target = real
            os.chmod(target, stat.S_IWRITE)
        self.assertFalse(os.path.isdir(self.p('bak')) and os.listdir(self.p('bak')), 'no backup of a failed write')
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith('.mmtmp')], [])


if __name__ == '__main__':
    unittest.main()
