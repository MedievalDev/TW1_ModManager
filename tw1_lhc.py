"""Level header cache ``Levels\\Map_LevelHeaders.lhc`` in pure Python.

The game keeps one record per map with everything it needs before the map is
loaded - above all the markers. The SDK builds the file with
``MeshParamsGen.exe "<game>/>" -levelheaderscache "Levels\\Map_*.lnd"
"Levels\\Map_LevelHeaders.lhc"`` (LevelHeadersCacheGen.bat). This module
writes the same bytes without the SDK.

Layout, measured 2026-09-19 on the cache inside Levels.wd (160 maps, every
record byte-identical to what this module builds):

    "LC\\0\\0"  u32 map count  u32 0
    per map, sorted by path:
        u32 path length, path ("Levels\\Map_A01.lnd", ASCII)
        the map body from its first byte to the end of the marker block

The map body is the uncompressed .lnd (editor files carry a metadata stream
in front, tw1_lnd.unwrap drops it).
"""

import re
import struct

import tw1_lnd

MAGIC = b'LC\x00\x00'
INNER = 'Levels\\Map_LevelHeaders.lhc'
_MAP = re.compile(r'^levels\\map_[a-z0-9_]+\.lnd$', re.I)


def is_map(inner):
    return bool(_MAP.match(inner))


def header_of(lnd_blob):
    """The part of a map the cache keeps: body[:end of marker block]."""
    body = tw1_lnd.unwrap(lnd_blob)
    _start, end = tw1_lnd._marker_section(body)
    return body[:end]


def build(maps):
    """``maps``: {inner path: .lnd bytes (packed or body)} -> cache bytes."""
    out = [MAGIC, struct.pack('<II', len(maps), 0)]
    for inner in sorted(maps):
        raw = inner.encode('ascii')
        out.append(struct.pack('<I', len(raw)) + raw + header_of(maps[inner]))
    return b''.join(out)


def parse(blob):
    """{inner path: header bytes} of a cache file (for comparing two caches)."""
    if blob[:4] != MAGIC:
        raise ValueError('not a level header cache')
    count = struct.unpack_from('<I', blob, 4)[0]
    starts = [m.start() for m in re.finditer(rb'Levels\\Map_[A-Za-z0-9_]+\.lnd', blob, re.I)]
    out = {}
    for i, st in enumerate(starts):
        n = struct.unpack_from('<I', blob, st - 4)[0]
        end = starts[i + 1] - 4 if i + 1 < len(starts) else len(blob)
        out[blob[st:st + n].decode('ascii')] = blob[st + n:end]
    if len(out) != count:
        raise ValueError(f'cache says {count} maps, found {len(out)}')
    return out
