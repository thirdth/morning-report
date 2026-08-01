#!/bin/bash
# Morning Report — App Bundle Installer
# Run once: bash ~/morning-report/install_app.sh
# Creates Morning Report.app in /Applications

set -e

APP_NAME="Morning Report"
APP_PATH="/Applications/${APP_NAME}.app"
SCRIPT_PATH="$HOME/morning-report/morning_report.py"
ICON_DIR="$HOME/morning-report/icon_build"

echo "→ Building ${APP_NAME}.app..."

# ── 1. Create app bundle structure ────────────────────────────────────────────
mkdir -p "${APP_PATH}/Contents/MacOS"
mkdir -p "${APP_PATH}/Contents/Resources"

# ── 2. Write the launcher script ─────────────────────────────────────────────
# exec replaces this shell with the python process (same PID) instead of
# running it as a child — a Quit/kill signal to the app then reliably kills
# the actual server too, instead of possibly orphaning it on port 5757.
cat > "${APP_PATH}/Contents/MacOS/morning-report" << 'LAUNCHER'
#!/bin/bash
exec /usr/bin/python3 "$HOME/morning-report/morning_report.py"
LAUNCHER

chmod +x "${APP_PATH}/Contents/MacOS/morning-report"

# ── 3. Write Info.plist ───────────────────────────────────────────────────────
cat > "${APP_PATH}/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>morning-report</string>
  <key>CFBundleIdentifier</key>
  <string>com.zackglaser.morningreport</string>
  <key>CFBundleName</key>
  <string>Morning Report</string>
  <key>CFBundleDisplayName</key>
  <string>Morning Report</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSUIElement</key>
  <false/>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

# ── 4. Generate icon using Python (no imagemagick required) ───────────────────
mkdir -p "$ICON_DIR"

python3 << ICONSCRIPT
import struct, zlib, math, os

def write_png(path, width, height, pixels):
    """pixels: list of (r,g,b,a) tuples, row-major"""
    def chunk(name, data):
        c = name + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    raw = b''
    for y in range(height):
        raw += b'\x00'
        for x in range(width):
            r,g,b,a = pixels[y*width+x]
            raw += bytes([r,g,b,a])
    
    compressed = zlib.compress(raw, 9)
    png  = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', ihdr)
    png += chunk(b'IDAT', compressed)
    png += chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(png)

