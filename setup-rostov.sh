#!/bin/bash
set -e

echo "==> Gym Tracker Bot setup for Rostov"

REPO_DIR="$HOME/gym-tracker-bot"

cd ~
if [ -d "$REPO_DIR" ]; then
    cd "$REPO_DIR" && git pull origin main
else
    git clone https://github.com/bskthefirst/gym-tracker-bot.git "$REPO_DIR"
    cd "$REPO_DIR"
fi

# Install Python deps
python3 -m pip install --user -q python-telegram-bot[job-queue] python-dotenv openpyxl Pillow pytesseract 2>/dev/null || \
python3 -m pip install --user -q python-telegram-bot[job-queue] python-dotenv openpyxl Pillow

# Optional: Tesseract for OCR
if ! command -v tesseract &>/dev/null; then
    echo "==> Tesseract not found. Install with: brew install tesseract"
fi

# Create .env if missing
if [ ! -f .env ]; then
    cp .env.example .env
    echo "==> Created .env — EDIT IT with your real BOT_TOKEN and USER_ID"
fi

# Init DB
python3 -c "import db; db.init_db()"

# Install launchd agent
PLIST_SRC="$REPO_DIR/com.gym-tracker.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.gym-tracker.plist"
cp "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST" 2>/dev/null || launchctl bootstrap gui/$(id -u) "$PLIST_DST"

echo "==> Done. Check logs: tail -f $REPO_DIR/bot.log"
