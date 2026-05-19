# Gym Tracker Bot

Telegram bot for logging gym workouts. Runs on your Mac Mini, free forever.

## Features

- **Photo upload**: Snap machine screen, send to bot. OCR pre-fills duration/calories/distance.
- **Override**: If OCR is wrong, just type the correct number.
- **Instant dashboard**: After every log, bot replies with today's total + 7-day averages.
- **Daily report**: Auto message at 9 PM with day's summary.
- **XLSX export**: `/export` appends new rows to your existing xlsx, preserving formulas.
- **Weight logging**: `/weight 87.5`

## Setup

1. **Create a Telegram bot**
   - Message [@BotFather](https://t.me/botfather)
   - `/newbot`, name it, get your **token**

2. **Get your Telegram user ID**
   - Message [@userinfobot](https://t.me/userinfobot)
   - Note the numeric ID

3. **Configure**
   ```bash
   cp .env.example .env
   # Edit .env with your token and user ID
   ```

4. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   # Optional: OCR support
   brew install tesseract
   pip install pytesseract pillow
   ```

5. **Run**
   ```bash
   python bot.py
   ```

6. **Auto-start on Mac Mini** (optional but recommended)
   ```bash
   cp com.gym-tracker.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.gym-tracker.plist
   ```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Today's dashboard |
| `/log` | Start manual workout log |
| `/today` | Today's summary |
| `/week` | Last 7 days + averages |
| `/export` | Sync new rows to xlsx |
| `/weight 87.5` | Log body weight |
| `/cancel` | Cancel current log |

## Gym workflow

1. Finish workout, snap photo of machine screen
2. Send photo to bot
3. Tap machine type button
4. Correct OCR values if needed (or /skip)
5. Confirm → instant dashboard reply

## Notes

- Data lives in `gym.db` (SQLite) on the Mini
- Photos saved to `photos/` folder
- XLSX is **not** written to automatically; use `/export` when you want to update it
