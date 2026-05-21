import os
import re
import datetime
import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import config
import db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
PHOTO, TYPE, DURATION, CALORIES, DISTANCE, CONFIRM, WAITING_WEIGHT = range(7)

MACHINE_OPTIONS = [
    ["Stair Master", "Incline Treadmill"],
    ["Indoor Cycling", "Bicep-Tricep Curl"],
    ["Leg", "Strength- Others"],
]

TYPE_MAP = {
    "Stair Master": "Cardio",
    "Incline Treadmill": "Cardio",
    "Indoor Cycling": "Cardio",
    "Bicep-Tricep Curl": "Strength",
    "Leg": "Strength",
    "Strength- Others": "Strength",
}

# kcal/min estimate for strength machines when user skips calorie entry
# Sources: Reis et al. 2017 (PLoS One), Adeel et al. 2021 (Appl. Sci.),
# StrongerByScience review of João et al. 2022.
STRENGTH_CAL_RATES = {
    "Bicep-Tricep Curl": 3.5,   # upper isolation, moderate intensity: 2.7–3.9 kcal/min
    "Leg": 6.0,                 # lower machine (leg press): 5.0–7.3 kcal/min
    "Strength- Others": 5.5,    # compound mixed: 5.5–6.0 kcal/min
}

SKIP_KEYBOARD = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Skip", callback_data="skip")]])

DEFAULT_KEYBOARD = ReplyKeyboardMarkup(
    [["Log Workout", "⚖️ Weight"], ["🛌 Rest Day"]], resize_keyboard=True, one_time_keyboard=False
)


