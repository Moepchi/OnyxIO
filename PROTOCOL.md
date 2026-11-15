# be quiet! Dark Mount – QLink-Protokoll

Stand 14.11.2026. Quelle: Debug-Log von IO Center v1.4.1 (`QT_LOGGING_RULES="qlink*.debug=true;bq_hid*.debug=true"`)
plus Strings aus `IO_Center.exe`. Firmware Main MCU v1.31.0, QLink v1.0.1. Nichts davon wurde an die Tastatur gesendet;
alles stammt aus mitgelesenem IO-Center-Verkehr.

> Neu mitschneiden: IO Center mit obiger Variable starten. **Achtung:** Das Log enthält dann jeden Tastendruck
> (siehe *Macros*), und es rotiert alle ~3 s (10 Dateien), wobei ältere Logs gelöscht werden.

## Transport

- USB `VID_373F&PID_0001`, Interface **MI_02**, Usage Page `0xFF00`, Usage 1. Reports: 64 Byte rein/raus.
  Unter Windows wird beim Schreiben Report-ID `0x00` vorangestellt (65 Byte).
- `PID_0009` = Bootloader (DFU). **Nicht anfassen.**

### Paket (64 Byte)

| Offset | Größe | Inhalt |
|---|---|---|
| 0 | 1 | `len` = Anzahl Bytes ab Offset 1 bis Ende Nutzdaten (= 2 + Datenlänge) |
| 1 | 1 | `seq`: Bit 7 = weitere Pakete folgen, Bits 0–6 = Paketindex (`80 81 82 03`) |
| 2 | 1 | Session-ID (SID), 0 vor `OpenSession` |
| 3..61 | ≤59 | Nachrichten-Bytes (Rest mit 0 auffüllen) |
| 62..63 | 2 | CRC-16/MODBUS über Byte 0..61, Little-Endian (an 20 289 Paketen geprüft) |

### Nachricht (nach dem Zusammensetzen)

```
[status u8][reqId u8][feature u8][command u8][params ...]
```

- Request: `status = 0`, `reqId` fortlaufend (8 Bit, wrappt).
- Response: gleiche `reqId`/`feature`/`command`, Status `0` = OK, sonst Fehler (z. B. `05` = InvalidParameter).
- Notification: `reqId = 0`, `command` = Notification-ID.
- Statuscodes in dieser Reihenfolge: Success, InvalidSessionId, InvalidFeatureId, InvalidCommandId, InvalidRequestId,
  InvalidParameter, NotAuthorized, NotActive, InvalidState, InvalidSize, InsufficientResources.
- Multi-Byte-Zahlen in Params sind Little-Endian.

## Session

- `Root.OpenSession` mit SID 0, Params `34 12 00 00 01` (Token 0x1234 …) → Antwort `[token u32][sid][state][timeout s]`.
  - Hält kein anderer Client die Tastatur, ist die Session **sofort aktiv**: `34 12 00 00 09 01 02` = SID 9, Active, 2 s.
    In diesem Fall kommt keine Notification.
  - Ist noch eine alte Session aktiv (IO Center beim Start), lautet die Antwort `… 03 00 02` (Inactive). Rund 0,8 s später
    kommt dann `Root.SessionStateChanged 01 01`.
- `Root.GetActiveSessionInfo` (0x01/0x06) → `[sid][2][1]`, z. B. `09 02 01`. Damit lässt sich prüfen, wem die Tastatur gehört.
- **Timeout 2 s:** Ohne Verkehr verfällt die Session. `Root.KeepAlive` (0x01/0x03, ungetestet) oder normaler Verkehr hält sie offen.
- Es kann nur eine Session aktiv sein (Clients „QControl“ = IO Center, „Web“). Eigene Software und IO Center
  laufen also **nicht gleichzeitig**.

## Feature- und Befehls-IDs (aus echten Frames)

