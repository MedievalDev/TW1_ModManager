"""What a mod changes, and how well two mods go together.

A ``.wd`` overrides game files by path. Two mods that ship the same path
collide on the file level - but inside three file types the changes can be
told apart and therefore merged:

- ``Parameters\\TwoWorlds.par``: per field of an entry (list index, entry name,
  field index); an entry the game does not have counts as one unit
- ``Scripts\\Quests\\TwoWorldsQuests.qtx``: per block (QUEST, NPC, CONTAINER ...
  up to END) and per LOCATION line, key = first two words
- ``Language\\*.lan``: per translation key and per dialog tree

Everything is compared against the game's own file (WDFiles, later archive
wins), so "changed" means changed by the mod, not merely shipped. Maps
(``Levels\\Map_X.lnd`` with ``Levels\\physic\\Map_X.phx``) count per tile, every
other file as a whole. ``Map_LevelHeaders.lhc`` never counts: it is rebuilt.

Levels (Marco 2026-09-19, limit removed 2026-09-22): green = no overlap
inside files; yellow = overlaps inside files, tiles or whole files - each one
is asked, however many there are; red = compiled scripts (.eco) both mods
change. Scripts cannot be mixed, so there one mod has to be the main mod and
keeps its scripts; every other overlap is still asked.

The scan of one archive is cached on disk by (mtime, size, format).
"""

import hashlib
import json
import os
import re
import struct
import zlib

import tw1_lan
import tw1_lhc
import tw1_lnd
import tw1_par

FORMAT = 6
BS = chr(92)
PAR = 'parameters\\twoworlds.par'
QTX = 'scripts\\quests\\twoworldsquests.qtx'
RETAIL_ORDER = ('Scripts.wd', 'Levels.wd', 'Parameters.wd', 'Graphics.wd',
                'Update11-15.wd', 'Update16.wd')
_TILE = re.compile(r'^levels\\(?:physic\\)?map_([a-z])(\d{2})(_\d+)?\.(lnd|phx)$')
_LEVEL = re.compile(r'^levels\\(?:physic\\)?([^\\]+)\.(lnd|phx)$')

KINDS = (('quests', ('.qtx',)), ('texts', ('.lan',)), ('par', ('.par',)),
         ('scripts', ('.eco', '.ec', '.ech')), ('maps', ('.lnd',)), ('physics', ('.phx',)),
         ('level cache', ('.lhc',)), ('meshes', ('.vdf', '.msh')), ('textures', ('.dds', '.tga')),
         ('materials', ('.mtr',)), ('sounds', ('.wav', '.ogg', '.xwb', '.xsb', '.xgs')),
         ('images', ('.bmp', '.png', '.jpg')), ('console', ('.con',)),
         ('animations', ('.ani', '.anm')), ('text files', ('.txt', '.rc', '.ini')),
         ('characters', ('.chv',)), ('archives', ('.zip', '.wd', '.exe', '.rar', '.7z')))


def kind_of(inner):
    low = inner.lower()
    if low.startswith('levels\\mipmaps\\'):
        return 'minimap'
    ext = os.path.splitext(low)[1]
    for name, exts in KINDS:
        if ext in exts:
            return name
    return ext.lstrip('.') or 'other'


def tile_of(inner_lower):
    m = _TILE.match(inner_lower)
    if m:
        return f'{m.group(1).upper()}{int(m.group(2))}{m.group(3) or ""}'
    m = _LEVEL.match(inner_lower)          # Net_*.lnd and other levels: the stem
    return m.group(1) if m else None


_FOLLOW = re.compile(r'^levels\\(?:mipmaps\\)?map_([a-z])(\d{2})(_\d+)?(?:@\d)?\.(?:dds|bmp)$')


def tile_follower(inner_lower):
    """The tile a minimap level or editor image belongs to (they show that
    tile's map, so they go with whoever wins the tile); None otherwise."""
    m = _FOLLOW.match(inner_lower)
    return f'{m.group(1).upper()}{int(m.group(2))}{m.group(3) or ""}' if m else None


def tile_sort_key(t):
    m = re.match(r'^([A-Z])(\d+)(_\d+)?$', t)
    return (0, m.group(1), int(m.group(2)), m.group(3) or '') if m else (1, t, 0, '')


def _h(data):
    if isinstance(data, str):
        data = data.encode('utf-8', 'replace')
    return hashlib.sha1(data).hexdigest()[:16]


