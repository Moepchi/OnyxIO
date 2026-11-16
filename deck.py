"""Dark Mount Deck: the 8 display keys like a Stream Deck, plus the keyboard lighting.

Start: pythonw deck.py [--min]   (IO Center must not run: the keyboard allows only one session)
Config: deck.json next to this file, edited in the window. Plugin log: deck.log. Self-check: python deck.py test
"""
import ctypes, ctypes.wintypes as W, io, json, winreg, logging, math, os, queue, struct, subprocess, sys, threading, time, uuid, webbrowser, zipfile, zlib
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps, ImageTk
import darkmount as dm
import lang, sdhost
from lang import LANGS, tr

CFG = lang.HOME / 'deck.json'
ICON = Path(getattr(sys, '_MEIPASS', lang.HOME)) / 'icon.ico'  # bundled into the .exe
COLS = 4  # 2 rows x 4 keys, like on the module
MIN_GAP = 60  # s between two images on one key that nobody asked for (see Device.upload)
DOCK_HOLD = 10  # s a mirrored display key stays on the dock before now playing returns
DOCK = {'color': '#dc4d00', 'h24': True, 'saver': 1, 'after': 30, 'off': 0, 'image': '', 'media': True,
        'pages': True}  # IO Center's factory values
LIGHT = {'effect': 2, 'direction': 4, 'brightness': 100, 'speed': 50, 'colors': dm.RAINBOW}  # what IO Center left
ACTIONS = {  # id: (label, hint)
    'none': (tr('No action'), ''),
    'open': (tr('Open program / file / folder'), tr('Path, e.g. C:\\Windows\\notepad.exe')),
    'url': (tr('Open website'), tr('e.g. https://www.youtube.com')),
    'hotkey': (tr('Send hotkey'), tr('e.g. ctrl+shift+m, win+d, alt+f4, f13')),
    'text': (tr('Type text'), tr('typed at the cursor position')),
    'media': (tr('Media / volume'), ''),
    'cmd': (tr('Run command'), tr('e.g. powershell -File C:\\scripts\\x.ps1')),
    'page_prev': (tr('Previous page'), tr('the last one comes before the first')),
    'page_next': (tr('Next page'), tr('after the last one comes the first again')),
}
PAGE = ('page_prev', 'page_next')
PI_FLAGS = ['--no-first-run', '--no-default-browser-check', '--disable-sync', '--inprivate',
              '--disable-features=msImplicitSignin,msEdgeSyncConsent,msSignInInterception']  # embedded plugin settings: no sign-in or sync dialogs


def top_window(pid):  # visible top-level window of a process
    out, u = [], ctypes.windll.user32

    @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def cb(h, _):
        q = W.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(q))
        if q.value == pid and u.IsWindowVisible(h):
            out.append(h)
        return True
    u.EnumWindows(cb, 0)
    return out[0] if out else None


def page_area(hwnd):  # Chromium's page inside its window: (left, top, extra width, extra height) or None
    found, u = [], ctypes.windll.user32

    @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def cb(c, _):
        n = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(c, n, 64)
        if n.value == 'Chrome_RenderWidgetHostHWND':
            found.append(c)
        return True
    u.EnumChildWindows(hwnd, cb, 0)
    if not found:
        return None
    a, b = W.RECT(), W.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(a))
    u.GetWindowRect(found[0], ctypes.byref(b))
    return b.left - a.left, b.top - a.top, (a.right - a.left) - (b.right - b.left), (a.bottom - a.top) - (b.bottom - b.top)
MEDIA = {'Play/Pause': 0xB3, 'Next track': 0xB0, 'Previous track': 0xB1, 'Stop': 0xB2,  # ids; shown via tr()
         'Mute': 0xAD, 'Volume up': 0xAF, 'Volume down': 0xAE}
VK = {'ctrl': 0x11, 'strg': 0x11, 'shift': 0x10, 'alt': 0x12, 'win': 0x5B, 'enter': 0x0D, 'esc': 0x1B, 'tab': 0x09,
      'space': 0x20, 'backspace': 0x08, 'delete': 0x2E, 'entf': 0x2E, 'insert': 0x2D, 'home': 0x24, 'end': 0x23,
      'pageup': 0x21, 'pagedown': 0x22, 'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28, 'printscreen': 0x2C,
      **{f'f{i}': 0x6F + i for i in range(1, 25)}}
EXTENDED = {0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x5B, 0x2C}
log = logging.getLogger('deck')


def _row(y, x, spec):
    """'Name|Label|width' tokens (Name = IO Center's Key_ name), '_w' = gap. A name seen twice continues the key
    (ISO Enter, numpad + and Enter)."""
    out = []
    for t in spec.split():
        if t[0] == '_':
            x += float(t[1:])
            continue
        n, label, *w = t.split('|')
        out.append(('Key_' + n, label, x, y, float(w[0]) if w else 1))
        x += out[-1][4]
    return out


# ISO-DE layout of the Dark Mount, numpad module on the right (NumpadModule.GetState 01 02); units = key widths
KEYS = [k for y, x, spec in (
    (0, 0, 'Escape|Esc _1 F1|F1 F2|F2 F3|F3 F4|F4 _.5 F5|F5 F6|F6 F7|F7 F8|F8 _.5 F9|F9 F10|F10 F11|F11 F12|F12 '
           '_.25 PrintScreen|Druck ScrollLock|Rollen PauseBreak|Pause'),
    (1.25, 0, 'GraveAccentAndTilde|^ 1|1 2|2 3|3 4|4 5|5 6|6 7|7 8|8 9|9 0|0 MinusAndUnderscore|ß EqualsAndPlus|´ '
              'Backspace|⟵|2 _.25 Insert|Einfg Home|Pos1 PageUp|Bild↑'),
    (2.25, 0, 'Tab|Tab|1.5 Q|Q W|W E|E R|R T|T Y|Z U|U I|I O|O P|P BracketLeft|Ü BracketRight|+ Enter|↵|1.5 '
              '_.25 Delete|Entf End|Ende PageDown|Bild↓'),
    (3.25, 0, 'CapsLock|Caps|1.75 A|A S|S D|D F|F G|G H|H J|J K|K L|L SemicolonAndColon|Ö ApostropheAndDoubleQuote|Ä '
              'NonUsTilde|# Enter||1.25'),
    (4.25, 0, 'LeftShift|⇧|1.25 NonUsBackslash|< Z|Y X|X C|C V|V B|B N|N M|M CommaAndLessThan|, PeriodAndBiggerThan|. '
              'SlashAndQuestionMark|- RightShift|⇧|2.75 _1.25 UpArrow|↑'),
    (5.25, 0, 'LeftCtrl|Strg|1.25 LeftGui|Win|1.25 LeftAlt|Alt|1.25 Space||6.25 RightAlt|AltGr|1.25 RightGui|Win|1.25 '
              'Fn|Fn|1.25 RightCtrl|Strg|1.25 _.25 LeftArrow|← DownArrow|↓ RightArrow|→'),
    (1.25, 20.25, 'NumLock|Num NumpadSlash|/ NumpadAsterisk|* NumpadMinus|-'),
    (2.25, 20.25, 'Numpad7|7 Numpad8|8 Numpad9|9 NumpadPlus|+'),
    (3.25, 20.25, 'Numpad4|4 Numpad5|5 Numpad6|6 NumpadPlus|'),
    (4.25, 20.25, 'Numpad1|1 Numpad2|2 Numpad3|3 NumpadEnter|↵'),
    (5.25, 20.25, 'Numpad0|0|2 NumpadPeriodAndDelete|, NumpadEnter|')) for k in _row(y, x, spec)]
KEYNAMES = list(dict.fromkeys(k[0] for k in KEYS))
# Edge LEDs: the ids IO Center streams for its edge layer (keyboard top/bottom/left, numpad top/bottom/right).
# 128-138 (right keyboard edge under the numpad) and 170 never get a color from it.
FRAMES = {'Frame_Keyboard': (*range(106, 128), *range(139, 170)), 'Frame_Numpad': tuple(range(171, 192))}
M, MT = .55, 1.15  # gap between the keys and the edge LEDs, in key units; top: room for the display keys
FRAME_BOX = {'Frame_Keyboard': (-M, -MT, 18.25 + M, 6.25 + M), 'Frame_Numpad': (20.25 - M, -MT, 24.25 + M, 6.25 + M)}
KEY_LEDS, ALL_LEDS = range(1, 106), 192  # key LEDs 1..105 (id 0 lights nothing), edges 106..191
U, PAD, LED_R = 40, 22, 8  # px per key unit, canvas margin and edge LED radius in the per-key picture
PADY, MINI = 34, 36  # canvas margin above/below; display key size in the picture (px)
KB_W, KB_H = 2 * PAD + (24.25 + 2 * M) * U, 2 * PADY + (6.25 + M + MT) * U  # picture size at scale 1
FPS, WAVE, CENTER = 25, 12, (11.6, 3.1)  # frames/s; key units per color cycle; middle for Tornado
# IO Center profile effects are an index into its std::variant<Breathing, ColorWave, Gif, Image, Matrix,
# MulticolorStatic, Reactive, ScreenCapture, ScreenSync, Sensor, Static, Tornado, Video, Off, Ripple> -> our effect id
PROFILE_FX = {0: 3, 1: 1, 4: 5, 5: 0, 6: 4, 10: 0, 11: 2, 13: 0, 14: 4}
PROFILE_DIR = {'Up': 0, 'Down': 1, 'Left': 2, 'Right': 3, 'Clockwise': 4, 'CounterClockwise': 5}


def _frame_pos():
    """Edge LED id -> (x, y) in key units, from IO Center's own edge stream (a horizontal color wave). Each edge is a
    ring. Keyboard: 106 at the left end, 107-127 along one long side, 128-138 the right side (under the numpad, never
    lit), 139-159 back along the other long side, 160-169 the left side. Numpad: 171-175, right side 176-186, 187-191.
    106-127 is the top, the ring runs clockwise (checked: 'Hoch' runs up the edge)."""
    (x0, y0, x1, y1), (nx0, ny0, nx1, ny1) = FRAME_BOX['Frame_Keyboard'], FRAME_BOX['Frame_Numpad']
    x, nx = (lambda k: x0 + k * (x1 - x0) / 21), (lambda j: nx0 + j * (nx1 - nx0) / 4)
    pos = {106 + k: (x(k), y0) for k in range(22)}
    pos.update({160 - k: (x(k), y1) for k in range(1, 22)})
    pos.update({159 + j: (x0, y1 - j * (y1 - y0) / 11) for j in range(1, 11)})
    pos.update({171 + j: (nx(j), ny0) for j in range(5)})
    pos.update({175 + j: (nx1, ny0 + j * (ny1 - ny0) / 12) for j in range(1, 12)})
    pos.update({191 - j: (nx(j), ny1) for j in range(5)})
    return pos


FRAMEPOS = _frame_pos()


def _frame_names():
    """IO Center's edge LED names (Led_KeyboardTop1 ...) -> LED id.
    ponytail: assumes IO Center counts each side left to right / top to bottom (unchecked; only matters for imported
    profiles that light part of an edge)."""
    sides = {'KeyboardTop': range(107, 128), 'KeyboardBottom': range(139, 160), 'KeyboardLeft': (106, *range(160, 170)),
             'NumpadTop': range(171, 176), 'NumpadBottom': range(187, 192), 'NumpadRight': range(176, 187)}
    out = {}
    for side, ids in sides.items():
        upright = side.endswith(('Left', 'Right'))
        ids = sorted(ids, key=lambda i: FRAMEPOS[i][::-1] if upright else FRAMEPOS[i])
        out.update({f'Led_{side}{n}': i for n, i in enumerate(ids, 1)})
    return out


FRAMELEDS = _frame_names()
# IO Center key name -> USB HID usage, which is what macro events store (checked: Numpad4 = 0x5c)
HID = {**{f'Key_{chr(65 + i)}': 4 + i for i in range(26)}, **{f'Key_{(i + 1) % 10}': 0x1E + i for i in range(10)},
       **{'Key_' + n: c for c, n in enumerate((
           'Enter Escape Backspace Tab Space MinusAndUnderscore EqualsAndPlus BracketLeft BracketRight Backslash '
           'NonUsTilde SemicolonAndColon ApostropheAndDoubleQuote GraveAccentAndTilde CommaAndLessThan '
           'PeriodAndBiggerThan SlashAndQuestionMark CapsLock ' + ' '.join(f'F{i}' for i in range(1, 13)) +
           ' PrintScreen ScrollLock PauseBreak Insert Home PageUp Delete End PageDown RightArrow LeftArrow DownArrow '
           'UpArrow NumLock NumpadSlash NumpadAsterisk NumpadMinus NumpadPlus NumpadEnter ' +
           ' '.join(f'Numpad{i}' for i in range(1, 10)) + ' Numpad0 NumpadPeriodAndDelete NonUsBackslash').split(), 0x28)},
       **{'Key_' + n: 0xE0 + i for i, n in enumerate('LeftCtrl LeftShift LeftAlt LeftGui RightCtrl RightShift RightAlt '
                                                     'RightGui'.split())}}
HIDNAME = {c: n for n, c in HID.items()}
HINT = tr('Recording appends at the end. Several steps: Shift/Ctrl-click.')
MODES, DELAYS = [tr('Once'), tr('Hold (while pressed)'), tr('Repeat')], [tr('None'), tr('Fixed'), tr('As recorded')]


def keylabel(name):
    label = next((k[1] for k in KEYS if k[0] == name and k[1]), '')
    return name[4:] if label in ('', name[4:]) else f'{label} ({name[4:]})'


# Mouse in a macro: one event type 0a carrying the buttons held from then on (read from an IO Center recording:
# left 1, right 2, middle 4, 0 = all up). Side buttons: IO Center records none, so their bits stay unknown.
MOUSE_EV, MOUSE_BTN = 0x0A, {1: tr('Left'), 2: tr('Right'), 4: tr('Wheel')}
MOUSE_VK = {1: 0x01, 2: 0x02, 4: 0x04}  # mask bit -> Windows VK_LBUTTON, VK_RBUTTON, VK_MBUTTON
RECORD = [tr('Keyboard'), tr('Mouse'), tr('Keyboard + mouse')]


def rec_events(rec, keymap):
    """Recorded [(t, LED/key id, down)] and [(t, 'm', button mask)] -> macro events
    [(02 down / 03 up, HID usage, ms until the next one)] or [(0a, mask, ms)]. Keys without a HID usage (Fn) drop."""
    names = {i: n for n, i in keymap.items()}
    rec = [(t, MOUSE_EV, d) if k == 'm' else (t, 2 if d else 3, HID[names[k]])
           for t, k, d in sorted(rec, key=lambda e: e[0]) if k == 'm' or names.get(k) in HID]
    return [(typ, c, min(65535, round(1000 * (rec[j + 1][0] - t))) if j + 1 < len(rec) else 0)
            for j, (t, typ, c) in enumerate(rec)]


