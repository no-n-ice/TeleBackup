#!/usr/bin/env bash
set -e

# Change to script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Initialize config if not present
if [ ! -f "config.ini" ] && [ -f "config.ini.example" ]; then
    echo "[TeleBackup] First run detected. Creating config.ini from config.ini.example..."
    cp config.ini.example config.ini
    echo "[TeleBackup] Please edit config.ini to add your Telegram API credentials if needed."
    echo ""
fi

# Detect Python interpreter (bundled portable Python or system python3)
if [ -x "$SCRIPT_DIR/python/bin/python3" ]; then
    PY_BIN="$SCRIPT_DIR/python/bin/python3"
elif [ -f "$SCRIPT_DIR/python/bin/python3" ]; then
    chmod +x "$SCRIPT_DIR/python/bin/python3" 2>/dev/null || true
    PY_BIN="$SCRIPT_DIR/python/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PY_BIN="python"
else
    echo "[TeleBackup] ERROR: Python 3 not found. Please install Python 3.10+ or run with bundled runtime."
    exit 1
fi

echo "==================================================="
echo " TeleBackup - Telegram Backup Solution"
echo "==================================================="
echo "[TeleBackup] Starting local server at http://127.0.0.1:8000 ..."
echo "[TeleBackup] Press Ctrl+C to stop."
echo ""

exec "$PY_BIN" app.py "$@"

