"""Two Worlds 1 .lnd reader - so far only the marker section.

Quest actions address markers by NUMBER, and that number is scoped by marker
TYPE: OBJECT_CREATE looks for a MARKER_QUEST_CREATE_OBJECT with that number,
NPC_TELEPORT for a MARKER_QUEST_TELEPORT, OPEN/CLOSE for a MARKER_GATE. Point
an action at a number its type does not have and NOTHING happens - no error,
no log line. That is how Q_390's orc spawn ran into the void for days.

Section layout up to the markers (see LNDTool/LND_FORMAT_GUIDE.md):

    u32   magic/pre
    str16 map name          (u32 count, then count UTF-16 code units)
    20 B  dimensions etc.
    6x    adjacent map name (u32 length + ASCII)
    u32   marker count | u32 pad
    per marker: u32 len + ASCII name | u32 instance id | 3x f32 xyz | 9 B
"""

import collections
import struct
import zlib


def unwrap(blob):
    """Raw .lnd bytes -> decompressed map data.

    Editor files are two concatenated zlib streams (a 24-byte header, then the
    map); SDK/game files are a single stream. Uncompressed data passes through.
    """
    return split(blob)[1]


def split(blob):
    """(header, map data) - header is b'' when there is only one stream.

    Needed to put a file back together after editing: the 24-byte header of
    the editor format carries a GUID and must survive untouched.
    """
    if blob[:1] != b'\x78':
        return b'', blob
    d = zlib.decompressobj()
    first = d.decompress(blob)
    if d.unused_data:
        return first, zlib.decompressobj().decompress(d.unused_data)
    return b'', first


def rewrap(header, data):
    """Inverse of split()."""
    if header:
        return zlib.compress(header) + zlib.compress(data)
    return zlib.compress(data)


def _ascii(data, pos):
    n = struct.unpack_from('<I', data, pos)[0]
    return data[pos + 4:pos + 4 + n].decode('latin-1'), pos + 4 + n


def _marker_section(data):
    """(offset of the count field, offset just past the last marker)."""
    pos = 4
    pos += 4 + struct.unpack_from('<I', data, pos)[0] * 2     # map name, UTF-16
    pos += 20
    for _ in range(6):                                        # adjacent maps
        _name, pos = _ascii(data, pos)
    start = pos
    count = struct.unpack_from('<I', data, pos)[0]
    pos += 8
    for _ in range(count):
        _name, pos = _ascii(data, pos)
        pos = _entry_end(data, pos)
    return start, pos


def _entry_end(data, pos):
    """Offset just past one marker, ``pos`` right after its name.

    After the name: u32 instance id, i32 x/y/z, u8 angle, then a length-
    prefixed ASCII text and a u32. The text is empty on nearly every marker
    (that is why it was read as "8 spare bytes"); measured 2026-09-19 on
    Dream Worlds' Map_E03: marker 323 carries "Engine.Draw3DObjects" there,
    and a fixed tail ran off the rails.
    """
    pos += 4 + 12 + 1
    n = struct.unpack_from('<I', data, pos)[0]
    return pos + 4 + n + 4


# Per marker, after the name: u32 instance id, i32 x/y/z, u8 angle, 8 spare.
# The XYZ are INTEGERS - reading them as float32 yields nothing but zeros.
ENTRY_TAIL = 4 + 12 + 9


def markers(blob):
    """{marker name: {instance id, ...}} for one .lnd."""
    return {n: {i for i, _p in v} for n, v in markers_full(blob).items()}


def markers_full(blob):
    """{marker name: [(instance id, (x, y, z, angle)), ...]}."""
    data = unwrap(blob)
    pos = 4
    pos += 4 + struct.unpack_from('<I', data, pos)[0] * 2
    pos += 20
    for _ in range(6):
        _name, pos = _ascii(data, pos)
    count = struct.unpack_from('<I', data, pos)[0]
    pos += 8
    out = collections.defaultdict(list)
    for _ in range(count):
        name, pos = _ascii(data, pos)
        ident, x, y, z = struct.unpack_from('<Iiii', data, pos)
        out[name].append((ident, (x, y, z, data[pos + 16])))
        pos = _entry_end(data, pos)
    return dict(out)


