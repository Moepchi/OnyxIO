# TODO

## Linux support
The protocol, lighting, macros, pages and the Tk UI are portable. Windows-only parts to replace (keep the differences
in a few functions, no second code base):

- [ ] **HID** (`darkmount.py`): find the MI_02 interface via `/sys/class/hidraw/*/device/uevent`
      (`HID_ID=…:0000373F:00000001`, interface 02) and read/write `/dev/hidraw*`; ship a udev rule so no root is needed.
- [ ] **Send hotkey / text / media** (`deck.py`, `SendInput`): `uinput` (works on X11 and Wayland).
- [ ] **Macro recording / "Assign key"** (`GetAsyncKeyState`): `evdev`, or the keyboard's own input events.
- [ ] **Plugin settings**: Chromium/Chrome `--app` instead of Edge; embed via X11 reparenting, own window on Wayland.
- [ ] **Small things**: UI language from `$LANG`, font fallback (DejaVu) instead of `arialbd.ttf`, `taskkill` → kill
      process group, plugins only from the own `plugins` folder (no Elgato app on Linux).
- [ ] Run the self-tests on `ubuntu-latest` in GitHub Actions as well.
- [ ] Note in the README: many Stream Deck plugins are Windows-only; network ones (Home Assistant, Govee, …) work.

## Smaller items
- [ ] Taskbar icon when started via `pythonw deck.py` (shows the Python icon; set an AppUserModelID).
- [ ] Square icon variant so the 16 px size stays recognizable.
- [ ] Code signing for the .exe (removes the SmartScreen warning; needs a paid certificate).
