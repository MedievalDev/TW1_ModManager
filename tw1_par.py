"""TW1 .par reader/writer - the format core of the TW1 PAR Editor (tw1_par_editor.py),
copied unchanged so the Mod Manager stays a single-folder tool."""
import io
import struct
import zlib

# ═══════════════════════════════════════════════════════════════════════════════
# PAR FORMAT CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

PAR_MAGIC = b'PAR\x00'
PAR_VERSION_TW1 = 0x600

# Data type IDs
TYPE_INT32        = 0
TYPE_FLOAT32      = 1
TYPE_UINT32       = 2
TYPE_STRING       = 3
TYPE_ARRAY_INT32  = 4
TYPE_ARRAY_FLOAT  = 5
TYPE_ARRAY_UINT32 = 6
TYPE_ARRAY_STR    = 7

TYPE_NAMES = {
    0: "int32",
    1: "float32",
    2: "uint32",
    3: "string",
    4: "int32[]",
    5: "float32[]",
    6: "uint32[]",
    7: "string[]",
}

# ═══════════════════════════════════════════════════════════════════════════════
# PAR DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

class ParFile:
    """Represents a complete PAR file."""
    def __init__(self):
        self.version = PAR_VERSION_TW1
        self.lists = []       # [ParList, ...]
        self.filepath = ""
        self.wrapper_header = None   # zlib wrapper header (stream 1)
        self.was_compressed = False   # file was zlib-compressed on disk
        self.trailing_data = None     # bytes after parsed content
        self.wd_entry = None          # directory entry of the par when read from a .wd

class ParList:
    """A list within the PAR file."""
    def __init__(self):
        self.unknown1 = 0
        self.unknown2 = 0
        self.entries = []     # [ParEntry, ...]

class ParEntry:
    """A single named entry with typed data fields."""
    def __init__(self):
        self.name = ""
        self.unknown_byte = 0
        self.unknown_u16a = 0
        self.unknown_u16b = 0
        self.fields = []      # [ParField, ...]

class ParField:
    """A single typed data field within an entry."""
    def __init__(self, dtype=0, value=None):
        self.dtype = dtype    # Type ID (0-7)
        self.value = value    # Python value (int, float, str, list)

# ═══════════════════════════════════════════════════════════════════════════════
# PAR BINARY READER
# ═══════════════════════════════════════════════════════════════════════════════

class ParReader:
    """Reads PAR binary format."""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.size = len(data)

    def read_bytes(self, n):
        if self.pos + n > self.size:
            raise ValueError(f"Read past end at offset 0x{self.pos:X}, need {n} bytes")
        result = self.data[self.pos:self.pos + n]
        self.pos += n
        return result

    def read_u8(self):
        return struct.unpack_from('<B', self.data, self._advance(1))[0]

    def read_i8(self):
        return struct.unpack_from('<b', self.data, self._advance(1))[0]

    def read_u16(self):
        return struct.unpack_from('<H', self.data, self._advance(2))[0]

    def read_u32(self):
        return struct.unpack_from('<I', self.data, self._advance(4))[0]

    def read_i32(self):
        return struct.unpack_from('<i', self.data, self._advance(4))[0]

    def read_f32(self):
        return struct.unpack_from('<f', self.data, self._advance(4))[0]

    def read_u64(self):
        return struct.unpack_from('<Q', self.data, self._advance(8))[0]

    def read_delphi_string(self):
        length = self.read_u32()
        if length > 1000000:
            raise ValueError(f"Unreasonable string length {length} at 0x{self.pos:X}")
        if length == 0:
            return ""
        raw = self.read_bytes(length)
        return raw.decode('ascii', errors='replace')

    def _advance(self, n):
        if self.pos + n > self.size:
            raise ValueError(f"Read past end at offset 0x{self.pos:X}")
        p = self.pos
        self.pos += n
        return p



def read_par(data):
    """Parse a PAR binary file. Returns ParFile."""
    r = ParReader(data)

    # Header
    magic = r.read_bytes(4)
    if magic != PAR_MAGIC:
        raise ValueError(f"Not a PAR file (header: {magic!r}, expected {PAR_MAGIC!r})")

    par = ParFile()
    par.version = r.read_u32()

    # Root list
    list_count = r.read_u32()
    _pad = r.read_u32()       # unknown pad

    for li in range(list_count):
        pl = ParList()
        pl.unknown1 = r.read_u32()
        pl.unknown2 = r.read_u32()

        # Prefixed Array<List Entry>
        entry_count = r.read_u32()

        for ei in range(entry_count):
            entry = ParEntry()
            entry.name = r.read_delphi_string()
            entry.unknown_byte = r.read_i8()

            data_entry_count = r.read_u16()
            entry.unknown_u16a = r.read_u16()
            entry.unknown_u16b = r.read_u16()

            # Data Type List
            type_list = []
            for _ in range(data_entry_count):
                type_list.append(r.read_u8())

            # Data Entry List
            for dtype in type_list:
                field = ParField(dtype)

                if dtype == TYPE_INT32:
                    field.value = r.read_i32()
                elif dtype == TYPE_FLOAT32:
                    field.value = r.read_f32()
                elif dtype == TYPE_UINT32:
                    field.value = r.read_u32()
                elif dtype == TYPE_STRING:
                    field.value = r.read_delphi_string()
                elif dtype == TYPE_ARRAY_INT32:
                    field.value = _read_extra_array(r, 'i')
                elif dtype == TYPE_ARRAY_FLOAT:
                    field.value = _read_extra_array(r, 'f')
                elif dtype == TYPE_ARRAY_UINT32:
                    field.value = _read_extra_array(r, 'I')
                elif dtype == TYPE_ARRAY_STR:
                    field.value = _read_extra_string_array(r)
                else:
                    raise ValueError(f"Unknown data type {dtype} at 0x{r.pos:X}")

                entry.fields.append(field)

            pl.entries.append(entry)
        par.lists.append(pl)

    # Preserve trailing data (some PAR files have extra data after the listed entries)
    if r.pos < r.size:
        par.trailing_data = data[r.pos:]

    return par