def step_text(typ, code, ms):
    if typ == MOUSE_EV:
        what = '🖱  ' + (' + '.join(v for b, v in MOUSE_BTN.items() if code & b) or tr('released'))
    else:
        n = HIDNAME.get(code)
        what = f'{"↓" if typ == 2 else "↑" if typ == 3 else "?"}  {keylabel(n) if n else hex(code)}'
    return f'{what}   {ms} ms'


KEYPOS = {n: (x + w / 2, y + .5) for n, _, x, y, w in reversed(KEYS)}  # reversed: a key's first part wins


def _mix(a, b, f):
    return tuple(round(u + (v - u) * f) for u, v in zip(a, b))


def _at(loop, f):  # color at f (any float, wraps) along a closed loop of colors
    f = f % 1 * (len(loop) - 1)
    i = int(f)
    return _mix(loop[i], loop[i + 1], f - i)


def _prep(L, x, y):
    """Effect + LED position -> what led_color needs every frame."""
    cols = [ImageColor.getrgb(c) for c in L['colors']]
    d, s = L['direction'], L['speed'] / 100
    ang = math.atan2(y - CENTER[1], x - CENTER[0]) / math.tau * (-1 if d == 5 else 1)
    u = {0: -y, 1: y, 2: -x, 3: x}.get(d)
    rest = cols[1] if len(cols) == 2 else (0, 0, 0)
    return {'fx': L['effect'], 'cols': cols, 'rest': rest, 'loop': cols if len(cols) > 2 else [cols[0], rest, cols[0]],
            'b': L['brightness'] / 100, 'rate': .05 + .6 * s, 'fade': 3 - 2.5 * s, 'y': y, 'ang': ang,
            'u': ang if u is None else u / WAVE, 'seed': x * .618 % 1}


def led_color(p, t, hit):
    """One LED at time t (s); hit = time of its key's last press or None. Drawn after the keyboard's own effects."""
    fx, cols = p['fx'], p['cols']
    if fx == 0:  # Statisch; a gradient lies still along the direction
        c = cols[0] if len(cols) < 3 else _at(p['loop'], p['u'])
    elif fx == 1:  # Farbwelle
        c = _at(p['loop'], p['u'] - t * p['rate'])
    elif fx == 2:  # Tornado
        c = _at(p['loop'], p['ang'] - t * p['rate'])
    elif fx == 3:  # Atmen, one color per breath
        ph = t * p['rate'] / 2
        c = _mix((0, 0, 0), cols[int(ph) % len(cols)], (1 - math.cos(math.tau * ph)) / 2)
    elif fx == 4:  # Reaktiv: first color on a press, fades back to the second (or dark)
        k = 1 if hit is None else (t - hit) / p['fade']
        c = p['rest'] if k >= 1 else _mix(cols[0], p['rest'], k)
    else:  # Matrix: one drop per column falls from top to bottom
        k = (t * p['rate'] + p['seed']) % 1 * 10 - 2 - p['y']
        c = _mix(cols[0], p['rest'], k / 3) if 0 <= k < 3 else p['rest']
    return tuple(round(v * p['b']) for v in c)


def light_plan(layers, base, keymap):
    """LED id -> _prep state for every key with an LED and every edge LED. The first layer that holds the key (or edge)
    wins, everything else shows the general effect."""
    owner = {n: L for L in reversed(layers) for n in L['keys']}
    plan = {keymap[n]: _prep(owner.get(n, base), *KEYPOS[n]) for n in KEYNAMES if n in keymap}
    plan.update({i: _prep(owner.get(n, base), *FRAMEPOS[i]) for n, i in FRAMELEDS.items()})
    for i in KEY_LEDS:  # keys not mapped yet would stay dark: general effect, drawn at the middle
        plan.setdefault(i, _prep(base, *CENTER))
    return plan


def read_profile(path):
    """IO Center export (.ioprofile: zip with one JSON file 'data') -> (general effect, layers) of the Dark Mount.
    Layers only when the profile is in mode Custom, as IO Center shows it. Effects only IO Center has (pictures,
    videos, screen, sensors) become Statisch."""
    with zipfile.ZipFile(path) as z:
        props = json.loads(z.read('data'))['data']['properties']
    light = next(p for p in props if p['polymorphic_name'] == 'LightingsProperty')['ptr_wrapper']['data']
    kb = next(s for s in light['subs'] if s['key'] == 'Dark Mount')['value']['ptr_wrapper']['data']

    def effect(v):  # {'index': variant index, 'data': params}, colors as '#AARRGGBB'
        a, m = v['data'], v['data'].get('colorMode')
        cs = ([a['dualColors']['first'], a['dualColors']['second']] if m == 'DualColor' else
              [g['first'] for g in a['gradient']] if m == 'Gradient' else [a.get('singleColor', '#ff000000')])
        return {'effect': PROFILE_FX.get(v['index'], 0), 'direction': PROFILE_DIR.get(a.get('direction'), 3),
                'brightness': int(a.get('brightness', 100)), 'speed': int(a.get('speed', 50)),
                'colors': ['#000000'] if v['index'] == 13 else ['#' + c[-6:] for c in cs]}

    layers = []
    for L in kb['customModeData']['layers'] if kb.get('mode') == 'Custom' else []:
        keys = [n for n in L['assignments'] if n in KEYNAMES or n in FRAMELEDS]
        layers.append({'name': L['name'], 'keys': keys, **effect(L['effectData']['data']['impl'])})
    return effect(kb['generalModeData']['data'][0]['impl']), layers


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('vk', ctypes.c_ushort), ('scan', ctypes.c_ushort), ('flags', ctypes.c_ulong),
                ('time', ctypes.c_ulong), ('extra', ctypes.c_size_t)]


class INPUT(ctypes.Structure):  # type 1 = keyboard; pad up to the larger MOUSEINPUT member of the union
    _fields_ = [('type', ctypes.c_ulong), ('ki', KEYBDINPUT), ('pad', ctypes.c_ubyte * 8)]


def send(events):  # [(vk, scan, flags)]; flags: 1 extended, 2 key up, 4 unicode
    arr = (INPUT * len(events))(*(INPUT(1, KEYBDINPUT(*e)) for e in events))
    ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def parse_hotkey(s):
    vks = []
    for name in s.lower().replace(' ', '').split('+'):
        if name in VK:
            vks.append(VK[name])
        elif len(name) == 1 and name.isascii() and name.isalnum():
            vks.append(ord(name.upper()))
        else:
            raise ValueError(tr('Unknown key in hotkey: "{name}"', name=name))
    return vks


def press(vks):
    ext = [1 if v in EXTENDED else 0 for v in vks]
    send([(v, 0, e) for v, e in zip(vks, ext)] + [(v, 0, e | 2) for v, e in reversed(list(zip(vks, ext)))])


def type_text(text):
    raw = text.encode('utf-16-le')
    units = [int.from_bytes(raw[i:i + 2], 'little') for i in range(0, len(raw), 2)]
    send([e for u in units for e in ((0, u, 4), (0, u, 6))])


def run_action(k):
    a, v = k.get('action', 'none'), k.get('value', '')
    if a == 'open':
        os.startfile(os.path.expandvars(v))
    elif a == 'url':
        webbrowser.open(v)
    elif a == 'hotkey':
        press(parse_hotkey(v))
    elif a == 'text':
        type_text(v)
    elif a == 'media':
        press([MEDIA[v]])
    elif a == 'cmd':
        subprocess.Popen(v, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)