# ---------------------------------------------------------------------------
# archives, read lazily

class Archive:
    """Directory of a WD 0x200 archive; file data only on demand."""

    def __init__(self, path):
        try:
            self._read(path)
        except (zlib.error, struct.error, IndexError, UnicodeError) as e:
            raise ValueError(f'not a readable Two Worlds 1 archive ({type(e).__name__})') from None

    def _read(self, path):
        self.path = path
        self.name = os.path.basename(path)
        st = os.stat(path)
        self.stamp = (st.st_mtime_ns, st.st_size)
        self.entries = []
        with open(path, 'rb') as f:
            head = f.read(64)
            d = zlib.decompressobj()
            magic = d.decompress(head)
            if magic[:8] != bytes([0xFF, 0xA1, 0xD0, 0x31, 0x57, 0x44, 0x00, 0x02]):
                raise ValueError('not a Two Worlds 1 archive')
            self.head_len = len(head) - len(d.unused_data)
            self.head = head[:self.head_len]
            size = f.seek(0, 2)
            f.seek(-4, 2)
            n = struct.unpack('<I', f.read(4))[0]
            if not 4 < n <= size - self.head_len:
                raise ValueError('not a readable Two Worlds 1 archive (directory size)')
            f.seek(-n, 2)
            t = zlib.decompressobj().decompress(f.read(n - 4))
        self.filetime = struct.unpack_from('<Q', t, 0)[0]
        off, count = 10, struct.unpack_from('<H', t, 8)[0]
        for _ in range(count):
            ln = t[off]; off += 1
            name = t[off:off + ln].decode('latin-1'); off += ln
            flags, foff, clen, rlen = struct.unpack_from('<BIII', t, off); off += 13
            res = kid = guid = None
            if flags & 0x08:
                xl = t[off]; off += 1
                res = t[off:off + xl]; off += xl
            if flags & 0x10:
                kid = struct.unpack_from('<I', t, off)[0]; off += 4
            if flags & 0x20:
                guid = t[off:off + 16]; off += 16
            self.entries.append({'path': name, 'low': name.lower(), 'flags': flags, 'offset': foff,
                                 'clen': clen, 'rlen': rlen, 'res': res, 'id': kid, 'guid': guid})
        for e in self.entries:
            if e['offset'] + e['clen'] > size:
                raise ValueError(f"not a readable Two Worlds 1 archive ({e['path']} lies outside the file)")
        self.by_low = {e['low']: e for e in self.entries}

    def raw(self, e):
        with open(self.path, 'rb') as f:
            f.seek(e['offset'])
            return f.read(e['clen'])

    def data(self, e):
        blob = self.raw(e)
        return zlib.decompressobj().decompress(blob) if e['flags'] & 1 else blob

    def get(self, low):
        e = self.by_low.get(low)
        return self.data(e) if e else None


_archives = {}


def archive(path):
    st = os.stat(path)
    key = (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size)
    a = _archives.get(key)
    if a is None:
        if len(_archives) > 60:
            _archives.clear()
        a = _archives[key] = Archive(path)
    return a


# ---------------------------------------------------------------------------
# the game's own files

class Retail:
    """The game's file for a path: WDFiles, later archive wins."""

    def __init__(self, game_dir):
        self.game_dir = game_dir
        self.index = {}
        self._cache = {}
        wd = os.path.join(game_dir or '', 'WDFiles')
        names = [n for n in (os.listdir(wd) if os.path.isdir(wd) else ()) if n.lower().endswith('.wd')]
        order = [n for n in RETAIL_ORDER if n in names] + sorted(n for n in names if n not in RETAIL_ORDER)
        self.stamp = []
        for n in order:
            p = os.path.join(wd, n)
            try:
                a = archive(p)
            except (OSError, ValueError, zlib.error, struct.error):
                continue
            st = os.stat(p)
            self.stamp.append([n, int(st.st_mtime), st.st_size])
            for e in a.entries:
                self.index[e['low']] = (a, e)
        self.stamp = _h(json.dumps(self.stamp))

    def get(self, low):
        if low not in self._cache:
            hit = self.index.get(low)
            self._cache[low] = hit[0].data(hit[1]) if hit else None
        return self._cache[low]

    # parsed forms, made once
    def par(self):
        if 'par' not in self._cache:
            raw = self.get(PAR)
            self._cache['par'] = par_units(read_par(raw)) if raw else None
        return self._cache['par']

    def qtx(self):
        if 'qtx' not in self._cache:
            raw = self.get(QTX)
            self._cache['qtx'] = qtx_units(raw.decode('latin-1'))[1] if raw else {}
        return self._cache['qtx']

    def lan(self, low):
        key = ('lan', low)
        if key not in self._cache:
            raw = self.get(low)
            self._cache[key] = lan_units(raw) if raw else ({}, {})
        return self._cache[key]


