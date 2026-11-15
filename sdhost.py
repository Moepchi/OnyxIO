"""Host for Elgato Stream Deck plugins (SDK 2) on the Dark Mount display keys.

Plugins talk to us over a local WebSocket exactly as they would to the Stream Deck app. Their settings pages
(Property Inspector) are served from the same port and open in a small Edge window. HTML plugins run in a headless Edge.
Skipped: encrypted Marketplace plugins (decrypting them would bypass Elgato's DRM).
Self-check: python sdhost.py test
"""
import base64, functools, hashlib, io, json, logging, os, platform, shutil, socket, subprocess, threading, webbrowser
import ctypes, winreg
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit
from PIL import Image
from lang import HOME, tr

OWN = HOME / 'plugins'  # own installs (open-source builds from GitHub) win over Elgato's copy
PLUGINS = Path(os.environ.get('APPDATA', '')) / 'Elgato/StreamDeck/Plugins'
PROFILES = Path(os.environ.get('LOCALAPPDATA', '')) / 'darkmount/edge'  # one browser profile per HTML plugin
DEVICE = 'DARKMOUNT'
WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
TYPES = {'.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8', '.js': 'text/javascript',
         '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml',
         '.png': 'image/png', '.jpg': 'image/jpeg', '.gif': 'image/gif', '.woff2': 'font/woff2', '.ttf': 'font/ttf'}
log = logging.getLogger('sdhost')


def find_img(base):  # manifest paths come without extension; PIL cannot draw SVG, those stay None
    for ext in ('@2x.png', '.png', '.jpg', '.gif', ''):
        p = Path(f'{base}{ext}')
        if p.is_file() and p.suffix.lower() != '.svg':
            return p


@functools.lru_cache(maxsize=256)
def load_img(p):
    return Image.open(p).convert('RGBA')


def decode(s, base):  # setImage payload: data URL or a file inside the plugin -> PIL image, None if not drawable
    try:
        if s.startswith('data:'):
            head, _, data = s.partition(',')
            return None if 'svg' in head else Image.open(io.BytesIO(base64.b64decode(data))).convert('RGBA')
        p = find_img(base / s)
        return p and load_img(p)
    except (OSError, ValueError):
        return None


