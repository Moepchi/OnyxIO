"""Minimal QLink client for the be quiet! Dark Mount (Windows, stdlib + Pillow). Protocol: PROTOCOL.md."""
import ctypes, io, queue, struct, sys, threading, time, types, uuid

HID_GUID = uuid.UUID('4d1e55b2-f16f-11cf-88cb-001111000030')
DISPLAY_KEYS = range(0x6d, 0x75)  # Key_NumpadImage0..7
STATUS = ['Success', 'InvalidSessionId', 'InvalidFeatureId', 'InvalidCommandId', 'InvalidRequestId',
          'InvalidParameter', 'NotAuthorized', 'NotActive', 'InvalidState', 'InvalidSize', 'InsufficientResources']


def crc16(data):  # CRC-16/MODBUS
    c = 0xffff
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xa001 if c & 1 else c >> 1
    return c


def find_path():
    cm = ctypes.WinDLL('cfgmgr32')
    guid = (ctypes.c_byte * 16).from_buffer_copy(HID_GUID.bytes_le)
    n = ctypes.c_ulong()
    cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(n), guid, None, 0)
    buf = ctypes.create_unicode_buffer(n.value)
    if cm.CM_Get_Device_Interface_ListW(guid, None, buf, n.value, 0):
        raise OSError('CM_Get_Device_Interface_ListW failed')
    for p in buf[:n.value].split('\0'):
        if 'vid_373f&pid_0001&mi_02' in p.lower():
            return p
    raise OSError('Dark Mount (MI_02) not found')


K = ctypes.WinDLL('kernel32', use_last_error=True)
K.CreateFileW.restype = ctypes.c_void_p
_io = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
K.ReadFile.argtypes = K.WriteFile.argtypes = _io
K.CloseHandle.argtypes = [ctypes.c_void_p]


def open_hid(path):
    # os.open lacks FILE_SHARE_WRITE (access denied) and msvcrt rejects HID handles -> raw Win32
    h = K.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)  # GENERIC_RW, SHARE_RW, OPEN_EXISTING
    if h in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    return h


