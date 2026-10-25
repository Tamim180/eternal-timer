#!/usr/bin/env bash
set -euo pipefail

BIN_DIR="$HOME/.local/bin"
AUTOSTART_DIR="$HOME/.config/autostart"
SYSTEMD_DIR="$HOME/.config/systemd/user"
APPS_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/128x128/apps"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Checking for PySide6..."
if ! python3 -c "import PySide6" 2>/dev/null; then
    echo
    echo "PySide6 isn't installed. Install it with:"
    echo "  sudo pacman -S pyside6"
    echo
    read -p "Install it now? [Y/n] " ans
    if [[ "${ans:-Y}" =~ ^[Yy]?$ ]]; then
        sudo pacman -S --needed pyside6
    else
        echo "Skipping -- install pyside6 before running the GUI."
    fi
fi

echo "Checking for python-cryptography (needed for phone sync)..."
if ! python3 -c "import cryptography" 2>/dev/null; then
    echo
    echo "python-cryptography isn't installed -- everything works except phone sync."
    read -p "Install it now? [Y/n] " ans
    if [[ "${ans:-Y}" =~ ^[Yy]?$ ]]; then
        sudo pacman -S --needed python-cryptography
    fi
fi

mkdir -p "$BIN_DIR" "$AUTOSTART_DIR" "$SYSTEMD_DIR" "$APPS_DIR" "$ICON_DIR"

cp "$SRC_DIR/eternal_timer_core.py" "$BIN_DIR/eternal_timer_core.py"
cp "$SRC_DIR/eternal_timer_sync.py" "$BIN_DIR/eternal_timer_sync.py"
cp "$SRC_DIR/etimer" "$BIN_DIR/etimer"
cp "$SRC_DIR/eternal_timer_gui.py" "$BIN_DIR/eternal_timer_gui.py"
chmod +x "$BIN_DIR/etimer" "$BIN_DIR/eternal_timer_gui.py"

# --- App icon (matches the in-app gradient mark) ---
python3 - "$ICON_DIR/eternal-timer.png" <<'PYEOF'
import sys, os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QPainter, QColor, QFont, QPixmap, QLinearGradient
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
app = QApplication([])
size = 128
pix = QPixmap(size, size)
pix.fill(Qt.transparent)
p = QPainter(pix)
p.setRenderHint(QPainter.Antialiasing)
grad = QLinearGradient(0, 0, size, size)
grad.setColorAt(0, QColor("#a78bfa"))
grad.setColorAt(1, QColor("#ec6eaa"))
p.setBrush(grad)
p.setPen(Qt.NoPen)
m = size * 0.0625
p.drawEllipse(int(m), int(m), int(size - 2 * m), int(size - 2 * m))
p.setPen(QColor("white"))
f = QFont(); f.setBold(True); f.setPointSize(int(size * 0.40))
p.setFont(f)
p.drawText(pix.rect(), Qt.AlignCenter, "\u221e")
p.end()
pix.save(sys.argv[1])
PYEOF

# --- Desktop launcher ---
cat > "$APPS_DIR/eternal-timer.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Eternal Timer
Comment=Multi-timer and stopwatch that survives shutdowns
Exec=$BIN_DIR/eternal_timer_gui.py
Icon=eternal-timer
Terminal=false
Categories=Utility;
EOF

# --- Autostart: catch finished countdowns at login, even across a power cut ---
cat > "$AUTOSTART_DIR/eternal-timer-check.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Eternal Timer check
Comment=Checks for finished timers at login and notifies
Exec=$BIN_DIR/etimer check
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

# --- systemd --user timer: re-checks every minute while logged in ---
cat > "$SYSTEMD_DIR/eternal-timer-check.service" <<EOF
[Unit]
Description=Eternal Timer finish check

[Service]
Type=oneshot
ExecStart=$BIN_DIR/etimer check
EOF

cat > "$SYSTEMD_DIR/eternal-timer-check.timer" <<EOF
[Unit]
Description=Run Eternal Timer check every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now eternal-timer-check.timer

echo
echo "Installed."
echo "  GUI:  find 'Eternal Timer' in your app launcher, or run: eternal_timer_gui.py"
echo "  CLI:  etimer add timer 25m \"deep work\""
echo "        etimer add stopwatch \"reading\""
echo "        etimer list"
echo "  Phone sync:  etimer sync setup   (or click the cloud button in the app)"
echo
echo "Make sure $BIN_DIR is on your PATH (add to ~/.bashrc or ~/.zshrc if not):"
echo '  export PATH="$HOME/.local/bin:$PATH"'
