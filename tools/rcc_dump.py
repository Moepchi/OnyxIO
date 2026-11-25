# Qt rcc reader: dumps files whose path matches argv[2] into argv[3]. Read only.
import struct, sys, zlib, os
d = open(sys.argv[1], 'rb').read()
assert d[:4] == b'qres'
ver, tree, data, names = struct.unpack('>IIII', d[4:20])
esz = 14 if ver < 2 else 22

def name(off):
    n = struct.unpack('>H', d[names + off:names + off + 2])[0]
    return d[names + off + 6:names + off + 6 + n * 2].decode('utf-16-be')

def walk(i, path):
    e = tree + i * esz
    noff, flags = struct.unpack('>IH', d[e:e + 6])
    p = path + '/' + name(noff) if i else ''
    if flags & 2:  # directory
        cnt, first = struct.unpack('>II', d[e + 6:e + 14])
        for c in range(first, first + cnt):
            yield from walk(c, p)
    else:
        doff = struct.unpack('>I', d[e + 10:e + 14])[0]
        n = struct.unpack('>I', d[data + doff:data + doff + 4])[0]
        b = d[data + doff + 4:data + doff + 4 + n]
        if flags & 1: b = zlib.decompress(b[4:])
        elif flags & 4: b = None  # zstd, not needed here
        yield p, b

for p, b in walk(0, ''):
    if sys.argv[2] in p:
        print(p, len(b) if b else 'zstd')
        if b and len(sys.argv) > 3:
            out = os.path.join(sys.argv[3], p.strip('/').replace('/', '__'))
            open(out, 'wb').write(b)