def hid_read(h):
    buf, n = ctypes.create_string_buffer(65), ctypes.c_ulong()
    if not K.ReadFile(h, buf, 65, ctypes.byref(n), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return buf.raw[:n.value]


def hid_write(h, data):
    n = ctypes.c_ulong()
    if not K.WriteFile(h, data, len(data), ctypes.byref(n), None):
        raise ctypes.WinError(ctypes.get_last_error())


class QLinkError(Exception):
    pass


class QLink:
    def __init__(self, path=None):
        path = path or find_path()
        # two handles: a blocking read on a synchronous handle would otherwise block writes
        self.w, self.r = open_hid(path), open_hid(path)
        self.sid = self.rid = 0
        self.replies, self.notes = queue.Queue(), queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buf = b''
        while True:
            try:
                p = hid_read(self.r)[1:]  # strip report id
            except OSError:  # unplugged or closed: end quietly, call() then times out
                return
            if len(p) < 64 or crc16(p[:62]) != int.from_bytes(p[62:64], 'little'):
                continue
            if self.sid and p[2] not in (0, self.sid):  # another client's session
                continue
            buf += p[3:1 + p[0]]
            if p[1] & 0x80:
                continue
            st, rid, feat, cmd = buf[:4]
            (self.notes if rid == 0 else self.replies).put((st, rid, feat, cmd, buf[4:]))
            buf = b''

    def close(self):
        # ponytail: a reader blocked in ReadFile only ends with the next report; harmless leftover thread
        K.CloseHandle(self.w)
        K.CloseHandle(self.r)

    def call(self, feat, cmd, params=b'', timeout=2.0):
        self.rid = self.rid % 255 + 1
        msg = bytes([0, self.rid, feat, cmd]) + params
        chunks = [msg[i:i + 59] for i in range(0, len(msg), 59)]
        for i, ch in enumerate(chunks):
            p = bytes([2 + len(ch), i | (0x80 if i < len(chunks) - 1 else 0), self.sid]) + ch
            p = p.ljust(62, b'\0')
            hid_write(self.w, b'\0' + p + crc16(p).to_bytes(2, 'little'))
        end = time.monotonic() + timeout
        while True:
            st, rid, *_, data = self.replies.get(timeout=max(0.01, end - time.monotonic()))
            if rid != self.rid:
                continue  # stale reply
            if st:
                raise QLinkError(f'{feat:#04x}/{cmd:#04x}: {STATUS[st] if st < len(STATUS) else st}')
            return data

    def open_session(self):
        d = self.call(0x01, 0x01, bytes.fromhex('3412000001'))  # same bytes as IO Center
        self.sid = d[4]
        if d[5] == 1:  # [token u32][sid][state][timeout s]: active at once if no other client holds it
            return True
        end = time.monotonic() + 3
        while time.monotonic() < end:  # wait for Root.SessionStateChanged -> Active
            try:
                _, _, feat, cmd, p = self.notes.get(timeout=0.2)
            except queue.Empty:
                continue
            if (feat, cmd) == (0x01, 0x01) and p[-1] == 1:
                return True
        return False

    def keep_alive(self):
        # ponytail: uses a verified read instead of the untested Root.KeepAlive (0x01/0x03)
        self.call(0x20, 0x01)

    def set_binding(self, key, action, data=b''):  # Bindings.SetBinding [key u16][action][data], ids: PROTOCOL.md
        self.call(0x11, 0x02, struct.pack('<HB', key, action) + data)

    def _list(self, cmd, prefix, size):  # Macros list reads: [prefix][offset u16] -> [total u16][items from offset]
        items, total = [], 1
        while len(items) < total:
            r = self.call(0x12, cmd, prefix + struct.pack('<H', len(items)))
            total = int.from_bytes(r[:2], 'little')
            got = [r[i:i + size] for i in range(2, len(r) - size + 1, size)]
            if not got:
                break
            items += got
        return items[:total]

    def macros(self):
        """Macros stored on the keyboard (PROTOCOL.md, Macros): [{id, name, mode, delay_mode, delay, events}],
        events = [(type 02 down / 03 up, HID usage, ms after it)]."""
        out = []
        for raw in self._list(0x01, b'', 2):  # only ids GetMacros itself lists, nothing guessed
            p = raw
            name = self.call(0x12, 0x07, p)
            mode, dmode, delay = struct.unpack('<BBH', self.call(0x12, 0x03, p)[:4])
            out.append({'id': int.from_bytes(p, 'little'), 'name': name[1:1 + name[0]].decode('utf-8', 'replace'),
                        'mode': mode, 'delay_mode': dmode, 'delay': delay,
                        'events': [struct.unpack('<BBH', e) for e in self._list(0x05, p, 4)]})
        return out

    def set_macro_ids(self, ids):  # SetMacros [total][offset][ids u16]: new ids start empty, missing ones are deleted
        for o in range(0, max(len(ids), 1), 24):
            self.call(0x12, 0x02, struct.pack(f'<HH{len(ids[o:o + 24])}H', len(ids), o, *ids[o:o + 24]))

    def write_macro(self, m):
        p = struct.pack('<H', m['id'])
        name = m['name'][:24].encode()
        self.call(0x12, 0x08, p + bytes([len(name)]) + name)
        self.call(0x12, 0x04, p + struct.pack('<BBH', m['mode'], m['delay_mode'], m['delay']))
        ev, n = b''.join(struct.pack('<BBH', *e) for e in m['events']), len(m['events'])
        for o in range(0, max(n, 1), 12):  # SetMacroEvents [id][total][offset][events], 12 per packet; total 0 = empty
            self.call(0x12, 0x06, p + struct.pack('<HH', n, o) + ev[4 * o:4 * o + 48])

    def set_lighting(self, cfg):  # layer 0 = global effect, rendered by the keyboard itself, survives unplugging
        self.call(0x10, 0x06, b'\0' + cfg)  # SetLayerConfig
        self.call(0x10, 0x02, b'\1')  # SetLightingMode: on-device effect (03 would wait for a PC stream)

    def show_leds(self, leds):
        """Realtime colors {ledId: (r, g, b)}, needs SetLightingMode 03. Same framing as IO Center: at most 40 entries
        `03 [id u16][R G B]` per call, a single `00` commit at the end. The device accepts any id without complaint."""
        e = [b'\3' + struct.pack('<H', i) + bytes(c) for i, c in sorted(leds.items())] + [b'\0']
        for j in range(0, len(e), 40):
            self.call(0x10, 0x0d, bytes([len(e[j:j + 40])]) + b''.join(e[j:j + 40]))

    def set_key_image(self, key, jpeg, w=120, h=120):
        data = struct.pack('<IHHB', 9 + len(jpeg), w, h, 3) + jpeg
        self.call(0x20, 0x02, struct.pack('<HI', key, 0) + data[:9])  # header alone, like IO Center
        for off in range(9, len(data), 248):
            self.call(0x20, 0x02, struct.pack('<HI', key, off) + data[off:off + 248])

    # --- Media Dock (0x21), byte layouts from IO Center's own traffic (PROTOCOL.md, Media Dock) ---
    def dock_connected(self):  # GetState -> [state 01 = connected][pos 01 = left]
        return self.call(0x21, 0x01)[:1] == b'\1'

    def dock_config(self):  # GetConfig -> [R G B][clock 0=12h 1=24h][saver 1=clock 2=image][after s u16][off s u16]
        return self.call(0x21, 0x02)[:9]

    def set_dock_config(self, cfg):
        self.call(0x21, 0x03, cfg)

    def dock_time(self):  # SetDateTime: local wall clock as Unix seconds u64, IO Center sends it once a minute
        self.call(0x21, 0x05, struct.pack('<Q', int(time.time()) + time.localtime().tm_gmtoff))

    def dock_profile(self, name):
        self.call(0x21, 0x09, pascal(name))

    def dock_playback(self, state, pos, dur, artist, album, track):  # state 0 stopped, 1 playing, 2 paused; seconds
        self.call(0x21, 0x0b, struct.pack('<BHH', state, min(pos, 65535), min(dur, 65535))
                  + pascal(artist) + pascal(album) + pascal(track))

    def dock_image(self, kind, im):  # kind 0 = screensaver 320x240 (flash), 1 = cover art 70x70
        data = dock_rgb565(im)
        self.call(0x21, 0x07, struct.pack('<BI', kind, 0) + data[:9])  # [kind][offset u32][chunk], header alone first
        for off in range(9, len(data), 248):
            self.call(0x21, 0x07, struct.pack('<BI', kind, off) + data[off:off + 248])


def pascal(s):  # [len u8][UTF-8], cut on a character boundary
    b = s.encode()[:120].decode('utf-8', 'ignore').encode()
    return bytes([len(b)]) + b


def dock_rgb565(im):  # -> 9-byte header (format 1) + little-endian RGB565 pixels, checked against IO Center's upload
    from PIL import Image, ImageChops
    r, g, b = im.convert('RGB').split()  # pixel = RRRRRGGG GGGBBBBB, low byte first; Pillow has no RGB565 packer
    lo = ImageChops.add(g.point(lambda v: (v >> 2 & 7) << 5), b.point(lambda v: v >> 3))
    hi = ImageChops.add(r.point(lambda v: v >> 3 << 3), g.point(lambda v: v >> 5))
    raw = Image.merge('LA', (lo, hi)).tobytes()
    return struct.pack('<IHHB', 9 + len(raw), im.width, im.height, 1) + raw


EFFECTS = ['Static', 'Color wave', 'Tornado', 'Breathing', 'Reactive', 'Matrix']  # Lightings effect id = index
DIRECTIONS = ['Up', 'Down', 'Left', 'Right', 'Clockwise', 'Counterclockwise']
RAINBOW = ['#ff0000', '#ffff00', '#00ff00', '#00ffff', '#0000ff', '#ff00ff', '#ff0000']


def layer_config(effect, direction, brightness, speed, colors):
    """Lightings layer config: [effect][dir][brightness %][speed %][colorMode] + colors.
    colorMode 00 = one color, 01 = two (Reaktiv: pressed, resting), 02 = gradient [n] n x [R G B pos %]."""
    rgb = [bytes.fromhex(c.lstrip('#')) for c in colors]
    head = bytes([effect, direction, brightness, speed])
    if len(rgb) < 3:
        return head + bytes([len(rgb) - 1]) + b''.join(rgb)
    n = len(rgb)
    return head + bytes([2, n]) + b''.join(c + bytes([min(99, i * 100 // (n - 1))]) for i, c in enumerate(rgb))


def key_jpeg(im):  # PIL image -> JPEG for a display key; the displays sit rotated 90° CCW, so pre-rotate CW
    out = io.BytesIO()
    im.rotate(-90).convert('RGB').save(out, 'JPEG', quality=90, optimize=True)  # optimize: ~30 % fewer bytes, lossless
    return out.getvalue()


def label_jpeg(text, bg):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new('RGB', (120, 120), bg)
    d = ImageDraw.Draw(im)
    d.text((60, 60), text, fill='white', anchor='mm', font=ImageFont.truetype('arialbd.ttf', 56))
    return key_jpeg(im)


def demo(listen=60, raw=False):
    ql = QLink()
    print('Session', 'active' if ql.open_session() else 'NOT active', 'SID', ql.sid)
    info = ql.call(0x03, 0x01)
    print('DeviceInfo', info.hex(), '| SN', ql.call(0x03, 0x02)[1:].decode(), '| Numpad', ql.call(0x20, 0x01).hex())
    colors = ['#c0392b', '#d35400', '#b7950b', '#1e8449', '#117a65', '#1f618d', '#6c3483', '#566573']
    for i, key in enumerate(DISPLAY_KEYS):
        ql.set_key_image(key, label_jpeg(str(i), colors[i]))
        print(f'Image {i} -> key {key:#04x} ok')
    # Macros.StartRecording like IO Center: without it the keyboard reports no key events at all.
    # It streams EVERY keystroke; everything but the display keys is dropped below.
    ql.call(0x12, 0x09, bytes.fromhex('0308090b'))
    print(f'Listening {f"{listen}s" if listen else "without time limit"} for display keys ...', flush=True)
    seen, end, last = set(), time.monotonic() + (listen or float('inf')), 0  # 0 = until all 8 were seen
    while time.monotonic() < end and len(seen) < len(DISPLAY_KEYS):
        if time.monotonic() - last > 1:
            ql.keep_alive()
            last = time.monotonic()
        try:
            _, _, feat, cmd, p = ql.notes.get(timeout=0.2)
        except queue.Empty:
            continue
        if (feat, cmd) == (0x11, 0x02) and int.from_bytes(p[:2], 'little') in DISPLAY_KEYS:  # Bindings.ActionFired
            key = int.from_bytes(p[:2], 'little')
            seen.add(key)
            print(f'Display key {key - 0x6d} pressed (ActionFired {p[2:].hex()})', flush=True)
            continue
        if feat != 0x12 or len(p) < 3:
            if (feat, cmd) != (0x20, 0x02):  # skip image-upload acks
                print(f'Notification {feat:#04x}/{cmd:#04x} {p.hex()}', flush=True)
            continue
        kind, key = p[0], int.from_bytes(p[1:3], 'little')
        if key in DISPLAY_KEYS:
            seen.add(key)
            print(f'Display key {key - 0x6d} ({key:#04x}) {"pressed" if kind == 8 else "released" if kind == 9 else kind}', flush=True)
        elif raw:  # this stream sees every keystroke: other ids only on explicit request
            print(f'Key {key} type {kind}', flush=True)
    print('done, seen:', sorted(k - 0x6d for k in seen))


if __name__ == '__main__':
    # self-check against a captured device frame (Lightings response, SID 2, reqId 0xe0)
    assert crc16(bytes.fromhex('06000200e0100d').ljust(62, b'\0')) == 0x1800
    # layer configs read back from the device (IO Center's rainbow Tornado and its Reactive layer)
    assert layer_config(2, 4, 100, 50, RAINBOW) == bytes.fromhex(
        '020464320207ff000000ffff001000ff002100ffff320000ff42ff00ff53ff000063')
    assert layer_config(4, 0, 100, 40, ['#1e1e1e', '#7e1212']) == bytes.fromhex('04006428011e1e1e7e1212')
    assert layer_config(0, 0, 100, 50, ['#ff2800']) == bytes.fromhex('0000643200ff2800')
    sent = []  # realtime framing like IO Center's 74-LED frames: 40 entries, then 34 + commit
    fake = types.SimpleNamespace(call=lambda f, c, p: sent.append(p))
    QLink.show_leds(fake, {i: (1, 2, 3) for i in range(74)})
    assert [p[0] for p in sent] == [40, 35] and len(sent[0]) == 241 and len(sent[1]) == 206 and sent[1][-1] == 0
    assert sent[0][1:7] == bytes.fromhex('030000010203')
    sent.clear()  # Media Dock frames as IO Center sent them
    fake.call = lambda f, c, p=b'': sent.append((f, c, p))
    QLink.dock_playback(fake, 2, 87, 218, 'Lumaraa', 'Mein Hund', 'Mein Hund')
    QLink.dock_profile(fake, 'Profil 1 (importiert)')
    assert sent == [(0x21, 0x0b, bytes.fromhex('025700da00074c756d61726161094d65696e2048756e64094d65696e2048756e64')),
                    (0x21, 0x09, bytes.fromhex('1550726f66696c20312028696d706f72746965727429'))]
    from PIL import Image
    sent.clear()
    QLink.dock_image(fake, 0, Image.new('RGB', (320, 240), (255, 0, 0)))
    assert sent[0][2] == bytes.fromhex('0000000000' '095802004001f00001') and sent[1][2][:7] == bytes.fromhex('00090000' '0000f8')
    assert sum(len(p) - 5 for *_, p in sent) == 153609 and len(sent) == 1 + 620
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        print('ok')
    elif sys.argv[1:2] == ['unbind']:  # display keys -> Disable (00): no OS action, presses only reach us
        ql = QLink()
        ql.open_session()
        for k in DISPLAY_KEYS:
            ql.set_binding(k, 0)
            print(f'{k:#04x} jetzt', ql.call(0x11, 0x06, struct.pack('<H', k)).hex())  # GetBinding readback
    else:
        demo(int(sys.argv[1]) if len(sys.argv) > 1 else 60, 'raw' in sys.argv)
