# InetShot for Linux

A fast, Lightshot-style screenshot and annotation tool for Ubuntu/GNOME.

![InetShot icon](snap/gui/icon.svg)

## Features

- Drag to select any region of the screen
- Annotate with arrows, freehand pen, rectangles, highlighter, lines, and text
- 6 colors · 3 sizes · undo
- Copy to clipboard (`Ctrl+C` / `Enter`) or save to file (`Ctrl+S`)
- Supports GNOME 46+ on Wayland and X11
- ~0.5s capture via xdg-desktop-portal (no flash, no extra windows)

## Install

### From the Snap Store (recommended)
```bash
sudo snap install inetshot
```

### Manual install
```bash
sudo apt install python3-pyqt6 python3-dbus python3-gi gir1.2-glib-2.0 wl-clipboard libnotify-bin
mkdir -p ~/.local/bin
curl -o ~/.local/bin/inetshot.py https://raw.githubusercontent.com/YOUR_USERNAME/inetshot/main/inetshot.py
cat > ~/.local/bin/inetshot.sh <<'SH'
#!/usr/bin/env bash
export QT_QPA_PLATFORM=xcb
exec python3 "$(dirname "$(readlink -f "$0")")/inetshot.py"
SH
chmod +x ~/.local/bin/inetshot.py ~/.local/bin/inetshot.sh
```

## Bind to a hotkey (GNOME)

```bash
# Free up the default Print binding (GNOME 46+)
gsettings set org.gnome.shell.keybindings show-screenshot-ui "[]"

SC=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/inetshot/
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['$SC']"
gsettings set "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$SC" name 'InetShot'
gsettings set "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$SC" command "$HOME/.local/bin/inetshot.sh"
gsettings set "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$SC" binding 'Print'
```

To restore GNOME's own screenshot UI on `Super+Print`:
```bash
gsettings set org.gnome.shell.keybindings show-screenshot-ui "['<Super>Print']"
```

## Controls

| Key | Action |
|-----|--------|
| Drag | Select region |
| Esc | Cancel |
| Enter / Ctrl+C | Copy to clipboard |
| Ctrl+S | Save to file |
| Ctrl+Z | Undo last annotation |

## Requirements

- Ubuntu 22.04+ (GNOME 42+) or any X11 desktop
- Python 3.10+
- PyQt6, python3-dbus, python3-gi

## Building the snap

```bash
sudo snap install snapcraft --classic
sudo snap install lxd && sudo lxd init --minimal
sudo usermod -aG lxd $USER   # then log out/in
snapcraft
sudo snap install inetshot_0.1.0_amd64.snap --dangerous
```

## License

MIT — see [LICENSE](LICENSE).
