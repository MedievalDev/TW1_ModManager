"""Two Worlds 1 .lan reader + writer (localisation format, version 3).

Binary little-endian:
    "LAN\\0" | u32 version=3
    u32 N ; N x Translation : u32 keyLenBytes, ASCII key,
                              u32 valLenCHARS, UTF-16LE value (chars*2 bytes)
    u32 M ; M x Alias        : u32 kLen, ASCII key, u32 vLen, ASCII value
    u32 K ; K x DialogTree    : (opaque, preserved verbatim by this module)

Keys carry the literal "translate" prefix (e.g. "translateQ_381"). A minimal
text-only file is the header + N translations + u32(0) alias + u32(0) tree;
both trailing count words are mandatory. Verified byte-exact round-trip
against the shipped TwoWorldsQuests.lan (3,310,229 bytes).
"""

import struct

_MAGIC = b'LAN\x00'
_VERSION = 3
PREFIX = 'translate'


def read(data):
    """Return (translations: dict[str,str], aliases: list[(k,v)], rest: bytes).

    `rest` is the dialog-tree section, kept opaque so a re-write can preserve
    it untouched. Keys keep their 'translate' prefix.
    """
    if data[:4] != _MAGIC:
        raise ValueError('Not a .lan file')
    ver = struct.unpack_from('<I', data, 4)[0]
    if ver != _VERSION:
        raise ValueError(f'Unsupported .lan version {ver}')
    off = 8
    n = struct.unpack_from('<I', data, off)[0]; off += 4
    translations = {}
    for _ in range(n):
        klen = struct.unpack_from('<I', data, off)[0]; off += 4
        key = data[off:off + klen].decode('latin-1'); off += klen
        vlen = struct.unpack_from('<I', data, off)[0]; off += 4
        val = data[off:off + vlen * 2].decode('utf-16-le', 'replace')
        off += vlen * 2
        translations[key] = val
    m = struct.unpack_from('<I', data, off)[0]; off += 4
    aliases = []
    for _ in range(m):
        klen = struct.unpack_from('<I', data, off)[0]; off += 4
        key = data[off:off + klen].decode('latin-1'); off += klen
        vlen = struct.unpack_from('<I', data, off)[0]; off += 4
        val = data[off:off + vlen].decode('latin-1'); off += vlen
        aliases.append((key, val))
    rest = data[off:]                        # dialog-tree section, opaque
    return translations, aliases, rest


