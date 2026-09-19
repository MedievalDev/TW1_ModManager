"""Keeps ``<game>\\Levels\\Map_LevelHeaders.lhc`` in step with the enabled mods.

The game reads the markers of every map from that cache. A mod that brings
maps needs a cache that knows them, and a cache built while a map mod was
installed keeps that mod's maps after the mod is gone (README of the Quest
Creator, 3.3: broken ground textures on those tiles). The SDK answer is to
run LevelHeadersCacheGen.bat by hand after every change. This module does it
after every change of the mod list, without the SDK (tw1_lhc.py writes the
same bytes; measured 2026-09-19: byte-identical to the SDK exe's output for
the game plus Yamalin.wd).

Sources, later wins: Levels.wd, Update11-15.wd, Update16.wd, then the
enabled archives of ``Mods\\`` and the archives in the game folder, by name.
UNVERIFIED: the order in which the game loads two mods. Tiles that two
enabled mods bring are reported, the cache takes the later name.
"""

import os
import struct
import time
import zlib

import tw1_lhc

KEEP_BACKUPS = 10
RETAIL = ('Levels.wd', 'Update11-15.wd', 'Update16.wd')
_headers = {}            # (path, mtime, size) -> {inner: header bytes}


def _directory(f):
    f.seek(-4, 2)
    n = struct.unpack('<I', f.read(4))[0]
    f.seek(-n, 2)
    t = zlib.decompressobj().decompress(f.read(n - 4))
    off, count = 10, struct.unpack_from('<H', t, 8)[0]
    for _ in range(count):
        ln = t[off]; off += 1
        name = t[off:off + ln].decode('latin-1'); off += ln
        flags, foff, clen, _rlen = struct.unpack_from('<BIII', t, off); off += 13
        if flags & 0x08:
            off += 1 + t[off]
        if flags & 0x10:
            off += 4
        if flags & 0x20:
            off += 16
        yield name, flags, foff, clen


def map_headers(wd_path):
    """{inner path: cache record body} of the maps in one archive (and the
    archive's own cache under tw1_lhc.INNER, if it ships one)."""
    st = os.stat(wd_path)
    key = (os.path.normcase(wd_path), int(st.st_mtime), st.st_size)
    if key in _headers:
        return _headers[key]
    out = {}
    with open(wd_path, 'rb') as f:
        entries = list(_directory(f))
        shipped = next((e for e in entries if e[0].lower() == tw1_lhc.INNER.lower()), None)
        known = {}
        if shipped and os.path.basename(wd_path) in RETAIL:
            # the game's own cache is exact for its own maps: no need to
            # unpack 160 maps on every start
            f.seek(shipped[2])
            raw = f.read(shipped[3])
            known = tw1_lhc.parse(zlib.decompress(raw) if shipped[1] & 1 else raw)
        for name, flags, foff, clen in entries:
            if not tw1_lhc.is_map(name):
                continue
            if name in known:
                out[name] = known[name]
                continue
            f.seek(foff)
            raw = f.read(clen)
            try:
                out[name] = tw1_lhc.header_of(zlib.decompress(raw) if flags & 1 else raw)
            except Exception:
                out[name] = None                     # unreadable map: reported
    if len(_headers) > 400:
        _headers.clear()
    _headers[key] = out
    return out


def plan(game_dir, enabled_mods):
    """(cache bytes, report). ``enabled_mods``: archive paths the game will
    load, any order. report: {'maps', 'mod_maps': {mod: [tiles]},
    'clashes': {inner: [mods]}, 'unreadable': [(mod, inner)]}"""
    records, canon = {}, {}
    report = {'mod_maps': {}, 'clashes': {}, 'unreadable': [], 'broken': []}

    def take(path, is_mod):
        name = os.path.basename(path)
        try:
            heads = map_headers(path)
        except Exception as e:                # one broken archive must not stop the cache for all
            report['broken'].append(f'{name} ({type(e).__name__})')
            return
        for inner, head in heads.items():
            if head is None:
                report['unreadable'].append((name, inner))
                continue
            key = canon.setdefault(inner.lower(), inner)
            if is_mod:
                report['mod_maps'].setdefault(name, []).append(key)
                report['clashes'].setdefault(key, []).append(name)
            records[key] = head
    for wd in RETAIL:
        p = os.path.join(game_dir, 'WDFiles', wd)
        if os.path.isfile(p):
            take(p, False)
    for p in sorted(enabled_mods, key=lambda x: os.path.basename(x).lower()):
        if os.path.isfile(p):
            take(p, True)
    report['clashes'] = {k: v for k, v in report['clashes'].items() if len(v) > 1}
    report['maps'] = len(records)
    out = [tw1_lhc.MAGIC, struct.pack('<II', len(records), 0)]
    for inner in sorted(records):
        raw = inner.encode('latin-1', 'replace')
        out.append(struct.pack('<I', len(raw)) + raw + records[inner])
    return b''.join(out), report


def target(game_dir):
    return os.path.join(game_dir, *tw1_lhc.INNER.split('\\'))


def sync(game_dir, enabled_mods, backup_dir):
    """Write the cache if it differs from the one on disk. Returns
    (changed, report); the old file goes to ``backup_dir`` first."""
    blob, report = plan(game_dir, enabled_mods)
    path = target(game_dir)
    old = None
    if os.path.isfile(path):
        with open(path, 'rb') as f:
            old = f.read()
    if old == blob:
        return False, report
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.{os.getpid()}.mmtmp'
    try:
        with open(tmp, 'wb') as f:
            f.write(blob)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    if old is not None:                    # only after the new one is in place
        try:
            os.makedirs(backup_dir, exist_ok=True)
            stem = time.strftime('Map_LevelHeaders_%Y-%m-%d_%H-%M-%S')
            keep, i = os.path.join(backup_dir, stem + '.lhc'), 1
            while os.path.exists(keep):
                i += 1
                keep = os.path.join(backup_dir, f'{stem}_{i}.lhc')
            with open(keep, 'wb') as f:
                f.write(old)
            report['backup'] = keep
            olds = sorted(n for n in os.listdir(backup_dir) if n.startswith('Map_LevelHeaders_') and n.endswith('.lhc'))
            for n in olds[:-KEEP_BACKUPS]:
                os.remove(os.path.join(backup_dir, n))
        except OSError:
            pass
    return True, report