def render(k):
    """Key config -> 120x120 image: optional icon on top, title below (or centered); font shrinks to fit.
    'img' (from a plugin) fills the whole key with the title over its lower part.
    'rot' turns the icon, 'turn' the whole key (degrees clockwise)."""
    bg = k.get('bg') or '#202020'
    r, g, b = ImageColor.getrgb(bg)
    ink = (0, 0, 0) if r * .299 + g * .587 + b * .114 > 150 else (255, 255, 255)
    im = Image.new('RGBA', (120, 120), ink + (0,))  # transparent content layer; ink as base keeps text edges clean
    title, top, stroke = k.get('title', ''), 4, 0
    if k.get('img') is not None:
        im.alpha_composite(k['img'].convert('RGBA').resize((120, 120), Image.Resampling.LANCZOS))
        ink, top, stroke = (255, 255, 255), 80, 2
    elif k.get('icon'):
        try:
            box = 72 if title else 112
            ic = Image.open(k['icon']).convert('RGBA')
            if k.get('rot'):
                ic = ic.rotate(-k['rot'], Image.Resampling.BICUBIC, expand=True)
            ic = ImageOps.contain(ic, (112, box))
            im.alpha_composite(ic, ((120 - ic.width) // 2, 4 + (box - ic.height) // 2))
            top = 80
        except OSError:
            pass
    if title:
        d = ImageDraw.Draw(im)
        for size in range(40, 7, -2):
            font = ImageFont.truetype('arialbd.ttf', size)
            x0, y0, x1, y1 = d.multiline_textbbox((0, 0), title, font=font, align='center', stroke_width=stroke)
            if x1 - x0 <= 112 and y1 - y0 <= 116 - top:
                break
        d.multiline_text((60, (top + 116) // 2), title, font=font, anchor='mm', align='center', fill=ink + (255,),
                         stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    if k.get('turn'):  # around the center; shrinks only as far as needed so no corner gets cut off
        im = im.rotate(-k['turn'], Image.Resampling.BICUBIC, expand=True)
        c, box = im.width / 2, im.getbbox()
        e = max(c - box[0], c - box[1], box[2] - c, box[3] - c) if box else 0
        if e > 60:
            n = round(im.width * 60 / e)
            im = im.resize((n, n), Image.Resampling.LANCZOS)
    out = Image.new('RGB', (120, 120), bg)
    out.paste(im, (60 - im.width // 2, 60 - im.height // 2), im)
    return out


def load():
    try:
        cfg = json.loads(CFG.read_text('utf-8'))
    except (OSError, ValueError):
        cfg = {}
    pages = cfg.get('pages') or [cfg.get('keys', [])]  # one page before pages existed
    cfg.pop('keys', None)
    cfg['pages'] = [blank_page(p) for p in pages]
    cfg['page'] = max(0, min(cfg.get('page', 0), len(cfg['pages']) - 1))
    cfg.setdefault('light', dict(LIGHT))
    cfg['dock'] = {**DOCK, **cfg.get('dock', {})}
    cfg.pop('keycolors', None)  # replaced by layers
    for L in cfg.get('layers', []):  # whole edges became single LEDs
        L['keys'] = [m for n in L['keys'] for m in ([k for k, i in FRAMELEDS.items() if i in FRAMES[n]]
                                                     if n in FRAMES else [n])]
    return cfg


RUN = r'Software\Microsoft\Windows\CurrentVersion\Run'


def autostart(on=None, name='DarkMountDeck'):
    """Windows autostart (per user, starts in the tray). None: report and repoint a moved program; True/False: set."""
    exe = [sys.executable] if lang.FROZEN else [str(Path(sys.executable).with_name('pythonw.exe')), os.path.abspath(__file__)]
    cmd = subprocess.list2cmdline(exe) + ' --min'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN) as k:
        if on is None:
            try:
                on = winreg.QueryValueEx(k, name)[0] != ''
            except OSError:
                return False
        if on:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, name)
            except OSError:
                pass
    return on


def blank_page(keys=()):
    return (list(keys) + [{'title': str(i + 1)} for i in range(len(keys), 8)])[:8]


def save(cfg):
    CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), 'utf-8')


class Device(threading.Thread):
    """Holds the keyboard session: uploads images, runs jobs, reports key presses; reconnects after unplug/standby.
    look(i) -> image for key i; on_key(i, down) runs in this thread."""

    def __init__(self, look, on_key):
        super().__init__(daemon=True)
        self.look, self.on_key, self.state = look, on_key, tr('connecting …')
        self.dirty, self.jobs = queue.Queue(), queue.Queue()  # (key, forced), (fn(ql), text when done)
        self.sent, self.pressed = {}, {}  # key -> (jpeg, time); key -> time of last physical press
        self.lights = None  # fn(t, hits) -> {ledId: rgb}: Deck draws every LED (realtime); None = the keyboard's effect
        self.hits = {}  # key id -> time of its last press, only for Reaktiv; memory only, dropped after 3 s
        self.record = None  # fn(key id, down) while the user records a macro or picks a key for one, else None
        self.dock_name = ''  # Media Dock: profile line (the page), now playing ((state, pos, dur, artist, album, title), art)
        self.dock_media, self.on_dock = None, None  # on_dock(step): dock arrow ◀ / ▶ pressed
        self.dock_keys, self.dock_flash = {}, None  # {key index: label} to mirror on the dock; (label, image, time)

    def run(self):
        while True:
            ql = None
            try:
                ql = dm.QLink()
                if not ql.open_session():
                    raise dm.QLinkError(tr('busy – is IO Center running?'))
                for key in dm.DISPLAY_KEYS:  # presses must not trigger an OS action of their own
                    if ql.call(0x11, 0x06, key.to_bytes(2, 'little'))[2:] != b'\0':
                        ql.set_binding(key, 0)
                # key events need Macros.StartRecording; it streams every keystroke, all but display keys are dropped
                ql.call(0x12, 0x09, bytes.fromhex('0308090b'))
                self.state, last, pending, dock = tr('connected'), 0, dict.fromkeys(range(8), False), {}
                shown, rt, full = {}, False, 0  # LEDs on the keyboard, realtime mode on, last full frame
                while True:
                    while not self.dirty.empty():
                        i, force = self.dirty.get()
                        pending[i] = force or pending.get(i, False)
                    for i in [i for i in pending if self.upload(ql, i, pending[i])]:
                        del pending[i]
                    while not self.jobs.empty():
                        job, done = self.jobs.get()
                        try:
                            job(ql)
                            self.state = tr('connected, {what}', what=done)
                        except dm.QLinkError as e:
                            self.state = tr('connected, error: {e}', e=e)
                        rt = False  # set_lighting left realtime
                    lights, now = self.lights, time.monotonic()
                    self.dock_sync(ql, dock, now)
                    if rt and not lights:
                        ql.call(0x10, 0x02, b'\1')  # back to the keyboard's own effect
                        rt = False
                    if lights and now - last >= 1 / FPS:
                        if not rt:  # realtime: keys Deck does not send stay dark; without us the keyboard drops back
                            ql.call(0x10, 0x02, b'\3')
                            shown, rt = {}, True
                        self.hits = {k: v for k, v in self.hits.items() if now - v < 3}
                        frame = lights(now, self.hits)
                        # only changed LEDs (the keyboard keeps the rest); all of them once a second, also as keep-alive
                        delta = frame if now - full > 1 else {i: c for i, c in frame.items() if shown.get(i) != c}
                        if delta:
                            ql.show_leds(delta)
                            shown.update(delta)
                            full = now if delta is frame else full
                        last = now
                    elif not lights and now - last > 1:
                        ql.keep_alive()
                        last = now
                    try:
                        wait = max(.001, last + 1 / FPS - time.monotonic()) if lights else .05  # picks up uploads soon
                        _, _, feat, cmd, p = ql.notes.get(timeout=wait)
                    except queue.Empty:
                        continue
                    if (feat, cmd) == (0x11, 0x02) and len(p) > 3 and p[3] == 8 and self.on_dock:  # ActionFired, action Profile:
                        self.on_dock({3: 1, 4: -1}.get(p[4] if len(p) > 4 else 0, 0))  # the dock's ▶ (03) / ◀ (04)
                    if feat == 0x12 and len(p) >= 3 and p[0] in (8, 9):  # InputEvent pressed / released
                        key = int.from_bytes(p[1:3], 'little')
                        rec = self.record  # only while the user asked for it (Makros: Aufnehmen / Taste zuweisen)
                        if rec and key not in dm.DISPLAY_KEYS:
                            rec(key, p[0] == 8)
                        if key not in dm.DISPLAY_KEYS:  # any other key: only when it lights up, never stored or logged
                            if p[0] == 8 and self.lights and key in KEY_LEDS:  # key id == LED id (checked)
                                self.hits[key] = time.monotonic()
                        else:
                            if p[0] == 8:
                                self.pressed[key - 0x6d] = time.monotonic()
                            self.on_key(key - 0x6d, p[0] == 8)
            except Exception as e:  # unplugged, standby, or a bad plugin image: never let the session thread die
                if not isinstance(e, (OSError, dm.QLinkError, queue.Empty)):
                    log.exception('device loop')
                self.state = tr('disconnected ({why}), retrying …', why=tr(str(e)) if str(e) else tr('no answer'))
                if ql:
                    ql.close()
                time.sleep(5)

    def dock_sync(self, ql, sent, now):  # Media Dock: only what changed; every minute the clock, like IO Center
        if now - sent.get('clock', -1e9) > 60:  # also notices the dock being attached or taken off
            on = ql.dock_connected()
            if not on or not sent.get('on'):
                sent.clear()
            if on:
                ql.dock_time()
            sent.update(clock=now, on=on)
        if not sent['on']:
            return
        if self.dock_name != sent.get('name'):
            ql.dock_profile(self.dock_name)
            sent['name'] = self.dock_name
        flash = self.dock_flash  # a mirrored display key, shown for DOCK_HOLD s, then back to now playing
        if flash and now - flash[2] < DOCK_HOLD:
            tag = ('flash', flash[2])
            if tag != sent.get('media'):
                ql.dock_playback(1, 0, 0, '', '', flash[0])
                try:
                    ql.dock_image(1, ImageOps.fit(flash[1].convert('RGB'), (70, 70)))
                except dm.QLinkError:
                    pass
                sent['media'], sent['art'] = tag, None  # drop the art cache so now playing redraws its cover afterwards
            return
        m = self.dock_media
        if m and m[0] != sent.get('media'):
            ql.dock_playback(*m[0])
            sent['media'] = m[0]
            if m[1] and m[1] != sent.get('art'):
                sent['art'] = m[1]
                try:  # cover art, not kept over unplugging (IO Center sends it with every track)
                    ql.dock_image(1, ImageOps.fit(Image.open(io.BytesIO(m[1])), (70, 70)))
                except dm.QLinkError:  # IO Center gets InvalidParameter here now and then, too
                    pass

    def upload(self, ql, i, force):
        """True when key i is done, False to try again later.
        ponytail: the keys keep their images across unplugging, so they sit in the keyboard's flash and every upload
        is a flash write. Unchanged images are never sent, and a key gets at most one image per MIN_GAP unless the user
        asked for it (Übernehmen) or pressed it in the last 3 s. Plugins that redraw every second (clocks, CPU meters)
        therefore update once a minute; raise MIN_GAP to spare the flash further."""
        now = time.monotonic()
        old, t = self.sent.get(i, (None, -1e9))
        if not force and now - t < MIN_GAP and now - self.pressed.get(i, -1e9) > 3:
            return False
        img = self.look(i)
        jpeg = dm.key_jpeg(img)
        if jpeg != old:
            ql.set_key_image(0x6d + i, jpeg)
            self.sent[i] = (jpeg, now)
            if i in self.dock_keys:  # a mirrored key changed: show it on the dock (dock_sync does the sending)
                self.dock_flash = (self.dock_keys[i], img, now)
        return True


def watch_media(dev, enabled):
    """Thread: Windows' "now playing" (Spotify, browser, VLC, …) -> dev.dock_media, like IO Center does. Polls once a
    second; a new entry only when track or play state change or the position jumps (the dock counts on by itself)."""
    try:
        import asyncio, datetime
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        from winrt.windows.storage.streams import Buffer, InputStreamOptions
    except ImportError:
        return log.info('now playing: winrt packages missing')

    async def art(p):
        if not p.thumbnail:
            return None
        st = await p.thumbnail.open_read_async()
        buf = Buffer(st.size)
        await st.read_async(buf, st.size, InputStreamOptions.READ_AHEAD)
        return bytes(buf)

    async def run():
        mgr, last, at = await Manager.request_async(), None, 0
        while True:
            try:
                s = enabled() and mgr.get_current_session()
                info = (0, 0, 0, '', '', '')
                if s:
                    p, tl = await s.try_get_media_properties_async(), s.get_timeline_properties()
                    state = {4: 1, 5: 2}.get(int(s.get_playback_info().playback_status), 0)  # Playing, Paused -> 1, 2
                    pos = tl.position.total_seconds()
                    if state == 1:  # the app reports the position only now and then
                        pos += (datetime.datetime.now(datetime.timezone.utc) - tl.last_updated_time).total_seconds()
                    info = (state, int(pos), int(tl.end_time.total_seconds()), p.artist, p.album_title, p.title)
                now = time.monotonic()
                guess = last[0][1] + (now - at if last[0][0] == 1 else 0) if last else 0
                if not last or info[:1] + info[2:] != last[0][:1] + last[0][2:] or abs(info[1] - guess) > 3:
                    cover = last[1] if last and info[3:] == last[0][3:] else s and await art(p)
                    last, at = (info, cover), now
                    dev.dock_media = last
            except Exception as e:  # player closed mid-query and the like: try again next second
                log.info('now playing: %r', e)
            await asyncio.sleep(1)

    try:
        asyncio.run(run())
    except Exception:  # never worth a crash: the dock just stops showing it
        log.exception('now playing')


def dock_conf(d):  # -> the dock's 9 config bytes [R G B][clock 1=24h][saver 1=clock 2=image][after s u16][off s u16]
    return bytes.fromhex(d['color'][1:]) + struct.pack('<BBHH', d['h24'], d['saver'], d['after'], d['off'])


def dock_picture(path):  # -> 320x240, cropped to fill; None without a usable file
    try:
        return ImageOps.fit(Image.open(path).convert('RGB'), (320, 240)) if path else None
    except OSError:
        return None


BG, FIELD, LINE, FG, DIM, SEL ='#1e1e1e', '#2b2b2b', '#3c3c3c', '#e0e0e0', '#7a7a7a', '#4a8fe7'


def dark(root):
    """Dark look: ttk 'clam' recolored, classic tk widgets via the option database, dark Windows title bar."""
    s = ttk.Style(root)
    s.theme_use('clam')
    s.configure('.', background=BG, foreground=FG, fieldbackground=FIELD, bordercolor=LINE, lightcolor=BG,
                darkcolor=BG, troughcolor=FIELD, arrowcolor=FG, insertcolor=FG, selectbackground=SEL,
                selectforeground='white')
    for style in ('TButton', 'TCombobox'):
        s.configure(style, background=FIELD, lightcolor=FIELD, darkcolor=FIELD)
        s.map(style, background=[('disabled', BG), ('pressed', LINE), ('active', LINE)],
              foreground=[('disabled', DIM)], lightcolor=[('pressed', LINE)], darkcolor=[('pressed', LINE)])
    s.map('TCombobox', fieldbackground=[('disabled', BG), ('readonly', FIELD)],
          selectbackground=[('readonly', FIELD)], selectforeground=[('readonly', FG)])
    s.configure('TNotebook', background=BG, bordercolor=LINE)
    s.configure('TNotebook.Tab', background=FIELD, foreground=DIM, bordercolor=LINE, lightcolor=FIELD, padding=(14, 4))
    s.map('TNotebook.Tab', background=[('selected', BG)], foreground=[('selected', FG)], lightcolor=[('selected', BG)])
    for k, v in {'*Text.background': FIELD, '*Text.foreground': FG, '*Text.insertBackground': FG,
                 '*Text.selectBackground': SEL, '*Text.relief': 'flat', '*Text.highlightThickness': 1,
                 '*Text.highlightBackground': LINE, '*Text.highlightColor': SEL,
                 '*Scale.background': BG, '*Scale.foreground': FG, '*Scale.troughColor': FIELD,
                 '*Scale.activeBackground': LINE, '*Scale.highlightThickness': 0, '*Button.highlightThickness': 0,
                 '*TCombobox*Listbox.background': FIELD, '*TCombobox*Listbox.foreground': FG,
                 '*TCombobox*Listbox.selectBackground': SEL, '*TCombobox*Listbox.selectForeground': 'white'}.items():
        root.option_add(k, v)
    root.configure(bg=BG)
    root.update_idletasks()  # the title bar belongs to the real window, which exists only now
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)  # IMMERSIVE_DARK_MODE


class FxEditor(ttk.Frame):
    """One lighting effect as stored in deck.json: effect, direction, colors, brightness, speed."""
    MODES = [tr('One color'), tr('Two colors (Reactive: press, rest)'), tr('Gradient')]

    def __init__(self, parent, root):
        super().__init__(parent)
        self.root = root
        for r, text in enumerate([tr('Effect'), tr('Direction'), tr('Colors'), '', tr('Brightness %'), tr('Speed %')]):
            ttk.Label(self, text=text).grid(row=r, column=0, sticky='w', padx=(0, 12), pady=4)
        self.fx, self.fdir, self.cmode = (ttk.Combobox(self, state='readonly', values=v)
                                          for v in ([tr(x) for x in dm.EFFECTS], [tr(x) for x in dm.DIRECTIONS],
                                                    self.MODES))
        for r, box in enumerate((self.fx, self.fdir, self.cmode)):
            box.grid(row=r, column=1, sticky='we', pady=4)
        self.cmode.bind('<<ComboboxSelected>>', lambda e: self.widgets())
        f = ttk.Frame(self)
        f.grid(row=3, column=1, sticky='w')
        self.lc_btn = [tk.Button(f, width=6, command=lambda j=j: self.pick(j)) for j in range(2)]
        for b in self.lc_btn:
            b.pack(side='left', padx=(0, 6))
        self.bright, self.speed = tk.IntVar(), tk.IntVar()
        for r, var in ((4, self.bright), (5, self.speed)):
            tk.Scale(self, from_=0, to=100, orient='horizontal', length=300, variable=var).grid(row=r, column=1, sticky='w')

    def set(self, L):
        c = L['colors']
        self.fx.current(L['effect'])
        self.fdir.current(L['direction'])
        self.cmode.current(min(len(c), 3) - 1)
        own = c if len(c) < 3 else []
        self.lc = own + ['#ff2800', '#ffffff'][len(own):]  # the two pickable colors
        self.grad = c if len(c) > 2 else dm.RAINBOW  # an imported gradient stays as it is
        self.bright.set(L['brightness'])
        self.speed.set(L['speed'])
        self.widgets()

    def get(self):
        n = self.cmode.current()
        return {'effect': self.fx.current(), 'direction': self.fdir.current(), 'brightness': self.bright.get(),
                'speed': self.speed.get(), 'colors': list(self.grad) if n == 2 else self.lc[:n + 1]}

    def widgets(self):
        n = self.cmode.current()
        for j, b in enumerate(self.lc_btn):
            on = n != 2 and j <= n
            b.config(bg=self.lc[j] if on else FIELD, activebackground=self.lc[j] if on else FIELD,
                     state='normal' if on else 'disabled')

    def pick(self, j):
        c = colorchooser.askcolor(self.lc[j], parent=self.root)[1]
        if c:
            self.lc[j] = c
            self.widgets()


class App:
    def __init__(self, root, connect=True):
        dark(root)
        self.root, self.cfg, self.sel, self.photos, self.ui = root, load(), 0, [None] * 8, queue.Queue()
        self.restart = False  # language switch: start again once closed
        self.dragged = self.ghost = self.pi_proc = self.pi_ctx = None
        self.host = sdhost.Host(self.cfg, self.ui.put)  # plugins report back through the UI queue
        self.choices = {label: (a, None) for a, (label, _) in ACTIONS.items()}  # label -> (action id, plugin action)
        self.choices.update({f'Plugin – {label}': ('plugin', au) for label, au in self.host.choices()})
        self.bg, self.icon, self.val, self.rot, self.turn = '#202020', '', tk.StringVar(), tk.IntVar(), tk.IntVar()
        root.title('Dark Mount Deck')
        try:
            root.iconbitmap(default=ICON)
        except tk.TclError:
            pass
        root.protocol('WM_DELETE_WINDOW', root.withdraw)  # keeps running in the tray like the Stream Deck app
        self.tray = None
        nb = ttk.Notebook(root)
        nb.grid(row=0, column=0, columnspan=2, sticky='nsew', padx=10, pady=(10, 0))
        tab = ttk.Frame(nb)
        nb.add(tab, text=tr('Keys'))
        tiles = ttk.Frame(tab, padding=10)
        tiles.grid(row=0, column=0, sticky='n')
        self.tiles = [tk.Button(tiles, bd=3, relief='flat', bg=LINE, command=lambda i=i: self.select(i)) for i in range(8)]
        for i, b in enumerate(self.tiles):
            b.grid(row=i // COLS, column=i % COLS, padx=3, pady=3)
        f = ttk.Frame(tiles)
        f.grid(row=2, column=0, columnspan=COLS, pady=(8, 0))
        ttk.Button(f, text='◀', width=3, command=lambda: self.goto(self.cfg['page'] - 1)).pack(side='left')
        self.page_lbl = ttk.Label(f, width=12, anchor='center')
        self.page_lbl.pack(side='left')
        ttk.Button(f, text='▶', width=3, command=lambda: self.goto(self.cfg['page'] + 1)).pack(side='left')
        ttk.Button(f, text=tr('New page'), command=self.add_page).pack(side='left', padx=(16, 0))
        ttk.Button(f, text=tr('Delete page'), command=self.del_page).pack(side='left')
        f = ttk.Frame(tab, padding=(10, 0))
        f.grid(row=1, column=0, sticky='nsew')
        ttk.Label(f, text=tr('Plugin settings'), foreground=DIM).pack(anchor='w', pady=(0, 4))
        self.pi = tk.Frame(f, bg=FIELD, width=530, height=290, highlightthickness=1, highlightbackground=LINE)
        self.pi.pack(fill='both', expand=True)
        self.pi_lbl = tk.Label(self.pi, bg=FIELD, fg=DIM, wraplength=440)
        self.pi_lbl.place(relx=.5, rely=.5, anchor='center')
        f = ttk.Frame(tab, padding=(10, 0))
        f.grid(row=1, column=1, sticky='nsew')
        ttk.Label(f, text=tr('Actions – drag onto a key or double-click'), foreground=DIM).pack(anchor='w',
                                                                                                      pady=(0, 4))
        self.tree = ttk.Treeview(f, show='tree', selectmode='browse')
        sb = ttk.Scrollbar(f, command=self.tree.yview)
        self.tree.config(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        self.fill_tree()
        ed = ttk.Frame(tab, padding=10)
        ed.grid(row=0, column=1, sticky='n')
        self.head = ttk.Label(ed, font=('Segoe UI', 12, 'bold'))
        self.head.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 8))
        for r, text in enumerate([tr('Title'), tr('Background'), tr('Image'), tr('Rotate image'), tr('Rotate all'), tr('Action'),
                                  tr('Target / value')], 1):
            ttk.Label(ed, text=text).grid(row=r, column=0, sticky='nw', padx=(0, 8), pady=3)
        self.title = tk.Text(ed, width=34, height=2, font=('Segoe UI', 10))
        self.title.grid(row=1, column=1, columnspan=2, sticky='we', pady=3)
        self.title.bind('<KeyRelease>', lambda e: self.preview())
        self.bg_btn = tk.Button(ed, width=6, command=self.pick_bg)
        self.bg_btn.grid(row=2, column=1, sticky='w', pady=3)
        self.icon_lbl = ttk.Label(ed, width=20)
        self.icon_lbl.grid(row=3, column=1, sticky='w')
        f = ttk.Frame(ed)
        f.grid(row=3, column=2, sticky='e')
        ttk.Button(f, text=tr('Choose …'), command=self.pick_icon).pack(side='left')
        ttk.Button(f, text=tr('Remove'), command=lambda: (self.set_icon(''), self.preview())).pack(side='left')
        for row, var in ((4, self.rot), (5, self.turn)):
            f = ttk.Frame(ed)
            f.grid(row=row, column=1, columnspan=2, sticky='w')
            tk.Scale(f, from_=-180, to=180, orient='horizontal', length=200, variable=var,
                     command=lambda v, var=var: self.snap(var, int(v))).pack(side='left')
            for deg, text in ((-90, '⟲ 90°'), (90, '⟳ 90°')):
                ttk.Button(f, text=text, width=6, command=lambda v=var, d=deg: self.step(v, d)).pack(side='left', anchor='s')
        self.action = ttk.Combobox(ed, state='readonly', values=list(self.choices), height=24)
        self.action.grid(row=6, column=1, columnspan=2, sticky='we', pady=3)
        self.action.bind('<<ComboboxSelected>>', lambda e: (self.val.set(''), self.update_value_widget(), self.preview()))
        self.value = ttk.Combobox(ed, textvariable=self.val)
        self.value.grid(row=7, column=1, columnspan=2, sticky='we', pady=3)
        f = ttk.Frame(ed)
        f.grid(row=8, column=1, columnspan=2, sticky='w')
        self.browse = [ttk.Button(f, text=tr('File …'), command=lambda: self.pick_target(filedialog.askopenfilename)),
                       ttk.Button(f, text=tr('Folder …'), command=lambda: self.pick_target(filedialog.askdirectory))]
        for b in self.browse:
            b.pack(side='left')
        self.hint = ttk.Label(ed, foreground='gray')
        self.hint.grid(row=9, column=1, columnspan=2, sticky='w')
        self.kdock = tk.BooleanVar()
        ttk.Checkbutton(ed, text=tr('Mirror on the Media Dock'), variable=self.kdock).grid(
            row=10, column=1, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Button(ed, text=tr('Apply'), command=lambda: self.apply() and self.pi_show()).grid(row=11, column=2, sticky='e', pady=(12, 0))
        n = len(self.host.plugins)
        ttk.Label(tab, foreground='gray', padding=(10, 0, 10, 8), text=tr(
            'Stream Deck plugins loaded: {n}, skipped: {skipped} (encrypted Marketplace plugins, list in deck.log; '
            'own plugins go into the “plugins” folder)', n=n, skipped=len(self.host.skipped))).grid(
            row=2, column=0, columnspan=2, sticky='w')
        self.build_keys(nb)
        self.build_macros(nb)
        self.build_dock(nb)
        self.status = ttk.Label(root, padding=(10, 0))
        self.status.grid(row=1, column=0, sticky='w', pady=10)
        f = ttk.Frame(root)
        f.grid(row=1, column=1, sticky='e', padx=10, pady=10)
        box = ttk.Combobox(f, state='readonly', values=list(LANGS.values()), width=9)
        box.set(LANGS[lang.LANG])
        box.bind('<<ComboboxSelected>>', lambda e: self.set_lang(list(LANGS)[box.current()]))
        box.pack(side='left', padx=(0, 8))
        self.auto = tk.BooleanVar(value=autostart())
        ttk.Checkbutton(f, text=tr('Start with Windows'), variable=self.auto,
                        command=lambda: autostart(self.auto.get())).pack(side='left', padx=(0, 12), before=box)
        ttk.Button(f, text=tr('Quit'), command=root.destroy).pack(side='left')
        self.dev = Device(lambda i: render(self.look(self.keys[i])), self.on_key)
        self.dev.on_dock = lambda step: step and self.cfg['dock']['pages'] and self.ui.put(
            lambda: self.goto(self.cfg['page'] + step))
        self.push_lights()
        self.load_macros()  # waits in the job queue until the keyboard is there
        if connect:
            log.info('plugins: %s; skipped: %s', sorted(self.host.plugins), self.host.skipped)
            self.host.start()
            self.dev.start()
            threading.Thread(target=watch_media, args=(self.dev, lambda: self.cfg['dock']['media']), daemon=True).start()
        for i in range(8):
            self.draw_tile(i)
        self.show_page()
        self.select(0)
        root.resizable(False, False)  # fixed size: everything visible, nothing to scale
        self.poll()

    # --- Media Dock: color, clock, idle screen and image sit in the dock's flash and go there only with Apply;
    # time, page name and now playing are sent live by Device.dock_sync
    def build_dock(self, nb):
        t = ttk.Frame(nb, padding=10)
        nb.add(t, text='Media Dock')
        d, ed = self.cfg['dock'], ttk.Frame(t)
        ed.grid(row=0, column=0, sticky='nw')
        for r, text in enumerate([tr('Accent color'), tr('Clock'), tr('Idle screen'), tr('Show idle screen after'),
                                  tr('Turn display off after'), tr('Idle image')]):
            ttk.Label(ed, text=text).grid(row=r, column=0, sticky='w', padx=(0, 12), pady=4)
        self.dk_color = tk.Button(ed, width=6, command=self.pick_dock_color)
        self.dk_color.grid(row=0, column=1, sticky='w')
        self.dk_clock = ttk.Combobox(ed, state='readonly', values=['24h', '12h'], width=10)
        self.dk_saver = ttk.Combobox(ed, state='readonly', values=[tr('Clock'), tr('Image')], width=10)
        self.dk_after = ttk.Spinbox(ed, from_=5, to=3600, increment=5, width=8)
        self.dk_off = ttk.Spinbox(ed, from_=0, to=3600, increment=10, width=8)
        for r, w in enumerate((self.dk_clock, self.dk_saver, self.dk_after, self.dk_off), 1):
            w.grid(row=r, column=1, sticky='w', pady=4)
        ttk.Label(ed, text='s').grid(row=3, column=2, sticky='w')
        ttk.Label(ed, text=tr('s (0 = never)')).grid(row=4, column=2, sticky='w')
        f = ttk.Frame(ed)
        f.grid(row=5, column=1, columnspan=2, sticky='w')
        ttk.Button(f, text=tr('Choose …'), command=self.pick_dock_image).pack(side='left')
        ttk.Button(f, text=tr('Remove'), command=lambda: self.set_dock_image('')).pack(side='left')
        self.dk_img = tk.Label(ed, bg=FIELD)
        self.dk_img.grid(row=6, column=1, columnspan=2, sticky='w', pady=4)
        self.dk_media, self.dk_pages = tk.BooleanVar(value=d['media']), tk.BooleanVar(value=d['pages'])
        ttk.Checkbutton(ed, text=tr('Show what is playing (Spotify, browser, …)'), variable=self.dk_media,
                        command=self.dock_switches).grid(row=7, column=0, columnspan=3, sticky='w', pady=(8, 0))
        ttk.Checkbutton(ed, text=tr('Dock arrows ◀ ▶ switch pages, the page is shown as profile'),
                        variable=self.dk_pages, command=self.dock_switches).grid(row=8, column=0, columnspan=3, sticky='w')
        ttk.Button(ed, text=tr('Apply'), command=self.apply_dock).grid(row=9, column=2, sticky='e', pady=(12, 0))
        ttk.Label(t, foreground='gray', wraplength=380, text=tr(
            'Color, clock, idle screen and image are stored on the dock and written only with Apply. '
            'Time, page and what is playing are sent while Deck runs.')).grid(row=0, column=1, sticky='nw', padx=(24, 0))
        self.dk_color.config(bg=d['color'], activebackground=d['color'])
        self.dk_clock.current(0 if d['h24'] else 1)
        self.dk_saver.current(d['saver'] - 1)
        self.dk_after.set(d['after'])
        self.dk_off.set(d['off'])
        self.set_dock_image(d['image'])

    def pick_dock_color(self):
        c = colorchooser.askcolor(self.dk_color.cget('bg'), parent=self.root)[1]
        if c:
            self.dk_color.config(bg=c, activebackground=c)

    def pick_dock_image(self):
        p = filedialog.askopenfilename(parent=self.root, filetypes=[
            (tr('Images'), '*.png *.jpg *.jpeg *.gif *.bmp *.ico *.webp'), (tr('All files'), '*.*')])
        if p:
            self.set_dock_image(os.path.normpath(p))
            self.dk_saver.current(1)

    def set_dock_image(self, path):
        self.dk_path, im = path, dock_picture(path)
        self.dk_photo = ImageTk.PhotoImage(im.resize((160, 120)) if im else Image.new('RGB', (160, 120), FIELD))
        self.dk_img.config(image=self.dk_photo)

    def dock_switches(self):  # take effect at once, nothing goes to the dock's flash
        self.cfg['dock'].update(media=self.dk_media.get(), pages=self.dk_pages.get())
        save(self.cfg)
        self.show_page()

    def apply_dock(self):
        d = self.cfg['dock']
        try:
            after, off = int(self.dk_after.get()), int(self.dk_off.get())
            assert 5 <= after <= 3600 and 0 <= off <= 3600
        except (ValueError, AssertionError):
            return messagebox.showerror('Dark Mount Deck', tr('Times: 5 – 3600 s, display off 0 – 3600 s.'),
                                        parent=self.root)
        d.update(color=self.dk_color.cget('bg'), h24=self.dk_clock.current() == 0, saver=self.dk_saver.current() + 1,
                 after=after, off=off, image=self.dk_path)
        self.dock_switches()  # also saves
        conf = dock_conf(d)
        im = dock_picture(d['image'])
        crc = im and zlib.crc32(im.tobytes())
        new = im and crc != d.get('sent')  # the 150 KB image only when it changed: it goes into flash

        def job(ql):
            if ql.dock_config() != conf:
                ql.set_dock_config(conf)
            if new:
                ql.dock_image(0, im)
                self.ui.put(lambda: (d.update(sent=crc), save(self.cfg)))
        self.dev.jobs.put((job, tr('Media Dock settings applied')))

    # --- lighting: the base effect (bottom of the layer list) lives on the keyboard, the layers above it come from Deck
    def apply_light(self):
        L = self.cfg['light']
        save(self.cfg)
        data = dm.layer_config(**L)
        self.dev.jobs.put((lambda ql: ql.set_lighting(data), tr('Lighting applied')))
        self.push_lights()

    # --- layers: Deck draws every LED in realtime mode while it runs, the keyboard falls back to the general effect ---
    def build_keys(self, nb):
        t = ttk.Frame(nb, padding=10)
        nb.add(t, text=tr('Lighting'))
        self.cal, self.newmap, self.lsel = None, {}, None  # LED walk position, its map, selected layer index
        self.layers = self.cfg.setdefault('layers', [])
        self.kb = tk.Canvas(t, width=KB_W, height=KB_H, bg=BG, highlightthickness=0)
        self.kb.grid(row=0, column=0, columnspan=2, sticky='w')
        self.k, self.ox, self.mini_src = 1, 0, [None] * 8  # picture scale and x offset (fixed now)
        for j, ev in enumerate(('<ButtonPress-1>', '<B1-Motion>', '<ButtonRelease-1>')):
            self.kb.bind(ev, lambda e, j=j: self.drag(e, j))
        self.toolbar, self.calbar = ttk.Frame(t), ttk.Frame(t)
        for bar, buttons in ((self.toolbar, ((tr('All / none'), self.select_all_keys), (tr('Map LEDs …'), self.calibrate),
                                             (tr('Import IO Center profile …'), self.import_profile))),
                             (self.calbar, ((tr('Back'), lambda: self.cal_step(-1)), (tr('Next'), lambda: self.cal_step(1)),
                                            (tr('Done'), self.cal_done)))):
            bar.grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 0))
            for text, cmd in buttons:
                ttk.Button(bar, text=text, command=cmd).pack(side='left', padx=(0, 6))
        self.calbar.grid_remove()
        self.khint = ttk.Label(t, foreground='gray', wraplength=2 * PAD + 24.25 * U, justify='left')
        self.khint.grid(row=2, column=0, columnspan=2, sticky='w', pady=(8, 0))
        left = ttk.Frame(t)
        left.grid(row=3, column=0, sticky='nw', pady=(12, 0), padx=(0, 24))
        self.llist = tk.Listbox(left, width=28, height=8, bg=FIELD, fg=FG, selectbackground=SEL, highlightthickness=0,
                                relief='flat', exportselection=False, activestyle='none')
        self.llist.grid(row=0, column=0, columnspan=4, sticky='we')
        self.llist.bind('<<ListboxSelect>>', lambda e: self.select_layer(next(iter(self.llist.curselection()), None)))
        for j, (text, cmd) in enumerate((('+', self.add_layer), ('−', self.del_layer),
                                         ('↑', lambda: self.move_layer(-1)), ('↓', lambda: self.move_layer(1)))):
            ttk.Button(left, text=text, width=4, command=cmd).grid(row=1, column=j, sticky='w', pady=(6, 0))
        ttk.Label(left, foreground='gray', text=tr('The top one wins, the base effect fills the rest.')).grid(
            row=2, column=0, columnspan=4, sticky='w')
        self.ledit = ttk.Frame(t)
        self.ledit.grid(row=3, column=1, sticky='nw', pady=(12, 0))
        ttk.Label(self.ledit, text='Name').grid(row=0, column=0, sticky='w', padx=(0, 12), pady=4)
        self.lname = ttk.Entry(self.ledit, width=30)
        self.lname.grid(row=0, column=1, sticky='w', pady=4)
        self.layer_ed = FxEditor(self.ledit, self.root)
        self.layer_ed.grid(row=1, column=0, columnspan=2, sticky='w')
        ttk.Button(self.ledit, text=tr('Apply'), command=self.apply_layer).grid(row=2, column=1, sticky='e', pady=(12, 0))
        self.mini_photos = [None] * 8  # the display keys above the numpad, as in the "Tasten" tab; click = edit there
        for i in range(8):
            self.kb.tag_bind(f'disp{i}', '<Button-1>', lambda e, i=i: (nb.select(0), self.select(i)))
        self.fill_layers()

    def cur(self):  # the selected layer, None for the base effect
        return self.layers[self.lsel] if self.lsel is not None and self.lsel < len(self.layers) else None

    def fill_layers(self, sel=None):
        self.llist.delete(0, 'end')
        for L in self.layers:
            self.llist.insert('end', f'{L["name"]}  ({tr(dm.EFFECTS[L["effect"]])}, {len(L["keys"])})')
        self.llist.insert('end', tr('Base effect  ({fx}, rest)', fx=tr(dm.EFFECTS[self.cfg['light']['effect']])))
        self.select_layer(sel if sel is not None and sel <= len(self.layers) else 0)

    def select_layer(self, j):
        if j is None:  # the list never ends up empty: the base effect is always there
            return
        self.lsel = j
        L = self.cur()  # not in one tuple assignment: cur() would still see the old lsel
        self.llist.selection_clear(0, 'end')
        self.llist.selection_set(j)
        self.lname.config(state='normal')
        self.lname.delete(0, 'end')
        self.lname.insert(0, L['name'] if L else tr('Base effect'))
        self.lname.config(state='normal' if L else 'disabled')
        self.layer_ed.set(L or self.cfg['light'])
        self.draw_keys()

    def layers_changed(self, sel=None):
        save(self.cfg)
        self.fill_layers(self.lsel if sel is None else sel)
        self.push_lights()

    def add_layer(self):
        self.layers.insert(0, {'name': tr('Layer {n}', n=len(self.layers) + 1), 'keys': [], 'effect': 0, 'direction': 3,
                               'brightness': 100, 'speed': 50, 'colors': ['#ffffff']})
        self.layers_changed(0)

    def del_layer(self):
        if self.cur():
            del self.layers[self.lsel]
            self.layers_changed(max(0, self.lsel - 1))

    def move_layer(self, d):
        j = self.lsel
        if self.cur() and 0 <= j + d < len(self.layers):
            self.layers[j], self.layers[j + d] = self.layers[j + d], self.layers[j]
            self.layers_changed(j + d)

    def apply_layer(self):
        if not self.cur():
            self.cfg['light'] = self.layer_ed.get()
            self.apply_light()  # also saves and pushes the layers
            return self.fill_layers(self.lsel)
        self.cur().update(self.layer_ed.get(), name=self.lname.get().strip() or tr('Layer'))
        self.layers_changed()

    def import_profile(self):
        p = filedialog.askopenfilename(parent=self.root, filetypes=[(tr('IO Center profile'), '*.ioprofile')])
        if not p:
            return
        try:
            light, layers = read_profile(p)
        except (OSError, KeyError, StopIteration, ValueError, zipfile.BadZipFile) as e:
            return messagebox.showerror('Dark Mount Deck', tr('Cannot read profile: {e}', e=repr(e)), parent=self.root)
        self.layers[:] = layers
        self.cfg['light'] = light
        self.apply_light()  # also saves and pushes the layers
        self.fill_layers(0)

    def kx(self, v):  # key units -> canvas px
        return self.ox + (PAD + (v + M) * U) * self.k

    def ky(self, v):
        return (PADY + (v + MT) * U) * self.k

    def draw_mini(self, i):
        n = max(8, round(MINI * self.k))
        if self.mini_src[i] is not None:
            self.mini_photos[i] = ImageTk.PhotoImage(self.mini_src[i].resize((n, n), Image.Resampling.LANCZOS))
            self.kb.itemconfig(f'dimg{i}', image=self.mini_photos[i])

    def draw_keys(self):
        c, m = self.kb, self.cfg.get('keymap', {})
        mine = set(self.cur()['keys']) if self.cur() else set()
        owner = {n: L for L in reversed(self.layers) for n in L['keys']}
        base = self.cfg['light']  # everything without a layer shows the base effect
        look = lambda L, x, y: '#%02x%02x%02x' % led_color({**_prep(L, x, y), 'b': 1}, 0, None)  # still, full bright
        cx, cy, k = self.kx, self.ky, self.k
        r, u, mini = LED_R * k, U * k, MINI * k
        c.delete('all')
        for x0, y0, x1, y1 in FRAME_BOX.values():
            c.create_rectangle(cx(x0), cy(y0), cx(x1), cy(y1), outline=LINE)
        mid = (cy(-MT) + r + cy(1.25)) / 2  # between the numpad's top LEDs and its keys
        for i, im in enumerate(self.mini_photos):
            x, y = cx(20.75 + i % COLS), mid + (mini / 2 + 2 * k) * (1 if i >= COLS else -1)
            c.create_rectangle(x - mini / 2 - 1, y - mini / 2 - 1, x + mini / 2, y + mini / 2, fill=FIELD, outline=LINE,
                               tags=(f'disp{i}',))
            c.create_image(x, y, tags=(f'disp{i}', f'dimg{i}'))
            self.draw_mini(i)
        for name, i in FRAMELEDS.items():
            x, y = FRAMEPOS[i]
            c.create_oval(cx(x) - r, cy(y) - r, cx(x) + r, cy(y) + r, fill=look(owner.get(name, base), x, y),
                          outline=SEL if name in mine else LINE, width=max(1, 3 * k) if name in mine else 1, tags=(name,))
        seen = set()
        for name, label, x, y, w in KEYS:
            if self.cal is None:
                fill = look(owner.get(name, base), *KEYPOS[name]) if name in owner or name in m else FIELD
            else:  # blue = mapped to the LED that is lit now, green = mapped
                fill = SEL if self.newmap.get(name) == self.cal else '#2e7d32' if name in self.newmap else FIELD
            x0, y0 = cx(x), cy(y)
            c.create_rectangle(x0 + 2, y0 + (3 if name not in seen else -3), x0 + w * u - 2, y0 + u - 2, fill=fill,
                               outline=SEL if name in mine else fill, width=max(1, 3 * k), tags=(name,))
            if label and name not in seen:
                c.create_text(x0 + w * u / 2, y0 + u / 2, text=label, font=('Segoe UI', max(5, round(10 * k))), tags=(name,),
                              fill='black' if sum(ImageColor.getrgb(fill)) > 380 else FG)
            seen.add(name)
        n = len(KEYNAMES) - len(m)
        self.khint.config(text=(
            tr('LED {led} of {first}–{last} is lit white, everything else is off. Click the key in the picture that is '
               'lit. If none is lit or the blue one is already right: “Next”. Green = mapped ({done} of {total}).',
               led=self.cal, first=KEY_LEDS[0], last=KEY_LEDS[-1], done=len(self.newmap), total=len(KEYNAMES))
            if self.cal is not None else
            tr('No LED map yet: run “Map LEDs …” once (one LED after another, about 3 minutes). The frame already '
               'works.') if not m else
            tr('The keyboard computes the base effect itself and keeps it even without this program. With Static, '
               'Breathing and Reactive the direction does not matter. “+” adds a layer above it; click its keys and '
               'edge LEDs or drag a box around them.') if not self.cur() else
            tr('Click keys and edge LEDs or drag a box around them (Shift = add, Ctrl = toggle; blue outline = in this '
               'layer). The keyboard shows layers only while this program runs, the base effect afterwards.')
            + (tr(' {n} keys have no LED yet.', n=n) if n else '')))

    def tags_at(self, *box):  # key / edge LED names under a point or inside a box on the picture
        return {g for item in self.kb.find_overlapping(*box) for g in self.kb.gettags(item) if g in KEYPOS or g in FRAMELEDS}

    def drag(self, e, phase):
        """Like IO Center: click or drag a box = exactly these in the layer, Shift = add them, Ctrl = switch each."""
        if phase == 0:
            self.drag_from = (e.x, e.y)
            return
        x0, y0 = self.drag_from
        self.kb.delete('band')
        if phase == 1:
            self.kb.create_rectangle(x0, y0, e.x, e.y, outline=FG, dash=(4, 3), tags='band')
            return
        far = abs(e.x - x0) + abs(e.y - y0) > 4
        names = self.tags_at(x0, y0, e.x, e.y) if far else self.tags_at(e.x, e.y, e.x, e.y)
        if self.cal is not None:
            return self.kb_click(next(iter(names), None)) if not far else None
        self.kb_pick(names, 'add' if e.state & 1 else 'toggle' if e.state & 4 else 'set')

    def kb_pick(self, names, how):
        L = self.cur()
        if not L or not names:  # a click into nothing (or on the base effect) changes nothing
            return
        old = set(L['keys'])
        new = names if how == 'set' else old | names if how == 'add' else old ^ names
        L['keys'] = [n for n in (*KEYNAMES, *FRAMELEDS) if n in new]
        self.layers_changed()

    def kb_click(self, name):  # calibration: the key that is lit now
        if name in KEYPOS:
            self.newmap = {k: v for k, v in self.newmap.items() if k != name and v != self.cal}
            self.newmap[name] = self.cal
            self.cal_step(1)

    def select_all_keys(self):
        L = self.cur()
        if L:
            L['keys'] = [] if L['keys'] else [*KEYNAMES, *FRAMELEDS]
            self.layers_changed()

    def push_lights(self):
        if not self.layers:
            self.dev.lights = None
            return
        plan = light_plan(self.layers, self.cfg['light'], self.cfg.get('keymap', {}))
        self.dev.lights = lambda t, hits: {i: led_color(p, t, hits.get(i)) for i, p in plan.items()}

    def calibrate(self):  # light one LED after the other, the user clicks the key that is lit
        self.cal, self.newmap = KEY_LEDS[0], dict(self.cfg.get('keymap', {}))
        self.toolbar.grid_remove()
        self.calbar.grid()
        self.cal_step(0)

    def cal_step(self, d):
        self.cal = max(KEY_LEDS[0], self.cal + d)
        if self.cal > KEY_LEDS[-1]:
            return self.cal_done()
        frame = {**dict.fromkeys(range(ALL_LEDS), (0, 0, 0)), self.cal: (255, 255, 255)}
        self.dev.lights = lambda t, hits: frame
        self.draw_keys()

    def cal_done(self):
        self.cfg['keymap'], self.cal = self.newmap, None
        save(self.cfg)
        self.calbar.grid_remove()
        self.toolbar.grid()
        self.draw_keys()
        self.push_lights()

    # --- keys -------------------------------------------------------------------------------------------------------
    def action_id(self):  # -> (action id, plugin action uuid or None)
        return self.choices.get(self.action.get(), ('none', None))

    @property
    def keys(self):  # the page being shown, on the keyboard and in the editor alike
        return self.cfg['pages'][self.cfg['page']]

    def look(self, k):  # key config -> what render() draws
        if k.get('action') in PAGE and not k.get('title') and not k.get('icon'):
            k = dict(k, title=f'{"◄►"[k["action"] == "page_next"]}\n{self.cfg["page"] + 1}/{len(self.cfg["pages"])}')
        return self.host.decorate(k) if k.get('action') == 'plugin' else k

    # --- macros: stored on the keyboard like IO Center does, played by the keyboard itself, bound to keys there ------
    def build_macros(self, nb):
        t = ttk.Frame(nb, padding=10)
        nb.add(t, text=tr('Macros'))
        self.macros, self.mbind, self.msel, self.steps, self.rec = [], {}, None, [], None
        left = ttk.Frame(t)
        left.grid(row=0, column=0, sticky='nw', padx=(0, 24))
        self.mlist = tk.Listbox(left, width=28, height=14, bg=FIELD, fg=FG, selectbackground=SEL, highlightthickness=0,
                                relief='flat', exportselection=False, activestyle='none')
        self.mlist.grid(row=0, column=0, columnspan=3, sticky='we')
        self.mlist.bind('<<ListboxSelect>>', lambda e: self.select_macro(next(iter(self.mlist.curselection()), None)))
        for j, (text, cmd) in enumerate((('+', self.add_macro), ('−', self.del_macro), (tr('Reload'), self.load_macros))):
            ttk.Button(left, text=text, width=4 if j < 2 else 10, command=cmd).grid(row=1, column=j, sticky='w',
                                                                                   pady=(6, 0))
        self.medit = ed = ttk.Frame(t)
        ed.grid(row=0, column=1, sticky='nw')
        for r, text in enumerate(['Name', tr('Playback'), tr('Delays'), tr('Steps'), '', tr('Keys')]):
            ttk.Label(ed, text=text).grid(row=r, column=0, sticky='nw', padx=(0, 12), pady=4)
        self.mname = ttk.Entry(ed, width=30)
        self.mname.grid(row=0, column=1, sticky='w', pady=4)
        self.mmode = ttk.Combobox(ed, state='readonly', values=MODES, width=28)
        self.mmode.grid(row=1, column=1, sticky='w', pady=4)
        f = ttk.Frame(ed)
        f.grid(row=2, column=1, sticky='w', pady=4)
        self.mdelay = ttk.Combobox(f, state='readonly', values=DELAYS, width=16)
        self.mdelay.pack(side='left')
        self.mms = ttk.Spinbox(f, from_=0, to=65535, increment=10, width=7)
        self.mms.pack(side='left', padx=6)
        ttk.Label(f, text=tr('ms (with “Fixed”)')).pack(side='left')
        f = ttk.Frame(ed)
        f.grid(row=3, column=1, sticky='w', pady=4)
        self.slist = tk.Listbox(f, width=36, height=12, bg=FIELD, fg=FG, selectbackground=SEL, highlightthickness=0,
                                relief='flat', exportselection=False, activestyle='none', selectmode='extended')
        self.slist.pack(side='left')
        self.slist.bind('<<ListboxSelect>>', lambda e: self.show_step_delay())
        side = ttk.Frame(f)
        side.pack(side='left', anchor='n', padx=6)
        self.rec_btn = ttk.Button(side, text=tr('● Record'), command=self.toggle_record)
        self.rec_btn.pack(fill='x')
        self.recwhat = ttk.Combobox(side, state='readonly', values=RECORD, width=14)
        self.recwhat.current(0)
        self.recwhat.pack(fill='x', pady=(6, 0))
        for text, cmd in ((tr('Remove'), self.del_steps), ('↑', lambda: self.move_step(-1)),
                          ('↓', lambda: self.move_step(1))):
            ttk.Button(side, text=text, command=cmd).pack(fill='x', pady=(6, 0))
        ttk.Label(side, text=tr('Delay after (ms)')).pack(anchor='w', pady=(12, 0))
        self.sms = ttk.Spinbox(side, from_=0, to=65535, increment=10, width=8)
        self.sms.pack(anchor='w')
        ttk.Button(side, text=tr('Set'), command=self.set_step_delay).pack(fill='x', pady=(6, 0))
        self.mhint = ttk.Label(ed, foreground='gray', text=HINT)
        self.mhint.grid(row=4, column=1, sticky='w')
        f = ttk.Frame(ed)
        f.grid(row=5, column=1, sticky='w', pady=4)
        self.mkeys = ttk.Label(f, width=30)
        self.mkeys.pack(side='left')
        self.bind_btn = ttk.Button(f, text=tr('Assign key …'), command=self.pick_macro_key)
        self.bind_btn.pack(side='left')
        ttk.Button(f, text=tr('Unbind'), command=self.unbind_macro).pack(side='left', padx=(6, 0))
        ttk.Button(ed, text=tr('Apply'), command=self.apply_macro).grid(row=6, column=1, sticky='e', pady=(12, 0))
        ttk.Label(t, foreground='gray', wraplength=760, justify='left', text=tr(
            'Macros live on the keyboard and run there even without this program, like with IO Center. Deck reads '
            'your key presses for this only while recording or during “Assign key”. Display keys and Fn cannot be '
            'recorded or assigned; set up display keys under “Keys”.')).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(16, 0))
        self.select_macro(None)

    def load_macros(self):
        def job(ql):
            ms, binds = ql.macros(), {}
            for k in KEY_LEDS:  # binding ids = key ids; keys without an own binding answer InvalidParameter
                try:
                    b = ql.call(0x11, 0x06, k.to_bytes(2, 'little'))
                except dm.QLinkError:
                    continue
                if b[2:3] == b'\x0a':
                    binds[k] = int.from_bytes(b[3:5], 'little')
            self.ui.put(lambda: self.macros_loaded(ms, binds))
        self.dev.jobs.put((job, tr('Macros read')))

    def macros_loaded(self, ms, binds):
        self.macros, self.mbind = ms, binds
        self.fill_macros(self.msel)

    def fill_macros(self, sel=None):
        self.mlist.delete(0, 'end')
        for m in self.macros:
            self.mlist.insert('end', f'{m["name"]}  ({len(m["events"])} Schritte)')
        self.select_macro(sel if sel is not None and sel < len(self.macros) else 0 if self.macros else None)

    def select_macro(self, j):
        self.stop_record()
        self.msel = j
        if j is None:
            return self.medit.grid_remove()
        m = self.macros[j]
        self.mlist.selection_clear(0, 'end')
        self.mlist.selection_set(j)
        self.mname.delete(0, 'end')
        self.mname.insert(0, m['name'])
        self.mmode.current(min(m['mode'], 2))
        self.mdelay.current(min(m['delay_mode'], 2))
        self.mms.set(m['delay'])
        self.steps = [list(e) for e in m['events']]
        self.fill_steps()
        self.medit.grid()

    def fill_steps(self, sel=(), extra=()):  # extra: the recording so far, shown live
        self.slist.delete(0, 'end')
        for typ, code, ms in [*self.steps, *extra]:
            self.slist.insert('end', step_text(typ, code, ms))
        for j in sel:
            self.slist.selection_set(j)
        if extra:
            self.slist.see('end')
        if self.msel is not None:
            names = {i: n for n, i in self.cfg.get('keymap', {}).items()}
            keys = [k for k, v in self.mbind.items() if v == self.macros[self.msel]['id']]
            self.mkeys.config(text=', '.join(keylabel(names[k]) if k in names else str(k) for k in keys) or tr('none'))

    def show_step_delay(self):
        s = self.slist.curselection()
        if s:
            self.sms.set(self.steps[s[0]][2])

    def set_step_delay(self):
        s = self.slist.curselection()
        try:
            ms = max(0, min(65535, int(self.sms.get())))
        except ValueError:
            return
        for j in s:
            self.steps[j][2] = ms
        self.fill_steps(s)

    def del_steps(self):
        for j in reversed(self.slist.curselection()):
            del self.steps[j]
        self.fill_steps()

    def move_step(self, d):
        s = self.slist.curselection()
        if len(s) == 1 and 0 <= s[0] + d < len(self.steps):
            j = s[0]
            self.steps[j], self.steps[j + d] = self.steps[j + d], self.steps[j]
            self.fill_steps((j + d,))

    def toggle_record(self):
        if self.rec is not None:
            return self.stop_record()
        rec = self.rec = []  # (time, key id, down) / (time, 'm', mask): only until Stopp, then steps; never logged
        km, what = self.cfg.get('keymap', {}), self.recwhat.current()

        def got(k, d):  # device thread (keys) or the mouse thread
            rec.append((time.monotonic(), k, d))
            self.ui.put(lambda: self.rec is rec and self.fill_steps(extra=rec_events(list(rec), km)))
        if what != 1:
            self.dev.record = got
        if what != 0:
            threading.Thread(target=self.watch_mouse, args=(rec, got), daemon=True).start()
        self.recwhat.config(state='disabled')
        self.rec_btn.config(text=tr('■ Stop'))
        self.mhint.config(text=tr('Recording – press the keys, then “■ Stop”.'))

    def stop_record(self):
        if self.rec is None:
            return
        self.dev.record, rec, self.rec = None, self.rec, None
        self.rec_btn.config(text=tr('● Record'))
        self.recwhat.config(state='readonly')
        self.mhint.config(text=HINT)
        m = [j for j, e in enumerate(rec) if e[1] == 'm']
        if m and rec[m[-1]][2] == 0 and len(m) > 1 and rec[m[-2]][2] == 1:
            del rec[m[-2]:]  # the click on Stopp itself (left down, up) and anything after it
        new = [list(e) for e in rec_events(rec, self.cfg.get('keymap', {}))]
        if self.steps and new:
            self.steps[-1][2] = 50  # gap between the old end and the new part
        self.steps += new
        self.fill_steps()

    def watch_mouse(self, rec, got):
        """While this recording runs: poll the mouse buttons (the keyboard cannot see them). Buttons already held at
        the start (the click on Aufnehmen) count only once all are up."""
        state, prev, armed = ctypes.windll.user32.GetAsyncKeyState, None, False
        while self.rec is rec:
            mask = sum(b for b, vk in MOUSE_VK.items() if state(vk) & 0x8000)
            armed = armed or not mask
            if armed and prev is not None and mask != prev:
                got('m', mask)
            prev = mask
            time.sleep(.005)

    def pick_macro_key(self):
        self.stop_record()
        self.bind_btn.config(text=tr('Press a key …'))
        self.mhint.config(text=tr('Now press the key that should trigger the macro.'))

        def got(k, down):
            if down and self.dev.record is got:
                self.dev.record = None
                self.ui.put(lambda: self.bind_macro_key(k))
        self.dev.record = got

    def bind_macro_key(self, k):
        self.bind_btn.config(text=tr('Assign key …'))
        names = {i: n for n, i in self.cfg.get('keymap', {}).items()}
        label = keylabel(names[k]) if k in names else tr('Key {n}', n=k)
        if self.msel is None or k not in KEY_LEDS:
            return self.mhint.config(text=tr('{key} cannot be assigned.', key=label))
        self.mhint.config(text=tr('{key} now triggers “{macro}”.', key=label, macro=self.macros[self.msel]['name']))
        mid = self.macros[self.msel]['id']
        self.mbind[k] = mid
        self.dev.jobs.put((lambda ql: ql.set_binding(k, 0x0A, mid.to_bytes(2, 'little')), tr('Key assigned')))
        self.fill_steps(self.slist.curselection())

    def unbind_macro(self, j=None):
        j = self.msel if j is None else j
        if j is None:
            return
        keys = [k for k, v in self.mbind.items() if v == self.macros[j]['id']]
        for k in keys:
            del self.mbind[k]

        def job(ql):
            for k in keys:  # ClearBinding: the key is a normal key again
                ql.call(0x11, 0x03, k.to_bytes(2, 'little'))
        self.dev.jobs.put((job, tr('Keys unbound')))
        self.fill_steps()

    def add_macro(self):
        used = {m['id'] for m in self.macros}
        m = {'id': next(i for i in range(1, 1000) if i not in used), 'name': tr('Macro {n}', n=len(self.macros) + 1),
             'mode': 0, 'delay_mode': 2, 'delay': 0, 'events': []}
        self.macros.append(m)
        ids = [x['id'] for x in self.macros]
        self.dev.jobs.put((lambda ql: (ql.set_macro_ids(ids), ql.write_macro(m)), tr('Macro created')))
        self.fill_macros(len(self.macros) - 1)

    def del_macro(self, ask=True):
        j = self.msel
        if j is None or ask and not messagebox.askyesno('Dark Mount Deck', tr('Delete “{name}”?', name=self.macros[j]['name']),
                                                        parent=self.root):
            return
        self.unbind_macro(j)
        del self.macros[j]
        ids = [x['id'] for x in self.macros]
        self.dev.jobs.put((lambda ql: ql.set_macro_ids(ids), tr('Macro deleted')))
        self.fill_macros(max(0, j - 1))

    def apply_macro(self):
        self.stop_record()
        if self.msel is None:
            return
        try:
            delay = max(0, min(65535, int(self.mms.get())))
        except ValueError:
            delay = 0
        m = self.macros[self.msel]
        m.update(name=self.mname.get().strip()[:24] or tr('Macro'), mode=self.mmode.current(),
                 delay_mode=self.mdelay.current(), delay=delay, events=[tuple(e) for e in self.steps])
        m = dict(m)
        self.dev.jobs.put((lambda ql: ql.write_macro(m), tr('Macro saved')))
        self.fill_macros(self.msel)

    def on_key(self, i, down):  # device thread
        a = self.keys[i].get('action')
        if a in PAGE:  # on release, so the new page's key never sees a lone keyUp
            if not down:
                self.ui.put(lambda: self.goto(self.cfg['page'] + (1 if a == 'page_next' else -1)))
        elif a == 'plugin':
            self.host.key(i, down)
        elif down:
            threading.Thread(target=self.fire, args=(i,), daemon=True).start()

    def fire(self, i):
        try:
            run_action(self.keys[i])
        except Exception as e:  # user-configured action: report, never kill the device loop
            self.dev.state = tr('connected, error on key {n}: {e}', n=i + 1, e=e)

    def select(self, i):
        if i != self.sel and not self.apply():  # switching keys saves the current one, like the Stream Deck app
            return
        self.sel, k = i, self.keys[i]
        self.head.config(text=tr('Key {n}', n=i + 1))
        self.title.delete('1.0', 'end')
        self.title.insert('1.0', k.get('title', ''))
        self.rot.set(k.get('rot', 0))
        self.turn.set(k.get('turn', 0))
        self.kdock.set(bool(k.get('dock')))
        self.set_bg(k.get('bg') or '#202020')
        self.set_icon(k.get('icon', ''))
        a = k.get('action', 'none')
        label = ACTIONS.get(a, ACTIONS['none'])[0]
        if a == 'plugin':
            label = next((l for l, (_, au) in self.choices.items() if au == k['value']), tr('Plugin missing: {id}', id=k['value']))
            self.choices.setdefault(label, ('plugin', k['value']))  # a removed plugin keeps its key until changed
        self.action.set(label)
        self.update_value_widget()
        self.val.set('' if a == 'plugin' else tr(k.get('value', '')) if a == 'media' else k.get('value', ''))
        for j, t in enumerate(self.tiles):
            t.config(bg=SEL if j == i else LINE, activebackground=SEL if j == i else LINE)  # frame = selection
        self.preview()
        self.pi_show()

    def update_value_widget(self):
        a, au = self.action_id()
        self.value.config(values=[tr(m) for m in MEDIA] if a == 'media' else [],
                          state='readonly' if a == 'media' else 'disabled' if a in ('none', 'plugin', *PAGE) else 'normal')
        for b in self.browse:
            b.config(state='normal' if a == 'open' else 'disabled')
        pi = a == 'plugin' and self.host.has_pi(au)
        self.hint.config(text=tr('Settings appear below') if pi else ACTIONS.get(a, ('', ''))[1])

    def current(self):  # editor state as key config
        (a, au), v = self.action_id(), self.val.get().strip()
        if a == 'media':  # shown translated, stored as id
            v = next((m for m in MEDIA if tr(m) == v), v)
        k = {'title': self.title.get('1.0', 'end-1c').strip(), 'bg': self.bg, 'icon': self.icon,
             'rot': self.rot.get(), 'turn': self.turn.get(), 'action': a, 'value': au or (v.strip('"') if a == 'open' else v)}
        if self.kdock.get():  # kept out of the config unless on, to stay lean
            k['dock'] = True
        if a == 'plugin':  # same plugin action keeps its instance id and settings, like moving nothing in Stream Deck
            old = self.keys[self.sel]
            same = old.get('action') == 'plugin' and old.get('value') == au
            k['context'] = old['context'] if same else uuid.uuid4().hex
            k['settings'] = old.get('settings', {}) if same else {}
        return k

    def apply(self):
        k = self.current()
        a, v = k['action'], k['value']
        try:
            if a == 'hotkey':
                parse_hotkey(v)
            if a == 'media' and v not in MEDIA:
                raise ValueError(tr('Please choose a media action.'))
        except ValueError as e:
            messagebox.showerror('Dark Mount Deck', str(e), parent=self.root)
            return False
        old = self.keys[self.sel]
        if k != old:
            self.keys[self.sel] = k
            save(self.cfg)
            if old.get('action') == 'plugin' and old.get('context') != k.get('context'):
                self.host.disappear(self.sel, old)
            if a == 'plugin' and old.get('context') != k['context']:
                self.host.appear(self.sel)
            self.dev.dirty.put((self.sel, True))
            self.draw_tile(self.sel)
            self.show_page()  # refreshes the dock's list of mirrored keys
        return True

    def tray_start(self):  # tray icon: click = show the window, menu = show / quit
        import pystray
        show = lambda: self.ui.put(self.show)
        self.tray = pystray.Icon('darkmount', Image.open(ICON), 'Dark Mount Deck', pystray.Menu(
            pystray.MenuItem(tr('Open'), show, default=True),
            pystray.MenuItem(tr('Quit'), lambda: self.ui.put(self.root.destroy))))
        self.tray.run_detached()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def set_lang(self, code):  # texts are fixed at start: save, close, come back in the new language
        if code != lang.LANG and self.apply():
            self.cfg['lang'] = code
            save(self.cfg)
            self.restart = True
            self.root.destroy()

    # --- pages ------------------------------------------------------------------------------------------------
    def goto(self, n, drop=False):  # show page n (wraps around); drop: delete the current page first
        if not drop and not self.apply():
            return
        pages = self.cfg['pages']
        for i, k in enumerate(self.keys):
            if k.get('action') == 'plugin':
                self.host.disappear(i, k)
        if drop:
            pages.pop(self.cfg['page'])
        self.cfg['page'] = min(n, len(pages) - 1) if drop else n % len(pages)
        save(self.cfg)
        now = time.monotonic()  # the user asked for the new page: plugin images that follow go out at once (MIN_GAP)
        self.dev.pressed.update(dict.fromkeys(range(8), now))
        for i, k in enumerate(self.keys):
            if k.get('action') == 'plugin':
                self.host.appear(i)
            self.dev.dirty.put((i, True))
            self.draw_tile(i)
        self.show_page()
        self.select(self.sel)  # same index: loads the new page's key without saving anything

    def show_page(self):
        self.page_lbl.config(text=tr('Page {n} / {total}', n=self.cfg['page'] + 1, total=len(self.cfg['pages'])))
        self.dev.dock_name = self.page_lbl.cget('text') if self.cfg['dock']['pages'] else 'Dark Mount Deck'
        self.dev.dock_keys = {i: (k.get('title') or '') for i, k in enumerate(self.keys) if k.get('dock')}

    def add_page(self):
        if self.apply():
            self.cfg['pages'].insert(self.cfg['page'] + 1, blank_page())
            self.goto(self.cfg['page'] + 1)

    def del_page(self):
        if len(self.cfg['pages']) > 1 and messagebox.askyesno(
                'Dark Mount Deck', tr('Delete page {n} with all its keys?', n=self.cfg['page'] + 1), parent=self.root):
            self.goto(self.cfg['page'], drop=True)

    # --- action list: drag onto a key or double-click for the selected one ------------------------------------
    def fill_tree(self):
        t, self.tree_imgs = self.tree, []

        def icon(p):
            try:
                im = ImageTk.PhotoImage(Image.open(p).convert('RGBA').resize((16, 16), Image.Resampling.LANCZOS))
            except (OSError, ValueError, AttributeError):
                return ''
            self.tree_imgs.append(im)
            return im
        ttk.Style(self.root).configure('Treeview', background=FIELD, fieldbackground=FIELD, rowheight=22)
        g = t.insert('', 'end', text=' Deck', open=True)
        for a, (label, _) in ACTIONS.items():
            if a != 'none':
                t.insert(g, 'end', f'a:{a}', text=f' {label}')
        for p in sorted(self.host.plugins.values(), key=lambda p: p['name'].lower()):
            if p['actions']:
                g = t.insert('', 'end', text=f' {p["name"]}', image=icon(p.get('icon')))
                for au, a in sorted(p['actions'].items(), key=lambda x: x[1]['name'].lower()):
                    t.insert(g, 'end', f'p:{au}', text=f' {a["name"]}', image=icon(a['icon']))
        t.bind('<ButtonPress-1>', lambda e: setattr(self, 'dragged', t.identify_row(e.y) if ':' in t.identify_row(e.y)
                                                    else None))
        t.bind('<B1-Motion>', self.drag_move)
        t.bind('<ButtonRelease-1>', self.drag_end)
        t.bind('<Double-1>', lambda e: ':' in t.focus() and self.assign(self.sel, t.focus()))

    def drag_move(self, e):
        if not self.dragged:
            return
        if not self.ghost:
            self.ghost = tk.Toplevel(self.root, bg=LINE)
            self.ghost.overrideredirect(True)
            self.ghost.attributes('-alpha', .85, '-topmost', True)
            ttk.Label(self.ghost, text=self.tree.item(self.dragged, 'text'), image=self.tree.item(self.dragged, 'image'),
                      compound='left', padding=4).pack()
        self.ghost.geometry(f'+{e.x_root + 14}+{e.y_root + 10}')

    def drag_end(self, e):
        if self.ghost:
            self.ghost.destroy()
            self.ghost = None
            w = self.root.winfo_containing(e.x_root, e.y_root)
            if w in self.tiles:
                self.assign(self.tiles.index(w), self.dragged)
        self.dragged = None

    def assign(self, i, iid):  # like dropping in Stream Deck: the key gets the action and a fresh look
        kind, v = iid.split(':', 1)
        self.select(i)
        if self.sel != i:  # the previous key could not be saved
            return
        self.action.set(next(l for l, c in self.choices.items() if c == (('plugin', v) if kind == 'p' else (v, None))))
        self.title.delete('1.0', 'end')
        self.set_icon('')
        self.val.set('')
        self.update_value_widget()
        if kind == 'p' or v in PAGE:  # nothing to fill in: save right away
            self.apply()
            self.pi_show()
        else:
            self.preview()
            self.value.focus_set()

    # --- plugin settings: the plugin's own page in an Edge app window, embedded in the frame -----------------
    def pi_close(self):
        p, self.pi_proc, self.pi_ctx = self.pi_proc, None, None
        if p and p.poll() is None:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)

    def pi_show(self):
        k = self.keys[self.sel]
        ctx = k.get('context') if k.get('action') == 'plugin' else None
        if ctx and ctx == self.pi_ctx and self.pi_proc and self.pi_proc.poll() is None:
            return
        self.pi_close()
        url, exe = ctx and self.host.pi_url(self.sel), sdhost.edge()
        self.pi_lbl.config(text=tr('Loading settings …') if url and exe else
                           tr('Microsoft Edge not found.') if url else
                           tr('This action has no settings.') if ctx else
                           tr('Drag a plugin action onto a key – its settings appear here.'))
        if url and exe:
            prof = Path(os.environ['LOCALAPPDATA']) / 'darkmount' / 'pi'
            prefs = prof / 'Default' / 'Preferences'  # no "translate this page?" bubble (no flag for that)
            try:
                pr = json.loads(prefs.read_text('utf-8'))
            except (OSError, ValueError):
                pr = {}
            if pr.get('translate', {}).get('enabled') is not False:
                prefs.parent.mkdir(parents=True, exist_ok=True)
                prefs.write_text(json.dumps({**pr, 'translate': {'enabled': False}}), 'utf-8')
            self.pi_ctx = ctx
            self.pi_proc = subprocess.Popen([exe, f'--app={url}', f'--user-data-dir={prof}', *PI_FLAGS,
                                             '--window-position=-32000,-32000', '--window-size=600,300'])
            self.root.after(100, self.pi_embed, self.pi_proc, 0)

    def pi_embed(self, proc, n, hwnd=None):
        """Wait for Edge's window, make it a borderless child of the frame, then push its title bar and
        borders outside the frame so only the page shows."""
        if proc is not self.pi_proc:  # another key was picked meanwhile
            return
        u, w, h = ctypes.windll.user32, self.pi.winfo_width(), self.pi.winfo_height()
        if hwnd is None:
            hwnd = top_window(proc.pid)
            if hwnd:
                u.GetWindowLongW.restype = ctypes.c_long
                st = u.GetWindowLongW(hwnd, -16)  # GWL_STYLE: + WS_CHILD, - WS_POPUP, WS_CAPTION, WS_THICKFRAME
                u.SetWindowLongW(hwnd, -16, (st | 0x40000000) & ~(0x80000000 | 0x00C00000 | 0x00040000))
                u.SetParent(hwnd, self.pi.winfo_id())
                u.MoveWindow(hwnd, 0, 0, w, h, True)
                n = 0
        else:
            a = page_area(hwnd)
            if a and n > 3:  # a few rounds for Edge to lay itself out in the new size
                u.MoveWindow(hwnd, -a[0], -a[1], w + a[2], h + a[3], True)
                return
        if n < 100:
            self.root.after(150, self.pi_embed, proc, n + 1, hwnd)

    def draw_tile(self, i, k=None):
        im = render(self.look(k or self.keys[i]))
        self.photos[i] = ImageTk.PhotoImage(im)
        self.tiles[i].config(image=self.photos[i])
        if k is None:  # the small copy under "Beleuchtung" shows what the key shows, not unsaved edits
            self.mini_src[i] = im
            self.draw_mini(i)

    def preview(self):  # unsaved edits on the selected tile; the keyboard gets them with "Übernehmen"
        self.draw_tile(self.sel, self.current())

    def snap(self, var, v):  # slider catches within 5° of every 45° step, free in between
        n = round(v / 45) * 45
        if v != n and abs(v - n) <= 5:
            var.set(n)
        self.preview()

    def step(self, var, deg):
        var.set((var.get() + deg + 180) % 360 - 180)
        self.preview()

    def pick_bg(self):
        c = colorchooser.askcolor(self.bg, parent=self.root)[1]
        if c:
            self.set_bg(c)
            self.preview()

    def set_bg(self, c):
        self.bg = c
        self.bg_btn.config(bg=c, activebackground=c)

    def pick_icon(self):
        p = filedialog.askopenfilename(parent=self.root, filetypes=[
            (tr('Images'), '*.png *.jpg *.jpeg *.gif *.bmp *.ico *.webp'), (tr('All files'), '*.*')])
        if p:
            self.set_icon(os.path.normpath(p))
            self.preview()

    def set_icon(self, p):
        self.icon = p
        self.icon_lbl.config(text=Path(p).name if p else tr('(none)'))

    def pick_target(self, dialog):
        p = dialog(parent=self.root)
        if p:
            self.val.set(os.path.normpath(p))

    def poll(self):  # plugin news from other threads: key index = new look, None = settings to save
        while not self.ui.empty():
            i = self.ui.get()
            if callable(i):  # a device job's result for the UI
                i()
            elif i is None:
                save(self.cfg)
            else:
                self.dev.dirty.put((i, False))
                self.preview() if i == self.sel else self.draw_tile(i)
        self.status.config(text=tr('Keyboard: {state}', state=self.dev.state))
        self.root.after(100, self.poll)