def _authorized(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid == config.USER_ID


def _today() -> str:
    return datetime.date.today().isoformat()


def fmt_report(today_rows, avg7) -> str:
    streak = db.get_streak()
    lines = []
    if streak > 1:
        lines.append(f"🔥 *{streak} day streak*")
    if db.is_rest_day(_today()):
        lines.append("🛌 *Rest Day*")
    lines.append("📋 *Today Total*")
    total_cal = sum(w["calories"] or 0 for w in today_rows)
    total_min = sum(w["duration_min"] or 0 for w in today_rows)
    if not today_rows:
        lines.append("No workouts logged yet.")
    else:
        for i, w in enumerate(today_rows, 1):
            name = w["machine"]
            lines.append(f"  • {name} — {w['calories']} kcal ({round(w['duration_min'])} min)")
    lines.append("")
    lines.append("📊 *Dashboard Now*")
    lines.append(f"  • Today calories burned: *{round(total_cal, 1)}* kcal")
    lines.append(f"  • Today workout time: *{round(total_min)}* min")
    lines.append(f"  • 7-day avg calories/day: *{avg7['avg_cal']}* kcal/day")
    lines.append(f"  • 7-day avg workout time/day: *{round(avg7['avg_min'])}* min/day")
    need = round(config.DAILY_GOAL_KCAL - total_cal, 1)
    if need > 0:
        lines.append(f"  • Need *{need}* more kcal to hit {config.DAILY_GOAL_KCAL}")
    else:
        lines.append(f"  • 🎯 Daily goal *{config.DAILY_GOAL_KCAL}* kcal reached!")
    return "\n".join(lines)


def parse_ocr_text(text: str) -> dict:
    result: dict = {"duration_min": None, "calories": None, "distance": None}
    if not text:
        return result

    cal_patterns = [
        r"(\d{1,4})\s*[Kk]?[Cc][Aa][Ll]",
        r"[Cc][Aa][Ll][Oo][Rr][Ii][Ee][Ss]?\s*(\d{1,4})",
        r"[Cc][Aa][Ll]\s*(\d{1,4})",
    ]
    for pat in cal_patterns:
        m = re.search(pat, text)
        if m:
            val = int(m.group(1))
            if 50 <= val <= 5000:
                result["calories"] = val
            break

    dur_patterns = [
        r"(\d{1,2}):(\d{2}):(\d{2})",
        r"(\d{1,2}):(\d{2})",
    ]
    for pat in dur_patterns:
        m = re.search(pat, text)
        if m:
            groups = m.groups()
            if len(groups) == 3:
                val = int(groups[0]) * 60 + int(groups[1]) + int(groups[2]) / 60
            else:
                val = int(groups[0]) + int(groups[1]) / 60
            if 1 <= val <= 300:
                result["duration_min"] = val
            break

    # Distance: look for km or mi; reject if > 50 (likely lost decimal point)
    dist_patterns = [
        r"(\d+\.?\d*)\s*[Kk][Mm]",
        r"(\d+\.?\d*)\s*[Mm][Ii]",
        r"[Dd][Ii][Ss][Tt][Aa][Nn][Cc][Ee]\s*(\d+\.?\d*)",
    ]
    for pat in dist_patterns:
        m = re.search(pat, text)
        if m:
            val = float(m.group(1))
            if 0.1 <= val <= 50:
                result["distance"] = val
            break

    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await update.message.reply_text("Unauthorized.")
        return
    today_rows = db.get_workouts_for_date(_today())
    avg7 = db.get_7day_avg()
    await update.message.reply_text(
        fmt_report(today_rows, avg7), parse_mode="Markdown", reply_markup=DEFAULT_KEYBOARD
    )


async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        return ConversationHandler.END
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton(m, callback_data=m) for m in row]
        for row in MACHINE_OPTIONS
    ]
    await update.message.reply_text(
        "Select machine:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TYPE


async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        return ConversationHandler.END
    # Guard: if already mid-workout log, don't restart
    if "machine" in context.user_data:
        await update.message.reply_text(
            "You're already logging a workout. Finish or /cancel first."
        )
        return ConversationHandler.END
    context.user_data.clear()
    photo = update.message.photo[-1]
    try:
        file = await photo.get_file()
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fp = file.file_path or ""
        ext = os.path.splitext(fp)[1] or ".jpg"
        path = os.path.join(config.PHOTO_DIR, f"workout_{ts}{ext}")
        await file.download_to_drive(path)
    except Exception as e:
        logger.error("Photo download failed: %s", e)
        await update.message.reply_text("❌ Failed to download photo. Try again or use /log.")
        return ConversationHandler.END
    context.user_data["photo_path"] = path

    ocr_result = {}
    try:
        from llm_ocr import llm_ocr
        ocr_result = llm_ocr(path) or {}
    except Exception as e:
        logger.info("LLM OCR skipped: %s", e)

    if not ocr_result:
        try:
            import pytesseract
            from PIL import Image
            pytesseract.pytesseract.tesseract_cmd = "/opt/homebrew/bin/tesseract"
            text = pytesseract.image_to_string(Image.open(path))
            if len(text.strip()) < 10 or not re.search(r"\d", text):
                logger.info("Tesseract OCR returned no usable text: %r", text)
            else:
                ocr_result = parse_ocr_text(text)
                logger.info("Tesseract OCR text: %s | parsed: %s", text, ocr_result)
        except Exception as e:
            logger.info("Tesseract OCR skipped: %s", e)

    context.user_data["ocr"] = ocr_result

    pre = []
    if ocr_result.get("duration_min"):
        pre.append(f"Duration: {round(ocr_result['duration_min'])} min")
    if ocr_result.get("calories"):
        pre.append(f"Calories: {ocr_result['calories']} kcal")
    if ocr_result.get("distance"):
        pre.append(f"Distance: {ocr_result['distance']} km")

    msg = "Photo received."
    if pre:
        msg += " I read:\n" + "\n".join(pre) + "\n\nSelect machine:"
    else:
        msg += " (Couldn't read numbers from photo — you'll enter them manually.)\n\nSelect machine:"

    keyboard = [
        [InlineKeyboardButton(m, callback_data=m) for m in row]
        for row in MACHINE_OPTIONS
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    return TYPE


async def type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    machine = query.data
    context.user_data["machine"] = machine
    context.user_data["workout_type"] = TYPE_MAP.get(machine, "Cardio")

    ocr = context.user_data.get("ocr", {})
    if ocr.get("duration_min"):
        context.user_data["duration_min"] = ocr["duration_min"]
        await query.edit_message_text(
            f"Machine: {machine}\nOCR duration: {round(ocr['duration_min'])} min\n\nReply with duration in minutes, or tap Skip to keep."
        )
        await query.message.reply_text("Duration:", reply_markup=SKIP_KEYBOARD)
    else:
        await query.edit_message_text(f"Machine: {machine}\n\nReply with duration in minutes:")
    return DURATION


async def duration_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    try:
        context.user_data["duration_min"] = float(text)
    except ValueError:
        await update.message.reply_text("Send a number for duration (minutes), or /cancel:")
        return DURATION

    return await _ask_calories(update, context)


async def duration_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if "duration_min" not in context.user_data:
        await query.edit_message_text("No OCR duration to skip. Reply with duration in minutes:")
        return DURATION
    await query.edit_message_text(f"Duration: {round(context.user_data['duration_min'])} min ✅")
    return await _ask_calories(update, context)


async def _ask_calories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg_obj = update.message or update.callback_query.message
    d = context.user_data
    if "duration_min" not in d:
        await msg_obj.reply_text("Something went wrong. Start over with /log")
        d.clear()
        return ConversationHandler.END
    ocr = d.get("ocr", {})
    machine = d.get("machine", "")
    if ocr.get("calories"):
        d["calories"] = ocr["calories"]
        msg = f"Duration: {round(d['duration_min'])} min\nOCR calories: {ocr['calories']}\n\nReply with calories, or tap Skip to keep."
    elif machine in STRENGTH_CAL_RATES:
        rate = STRENGTH_CAL_RATES[machine]
        est = round(d['duration_min'] * rate)
        msg = f"Reply with calories, or tap Skip to auto-estimate (~{est} kcal):"
    else:
        msg = "Reply with calories shown on machine:"
    await msg_obj.reply_text(msg, reply_markup=SKIP_KEYBOARD)
    return CALORIES


async def calories_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)
    try:
        context.user_data["calories"] = float(text)
    except ValueError:
        await update.message.reply_text("Send a number for calories, or /cancel:")
        return CALORIES

    return await _ask_distance(update, context)


async def calories_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    machine = context.user_data.get("machine", "")
    if "calories" not in context.user_data:
        if machine in STRENGTH_CAL_RATES and "duration_min" in context.user_data:
            estimated = round(context.user_data["duration_min"] * STRENGTH_CAL_RATES[machine])
            context.user_data["calories"] = estimated
            await query.edit_message_text(f"Calories: {estimated} kcal (auto-estimated) ✅")
            return await _ask_distance(update, context)
        await query.edit_message_text("No calories to skip. Reply with calories:")
        return CALORIES
    await query.edit_message_text(f"Calories: {context.user_data['calories']} kcal ✅")
    return await _ask_distance(update, context)


async def _ask_distance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg_obj = update.message or update.callback_query.message
    d = context.user_data
    if "calories" not in d:
        await msg_obj.reply_text("Something went wrong. Start over with /log")
        d.clear()
        return ConversationHandler.END
    ocr = d.get("ocr", {})
    if ocr.get("distance"):
        d["distance"] = ocr["distance"]
        msg = f"Calories: {d['calories']}\nOCR distance: {ocr['distance']} km\n\nReply with distance (km), or tap Skip to keep."
    else:
        msg = "Reply with distance (km), or tap Skip if not applicable:"
    await msg_obj.reply_text(msg, reply_markup=SKIP_KEYBOARD)
    return DISTANCE


async def distance_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/cancel":
        return await cancel(update, context)

    miles_match = re.search(r"([\d.]+)\s*(?:miles?|mi)", text, re.IGNORECASE)
    if miles_match:
        miles = float(miles_match.group(1))
        km = round(miles * 1.60934, 2)
        context.user_data["distance"] = km
        await update.message.reply_text(f"Converted {miles} mi → {km} km")
    else:
        try:
            context.user_data["distance"] = float(text)
        except ValueError:
            await update.message.reply_text("Send a number for distance, or /cancel:")
            return DISTANCE

    return await _ask_confirm(update, context)


async def distance_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Distance skipped ✅")
    return await _ask_confirm(update, context)


async def _ask_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    d = context.user_data
    required = ["machine", "duration_min", "calories"]
    missing = [k for k in required if k not in d]
    if missing:
        msg_obj = update.message or update.callback_query.message
        await msg_obj.reply_text(
            f"Missing data ({', '.join(missing)}). Start over with /log"
        )
        d.clear()
        return ConversationHandler.END
    summary = (
        f"Confirm log:\n"
        f"  • {d['machine']} ({d['workout_type']})\n"
        f"  • {round(d['duration_min'])} min\n"
        f"  • {d['calories']} kcal"
    )
    if "distance" in d:
        summary += f"\n  • {d['distance']} km"

    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ]
    ]
    msg_obj = update.message or update.callback_query.message
    await msg_obj.reply_text(summary, reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM


async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        context.user_data.clear()
        return ConversationHandler.END

    if context.user_data.get("_confirmed"):
        await query.edit_message_text("Already saved.")
        return ConversationHandler.END

    d = context.user_data
    required = ["machine", "duration_min", "calories"]
    missing = [k for k in required if k not in d]
    if missing:
        await query.edit_message_text(f"Missing: {', '.join(missing)}. Start over with /log")
        context.user_data.clear()
        return ConversationHandler.END

    machine = d["machine"]
    calories = d["calories"]

    try:
        prs = db.get_machine_prs()
        previous_best = prs.get(machine)

        context.user_data["_confirmed"] = True
        wid = db.add_workout(
            date=_today(),
            workout_type=d.get("workout_type", "Cardio"),
            machine=machine,
            duration_min=d["duration_min"],
            calories=calories,
            level=d.get("level"),
            distance=d.get("distance"),
            notes=d.get("notes"),
            photo_path=d.get("photo_path"),
        )
    except Exception as e:
        logger.error("Confirm handler error: %s", e)
        await query.edit_message_text(f"❌ Error saving workout: {e}\nTry /log again.")
        context.user_data.clear()
        return ConversationHandler.END

    try:
        import subprocess
        subprocess.run(["python3", "export_json.py"], cwd="/Users/billkim/gym-tracker", check=True, capture_output=True)
        subprocess.run(["git", "add", "docs/data/workouts.json"], cwd="/Users/billkim/gym-tracker", check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"Auto-export workouts after log {wid}"], cwd="/Users/billkim/gym-tracker", check=False, capture_output=True)
        subprocess.run(["git", "push", "origin", "main"], cwd="/Users/billkim/gym-tracker", check=True, capture_output=True)
    except Exception as e:
        logger.error("Auto-export/push failed: %s", e)

    today_rows = db.get_workouts_for_date(_today())
    avg7 = db.get_7day_avg()
    await query.edit_message_text(fmt_report(today_rows, avg7), parse_mode="Markdown")

    if previous_best is None or calories > previous_best:
        msg = f"🎉 New PR on {machine}: {calories} kcal"
        if previous_best:
            msg += f" (previous best: {previous_best})"
        await context.bot.send_message(chat_id=config.USER_ID, text=msg)

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        return ConversationHandler.END
    await update.message.reply_text("Cancelled.")
    context.user_data.clear()
    return ConversationHandler.END


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
    except ImportError:
        await update.message.reply_text("❌ openpyxl not installed.")
        return

    xlsx_path = "/Users/billkim/.hermes/profiles/berthier/cache/documents/doc_d65413c65ba1_workout_tracker_tuesday_treadmill_309_corrected_dashboard.xlsx"
    try:
        wb = openpyxl.load_workbook(xlsx_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Cannot load xlsx: {e}")
        return
    ws = wb["Workout Log"]

    row = 2
    while ws.cell(row=row, column=1).value is not None:
        row += 1

    workouts = db.get_recent_workouts(30)
    existing_dates_machines = set()
    for r in range(2, row):
        d = ws.cell(row=r, column=1).value
        m = ws.cell(row=r, column=4).value
        if d and m:
            if hasattr(d, "isoformat"):
                d = d.isoformat()
            existing_dates_machines.add((str(d)[:10], str(m)))

    added = 0
    for w in workouts:
        key = (w["date"], w["machine"])
        if key in existing_dates_machines:
            continue
        ws.cell(row=row, column=1, value=w["date"])
        ws.cell(row=row, column=2, value=f"=IF(A{row}=\"\",\"\",TEXT(A{row},\"ddd\"))")
        ws.cell(row=row, column=3, value=w["type"])
        ws.cell(row=row, column=4, value=w["machine"])
        ws.cell(row=row, column=5, value=w["duration_min"])
        ws.cell(row=row, column=6, value=w["calories"])
        ws.cell(row=row, column=7, value=f"=IF(F{row}=\"\",\"\",F{row}*Settings!\$B\$3)")
        ws.cell(row=row, column=8, value=w["level"])
        ws.cell(row=row, column=9, value=w["distance"])
        ws.cell(row=row, column=10, value=w["floors_steps"])
        ws.cell(row=row, column=11, value=w["weight_load"])
        ws.cell(row=row, column=12, value=w["sets_reps"])
        ws.cell(row=row, column=13, value=w["notes"])
        ws.cell(row=row, column=14, value=f"=IF(A{row}=\"\",\"\",A{row}-WEEKDAY(A{row},2)+1)")
        ws.cell(row=row, column=15, value=f"=IF(A{row}=\"\",\"\",TEXT(A{row},\"yyyy-mm\"))")
        row += 1
        added += 1

    try:
        wb.save(xlsx_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to save xlsx: {e}")
        return
    await update.message.reply_text(f"Exported {added} new workouts to xlsx.")


async def weight_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    # Cancel any active conversation
    context.user_data.clear()
    text = update.message.text.replace("/weight", "").strip()
    if not text:
        await update.message.reply_text("Usage: /weight 87.5")
        return
    try:
        w = float(text)
    except ValueError:
        await update.message.reply_text("Usage: /weight 87.5")
        return
    if w <= 0:
        await update.message.reply_text("Weight must be positive.")
        return
    await _log_weight(update, context, w)


async def weight_button_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("Send your weight in kg (e.g., 87.5):")
    return WAITING_WEIGHT


async def weight_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/cancel":
        await update.message.reply_text("Cancelled.")
        context.user_data.clear()
        return ConversationHandler.END
    # Allow "/weight 86.1" as a reply, not just "86.1"
    text = text.replace("/weight", "").strip()
    try:
        w = float(text)
    except ValueError:
        await update.message.reply_text("Send a number for weight in kg, or /cancel:")
        return WAITING_WEIGHT
    if w <= 0:
        await update.message.reply_text("Weight must be positive. Try again or /cancel:")
        return WAITING_WEIGHT
    await _log_weight(update, context, w)
    context.user_data.clear()
    return ConversationHandler.END


async def _log_weight(update: Update, context: ContextTypes.DEFAULT_TYPE, w: float) -> None:
    db.add_body_metric(_today(), weight_kg=w)
    reply = f"✅ Logged weight: {w} kg"
    proj = db.weight_projection()
    if proj:
        reply += f"\n📊 7-day avg: {proj['current_ma']} kg"
        reply += f"\n📉 Trend: {proj['slope_kg_week']:+} kg/week"
        if proj['weeks_to_goal'] is not None:
            reply += f"\n🎯 Goal {proj['goal']} kg: ~{proj['weeks_to_goal']} weeks"
        if proj['message']:
            reply += f"\n{proj['message']}"
    await update.message.reply_text(reply)


async def goal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    text = update.message.text.replace("/goal", "").strip()
    if not text:
        current = db.get_goal_weight()
        if current:
            await update.message.reply_text(f"Current goal: {current} kg\nTo change: /goal 80")
        else:
            await update.message.reply_text("No goal set. Use /goal 80")
        return
    try:
        w = float(text)
    except ValueError:
        await update.message.reply_text("Usage: /goal 80")
        return
    if w <= 0:
        await update.message.reply_text("Goal must be positive.")
        return
    db.set_goal_weight(w)
    await update.message.reply_text(f"Goal set: {w} kg")


async def rest_day_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    today = _today()
    if db.is_rest_day(today):
        await update.message.reply_text(
            "🛌 Today is already marked as a rest day.", reply_markup=DEFAULT_KEYBOARD
        )
        return
    db.mark_rest_day(today)
    streak = db.get_streak()
    msg = "🛌 Rest day marked for today."
    if streak > 1:
        msg += f"\n🔥 Streak alive: *{streak}* days"
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=DEFAULT_KEYBOARD)


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    today_rows = db.get_workouts_for_date(_today())
    avg7 = db.get_7day_avg()
    await update.message.reply_text(
        fmt_report(today_rows, avg7), parse_mode="Markdown", reply_markup=DEFAULT_KEYBOARD
    )


async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    avg7 = db.get_7day_avg()
    recent = db.get_recent_workouts(7)
    lines = ["📅 *Last 7 Days*"]
    for w in recent:
        lines.append(
            f"  • {w['date']} {w['day']} — {w['machine']} {w['calories']} kcal"
        )
    lines.append("")
    lines.append(f"7-day avg: *{avg7['avg_cal']}* kcal/day, *{round(avg7['avg_min'])}* min/day")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def daily_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    today_rows = db.get_workouts_for_date(_today())
    if not today_rows and not db.is_rest_day(_today()):
        return
    avg7 = db.get_7day_avg()
    text = fmt_report(today_rows, avg7)
    await context.bot.send_message(
        chat_id=config.USER_ID, text=text, parse_mode="Markdown", reply_markup=DEFAULT_KEYBOARD
    )


async def beat_yesterday_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    yesterday = db.get_yesterday_summary()
    today_so_far = db.get_today_summary()
    lines = ["🌅 *Morning Challenge*"]
    lines.append(f"Yesterday: *{yesterday['total_cal']}* kcal, *{round(yesterday['total_min'])}* min")
    if today_so_far["total_cal"] > 0:
        lines.append(f"So far today: *{today_so_far['total_cal']}* kcal")
    if yesterday["total_cal"] > 0:
        need = round(yesterday["total_cal"] - today_so_far["total_cal"], 1)
        if need > 0:
            lines.append(f"🎯 Beat yesterday: need *{need}* more kcal")
        else:
            lines.append("🎯 Already beat yesterday!")
    else:
        lines.append("No workout yesterday. Today is a fresh start.")
    await context.bot.send_message(
        chat_id=config.USER_ID, text="\n".join(lines), parse_mode="Markdown", reply_markup=DEFAULT_KEYBOARD
    )


async def weekly_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    summary = db.get_week_summary()
    target_cal = config.DAILY_GOAL_KCAL * 7
    target_min = summary["target_min"]
    cal_gap = round(target_cal - summary["total_cal"], 1)
    min_gap = round(target_min - summary["total_min"], 1)
    lines = [f"📅 *Week of {summary['week_start']}*"]
    lines.append(f"  • Calories: *{summary['total_cal']}* / {target_cal} kcal")
    lines.append(f"  • Cardio time: *{round(summary['total_min'])}* / {target_min} min")
    lines.append(f"  • Workouts: *{summary['workout_count']}* on *{summary['days_with_workouts']}* days")
    lines.append("")
    if cal_gap > 0 and min_gap > 0:
        lines.append(f"⚠️ Need *{cal_gap}* kcal and *{min_gap}* min to hit weekly targets.")
    elif cal_gap > 0:
        lines.append(f"⚠️ Need *{cal_gap}* kcal to hit weekly calorie target.")
    elif min_gap > 0:
        lines.append(f"⚠️ Need *{min_gap}* min to hit weekly cardio target.")
    else:
        lines.append("🎯 Weekly targets reached!")
    await context.bot.send_message(
        chat_id=config.USER_ID, text="\n".join(lines), parse_mode="Markdown", reply_markup=DEFAULT_KEYBOARD
    )


async def daily_gmail_brief(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the standalone Gmail brief script. It handles its own
    noise filtering, deduplication, and Telegram sending."""
    import subprocess
    import sys
    try:
        proc = subprocess.run(
            [sys.executable, "scripts/gmail_brief.py"],
            cwd="/Users/billkim/gym-tracker",
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            logger.error("daily_gmail_brief failed: %s", proc.stderr)
        else:
            out = proc.stdout.strip()
            if out and not out.startswith("[SKIP]"):
                logger.info("daily_gmail_brief sent: %s", out[:200])
            else:
                logger.info("daily_gmail_brief: %s", out or "no output")
    except Exception as e:
        logger.error("daily_gmail_brief exception: %s", e)


def main() -> None:
    db.init_db()
    application = Application.builder().token(config.BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("log", log_cmd),
            MessageHandler(filters.Regex("^(Log Workout)$"), log_cmd),
            MessageHandler(filters.PHOTO, photo_received),
        ],
        states={
            TYPE: [CallbackQueryHandler(type_selected)],
            DURATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, duration_received),
                CallbackQueryHandler(duration_skip, pattern="^skip$"),
            ],
            CALORIES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, calories_received),
                CallbackQueryHandler(calories_skip, pattern="^skip$"),
            ],
            DISTANCE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, distance_received),
                CallbackQueryHandler(distance_skip, pattern="^skip$"),
            ],
            CONFIRM: [CallbackQueryHandler(confirm_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    weight_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^(⚖️ Weight)$"), weight_button_start),
        ],
        states={
            WAITING_WEIGHT: [MessageHandler(filters.TEXT, weight_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("today", today_cmd))
    application.add_handler(CommandHandler("week", week_cmd))
    application.add_handler(CommandHandler("rest", rest_day_cmd))
    application.add_handler(CommandHandler("export", export_cmd))
    application.add_handler(CommandHandler("goal", goal_cmd))
    application.add_handler(CommandHandler("weight", weight_cmd))
    application.add_handler(MessageHandler(filters.Regex("^(🛌 Rest Day)$"), rest_day_cmd))
    application.add_handler(weight_conv)
    application.add_handler(conv)

    job_queue = application.job_queue
    job_queue.run_daily(daily_report, time=datetime.time(hour=config.DAILY_REPORT_HOUR, minute=0))
    # Morning challenge — Mon/Wed/Fri 8:10 AM CDT
    job_queue.run_daily(beat_yesterday_report, time=datetime.time(hour=8, minute=10), days=(0, 2, 4))
    # Daily Gmail brief — 9:00 AM CDT
    job_queue.run_daily(daily_gmail_brief, time=datetime.time(hour=9, minute=0))
    # Saturday 9:00 AM CDT (Mini local time)
    job_queue.run_daily(weekly_alert, time=datetime.time(hour=9, minute=0), days=(5,))

    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
