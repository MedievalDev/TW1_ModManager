"""Merger against the real community mods (Desktop\\modsTW1), if they are there.

The strong check: the merged archive is scanned again, and every unit
(par field, quest block, text key) must carry the value of the mod that was
supposed to win it. Sources are hashed before and after.
"""
import glob
import hashlib
import itertools
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import merger  # noqa: E402
import modscan as S  # noqa: E402
import tw1_lan  # noqa: E402
import tw1_wd  # noqa: E402

GAME = r'F:\SteamLibrary\steamapps\common\Two Worlds - Epic Edition'
MODS = os.path.join(os.path.expanduser('~'), 'Desktop', 'modsTW1')


def sha(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


@unittest.skipUnless(os.path.isdir(MODS) and os.path.isdir(GAME), 'community mods or game not on this PC')
class RealMods(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='mm_merge_')
        cls.retail = S.Retail(GAME)
        cls.paths = {os.path.basename(p).split('.')[0].split()[0]: p for p in glob.glob(os.path.join(MODS, '*.wd'))}
        cls.before = {p: sha(p) for p in cls.paths.values()}

    @classmethod
    def tearDownClass(cls):
        for p, h in cls.before.items():
            assert sha(p) == h, f'source changed: {p}'
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_roundtrips(self):
        """Splitting into units and putting back gives the same file."""
        for name, p in self.paths.items():
            a = S.archive(p)
            raw = a.get(S.QTX)
            if raw:
                order, units = S.qtx_units(raw.decode('latin-1'))
                want = raw.decode('latin-1').replace('\r\n', '\n')
                self.assertEqual(S.qtx_text(order, units), want if want.endswith('\n') else want + '\n', name)
            for e in a.entries:
                if e['low'].endswith('.lan'):
                    raw = a.data(e)
                    tr, al, rest = tw1_lan.read(raw)
                    self.assertEqual(tw1_lan.build(tr, al, rest), raw, f"{name} {e['path']}")
                    trees = tw1_lan.parse_trees(rest)
                    self.assertEqual(tw1_lan.build_trees(trees), rest, f"{name} {e['path']} trees")

    def _expected(self, m, kind, per_mod, decisions, main):
        """{unit key: hash} the merged file has to show against the game."""
        clash = {c.key if kind in ('par', 'qtx') else c.key[1]: c for c in m.conflicts if c.kind == kind}
        out = {}
        for i, units in per_mod.items():
            for k, h in units.items():
                ck = k
                if kind == 'par':
                    ent = k.rsplit('|', 1)[0] + '|*'
                    ck = ent if ent in clash else k
                c = clash.get(ck)
                if c is None:
                    out[k] = h
                    continue
                cid = (kind, ck) if kind in ('par', 'qtx') else (kind, c.key)
                w = decisions.get(cid, main if main in c.mods else c.mods[0])
                if w == i:
                    out[k] = h
        return out

    def _check(self, names, main=None, decisions=None):
        paths = [self.paths[n] for n in names]
        m = merger.Merge(paths, self.retail, self.tmp)
        out = os.path.join(self.tmp, '_'.join(names) + f'_{main}_{len(decisions or {})}.wd')
        t = time.time()
        rep = m.build(out, decisions or {}, main)
        dt = time.time() - t
        got = S.scan(out, self.retail, None)
        self.assertIsNone(got['error'])
        tw1_wd.read(out)                                     # independent reader
        prov_par = m.fine.get(S.PAR)
        if prov_par:
            exp = self._expected(m, 'par', {i: m.infos[i]['par'] for i in prov_par}, decisions or {}, main)
            self.assertEqual({k: v for k, v in got['par'].items()}, exp, 'par units')
        prov_q = m.fine.get(S.QTX)
        if prov_q:
            exp = self._expected(m, 'qtx', {i: m.infos[i]['qtx'] for i in prov_q}, decisions or {}, main)
            self.assertEqual(got['qtx'], exp, 'quest units')
        # every path of every source is in the result (but the old cache)
        fought = {c.key for c in m.conflicts if c.kind == 'tile'}
        want = {low for info in m.infos for low in info['files']
                if (S.tile_of(low) or S.tile_follower(low)) not in fought}      # those go with the tile's winner
        got['files'] = {k: v for k, v in got['files'].items()
                        if (S.tile_of(k) or S.tile_follower(k)) not in fought}
        self.assertEqual(set(got['files']) - {'levels\\map_levelheaders.lhc'},
                         want - {'levels\\map_levelheaders.lhc'})
        return m, rep, dt, out

    def test_green_pairs_need_no_decision(self):
        for a, b in itertools.combinations(sorted(self.paths), 2):
            m = merger.Merge([self.paths[a], self.paths[b]], self.retail, self.tmp)
            if m.level == 'green':
                self.assertEqual(m.conflicts, [])
                self._check([a, b])

    def test_yellow_each_side(self):
        for names in (['Yamalin', 'revamp'], ['Yamalin', 'bettergearnpcs'], ['Elite', 'Yamalin']):
            if not all(n in self.paths for n in names):
                continue
            for main in (0, 1):
                m, _rep, _dt, _o = self._check(names, main=main)
                self.assertEqual(m.level, 'yellow')
            # decide half of the conflicts against the main mod
            m = merger.Merge([self.paths[n] for n in names], self.retail, self.tmp)
            dec = {c.cid: c.mods[-1] for c in m.conflicts[::2]}
            self._check(names, main=0, decisions=dec)

    def test_red_needs_main(self):
        if 'Elite' in self.paths and 'revamp' in self.paths:
            m, rep, dt, _o = self._check(['Elite', 'revamp'], main=1)
            self.assertEqual(m.level, 'red')
            self.assertGreater(m.soft, S.SOFT_LIMIT)
        if 'Elite' in self.paths and 'skill' in self.paths:
            m, _rep, _dt, out = self._check(['Elite', 'skill'], main=1)
            self.assertEqual(m.level, 'red')
            low = 'scripts\\rpgcompute\\rpgcompute.eco'
            self.assertEqual(S.archive(out).get(low), S.archive(self.paths['skill']).get(low))

    def test_three_mods(self):
        names = [n for n in ('Yamalin', 'revamp', 'bettergearnpcs') if n in self.paths]
        if len(names) == 3:
            self._check(names, main=0)

    def test_maps_pair_and_markers(self):
        if not ('Dream' in self.paths and 'Yamalin' in self.paths):
            self.skipTest('no map mods')
        import tw1_lnd
        import tw1_lhc
        for main, other in ((0, 1), (1, 0)):
            names = ['Dream', 'Yamalin']
            m, rep, _dt, out = self._check(names, main=main)
            a = S.archive(out)
            src = [S.archive(self.paths[n]) for n in names]
            for c in [c for c in m.conflicts if c.kind == 'tile']:
                for low, e in a.by_low.items():
                    if S.tile_of(low) != c.key:
                        continue
                    if low.endswith('.phx'):
                        self.assertEqual(a.data(e), src[main].get(low), f'{low}: physics from the chosen mod')
                    else:
                        got = tw1_lnd.markers_full(a.data(e))
                        keys = {(n, i) for n, lst in got.items() for i, _p in lst}
                        for s in src:
                            if low in s.by_low:
                                for n, lst in tw1_lnd.markers_full(s.get(low)).items():
                                    if not merger.quest_marker(n):
                                        continue          # furniture stays with its own ground
                                    for i, _p in lst:
                                        self.assertTrue((n, i) in keys, f'{low}: marker {n} {i} lost')
            cache = tw1_lhc.parse(a.get('levels\\map_levelheaders.lhc'))
            for low, e in a.by_low.items():
                if tw1_lhc.is_map(low):
                    rec = next(v for k, v in cache.items() if k.lower() == low)
                    self.assertEqual(rec, tw1_lhc.header_of(a.data(e)), f'cache record of {low}')

    def test_never_overwrites(self):
        p = os.path.join(self.tmp, 'exists.wd')
        open(p, 'wb').close()
        m = merger.Merge([self.paths['revamp'], self.paths['skill']], self.retail, self.tmp)
        with self.assertRaises(FileExistsError):
            m.build(p)
        self.assertEqual(os.path.getsize(p), 0)


class Names(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(merger.safe_name('  My Mod.wd '), 'My Mod.wd')
        self.assertEqual(merger.safe_name('a/b\\c:*?'), 'abc.wd')
        self.assertEqual(merger.safe_name(''), 'MergedMod.wd')


if __name__ == '__main__':
    unittest.main()