def edge():
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            return winreg.QueryValue(root, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe')
        except OSError:
            pass


def scan():
    """-> ({plugin uuid: plugin}, [(folder, why skipped)])"""
    plugins, skipped, seen, node, browser = {}, [], set(), shutil.which('node'), edge()
    for d in sorted(OWN.glob('*.sdPlugin')) + sorted(PLUGINS.glob('*.sdPlugin')):
        if d.name in seen:
            continue
        seen.add(d.name)
        try:
            m = json.loads((d / 'manifest.json').read_text('utf-8-sig'))
        except UnicodeDecodeError:
            skipped.append((d.name, 'encrypted (Marketplace)'))
            continue
        except (OSError, ValueError):
            skipped.append((d.name, 'manifest.json unreadable'))
            continue
        code = m.get('CodePathWin') or m.get('CodePath') or ''
        exe = d / code
        if not exe.is_file() and Path(f'{exe}.exe').is_file():
            exe = Path(f'{exe}.exe')
        kind = exe.suffix.lower()
        html = kind in ('.html', '.htm')
        cmd = ([str(exe)] if kind == '.exe' else [node, str(exe)] if kind in ('.js', '.mjs', '.cjs') and node else
               [browser, '--headless=new', '--no-first-run', '--disable-gpu', '--disable-extensions',  # ~145 MB, not 225
                '--disable-component-extensions-with-background-pages', f'--user-data-dir={PROFILES / d.name}']
               if html and browser else None)
        if not code or not exe.is_file() or not cmd:
            why = 'Edge missing' if html else 'Node.js missing' if kind.endswith('js') else 'no program'
            skipped.append((d.name, why))
            continue
        actions = {}
        for a in m.get('Actions', []):
            if 'Keypad' in a.get('Controllers', ['Keypad']):
                actions[a['UUID']] = {
                    'name': a.get('Name') or a['UUID'], 'pi': a.get('PropertyInspectorPath') or m.get('PropertyInspectorPath'),
                    'states': [{'img': s.get('Image') and find_img(d / s['Image']), 'title': s.get('Title', '')}
                               for s in a.get('States') or [{}]],
                    'icon': a.get('Icon') and find_img(d / a['Icon']), 'auto': not a.get('DisableAutomaticStates')}
        uid = m.get('UUID') or d.name[:-len('.sdPlugin')]
        plugins[uid] = {'uuid': uid, 'name': (m.get('Name') or '').strip() or m.get('Category') or uid, 'dir': d, 'cmd': cmd, 'version': m.get('Version', ''),
                        'actions': actions, 'proc': None, 'conn': None, 'page': html and code,
                        'icon': (m.get('CategoryIcon') or m.get('Icon')) and find_img(d / (m.get('CategoryIcon') or m['Icon']))}
    return plugins, skipped


class Conn:  # one WebSocket peer: a plugin or a property inspector
    def __init__(self, sock):
        self.sock, self.lock = sock, threading.Lock()

    def frame(self, op, data):
        n = len(data)
        size = bytes([n]) if n < 126 else b'\x7e' + n.to_bytes(2, 'big') if n < 65536 else b'\x7f' + n.to_bytes(8, 'big')
        with self.lock:
            try:
                self.sock.sendall(bytes([0x80 | op]) + size + data)
            except OSError:
                pass  # peer gone; its reader thread cleans up

    def send(self, obj):
        self.frame(1, json.dumps(obj).encode())


class Host:
    """cfg: deck.json dict (keys, globals). notify(i): key i looks different; notify(None): cfg needs saving.
    Plugin keys: {'action': 'plugin', 'value': action uuid, 'context': id, 'settings': {...}} + the usual look."""

    def __init__(self, cfg, notify):
        self.cfg, self.notify = cfg, notify
        self.plugins, self.skipped = scan()
        self.owner = {a: p for p in self.plugins.values() for a in p['actions']}
        self.live, self.pis, self.port = {}, {}, 0  # live: context -> {'state', 'img', 'title'}; pis: context -> Conn

    @property
    def keys(self):  # the page on the display keys right now; the other pages are "disappeared"
        return self.cfg['pages'][self.cfg['page']]

    def start(self):
        self.srv = socket.create_server(('127.0.0.1', 0))
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()
        for i, k in enumerate(self.keys):
            if k.get('action') == 'plugin':
                self.appear(i)

    def stop(self):
        for p in self.plugins.values():
            if p['proc'] and p['proc'].poll() is None:
                p['proc'].terminate()

    def choices(self):  # [(label, action uuid)] for the action list
        return sorted((f'{p["name"]}: {a["name"]}', au) for p in self.plugins.values() for au, a in p['actions'].items())

    def has_pi(self, action):
        return bool(self.owner.get(action) and self.owner[action]['actions'][action]['pi'])

    # --- keys -----------------------------------------------------------------------------------------------------
    def index(self, ctx):
        return next((i for i, k in enumerate(self.keys) if k.get('action') == 'plugin' and k.get('context') == ctx), None)

    def payload(self, i, k=None):
        k = k or self.keys[i]
        return {'settings': k.get('settings') or {}, 'coordinates': {'column': i % 4, 'row': i // 4},
                'state': self.live.get(k.get('context'), {}).get('state', 0), 'isInMultiAction': False,
                'controller': 'Keypad'}

    def to_plugin(self, i, event, k=None, **extra):
        k = k or self.keys[i]
        p = self.owner.get(k.get('value'))
        if p and p['conn']:
            p['conn'].send({'action': k['value'], 'event': event, 'context': k['context'], 'device': DEVICE, **extra})

    def appear(self, i):
        k = self.keys[i]
        p = self.owner.get(k['value'])
        if p:
            self.live.setdefault(k['context'], {'state': 0, 'img': {}, 'title': {}})
            self.to_plugin(i, 'willAppear', payload=self.payload(i))
            self.launch(p)

    def disappear(self, i, k):  # k: the key config that is going away
        self.to_plugin(i, 'willDisappear', k, payload=self.payload(i, k))
        self.live.pop(k['context'], None)

    def key(self, i, down):
        k = self.keys[i]
        p, lv = self.owner.get(k.get('value')), self.live.get(k.get('context'))
        if not p or lv is None:
            return
        self.launch(p)  # crashed plugins come back with the next press
        self.to_plugin(i, 'keyDown' if down else 'keyUp', payload=self.payload(i))
        a = p['actions'][k['value']]
        if not down and a['auto'] and len(a['states']) > 1:  # like Stream Deck: multi-state keys flip on release
            lv['state'] = (lv['state'] + 1) % len(a['states'])
            self.notify(i)

    def decorate(self, k):
        """Key config -> what render() draws: live plugin image > own icon > manifest image > action icon + name."""
        p = self.owner.get(k.get('value'))
        if not p:
            return dict(k, title=k.get('title') or tr('Plugin\nmissing'))
        a, lv = p['actions'][k['value']], self.live.get(k.get('context'), {'state': 0, 'img': {}, 'title': {}})
        s = min(lv['state'], len(a['states']) - 1)
        out = dict(k, title=k.get('title') or lv['title'].get(s, lv['title'].get(None)) or a['states'][s]['title'])
        img = lv['img'].get(s, lv['img'].get(None))
        if img is None and not k.get('icon') and a['states'][s]['img']:
            img = load_img(a['states'][s]['img'])
        if img is not None:
            out['img'] = img
        elif not k.get('icon'):
            out['icon'], out['title'] = a['icon'] and str(a['icon']), out['title'] or a['name']
        return out

    def pi_url(self, i):  # key i's property inspector page, or None
        k = self.keys[i]
        p = self.owner.get(k.get('value'))
        if not self.port or not p or not p['actions'][k['value']]['pi']:
            return None
        self.launch(p)
        return f'http://127.0.0.1:{self.port}/{quote(p["uuid"])}/{quote(p["actions"][k["value"]]["pi"])}?context={k["context"]}'

    # --- plugin processes ---------------------------------------------------------------------------------------
    def info(self, p):
        return {'application': {'font': 'Segoe UI', 'language': 'de', 'platform': 'windows',
                                'platformVersion': platform.version(), 'version': '6.9.0.0'},
                'plugin': {'uuid': p['uuid'], 'version': p['version']}, 'devicePixelRatio': 2,
                'colors': {'buttonPressedBackgroundColor': '#303030FF', 'buttonPressedBorderColor': '#646464FF',
                           'buttonPressedTextColor': '#969696FF', 'disabledColor': '#007AFF59',
                           'highlightColor': '#007AFFFF', 'mouseDownColor': '#2EA8FFFF'},
                'devices': [{'id': DEVICE, 'name': 'Dark Mount', 'size': {'columns': 4, 'rows': 2}, 'type': 0}]}

    def launch(self, p):
        if p['conn'] or (p['proc'] and p['proc'].poll() is None):
            return
        args = ([f'http://127.0.0.1:{self.port}/{quote(p["uuid"])}/{quote(p["page"])}?plugin=1'] if p.get('page') else
                ['-port', str(self.port), '-pluginUUID', p['uuid'], '-registerEvent', 'registerPlugin',
                 '-info', json.dumps(self.info(p))])
        try:
            p['proc'] = subprocess.Popen(p['cmd'] + args, cwd=p['dir'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            log.info('started %s', p['uuid'])
        except OSError as e:
            log.warning('cannot start %s: %s', p['uuid'], e)

    # --- server -------------------------------------------------------------------------------------------------
    def accept(self):
        while True:
            s, _ = self.srv.accept()
            threading.Thread(target=self.serve, args=(s,), daemon=True).start()

    def serve(self, sock):
        with sock:
            try:
                f = sock.makefile('rb')
                method, path, _ = f.readline(8192).decode('latin-1').split(' ', 2)
                h = {}
                while (line := f.readline(8192).decode('latin-1').strip()):
                    name, _, v = line.partition(':')
                    h[name.strip().lower()] = v.strip()
            except (OSError, ValueError):
                return
            # only plugins (no Origin) and our own pages may connect, not any website open in the browser
            own = {f'127.0.0.1:{self.port}', f'localhost:{self.port}'}
            if h.get('host') not in own or ('origin' in h and h['origin'].removeprefix('http://') not in own):
                log.warning('rejected host=%s origin=%s', h.get('host'), h.get('origin'))
                sock.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                return
            if h.get('upgrade', '').lower() == 'websocket':
                acc = base64.b64encode(hashlib.sha1((h.get('sec-websocket-key', '') + WS_GUID).encode()).digest())
                sock.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                             b'Sec-WebSocket-Accept: ' + acc + b'\r\n\r\n')
                self.ws(Conn(sock), f)
            elif method == 'GET':
                self.http(sock, path)

    def http(self, sock, path):
        u = urlsplit(path)
        uid, _, rel = unquote(u.path).lstrip('/').partition('/')
        p = self.plugins.get(uid)
        f = (p['dir'] / rel).resolve() if p else None
        if not f or not f.is_file() or not f.is_relative_to(p['dir'].resolve()):
            sock.sendall(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            return
        body = f.read_bytes()
        q = parse_qs(u.query)
        i = self.index(q.get('context', [''])[0])
        if f.suffix.lower() in ('.html', '.htm') and (i is not None or 'plugin' in q):  # what Stream Deck does after loading
            if i is None:  # an HTML plugin's own page
                args = [p['uuid'], 'registerPlugin', json.dumps(self.info(p))]
            else:  # a settings page
                k = self.keys[i]
                action = {'action': k['value'], 'context': k['context'], 'device': DEVICE, 'payload': self.payload(i)}
                args = [k['context'], 'registerPropertyInspector', json.dumps(self.info(p)), json.dumps(action)]
            js = f'connectElgatoStreamDeckSocket({", ".join(map(json.dumps, [self.port] + args))})'.replace('</', '<\\/')
            # module scripts (Vue, React) may define the function late, so wait for it
            body += ('<script>addEventListener("load", function go() { window.connectElgatoStreamDeckSocket ? '
                     f'{js} : setTimeout(go, 50) }})</script>').encode()
        head = (f'HTTP/1.1 200 OK\r\nContent-Type: {TYPES.get(f.suffix.lower(), "application/octet-stream")}\r\n'
                f'Content-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n')
        sock.sendall(head.encode() + body)

    def ws(self, conn, f):
        who, msg = None, b''
        try:
            while True:
                head = f.read(2)
                if len(head) < 2:
                    break
                op, n = head[0] & 15, head[1] & 127
                if n > 125:
                    n = int.from_bytes(f.read(2 if n == 126 else 8), 'big')
                mask = f.read(4) if head[1] & 128 else b'\0' * 4
                data = f.read(n)
                if len(data) < n:
                    break
                data = (int.from_bytes(data, 'little') ^ int.from_bytes((mask * (n // 4 + 1))[:n], 'little')).to_bytes(n, 'little')
                if op == 8:
                    break
                if op == 9:
                    conn.frame(10, data)
                elif op in (0, 1, 2):
                    msg += data
                    if head[0] & 0x80:
                        try:
                            who = self.handle(conn, who, json.loads(msg)) or who
                        except (ValueError, KeyError, TypeError, AttributeError) as e:
                            log.warning('bad message from %s: %s', who, e)
                        msg = b''
        except OSError:
            pass
        if who and who[0] == 'plugin' and self.plugins[who[1]]['conn'] is conn:
            self.plugins[who[1]]['conn'] = None
        elif who and who[0] == 'pi' and self.pis.get(who[1]) is conn:
            del self.pis[who[1]]
            i = self.index(who[1])
            if i is not None:
                self.to_plugin(i, 'propertyInspectorDidDisappear')

    def handle(self, conn, who, m):
        ev, ctx, pl = m.get('event'), m.get('context'), m.get('payload') or {}
        if ev == 'registerPlugin' and m.get('uuid') in self.plugins:
            p = self.plugins[m['uuid']]
            p['conn'] = conn
            conn.send({'event': 'deviceDidConnect', 'device': DEVICE,
                       'deviceInfo': {'name': 'Dark Mount', 'type': 0, 'size': {'columns': 4, 'rows': 2}}})
            for i, k in enumerate(self.keys):
                if k.get('action') == 'plugin' and self.owner.get(k['value']) is p:
                    self.live.setdefault(k['context'], {'state': 0, 'img': {}, 'title': {}})
                    self.to_plugin(i, 'willAppear', payload=self.payload(i))
            log.info('registered %s', p['uuid'])
            return 'plugin', p['uuid']
        if ev == 'registerPropertyInspector' and self.index(m.get('uuid')) is not None:
            self.pis[m['uuid']] = conn
            self.to_plugin(self.index(m['uuid']), 'propertyInspectorDidAppear')
            return 'pi', m['uuid']
        if not who:
            return
        from_plugin = who[0] == 'plugin'
        if ev in ('setGlobalSettings', 'getGlobalSettings'):
            uid = who[1] if from_plugin else self.owner[self.keys[self.index(who[1])]['value']]['uuid']
            g = self.cfg.setdefault('globals', {})
            note = {'event': 'didReceiveGlobalSettings', 'payload': {'settings': g.get(uid, {})}}
            if ev == 'getGlobalSettings':
                return conn.send(note)
            g[uid] = note['payload']['settings'] = pl
            self.notify(None)
            if not from_plugin and self.plugins[uid]['conn']:
                self.plugins[uid]['conn'].send(note)
            for c, pc in list(self.pis.items()) if from_plugin else []:  # the plugin's open settings pages
                j = self.index(c)
                if j is not None and self.owner.get(self.keys[j]['value']) is self.plugins[uid]:
                    pc.send(note)
            return
        if ev == 'openUrl' and str(pl.get('url', '')).startswith(('http://', 'https://')):
            return webbrowser.open(pl['url'])
        if ev == 'logMessage':
            return log.info('%s: %s', who[1], pl.get('message'))
        i = self.index(ctx if from_plugin else who[1])
        if i is None:
            return
        k = self.keys[i]
        lv = self.live.setdefault(k['context'], {'state': 0, 'img': {}, 'title': {}})
        note = {'action': k['value'], 'event': 'didReceiveSettings', 'context': k['context'], 'device': DEVICE,
                'payload': self.payload(i)}
        if ev == 'getSettings':
            conn.send(note)
        elif ev == 'setSettings':
            k['settings'] = note['payload']['settings'] = pl
            self.notify(None)
            other = self.pis.get(k['context']) if from_plugin else self.owner[k['value']]['conn']
            if other:
                other.send(note)
        elif ev == 'sendToPlugin':
            self.to_plugin(i, 'sendToPlugin', payload=pl)
        elif ev == 'sendToPropertyInspector' and k['context'] in self.pis:
            self.pis[k['context']].send({'action': k['value'], 'event': ev, 'context': k['context'], 'payload': pl})
        elif ev in ('setImage', 'setTitle', 'setState') and from_plugin:
            if ev == 'setState':
                lv['state'] = int(pl.get('state', 0))
            else:  # one state, or all states when none is given; empty = back to the default
                raw = pl.get('image' if ev == 'setImage' else 'title')
                v = decode(raw, self.owner[k['value']]['dir']) if ev == 'setImage' and raw else raw
                if raw and v is None:
                    return  # SVG or broken image: keep the last one
                d, s = lv['img' if ev == 'setImage' else 'title'], pl.get('state')
                if s is None:
                    d.clear()
                if v in (None, ''):
                    d.pop(s, None)
                else:
                    d[s] = v
            self.notify(i)


if __name__ == '__main__' and __import__('sys').argv[1:2] == ['test']:
    import time
    events, flags = [], []
    host = Host({'pages': [[{'action': 'plugin', 'value': 'x.act', 'context': 'c1', 'settings': {'a': 1}}] +
                           [{}] * 7], 'page': 0}, flags.append)
    host.plugins['x'] = {'uuid': 'x', 'name': 'X', 'dir': Path('.'), 'cmd': [], 'version': '1', 'proc': None, 'conn': None,
                         'actions': {'x.act': {'name': 'Act', 'pi': None, 'states': [{'img': None, 'title': ''}] * 2,
                                               'icon': None, 'auto': True}}}
    host.owner['x.act'] = host.plugins['x']
    host.launch = lambda p: None
    host.start()

    def frame(obj):  # client frame, masked like every real client
        data, mask = json.dumps(obj).encode(), b'\x01\x02\x03\x04'
        return bytes([0x81, 0x80 | 126]) + len(data).to_bytes(2, 'big') + mask + bytes(b ^ mask[j % 4] for j, b in enumerate(data))

    def recv(f):
        b0, b1 = f.read(2)
        n = int.from_bytes(f.read(2), 'big') if b1 & 127 == 126 else b1 & 127
        return json.loads(f.read(n))

    c = socket.create_connection(('127.0.0.1', host.port))
    c.sendall(f'GET / HTTP/1.1\r\nHost: 127.0.0.1:{host.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
              'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n'.encode())
    f = c.makefile('rb')
    assert b's3pPLMBiTxaQ9kYGzzhZRbK+xOo=' in f.read(129)  # RFC 6455 example key -> accept value
    c.sendall(frame({'event': 'registerPlugin', 'uuid': 'x'}))
    assert recv(f)['event'] == 'deviceDidConnect'
    assert recv(f)['payload']['settings'] == {'a': 1}  # willAppear
    big = 'data:image/png;base64,' + base64.b64encode(b'x' * 300).decode()
    c.sendall(frame({'event': 'setSettings', 'context': 'c1', 'payload': {'b': 2, 'pad': big}}))
    c.sendall(frame({'event': 'setTitle', 'context': 'c1', 'payload': {'title': 'Hi'}}))
    for _ in range(50):
        if 0 in flags:
            break
        time.sleep(0.02)
    assert host.keys[0]['settings']['b'] == 2 and None in flags and 0 in flags
    assert host.decorate(host.keys[0])['title'] == 'Hi'
    host.key(0, False)  # release flips a two-state key
    assert host.live['c1']['state'] == 1 and recv(f)['event'] == 'keyUp'
    bad = socket.create_connection(('127.0.0.1', host.port))  # a website must not get in
    bad.sendall(f'GET / HTTP/1.1\r\nHost: 127.0.0.1:{host.port}\r\nOrigin: https://evil.example\r\nUpgrade: websocket\r\n\r\n'.encode())
    assert bad.recv(64).startswith(b'HTTP/1.1 403')
    import tempfile
    with tempfile.TemporaryDirectory() as t:  # an HTML plugin's page gets the registerPlugin call appended
        Path(t, 'p.html').write_text('<p>hi</p>')
        host.plugins['h'] = dict(host.plugins['x'], uuid='h', dir=Path(t), page='p.html', actions={})
        g = socket.create_connection(('127.0.0.1', host.port))
        g.sendall(f'GET /h/p.html?plugin=1 HTTP/1.1\r\nHost: 127.0.0.1:{host.port}\r\n\r\n'.encode())
        assert b'"h", "registerPlugin"' in g.makefile('rb').read()
    print('ok')
