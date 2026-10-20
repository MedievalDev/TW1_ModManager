"""Two Worlds 1 WD archive reader + writer (format version 0x200).

Pure standard-library. Round-trips real archives byte-faithfully at the
*content* level (compression/GUID/timestamp in the container may differ, the
extracted file bytes do not). Built from the CC0 WD format documentation and
verified against the game's own archives.

Layout of a TW1 .wd:
    [zlib(Header)]                      at offset 0
    [file data blobs]                  each at its recorded absolute offset
    [zlib(Directory)]                  the last DIR_OFF bytes before the tail
    uint32 DIR_OFF                     final 4 bytes = len(zlib(Directory))

Header (pre-zlib, 24 bytes):
    uint32 0xFFA1D031 | "WD" | uint16 0x0200 | byte[16] GUID
Directory (pre-zlib):
    uint64 FILETIME | uint16 count | count x Entry
Entry:
    pascal(uint8 len) path | uint8 flags | uint32 offset
    | uint32 clen | uint32 rlen
    | pascal extra_string   if flags & 0x08
    | uint32 extra_int      if flags & 0x10
    | byte[16] guid         if flags & 0x20
Flags: 0x01 compressed, 0x04 unknown-but-set-on-.qtx, 0x08 str, 0x10 int,
0x20 guid. Bit 0x04 adds no bytes to the entry; it is copied verbatim so a
repacked file matches how the game stored it.
"""

import hashlib
import struct
import zlib

# Stored as the literal byte sequence FF A1 D0 31 - NOT a little-endian uint32.
# Writing it the wrong way round produces an archive that this module's own
# reader still accepts (it only checks the "WD" tag) but that Tw1WDRepacker and
# the game both reject.
_MAGIC_PRE = bytes.fromhex('FFA1D031')
_VERSION = 0x0200
# A fixed GUID keeps rebuilds reproducible; the game tolerates any value.
_HEADER_GUID = bytes.fromhex('54573144-5750-0000-0000-000000000000'.replace('-', ''))
# 2007-01-01 in FILETIME (100ns ticks since 1601); a constant, engine ignores it.
_FILETIME = 128119104000000000

# default flag byte per extension, mirroring the vanilla archives
_DEFAULT_FLAGS = {
    '.qtx': 0x05,   # compressed + 0x04 (as shipped in Update16.wd)
    '.lan': 0x01,   # compressed
    '.phx': 0x00,   # physics files are stored uncompressed
}
_FLAG_COMPRESSED = 0x01
_FLAG_STR = 0x08
_FLAG_INT = 0x10
_FLAG_GUID = 0x20


class Entry:
    __slots__ = ('path', 'data', 'flags', 'extra_str', 'extra_int', 'guid')

    def __init__(self, path, data, flags=None, extra_str=b'', extra_int=0,
                 guid=b''):
        self.path = path
        self.data = data            # always the UNCOMPRESSED file bytes
        if flags is None:
            ext = path[path.rfind('.'):].lower() if '.' in path else ''
            flags = _DEFAULT_FLAGS.get(ext, _FLAG_COMPRESSED)
        self.flags = flags
        self.extra_str = extra_str
        self.extra_int = extra_int
        self.guid = guid


def _read_zlib_stream(buf, offset):
    """Decompress one zlib stream starting at `offset`; return (data, consumed)."""
    d = zlib.decompressobj()
    out = d.decompress(buf[offset:])
    out += d.flush()
    consumed = len(buf) - offset - len(d.unused_data)
    return out, consumed