| Feature | ID | Befehle (ID) |
|---|---|---|
| Root | 0x01 | OpenSession 01, GetSupportedFeatures 04, GetQLinkVersion 05, GetActiveSessionInfo 06 |
| DFU | 0x02 | **nicht benutzen** |
| DeviceInfo | 0x03 | GetDeviceInfo 01, GetSerialNumber 02 |
| Storage | 0x04 | – |
| Keyboard | 0x07 | GetLayout 01, GetConfig 02, GetState 04 |
| Lightings | 0x10 | GetLightingMode 01, SetLightingMode 02, GetLayersLayout 03, SetLayersLayout 04, GetLayerConfig 05, SetLayerConfig 06, GetLayerMask 07, SetLayerMask 08, GetLayerName 09, SetLayerName 0a, PerformRealtimeUpdates 0d |
| Bindings | 0x11 | GetBindings 01, SetBinding 02, ClearBinding 03, GetConfig 04, SetConfig 05, GetBinding 06; Notifications BindingsChanged 01, ActionFired 02, ConfigChanged 03 |
| Macros | 0x12 | GetMacros 01, SetMacros 02, GetMacroConfig 03, SetMacroConfig 04, GetMacroEvents 05, SetMacroEvents 06, GetMacroName 07, SetMacroName 08, StartRecording 09, StopRecording 0a, AbortPlayback 0b; Notifications MacrosChanged 01, MacroConfigChanged 02, MacroEventsChanged 03, MacroNameChanged 04, InputEvent 05 |
| NumpadModule | 0x20 | GetState 01, SetImage 02 |
| MediaDockModule | 0x21 | GetState 01, GetConfig 02, SetConfig 03, SetDateTime 05, GetImage 06, SetImage 07, SetProfileInfo 09, SetPlaybackInfo 0b |

Nicht beobachtete Befehle folgen sehr wahrscheinlich der Namensreihenfolge in der Exe (1-basiert). Die Namen
stehen dort als UTF-16-Tabelle `GetLightingMode|SetLightingMode|…`. Bevor ein Befehl benutzt wird, gegen einen Mitschnitt prüfen.

## Displaytasten (Numpad-Modul) → Stream-Deck-Ziel

- `NumpadModule.GetState` → `01 02` = verbunden, rechts.
- 8 Displaytasten `Key_NumpadImage0..7` = Key-IDs **0x6d..0x74**. Bild jeweils **120×120 JPEG** (~2 KB).
  Anordnung 2 Reihen × 4. Die Displays sind um 90° gegen den Uhrzeigersinn gedreht eingebaut, deshalb das Bild vor dem Upload
  um 90° im Uhrzeigersinn drehen (bestätigt).
- Bilder bleiben nach dem Abstecken erhalten, liegen also im Flash (siehe *Flash-Verschleiß*).
- `NumpadModule.SetImage` Params: `[keyId u16][offset u32][chunk]`, Chunks bis 248 Byte.
  Der Bildstrom beginnt mit 9 Byte Header: `[totalSize u32 inkl. Header][w u16][h u16][format u8]`, Format 3 = JPEG.
  Danach folgen die JPEG-Bytes (`FF D8 … FF D9`).
- Quittung: Notification `NumpadModule.ImageChanged` (0x20/0x02) `[sid][keyId u16]`.
- **Tastendruck (live bestätigt):** Jede Displaytaste meldet dieselbe ID wie ihr Bild:
  - mit laufendem `Macros.StartRecording` als `Macros.InputEvent` (0x12) `[08 = gedrückt / 09 = losgelassen][keyId u16]`;
  - bei jedem Druck zusätzlich als `Bindings.ActionFired` (0x11/0x02) `[keyId u16][?][action][data]` mit der Belegung,
    die auf der Tastatur gespeichert ist. Das unbekannte Byte war 00 bei OpenUrl und 01 bei WindowsShortcut.
    Auch mit `Disable` kommt die Meldung, als `[keyId u16] 01 00` (bestätigt, alle 8 Tasten).
  - **Ohne `StartRecording` kommt nichts an**, auch keine 0x11/0x02-Meldung (bestätigt, alle 8 Tasten gedrückt).
    Eigene Software muss den Mitschnitt also starten und alle anderen Tasten sofort verwerfen.

