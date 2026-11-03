"""Deterministic single-folder MSZIP cabinet creation using the Python standard library."""
from pathlib import Path
import struct
import zlib


def pack(files, destination):
    """files is an ordered sequence of (ASCII relative name, bytes)."""
    records, content, offset = [], [], 0
    for name, data in files:
        encoded = name.replace('/', '\\').encode('ascii')
        if name.startswith(('/', '\\')) or '..' in name.split('/') or ':' in name:
            raise ValueError('Unsafe cabinet member: ' + name)
        # Archive attribute, deterministic DOS timestamp 2026-01-01 00:00.
        records.append(struct.pack('<IIHHHH', len(data), offset, 0, 0x5c21, 0, 0x20) + encoded + b'\0')
        content.append(data)
        offset += len(data)
    plain = b''.join(content)
    blocks, previous = [], b''
    for start in range(0, len(plain), 32768):
        data = plain[start:start+32768]
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15, zdict=previous) if previous else zlib.compressobj(9, zlib.DEFLATED, -15)
        compressed = b'CK' + compressor.compress(data) + compressor.flush()
        blocks.append(struct.pack('<IHH', 0, len(compressed), len(data)) + compressed)
        previous = data
    if len(files) > 65535 or len(blocks) > 65535:
        raise ValueError('Cabinet exceeds single-folder limits')
    file_table = b''.join(records)
    data_start = 44 + len(file_table)
    size = data_start + sum(map(len, blocks))
    header = struct.pack('<4sIIIIIBBHHHHH', b'MSCF', 0, size, 0, 44, 0, 3, 1,
                         1, len(files), 0, 0, 0)
    folder = struct.pack('<IHH', data_start, len(blocks), 1)
    Path(destination).write_bytes(header + folder + file_table + b''.join(blocks))


def unpack_for_test(file):
    """Independent structural reader, also checks every decompressed block size."""
    blob = Path(file).read_bytes()
    signature, _, size, _, table, _, minor, major, folders, count, flags, _, _ = struct.unpack_from('<4sIIIIIBBHHHHH', blob)
    assert signature == b'MSCF' and size == len(blob) and (minor, major, folders, flags) == (3, 1, 1, 0)
    start, block_count, compression = struct.unpack_from('<IHH', blob, 36)
    assert compression == 1
    rows, pos = [], table
    for _ in range(count):
        length, offset, folder, _, _, _ = struct.unpack_from('<IIHHHH', blob, pos)
        end = blob.index(0, pos+16)
        name = blob[pos+16:end].decode('ascii').replace('\\', '/')
        assert folder == 0
        rows.append((name, length, offset))
        pos = end+1
    assert pos == start
    pieces, previous, pos = [], b'', start
    for _ in range(block_count):
        _, compressed, length = struct.unpack_from('<IHH', blob, pos)
        pos += 8
        data = blob[pos:pos+compressed]
        assert data[:2] == b'CK'
        decompressor = zlib.decompressobj(-15, zdict=previous) if previous else zlib.decompressobj(-15)
        plain = decompressor.decompress(data[2:]) + decompressor.flush()
        assert len(plain) == length and decompressor.eof
        pieces.append(plain)
        previous = plain
        pos += compressed
    assert pos == len(blob)
    plain = b''.join(pieces)
    return {name: plain[offset:offset+length] for name, length, offset in rows}
