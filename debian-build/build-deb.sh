#!/usr/bin/env bash
# Build a .deb for InetShot.
# Run from the repo root (where inetshot.py and snap/ live):
#   bash debian-build/build-deb.sh
# Produces: inetshot_<version>_amd64.deb

set -e

VERSION="0.1.0"
ARCH="amd64"
PKG="inetshot"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build-deb-tmp"
STAGE="$BUILD/${PKG}_${VERSION}_${ARCH}"

rm -rf "$BUILD"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/usr/lib/inetshot" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps"

# --- app code ---
cp "$ROOT/inetshot.py" "$STAGE/usr/lib/inetshot/inetshot.py"

# --- launcher on PATH (forces XWayland for the overlay) ---
cat > "$STAGE/usr/bin/inetshot" <<'EOF'
#!/bin/sh
export QT_QPA_PLATFORM=xcb
exec /usr/bin/python3 /usr/lib/inetshot/inetshot.py "$@"
EOF
chmod 755 "$STAGE/usr/bin/inetshot"

# --- icon ---
cp "$ROOT/snap/gui/icon.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/inetshot.svg"

# --- desktop entry ---
cat > "$STAGE/usr/share/applications/inetshot.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=InetShot
Comment=Lightshot-style screenshot and annotation tool
Exec=inetshot
Icon=inetshot
Terminal=false
Categories=Utility;Graphics;
Keywords=screenshot;capture;annotate;screen;
StartupNotify=false
EOF

# --- control file (declares deps so apt installs them automatically) ---
cat > "$STAGE/DEBIAN/control" <<EOF
Package: ${PKG}
Version: ${VERSION}
Section: graphics
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.10), python3-pyqt6, python3-dbus, python3-gi, gir1.2-glib-2.0, wl-clipboard, libnotify-bin
Recommends: gnome-screenshot
Maintainer: Vahe Nazaryan <23nazaryan@gmail.com>
Homepage: https://github.com/23nazaryan/inetshot
Description: Lightshot-style screenshot and annotation tool
 InetShot is a fast screenshot and annotation tool for Ubuntu/GNOME.
 Drag to select a region, annotate with arrows, pen, rectangles,
 highlighter, lines, and text, then copy to clipboard or save to file.
 Supports GNOME 46+ on Wayland and X11.
EOF

# --- post-install: refresh icon + desktop caches ---
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ -x "$(command -v gtk-update-icon-cache)" ]; then
    gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
if [ -x "$(command -v update-desktop-database)" ]; then
    update-desktop-database -q /usr/share/applications || true
fi
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

# --- build ---
dpkg-deb --root-owner-group --build "$STAGE"
mv "$BUILD/${PKG}_${VERSION}_${ARCH}.deb" "$ROOT/"
rm -rf "$BUILD"

echo
echo "Built: $ROOT/${PKG}_${VERSION}_${ARCH}.deb"
echo "Install with:  sudo apt install ./${PKG}_${VERSION}_${ARCH}.deb"