## Tastenbelegung (Bindings, 0x11)

- `GetBinding [keyId u16]` → `[keyId u16][action][data]`. `SetBinding` nimmt genau dieses Format (verifiziert, Rücklesen gleich).
- `GetBindings [00 00]` → `[count u16]` + Einträge im selben Format. Ab Werk sind es 22: Lighting-, Media- und die 8 Displaytasten.
- Action-IDs = Reihenfolge der `std::variant` in der Exe, gezählt ab 0:
  `Disable 00, StandardKey 01, Media 02, Mouse 03, OpenFolder 04, OpenFile 05, OpenUrl 06, WindowsShortcut 07,
  Profile 08, Lighting 09, Macros 0a, AltCode 0b, DPI 0c`. Bestätigt sind 00, 02, 06, 07 und 09; 0a wird angenommen und zurückgelesen (`[0a][macroId u16]`).
- `ClearBinding [keyId u16]` entfernt eine eigene Belegung, die Taste ist wieder normal (bestätigt an A = 30; danach liefert GetBinding InvalidParameter).
- Binding-IDs normaler Tasten = InputEvent-/LED-IDs. GetBinding auf Tasten **ohne** eigene Belegung → InvalidParameter (harmlos).
- Displaytasten ab Werk (Sicherung in `bindings-backup.txt`):
  - `6d00 06 1a "https://www.bequiet.com/en"`: OpenUrl, Pascal-String.
  - `6e..74 00 07 YY` mit YY = 14, 01, 15, 08, 03, 04, 13: WindowsShortcut, Unternummer noch nicht zugeordnet.
- `python darkmount.py unbind` setzt alle 8 Displaytasten auf `Disable` (`xx00 00`), damit ein Druck keine OS-Aktion mehr auslöst
  (bestätigt: alle 8 gedrückt, kein Effekt am PC, Druck kommt trotzdem an).
  Zurück auf Werkszustand: per `SetBinding` mit den Bytes aus der Sicherung.

## Windows-Zugriff (ohne Bibliotheken)

- Gerätepfad über `CM_Get_Device_Interface_ListW` (HID-GUID) bestimmen und nach `vid_373f&pid_0001&mi_02` filtern.
- Öffnen mit `CreateFileW(GENERIC_READ|WRITE, FILE_SHARE_READ|WRITE, OPEN_EXISTING)`. `os.open` scheitert am Share-Mode,
  `msvcrt.open_osfhandle` lehnt HID-Handles ab.
- Zwei Handles, eines zum Lesen und eines zum Schreiben: Ein blockierendes `ReadFile` würde sonst `WriteFile` aufhalten.

## Media Dock (Display links)

- `GetConfig` → `dc4d00 01 01 1e00 0000` = Akzentfarbe RGB, 24h-Uhr, Screensaver Clock, nach 30 s aktiv, nie aus.
- `SetDateTime` Params: Unix-Zeit u64.
- Bilder `GetImage`/`SetImage`: `[type u8][offset u32][size/chunk]`, 320×240 RGB565 (Format 1), Header wie oben.
  Typ 0 = Screensaver. IO Center bekommt bei Artwork-Uploads oft `InvalidParameter`.
- `SetPlaybackInfo`: Mediainfos (Titel als Pascal-String), `SetProfileInfo`: Profilname als Pascal-String.

## Beleuchtung (0x10)

Live geprüft am 14.11.2026, jeweils mit Blick auf die Tastatur.

