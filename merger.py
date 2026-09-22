"""Merge several mods into one new archive (experimental).

The source archives are only read. The result is a new ``.wd``; nothing that
exists is overwritten.

How files are put together (units as in modscan.py):

- a path only one mod ships: copied packed as it is, with its directory entry
- ``TwoWorlds.par``, the quest file, ``.lan`` files that several mods ship:
  the first mod in the list that ships the file is the base, the changes of
  the others (measured against the game's file) are applied on top - field
  by field, block by block, key by key. So the base mod's file stays byte
  for byte where nobody else touches it.
- a unit two mods change differently is a conflict: the caller decides per
  conflict, or names a main mod that wins all of them
- maps go per tile: ``.lnd`` and ``.phx`` always from the same mod (the
  collision has to fit the ground). Markers the other mod added to its
  version of the tile are carried over, set onto the chosen tile's ground
  (lndmap.Terrain) where that can be read; game markers the chosen tile lost
  are put back.
- ``Map_LevelHeaders.lhc`` is never taken from a mod: a fresh one is built
  for the merged maps (tw1_lhc.py)

What this cannot know: whether the game runs the result. Two balance mods
merged cleanly on the byte level can still be nonsense in play.
"""

import copy
import os
import re
import struct
import time
import zlib

import fieldnames
import levelcache
import lndmap
import modscan as S
import tw1_lan
import tw1_lhc
import tw1_lnd
import tw1_par

WD_MAGIC = bytes([0xFF, 0xA1, 0xD0, 0x31, 0x57, 0x44, 0x00, 0x02])
FILETIME_EPOCH = 116444736000000000
NL = chr(10)


class Conflict:
    """One unit several mods change differently."""
    __slots__ = ('kind', 'key', 'mods', 'label', 'group')

    def __init__(self, kind, key, mods, label, group):
        self.kind = kind            # 'par' | 'qtx' | 'lan' | 'tree' | 'tile' | 'file' | 'script'
        self.key = key
        self.mods = mods            # indices into Merge.paths, in list order
        self.label = label
        self.group = group          # heading in the dialog

    @property
    def cid(self):
        return (self.kind, self.key)


def safe_name(name):
    name = re.sub(r'[^A-Za-z0-9 _.()+-]+', '', (name or '').strip())
    name = re.sub(r'\.wd$', '', name, flags=re.I).strip(' .')[:80].strip(' .')
    return (name or 'MergedMod') + '.wd'