if __name__ == '__main__':
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp window on scaled displays
    if sys.argv[1:2] == ['test']:
        import io, types
        assert ctypes.sizeof(INPUT) == 40
        assert parse_hotkey('Ctrl + Shift+M') == [0x11, 0x10, 0x4D] and parse_hotkey('win+f13') == [0x5B, 0x7C]
        assert render({'title': 'Hallo\nWelt', 'bg': '#ffffff'}).size == (120, 120)
        assert load()['pages'][0][7].get('title') is not None and 'light' in load()
        assert dock_conf(DOCK).hex() == 'dc4d0001011e000000' and dock_picture('') is None  # IO Center's factory bytes
        buf = io.BytesIO()  # a tall red bar turned 90° must fill the width
        Image.new('RGBA', (10, 30), 'red').save(buf, 'PNG')
        assert render({'icon': io.BytesIO(buf.getvalue()), 'rot': 90}).getpixel((20, 60))[0] > 200
        assert render({'icon': io.BytesIO(buf.getvalue()), 'rot': 0}).getpixel((60, 60))[0] > 200  # loaded at all
        assert render({'icon': io.BytesIO(buf.getvalue()), 'rot': 0}).getpixel((20, 60))[0] < 100
        assert render({'icon': io.BytesIO(buf.getvalue()), 'turn': 90}).getpixel((20, 60))[0] > 200
        assert render({'title': 'X' * 30, 'turn': 45}).size == (120, 120)
        assert render({'img': Image.new('RGBA', (72, 72), 'red'), 'title': 'x'}).getpixel((5, 5))[0] > 200  # full bleed
        shown, ql = ['a'], types.SimpleNamespace(n=0)  # flash guard
        ql.set_key_image = lambda key, jpeg: setattr(ql, 'n', ql.n + 1)
        dev = Device(lambda i: render({'title': shown[0]}), None)
        assert dev.upload(ql, 0, False) and ql.n == 1
        assert dev.upload(ql, 0, True) and ql.n == 1  # unchanged: no write, even when forced
        shown[0] = 'b'
        assert not dev.upload(ql, 0, False) and ql.n == 1  # changed within MIN_GAP: held back
        dev.pressed[0] = time.monotonic()
        assert dev.upload(ql, 0, False) and ql.n == 2  # right after a press: at once
        dev.dock_keys, shown[0] = {0: 'Living room'}, 'c'  # a mirrored key: its change arms a dock flash
        dev.upload(ql, 0, True)
        assert dev.dock_flash and dev.dock_flash[0] == 'Living room'
        plays = []  # dock_sync shows the flash, then (once it ages out) falls back to now playing
        ql.dock_playback = lambda *a: plays.append(a)
        ql.dock_image = ql.dock_profile = ql.dock_time = lambda *a: None
        ql.dock_connected = lambda: True
        dev.dock_media = ((2, 0, 0, '', '', 'Song'), None)
        st = {}
        dev.dock_sync(ql, st, time.monotonic())
        assert plays[-1] == (1, 0, 0, '', '', 'Living room')  # the mirrored label
        dev.dock_flash = (dev.dock_flash[0], dev.dock_flash[1], time.monotonic() - DOCK_HOLD - 1)  # expired
        dev.dock_sync(ql, st, time.monotonic())
        assert plays[-1] == (2, 0, 0, '', '', 'Song')  # now playing is back
        root = tk.Tk()  # GUI smoke test without touching the keyboard
        app = App(root, connect=False)
        for v, want in ((43, 45), (-131, -135), (30, 30), (178, 180)):
            app.turn.set(v)  # what the slider does before calling its command
            app.snap(app.turn, v)
            assert app.turn.get() == want, (v, app.turn.get())
        globals()['save'] = lambda cfg: None  # nothing below may touch the real deck.json
        assert app.dev.jobs.get_nowait()[1] == 'Macros read' and app.dev.jobs.empty()
        app.layers[:] = []
        app.fill_layers()
        assert app.cur() is None and app.llist.size() == 1  # only the base effect
        app.cfg['keymap'], km0 = {'Key_A': 30}, app.cfg.get('keymap')
        app.draw_keys()  # a mapped key without a layer shows the base effect
        app.cfg['keymap'] = km0 or {}
        app.layer_ed.set({'effect': 4, 'direction': 0, 'brightness': 50, 'speed': 50, 'colors': ['#ff0000', '#0000ff']})
        app.apply_layer()
        job, _ = app.dev.jobs.get_nowait()
        calls = []
        job(types.SimpleNamespace(set_lighting=calls.append))
        assert calls == [dm.layer_config(4, 0, 50, 50, ['#ff0000', '#0000ff'])]
        # geometry: 74 edge LEDs, all inside their boxes, none shared with keys
        assert len(KEYNAMES) == 105 and not set(sum(FRAMES.values(), ())) & set(KEY_LEDS)
        assert sorted(FRAMEPOS) == sorted(sum(FRAMES.values(), ())), set(FRAMEPOS) ^ set(sum(FRAMES.values(), ()))
        for g, ids in FRAMES.items():
            x0, y0, x1, y1 = FRAME_BOX[g]
            assert all(x0 - .01 <= FRAMEPOS[i][0] <= x1 + .01 and y0 - .01 <= FRAMEPOS[i][1] <= y1 + .01 for i in ids), g
        assert len(FRAMELEDS) == 74 and set(FRAMELEDS.values()) == set(FRAMEPOS)
        assert FRAMELEDS['Led_KeyboardTop1'] == 107 and FRAMELEDS['Led_KeyboardLeft1'] == 106
        assert FRAMELEDS['Led_KeyboardBottom1'] == 159 and FRAMELEDS['Led_NumpadRight1'] == 176
        # effects
        p = _prep({'effect': 4, 'direction': 0, 'brightness': 50, 'speed': 50, 'colors': ['#ff0000', '#0000ff']}, 1, 1)
        assert led_color(p, 10, None) == (0, 0, 128) and led_color(p, 10, 10) == (128, 0, 0)  # rest, just pressed
        assert led_color(p, 10, 9.9)[0] > led_color(p, 10, 9)[0] > 0 == led_color(p, 10, 5)[0]  # fades back
        wave = {'effect': 1, 'direction': 3, 'brightness': 100, 'speed': 50, 'colors': dm.RAINBOW}
        a, b = _prep(wave, 1, 1), _prep(wave, 7, 1)
        assert led_color(a, 0, None) != led_color(b, 0, None) and led_color(a, 0, None) != led_color(a, .5, None)
        assert led_color(a, 0, None) == led_color(_prep(wave, 1, 5), 0, None)  # 'Rechts' ignores the row
        for fx in range(6):  # every effect gives valid colors everywhere, any time, with 1, 2 or 7 colors
            for cs in (['#ff0000'], ['#ff0000', '#00ff00'], dm.RAINBOW):
                q = _prep({**wave, 'effect': fx, 'colors': cs, 'direction': fx}, 3, 2)
                assert all(0 <= v <= 255 for t in (0, .3, 7.7) for v in led_color(q, t, t - .1)), (fx, cs)
        # layers: first wins, rest = general effect, every key LED and edge LED drawn
        base = {'effect': 0, 'direction': 0, 'brightness': 100, 'speed': 50, 'colors': ['#010203']}
        lay = [{'name': 'a', 'keys': ['Key_A', *[f'Led_NumpadTop{j}' for j in range(1, 6)]], **base, 'colors': ['#ff0000']},
               {'name': 'b', 'keys': ['Key_A', 'Key_B'], **base, 'colors': ['#00ff00']}]
        plan = light_plan(lay, base, {'Key_A': 5, 'Key_B': 6, 'Key_C': 7})
        col = {i: led_color(p, 0, None) for i, p in plan.items()}
        assert col[5] == (255, 0, 0) and col[6] == (0, 255, 0) and col[7] == (1, 2, 3) and col[171] == (255, 0, 0)
        assert col[106] == (1, 2, 3) and set(col) == {*KEY_LEDS, *FRAMEPOS}
        prof = Path(os.environ.get('DM_TEST_PROFILE', ''))  # optional: a known IO Center export to check against
        if prof.is_file():
            light, layers = read_profile(prof)
            assert light == {'effect': 4, 'direction': 3, 'brightness': 20, 'speed': 50,
                             'colors': ['#ff2800', '#d3d6da']}, light
            assert [(L['name'], L['effect'], len(L['keys'])) for L in layers] == [('Reaktiv', 4, 105), ('Farbwelle', 1, 74)]
            assert layers[1]['direction'] == 3 and len(layers[1]['colors']) > 2, layers[1]
        # UI: a layer, keys clicked into it, Deck draws it
        app.cfg['keymap'] = {'Key_A': 5}
        app.layers[:] = []
        app.fill_layers()
        assert app.dev.lights is None or not app.layers
        app.add_layer()
        assert app.cur() is app.layers[0] and app.lname.get() == app.layers[0]['name']
        app.select_layer(1)
        assert app.cur() is None and app.lname.get() == 'Base effect'
        app.select_layer(0)
        ev = lambda x, y, state=0: types.SimpleNamespace(x=x, y=y, state=state)
        pt = lambda n: [f(v) + .5 for f, v in zip((app.kx, app.ky), FRAMEPOS[FRAMELEDS[n]] if n in FRAMELEDS else KEYPOS[n])]
        app.drag(ev(*pt('Key_A')), 0)
        app.drag(ev(*pt('Key_A')), 2)  # click: only A
        assert app.layers[0]['keys'] == ['Key_A'], app.layers[0]['keys']
        (ax, ay), (bx, by) = pt('Led_NumpadTop1'), pt('Led_NumpadTop5')
        app.drag(ev(ax - 6, ay - 6), 0)  # box around the numpad's top edge, Shift: add
        app.drag(ev(bx + 6, by + 6), 1)
        app.drag(ev(bx + 6, by + 6, 1), 2)
        assert app.layers[0]['keys'] == ['Key_A', *[f'Led_NumpadTop{j}' for j in range(1, 6)]], app.layers[0]['keys']
        app.layer_ed.set({**base, 'colors': ['#102030']})
        app.apply_layer()
        frame = app.dev.lights(0, {})
        assert frame[5] == (16, 32, 48) and frame[171] == (16, 32, 48) and frame[6] == (0, 0, 128)  # 6: Reaktiv rest
        assert frame[176] == (0, 0, 128)  # numpad right edge not in the layer
        app.drag(ev(*pt('Key_A')), 0)
        app.drag(ev(*pt('Key_A'), 4), 2)  # Ctrl: A off again
        assert app.layers[0]['keys'] == [f'Led_NumpadTop{j}' for j in range(1, 6)] and app.dev.lights(0, {})[5] == (0, 0, 128)
        app.drag(ev(1, 1), 0)
        app.drag(ev(1, 1), 2)  # click into nothing: layer unchanged
        x, y = app.kx(20.75), (app.ky(-MT) + LED_R + app.ky(1.25)) / 2 - MINI / 2 - 2
        assert app.kb.find_withtag('dimg0') and app.mini_photos[0]
        assert 'disp0' in {g for it in app.kb.find_overlapping(x, y, x, y) for g in app.kb.gettags(it)}
        assert len(app.layers[0]['keys']) == 5
        app.calibrate()
        f = app.dev.lights(0, {})
        assert f[1] == (255, 255, 255) and sum(map(sum, f.values())) == 765
        app.kb_click('Key_Escape')  # LED 1 lit Esc
        app.kb_click('Led_NumpadTop1')  # edge LEDs are not keys: ignored
        app.kb_click('Key_A')  # LED 2 lit A (was 5)
        app.cal_step(-1)
        app.cal_step(1)
        app.cal_done()
        assert app.cfg['keymap'] == {'Key_Escape': 1, 'Key_A': 2}, app.cfg['keymap']
        app.del_layer()
        assert app.dev.lights is None and not app.layers
        # macros: HID table, recording -> steps, the bytes sent to the keyboard
        assert set(KEYNAMES) - set(HID) == {'Key_Fn'} and HID['Key_Numpad4'] == 0x5C and HID['Key_A'] == 4
        assert HID['Key_NonUsBackslash'] == 0x64 and HID['Key_RightGui'] == 0xE7 and HID['Key_0'] == 0x27
        km = {'Key_A': 30, 'Key_Numpad4': 72}
        assert rec_events([(1.0, 72, True), (1.135, 72, False), (1.2, 999, True), (1.5, 30, True)], km) ==             [(2, 0x5C, 135), (3, 0x5C, 365), (2, 4, 0)]
        sent = []
        fake = types.SimpleNamespace(call=lambda f, c, p=b'': sent.append((f, c, p.hex())) or b'')
        dm.QLink.write_macro(fake, {'id': 1, 'name': 'Makro 1', 'mode': 0, 'delay_mode': 2, 'delay': 0,
                                    'events': [(2, 0x5C, 135), (3, 0x5C, 0)]})
        assert sent == [(0x12, 8, '0100074d616b726f2031'), (0x12, 4, '010000020000'),
                        (0x12, 6, '01000200' + '0000' + '025c8700035c0000')], sent  # as read back from the keyboard
        sent.clear()
        dm.QLink.write_macro(fake, {'id': 2, 'name': '', 'mode': 0, 'delay_mode': 0, 'delay': 0,
                                    'events': [(2, 4, 10)] * 20})
        assert [x[2][:12] for x in sent[2:]] == ['020014000000', '020014000c00'] and len(sent[3][2]) == 12 + 64
        sent.clear()
        dm.QLink.write_macro(fake, {'id': 2, 'name': '', 'mode': 0, 'delay_mode': 0, 'delay': 0, 'events': []})
        assert sent[2] == (0x12, 6, '020000000000')  # empty list clears
        pages = {0: '1400' + '025c8700' * 13, 13: '1400' + '035c0000' * 7}  # 20 events in two answers
        fake.call = lambda f, c, p=b'': bytes.fromhex(pages[int.from_bytes(p[2:], 'little')])
        assert len(dm.QLink._list(fake, 5, b'\1\0', 4)) == 20
        app.cfg['keymap'] = km
        app.macros_loaded([{'id': 1, 'name': 'Makro 1', 'mode': 0, 'delay_mode': 2, 'delay': 0,
                            'events': [(2, 0x5C, 135), (3, 0x5C, 0)]}], {30: 1})
        assert app.mkeys.cget('text') == 'A' and app.slist.size() == 2
        while not app.dev.jobs.empty():
            app.dev.jobs.get_nowait()
        app.add_macro()
        assert [m['id'] for m in app.macros] == [1, 2] and app.msel == 1
        app.toggle_record()
        app.dev.record(30, True)
        app.dev.record(30, False)
        app.poll()
        assert app.slist.size() == 2 and app.slist.get(0).startswith('↓  A'), app.slist.get(0, 'end')  # live
        app.toggle_record()
        assert app.dev.record is None and [s[:2] for s in app.steps] == [[2, 4], [3, 4]]
        app.recwhat.current(2)  # keyboard + mouse; the mouse thread polls the real buttons, so feed it by hand
        app.toggle_record()
        rec = app.rec
        t0 = time.monotonic()
        rec += [(t0, 'm', 1), (t0 + .1, 'm', 3), (t0 + .2, 'm', 0), (t0 + .25, 30, True), (t0 + .3, 30, False),
                (t0 + .5, 'm', 1), (t0 + .6, 'm', 0)]  # last two: the click on Stopp
        app.toggle_record()
        assert [tuple(s[:2]) for s in app.steps[2:]] == [(0x0A, 1), (0x0A, 3), (0x0A, 0), (2, 4), (3, 4)], app.steps
        assert step_text(0x0A, 3, 5) == '🖱  Left + Right   5 ms' and step_text(0x0A, 0, 0).startswith('🖱  releas')
        del app.steps[2:]
        app.mname.delete(0, 'end')
        app.mname.insert(0, 'Test')
        app.apply_macro()
        sent = []
        fake.call = lambda f, c, p=b'': sent.append((f, c, p.hex())) or b''
        fake.set_macro_ids = lambda ids: dm.QLink.set_macro_ids(fake, ids)
        fake.write_macro = lambda m: dm.QLink.write_macro(fake, m)
        fake.set_binding = lambda k, a, d=b'': sent.append(('bind', k, a, d.hex()))
        while not app.dev.jobs.empty():
            app.dev.jobs.get_nowait()[0](fake)
        assert sent[0] == (0x12, 2, '0200000001000200') and sent[-3] == (0x12, 8, '02000454657374'), sent
        assert sent[-1][2].startswith('02000200') and app.macros[1]['name'] == 'Test'
        app.pick_macro_key()
        app.dev.record(72, True)
        app.poll()
        assert app.mbind == {30: 1, 72: 2} and app.dev.record is None
        assert app.mhint.cget('text').startswith('4 (Numpad4) now triggers'), app.mhint.cget('text')
        app.del_macro(ask=False)
        sent.clear()
        while not app.dev.jobs.empty():
            app.dev.jobs.get_nowait()[0](fake)
        assert ('bind', 72, 10, '0200') in sent and (0x11, 3, '4800') in sent and (0x12, 2, '010000000100') in sent, sent
        assert app.mbind == {30: 1} and len(app.macros) == 1
        app.cfg['pages'], app.cfg['page'] = [blank_page()], 0  # pages, page keys, action list
        app.select(0)
        app.add_page()
        assert len(app.cfg['pages']) == 2 and app.cfg['page'] == 1 and app.page_lbl.cget('text') == 'Page 2 / 2'
        app.assign(3, 'a:page_next')
        assert app.keys[3]['action'] == 'page_next' and app.look(app.keys[3])['title'] == '►\n2/2'
        app.on_key(3, True), app.on_key(3, False), app.poll()  # wraps around to the first page
        assert app.cfg['page'] == 0 and app.keys[3].get('action') in (None, 'none')
        app.assign(0, 'a:url')  # needs a value: editor only, nothing saved yet
        assert app.action_id() == ('url', None) and app.keys[0].get('action') in (None, 'none')
        app.goto(-1)
        assert app.cfg['page'] == 1 and 'plugin action' in app.pi_lbl.cget('text')
        assert all(time.monotonic() - app.dev.pressed[i] < 5 for i in range(8))  # plugin images pass MIN_GAP
        app.goto(1, drop=True)
        assert app.cfg['pages'] and len(app.cfg['pages']) == 1 and app.cfg['page'] == 0
        assert app.tree.get_children() and app.tree.get_children(app.tree.get_children()[0])
        assert load.__code__ and blank_page([{'x': 1}])[0] == {'x': 1} and len(blank_page()) == 8
        import ast  # every text the UI translates has a German version
        for f in ('deck.py', 'sdhost.py'):
            for c in ast.walk(ast.parse(Path(__file__).with_name(f).read_text('utf-8'))):
                if isinstance(c, ast.Call) and getattr(c.func, 'id', '') == 'tr' and isinstance(c.args[0], ast.Constant):
                    assert c.args[0].value in lang.DE, (f, c.args[0].value)
        assert set(MEDIA) - {'Play/Pause'} <= set(lang.DE) and set(dm.DIRECTIONS) <= set(lang.DE)
        assert set(dm.EFFECTS) - {'Tornado', 'Matrix'} <= set(lang.DE)
        t = 'DarkMountDeckSelfTest'  # autostart on a throwaway entry
        assert not autostart(None, t) and autostart(True, t) and autostart(None, t) and '--min' in winreg.QueryValueEx(
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN), t)[0] and not autostart(False, t) and not autostart(None, t)
        lang.LANG = 'de'
        assert tr('Page {n} / {total}', n=1, total=2) == 'Seite 1 / 2'
        lang.LANG = 'en'
        root.update()
        root.destroy()
        print('ok')
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s', handlers=[
            RotatingFileHandler(lang.HOME / 'deck.log', maxBytes=200_000, backupCount=1, encoding='utf-8')])
        root = tk.Tk()
        app = App(root)
        app.tray_start()
        if '--min' in sys.argv:
            root.withdraw()
        root.mainloop()
        app.tray.stop()
        app.pi_close()
        app.host.stop()
        if app.restart:
            subprocess.Popen([sys.executable, *([] if lang.FROZEN else [os.path.abspath(__file__)]), *[a for a in sys.argv[1:] if a != '--min']])