- `SetLightingMode [m]`, Namen aus IO_Center.exe (Enum LightingMode: General, Custom, DynamicLighting), Zuordnung
  vermutet nach Reihenfolge:
  - `00` = aus bzw. eingefroren.
  - `01` (General) und `02` (Custom) zeigen beide den **globalen Effekt aus Layer 0**.
  - `03` (DynamicLighting) = Realtime: IO Center berechnet die Effekte auf dem PC und streamt sie per `PerformRealtimeUpdates`, mehrere Frames
    pro Sekunde, je Eintrag `03 [ledId u16][R][G][B]`. Variante `00` Commit, `01` RangeIndividualColors, `02` RangeSingleColor,
    `03` SingleLed. Ohne Stream fällt `03` auf Layer 0 zurück.
- **Layer 0 + Modus 01 überlebt Ab- und Anstecken**, ohne laufende Software (bestätigt mit Tornado). Das löst das
  Profilverlust-Problem: `darkmount.QLink.set_lighting` schreibt Layer 0 und setzt Modus 01.
- `SetLayerConfig [layerId] + [effect][dir][brightness %][speed %][colorMode][Farben]`, `GetLayerConfig [layerId]` liefert
  dasselbe ohne Layer-ID zurück.
  - Effekt: Statisch 00, Farbwelle 01, Tornado 02, Atmen 03, Reaktiv 04, Matrix 05. „Ripple“ aus IO Center ist nicht zugeordnet.
  - Richtung: Hoch 00, Runter 01, Links 02, Rechts 03, Im Uhrzeigersinn 04, Gegen den Uhrzeigersinn 05.
  - colorMode `00` = eine Farbe `RGB` (von der Tastatur angenommen und zurückgelesen, Anzeige nicht geprüft),
    `01` = zwei Farben `RGB RGB` (Reaktiv: Grund, Druck), `02` = Verlauf `[n]` + n × `[R G B pos %]`.
    Regenbogen-Tornado: `02 04 64 32 02 07 ff0000 00 ffff00 10 00ff00 21 00ffff 32 0000ff 42 ff00ff 53 ff0000 63`.
- Custom-Layer (Zonen): `SetLayersLayout [n][ids…]`, `SetLayerMask [id][len=0x1a][26 Byte Bitmaske]`. Layer 1/2 aus IO Center
  wurden in keinem Modus von der Tastatur gezeichnet, auch nicht nach erneutem Senden des Layouts. IO Center rendert Zonen
  offenbar selbst und streamt sie (Modus 03). Zonen gehen also nur mit laufender Software. `GetLayerMask [0..5]` →
  InvalidParameter. `GetLayersLayout` → `02 01 02`, die Configs von Layer 1–32 lesen sich als Nullen.
  - IO Center beim Start (Log): Modus 01, dann 03; Layer 0 Tornado; `SetLayersLayout 02 03 04`;
    `SetLayerMask 03 1a fe ff … 03 00 …` = Layer 3, Bits 1–105 gesetzt, also genau die Tasten-LEDs (LSB zuerst).
  - **Achtung:** `GetLayerConfig 255` bekam keine Antwort, die Tastatur trennte sich vom USB und startete neu (Einstellungen
    blieben erhalten). Keine IDs außerhalb des bekannten Bereichs abfragen.
- `PerformRealtimeUpdates`: `[Anzahl Einträge]` + Einträge, höchstens 40 je Aufruf (IO Center: 40, dann 34 + Commit). Der
  Commit `00` (1 Byte) steht nur am Ende des letzten Aufrufs. Die Antwort ist leer. Die Tastatur nimmt jede ID an, auch
  300, und meldet keinen Fehler. Modus 03 hält mindestens 3 s ohne neue Frames.