class Merge:
    def __init__(self, paths, retail, data_dir=None):
        self.paths = list(paths)
        self.names = [os.path.basename(p) for p in self.paths]
        if len(set(n.lower() for n in self.names)) < len(self.names):      # same file name from two folders
            self.names = [os.path.join(os.path.basename(os.path.dirname(p)), os.path.basename(p))
                          for p in self.paths]
        self.retail = retail
        self.infos = [S.scan(p, retail, data_dir) for p in self.paths]
        for name, info in zip(self.names, self.infos):
            if info.get('error'):
                raise ValueError(f"{name}: {info['error']}")
        self.archives = [S.archive(p) for p in self.paths]
        self._pars = {}
        self._qtx = {}
        self._lans = {}
        self.fine = {}              # low path -> [mod idx] merged unit by unit
        self.fine_ok = set()
        self.conflicts = []
        self._find()

    # -- analysis ---------------------------------------------------------

    def _providers(self, low):
        """Mods that ship ``low`` with content that is not the game's own."""
        return [i for i, info in enumerate(self.infos)
                if low in info['files'] and low not in info['same']]

    def _units(self, i, low):
        info = self.infos[i]
        if low == S.PAR:
            return info['par']
        if low == S.QTX:
            return info['qtx']
        return None

    def _find(self):
        lows = {}
        for i, info in enumerate(self.infos):
            if info.get('error'):
                raise ValueError(f"{self.names[i]}: {info['error']}")
            for low in info['files']:
                lows.setdefault(low, []).append(i)
        self.all_lows = lows
        self.all_paths = {low: info['files'][low][0] for info in self.infos for low in info['files']}
        per_mod = [S.tile_files(info) for info in self.infos]
        tiles = {}
        for t in {t for f in per_mod for t in f}:
            files = [f.get(t) or set() for f in per_mod]
            if S.tile_clash(self.paths, self.infos, t, files):
                tiles[t] = {i for i, f in enumerate(files) if f}
        for low in sorted(lows):
            if low == tw1_lhc.INNER.lower():
                continue
            prov = self._providers(low)
            if len(prov) < 2:
                continue
            if low in (S.PAR, S.QTX) and all(self._units(i, low) is not None for i in prov):
                self.fine[low] = prov
                self.fine_ok.add(low)
                kind = 'par' if low == S.PAR else 'qtx'
                self._unit_conflicts(kind, low, {i: self._units(i, low) for i in prov})
                continue
            if low.endswith('.lan') and all(low in self.infos[i]['lan'] for i in prov):
                self.fine[low] = prov
                self.fine_ok.add(low)
                self._unit_conflicts('lan', low, {i: self.infos[i]['lan'][low] for i in prov})
                self._unit_conflicts('tree', low, {i: self.infos[i]['trees'].get(low) or {} for i in prov})
                continue
            hashes = {i: S.file_hash(self.paths[i], low) for i in prov}
            if len(set(hashes.values())) < 2:
                continue
            if S.tile_of(low) or S.tile_follower(low) in tiles:
                continue                          # judged as pairs above; minimaps follow their tile
            shown = self.infos[prov[0]]['files'][low][0]
            kind = 'script' if S.kind_of(low) == 'scripts' else 'file'
            self.conflicts.append(Conflict(kind, low, prov, shown,
                                           'Compiled scripts' if kind == 'script' else 'Whole files'))
        for t in sorted(tiles, key=S.tile_sort_key):
            self.conflicts.append(Conflict('tile', t, sorted(tiles[t]), f'Map {t}', 'Maps'))

    def _unit_conflicts(self, kind, low, per_mod):
        """per_mod: {mod idx: {unit key: hash}}; rules in modscan.unit_clashes"""
        clash = S.unit_clashes(kind, per_mod)
        groups = {'par': 'Parameters', 'qtx': 'Quest file', 'lan': 'Texts', 'tree': 'Dialog trees'}
        for k in sorted(clash):
            key = k if kind in ('par', 'qtx') else (low, k)
            self.conflicts.append(Conflict(kind, key, clash[k], self._label(kind, k, low), groups[kind]))

    @staticmethod
    def _label(kind, k, low):
        if kind == 'par':
            li, name, fi = S.par_key(k)
            what = {'*': 'whole entry', '-': 'entry removed'}.get(fi)
            if what is None:
                sdk = fieldnames.field_name(name, int(fi))
                what = f'{sdk} (field {fi})' if sdk else f'field {fi}'
            return f'{name} - {what}'
        if kind == 'qtx':
            return k
        if kind == 'tree':
            return (f'dialog {k}' if k != '*' else 'all dialog trees') + f'  ({os.path.basename(low)})'
        if k.startswith(S.ALIAS):
            return f'alias {k[len(S.ALIAS):]}  ({os.path.basename(low)})'
        return f'{k}  ({os.path.basename(low)})'

    # -- levels -------------------------------------------------------------

    @property
    def soft(self):
        return sum(1 for c in self.conflicts if c.kind in ('par', 'qtx', 'lan', 'tree'))

    @property
    def level(self):
        if any(c.kind == 'script' for c in self.conflicts):
            return 'red'                      # only scripts force a main mod
        return 'yellow' if self.conflicts else 'green'

    # -- values for the dialog ---------------------------------------------

    def par_of(self, i):
        if i not in self._pars:
            self._pars[i] = S.read_par(self.archives[i].get(S.PAR))
        return self._pars[i]

    def qtx_of(self, i):
        if i not in self._qtx:
            raw = self.archives[i].get(S.QTX)
            self._qtx[i] = S.qtx_units(raw.decode('latin-1')) + (NL if b'\r\n' not in raw else '\r\n',)
        return self._qtx[i]

    def lan_of(self, i, low):
        key = (i, low)
        if key not in self._lans:
            tr, aliases, rest = tw1_lan.read(self.archives[i].get(low))
            try:
                trees = tw1_lan.parse_trees(rest)
            except Exception:
                trees = None
            self._lans[key] = (tr, aliases, rest, trees)
        return self._lans[key]

    def game_value(self, c, limit=160):
        """What the game itself has at the place of conflict ``c`` ('' if nothing)."""
        try:
            if c.kind == 'par':
                if 'gpar' not in self._pars:
                    raw = self.retail.get(S.PAR)
                    self._pars['gpar'] = S.read_par(raw) if raw else None
                return self._par_text(self._pars['gpar'], c, limit) if self._pars['gpar'] else ''
            if c.kind == 'qtx':
                return (self.retail.qtx().get(c.key) or '')[:limit * 4]
            if c.kind == 'lan':
                low, k = c.key
                return (self.retail.lan(low)[0].get(k) or '')[:limit * 3]
        except Exception:
            pass
        return ''

    @staticmethod
    def _par_text(par, c, limit):
        li, name, fi = S.par_key(c.key)
        ent = next((e for e in par.lists[int(li)].entries if e.name == name), None)
        if ent is None:
            return ''
        if fi.isdigit():
            return repr(ent.fields[int(fi)].value)[:limit]
        return f'{len(ent.fields)} fields: ' + ', '.join(repr(f.value) for f in ent.fields[:8])[:limit]

    def value(self, c, i, limit=160):
        """What mod ``i`` has for conflict ``c`` (short text for the dialog)."""
        try:
            if c.kind == 'par':
                li, name, fi = S.par_key(c.key)
                ent = next((e for e in self.par_of(i).lists[int(li)].entries if e.name == name), None)
                if ent is None:
                    return '(entry removed)' if name else ''
                if fi.isdigit():
                    return repr(ent.fields[int(fi)].value)[:limit]
                return f'{len(ent.fields)} fields: ' + ', '.join(repr(f.value) for f in ent.fields[:8])[:limit]
            if c.kind == 'qtx':
                return (self.qtx_of(i)[1].get(c.key) or '(removed)')[:limit * 4]
            if c.kind == 'lan':
                low, k = c.key
                tr, aliases = self.lan_of(i, low)[:2]
                if k.startswith(S.ALIAS):
                    return dict(aliases).get(k[len(S.ALIAS):], '')[:limit]
                return (tr.get(k) or '')[:limit * 3]
            if c.kind == 'tree':
                low, k = c.key
                trees = self.lan_of(i, low)[3] or []
                t = next((t for t in trees if str(t.id) == k), None)
                return f'{len(t.entries)} lines' if t is not None else '(whole tree section)'
            if c.kind == 'tile':
                a = self.archives[i]
                parts = []
                for low, e in a.by_low.items():
                    if S.tile_of(low) == c.key:
                        parts.append(f"{os.path.splitext(low)[1]} {e['rlen'] // 1024} KB")
                        if low.endswith('.lnd'):
                            n = sum(len(v) for v in tw1_lnd.markers_full(a.data(e)).values())
                            parts.append(f'{n} markers')
                return ', '.join(parts)
            e = self.archives[i].by_low[c.key]
            return f"{e['rlen']} bytes"
        except Exception as ex:                      # a value is a comfort
            return f'({type(ex).__name__})'

    # -- building -------------------------------------------------------------

    def _winner(self, c_kind, key, decisions, main, providers):
        pick = decisions.get((c_kind, key))
        if pick is None:
            pick = main if main in providers else providers[0]
        return pick

    def build(self, out_path, decisions=None, main=None, carry_markers=True, progress=None):
        """Write the merged archive. ``decisions``: {conflict.cid: mod idx};
        every conflict without one goes to ``main`` (or the first mod that
        has it). Returns the report lines."""
        decisions = dict(decisions or {})
        if os.path.exists(out_path):
            raise FileExistsError(f'{os.path.basename(out_path)} exists already - choose another name')
        rep = [f'Merged mod {os.path.basename(out_path)}', time.strftime('%Y-%m-%d %H:%M'),
               'Sources (read only): ' + ', '.join(self.names)]
        if main is not None:
            rep.append(f'Main mod: {self.names[main]} - wins every conflict not decided one by one')
        rep.append(f'Level: {self.level}, {len(self.conflicts)} conflict(s), of them {self.soft} inside files')
        clash = {c.cid: c for c in self.conflicts}
        files = []                                  # (entry meta, packed blob)
        tiles_done = {}
        lows = sorted(self.all_lows)
        for n, low in enumerate(lows):
            if progress and n % 25 == 0:
                progress(n, len(lows))
            if low == tw1_lhc.INNER.lower():
                continue
            have = self.all_lows[low]
            prov = self._providers(low) or have[:1]
            if low in self.fine and low in self.fine_ok:
                meta, blob, lines = self._merge_fine(low, decisions, main)
                files.append((meta, blob))
                rep += lines
                continue
            t = S.tile_of(low) or S.tile_follower(low)
            if t and ('tile', t) in clash:
                c = clash[('tile', t)]
                w = self._winner('tile', t, decisions, main, c.mods)
                if t not in tiles_done:
                    tiles_done[t] = w
                    rep.append(f'map {t}: from {self.names[w]}')
                    mine = S.tile_files(self.infos[w]).get(t) or set()
                    if not any(x.endswith('.lnd') for x in mine):
                        rep.append(f"  {self.names[w]} ships no .lnd for {t}: the map of the game stays, with this mod's physics")
                    if not any(x.endswith('.phx') for x in mine):
                        rep.append(f"  {self.names[w]} ships no .phx for {t}: the physics of the game stays")
                if w not in have and S.tile_follower(low):
                    rep.append(f"  {self.all_paths.get(low, low)}: left out - it shows the map of a mod that lost {t}")
                if w in have:
                    e = self.archives[w].by_low[low]
                    blob = self.archives[w].raw(e)
                    if low.endswith('.lnd') and carry_markers:
                        e, blob, lines = self._carry(t, low, w, [i for i in c.mods if i != w])
                        rep += lines
                    files.append((e, blob))
                continue
            w = prov[0]
            if ('file', low) in clash or ('script', low) in clash:
                c = clash.get(('file', low)) or clash[('script', low)]
                w = self._winner(c.kind, low, decisions, main, c.mods)
                rep.append(f"{c.label}: from {self.names[w]}")
            e = self.archives[w].by_low[low]
            files.append((e, self.archives[w].raw(e)))
        maps = {e['path']: (zlib.decompress(b) if e['flags'] & 1 else b)
                for e, b in files if tw1_lhc.is_map(e['path'])}
        if maps:
            files.append(self._cache_entry(maps))
            rep.append(f'level header cache: built new for {len(maps)} map(s) of this mod')
            for inner in getattr(self, '_cache_skipped', []):
                rep.append(f'  {inner}: not readable as a map - left out of the cache, the game keeps its own markers')
        files.sort(key=lambda eb: eb[0]['path'].lower())
        write_wd(out_path, files)
        rep.append(f'{len(files)} files, {os.path.getsize(out_path)} bytes')
        rep.append('')
        rep.append('Experimental. The tool cannot test whether the game starts with this mod.')
        if progress:
            progress(len(lows), len(lows))
        return rep

    def _cache_entry(self, maps):
        records = {}
        for wd in levelcache.RETAIL:
            p = os.path.join(self.retail.game_dir, 'WDFiles', wd)
            if os.path.isfile(p):
                records.update({k: v for k, v in levelcache.map_headers(p).items() if v is not None})
        canon = {k.lower(): k for k in records}
        self._cache_skipped = []
        for inner, body in maps.items():
            try:
                records[canon.get(inner.lower(), inner)] = tw1_lhc.header_of(body)
            except Exception:
                self._cache_skipped.append(inner)
        out = [tw1_lhc.MAGIC, struct.pack('<II', len(records), 0)]
        for inner in sorted(records):
            raw = inner.encode('latin-1', 'replace')
            out.append(struct.pack('<I', len(raw)) + raw + records[inner])
        blob = b''.join(out)
        return ({'path': tw1_lhc.INNER, 'flags': 0x01, 'rlen': len(blob), 'res': None, 'id': None,
                 'guid': None}, zlib.compress(blob, 6))

    # .. unit by unit ...........................................................

    def _merge_fine(self, low, decisions, main):
        prov = self.fine[low]
        base_i = main if main in prov else prov[0]
        others = [i for i in prov if i != base_i]
        e = dict(self.archives[base_i].by_low[low])
        lines = [f"{e['path']}: base {self.names[base_i]}, changes of "
                 + ', '.join(self.names[i] for i in others)]
        if low == S.PAR:
            data, n = self._merge_par(base_i, others, decisions, main)
        elif low == S.QTX:
            data, n = self._merge_qtx(base_i, others, decisions, main)
        else:
            data, n = self._merge_lan(low, base_i, others, decisions, main)
        for i in others:
            lines.append(f'  {self.names[i]}: {n.get(i, 0)} change(s) taken, {n.get((i, "lost"), 0)} lost to a conflict')
        e['rlen'] = len(data)
        e['flags'] |= 0x01
        return e, zlib.compress(data, 6), lines

    def _owner(self, kind, key, i, decisions, main, base_i):
        """True when mod ``i`` may write unit ``key``."""
        c = self._clash_index().get((kind, key))
        if c is None:
            return True
        return self._winner(kind, key, decisions, main if main is not None else base_i, c.mods) == i

    def _clash_index(self):
        if not hasattr(self, '_ci'):
            self._ci = {c.cid: c for c in self.conflicts}
        return self._ci

    def _merge_par(self, base_i, others, decisions, main):
        # a fresh parse instead of deepcopy: 0.5 s against 4 s for 5155 entries
        par = self._pars.pop(base_i, None) or S.read_par(self.archives[base_i].get(S.PAR))
        index = [{e.name: e for e in reversed(pl.entries)} for pl in par.lists]
        taken = {}
        whole_done = set()
        for i in others:
            src = self.par_of(i)
            src_index = [{e.name: e for e in reversed(pl.entries)} for pl in src.lists]
            for key in self.infos[i]['par']:
                li, name, fi = S.par_key(key)
                ent_key = f'{li}|{name}|*'
                ck = ent_key if ('par', ent_key) in self._clash_index() else key
                if not self._owner('par', ck, i, decisions, main, base_i):
                    taken[(i, 'lost')] = taken.get((i, 'lost'), 0) + 1
                    continue
                li_n = int(li)
                if ('par', ent_key) in self._clash_index():
                    # the dialog said "this mod wins the whole entry": its entry
                    # as it is, once - not a blend with the base mod's fields
                    if (i, ent_key) in whole_done:
                        continue
                    whole_done.add((i, ent_key))
                    fi = '*' if src_index[li_n].get(name) is not None else '-'
                lst = par.lists[li_n].entries
                mine = index[li_n].get(name)
                theirs_list = src.lists[li_n].entries
                theirs = src_index[li_n].get(name)
                if fi == '-':
                    if mine is not None:
                        lst.remove(mine)
                        del index[li_n][name]
                elif theirs is None:
                    continue
                elif fi == '*' or mine is None or len(mine.fields) != len(theirs.fields):
                    new = copy.deepcopy(theirs)
                    index[li_n][name] = new
                    if mine is not None:
                        lst[lst.index(mine)] = new
                    else:
                        # keep the mod's place in the list: after the same neighbour
                        pos = theirs_list.index(theirs)
                        at = len(lst)
                        for prev in reversed(theirs_list[:pos]):
                            hit = index[li_n].get(prev.name)
                            if hit is not None:
                                at = lst.index(hit) + 1
                                break
                        lst.insert(at, new)
                else:
                    f = theirs.fields[int(fi)]
                    v = list(f.value) if isinstance(f.value, list) else f.value
                    mine.fields[int(fi)] = tw1_par.ParField(f.dtype, v)
                taken[i] = taken.get(i, 0) + 1
        return tw1_par.write_par(par), taken

    def _merge_qtx(self, base_i, others, decisions, main):
        order, units, nl = self.qtx_of(base_i)
        order, units = list(order), dict(units)
        taken = {}
        for i in others:
            o_order, o_units, _nl = self.qtx_of(i)
            for key, how in self.infos[i]['qtx'].items():
                if not self._owner('qtx', key, i, decisions, main, base_i):
                    taken[(i, 'lost')] = taken.get((i, 'lost'), 0) + 1
                    continue
                if how == S.GONE:
                    if key in units:
                        del units[key]
                        order.remove(key)
                elif key in units:
                    units[key] = o_units[key]
                else:
                    at = 0 if key == S.HEAD else len(order)
                    pos = o_order.index(key)
                    for prev in reversed(o_order[:pos]):
                        if prev in units:
                            at = order.index(prev) + 1
                            break
                    order.insert(at, key)
                    units[key] = o_units[key]
                taken[i] = taken.get(i, 0) + 1
        text = S.qtx_text(order, units)
        if nl != NL:
            text = text.replace(NL, nl)
        return text.encode('latin-1'), taken

    def _merge_lan(self, low, base_i, others, decisions, main):
        tr, aliases, rest, trees = self.lan_of(base_i, low)
        tr = dict(tr)
        aliases = list(aliases)
        trees = list(trees) if trees is not None else None
        taken = {}
        whole = ('tree', (low, '*')) in self._clash_index()
        for i in others:
            o_tr, o_al, o_rest, o_trees = self.lan_of(i, low)
            o_alias = dict(o_al)
            for k in self.infos[i]['lan'][low]:
                if not self._owner('lan', (low, k), i, decisions, main, base_i):
                    taken[(i, 'lost')] = taken.get((i, 'lost'), 0) + 1
                    continue
                if self.infos[i]['lan'][low][k] == S.GONE:
                    if k.startswith(S.ALIAS):
                        aliases = [x for x in aliases if x[0] != k[len(S.ALIAS):]]
                    else:
                        tr.pop(k, None)
                elif k.startswith(S.ALIAS):
                    name = k[len(S.ALIAS):]
                    hit = next((n for n, (ak, _v) in enumerate(aliases) if ak == name), None)
                    if hit is None:
                        aliases.append((name, o_alias[name]))
                    else:
                        aliases[hit] = (name, o_alias[name])
                else:
                    tr[k] = o_tr[k]
                taken[i] = taken.get(i, 0) + 1
            changed = self.infos[i]['trees'].get(low) or {}
            if changed and (whole or trees is None or o_trees is None):
                # a tree section that cannot be read tree by tree goes as a whole
                if self._owner('tree', (low, '*'), i, decisions, main, base_i):
                    rest, trees = o_rest, (list(o_trees) if o_trees is not None else None)
                    taken[i] = taken.get(i, 0) + 1
                else:
                    taken[(i, 'lost')] = taken.get((i, 'lost'), 0) + 1
                continue
            for k in changed:
                if not self._owner('tree', (low, k), i, decisions, main, base_i):
                    taken[(i, 'lost')] = taken.get((i, 'lost'), 0) + 1
                    continue
                new = next((t for t in o_trees if str(t.id) == k), None)
                if new is None:
                    continue
                hit = next((n for n, t in enumerate(trees) if str(t.id) == k), None)
                if hit is None:
                    trees.append(new)
                else:
                    trees[hit] = new
                taken[i] = taken.get(i, 0) + 1
        rest_out = tw1_lan.build_trees(trees) if trees is not None else rest
        return tw1_lan.build(tr, aliases, rest_out), taken

    # .. markers ................................................................

    def _carry(self, tile, low, w, losers):
        """The chosen tile with the quest markers the other mods added to theirs.

        Only marker kinds quests address by number (quest_marker): furniture
        and spawn points belong to the ground they were made for, and a mod
        that removed game spawns did so on purpose.
        Height: the marker keeps its distance to the ground - a chest in a
        cellar stays below, one on a bridge stays above (z + difference of
        the two grounds at that spot)."""
        a = self.archives[w]
        e = dict(a.by_low[low])
        raw = a.raw(e)
        lines = []
        try:
            body = tw1_lnd.unwrap(a.data(a.by_low[low]))
            retail_raw = self.retail.get(low)
            retail = tw1_lnd.markers_full(retail_raw) if retail_raw else {}
            where = {(n, i): p[:3] for n, lst in tw1_lnd.markers_full(body).items() for i, p in lst}
            have = set(where)
            game_at = {(n, i): p[:3] for n, lst in retail.items() for i, p in lst}
            add, note = [], []
            terr = self._terrain(body)
            rterr = self._terrain(tw1_lnd.unwrap(retail_raw)) if (terr and retail_raw) else None

            def onto(z, x, y, other):
                if not (terr and other):
                    return z
                d = terr.height(x, y) - other.height(x, y)
                return z + d if abs(d) > 16 else z

            for n, lst in retail.items():                 # quest markers of the game the tile lost
                if not quest_marker(n):
                    continue
                for ident, (x, y, z, ang) in lst:
                    if (n, ident) not in have:
                        nz = onto(z, x, y, rterr)
                        add.append((n, ident, x, y, nz, ang))
                        have.add((n, ident))
                        note.append(f'{n} {ident} of the game put back' + (f' (height {z} -> {nz})' if nz != z else ''))
            lost = len(add)
            for i in losers:
                oe = self.archives[i].by_low.get(low)
                if oe is None:
                    continue
                obody = tw1_lnd.unwrap(self.archives[i].data(oe))
                oterr = self._terrain(obody) if terr else None
                for n, lst in tw1_lnd.markers_full(obody).items():
                    if not quest_marker(n):
                        continue
                    for ident, (x, y, z, ang) in lst:
                        key = (n, ident)
                        if key in have:
                            at = where.get(key)
                            moved = key not in game_at or max(abs(game_at[key][0] - x), abs(game_at[key][1] - y)) > 64
                            if at and moved and max(abs(at[0] - x), abs(at[1] - y)) > 64:
                                note.append(f'{n} {ident}: {self.names[i]} has it at another place - it stays where '
                                            f'the chosen map has it, quests of {self.names[i]} using it happen there')
                            continue
                        nz = onto(z, x, y, oterr)
                        if terr and terr.passable(x, y) is False:
                            note.append(f'{n} {ident} stands on blocked ground')
                        add.append((n, ident, x, y, nz, ang))
                        have.add(key)
                        note.append(f'{n} {ident} from {self.names[i]}' + (f' (height {z} -> {nz})' if nz != z else ''))
            warn = [x for x in note if 'another place' in x or 'blocked' in x]
            rest = [x for x in note if x not in warn]
            shown = warn + rest[:max(0, 60 - len(warn))]
            more = len(note) - len(shown)
            if not add:
                if note:
                    lines.append(f'  map {tile}:')
                    lines += ['    ' + x for x in shown]
                return e, raw, lines
            body = lndmap.add_markers(body, add)
            lines.append(f'  map {tile}: {len(add) - lost} quest marker(s) carried over, {lost} of the game put back'
                         + ('' if terr else ' - heights kept as they were (no readable ground, interior?)'))
            lines += ['    ' + x for x in shown] + ([f'    ... and {more} more'] if more > 0 else [])
            e['rlen'] = len(body)
            e['flags'] |= 0x01
            return e, zlib.compress(body, 3), lines
        except Exception as ex:
            lines.append(f'  map {tile}: markers not carried over ({type(ex).__name__}: {ex})')
            return e, raw, lines

    @staticmethod
    def _terrain(body):
        try:
            t = lndmap.Terrain(body)
            return t if t.hw and t.hh else None
        except Exception:
            return None


