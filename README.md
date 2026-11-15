# Dark Mount Deck

A lean, open replacement for IO Center for the **be quiet! Dark Mount** keyboard on Windows: the eight display keys
work like a Stream Deck, plus lighting and macros. One Python program, no installer, no account, no telemetry.

**[Deutsch weiter unten](#deutsch)**

![Keys tab](docs/keys.png)

> Unofficial community project, not affiliated with be quiet! or Elgato. It talks to the keyboard only through the
> same configuration protocol IO Center uses. It never flashes firmware and never touches the bootloader.

## Features

### Display keys
- Title, background color and image per key (PNG, JPG, GIF, …), rotate the image or the whole key.
- Actions: open a program / file / folder, open a website, send a hotkey (`ctrl+shift+m`, `win+d`, `f13`, …),
  type text, media / volume, run a command, previous / next page.
- **Pages** like on a Stream Deck: as many pages of 8 keys as you like; next/previous wrap around.
- Action list grouped by plugin – **drag an action onto a key** or double-click it.

### Stream Deck plugins
- Runs Elgato Stream Deck plugins (SDK 2) on the display keys: plugins installed by the Stream Deck app are found
  automatically, your own go into the `plugins` folder.
- Node.js and HTML plugins (HTML ones run in a hidden Microsoft Edge).
- The plugin's **settings page (Property Inspector) is embedded** right in the window.
- Encrypted Marketplace plugins are skipped on purpose (no DRM circumvention); see `deck.log` for the list.

### Lighting
- Base effect computed by the keyboard itself (keeps running without this program): Static, Color wave, Tornado,
  Breathing, Reactive, Matrix – with direction, one / two colors or gradient, brightness and speed.
- **Layers** on top: pick keys and edge LEDs with a click or a drag box, each layer with its own effect (shown while
  the program runs).
- Live preview of the whole keyboard incl. the edge LEDs and the display key images.
- Import an IO Center profile (`.ioprofile`).
- LED mapping wizard for keys that have no LED assigned yet.

### Macros
- Macros are stored **on the keyboard** and work without this program.
- Record keyboard and mouse, edit steps and delays, playback once / while held / repeat.
- Assign a macro to a key or unbind it.

### Other
- English and German UI (switch bottom right; default follows the Windows display language).
- Closing the window minimizes it; the keys keep working. **Quit** ends the program.
- Privacy: key presses are evaluated only for the display keys, the Reactive preview (memory only) and while *you*
  record a macro or assign a key. Nothing is logged.

![Lighting tab](docs/lighting.png)

## Requirements
- Windows 10 / 11, be quiet! Dark Mount
- Python 3.10+ and Pillow (`pip install -r requirements.txt`)
- Optional: Node.js (for Node plugins), Microsoft Edge (for HTML plugins and plugin settings, preinstalled on Windows)

## Start
```bash
pip install -r requirements.txt
pythonw deck.py
```
- `pythonw deck.py --min` starts minimized (e.g. from the Windows autostart folder, `shell:startup`).
- **Close IO Center first** – the keyboard accepts only one program at a time.
- Settings are saved in `deck.json` next to the program; plugin output goes to `deck.log`.
- Self-tests: `python deck.py test` and `python sdhost.py test` (no keyboard needed).

## Files
| File | What |
|---|---|
| `deck.py` | the program (window, display keys, lighting, macros) |
| `sdhost.py` | host for Stream Deck plugins |
| `darkmount.py` | minimal client for the keyboard's protocol |
| `lang.py` | UI texts: English → German |
| `PROTOCOL.md` | protocol notes (German), observed from IO Center's own traffic |

## Adding a language
Copy the `DE` dictionary in `lang.py`, translate the values and add the code to `LANGS` and `tr()`.
Pull requests welcome.

## License
MIT – see [LICENSE](LICENSE). Stream Deck plugins keep their own licenses. "be quiet!", "Dark Mount", "Elgato" and
"Stream Deck" are trademarks of their owners.

---

<a id="deutsch"></a>
# Dark Mount Deck (Deutsch)

Ein schlanker, offener Ersatz für IO Center für die **be quiet! Dark Mount** unter Windows: Die acht Displaytasten
funktionieren wie ein Stream Deck, dazu Beleuchtung und Makros. Ein Python-Programm, kein Installer, kein Konto,
keine Telemetrie.

> Inoffizielles Community-Projekt, nicht verbunden mit be quiet! oder Elgato. Es spricht mit der Tastatur nur über
> dasselbe Konfigurationsprotokoll wie IO Center. Es flasht nie Firmware und rührt den Bootloader nicht an.

## Funktionen

### Displaytasten
- Titel, Hintergrundfarbe und Bild pro Taste, Bild oder ganze Taste drehen.
- Aktionen: Programm / Datei / Ordner öffnen, Website öffnen, Tastenkürzel senden, Text eingeben,
  Medien / Lautstärke, Befehl ausführen, vorherige / nächste Seite.
- **Seiten** wie beim Stream Deck: beliebig viele Seiten mit 8 Tasten; vor/zurück läuft im Kreis.
- Aktionsliste nach Plugin gruppiert – **Aktion auf eine Taste ziehen** oder doppelklicken.

### Stream-Deck-Plugins
- Führt Elgato-Stream-Deck-Plugins (SDK 2) auf den Displaytasten aus: Plugins der Stream-Deck-App werden automatisch
  gefunden, eigene kommen in den Ordner `plugins`.
- Node.js- und HTML-Plugins (HTML-Plugins laufen in einem unsichtbaren Microsoft Edge).
- Die **Plugin-Einstellungen (Property Inspector) sind direkt ins Fenster eingebettet**.
- Verschlüsselte Marketplace-Plugins werden bewusst übersprungen (keine DRM-Umgehung); Liste in `deck.log`.

### Beleuchtung
- Grundeffekt, den die Tastatur selbst rechnet (läuft auch ohne dieses Programm): Statisch, Farbwelle, Tornado,
  Atmen, Reaktiv, Matrix – mit Richtung, ein / zwei Farben oder Verlauf, Helligkeit und Tempo.
- **Ebenen** darüber: Tasten und Rand-LEDs anklicken oder einrahmen, jede Ebene mit eigenem Effekt (sichtbar, solange
  das Programm läuft).
- Live-Vorschau der ganzen Tastatur inkl. Rand-LEDs und Displaytasten-Bildern.
- IO-Center-Profil (`.ioprofile`) importieren.
- Assistent zum Zuordnen der LEDs.

### Makros
- Makros liegen **auf der Tastatur** und funktionieren ohne dieses Programm.
- Tastatur und Maus aufnehmen, Schritte und Pausen bearbeiten, abspielen einmal / solange gedrückt / wiederholen.
- Makro einer Taste zuweisen oder lösen.

### Sonstiges
- Oberfläche auf Englisch und Deutsch (unten rechts umschalten; Standard folgt der Windows-Anzeigesprache).
- Fenster schließen minimiert nur, die Tasten funktionieren weiter. **Beenden** beendet das Programm.
- Datenschutz: Tastendrücke werden nur für die Displaytasten, die Reaktiv-Vorschau (nur im Speicher) und während
  *du* ein Makro aufnimmst oder eine Taste zuweist ausgewertet. Nichts wird protokolliert.

## Voraussetzungen
- Windows 10 / 11, be quiet! Dark Mount
- Python 3.10+ und Pillow (`pip install -r requirements.txt`)
- Optional: Node.js (für Node-Plugins), Microsoft Edge (für HTML-Plugins und Plugin-Einstellungen, bei Windows dabei)

## Starten
```bash
pip install -r requirements.txt
pythonw deck.py
```
- `pythonw deck.py --min` startet minimiert (z. B. aus dem Autostart-Ordner, `shell:startup`).
- **Vorher IO Center schließen** – die Tastatur nimmt nur ein Programm gleichzeitig an.
- Einstellungen landen in `deck.json` neben dem Programm, Plugin-Ausgaben in `deck.log`.
- Selbsttests: `python deck.py test` und `python sdhost.py test` (ohne Tastatur).

## Lizenz
MIT – siehe [LICENSE](LICENSE). Stream-Deck-Plugins behalten ihre eigenen Lizenzen. „be quiet!“, „Dark Mount“,
„Elgato“ und „Stream Deck“ sind Marken ihrer jeweiligen Inhaber.