# ---------------------------------------------------------------------------
# units inside the mergeable files

def read_par(raw):
    """Bare PAR stream (as inside archives) or a zlib file of one or two streams."""
    if raw[:4] != tw1_par.PAR_MAGIC and raw[:1] == b'x':
        d = zlib.decompressobj()
        first = d.decompress(raw)
        raw = first if first[:4] == tw1_par.PAR_MAGIC else zlib.decompress(d.unused_data)
    return tw1_par.read_par(raw)


def _field_repr(f):
    v = f.value
    if f.dtype == tw1_par.TYPE_FLOAT32:
        v = struct.pack('<f', v).hex()
    elif f.dtype == tw1_par.TYPE_ARRAY_FLOAT:
        v = [struct.pack('<f', x).hex() for x in (v or [])]
    return f'{f.dtype}:{v!r}'


def par_units(par):
    """{'lists': n, 'entries': {(li, name): [field repr, ...]}, 'dups': n}"""
    out, dups = {}, 0
    for li, pl in enumerate(par.lists):
        for e in pl.entries:
            key = (li, e.name)
            if key in out:
                dups += 1
                continue
            out[key] = [_field_repr(f) for f in e.fields]
    return {'lists': len(par.lists), 'entries': out, 'dups': dups}


def par_diff(mod, base):
    """{unit key: hash}. Unit key 'li|name|fi' for a field, 'li|name|*' for
    an entry the game lacks or whose field count differs, 'li|name|-' for an
    entry the mod removes."""
    out = {}
    be = base['entries']
    for key, fields in mod['entries'].items():
        old = be.get(key)
        tag = f'{key[0]}|{key[1]}|'
        if old is None or len(old) != len(fields):
            out[tag + '*'] = _h(repr(fields))
            continue
        if old != fields:
            for fi, (a, b) in enumerate(zip(old, fields)):
                if a != b:
                    out[tag + str(fi)] = _h(b)
    for key in be:
        if key not in mod['entries']:
            out[f'{key[0]}|{key[1]}|-'] = 'gone'
    return out


_UNIT_START = re.compile(r'^(?=\S)', re.M)


def qtx_units(text):
    """(order [key], {key: unit text}). A unit starts at a line that begins
    in column 0 and is not END; key = its first two words."""
    text = text.replace('\r\n', '\n')
    order, units, cur, key = [], {}, [], HEAD

    def flush():
        if key == HEAD and not cur:
            return
        k, n = key, 2
        while k in units:                 # same key twice: keep both
            k = f'{key}#{n}'
            n += 1
        units[k] = '\n'.join(cur)          # with its blank lines: joined again the file is the same
        order.append(k)
    for ln in text.split('\n'):
        if ln[:1].strip() and not ln.startswith('END'):
            flush()
            cur = [ln]
            key = ' '.join(ln.split()[:2])
        else:
            cur.append(ln)
    flush()
    return order, units


def qtx_text(order, units):
    text = '\n'.join(units[k] for k in order)
    return text if text.endswith('\n') else text + '\n'


def looks_like_qtx(units):
    return any(k.startswith(('QUEST ', 'NPC ', 'LOCATION ', 'CONTAINER ')) for k in units)


GONE = 'gone'
HEAD = '(file head)'


def qtx_diff(mod_units, base_units):
    # blank lines around a block are layout, not a change
    out = {k: _h(v.rstrip()) for k, v in mod_units.items() if (base_units.get(k) or '').rstrip() != v.rstrip()
           or k not in base_units}
    out.update({k: GONE for k in base_units if k not in mod_units})     # same key as a change: they clash
    return out


ALIAS = '@alias '