QUEST_MARKERS = ('MARKER_QUEST', 'MARKER_GATE', 'MARKER_CHEST', 'MARKER_TELEPORT')


def quest_marker(name):
    """Marker kinds the quest file and the scripts address by number."""
    return name.startswith(QUEST_MARKERS)


def write_wd(out_path, files):
    """files: [(entry meta dict, packed blob)] -> a new WD 0x200 archive."""
    head = zlib.compress(WD_MAGIC + os.urandom(16))
    tab = bytearray(struct.pack('<QH', int(time.time() * 10000000) + FILETIME_EPOCH, len(files)))
    offset = len(head)
    tmp = f'{out_path}.{os.getpid()}.mmtmp'
    try:
        with open(tmp, 'wb') as f:
            f.write(head)
            for e, blob in files:
                name = e['path'].encode('latin-1')
                flags = e['flags']
                tab += bytes([len(name)]) + name + struct.pack('<BIII', flags, offset, len(blob), e['rlen'])
                if flags & 0x08:
                    tab += bytes([len(e['res'])]) + e['res']
                if flags & 0x10:
                    tab += struct.pack('<I', e['id'])
                if flags & 0x20:
                    tab += e['guid']
                f.write(blob)
                offset += len(blob)
            cdir = zlib.compress(bytes(tab))
            f.write(cdir)
            f.write(struct.pack('<I', len(cdir) + 4))
        os.replace(tmp, out_path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
