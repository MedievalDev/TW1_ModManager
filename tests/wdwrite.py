"""Writes a small .wd archive for the tests (test helper only)."""
import os
import struct
import time
import zlib

WD_MAGIC = bytes([0xFF, 0xA1, 0xD0, 0x31, 0x57, 0x44, 0x00, 0x02])
FILETIME_EPOCH = 116444736000000000


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
