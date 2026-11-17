# TODO

## Linux support
The protocol, lighting, macros, pages and the Tk UI are portable. Windows-only parts to replace (keep the differences
in a few functions, no second code base):

Done (tested in an Ubuntu 24.04 VM): hidraw HID (checked on the real keyboard), uinput hotkeys/media, text via
`wtype`/`xdotool`, now playing via `playerctl`, XDG autostart, `$LANG`, DejaVu font, Chromium for plugins,
udev rule `70-darkmount.rules`. Open:

- [ ] Full run on the real keyboard: lighting, display keys, macros, dock.
- [ ] Mouse buttons in macro recordings (needs evdev; keys only for now).
- [ ] Plugin settings embedded in the window (X11 reparenting); today they open in their own Chromium window.
- [ ] Linux build (PyInstaller or just `python3 deck.py`) and a README section.
- [ ] Note in the README: many Stream Deck plugins are Windows-only; network ones (Home Assistant, Govee, …) work.

## Own plugin API (independent of Elgato)
Today plugins are Stream Deck plugins (Elgato SDK, WebSocket, property inspector in Edge). Add a lean native way:

- [ ] Simple plugin format in the own `plugins` folder: a manifest plus one script or exe; no Elgato app, no Chromium.
- [ ] Minimal contract: key pressed / released → plugin; plugin → set key image / title / LED colour.
- [ ] Settings from the manifest as plain Tk fields instead of an HTML property inspector.
- [ ] Keep the Elgato host as is; both sit side by side.
- [ ] Wiki page documenting the API with one tiny example plugin.

## Smaller items
- [ ] Square icon variant so the 16 px size stays recognizable.
- [ ] Code signing for the .exe (removes the SmartScreen warning; needs a paid certificate).
