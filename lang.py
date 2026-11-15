"""UI language. English is the source language; other languages map its texts. deck.json "lang" picks one, without
it Windows' display language decides (German -> Deutsch, anything else -> English). Texts are fixed at start."""
import ctypes, json, sys
from pathlib import Path

LANGS = {'en': 'English', 'de': 'Deutsch'}
FROZEN = getattr(sys, 'frozen', False)  # running as the PyInstaller .exe
HOME = Path(sys.executable if FROZEN else __file__).parent  # deck.json, deck.log and plugins live here


def pick():
    try:
        lang = json.loads((HOME / 'deck.json').read_text('utf-8')).get('lang')
    except (OSError, ValueError):
        lang = None
    if lang in LANGS:
        return lang
    return 'de' if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x07 else 'en'


LANG = 'en' if sys.argv[1:2] == ['test'] else pick()  # self-tests check the English texts


def tr(s, **kw):
    s = DE.get(s, s) if LANG == 'de' else s
    return s.format(**kw) if kw else s


DE = {
    # display keys
    'No action': 'Keine Aktion',
    'Open program / file / folder': 'Programm / Datei / Ordner öffnen',
    'Path, e.g. C:\\Windows\\notepad.exe': 'Pfad, z. B. C:\\Windows\\notepad.exe',
    'Open website': 'Website öffnen',
    'e.g. https://www.youtube.com': 'z. B. https://www.youtube.com',
    'Send hotkey': 'Tastenkürzel senden',
    'e.g. ctrl+shift+m, win+d, alt+f4, f13': 'z. B. ctrl+shift+m, win+d, alt+f4, f13',
    'Type text': 'Text eingeben',
    'typed at the cursor position': 'wird an der Cursorposition getippt',
    'Media / volume': 'Medien / Lautstärke',
    'Run command': 'Befehl ausführen',
    'e.g. powershell -File C:\\scripts\\x.ps1': 'z. B. powershell -File C:\\skripte\\x.ps1',
    'Previous page': 'Vorherige Seite',
    'the last one comes before the first': 'vor der ersten kommt die letzte',
    'Next page': 'Nächste Seite',
    'after the last one comes the first again': 'nach der letzten kommt wieder die erste',
    'Next track': 'Nächster Titel',
    'Previous track': 'Vorheriger Titel',
    'Stop': 'Stopp',
    'Mute': 'Stumm',
    'Volume up': 'Lauter',
    'Volume down': 'Leiser',
    'Unknown key in hotkey: "{name}"': 'Unbekannte Taste im Kürzel: "{name}"',
    'Please choose a media action.': 'Bitte eine Medien-Aktion wählen.',
    'Keys': 'Tasten',
    'New page': 'Neue Seite',
    'Delete page': 'Seite löschen',
    'Page {n} / {total}': 'Seite {n} / {total}',
    'Delete page {n} with all its keys?': 'Seite {n} mit allen Tasten löschen?',
    'Plugin settings': 'Plugin-Einstellungen',
    'Actions – drag onto a key or double-click': 'Aktionen – auf eine Taste ziehen oder doppelklicken',
    'Key {n}': 'Taste {n}',
    'Title': 'Titel',
    'Background': 'Hintergrund',
    'Image': 'Bild',
    'Rotate image': 'Bild drehen',
    'Rotate all': 'Alles drehen',
    'Action': 'Aktion',
    'Target / value': 'Ziel / Wert',
    'Choose …': 'Wählen …',
    'Remove': 'Entfernen',
    'File …': 'Datei …',
    'Folder …': 'Ordner …',
    'Apply': 'Übernehmen',
    'Quit': 'Beenden',
    'Open': 'Öffnen',
    '(none)': '(keins)',
    'Images': 'Bilder',
    'All files': 'Alle Dateien',
    'Settings appear below': 'Einstellungen erscheinen unten',
    'Plugin missing: {id}': 'Plugin fehlt: {id}',
    'Plugin\nmissing': 'Plugin\nfehlt',
    'Stream Deck plugins loaded: {n}, skipped: {skipped} (encrypted Marketplace plugins, list in deck.log; '
    'own plugins go into the “plugins” folder)':
        'Stream-Deck-Plugins geladen: {n}, übersprungen: {skipped} (verschlüsselte Marketplace-Plugins, Liste in '
        'deck.log; eigene Plugins in den Ordner „plugins“)',
    'Loading settings …': 'Einstellungen werden geladen …',
    'Microsoft Edge not found.': 'Microsoft Edge nicht gefunden.',
    'This action has no settings.': 'Diese Aktion hat keine Einstellungen.',
    'Drag a plugin action onto a key – its settings appear here.':
        'Eine Plugin-Aktion auf eine Taste ziehen – ihre Einstellungen erscheinen hier.',
    # keyboard connection (status line)
    'Keyboard: {state}': 'Tastatur: {state}',
    'connecting …': 'verbinde …',
    'connected': 'verbunden',
    'connected, {what}': 'verbunden, {what}',
    'connected, error: {e}': 'verbunden, Fehler: {e}',
    'connected, error on key {n}: {e}': 'verbunden, Fehler bei Taste {n}: {e}',
    'disconnected ({why}), retrying …': 'getrennt ({why}), neuer Versuch …',
    'no answer': 'keine Antwort',
    'busy – is IO Center running?': 'belegt, läuft IO Center?',
    'Dark Mount (MI_02) not found': 'Dark Mount (MI_02) nicht gefunden',
    # lighting
    'Lighting': 'Beleuchtung',
    'Lighting applied': 'Beleuchtung übernommen',
    'Static': 'Statisch',
    'Color wave': 'Farbwelle',
    'Breathing': 'Atmen',
    'Reactive': 'Reaktiv',
    'Up': 'Hoch',
    'Down': 'Runter',
    'Left': 'Links',
    'Right': 'Rechts',
    'Clockwise': 'Im Uhrzeigersinn',
    'Counterclockwise': 'Gegen den Uhrzeigersinn',
    'One color': 'Einfarbig',
    'Two colors (Reactive: press, rest)': 'Zweifarbig (Reaktiv: Druck, Ruhe)',
    'Gradient': 'Verlauf',
    'Effect': 'Effekt',
    'Direction': 'Richtung',
    'Colors': 'Farben',
    'Brightness %': 'Helligkeit %',
    'Speed %': 'Tempo %',
    'All / none': 'Alle / keine',
    'Map LEDs …': 'LEDs zuordnen …',
    'Import IO Center profile …': 'IO-Center-Profil importieren …',
    'IO Center profile': 'IO-Center-Profil',
    'Cannot read profile: {e}': 'Profil nicht lesbar: {e}',
    'Back': 'Zurück',
    'Next': 'Weiter',
    'Done': 'Fertig',
    'The top one wins, the base effect fills the rest.': 'Oben gewinnt, der Grundeffekt füllt den Rest.',
    'Base effect': 'Grundeffekt',
    'Base effect  ({fx}, rest)': 'Grundeffekt  ({fx}, Rest)',
    'Layer {n}': 'Ebene {n}',
    'Layer': 'Ebene',
    'LED {led} of {first}–{last} is lit white, everything else is off. Click the key in the picture that is lit. '
    'If none is lit or the blue one is already right: “Next”. Green = mapped ({done} of {total}).':
        'LED {led} von {first}–{last} leuchtet weiß, alles andere ist aus. Klicke im Bild die Taste an, die leuchtet. '
        'Leuchtet keine oder stimmt die blaue schon: „Weiter“. Grün = zugeordnet ({done} von {total}).',
    'No LED map yet: run “Map LEDs …” once (one LED after another, about 3 minutes). The frame already works.':
        'Noch keine LED-Zuordnung: einmal „LEDs zuordnen …“ ausführen (eine LED nach der anderen, etwa 3 Minuten). '
        'Der Rahmen geht schon.',
    'The keyboard computes the base effect itself and keeps it even without this program. With Static, Breathing '
    'and Reactive the direction does not matter. “+” adds a layer above it; click its keys and edge LEDs or drag a '
    'box around them.':
        'Den Grundeffekt rechnet die Tastatur selbst und behält ihn auch ohne dieses Programm. Bei Statisch, Atmen '
        'und Reaktiv spielt die Richtung keine Rolle. Mit „+“ kommt eine Ebene darüber; ihre Tasten und Rand-LEDs '
        'anklicken oder mit gedrückter Maus einrahmen.',
    'Click keys and edge LEDs or drag a box around them (Shift = add, Ctrl = toggle; blue outline = in this layer). '
    'The keyboard shows layers only while this program runs, the base effect afterwards.':
        'Tasten und Rand-LEDs anklicken oder mit gedrückter Maus einrahmen (Shift = dazu, Strg = an/aus; blau '
        'umrandet = in dieser Ebene). Ebenen zeigt die Tastatur nur, solange dieses Programm läuft, danach wieder '
        'den Grundeffekt.',
    ' {n} keys have no LED yet.': ' {n} Tasten haben noch keine LED.',
    # macros
    'Macros': 'Makros',
    'Macro': 'Makro',
    'Macro {n}': 'Makro {n}',
    'Reload': 'Neu laden',
    'Playback': 'Abspielen',
    'Delays': 'Pausen',
    'Steps': 'Schritte',
    'Once': 'Einmal',
    'Hold (while pressed)': 'Halten (solange gedrückt)',
    'Repeat': 'Wiederholen',
    'None': 'Keine',
    'Fixed': 'Fest',
    'As recorded': 'Wie aufgenommen',
    'ms (with “Fixed”)': 'ms (bei „Fest“)',
    'Keyboard': 'Tastatur',
    'Mouse': 'Maus',
    'Keyboard + mouse': 'Tastatur + Maus',
    'Wheel': 'Mausrad',
    'released': 'losgelassen',
    '● Record': '● Aufnehmen',
    '■ Stop': '■ Stopp',
    'Recording appends at the end. Several steps: Shift/Ctrl-click.':
        'Aufnahme hängt hinten an. Mehrere Schritte: Shift/Strg-Klick.',
    'Recording – press the keys, then “■ Stop”.': 'Aufnahme läuft – drücke die Tasten, dann „■ Stopp“.',
    'Delay after (ms)': 'Pause danach (ms)',
    'Set': 'Setzen',
    'Assign key …': 'Taste zuweisen …',
    'Press a key …': 'Taste drücken …',
    'Now press the key that should trigger the macro.': 'Drücke jetzt die Taste, die das Makro auslösen soll.',
    '{key} cannot be assigned.': '{key} lässt sich nicht belegen.',
    '{key} now triggers “{macro}”.': '{key} löst jetzt „{macro}“ aus.',
    'Unbind': 'Lösen',
    'none': 'keine',
    'Delete “{name}”?': '„{name}“ löschen?',
    'Macros read': 'Makros gelesen',
    'Key assigned': 'Taste belegt',
    'Keys unbound': 'Tasten gelöst',
    'Macro created': 'Makro angelegt',
    'Macro deleted': 'Makro gelöscht',
    'Macro saved': 'Makro gespeichert',
    'Macros live on the keyboard and run there even without this program, like with IO Center. Deck reads your '
    'key presses for this only while recording or during “Assign key”. Display keys and Fn cannot be recorded or '
    'assigned; set up display keys under “Keys”.':
        'Makros liegen auf der Tastatur und laufen dort auch ohne dieses Programm, wie bei IO Center. Nur während der '
        'Aufnahme oder „Taste zuweisen“ wertet Deck deine Tastendrücke dafür aus. Displaytasten und Fn lassen sich '
        'nicht aufnehmen oder belegen; Displaytasten stellst du unter „Tasten“ ein.',
}
