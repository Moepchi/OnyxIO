"""OnyxIO: the 8 display keys of the be quiet! Dark Mount like a Stream Deck, plus the keyboard lighting.

Start: pythonw deck.py [--min]   (IO Center must not run: the keyboard allows only one session)
Config: deck.json next to this file, edited in the window. Plugin log: deck.log. Self-check: python deck.py test
"""
import ast, ctypes, ctypes.wintypes as W, io, json, logging, math, os, queue, random, shlex, struct, subprocess, sys, threading, time, uuid, webbrowser, zipfile, zlib
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont, ImageOps, ImageTk
try:
    import sv_ttk  # Windows 11 look; Linux distributions rarely package it, plain ttk works there too
except ImportError:
    sv_ttk = None
import darkmount as dm
import lang, sdhost
from lang import LANGS, tr

VERSION = '1.7.0'  # raise together with the release tag, the update hint compares against it
REPO = 'Moepchi/OnyxIO'
WIN = sys.platform == 'win32'  # everything else is treated as Linux
FONT = 'arialbd.ttf' if WIN else 'DejaVuSans-Bold.ttf'  # Pillow finds both by name in the system font folders
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


# Dark Mount layouts: same PCB and LED map, only the keycaps/legends differ (ISO = 105 keys, ANSI = 104). The key
# table below is the only thing that varies; FRAMES, the LED counts and HID are the shared PCB. The keymap is
# user-calibrated, so it absorbs the physical ISO/ANSI difference (an unused LED just stays at the general fill).
# ponytail: assumes the US/UK boards share the ISO PCB's 192-LED map; a real capture would confirm it. The Light
# Mount (TKL, no numpad, ARGB bars) needs its own table + LED map from a capture before it can be added here.
_NUMPAD = (
    (1.25, 20.25, 'NumLock|Num NumpadSlash|/ NumpadAsterisk|* NumpadMinus|-'),
    (2.25, 20.25, 'Numpad7|7 Numpad8|8 Numpad9|9 NumpadPlus|+'),
    (3.25, 20.25, 'Numpad4|4 Numpad5|5 Numpad6|6 NumpadPlus|'),
    (4.25, 20.25, 'Numpad1|1 Numpad2|2 Numpad3|3 NumpadEnter|↵'),
    (5.25, 20.25, 'Numpad0|0|2 NumpadPeriodAndDelete|, NumpadEnter|'))
LAYOUTS = {
    'DE': (  # ISO, German legends
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
        *_NUMPAD),
    'US': (  # ANSI, US legends (wide Enter, wide LeftShift, no ISO extra keys, a Backslash on row 2)
        (0, 0, 'Escape|Esc _1 F1|F1 F2|F2 F3|F3 F4|F4 _.5 F5|F5 F6|F6 F7|F7 F8|F8 _.5 F9|F9 F10|F10 F11|F11 F12|F12 '
               '_.25 PrintScreen|PrtSc ScrollLock|ScrLk PauseBreak|Pause'),
        (1.25, 0, 'GraveAccentAndTilde|` 1|1 2|2 3|3 4|4 5|5 6|6 7|7 8|8 9|9 0|0 MinusAndUnderscore|- EqualsAndPlus|= '
                  'Backspace|⟵|2 _.25 Insert|Ins Home|Home PageUp|PgUp'),
        (2.25, 0, 'Tab|Tab|1.5 Q|Q W|W E|E R|R T|T Y|Y U|U I|I O|O P|P BracketLeft|[ BracketRight|] Backslash|\\|1.5 '
                  '_.25 Delete|Del End|End PageDown|PgDn'),
        (3.25, 0, 'CapsLock|Caps|1.75 A|A S|S D|D F|F G|G H|H J|J K|K L|L SemicolonAndColon|; ApostropheAndDoubleQuote|\' '
                  'Enter|↵|2.25'),
        (4.25, 0, 'LeftShift|⇧|2.25 Z|Z X|X C|C V|V B|B N|N M|M CommaAndLessThan|, PeriodAndBiggerThan|. '
                  'SlashAndQuestionMark|/ RightShift|⇧|2.75 _1.25 UpArrow|↑'),
        (5.25, 0, 'LeftCtrl|Ctrl|1.25 LeftGui|Win|1.25 LeftAlt|Alt|1.25 Space||6.25 RightAlt|Alt|1.25 RightGui|Win|1.25 '
                  'Fn|Fn|1.25 RightCtrl|Ctrl|1.25 _.25 LeftArrow|← DownArrow|↓ RightArrow|→'),
        *_NUMPAD),
    'UK': (  # ISO, UK/English legends (ISO Enter + # and \ keys like DE, but English letters)
        (0, 0, 'Escape|Esc _1 F1|F1 F2|F2 F3|F3 F4|F4 _.5 F5|F5 F6|F6 F7|F7 F8|F8 _.5 F9|F9 F10|F10 F11|F11 F12|F12 '
               '_.25 PrintScreen|PrtSc ScrollLock|ScrLk PauseBreak|Pause'),
        (1.25, 0, 'GraveAccentAndTilde|` 1|1 2|2 3|3 4|4 5|5 6|6 7|7 8|8 9|9 0|0 MinusAndUnderscore|- EqualsAndPlus|= '
                  'Backspace|⟵|2 _.25 Insert|Ins Home|Home PageUp|PgUp'),
        (2.25, 0, 'Tab|Tab|1.5 Q|Q W|W E|E R|R T|T Y|Y U|U I|I O|O P|P BracketLeft|[ BracketRight|] Enter|↵|1.5 '
                  '_.25 Delete|Del End|End PageDown|PgDn'),
        (3.25, 0, 'CapsLock|Caps|1.75 A|A S|S D|D F|F G|G H|H J|J K|K L|L SemicolonAndColon|; ApostropheAndDoubleQuote|\' '
                  'NonUsTilde|# Enter||1.25'),
        (4.25, 0, 'LeftShift|⇧|1.25 NonUsBackslash|\\ Z|Z X|X C|C V|V B|B N|N M|M CommaAndLessThan|, PeriodAndBiggerThan|. '
                  'SlashAndQuestionMark|/ RightShift|⇧|2.75 _1.25 UpArrow|↑'),
        (5.25, 0, 'LeftCtrl|Ctrl|1.25 LeftGui|Win|1.25 LeftAlt|Alt|1.25 Space||6.25 RightAlt|AltGr|1.25 RightGui|Win|1.25 '
                  'Fn|Fn|1.25 RightCtrl|Ctrl|1.25 _.25 LeftArrow|← DownArrow|↓ RightArrow|→'),
        *_NUMPAD)}
LAYOUTNAMES = {'DE': 'Deutsch (ISO)', 'US': 'US (ANSI)', 'UK': 'UK (ISO)'}


def build_layout(name):  # (re)build the board globals for the chosen layout; called at start and reused by the tests
    global KEYS, KEYNAMES, KEYPOS
    KEYS = [k for y, x, spec in LAYOUTS[name] for k in _row(y, x, spec)]
    KEYNAMES = list(dict.fromkeys(k[0] for k in KEYS))
    KEYPOS = {n: (x + w / 2, y + .5) for n, _, x, y, w in reversed(KEYS)}  # reversed: a key's first part wins


def _pick_layout():  # deck.json "layout" picks the board; tests and anything unknown use the full ISO-DE board
    try:
        v = json.loads(CFG.read_text('utf-8')).get('layout')
    except (OSError, ValueError):
        v = None
    return v if v in LAYOUTS else 'DE'


LAYOUT = 'DE' if sys.argv[1:2] == ['test'] else _pick_layout()
build_layout(LAYOUT)
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
RIPPLE = (30, 28)  # ring speed (units/s per speed frac), reach where it dies (~board diagonal) — tune; width is per layer
PV_FPS, PV_PRESS = 25, 0.7  # live-preview frame rate and how often a random key "presses" (s), so Reactive / Ripple move
# IO Center profile effects are an index into its std::variant<Breathing, ColorWave, Gif, Image, Matrix,
# MulticolorStatic, Reactive, ScreenCapture, ScreenSync, Sensor, Static, Tornado, Video, Off, Ripple> -> our effect id
PROFILE_FX = {0: 3, 1: 1, 4: 5, 5: 0, 6: 4, 10: 0, 11: 2, 13: 0, 14: 4}
PROFILE_DIR = {'Up': 0, 'Down': 1, 'Left': 2, 'Right': 3, 'Clockwise': 4, 'CounterClockwise': 5}
FXNAMES = dm.EFFECTS + ['Ripple', 'Custom']  # Ripple + Custom are OnyxIO-only layer effects; firmware knows only dm.EFFECTS
RAINBOW_RGB = [ImageColor.getrgb(c) for c in dm.RAINBOW]  # Ripple's random-per-drop colors are picked from this loop
MOTION = ['Still', 'Linear', 'Radial', 'Spiral', 'Random']  # Custom: how the colour field moves across the board
REACT = ['None', 'Flash', 'Wave']  # Custom: what a key press does on top of the field


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


def _mix(a, b, f):
    return tuple(round(u + (v - u) * f) for u, v in zip(a, b))


def _at(loop, f):  # color at f (any float, wraps) along a closed loop of colors
    f = f % 1 * (len(loop) - 1)
    i = int(f)
    return _mix(loop[i], loop[i + 1], f - i)


# --- Custom free formula: a user expression -> palette position. Profiles are shared, so no bare eval:
# only a whitelist of math over a fixed set of variables, no attribute/index access, no ** (exponent DoS).
_FUNCS = {k: getattr(math, k) for k in ('sin', 'cos', 'tan', 'sqrt', 'exp', 'floor', 'ceil', 'atan2', 'hypot')}
_FUNCS.update(abs=abs, min=min, max=max, pi=math.pi, tau=math.tau)
_VARS = {'x', 'y', 't', 'd', 'a', 'seit'}  # position, time, distance/angle from centre, seconds since this key's press
_OK = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
       ast.FloorDiv, ast.Mod, ast.USub, ast.UAdd, ast.Call, ast.IfExp, ast.Compare, ast.BoolOp, ast.And, ast.Or,
       ast.Lt, ast.Gt, ast.LtE, ast.GtE, ast.Eq, ast.NotEq)