def lan_units(raw):
    """({key: text}, {tree id: hash}) of one .lan; unreadable trees -> {}"""
    tr, aliases, rest = tw1_lan.read(raw)
    tr = dict(tr)
    for k, v in aliases:
        tr[ALIAS + k] = v
    trees = {}
    try:
        for t in tw1_lan.parse_trees(rest):
            trees[str(t.id)] = _h(tw1_lan.build_trees([t]))
    except Exception:
        trees = {'*': _h(rest)} if rest else {}
    return tr, trees


# ---------------------------------------------------------------------------
# scan of one mod

def cache_dir(data_dir):
    d = os.path.join(data_dir, 'scan')
    os.makedirs(d, exist_ok=True)
    return d


def scan(path, retail, data_dir=None):
    """Summary of one mod, JSON-able (see module doc). Cached on disk."""
    st = os.stat(path)
    stamp = [int(st.st_mtime), st.st_size, FORMAT, retail.stamp]
    cfile = None
    if data_dir:
        cfile = os.path.join(cache_dir(data_dir), _h(os.path.normcase(os.path.abspath(path))) + '.json')
        try:
            with open(cfile, encoding='utf-8') as f:
                got = json.load(f)
            if got.get('stamp') == stamp:
                return got
        except (OSError, ValueError):
            pass
    info = {'stamp': stamp, 'name': os.path.basename(path), 'files': {}, 'kinds': {}, 'tiles': [],
            'par': None, 'qtx': None, 'lan': {}, 'trees': {}, 'same': [], 'error': None, 'notes': []}
    try:
        a = archive(path)
    except (OSError, ValueError, zlib.error, struct.error, IndexError) as e:
        info['error'] = str(e) or type(e).__name__
        return info
    tiles = set()
    for e in a.entries:
        low = e['low']
        k = kind_of(low)
        info['files'][low] = [e['path'], k, e['rlen']]
        info['kinds'][k] = info['kinds'].get(k, 0) + 1
        t = tile_of(low)
        if t:
            tiles.add(t)
    info['tiles'] = sorted(tiles)
    for e in a.entries:
        hit = retail.index.get(e['low'])
        if hit and hit[1]['rlen'] == e['rlen'] and e['low'] not in (PAR, QTX) and not e['low'].endswith('.lan'):
            try:
                if a.data(e) == hit[0].data(hit[1]):
                    info['same'].append(e['low'])
            except Exception:
                pass
    def guarded(what, fn):
        try:
            fn()
        except Exception as ex:                   # a broken file must not hide the rest
            info['notes'].append(f'{what}: not analysed ({type(ex).__name__}) - only usable as a whole')

    def do_par():
        if PAR in a.by_low:
            raw = a.get(PAR)
            if raw == retail.get(PAR):
                info['same'].append(PAR)
            else:
                base = retail.par()
                mine = par_units(read_par(raw))
                if base is None or mine['lists'] != base['lists']:
                    info['notes'].append('par: other list count than the game (%s) - only usable as a whole'
                                         % mine['lists'])
                else:
                    info['par'] = par_diff(mine, base)

    def do_qtx():
        if QTX in a.by_low:
            raw = a.get(QTX)
            if raw == retail.get(QTX):
                info['same'].append(QTX)
            else:
                units = qtx_units(raw.decode('latin-1'))[1]
                if not looks_like_qtx(units):
                    raise ValueError('no quest blocks')
                info['qtx'] = qtx_diff(units, retail.qtx())

    def do_lan():
        for e in a.entries:
            if e['low'].endswith('.lan'):
                raw = a.data(e)
                if raw == retail.get(e['low']):
                    info['same'].append(e['low'])
                    continue
                try:
                    tr, trees = lan_units(raw)
                except Exception as ex:
                    info['notes'].append(f"{e['path']}: unreadable ({ex})")
                    continue
                btr, btrees = retail.lan(e['low'])
                ch = {k: _h(v) for k, v in tr.items() if btr.get(k) != v}
                ch.update({k: GONE for k in btr if k not in tr})          # a removed key is a change too
                info['lan'][e['low']] = ch
                info['trees'][e['low']] = {k: v for k, v in trees.items() if btrees.get(k) != v}
    guarded('TwoWorlds.par', do_par)
    guarded('quest file', do_qtx)
    guarded('texts', do_lan)
    if cfile:
        try:
            tmp = cfile + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(info, f)
            os.replace(tmp, cfile)
        except OSError:
            pass
    return info


def _same(text):
    return text