- LED-IDs: Rand = was IO Center für seine Rand-Ebene streamt: 106–127 und 139–169 Tastatur, 171–191 Numpad. 128–138
  (11 LEDs, rechter Rand unter dem Numpad) und 170 bekommen nie eine Farbe. IO Center hat die Tasten nie gestreamt,
  deshalb gibt es in `deck.py` den Assistenten „LEDs zuordnen“. Er schreibt nach `deck.json` unter `keymap`.
  Ergebnis am 14.11.2026 (ISO-DE, Numpad rechts), ID 0 leuchtet nicht:
  - 1–14: `^` bis Backspace; 15–28: Tab bis Enter; 29–41: Caps bis `#`; 42–53: Shift links bis Shift rechts, ohne `<`.
  - 54–59: Strg, Fn, Alt, Leertaste, AltGr, Strg.
  - 60–69: Navigationsblock spaltenweise (Einfg Entf ← Pos1 Ende ↑ ↓ BildAuf BildAb →).
  - 70–86: Numpad spaltenweise (Num 7 4 1, / 8 5 2 0, * 9 6 3 `,`, - + Enter).
  - 87–102: Esc, F1–F12, Druck, Rollen, Pause.
  - 103–105: Win links, Win rechts, `<`. Das sind wohl die Tasten, die es nicht in jeder Sprachversion gibt.
- IO-Center-Profil (`.ioprofile` = ZIP mit einer Datei `data`, JSON von cereal), Abschnitt `LightingsProperty`:
  - `mode`: `General` | `Custom` | `DynamicLighting`. `generalModeData` = ein Effekt für alles, steht auf der Tastatur
    in Layer 0 (passt Byte für Byte zu `GetLayerConfig 0`).
  - `customModeData.layers` (IO Center erlaubt bis zu 7): je Layer `name`, `assignments` (Tastennamen `Key_*` oder
    Rand-LEDs `Led_*`) und ein Effekt mit `brightness`, `speed`, `direction`, `colorMode`
    (`SingleColor`/`DualColor`/`Gradient`) und Farben `#AARRGGBB`.
  - Rand-LEDs heißen `Led_KeyboardTop1–21`, `Led_KeyboardBottom1–21`, `Led_KeyboardLeft1–11` (zusammen 53 =
    106–127 + 139–169) und `Led_NumpadTop1–5`, `Led_NumpadBottom1–5`, `Led_NumpadRight1–11` (21 = 171–191).
    `deck.py` nimmt an: Top = 107–127, Left = 106 + 160–169, Bottom = 139–159, jede Seite von links nach rechts bzw.
    von oben nach unten gezählt (Top1 = 107, Left1 = 106, Bottom1 = 159). Ungeprüft.
  - Beispiel vom 15.11.2026, Modus Custom: Layer „Reaktiv“ auf allen 105 Tasten, Layer „Farbwelle“ auf allen 74 Rand-LEDs.
    Auf der Tastatur standen trotzdem nur Modus 01 und Layer 0 (General-Effekt). Custom-Layer sind dort nicht gespeichert.
- Sichttest 15.11.2026 (`customtest.py`): Layout `[2] 01 02`, Layer 1 statisch blau mit Maske WASD, Layer 2 Atmen grün mit
  Maske Rand, dann Modus 02. **Die Tastatur zeigte nur Layer 0** (weiß, Reaktiv), weder WASD blau noch den grünen Rand,
  auch nicht nach Trennen der Sitzung oder Abstecken. Custom-Layer zeichnet die Tastatur also nicht selbst, IO Center
  rechnet den Modus Benutzerdefiniert auf dem PC. Die Test-Configs stehen noch in Layer 1/2, wirken in Modus 01 aber nicht.
- Modus 03 (Streaming), bestätigt am 14.11.2026:
  - Rand und einzelne Tasten färben sich.
  - Ein Wechsel 03 → 01 → 03 löscht gestreamte LEDs.
  - **Nicht gestreamte Tasten bleiben in Modus 03 dunkel**, sie zeigen nicht den Effekt aus Layer 0.