def _compile_formula(expr):  # -> code object, or raises ValueError(msg) on anything outside the whitelist
    tree = ast.parse(expr, mode='eval')
    for node in ast.walk(tree):
        if not isinstance(node, _OK):
            raise ValueError(tr('not allowed: {x}', x=type(node).__name__))
        if isinstance(node, ast.Name) and node.id not in _VARS and node.id not in _FUNCS:
            raise ValueError(tr('unknown: {x}', x=node.id))
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
            raise ValueError(tr('only the listed functions'))
    return compile(tree, '<formula>', 'eval')


def _run_formula(code, p, t, hit):  # the whitelist already blocks escapes; empty builtins is a second wall
    v = {'x': p['pos'][0], 'y': p['pos'][1], 't': t, 'd': p['dist'], 'a': p['ang'],
         'seit': 1e9 if hit is None else t - hit}
    return eval(code, {'__builtins__': {}}, {**_FUNCS, **v})


def _prep(L, x, y):
    """Effect + LED position -> what led_color needs every frame."""
    cols = [ImageColor.getrgb(c) for c in L['colors']]
    d, s = L['direction'], L['speed'] / 100
    ang = math.atan2(y - CENTER[1], x - CENTER[0]) / math.tau * (-1 if d == 5 else 1)
    u = {0: -y, 1: y, 2: -x, 3: x}.get(d)
    rest = cols[1] if len(cols) == 2 else (0, 0, 0)
    code = None
    if L.get('free') and L.get('formula'):  # a bad formula just falls back to the pattern, never crashes the render
        try:
            code = _compile_formula(L['formula'])
        except (ValueError, SyntaxError):
            code = None
    return {'fx': L['effect'], 'cols': cols, 'rest': rest, 'loop': cols if len(cols) > 2 else [cols[0], rest, cols[0]],
            'free': L.get('free', False), 'code': code,
            'b': L['brightness'] / 100, 'rate': .05 + .6 * s, 'fade': 3 - 2.5 * s, 'y': y, 'ang': ang, 'pos': (x, y),
            'u': ang if u is None else u / WAVE, 'seed': x * .618 % 1, 'width': .5 + L.get('thick', 50) / 100 * 3,
            'rand': L.get('random', False), 'rpal': cols if len(cols) > 2 else RAINBOW_RGB,
            'pat': L.get('pat', 0), 'react': L.get('react', 0), 'dist': math.hypot(x - CENTER[0], y - CENTER[1]),
            'scale': .3 + L.get('thick', 50) / 100 * 2, 'noise': (math.sin(x * 12.9898 + y * 78.233) * 43758.5) % 1}


def _ring(p, t, hits, pos):  # strongest press ring reaching this LED -> (glow 0..1, crest colour); for Ripple + Custom
    speed, reach = RIPPLE
    x0, y0 = p['pos']
    glow, crest = 0.0, p['cols'][0]
    for j, hj in (hits or {}).items():
        jx, jy = (pos or {}).get(j, (x0, y0))
        r = (t - hj) * speed * p['rate']  # ring radius grows with the layer's speed
        fade = min(1.0, max(0.0, (reach - r) / (reach * .3)))  # full brightness until 70 % of reach, then out
        env = math.exp(-((math.hypot(x0 - jx, y0 - jy) - r) / p['width']) ** 2) * fade
        if env > glow:  # a random drop keeps one hue per press (seeded on its time), else the picked color/gradient
            glow = env
            crest = _at(p['rpal'], hj * .618 % 1) if p['rand'] else (
                p['cols'][0] if len(p['cols']) < 3 else _at(p['loop'], r / WAVE))
    return glow, crest


def led_color(p, t, hit, hits=None, pos=None):
    """One LED at time t (s); hit = time of its key's last press or None. Drawn after the keyboard's own effects.
    hits (all key id -> press time) and pos (key id -> (x, y)) are used by Ripple + Custom, which spread across LEDs."""
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
    elif fx == 5:  # Matrix: one drop per column falls from top to bottom
        k = (t * p['rate'] + p['seed']) % 1 * 10 - 2 - p['y']
        c = _mix(cols[0], p['rest'], k / 3) if 0 <= k < 3 else p['rest']
    elif fx == 6:  # Ripple: every press sends a ring outward (stone in water), added over the base effect
        bg = led_color(p['bg'], t, hit, hits, pos) if p.get('bg') else p['rest']  # the base effect shows through at rest
        glow, crest = _ring(p, t, hits, pos)
        return tuple(min(255, b + round(v * glow * p['b'])) for b, v in zip(bg, crest))
    else:  # Custom (Baukasten): a colour field (a pattern, or a free formula) plus an optional press reaction
        if p['code'] is not None:  # free formula -> a palette position; a runtime error (e.g. /0) just goes dark
            try:
                ph = _run_formula(p['code'], p, t, hit)
            except Exception:
                ph = 0.0
        else:
            mv, sc = t * p['rate'], p['scale']
            ph = (p['u'] / sc, p['u'] / sc - mv, p['dist'] / (WAVE * sc) - mv,
                  p['ang'] + p['dist'] / (WAVE * sc) - mv, p['noise'] - mv)[p['pat']]  # Still/Linear/Radial/Spiral/Random
        c = _at(p['loop'], ph)
        if p['react'] == 2:  # Welle: rings spread from each press, added over the field
            glow, crest = _ring(p, t, hits, pos)
            return tuple(min(255, round(f * p['b']) + round(v * glow * p['b'])) for f, v in zip(c, crest))
        if p['react'] == 1 and hit is not None and t - hit < p['fade']:  # Aufleuchten: a white flash relaxes to the field
            c = _mix((255, 255, 255), c, (t - hit) / p['fade'])
    return tuple(round(v * p['b']) for v in c)


def light_plan(layers, base, keymap):
    """LED id -> _prep state for every key with an LED and every edge LED. The first layer that holds the key (or edge)
    wins, everything else shows the general effect."""
    owner = {n: L for L in reversed(layers) for n in L['keys']}

    def prep(n, x, y):
        L = owner.get(n, base)
        p = _prep(L, x, y)
        if L is not base and L['effect'] == 6:  # a Ripple layer is drawn over the base effect, not instead of it
            p['bg'] = _prep(base, x, y)
        return p
    plan = {keymap[n]: prep(n, *KEYPOS[n]) for n in KEYNAMES if n in keymap}
    plan.update({i: prep(n, *FRAMEPOS[i]) for n, i in FRAMELEDS.items()})
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


if WIN:
    def press(vks):
        ext = [1 if v in EXTENDED else 0 for v in vks]
        send([(v, 0, e) for v, e in zip(vks, ext)] + [(v, 0, e | 2) for v, e in reversed(list(zip(vks, ext)))])

    def type_text(text):
        raw = text.encode('utf-16-le')
        units = [int.from_bytes(raw[i:i + 2], 'little') for i in range(0, len(raw), 2)]
        send([e for u in units for e in ((0, u, 4), (0, u, 6))])
else:
    # Windows VK -> Linux evdev key code (input-event-codes.h). Letters/digits go by key position like on a US board.
    EV = dict(zip(b'1234567890QWERTYUIOPASDFGHJKLZXCVBNM', [*range(2, 12), *range(16, 26), *range(30, 39), *range(44, 51)]))
    EV.update({0x11: 29, 0x10: 42, 0x12: 56, 0x5B: 125, 0x0D: 28, 0x1B: 1, 0x09: 15, 0x20: 57, 0x08: 14, 0x2E: 111,
               0x2D: 110, 0x24: 102, 0x23: 107, 0x21: 104, 0x22: 109, 0x25: 105, 0x26: 103, 0x27: 106, 0x28: 108,
               0x2C: 99, 0xB3: 164, 0xB0: 163, 0xB1: 165, 0xB2: 166, 0xAD: 113, 0xAF: 115, 0xAE: 114,
               **{0x6F + i: c for i, c in enumerate([*range(59, 69), 87, 88, *range(183, 195)], 1)}})
    _uinput = []

    def press(vks):
        """A virtual keyboard via /dev/uinput (X11 and Wayland alike; needs write access, see the udev rule)."""
        import fcntl
        if not _uinput:
            fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
            fcntl.ioctl(fd, 0x40045564, 1)  # UI_SET_EVBIT EV_KEY
            for c in set(EV.values()):
                fcntl.ioctl(fd, 0x40045565, c)  # UI_SET_KEYBIT
            fcntl.ioctl(fd, 0x405c5503, struct.pack('<4H80sI', 6, 0, 0, 1, b'OnyxIO', 0))  # UI_DEV_SETUP, BUS_VIRTUAL
            fcntl.ioctl(fd, 0x5501)  # UI_DEV_CREATE
            time.sleep(.3)  # the desktop needs a moment to pick up the new device, else the first keys get lost
            _uinput.append(fd)
        ev = lambda t, c, v: struct.pack('llHHi', 0, 0, t, c, v)  # struct input_event; EV_SYN/SYN_REPORT after each
        for code, v in [(EV[k], 1) for k in vks] + [(EV[k], 0) for k in reversed(vks)]:
            os.write(_uinput[0], ev(1, code, v) + ev(0, 0, 0))

    def type_text(text):  # uinput only knows keys, not characters: the desktop's own typing tool does Unicode
        tool = ['wtype', '--'] if os.environ.get('WAYLAND_DISPLAY') else ['xdotool', 'type', '--']
        try:
            subprocess.run(tool + [text], timeout=30)
        except OSError:
            log.warning('type text: %s missing', tool[0])