def kinds_text(info, tr=_same, limit=9):
    kinds = sorted(info['kinds'].items(), key=lambda kv: (-kv[1], kv[0]))
    return ', '.join(f'{tr(k)} {n}' for k, n in kinds[:limit]) + (' ...' if len(kinds) > limit else '')


def summary_lines(info, limit=9, tr=_same):
    """Tooltip text: what the mod changes."""
    if info.get('error'):
        return [info['error']]
    out = [kinds_text(info, tr, limit)]
    if info.get('par') is not None:
        ch = info['par']
        fields = sum(1 for k in ch if k[-1].isdigit())
        new = sum(1 for k in ch if k.endswith('|*'))
        out.append(tr('par: {a} fields changed, {b} entries new or rebuilt').format(a=fields, b=new))
    if info.get('qtx') is not None:
        kinds2 = {}
        for k in info['qtx']:
            w = k.split()[0]
            kinds2[w] = kinds2.get(w, 0) + 1
        out.append(tr('quest file: ') + ', '.join(f'{n} {w}' for w, n in sorted(kinds2.items())))
    nkeys = sum(len(v) for v in info['lan'].values())
    ntrees = sum(len(v) for v in info['trees'].values())
    if nkeys or ntrees:
        out.append(tr('texts: {a} keys, {b} dialog trees').format(a=nkeys, b=ntrees))
    if info['tiles']:
        out.append(tr('maps: ') + ', '.join(info['tiles'][:14]) + (' ...' if len(info['tiles']) > 14 else ''))
    if info['same']:
        out.append(tr("{n} file(s) identical to the game's").format(n=len(info['same'])))
    out += info['notes'][:3]
    return out


# ---------------------------------------------------------------------------
# two mods against each other

_file_hash = {}


def file_hash(path, low):
    a = archive(path)
    e = a.by_low[low]
    key = (a.path, a.stamp, e['offset'], e['clen'])
    if key not in _file_hash:
        if len(_file_hash) > 4000:
            _file_hash.clear()
        _file_hash[key] = _h(a.data(e))
    return _file_hash[key]


def unit_clashes(kind, per_mod):
    """{unit key: [mods]} of units that several mods change differently.
    ``per_mod``: {mod: {unit key: hash}}. The one rule book for the colours
    (compare) and for the merger.

    par: an entry one mod rebuilds or removes ('|*', '|-') clashes with every
    change another mod makes inside that entry -> key 'li|name|*'.
    tree: a tree section that could not be parsed ('*') is one unit against
    everything another mod changes in it."""
    seen = {}
    if kind == 'tree' and any('*' in u for u in per_mod.values()):
        per_mod = {i: ({'*': _h(repr(sorted(u.items())))} if u else {}) for i, u in per_mod.items()}
    for i, units in per_mod.items():
        for k, h in (units or {}).items():
            seen.setdefault(k, {}).setdefault(h, []).append(i)
    clash = {k: sorted(i for lst in v.values() for i in lst) for k, v in seen.items() if len(v) > 1}
    if kind == 'par':
        by_entry = {}
        for k, v in seen.items():
            if k.endswith(('|*', '|-')):
                by_entry.setdefault(k.rsplit('|', 1)[0], None)
        if by_entry:
            for k, v in seen.items():
                ent = k.rsplit('|', 1)[0]
                if ent in by_entry:
                    by_entry[ent] = by_entry[ent] or {}
                    by_entry[ent][k] = v
            for ent, keys in by_entry.items():
                mods = sorted({i for v in keys.values() for lst in v.values() for i in lst})
                hashes = {h for v in keys.values() for h in v}
                if len(mods) > 1 and (len(keys) > 1 or len(hashes) > 1):
                    for k in keys:
                        clash.pop(k, None)
                    clash[ent + '|*'] = mods
    return clash


def _clash(a, b, kind='lan'):
    if not a or not b:
        return []
    return list(unit_clashes(kind, {0: a, 1: b}))


def par_key(k):
    """'li|name|fi' -> (li, name, fi); entry names may contain '|'."""
    li, rest = k.split('|', 1)
    name, fi = rest.rsplit('|', 1)
    return li, name, fi


def tile_files(info):
    """{tile: set of lower paths} of what one mod ships for each tile (not the game's own files)."""
    out = {}
    for low in info['files']:
        t = tile_of(low)
        if t and low not in info['same']:
            out.setdefault(t, set()).add(low)
    return out