def make_icon(size):
    pixels = []
    cx, cy = size/2, size/2
    radius = size * 0.46
    corner = size * 0.22
    
    # Background color: #111110
    bg = (17, 17, 16)
    # Text/border color: #d4d4d0
    fg = (212, 212, 208)
    # Muted: #3a3a38
    muted = (58, 58, 56)
    # Border: #2a2a28
    border_col = (42, 42, 40)
    
    def in_rounded_rect(x, y, rx, ry, rw, rh, rc):
        if x < rx or x > rx+rw or y < ry or y > ry+rh:
            return False
        corners = [(rx+rc, ry+rc), (rx+rw-rc, ry+rc),
                   (rx+rc, ry+rh-rc), (rx+rw-rc, ry+rh-rc)]
        if x < rx+rc and y < ry+rc:
            return math.hypot(x-(rx+rc), y-(ry+rc)) <= rc
        if x > rx+rw-rc and y < ry+rc:
            return math.hypot(x-(rx+rw-rc), y-(ry+rc)) <= rc
        if x < rx+rc and y > ry+rh-rc:
            return math.hypot(x-(rx+rc), y-(ry+rh-rc)) <= rc
        if x > rx+rw-rc and y > ry+rh-rc:
            return math.hypot(x-(rx+rw-rc), y-(ry+rh-rc)) <= rc
        return True

    pad = size * 0.04
    rc = size * 0.22
    
    for y in range(size):
        for x in range(size):
            fx, fy = float(x), float(y)
            
            # Check if inside icon shape (rounded rect)
            inside = in_rounded_rect(fx, fy, pad, pad, size-2*pad, size-2*pad, rc)
            if not inside:
                pixels.append((0,0,0,0))
                continue
            
            # Check border ring (1px inside edge)
            on_border = not in_rounded_rect(fx, fy, pad+1.5, pad+1.5, size-2*pad-3, size-2*pad-3, rc-1.5)
            if on_border:
                pixels.append(border_col + (255,))
                continue
            
            # Text rendering via simple pixel font approximation
            # "morning report." rendered as two lines of monospace dots
            
            # Line 1: "morning" — centered, upper portion
            # Line 2: "report." — centered
            # We'll draw simple 1px horizontal rules to simulate text lines
            
            norm_x = (fx - pad) / (size - 2*pad)
            norm_y = (fy - pad) / (size - 2*pad)
            
            # Top label: tiny dots row at ~18% height
            label_y = 0.18
            if abs(norm_y - label_y) < (0.008 * (512/size + 1)):
                if 0.28 < norm_x < 0.72:
                    pixels.append(muted + (180,))
                    continue
            
            # "morning" line at ~42% height — thick text bar
            text1_y = 0.42
            text1_h = 0.07
            if text1_y - text1_h/2 < norm_y < text1_y + text1_h/2:
                if 0.18 < norm_x < 0.82:
                    # simulate letter gaps
                    slot = (norm_x - 0.18) / 0.64
                    gap_positions = [1/7, 2/7, 3/7, 4/7, 5/7, 6/7]
                    is_gap = any(abs(slot - g) < 0.018 for g in gap_positions)
                    if not is_gap:
                        pixels.append(fg + (230,))
                        continue
            
            # "report." line at ~56% height
            text2_y = 0.56
            text2_h = 0.07
            if text2_y - text2_h/2 < norm_y < text2_y + text2_h/2:
                if 0.22 < norm_x < 0.78:
                    slot = (norm_x - 0.22) / 0.56
                    gap_positions = [1/6, 2/6, 3/6, 4/6, 5/6]
                    is_gap = any(abs(slot - g) < 0.022 for g in gap_positions)
                    if not is_gap:
                        pixels.append(fg + (230,))
                        continue
            
            # Divider line at ~66% height
            div_y = 0.66
            if abs(norm_y - div_y) < 0.006:
                if 0.18 < norm_x < 0.82:
                    pixels.append(border_col + (255,))
                    continue
            
            # [ trash ] [ keep ] rows at ~76% and ~84%
            for row_y in [0.76, 0.84]:
                if abs(norm_y - row_y) < 0.018:
                    # two short blocks
                    if 0.18 < norm_x < 0.44 or 0.56 < norm_x < 0.82:
                        pixels.append(muted + (140,))
                        break
            else:
                pixels.append(bg + (255,))
                continue
            
    return pixels

icon_dir = os.path.expanduser('~/morning-report/icon_build')
os.makedirs(icon_dir, exist_ok=True)

for size in [16, 32, 64, 128, 256, 512, 1024]:
    pixels = make_icon(size)
    write_png(f'{icon_dir}/icon_{size}.png', size, size, pixels)
    print(f'  wrote {size}x{size}')

print('  PNG files written.')
ICONSCRIPT

# ── 5. Build .icns from PNGs ──────────────────────────────────────────────────
echo "→ Building .icns..."

ICONSET="$ICON_DIR/AppIcon.iconset"
mkdir -p "$ICONSET"

cp "$ICON_DIR/icon_16.png"   "$ICONSET/icon_16x16.png"
cp "$ICON_DIR/icon_32.png"   "$ICONSET/icon_16x16@2x.png"
cp "$ICON_DIR/icon_32.png"   "$ICONSET/icon_32x32.png"
cp "$ICON_DIR/icon_64.png"   "$ICONSET/icon_32x32@2x.png"
cp "$ICON_DIR/icon_128.png"  "$ICONSET/icon_128x128.png"
cp "$ICON_DIR/icon_256.png"  "$ICONSET/icon_128x128@2x.png"
cp "$ICON_DIR/icon_256.png"  "$ICONSET/icon_256x256.png"
cp "$ICON_DIR/icon_512.png"  "$ICONSET/icon_256x256@2x.png"
cp "$ICON_DIR/icon_512.png"  "$ICONSET/icon_512x512.png"
cp "$ICON_DIR/icon_1024.png" "$ICONSET/icon_512x512@2x.png"

iconutil -c icns "$ICONSET" -o "${APP_PATH}/Contents/Resources/AppIcon.icns"

# ── 6. Clean up and touch the app ─────────────────────────────────────────────
rm -rf "$ICON_DIR"
touch "$APP_PATH"

echo ""
echo "✓ Installed: ${APP_PATH}"
echo ""
echo "Next steps:"
echo "  1. Open /Applications and find 'Morning Report'"
echo "  2. Right-click → Open (first launch only, to clear Gatekeeper)"
echo "  3. Drag to your Dock"
echo ""
echo "To update the icon cache if it looks wrong:"
echo "  killall Dock"