def build(translations, aliases=None, rest=None):
    """Serialize a .lan. `translations` maps full keys ('translateQ_...') to
    text. `rest` (dialog-tree bytes) defaults to an empty tree section."""
    out = bytearray(_MAGIC + struct.pack('<I', _VERSION))
    out += struct.pack('<I', len(translations))
    for key, val in translations.items():
        raw_key = key.encode('latin-1')
        raw_val = val.encode('utf-16-le')
        out += struct.pack('<I', len(raw_key)) + raw_key
        out += struct.pack('<I', len(raw_val) // 2) + raw_val
    aliases = aliases or []
    out += struct.pack('<I', len(aliases))
    for key, val in aliases:
        rk = key.encode('latin-1'); rv = val.encode('latin-1')
        out += struct.pack('<I', len(rk)) + rk
        out += struct.pack('<I', len(rv)) + rv
    if rest is None:
        out += struct.pack('<I', 0)          # zero dialog trees
    else:
        out += rest
    return bytes(out)


# --- dialog trees -----------------------------------------------------------
# Tree section layout (verified byte-exact against the shipped file):
#   u32 treeCount
#   per tree : u32 idLen, ASCII id ("translateDQ_<n>")
#              u32 entryCount, u32 pad(=1), entries[]
#   per entry: i32 lector
#              u32 len, ASCII tid          (key into the translations table)
#              u32 len, ASCII cue          ("CUE_<lector:04>_<n:04>", "" = silent)
#              u32 nextCount, u32 pad(=1), i32[nextCount]
#              u32 flags
#              u32 camCount,  u32 pad(=1), i32[camCount]
#              u32 anim1, u32 anim2

class DialogEntry:
    __slots__ = ('lector', 'tid', 'cue', 'next', 'flags', 'cams',
                 'anim1', 'anim2', 'next_pad', 'cam_pad')

    def __init__(self, lector, tid, cue='', next=None, flags=0, cams=None,
                 anim1=0, anim2=0, next_pad=1, cam_pad=1):
        self.lector = lector
        self.tid = tid
        self.cue = cue
        self.next = list(next or [])
        self.flags = flags
        self.cams = list(cams or [])
        self.anim1 = anim1
        self.anim2 = anim2
        self.next_pad = next_pad
        self.cam_pad = cam_pad


class DialogTree:
    __slots__ = ('id', 'entries', 'pad')

    def __init__(self, id, entries=None, pad=1):
        self.id = id
        self.entries = list(entries or [])
        self.pad = pad


def _rd_str(buf, off):
    n = struct.unpack_from('<I', buf, off)[0]
    off += 4
    return buf[off:off + n].decode('latin-1'), off + n


def _wr_str(text):
    raw = text.encode('latin-1', 'replace')
    return struct.pack('<I', len(raw)) + raw


def parse_trees(rest):
    """Parse the dialog-tree section into [DialogTree]."""
    off = 0
    count = struct.unpack_from('<I', rest, off)[0]
    off += 4
    trees = []
    for _ in range(count):
        tid, off = _rd_str(rest, off)
        n, pad = struct.unpack_from('<II', rest, off)
        off += 8
        entries = []
        for _ in range(n):
            lector = struct.unpack_from('<i', rest, off)[0]
            off += 4
            key, off = _rd_str(rest, off)
            cue, off = _rd_str(rest, off)
            nn, npad = struct.unpack_from('<II', rest, off)
            off += 8
            nxt = list(struct.unpack_from(f'<{nn}i', rest, off)) if nn else []
            off += 4 * nn
            flags = struct.unpack_from('<I', rest, off)[0]
            off += 4
            cn, cpad = struct.unpack_from('<II', rest, off)
            off += 8
            cams = list(struct.unpack_from(f'<{cn}i', rest, off)) if cn else []
            off += 4 * cn
            a1, a2 = struct.unpack_from('<II', rest, off)
            off += 8
            entries.append(DialogEntry(lector, key, cue, nxt, flags, cams,
                                       a1, a2, npad, cpad))
        trees.append(DialogTree(tid, entries, pad))
    return trees


def build_trees(trees):
    """Serialize [DialogTree] back into the tree-section bytes."""
    out = bytearray(struct.pack('<I', len(trees)))
    for t in trees:
        out += _wr_str(t.id)
        out += struct.pack('<II', len(t.entries), t.pad)
        for e in t.entries:
            out += struct.pack('<i', e.lector)
            out += _wr_str(e.tid)
            out += _wr_str(e.cue)
            out += struct.pack('<II', len(e.next), e.next_pad)
            out += struct.pack(f'<{len(e.next)}i', *e.next)
            out += struct.pack('<I', e.flags)
            out += struct.pack('<II', len(e.cams), e.cam_pad)
            out += struct.pack(f'<{len(e.cams)}i', *e.cams)
            out += struct.pack('<II', e.anim1, e.anim2)
    return bytes(out)


def build_quest_text(entries):
    """Convenience: entries is {quest_id_int: {'name':..., 'take':...,
    'close':..., 'solve':...}} -> a text-only .lan with the right keys."""
    tr = {}
    for qid, fields in entries.items():
        if 'name' in fields:
            tr[f'{PREFIX}Q_{qid}'] = fields['name']
        if 'take' in fields:
            tr[f'{PREFIX}Q_{qid}_QTD'] = fields['take']
        if 'solve' in fields:
            tr[f'{PREFIX}Q_{qid}_QSD'] = fields['solve']
        if 'close' in fields:
            tr[f'{PREFIX}Q_{qid}_QCD'] = fields['close']
    return build(tr)