def run_action(k):
    a, v = k.get('action', 'none'), k.get('value', '')
    if a == 'open':
        os.startfile(os.path.expandvars(v)) if WIN else subprocess.Popen(['xdg-open', os.path.expanduser(os.path.expandvars(v))])
    elif a == 'url':
        webbrowser.open(v)
    elif a == 'hotkey':
        press(parse_hotkey(v))
    elif a == 'text':
        type_text(v)
    elif a == 'media':
        press([MEDIA[v]])
    elif a == 'cmd':
        subprocess.Popen(v, shell=True, creationflags=sdhost.NOWIN)


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
            font = ImageFont.truetype(FONT, size)
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
    cfg.setdefault('layout', LAYOUT)  # which board the keyboard picture / key list use
    cfg.setdefault('light', dict(LIGHT))
    cfg['dock'] = {**DOCK, **cfg.get('dock', {})}
    cfg.pop('keycolors', None)  # replaced by layers
    for L in cfg.get('layers', []):  # whole edges became single LEDs
        L['keys'] = [m for n in L['keys'] for m in ([k for k, i in FRAMELEDS.items() if i in FRAMES[n]]
                                                     if n in FRAMES else [n])]
    return cfg


def _ver(tag):
    return tuple(int(n) for n in tag.lstrip('v').split('.'))


def newer_release():
    """Latest GitHub release tag if newer than VERSION, else None. Only a hint with a link: nothing is downloaded."""
    import urllib.request
    try:
        with urllib.request.urlopen(f'https://api.github.com/repos/{REPO}/releases/latest', timeout=10) as r:
            tag = json.load(r)['tag_name']
        return tag if _ver(tag) > _ver(VERSION) else None
    except (OSError, ValueError, KeyError, TypeError):  # offline, rate limit, odd tag: stay quiet
        return None


RUN = r'Software\Microsoft\Windows\CurrentVersion\Run'


def autostart(on=None, name='OnyxIO'):
    """Autostart at login (per user, starts in the tray). None: report and repoint a moved program; True/False: set.
    Windows: registry Run key. Linux: an XDG autostart .desktop file."""
    if not WIN:
        f = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'autostart' / f'{name}.desktop'
        if on is None:
            on = f.is_file()
        if on:
            exe = [sys.executable] + ([] if lang.FROZEN else [os.path.abspath(__file__)])
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f'[Desktop Entry]\nType=Application\nName=OnyxIO\nExec={shlex.join(exe)} --min\n', 'utf-8')
        else:
            f.unlink(missing_ok=True)
        return on
    import winreg
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
        self.lights = None  # fn(t, hits) -> {ledId: rgb}: OnyxIO draws every LED (realtime); None = the keyboard's effect
        self.hits = {}  # key id -> time of its last press, for Reaktiv / Ripple; memory only, dropped after 3 s
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
                        if not rt:  # realtime: keys OnyxIO does not send stay dark; without us the keyboard drops back
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
                    if (feat, cmd) == (0x11, 0x02) and len(p) >= 2 and int.from_bytes(p[:2], 'little') in dm.DISPLAY_KEYS:
                        i = int.from_bytes(p[:2], 'little') - 0x6d  # ActionFired = display key press; the InputEvent
                        self.pressed[i] = time.monotonic()  # "pressed" does not always come (seen on Linux), release does
                        self.on_key(i, True)
                    if feat == 0x12 and len(p) >= 3 and p[0] in (8, 9):  # InputEvent pressed / released
                        key = int.from_bytes(p[1:3], 'little')
                        rec = self.record  # only while the user asked for it (Makros: Aufnehmen / Taste zuweisen)
                        if rec and key not in dm.DISPLAY_KEYS:
                            rec(key, p[0] == 8)
                        if key not in dm.DISPLAY_KEYS:  # any other key: only when it lights up, never stored or logged
                            if p[0] == 8 and self.lights and key in KEY_LEDS:  # key id == LED id (checked)
                                self.hits[key] = time.monotonic()
                        elif p[0] == 9:  # display key released; its press came as ActionFired above
                            self.on_key(key - 0x6d, False)
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
            try:
                ql.set_key_image(0x6d + i, jpeg)
            except dm.QLinkError as e:  # no numpad module (the displays sit in it): keep the rest working, retry later
                if 'InsufficientResources' not in str(e):
                    raise
                self.sent[i] = (None, now)
                self.dirty.put((i, False))  # not forced: next try after MIN_GAP, so the image shows once it is attached
                return True
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