def add_marker(blob, name, ident, x, y, z, angle=0):
    """Append one marker to a .lnd and return the new raw bytes.

    The Two Worlds map editor cannot place quest markers, so this writes the
    record directly. Everything outside the marker section is carried through
    untouched, including the editor header with its GUID.
    """
    header, data = split(blob)
    start, end = _marker_section(data)
    count = struct.unpack_from('<I', data, start)[0]

    raw = name.encode('latin-1')
    entry = (struct.pack('<I', len(raw)) + raw
             + struct.pack('<Iiii', ident, x, y, z)
             + bytes([angle]) + b'\x00' * 8)

    out = (data[:start] + struct.pack('<I', count + 1) + data[start + 4:end]
           + entry + data[end:])
    return rewrap(header, out)


def move_marker(blob, name, ident, dz=0, x=None, y=None, z=None):
    """Verschiebt einen VORHANDENEN Marker. Liefert die neuen Rohbytes.

    Gedacht fuer den Fall, dass ein im Editor gesetzter Marker auf der
    falschen Hoehe liegt: Two Worlds legt Marker offenbar auf der Ebene ab,
    die beim Setzen unter dem Mauszeiger lag, nicht auf dem Boden des Raums,
    in dem sie stehen sollen. In F01_1 sind dadurch alle selbst gesetzten
    Marker im Schmiede-Bereich rund 200 bis 300 Einheiten zu tief - die
    Originalmarker desselben Raums liegen bei z=2748, die eigenen bei 2442.

    Nur die Koordinaten werden angefasst; Name, Id, Winkel und alles
    ausserhalb des Markerblocks bleiben Byte fuer Byte, wie sie waren.

    dz addiert auf die vorhandene Hoehe, x/y/z setzen absolut. Wirft, wenn
    der Marker nicht existiert - ein stiller Fehlschlag waere hier schlimmer
    als ein Abbruch, weil man ihn erst im Spiel bemerkt.
    """
    header, data = split(blob)
    start, end = _marker_section(data)
    count = struct.unpack_from('<I', data, start)[0]
    pos = start + 8
    for _ in range(count):
        nlen = struct.unpack_from('<I', data, pos)[0]
        gefunden = data[pos + 4:pos + 4 + nlen].decode('latin-1')
        kopf = pos + 4 + nlen
        mid, mx, my, mz = struct.unpack_from('<Iiii', data, kopf)
        if gefunden == name and mid == ident:
            neu = struct.pack('<Iiii', mid,
                              mx if x is None else x,
                              my if y is None else y,
                              (mz + dz) if z is None else z)
            out = data[:kopf] + neu + data[kopf + 16:]
            return rewrap(header, out), (mx, my, mz)
        pos = _entry_end(data, kopf)
    raise KeyError(f'{name} {ident} nicht in der Karte')


# Which marker type each quest action addresses.
ACTION_MARKER = {
    'OBJECT_CREATE': 'MARKER_QUEST_CREATE_OBJECT',
    'ENEMY_CREATE': 'MARKER_QUEST_CREATE_ENEMY',
    'NPC_TELEPORT': 'MARKER_QUEST_TELEPORT',
    'HERO_TELEPORT_DELAYED': 'MARKER_QUEST_TELEPORT',
    'CLEAR_AREA': 'MARKER_QUEST_CLEAR_AREA',
    'KILL_AREA': 'MARKER_QUEST_KILL_AREA',
    'NPC_GO': 'MARKER_QUEST_WALK',
    'OPEN': 'MARKER_GATE',
    'CLOSE': 'MARKER_GATE',
}
FC_MARKER = {
    'GO': 'MARKER_QUEST_POINT',
    'FIND_PLACE': 'MARKER_QUEST_POINT',
}