- Ebenen in `deck.py` (Reiter „Ebenen“) funktionieren wie IO Centers Benutzerdefiniert: Deck rechnet jede LED auf dem PC
  und streamt in Modus 03 mit 25 Bildern/s nur geänderte LEDs, einmal pro Sekunde alle. Tasten ohne Ebene bekommen den
  Effekt aus „Beleuchtung“, ebenfalls vom PC gerechnet. Ohne Ebenen bleibt Modus 01 und die Tastatur rechnet selbst.
- Effekt-Index im Profil = Position in IO Centers `std::variant` (RTTI in `IO_Center.exe`): 0 Breathing, 1 ColorWave,
  2 Gif, 3 Image, 4 Matrix, 5 MulticolorStatic, 6 Reactive, 7 ScreenCapture, 8 ScreenSync, 9 Sensor, 10 Static,
  11 Tornado, 12 Video, 13 Off, 14 Ripple. Richtungen: `Up`, `Down`, `Left`, `Right`, `Clockwise`, `CounterClockwise`.
- Reaktiv mit zwei Farben: **erste = Farbe beim Druck, zweite = Ruhefarbe** (Layer 0 `ff2800 d3d6da` zeigt weiße Tasten,
  die beim Druck rot aufleuchten).
- Lage der Rand-LEDs, abgeleitet aus IO Centers Stream einer waagrechten Farbwelle (Farbton-Versatz je LED gegen 106,
  ca. 28° pro Schritt, ausgewertet nur als Mittelwert):
  - Tastatur: 106 an der linken Ecke, 107–127 entlang einer Längsseite nach rechts, 128–138 = rechte Kante unter dem
    Numpad (nie gefärbt), 139–159 die andere Längsseite zurück nach links, 160–169 die linke Kante.
  - Numpad: 171–175 eine Längsseite, 176–186 die rechte Kante, 187–191 die andere Längsseite zurück. 170 unbekannt.
  - 106–127 liegen oben, der Ring läuft im Uhrzeigersinn (bestätigt am 15.11.2026: „Hoch“ läuft am Rand nach oben).
- Tastendrücke für Reaktiv: **Tasten-ID im `InputEvent` = LED-ID** (bestätigt am 15.11.2026, die gedrückte Taste leuchtet). Deck hält nur „Taste x zuletzt
  um t gedrückt“ im Speicher, höchstens 3 s, und nur solange Ebenen aktiv sind.

## Flash-Verschleiß

Displaybilder bleiben nach dem Abstecken erhalten (bestätigt), liegen also im Flash. Jeder `SetImage`-Upload ist ein
Schreibvorgang. `deck.py` sendet deshalb keine unveränderten Bilder und lädt pro Taste höchstens ein Bild je `MIN_GAP`
(60 s) hoch, außer nach „Übernehmen“ oder bis 3 s nach einem Druck auf die Taste. Die Beleuchtung wird nur bei
„Übernehmen“ geschrieben, nie beim Verbinden.

## Stream-Deck-Plugins (`sdhost.py`)

- Elgato-SDK 2: Das Plugin wird mit `-port P -pluginUUID U -registerEvent registerPlugin -info JSON` gestartet und
  verbindet sich per WebSocket. Die Einstellungsseite (Property Inspector) kommt per HTTP vom selben Port. Nach dem Laden
  wird `connectElgatoStreamDeckSocket(port, context, 'registerPropertyInspector', info, actionInfo)` aufgerufen.
- HTML-Plugins (`CodePath` = .html) laufen in einem unsichtbaren Edge (`--headless=new`, eigenes Profil unter
  `%LOCALAPPDATA%\darkmount\edge\<Plugin>`). Ihre Seite kommt vom selben Port, angehängt wird
  `connectElgatoStreamDeckSocket(port, uuid, 'registerPlugin', info)`. Weil Vue- oder React-Seiten die Funktion erst
  spät anlegen, wird nach `load` gewartet, bis es sie gibt. Mit `--disable-gpu --disable-extensions
  --disable-component-extensions-with-background-pages` braucht Edge etwa 145 MB statt 225 MB.