def watch_mpris(dev, enabled):
    """Linux twin of watch_media: the MPRIS "now playing" of any player, read with playerctl once a second."""
    import shutil, urllib.request
    if not shutil.which('playerctl'):
        return log.info('now playing: playerctl missing')
    fmt = '\t'.join('{{%s}}' % f for f in ('status', 'position', 'mpris:length', 'artist', 'album', 'title', 'mpris:artUrl'))
    last, at = None, 0
    while True:
        try:
            out = enabled() and subprocess.run(['playerctl', 'metadata', '--format', fmt], capture_output=True,
                                               text=True, timeout=3).stdout.rstrip('\n').split('\t')
            info, url = (0, 0, 0, '', '', ''), ''
            if out and len(out) == 7:
                st, pos, dur, *tags, url = out
                info = ({'Playing': 1, 'Paused': 2}.get(st, 0), int(pos or 0) // 10**6, int(dur or 0) // 10**6, *tags)
            now = time.monotonic()
            guess = last[0][1] + (now - at if last[0][0] == 1 else 0) if last else 0
            if not last or info[:1] + info[2:] != last[0][:1] + last[0][2:] or abs(info[1] - guess) > 3:
                cover = last[1] if last and info[3:] == last[0][3:] else None
                if cover is None and url.startswith(('file://', 'http://', 'https://')):
                    with urllib.request.urlopen(url, timeout=5) as r:  # local file or the player's cover link
                        cover = r.read()
                last, at = (info, cover), now
                dev.dock_media = last
        except Exception as e:  # player closed mid-query, odd values, cover offline: try again next second
            log.info('now playing: %r', e)
        time.sleep(1)


if not WIN:
    watch_media = watch_mpris


def dock_conf(d):  # -> the dock's 9 config bytes [R G B][clock 1=24h][saver 1=clock 2=image][after s u16][off s u16]
    return bytes.fromhex(d['color'][1:]) + struct.pack('<BBHH', d['h24'], d['saver'], d['after'], d['off'])


def dock_picture(path):  # -> 320x240, cropped to fill; None without a usable file
    try:
        return ImageOps.fit(Image.open(path).convert('RGB'), (320, 240)) if path else None
    except OSError:
        return None


BG, FIELD, LINE, FG, DIM, SEL = '#1c1c1c', '#2a2a2a', '#3c3c3c', '#fafafa', '#7a7a7a', '#2f60d8'  # Sun Valley dark
LABEL = 150  # px, label column of the editors
SIDE, HOVER, ORANGE, PICK = '#141414', '#262626', '#e8590c', '#b5420a'  # sidebar, its hover row, OnyxIO accent, selected list row
ICONS = ('Segoe Fluent Icons' if sys.getwindowsversion().build >= 22000 else 'Segoe MDL2 Assets') if WIN else None


def slider(parent, var, lo, hi, length, command=None):
    """ttk.Scale with its value beside it (ttk shows no number and would store floats in the IntVar)."""
    f = ttk.Frame(parent)
    ttk.Scale(f, from_=lo, to=hi, length=length, variable=var,
              command=lambda v: (var.set(round(float(v))), command and command(var.get()))).pack(side='left')
    ttk.Label(f, textvariable=var, width=4, anchor='e').pack(side='left', padx=(8, 0))
    return f


class Nav(ttk.Frame):
    """Sidebar in place of tabs. Pages are its children; add() and select() work like ttk.Notebook's."""

    def __init__(self, parent):
        super().__init__(parent)
        self.side = tk.Frame(self, bg=SIDE, padx=8, pady=8)
        self.side.grid(row=0, column=0, rowspan=2, sticky='ns')
        self.title = ttk.Label(self, font=('Segoe UI Semibold', 16), padding=(24, 14, 24, 4))
        self.title.grid(row=0, column=1, sticky='w')
        self.pages, self.items, self.cur = [], [], 0
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

    def item(self, text, icon, command, bottom=False):  # one row: orange bar, icon, text
        row = tk.Frame(self.side, bg=SIDE, cursor='hand2')
        row.hot = ORANGE if bottom else FG  # hover color: orange for Quit
        row.bar = tk.Frame(row, bg=SIDE, width=3)
        row.bar.pack(side='left', fill='y', pady=8)
        row.icon = tk.Label(row, text=icon if ICONS else '', font=(ICONS or 'Segoe UI', 13), bg=SIDE, fg=DIM, padx=10)
        row.icon.pack(side='left')
        row.text = tk.Label(row, text=text, font=('Segoe UI', 11), bg=SIDE, fg=DIM, anchor='w', pady=9)
        row.text.pack(side='left', fill='x', expand=True, padx=(0, 28))
        row.pack(side='bottom' if bottom else 'top', fill='x', pady=1)
        for w in (row, row.icon, row.text):
            w.bind('<Button-1>', lambda e: command())
            w.bind('<Enter>', lambda e: self.paint(row, True))
            w.bind('<Leave>', lambda e: self.paint(row, False))
        return row

    def paint(self, row, hover=False):
        on = self.items.index(row) == self.cur if row in self.items else False
        for w in (row, row.icon, row.text):
            w.config(bg=HOVER if on or hover else SIDE)
        row.text.config(fg=FG if on else row.hot if hover else DIM)
        row.icon.config(fg=ORANGE if on else row.hot if hover else DIM)
        row.bar.config(bg=ORANGE if on or hover and row.hot == ORANGE else HOVER if hover else SIDE)

    def add(self, page, text, icon=''):
        i = len(self.pages)
        self.items.append(self.item(text, icon, lambda: self.select(i)))
        page.configure(padding=(24, 8, 24, 16))
        page.grid(row=1, column=1, sticky='nsew')  # all pages in one cell: the window keeps the size of the largest
        self.pages.append(page)
        self.select(self.cur)

    def tabs(self):
        return self.pages

    def select(self, i):
        self.cur = i
        self.pages[i].tkraise()
        self.title.config(text=self.items[i].text['text'])
        for row in self.items:
            self.paint(row)


def orange(root):
    """Sun Valley paints its accent (checkboxes, switches, sliders, selections) blue: make it OnyxIO orange."""
    im = Image.open(Path(sv_ttk.__file__).parent / 'theme' / 'spritesheet_dark.png').convert('RGBA')
    h, sat, v = im.convert('RGB').convert('HSV').split()
    blue = ImageChops.multiply(h.point(lambda x: 255 * (120 <= x <= 185)), sat.point(lambda x: 255 * (x > 60)))
    new = Image.merge('HSV', (h.point(lambda x: 15), sat.point(lambda x: min(255, x * 1.4)), v)).convert('RGB')
    out = Image.composite(new, im.convert('RGB'), blue)
    out.putalpha(im.getchannel('A'))
    sheet = ImageTk.PhotoImage(out, master=root)
    root.tk.eval(f'foreach {{n x y w h}} $::spriteinfo {{$ttk::theme::sv_dark::I($n) copy {sheet} '
                 f'-from $x $y [expr {{$x+$w}}] [expr {{$y+$h}}] -compositingrule set}}')
    root.tk.eval(f'set ttk::theme::sv_dark::colors(-selbg) {PICK}; configure_colors')  # list/combobox selection


def popup(root, x, y, entries):
    """Context menu in the app's look (a tk.Menu stays a Windows menu). entries: (text, icon, command or None = greyed) or None = line."""
    w = tk.Toplevel(root, bg=LINE, padx=1, pady=1)
    w.overrideredirect(True)
    f = tk.Frame(w, bg=FIELD, pady=4)
    f.pack()
    for e in entries:
        if not e:
            tk.Frame(f, bg=LINE, height=1).pack(fill='x', padx=10, pady=4)
            continue
        text, icon, cmd = e
        row = tk.Frame(f, bg=FIELD, cursor='hand2' if cmd else '')
        ic = tk.Label(row, text=icon if ICONS else '', font=(ICONS or 'Segoe UI', 10), bg=FIELD, fg=DIM if cmd else LINE, width=3)
        ic.pack(side='left', padx=(6, 0))
        tx = tk.Label(row, text=text, font=('Segoe UI', 10), bg=FIELD, fg=FG if cmd else DIM, anchor='w', pady=5)
        tx.pack(side='left', fill='x', expand=True, padx=(2, 28))
        row.pack(fill='x', padx=4)
        if cmd:
            for part in (row, ic, tx):
                part.bind('<Enter>', lambda e, r=row, ic=ic, tx=tx: (r.config(bg=HOVER), tx.config(bg=HOVER), ic.config(bg=HOVER, fg=ORANGE)))
                part.bind('<Leave>', lambda e, r=row, ic=ic, tx=tx: (r.config(bg=FIELD), tx.config(bg=FIELD), ic.config(bg=FIELD, fg=DIM)))
                part.bind('<ButtonRelease-1>', lambda e, cmd=cmd: (w.destroy(), cmd()))
    w.update_idletasks()  # keep it on screen near the edges
    w.geometry(f'+{min(x, w.winfo_screenwidth() - w.winfo_reqwidth())}+{min(y, w.winfo_screenheight() - w.winfo_reqheight())}')
    w.grab_set()  # a click anywhere else closes it (no focus for borderless windows on Windows)
    w.bind('<Button>', lambda e: str(w.winfo_containing(e.x_root, e.y_root)).startswith(str(w)) or w.destroy())
    w.bind('<Escape>', lambda e: w.destroy())
    w.focus_set()


def dark(root):
    """Dark look: Sun Valley (Windows 11 style ttk theme), classic tk widgets to match, dark Windows title bar."""
    if sv_ttk:
        sv_ttk.set_theme('dark', root)
        try:
            orange(root)
        except (tk.TclError, OSError) as e:  # another sv-ttk version: stays blue
            log.warning('accent: %s', e)
    else:
        ttk.Style(root).theme_use('clam')
    # no colors in the option database: the theme's tk_setPalette resets every widget whose color matches it
    for k, v in {'*Button.highlightThickness': 0, '*Listbox.relief': 'flat', '*Listbox.borderWidth': 0,
                 '*Listbox.activeStyle': 'none', '*TCombobox*Listbox.background': FIELD,
                 '*TCombobox*Listbox.foreground': FG, '*TCombobox*Listbox.selectBackground': PICK,
                 '*TCombobox*Listbox.selectForeground': FG, '*TCombobox*Listbox.font': ('Segoe UI', 10)}.items():
        root.option_add(k, v)
    ttk.Style(root).configure('ComboboxPopdownFrame', bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    root.configure(bg=BG)
    if not WIN:  # the title bar belongs to the desktop's own theme there
        return
    root.update_idletasks()  # the title bar belongs to the real window, which exists only now
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)  # IMMERSIVE_DARK_MODE


class FxEditor(ttk.Frame):
    """One lighting effect as stored in deck.json: effect, direction, colors, brightness, speed."""
    MODES = [tr('One color'), tr('Two colors (Reactive: press, rest)'), tr('Gradient')]

    def __init__(self, parent, root):
        super().__init__(parent)
        self.root = root
        self.columnconfigure(0, minsize=LABEL)  # same label column as the layer name above it
        for r, text in enumerate([tr('Effect'), tr('Direction'), tr('Colors'), '', tr('Brightness %'), tr('Speed %')]):
            ttk.Label(self, text=text).grid(row=r, column=0, sticky='w', padx=(0, 12), pady=4)
        self.fx, self.fdir, self.cmode = (ttk.Combobox(self, state='readonly', values=v)
                                          for v in ([tr(x) for x in FXNAMES], [tr(x) for x in dm.DIRECTIONS],
                                                    self.MODES))
        for r, box in enumerate((self.fx, self.fdir, self.cmode)):
            box.grid(row=r, column=1, sticky='we', pady=4)
        self.cmode.bind('<<ComboboxSelected>>', lambda e: self.widgets())
        f = ttk.Frame(self)
        f.grid(row=3, column=1, sticky='w')
        self.lc_btn = [tk.Button(f, width=6, command=lambda j=j: self.pick(j)) for j in range(2)]
        for b in self.lc_btn:
            b.pack(side='left', padx=(0, 6))
        self.bright, self.speed, self.thick = tk.IntVar(), tk.IntVar(), tk.IntVar()
        for r, var in ((4, self.bright), (5, self.speed)):
            slider(self, var, 0, 100, 300).grid(row=r, column=1, sticky='w')
        self.thick_lbl = ttk.Label(self, text=tr('Wave width %'))  # Ripple only, shown by fx_widgets()
        self.thick_scale = slider(self, self.thick, 0, 100, 300)
        self.rand = tk.BooleanVar()
        self.rand_chk = ttk.Checkbutton(self, text=tr('Random color per drop'), variable=self.rand)
        self.pat_lbl = ttk.Label(self, text=tr('Pattern'))  # Custom only, shown by fx_widgets()
        self.react_lbl = ttk.Label(self, text=tr('Press reaction'))
        self.pat = ttk.Combobox(self, state='readonly', values=[tr(x) for x in MOTION], width=14)
        self.react = ttk.Combobox(self, state='readonly', values=[tr(x) for x in REACT], width=14)
        self.react.bind('<<ComboboxSelected>>', lambda e: self.fx_widgets())  # random colour only matters for Wave
        self.free = tk.BooleanVar()  # Custom: write a formula instead of picking a pattern
        self.free_chk = ttk.Checkbutton(self, text=tr('Free formula'), variable=self.free, command=self.fx_widgets)
        self.formula = ttk.Entry(self, width=42)
        self.formula.bind('<KeyRelease>', lambda e: self.check_formula())
        self.f_hint = ttk.Label(self, foreground='gray', wraplength=320)
        self.fx.bind('<<ComboboxSelected>>', lambda e: self.fx_widgets())

    def fx_widgets(self):  # Ripple shows width; Custom shows scale + pattern, or (free) a formula box
        fxi = self.fx.current()
        rip, cust = fxi == FXNAMES.index('Ripple'), fxi == FXNAMES.index('Custom')
        free = cust and self.free.get()
        self.thick_lbl.config(text=tr('Pattern scale %') if cust else tr('Wave width %'))
        grid = lambda w, on, **g: w.grid(**g) if on else w.grid_remove()
        grid(self.thick_lbl, rip or (cust and not free), row=6, column=0, sticky='w', padx=(0, 12), pady=4)
        grid(self.thick_scale, rip or (cust and not free), row=6, column=1, sticky='w')
        grid(self.free_chk, cust, row=7, column=1, sticky='w', pady=(4, 0))
        grid(self.pat_lbl, cust and not free, row=8, column=0, sticky='w', padx=(0, 12), pady=4)
        grid(self.pat, cust and not free, row=8, column=1, sticky='w', pady=4)
        grid(self.formula, free, row=8, column=1, sticky='we', pady=4)
        grid(self.f_hint, free, row=9, column=1, sticky='w')
        grid(self.react_lbl, cust, row=10, column=0, sticky='w', padx=(0, 12), pady=4)
        grid(self.react, cust, row=10, column=1, sticky='w', pady=4)
        grid(self.rand_chk, rip or (cust and self.react.current() == REACT.index('Wave')),
             row=11, column=1, sticky='w', pady=(4, 0))
        if free:
            self.check_formula()

    def check_formula(self):  # live feedback under the formula box: ok, the error, or the variable cheat-sheet
        expr = self.formula.get().strip()
        if not expr:
            return self.f_hint.config(foreground='gray', text=tr(
                'x y = key, t = s, d a = dist/angle from centre, seit = s since press; value = palette position'))
        try:
            _compile_formula(expr)
            self.f_hint.config(foreground='#3a8a3a', text=tr('Formula OK'))
        except (ValueError, SyntaxError) as e:
            self.f_hint.config(foreground='#c04040', text='⚠ ' + str(e))

    def set(self, L, base=False):
        c = L['colors']
        self.fx.config(values=[tr(x) for x in (dm.EFFECTS if base else FXNAMES)])  # Ripple is a layer-only effect
        self.fx.current(L['effect'])
        self.thick.set(L.get('thick', 50))
        self.rand.set(bool(L.get('random')))
        self.pat.current(L.get('pat', 0))
        self.react.current(L.get('react', 0))
        self.free.set(bool(L.get('free')))
        self.formula.delete(0, 'end')
        self.formula.insert(0, L.get('formula', ''))
        self.fx_widgets()
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
        fx = self.fx.current()
        rip, cust = fx == FXNAMES.index('Ripple'), fx == FXNAMES.index('Custom')
        extra = {'thick': self.thick.get(), 'random': self.rand.get()} if rip else (
            {'thick': self.thick.get(), 'random': self.rand.get(), 'pat': self.pat.current(),
             'react': self.react.current(), 'free': self.free.get(), 'formula': self.formula.get().strip()}
            if cust else {})
        return {'effect': fx, 'direction': self.fdir.current(), 'brightness': self.bright.get(),
                'speed': self.speed.get(), 'colors': list(self.grad) if n == 2 else self.lc[:n + 1],
                **extra}  # width/random (Ripple) and pattern/reaction (Custom) are layer-only config fields

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
        root.title('OnyxIO')
        try:
            root.iconbitmap(default=ICON) if WIN else root.iconphoto(True, ImageTk.PhotoImage(Image.open(ICON)))
        except (tk.TclError, OSError):
            pass
        # keeps running in the tray like the Stream Deck app; Linux: not every desktop shows a tray, so minimize instead
        root.protocol('WM_DELETE_WINDOW', root.withdraw if WIN else root.iconify)
        self.tray = None
        head = tk.Frame(root, bg=SIDE, padx=16, pady=10)
        head.grid(row=0, column=0, sticky='we')
        self.logo = ImageTk.PhotoImage(Image.open(ICON).convert('RGBA').resize((26, 26), Image.Resampling.LANCZOS))
        tk.Label(head, image=self.logo, bg=SIDE).pack(side='left', padx=(0, 10))
        tk.Label(head, text='Onyx', font=('Segoe UI Semibold', 15), bg=SIDE, fg=FG, bd=0, padx=0).pack(side='left')
        tk.Label(head, text='IO', font=('Segoe UI Semibold', 15), bg=SIDE, fg=ORANGE, bd=0, padx=0).pack(side='left')
        tk.Label(head, text=VERSION, font=('Segoe UI', 9), bg=SIDE, fg=DIM).pack(side='left', anchor='s', padx=8, pady=(0, 3))
        self.status = tk.Label(head, font=('Segoe UI', 10), bg=SIDE, fg=DIM)
        self.status.pack(side='right')
        self.dot = tk.Label(head, text='\u25cf', font=('Segoe UI', 11), bg=SIDE, fg=DIM)
        self.dot.pack(side='right', padx=(0, 4))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        nb = self.nav = Nav(root)
        nb.grid(row=1, column=0, sticky='nsew')
        tab = ttk.Frame(nb, padding=(16, 12))
        nb.add(tab, tr('Keys'), '\ue765')
        tiles = ttk.Frame(tab, padding=10)
        tiles.grid(row=0, column=0, sticky='n')
        self.tiles = [tk.Button(tiles, bd=3, relief='flat', bg=LINE, command=lambda i=i: self.select(i)) for i in range(8)]
        for i, b in enumerate(self.tiles):
            b.grid(row=i // COLS, column=i % COLS, padx=3, pady=3)
            b.bind('<Button-3>', lambda e, i=i: self.key_menu(e, i))
        self.clip = None
        f = ttk.Frame(tiles)
        f.grid(row=2, column=0, columnspan=COLS, pady=(8, 0))
        ttk.Button(f, text='◀', width=3, command=lambda: self.goto(self.cfg['page'] - 1)).pack(side='left', padx=(0, 6))
        self.page_lbl = ttk.Label(f, width=12, anchor='center')
        self.page_lbl.pack(side='left')
        ttk.Button(f, text='▶', width=3, command=lambda: self.goto(self.cfg['page'] + 1)).pack(side='left', padx=(6, 0))
        ttk.Button(f, text=tr('New page'), command=self.add_page).pack(side='left', padx=(16, 6))
        ttk.Button(f, text=tr('Delete page'), command=self.del_page).pack(side='left', padx=(0, 6))
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
        self.title = tk.Text(ed, width=34, height=2, font=('Segoe UI', 10), bg=FIELD, fg=FG, insertbackground=FG,
                             relief='flat', highlightthickness=1, highlightbackground=LINE, highlightcolor=ORANGE)
        self.title.grid(row=1, column=1, columnspan=2, sticky='we', pady=3)
        self.title.bind('<KeyRelease>', lambda e: self.preview())
        self.bg_btn = tk.Button(ed, width=6, command=self.pick_bg)
        self.bg_btn.grid(row=2, column=1, sticky='w', pady=3)
        self.icon_lbl = ttk.Label(ed, width=20)
        self.icon_lbl.grid(row=3, column=1, sticky='w')
        f = ttk.Frame(ed)
        f.grid(row=3, column=2, sticky='e', pady=3)
        ttk.Button(f, text=tr('Choose …'), width=10, command=self.pick_icon).pack(side='left', padx=(0, 6))
        ttk.Button(f, text=tr('Remove'), width=10, command=lambda: (self.set_icon(''), self.preview())).pack(side='left')
        for row, var in ((4, self.rot), (5, self.turn)):
            f = ttk.Frame(ed)
            f.grid(row=row, column=1, columnspan=2, sticky='we', pady=3)
            slider(f, var, -180, 180, 150, lambda v, var=var: self.snap(var, v)).pack(side='left')
            for deg, text in ((90, '⟳ 90°'), (-90, '⟲ 90°')):  # from the right: flush with "Remove" above
                ttk.Button(f, text=text, width=10, command=lambda v=var, d=deg: self.step(v, d)).pack(  # as wide as the two above
                    side='right', padx=(6, 0))
        self.action = ttk.Combobox(ed, state='readonly', values=list(self.choices), height=24)
        self.action.grid(row=6, column=1, columnspan=2, sticky='we', pady=3)
        self.action.bind('<<ComboboxSelected>>', lambda e: (self.val.set(''), self.update_value_widget(), self.preview()))
        self.value = ttk.Combobox(ed, textvariable=self.val)
        self.value.grid(row=7, column=1, columnspan=2, sticky='we', pady=3)
        f = ttk.Frame(ed)
        f.grid(row=8, column=1, columnspan=2, sticky='w', pady=(4, 0))
        self.browse = [ttk.Button(f, text=tr('File …'), command=lambda: self.pick_target(filedialog.askopenfilename)),
                       ttk.Button(f, text=tr('Folder …'), command=lambda: self.pick_target(filedialog.askdirectory))]
        for b in self.browse:
            b.pack(side='left', padx=(0, 6))
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
        f = ttk.Frame(nb)
        nb.add(f, tr('Settings'), '')
        nb.item(tr('Quit'), '', root.destroy, bottom=True)
        tk.Label(nb.side, text='created by Möpchi', font=('Segoe UI', 8), bg=SIDE, fg=DIM).pack(side='bottom', pady=(0, 10))
        link = tk.Label(nb.side, text='GitHub ↗', font=('Segoe UI', 9), bg=SIDE, fg=DIM, cursor='hand2')
        link.pack(side='bottom')
        link.bind('<Button-1>', lambda e: webbrowser.open(f'https://github.com/{REPO}'))
        link.bind('<Enter>', lambda e: link.config(fg=ORANGE))
        link.bind('<Leave>', lambda e: link.config(fg=DIM))
        for r, text in enumerate([tr('Keyboard layout'), tr('Language')], 1):
            ttk.Label(f, text=text).grid(row=r, column=0, sticky='w', padx=(0, 32), pady=6)
        lay = ttk.Combobox(f, state='readonly', values=list(LAYOUTNAMES.values()), width=18)
        lay.set(LAYOUTNAMES[self.cfg['layout']])
        lay.bind('<<ComboboxSelected>>', lambda e: self.set_layout(list(LAYOUTNAMES)[lay.current()]))
        lay.grid(row=1, column=1, sticky='w', pady=4)
        box = ttk.Combobox(f, state='readonly', values=list(LANGS.values()), width=18)
        box.set(LANGS[lang.LANG])
        box.bind('<<ComboboxSelected>>', lambda e: self.set_lang(list(LANGS)[box.current()]))
        box.grid(row=2, column=1, sticky='w', pady=4)
        self.bar, self.upd_shown = head, False  # the update hint goes into the header
        self.upd = tk.BooleanVar(value=self.cfg.get('updates', False))  # opt-in: off, OnyxIO sends nothing anywhere
        ttk.Checkbutton(f, text=tr('Check for updates'), variable=self.upd, command=self.set_updates,
                        style='Switch.TCheckbutton' if sv_ttk else 'TCheckbutton').grid(row=3, column=0, columnspan=2, sticky='w', pady=(20, 6))
        if autostart(None, 'DarkMountDeck'):  # ponytail: moves the pre-rename (<= 1.4) entry, drop after a few releases
            autostart(False, 'DarkMountDeck'), autostart(True)
        self.auto = tk.BooleanVar(value=autostart())
        ttk.Checkbutton(f, text=tr('Start with Windows') if WIN else tr('Start at login'), variable=self.auto,
                        command=lambda: autostart(self.auto.get()),
                        style='Switch.TCheckbutton' if sv_ttk else 'TCheckbutton').grid(row=4, column=0, columnspan=2, sticky='w', pady=6)
        nb.select(0)
        self.dev = Device(lambda i: render(self.look(self.keys[i])), self.on_key)
        self.dev.on_dock = lambda step: step and self.cfg['dock']['pages'] and self.ui.put(
            lambda: self.goto(self.cfg['page'] + step))
        # plugin extras (sdhost: onyxio.*); with layers the frame reads host.light live, nothing to rebuild
        self.host.on_light = lambda: self.layers or self.cal or self.push_lights()
        self.host.on_page = self.goto
        self.host.on_dock = lambda i, text: setattr(self.dev, 'dock_flash', (text, self.dev.look(i), time.monotonic()))
        self.push_lights()
        self.load_macros()  # waits in the job queue until the keyboard is there
        if connect:
            log.info('plugins: %s; skipped: %s', sorted(self.host.plugins), self.host.skipped)
            self.host.start()
            self.dev.start()
            threading.Thread(target=watch_media, args=(self.dev, lambda: self.cfg['dock']['media']), daemon=True).start()
            self.check_update()
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
        nb.add(t, 'Media Dock', '\ue8d6')
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
            w.grid(row=r, column=1, sticky='we', pady=4)
        ttk.Label(ed, text='s').grid(row=3, column=2, sticky='w', padx=8)
        ttk.Label(ed, text=tr('s (0 = never)')).grid(row=4, column=2, sticky='w', padx=8)
        f = ttk.Frame(ed)
        f.grid(row=5, column=1, columnspan=2, sticky='w', pady=4)
        ttk.Button(f, text=tr('Choose …'), command=self.pick_dock_image).pack(side='left', padx=(0, 6))
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
            'Time, page and what is playing are sent while OnyxIO runs.')).grid(row=0, column=1, sticky='nw', padx=(24, 0))
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
            return messagebox.showerror('OnyxIO', tr('Times: 5 – 3600 s, display off 0 – 3600 s.'),
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

    # --- lighting: the base effect (bottom of the layer list) lives on the keyboard, the layers above it come from OnyxIO
    def apply_light(self):
        L = self.cfg['light']
        save(self.cfg)
        data = dm.layer_config(**L)
        self.dev.jobs.put((lambda ql: ql.set_lighting(data), tr('Lighting applied')))
        self.push_lights()

    # --- layers: OnyxIO draws every LED in realtime mode while it runs, the keyboard falls back to the general effect ---
    def build_keys(self, nb):
        t = ttk.Frame(nb, padding=10)
        nb.add(t, tr('Lighting'), '\ue82f')
        self.cal, self.newmap, self.lsel = None, {}, None  # LED walk position, its map, selected layer index
        self.layers = self.cfg.setdefault('layers', [])
        self.pv_on = tk.BooleanVar(value=self.cfg.get('preview', True))  # the live preview: the effect, animated, in the app
        self.pv_hits, self.pv_rand, self.pv_token = {}, 0, None
        self.pv_plan, self.pv_pos, self.pv_names = {}, {}, {}
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
        ttk.Checkbutton(self.toolbar, text=tr('Live preview'), variable=self.pv_on, command=self.preview_toggle).pack(
            side='left', padx=(12, 0))
        self.calbar.grid_remove()
        self.khint = ttk.Label(t, foreground='gray', wraplength=2 * PAD + 24.25 * U, justify='left')
        self.khint.grid(row=2, column=0, columnspan=2, sticky='w', pady=(8, 0))
        left = ttk.Frame(t)
        left.grid(row=3, column=0, sticky='nw', pady=(12, 0), padx=(0, 24))
        self.llist = tk.Listbox(left, width=28, height=8, bg=FIELD, fg=FG, selectbackground=PICK, highlightthickness=0,
                                relief='flat', exportselection=False, activestyle='none')
        self.llist.grid(row=0, column=0, columnspan=4, sticky='we')
        self.llist.bind('<<ListboxSelect>>', lambda e: self.select_layer(next(iter(self.llist.curselection()), None)))
        for j, (text, cmd) in enumerate((('+', self.add_layer), ('−', self.del_layer),
                                         ('↑', lambda: self.move_layer(-1)), ('↓', lambda: self.move_layer(1)))):
            ttk.Button(left, text=text, width=4, command=cmd).grid(row=1, column=j, sticky='w', padx=(0, 6), pady=(6, 0))
        ttk.Label(left, foreground='gray', text=tr('The top one wins, the base effect fills the rest.')).grid(
            row=2, column=0, columnspan=4, sticky='w')
        self.ledit = ttk.Frame(t)
        self.ledit.grid(row=3, column=1, sticky='nw', pady=(12, 0))
        ttk.Label(self.ledit, text='Name').grid(row=0, column=0, sticky='w', padx=(0, 12), pady=4)
        self.ledit.columnconfigure(0, minsize=LABEL)
        self.ledit.columnconfigure(1, weight=1)  # the effect editor's extra width goes to the fields, not the labels
        self.lname = ttk.Entry(self.ledit)
        self.lname.grid(row=0, column=1, sticky='we', pady=4)
        self.layer_ed = FxEditor(self.ledit, self.root)
        self.layer_ed.grid(row=1, column=0, columnspan=2, sticky='we')
        ttk.Button(self.ledit, text=tr('Apply'), command=self.apply_layer).grid(row=2, column=1, sticky='e', pady=(12, 0))
        self.mini_photos = [None] * 8  # the display keys above the numpad, as in the "Tasten" tab; click = edit there
        for i in range(8):
            self.kb.tag_bind(f'disp{i}', '<Button-1>', lambda e, i=i: (nb.select(0), self.select(i)))
        self.fill_layers()
        self.preview_start()  # start the live preview if it is on

    # --- live preview: run the very same led_color the keyboard gets, animated, onto the picture ------------------------
    def preview_sync(self):  # rebuild the render plan + LED-id -> canvas-name map after any lighting change
        keymap = self.cfg.get('keymap', {})
        self.pv_plan = light_plan(self.layers, self.cfg['light'], keymap)
        self.pv_pos = {i: p['pos'] for i, p in self.pv_plan.items()}
        self.pv_names = {i: n for n, i in {**keymap, **FRAMELEDS}.items()}

    def preview_start(self):  # (re)start or stop the loop to match pv_on, without persisting
        if self.pv_on.get():
            self.preview_sync()
            self.preview_tick()
        else:
            self.pv_token = None  # stops the running chain
            self.draw_keys()  # back to the still render

    def preview_toggle(self):  # the checkbutton: remember the choice, then apply it
        self.cfg['preview'] = self.pv_on.get()
        save(self.cfg)
        self.preview_start()

    def preview_tick(self, token=None):
        if token is None:  # a fresh start; a new token invalidates any older chain still pending
            self.pv_token = token = object()
        if token is not self.pv_token or not self.pv_on.get() or self.cal is not None:
            return
        if not self.kb.winfo_viewable():  # another tab is up or the window sits in the tray: idle, keep the chain alive
            return self.root.after(200, lambda: self.preview_tick(token))
        now = time.monotonic()
        if now - self.pv_rand > PV_PRESS and self.pv_plan:  # a random key "press" so Reactive / Ripple come alive
            keys = [i for i in self.pv_plan if i in KEY_LEDS]
            if keys:
                self.pv_hits[random.choice(keys)] = now
            self.pv_rand = now
        self.pv_hits = {i: v for i, v in self.pv_hits.items() if now - v < 3}
        for i, p in self.pv_plan.items():
            n = self.pv_names.get(i)
            if n:
                self.kb.itemconfig('led_' + n, fill='#%02x%02x%02x' % led_color(p, now, self.pv_hits.get(i),
                                                                                self.pv_hits, self.pv_pos))
        self.root.after(round(1000 / PV_FPS), lambda: self.preview_tick(token))

    def cur(self):  # the selected layer, None for the base effect
        return self.layers[self.lsel] if self.lsel is not None and self.lsel < len(self.layers) else None

    def fill_layers(self, sel=None):
        self.llist.delete(0, 'end')
        for L in self.layers:
            self.llist.insert('end', f'{L["name"]}  ({tr(FXNAMES[L["effect"]])}, {len(L["keys"])})')
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
        self.layer_ed.set(L or self.cfg['light'], base=L is None)
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
            return messagebox.showerror('OnyxIO', tr('Cannot read profile: {e}', e=repr(e)), parent=self.root)
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
                          outline=SEL if name in mine else LINE, width=max(1, 3 * k) if name in mine else 1,
                          tags=(name, f'led_{name}'))
        seen = set()
        for name, label, x, y, w in KEYS:
            if self.cal is None:
                fill = look(owner.get(name, base), *KEYPOS[name]) if name in owner or name in m else FIELD
            else:  # blue = mapped to the LED that is lit now, green = mapped
                fill = SEL if self.newmap.get(name) == self.cal else '#2e7d32' if name in self.newmap else FIELD
            x0, y0 = cx(x), cy(y)
            c.create_rectangle(x0 + 2, y0 + (3 if name not in seen else -3), x0 + w * u - 2, y0 + u - 2, fill=fill,
                               outline=SEL if name in mine else fill, width=max(1, 3 * k),
                               tags=(name, f'led_{name}') if self.cal is None else (name,))
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
        if hasattr(self, 'pv_plan'):
            self.preview_sync()  # keep the live preview in step with every edit
        if not self.layers and not self.host.light:
            self.dev.lights = None
            return
        keymap = self.cfg.get('keymap', {})
        plan = light_plan(self.layers, self.cfg['light'], keymap)
        pos = {i: p['pos'] for i, p in plan.items()}  # a Ripple layer needs every press's LED position
        ids = {**keymap, **FRAMELEDS}  # LED names plugins use -> LED ids

        def lights(t, hits):
            frame = {i: led_color(p, t, hits.get(i), hits, pos) for i, p in plan.items()}
            for leds in list(self.host.light.values()):  # plugin lights sit on top of every layer
                if 'all' in leds:
                    frame = dict.fromkeys(frame, leds['all'])
                frame.update({ids[n]: c for n, c in leds.items() if n in ids})
            return frame
        self.dev.lights = lights

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
        self.preview_start()  # calibration blocked the preview; resume it

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
        nb.add(t, tr('Macros'), '\ue7c8')
        self.macros, self.mbind, self.msel, self.steps, self.rec = [], {}, None, [], None
        left = ttk.Frame(t)
        left.grid(row=0, column=0, sticky='nw', padx=(0, 24))
        self.mlist = tk.Listbox(left, width=28, height=14, bg=FIELD, fg=FG, selectbackground=PICK, highlightthickness=0,
                                relief='flat', exportselection=False, activestyle='none')
        self.mlist.grid(row=0, column=0, columnspan=3, sticky='we')
        self.mlist.bind('<<ListboxSelect>>', lambda e: self.select_macro(next(iter(self.mlist.curselection()), None)))
        for j, (text, cmd) in enumerate((('+', self.add_macro), ('−', self.del_macro), (tr('Reload'), self.load_macros))):
            ttk.Button(left, text=text, width=4 if j < 2 else 10, command=cmd).grid(row=1, column=j, sticky='w', padx=(0, 6),
                                                                                   pady=(6, 0))
        self.medit = ed = ttk.Frame(t)
        ed.grid(row=0, column=1, sticky='nw')
        for r, text in enumerate(['Name', tr('Playback'), tr('Delays'), tr('Steps'), '', tr('Keys')]):
            ttk.Label(ed, text=text).grid(row=r, column=0, sticky='nw' if r == 3 else 'w', padx=(0, 16), pady=(10, 4) if r == 3 else 4)
        ed.columnconfigure(1, weight=1)
        self.mname = ttk.Entry(ed)
        self.mname.grid(row=0, column=1, sticky='we', pady=4)
        self.mmode = ttk.Combobox(ed, state='readonly', values=MODES)
        self.mmode.grid(row=1, column=1, sticky='we', pady=4)
        f = ttk.Frame(ed)
        f.grid(row=2, column=1, sticky='we', pady=4)
        self.mdelay = ttk.Combobox(f, state='readonly', values=DELAYS, width=18)
        self.mdelay.pack(side='left')
        self.mms = ttk.Spinbox(f, from_=0, to=65535, increment=10, width=7)
        self.mms.pack(side='left', padx=6)
        ttk.Label(f, text=tr('ms (with “Fixed”)')).pack(side='left')
        f = ttk.Frame(ed)  # steps: record bar, list, edit bar
        f.grid(row=3, column=1, sticky='we', pady=4)
        bar = ttk.Frame(f)
        bar.pack(fill='x')
        self.rec_btn = ttk.Button(bar, text=tr('● Record'), command=self.toggle_record, style='Accent.TButton')
        self.rec_btn.pack(side='left')
        self.recwhat = ttk.Combobox(bar, state='readonly', values=RECORD, width=14)
        self.recwhat.current(0)
        self.recwhat.pack(side='left', padx=6)
        self.slist = tk.Listbox(f, width=60, height=12, bg=FIELD, fg=FG, selectbackground=PICK, highlightthickness=0,
                                relief='flat', exportselection=False, activestyle='none', selectmode='extended')
        self.slist.pack(fill='x', pady=6)
        self.slist.bind('<<ListboxSelect>>', lambda e: self.show_step_delay())
        bar = ttk.Frame(f)
        bar.pack(fill='x')
        for text, cmd in ((tr('Remove'), self.del_steps), ('↑', lambda: self.move_step(-1)), ('↓', lambda: self.move_step(1))):
            ttk.Button(bar, text=text, width=None if len(text) > 1 else 3, command=cmd).pack(side='left', padx=(0, 6))
        ttk.Button(bar, text=tr('Set'), command=self.set_step_delay).pack(side='right')
        self.sms = ttk.Spinbox(bar, from_=0, to=65535, increment=10, width=7)
        self.sms.pack(side='right', padx=6)
        ttk.Label(bar, text=tr('Delay after (ms)')).pack(side='right')
        self.mhint = ttk.Label(ed, foreground='gray', text=HINT)
        self.mhint.grid(row=4, column=1, sticky='w')
        f = ttk.Frame(ed)
        f.grid(row=5, column=1, sticky='we', pady=4)
        self.mkeys = ttk.Label(f)
        self.mkeys.pack(side='left')
        ttk.Button(f, text=tr('Unbind'), command=self.unbind_macro).pack(side='right')
        self.bind_btn = ttk.Button(f, text=tr('Assign key …'), command=self.pick_macro_key)
        self.bind_btn.pack(side='right', padx=6)
        ttk.Button(ed, text=tr('Apply'), command=self.apply_macro).grid(row=6, column=1, sticky='e', pady=(12, 0))
        ttk.Label(t, foreground='gray', wraplength=760, justify='left', text=tr(
            'Macros live on the keyboard and run there even without this program, like with IO Center. OnyxIO reads '
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
        if self.recwhat.current() != 1 and not self.cfg.get('keymap'):  # key ids -> names come from the LED mapping
            return messagebox.showinfo('OnyxIO', tr('Recording keys needs the LED mapping: Lighting → “Map LEDs …” once.'),
                                       parent=self.root)
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
        if not WIN:  # ponytail: no global mouse poll on Linux without evdev; macros record keys only there
            return
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
        if j is None or ask and not messagebox.askyesno('OnyxIO', tr('Delete “{name}”?', name=self.macros[j]['name']),
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
            t.config(bg=ORANGE if j == i else LINE, activebackground=ORANGE if j == i else LINE)  # frame = selection
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

    def key_menu(self, e, i):  # right click on a display key
        popup(self.root, e.x_root, e.y_root, [
            (tr('Edit'), '', lambda: self.select(i)), None,
            (tr('Copy'), '', lambda: setattr(self, 'clip', json.loads(json.dumps(self.keys[i])))),
            (tr('Paste'), '', self.clip and (lambda: self.put_key(  # a plugin copy is its own instance
                i, {**self.clip, **({'context': uuid.uuid4().hex} if 'context' in self.clip else {})}))),
            (tr('Swap with next key'), '', lambda: self.swap_keys(i, (i + 1) % 8)), None,
            (tr('Clear'), '', lambda: self.put_key(i, {'title': ''}))])

    def put_key(self, i, k):  # replace key i from outside the editor (menu); the editor shows it if selected
        old, self.keys[i] = self.keys[i], k
        save(self.cfg)
        if old.get('action') == 'plugin' and old.get('context') != k.get('context'):
            self.host.disappear(i, old)
        if k.get('action') == 'plugin' and old.get('context') != k.get('context'):
            self.host.appear(i)
        self.dev.dirty.put((i, True))
        self.draw_tile(i)
        self.show_page()
        if i == self.sel:
            self.select(i)

    def swap_keys(self, i, j):
        a, b = self.keys[i], self.keys[j]
        self.put_key(i, {'title': ''})  # through blank: each plugin instance disappears once and appears at its new key
        self.put_key(j, a)
        self.put_key(i, b)

    def apply(self):
        k = self.current()
        a, v = k['action'], k['value']
        try:
            if a == 'hotkey':
                parse_hotkey(v)
            if a == 'media' and v not in MEDIA:
                raise ValueError(tr('Please choose a media action.'))
        except ValueError as e:
            messagebox.showerror('OnyxIO', str(e), parent=self.root)
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
        self.tray = pystray.Icon('darkmount', Image.open(ICON), 'OnyxIO', pystray.Menu(
            pystray.MenuItem(tr('Open'), show, default=True),
            pystray.MenuItem(tr('Quit'), lambda: self.ui.put(self.root.destroy))))
        self.tray.run_detached()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def set_updates(self):
        self.cfg['updates'] = self.upd.get()
        save(self.cfg)
        self.check_update()

    def check_update(self):  # the only request OnyxIO makes on its own, and only once the box is ticked
        def hint(tag):
            if not self.upd_shown:
                self.upd_shown = True
                ttk.Button(self.bar, text=tr('Update {v} available', v=tag), command=lambda: webbrowser.open(
                    f'https://github.com/{REPO}/releases/latest')).pack(side='right', padx=(0, 16), before=self.dot)
        if self.cfg.get('updates'):
            threading.Thread(target=lambda: (t := newer_release()) and self.ui.put(lambda: hint(t)), daemon=True).start()

    def set_lang(self, code):  # texts are fixed at start: save, close, come back in the new language
        if code != lang.LANG and self.apply():
            self.cfg['lang'] = code
            save(self.cfg)
            self.restart = True
            self.root.destroy()

    def set_layout(self, code):  # the board table is fixed at start, like the language: save, close, come back
        if code != self.cfg.get('layout') and self.apply():
            self.cfg['layout'] = code
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
        self.dev.dock_name = self.page_lbl.cget('text') if self.cfg['dock']['pages'] else 'OnyxIO'
        self.dev.dock_keys = {i: (k.get('title') or '') for i, k in enumerate(self.keys) if k.get('dock')}

    def add_page(self):
        if self.apply():
            self.cfg['pages'].insert(self.cfg['page'] + 1, blank_page())
            self.goto(self.cfg['page'] + 1)

    def del_page(self):
        if len(self.cfg['pages']) > 1 and messagebox.askyesno(
                'OnyxIO', tr('Delete page {n} with all its keys?', n=self.cfg['page'] + 1), parent=self.root):
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
        ttk.Style(self.root).configure('Treeview', rowheight=22)  # colors from the theme
        g = t.insert('', 'end', text=' OnyxIO', open=True)
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
        if p and p.poll() is None and WIN:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True, creationflags=sdhost.NOWIN)
        elif p and p.poll() is None:  # its own session (see pi_show): the whole browser process group goes
            os.killpg(p.pid, 9)

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
            prof = sdhost.CACHE / 'pi'
            prefs = prof / 'Default' / 'Preferences'  # no "translate this page?" bubble (no flag for that)
            try:
                pr = json.loads(prefs.read_text('utf-8'))
            except (OSError, ValueError):
                pr = {}
            if pr.get('translate', {}).get('enabled') is not False:
                prefs.parent.mkdir(parents=True, exist_ok=True)
                prefs.write_text(json.dumps({**pr, 'translate': {'enabled': False}}), 'utf-8')
            self.pi_ctx = ctx
            # ponytail: Linux shows the settings in their own window; embedding would need X11 reparenting
            self.pi_proc = subprocess.Popen([exe, f'--app={url}', f'--user-data-dir={prof}', *PI_FLAGS, '--window-size=600,300',
                                             *(['--window-position=-32000,-32000'] if WIN else [])], start_new_session=not WIN)
            if WIN:
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
        on = self.dev.state.startswith(tr('connected'))
        self.dot.config(fg=DIM if not on else ORANGE if tr('error') in self.dev.state else '#3fb950')  # green / trouble / offline
        self.root.after(100, self.poll)


if __name__ == '__main__':
    if WIN:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp window on scaled displays
    if sys.argv[1:2] == ['test']:
        import io, types
        assert ctypes.sizeof(INPUT) == 40 or not WIN
        assert WIN or EV[0x5A] == 44 and EV[0x31] == 2 and EV[0x70] == 59 and EV[0x7C] == 183 and EV[0xB3] == 164
        assert _ver('v1.10.0') > _ver('1.4.0') > _ver('v1.3.9') and _ver(f'v{VERSION}') == _ver(VERSION)
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
        nopad = types.SimpleNamespace(set_key_image=lambda *a: (_ for _ in ()).throw(dm.QLinkError('InsufficientResources')))
        shown[0] = 'd'  # no numpad: the key is skipped (no disconnect) and queued again for a later try
        assert dev.upload(nopad, 1, True) and dev.dirty.get_nowait() == (1, False) and dev.sent[1][0] is None
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
        app.host.light = {'p': {'Key_A': (1, 2, 3), 'Led_KeyboardTop1': (4, 5, 6), 'Nope': (7, 7, 7)}}  # plugin extras
        app.host.on_light()  # no layers: a plugin light alone turns realtime on, the base effect fills the rest
        frame = app.dev.lights(0, {})
        assert frame[30] == (1, 2, 3) and frame[107] == (4, 5, 6) and len(frame) > 100
        app.host.light = {'p': {'all': (9, 9, 9)}}
        assert set(app.dev.lights(0, {}).values()) == {(9, 9, 9)}
        app.host.light = {}
        app.host.on_light()
        assert app.dev.lights is None  # back to the keyboard's own effect
        app.host.on_dock(0, 'Hi')
        assert app.dev.dock_flash[0] == 'Hi'
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
        for fx in range(8):  # every effect gives valid colors everywhere, any time, with 1, 2 or 7 colors
            for cs in (['#ff0000'], ['#ff0000', '#00ff00'], dm.RAINBOW):
                q = _prep({**wave, 'effect': fx, 'colors': cs, 'direction': fx}, 3, 2)
                assert all(0 <= v <= 255 for t in (0, .3, 7.7) for v in led_color(q, t, t - .1, {5: 0.}, {5: (1, 1)})), (fx, cs)
        rp = _prep({'effect': 6, 'direction': 3, 'brightness': 100, 'speed': 50, 'colors': ['#ff0000']}, 10, 1)  # Ripple
        assert led_color(rp, 10 / (RIPPLE[0] * .35), None, {1: 0.}, {1: (0, 1)})[0] > \
            led_color(rp, 0, None, {1: 0.}, {1: (0, 1)})[0] == 0  # dark until the ring (speed .35) reaches d=10, then lit
        assert led_color(rp, 3, None, {1: 0.}, {1: (0, 1)})[0] == 0  # gone once the ring passed its reach
        bg = _prep({'effect': 0, 'direction': 0, 'brightness': 100, 'speed': 50, 'colors': ['#003000']}, 10, 1)
        rpb = {**rp, 'bg': bg}  # Ripple over a green base effect
        assert led_color(rpb, 0, None, {}, {})[1] == 0x30  # at rest the base effect shows through
        peak = led_color(rpb, 10 / (RIPPLE[0] * .35), None, {1: 0.}, {1: (0, 1)})
        assert peak[0] > 0 and peak[1] == 0x30  # the red ring adds on top of the green background
        rw = {'effect': 6, 'direction': 3, 'brightness': 100, 'speed': 50, 'colors': ['#ff0000']}
        thin, wide = _prep({**rw, 'thick': 0}, 12, 1), _prep({**rw, 'thick': 100}, 12, 1)  # LED at d=12, ring at r=10
        assert led_color(wide, 10 / (RIPPLE[0] * .35), None, {1: 0.}, {1: (0, 1)})[0] > \
            led_color(thin, 10 / (RIPPLE[0] * .35), None, {1: 0.}, {1: (0, 1)})[0]  # a wider wave reaches further
        rr = _prep({**rw, 'random': True}, 10, 1)  # random drop: two presses at different times give different hues
        at = lambda hj: led_color(rr, hj + 10 / (RIPPLE[0] * .35), None, {1: hj}, {1: (0, 1)})
        assert at(0.) != at(1.3) and sum(at(0.)) > 0  # a colour, and not the same one each press
        cu = {'effect': 7, 'direction': 3, 'brightness': 100, 'speed': 50, 'colors': dm.RAINBOW}  # Custom (Baukasten)
        for pat in range(1, 5):  # Linear/Radial/Spiral/Random move: the field is not frozen over time (Still, pat 0, is)
            cp = _prep({**cu, 'pat': pat}, 7, 2)
            assert led_color(cp, 0, None) != led_color(cp, 2.5, None), pat
        cf = _prep({**cu, 'pat': 0, 'react': 1}, 7, 2)  # Flash: pressed key is brighter than the resting field
        assert sum(led_color(cf, 10, 10)) > sum(led_color(cf, 10, None))
        cw = _prep({**cu, 'pat': 2, 'react': 2}, 10, 1)  # Wave: a ring adds over the field
        assert sum(led_color(cw, 10 / (RIPPLE[0] * .35), None, {1: 0.}, {1: (0, 1)})) > sum(led_color(cw, 0, None, {}, {}))
        assert _compile_formula('sin(t) + x/3 if d < 5 else a')  # Phase 3: a valid formula compiles
        for bad in ('__import__("os")', 'x.__class__', 't**9', 'open("f")', '(1).real', 'lambda: 0'):
            try:
                _compile_formula(bad)
                raise AssertionError(bad)  # every escape / exponent must be rejected before it can run
            except (ValueError, SyntaxError):
                pass
        cff = _prep({**cu, 'free': True, 'formula': 'sin(t*2) + d/5'}, 7, 2)  # free field compiles and moves over time
        assert cff['code'] is not None and led_color(cff, 0, None) != led_color(cff, 1.0, None)
        cbad = _prep({**cu, 'free': True, 'formula': '1/0'}, 7, 2)  # compiles, errors per frame -> dark, never raises
        assert all(0 <= v <= 255 for v in led_color(cbad, 1, None))
        cinv = _prep({**cu, 'free': True, 'formula': 'x.__class__'}, 7, 2)  # rejected -> code None -> falls back to pattern
        assert cinv['code'] is None and all(0 <= v <= 255 for v in led_color(cinv, 1, None))

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
        # UI: a layer, keys clicked into it, OnyxIO draws it
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
        app.pv_on.set(True)
        app.preview_start()  # one live-preview frame colours the picture from the very same led_color, no crash
        assert app.pv_names[171] == 'Led_NumpadTop1' and app.kb.itemcget('led_Led_NumpadTop1', 'fill').startswith('#')
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
        for name, n in (('DE', 105), ('US', 104), ('UK', 105)):  # every board builds, maps to HID and fits the box
            build_layout(name)
            iso = name != 'US'
            assert len(KEYNAMES) == n and set(KEYNAMES) <= set(KEYPOS) and set(KEYNAMES) - set(HID) == {'Key_Fn'}
            assert ('Key_NonUsTilde' in KEYNAMES) == iso and ('Key_Backslash' in KEYNAMES) == (not iso)
            assert 'Key_Numpad7' in KEYNAMES and all(0 <= x <= 24.25 and 0 <= y <= 6.25 for x, y in KEYPOS.values())
            assert KEYPOS['Key_RightShift'][0] < 20.25 - M  # alpha block never reaches the numpad
        build_layout('DE')  # the rest of the tests assume the ISO-DE board
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
        t = 'OnyxIOSelfTest'  # autostart on a throwaway entry
        assert not autostart(None, t) and autostart(True, t) and autostart(None, t)
        if WIN:
            import winreg
            assert '--min' in winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN), t)[0]
        else:
            assert '--min' in (Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / f'autostart/{t}.desktop').read_text()
        assert not autostart(False, t) and not autostart(None, t)
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
            root.withdraw() if WIN else root.iconify()
        root.mainloop()
        app.tray.stop()
        app.pi_close()
        app.host.stop()
        if app.restart:
            # the .exe child must unpack its own copy: by default it reuses ours, which is deleted as we exit (icon.ico gone)
            subprocess.Popen([sys.executable, *([] if lang.FROZEN else [os.path.abspath(__file__)]), *[a for a in sys.argv[1:] if a != '--min']],
                             env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'})