def read(path):
    """Read a WD archive into a list of Entry (uncompressed data)."""
    raw = open(path, 'rb').read()
    if raw[:2] != b'\x78\x9c':
        raise ValueError(f'Not a zlib-headed WD archive: {path}')
    header, _ = _read_zlib_stream(raw, 0)
    if header[:4] != _MAGIC_PRE:
        raise ValueError(f'Bad WD magic {header[:4].hex()} '
                         f'(expected {_MAGIC_PRE.hex()}): {path}')
    if header[4:6] != b'WD' or struct.unpack_from('<H', header, 6)[0] != _VERSION:
        raise ValueError(f'Not a TW1 (0x200) WD archive: {path}')
    dir_off = struct.unpack_from('<I', raw, len(raw) - 4)[0]
    table, _ = _read_zlib_stream(raw, len(raw) - dir_off)
    off = 8                                  # skip FILETIME
    count = struct.unpack_from('<H', table, off)[0]
    off += 2
    entries = []
    for _ in range(count):
        nlen = table[off]; off += 1
        name = table[off:off + nlen].decode('latin-1'); off += nlen
        flags, foff, clen, rlen = struct.unpack_from('<BIII', table, off)
        off += 13
        extra_str, extra_int, guid = b'', 0, b''
        if flags & _FLAG_STR:
            xl = table[off]; off += 1
            extra_str = table[off:off + xl]; off += xl
        if flags & _FLAG_INT:
            extra_int = struct.unpack_from('<I', table, off)[0]; off += 4
        if flags & _FLAG_GUID:
            guid = table[off:off + 16]; off += 16
        if flags & _FLAG_COMPRESSED:
            data, _ = _read_zlib_stream(raw, foff)
        else:
            data = raw[foff:foff + clen]
        entries.append(Entry(name, data, flags, extra_str, extra_int, guid))
    return entries


def write(path, entries):
    """DEPRECATED - do not use for anything the game will load.

    Kept only for round-trip tests. Real mods must be packed with the proven
    tools (Tw1WDRepacker.exe, or buglord's wdio.py which QuestForge drives) -
    hand-rolled packing cost a lot of debugging time for no benefit. Use
    questforge.pack_mod() instead.
    """
    import warnings
    warnings.warn('tw1_wd.write is deprecated; pack with wdio.py '
                  '(see questforge.pack_mod)', DeprecationWarning, stacklevel=2)
    return _write_unsafe(path, entries)


def _write_unsafe(path, entries):
    """Write a list of Entry to a WD archive (test helper only)."""
    # Real archives each carry their own GUID. Derive it from the content so a
    # rebuild of the same mod is reproducible but two different mods differ.
    digest = hashlib.sha1()
    for e in entries:
        digest.update(e.path.encode('latin-1'))
        digest.update(e.data)
    guid = digest.digest()[:16]
    header = _MAGIC_PRE + b'WD' + struct.pack('<H', _VERSION) + guid
    # default level (6) so the zlib header is 0x78 0x9c, exactly as the game's
    # own archives store it (level 9 would emit 0x78 0xDA and trip readers that
    # sniff the 0x789C magic).
    out = bytearray(zlib.compress(header))

    placed = []                              # (entry, offset, clen, rlen, stored)
    for e in entries:
        rlen = len(e.data)
        if e.flags & _FLAG_COMPRESSED:
            stored = zlib.compress(e.data)
        else:
            stored = e.data
        offset = len(out)
        out += stored
        placed.append((e, offset, len(stored), rlen))

    table = bytearray(struct.pack('<Q', _FILETIME))
    table += struct.pack('<H', len(placed))
    for e, offset, clen, rlen in placed:
        raw_name = e.path.encode('latin-1')
        table += bytes([len(raw_name)]) + raw_name
        table += struct.pack('<BIII', e.flags, offset, clen, rlen)
        if e.flags & _FLAG_STR:
            table += bytes([len(e.extra_str)]) + e.extra_str
        if e.flags & _FLAG_INT:
            table += struct.pack('<I', e.extra_int)
        if e.flags & _FLAG_GUID:
            table += (e.guid or _HEADER_GUID)[:16].ljust(16, b'\0')

    comp_dir = zlib.compress(bytes(table))
    out += comp_dir
    # DIR_OFF is the distance from EOF back to the start of the directory,
    # i.e. the compressed directory plus this 4-byte field itself.
    out += struct.pack('<I', len(comp_dir) + 4)
    with open(path, 'wb') as f:
        f.write(out)
    return len(out)


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        for e in read(p):
            print(f'0x{e.flags:02X} {len(e.data):>9} {e.path}')