def tile_clash(paths, infos, tile, files_per_mod):
    """True when the mods do not ship the very same files with the very same
    content for ``tile`` - then one mod has to be chosen for the pair."""
    sets = [f for f in files_per_mod if f]
    if len(sets) < 2:
        return False
    if any(f != sets[0] for f in sets):
        return True                        # one has only the .phx, another only the .lnd
    who = [i for i, f in enumerate(files_per_mod) if f]
    return any(len({file_hash(paths[i], low) for i in who}) > 1 for low in sets[0])


def compare(path_a, info_a, path_b, info_b):
    """{'level', 'soft': n, 'par': [...], 'qtx': [...], 'lan': [...],
    'trees': [...], 'tiles': [...], 'files': [...], 'scripts': [...]}"""
    out = {'level': 'green', 'par': [], 'qtx': [], 'lan': [], 'trees': [], 'tiles': [], 'files': [],
           'scripts': [], 'whole': []}
    if info_a.get('error') or info_b.get('error'):
        out['level'] = 'unknown'
        return out
    common = set(info_a['files']) & set(info_b['files'])
    ta, tb = tile_files(info_a), tile_files(info_b)
    tiles = {t for t in set(ta) & set(tb)
             if tile_clash((path_a, path_b), (info_a, info_b), t, [ta[t], tb[t]])}
    for low in sorted(common):
        if low == tw1_lhc.INNER.lower():
            continue
        if low == PAR and info_a['par'] is not None and info_b['par'] is not None:
            out['par'] = sorted(_clash(info_a['par'], info_b['par'], 'par'))
            continue
        if low == QTX and info_a['qtx'] is not None and info_b['qtx'] is not None:
            out['qtx'] = sorted(_clash(info_a['qtx'], info_b['qtx']))
            continue
        if low.endswith('.lan') and low in info_a['lan'] and low in info_b['lan']:
            out['lan'] += [(low, k) for k in sorted(_clash(info_a['lan'][low], info_b['lan'][low]))]
            out['trees'] += [(low, k) for k in sorted(_clash(info_a['trees'].get(low), info_b['trees'].get(low), 'tree'))]
            continue
        if low in info_a['same'] or low in info_b['same']:
            continue                          # one of them ships the game's own file
        if file_hash(path_a, low) == file_hash(path_b, low):
            continue
        if tile_of(low):
            continue                          # tiles are judged as pairs above
        if kind_of(low) == 'scripts':
            out['scripts'].append(info_a['files'][low][0])
        else:
            if low in (PAR, QTX) or low.endswith('.lan'):
                out['whole'].append(info_a['files'][low][0])
            out['files'].append(info_a['files'][low][0])
    out['tiles'] = sorted(tiles, key=tile_sort_key)
    soft = len(out['par']) + len(out['qtx']) + len(out['lan']) + len(out['trees'])
    out['soft'] = soft
    asks = soft + len(out['tiles']) + len(out['files'])
    if out['scripts']:                        # compiled scripts cannot be mixed
        out['level'] = 'red'
    elif asks:
        out['level'] = 'yellow'
    return out


def compare_lines(c, tr=_same):
    """Human text for one comparison."""
    if c['level'] == 'unknown':
        return [tr('not analysed')]
    if c['level'] == 'green':
        return [tr('no overlap inside files')]
    out = []
    if c['par']:
        out.append(tr('par: {n} fields changed by both').format(n=len(c['par'])))
    if c['qtx']:
        out.append(tr('quest file: {n} blocks changed by both').format(n=len(c['qtx'])))
    if c['lan'] or c['trees']:
        out.append(tr('texts: {a} keys, {b} dialog trees changed by both').format(a=len(c['lan']), b=len(c['trees'])))
    if c['tiles']:
        out.append(tr('maps in both: ') + ', '.join(c['tiles'][:12]) + (' ...' if len(c['tiles']) > 12 else ''))
    if c['files']:
        out.append(tr('{n} other file(s) in both: ').format(n=len(c['files']))
                   + ', '.join(os.path.basename(f) for f in c['files'][:4]))
    if c['scripts']:
        out.append(tr('compiled scripts in both (cannot be merged): ')
                   + ', '.join(os.path.basename(f) for f in c['scripts'][:4]))
    return out