def _read_extra_array(reader, fmt_char):
    """Read Extra Prefixed Array<T> for numeric types."""
    check = reader.read_u64()
    if check == 0:
        return []
    length = reader.read_u32()
    values = []
    for _ in range(length):
        if fmt_char == 'i':
            values.append(reader.read_i32())
        elif fmt_char == 'f':
            values.append(reader.read_f32())
        elif fmt_char == 'I':
            values.append(reader.read_u32())
    return values


def _read_extra_string_array(reader):
    """Read Extra Prefixed Array<Delphi ASCII>."""
    check = reader.read_u64()
    if check == 0:
        return []
    length = reader.read_u32()
    values = []
    for _ in range(length):
        values.append(reader.read_delphi_string())
    return values


# ═══════════════════════════════════════════════════════════════════════════════
# PAR BINARY WRITER
# ═══════════════════════════════════════════════════════════════════════════════

class ParWriter:
    """Writes PAR binary format."""

    def __init__(self):
        self.buf = io.BytesIO()

    def write_bytes(self, b):
        self.buf.write(b)

    def write_u8(self, v):
        self.buf.write(struct.pack('<B', v & 0xFF))

    def write_i8(self, v):
        self.buf.write(struct.pack('<b', v))

    def write_u16(self, v):
        self.buf.write(struct.pack('<H', v & 0xFFFF))

    def write_u32(self, v):
        self.buf.write(struct.pack('<I', v & 0xFFFFFFFF))

    def write_i32(self, v):
        self.buf.write(struct.pack('<i', v))

    def write_f32(self, v):
        self.buf.write(struct.pack('<f', v))

    def write_u64(self, v):
        self.buf.write(struct.pack('<Q', v))

    def write_delphi_string(self, s):
        encoded = s.encode('ascii', errors='replace')
        self.write_u32(len(encoded))
        self.buf.write(encoded)

    def get_bytes(self):
        return self.buf.getvalue()


def write_par(par):
    """Write a ParFile to binary. Returns bytes."""
    w = ParWriter()

    # Header
    w.write_bytes(PAR_MAGIC)
    w.write_u32(par.version)

    # Root list
    w.write_u32(len(par.lists))
    w.write_u32(0)   # pad

    for pl in par.lists:
        w.write_u32(pl.unknown1)
        w.write_u32(pl.unknown2)

        # Prefixed Array<List Entry>
        w.write_u32(len(pl.entries))

        for entry in pl.entries:
            w.write_delphi_string(entry.name)
            w.write_i8(entry.unknown_byte)

            field_count = len(entry.fields)
            w.write_u16(field_count)
            w.write_u16(entry.unknown_u16a)
            w.write_u16(entry.unknown_u16b)

            # Data Type List
            for field in entry.fields:
                w.write_u8(field.dtype)

            # Data Entry List
            for field in entry.fields:
                dtype = field.dtype
                val = field.value

                if dtype == TYPE_INT32:
                    w.write_i32(int(val))
                elif dtype == TYPE_FLOAT32:
                    w.write_f32(float(val))
                elif dtype == TYPE_UINT32:
                    w.write_u32(int(val))
                elif dtype == TYPE_STRING:
                    w.write_delphi_string(str(val))
                elif dtype == TYPE_ARRAY_INT32:
                    _write_extra_array(w, val, 'i')
                elif dtype == TYPE_ARRAY_FLOAT:
                    _write_extra_array(w, val, 'f')
                elif dtype == TYPE_ARRAY_UINT32:
                    _write_extra_array(w, val, 'I')
                elif dtype == TYPE_ARRAY_STR:
                    _write_extra_string_array(w, val)

    result = w.get_bytes()

    # Append trailing data if present (for byte-perfect roundtrips)
    if getattr(par, 'trailing_data', None):
        result += par.trailing_data

    return result


def _write_extra_array(writer, values, fmt_char):
    """Write Extra Prefixed Array<T> for numeric types."""
    if not values:
        writer.write_u64(0)
        return
    writer.write_u64(1)
    writer.write_u32(len(values))
    for v in values:
        if fmt_char == 'i':
            writer.write_i32(int(v))
        elif fmt_char == 'f':
            writer.write_f32(float(v))
        elif fmt_char == 'I':
            writer.write_u32(int(v))


def _write_extra_string_array(writer, values):
    """Write Extra Prefixed Array<Delphi ASCII>."""
    if not values:
        writer.write_u64(0)
        return
    writer.write_u64(1)
    writer.write_u32(len(values))
    for v in values:
        writer.write_delphi_string(str(v))