- Marketplace-Plugins sind verschlüsselt (`manifest.json` beginnt mit `ELGATO`). Sie werden **nicht** entschlüsselt, weil
  das Elgatos DRM umgehen würde.
- Ersatz: unverschlüsselte Builds (z. B. von GitHub) als entpackten `<uuid>.sdPlugin`-Ordner nach `plugins\` neben
  `deck.py` legen. Sie haben Vorrang vor dem gleichnamigen Ordner der Stream-Deck-App. Home Assistant 3.8.4 kommt von
  github.com/cgiesche/streamdeck-homeassistant.

## Makros (Macros, 0x12)

Alles live geprüft (Schreiben, Zurücklesen, Ausgangszustand wiederhergestellt). Listen sind seitenweise: Anfrage mit
`[offset u16]`, Antwort `[total u16][Einträge ab offset]`.

- `GetMacros [offset]` → `[total][ids u16…]`. `SetMacros [total][offset][ids u16…]` setzt die ganze Liste: neue IDs
  entstehen leer (Name leer, Config `00000000`, keine Events), fehlende werden gelöscht.
- `GetMacroName [id]` → Pascal-String (UTF-8). `SetMacroName [id][len][bytes]`.
- `GetMacroConfig [id]` → `[playbackMode u8][delayMode u8][delay u16 ms]`, `SetMacroConfig [id]` + dieselben 4 Byte.
  playbackMode Once/Hold/Repeat = 0/1/2, delayMode None/Custom/Record = 0/1/2 (Reihenfolge aus der Exe, IO Center legt
  `00 02 0000` an; die Werte 1/2 beim Abspielmodus sind noch nicht am Gerät beobachtet).
- `GetMacroEvents [id][offset]` → `[total][Events ab offset]`, bis 13 pro Antwort.
  Event = `[type u8][HID-Usage u8][delay u16 ms danach]`, type 02 = gedrückt, 03 = losgelassen.
  Tasten sind hier **USB-HID-Usages** (Numpad 4 = 0x5c), nicht die LED-/InputEvent-IDs.
  Maus: type **0a**, Wert = danach gehaltene Tasten als Bitmaske (links 1, rechts 2, Mausrad 4, 0 = alle los;
  abgelesen aus einer IO-Center-Aufnahme). Seitentasten nimmt IO Center nicht auf, ihre Bits sind unbekannt.
- `SetMacroEvents [id][total u16][offset u16][Events]`, bis 12 pro Paket; ersetzt die Liste. `total 0` leert sie.
  Achtung: Teil-Schreibvorgänge mit falschem `total` hängen an statt zu ersetzen.
  **Achtung:** Ein Event mit type 00 (Taste 01) ließ die Tastatur verstummen, danach war ein frisch angelegtes Makro
  weg (vermutlich Neustart). Keine Event-Typen raten; Maus-Codes nur aus einem in IO Center aufgenommenen Makro ablesen.
- Deck nimmt selbst auf (InputEvents, LED-ID → Tastenname über `keymap` → HID-Usage); `StartRecording` läuft ohnehin.

## Datenschutz

IO Center ruft `Macros.StartRecording [08 09 0b]` auf und bekommt dann **jeden Tastendruck** als Notification
`Macros.InputEvent` (`08 kk 00` = gedrückt, `09 kk 00` = losgelassen). Eigene Software braucht das nur für
Displaytasten-Aktionen und sollte es auch nur dafür auswerten. Einzige Ausnahme in `deck.py`: Reaktiv-Ebenen. Dafür hält
Deck pro Taste nur den Zeitpunkt des letzten Drucks im Speicher, verwirft ihn nach 3 s und schreibt ihn nirgends hin.
Zweite Ausnahme: Makro-Aufnahme und „Taste zuweisen“, nur solange der Nutzer sie gestartet hat; die Aufnahme wird zu
Makro-Schritten und landet (wie bei IO Center) erst mit „Übernehmen“ auf der Tastatur, nie in Logs.
