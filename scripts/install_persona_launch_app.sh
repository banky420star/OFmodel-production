#!/bin/bash
# Build "Persona Studio.app" — the clickable launcher — into ~/Applications.
#
# Run it again after editing persona-studio-launcher.sh or the icon; it
# overwrites the bundle in place. Nothing else on the system is touched.
#
#   bash /Volumes/AI_DRIVE/persona/scripts/install_persona_launch_app.sh
#
# Why a bundle at all: macOS only gives a double-clickable, icon-bearing
# launcher as a .app. This one is a shell script in a thin wrapper — nothing is
# compiled and nothing is signed with a real certificate.

set -euo pipefail

REPO="/Volumes/AI_DRIVE/persona"
LAUNCHER="$REPO/scripts/persona-studio-launcher.sh"
ICNS="$REPO/storage/AppIcon.icns"
ICON_SCRIPT="$REPO/scripts/make_launch_icon.mjs"
APP="$HOME/Applications/Persona Studio.app"

[ -f "$LAUNCHER" ] || { echo "missing $LAUNCHER" >&2; exit 1; }

# The icon is generated from the app's own brand mark (scripts/persona-mark.svg),
# not committed as a blob, so build it if it is absent. Rendering needs node —
# it borrows the web app's Playwright browser rather than shipping artwork.
if [ ! -f "$ICNS" ]; then
    NODE="$(command -v node || true)"
    [ -n "$NODE" ] || { echo "node not found, cannot render the icon" >&2; exit 1; }
    echo "generating the icon…"
    "$NODE" "$ICON_SCRIPT"
fi

echo "building $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

install -m 755 "$LAUNCHER" "$APP/Contents/MacOS/PersonaStudio"
install -m 644 "$ICNS" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Persona Studio</string>
    <key>CFBundleDisplayName</key><string>Persona Studio</string>
    <key>CFBundleIdentifier</key><string>studio.persona.launcher</string>
    <key>CFBundleExecutable</key><string>PersonaStudio</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
</dict>
</plist>
PLIST

/usr/bin/plutil -lint "$APP/Contents/Info.plist"

# Ad-hoc signature. Without one, macOS 14+ can show the bundle as "damaged" or
# keep a stale icon in the Dock cache; with it, the icon shows up immediately.
if command -v codesign >/dev/null 2>&1; then
    /usr/bin/codesign --force --sign - "$APP" >/dev/null 2>&1 \
        && echo "ad-hoc signed" || echo "warning: ad-hoc signing failed (the app still runs)"
fi

# Nudge Finder/LaunchServices so the icon appears without a logout.
/usr/bin/touch "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APP" >/dev/null 2>&1 || true

echo
echo "done → $APP"
echo "Double-click it, or drag it to the Dock to keep it there."
