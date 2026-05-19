import os
import re
import datetime
import logging
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
PHOTO, TYPE, MACHINE, DURATION, CALORIES, LEVEL, DISTANCE, NOTES, CONFIRM = range(9)

MACHINE_OPTIONS = [
    ["Treadmill", "Incline treadmill"],
    ["StairMaster", "Indoor bike"],
    ["Row machine", "Elliptical"],
    ["Strength / Other"],
]

TYPE_MAP = {
    "Treadmill": "Cardio",
    "Incline treadmill": "Cardio",
    "StairMaster": "Cardio",
    "Indoor bike": "Cardio",
    "Row machine": "Strength",
    "Elliptical": "Cardio",
    "Strength / Other": "Strength",
}


def _authorized(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid == config.USER_ID


def _today() -> str:
    return datetime.date.today().isoformat()


def fmt_report(today_rows, avg7) -> str:
    today = db.get_today_summary()
    lines = ["📋 *Today Total*"]
    if not today_rows:
        lines.append("No workouts logged yet.")
    else:
        for i, w in enumerate(today_rows, 1):
            name = w["machine"]
            lines.append(f"  • {name} — {w['calories']} kcal ({w['duration_min']} min)")
    lines.append("")
    lines.append("📊 *Dashboard Now*")
    lines.append(f"  • Today calories burned: *{today['total_cal']}* kcal")
    lines.append(f"  • Today workout time: *{today['total_min']}* min")
    lines.append(f"  • 7-day avg calories/day: *{avg7['avg_cal']}* kcal/day")
    lines.append(f"  • 7-day avg workout time/day: *{avg7['avg_min']}* min/day")
    need = round(config.DAILY_GOAL_KCAL - today["total_cal"], 1)
    if need > 0:
        lines.append(f"  • Need *{need}* more kcal to hit {config.DAILY_GOAL_KCAL}")
    else:
        lines.append(f"  • 🎯 Daily goal *{config.DAILY_GOAL_KCAL}* kcal reached!")
    return "\n".join(lines)


def parse_ocr_text(text: str) -> dict:
    """Best-effort parse of gym machine screen text."""
    result: dict = {"duration_min": None, "calories": None, "distance": None}
    if not text:
        return result

    # Calories: look for number near CAL, KCAL, etc.
    cal_patterns = [
        r"(\d{3,4})\s*[Kk]?[Cc][Aa][Ll]",
        r"[Cc][Aa][Ll][Oo][Rr][Ii][Ee][Ss]?\s*(\d{3,4})",
        r"[Cc][Aa][Ll]\s*(\d{3,4})",
    ]
    for pat in cal_patterns:
        m = re.search(pat, text)
        if m:
            result["calories"] = int(m.group(1))
            break

    # Duration: look for mm:ss or hh:mm:ss
    dur_patterns = [
        r"(\d{1,2}):(\d{2}):(\d{2})",
        r"(\d{1,2}):(\d{2})",
    ]
    for pat in dur_patterns:
        m = re.search(pat, text)
        if m:
            groups = m.groups()
            if len(groups) == 3:
                result["duration_min"] = int(groups[0]) * 60 + int(groups[1]) + int(groups[2]) / 60
            else:
                result["duration_min"] = int(groups[0]) + int(groups[1]) / 60
            break

    # Distance: look for km or mi
    dist_patterns = [
        r"(\d+\.?\d*)\s*[Kk][Mm]",
        r"(\d+\.?\d*)\s*[Mm][Ii]",
        r"[Dd][Ii][Ss][Tt][Aa][Nn][Cc][Ee]\s*(\d+\.?\d*)",
    ]
    for pat in dist_patterns:
        m = re.search(pat, text)
        if m:
            result["distance"] = float(m.group(1))
            break

    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await update.message.reply_text("Unauthorized.")
        return
    today_rows = db.get_workouts_for_date(_today())
    avg7 = db.get_7day_avg()
    await update.message.reply_text(fmt_report(today_rows, avg7), parse_mode="Markdown")


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    today_rows = db.get_workouts_for_date(_today())
    avg7 = db.get_7day_avg()
    await update.message.reply_text(fmt_report(today_rows, avg7), parse_mode="Markdown")


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
    lines.append(f"7-day avg: *{avg7['avg_cal']}* kcal/day, *{avg7['avg_min']}* min/day")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
    context.user_data.clear()
    photo = update.message.photo[-1]
    file = await photo.get_file()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = os.path.splitext(file.file_path)[1] or ".jpg"
    path = os.path.join(config.PHOTO_DIR, f"workout_{ts}{ext}")
    await file.download_to_drive(path)
    context.user_data["photo_path"] = path

    # Try LLM OCR first, then Tesseract fallback
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
            text = pytesseract.image_to_string(Image.open(path))
            ocr_result = parse_ocr_text(text)
            logger.info("Tesseract OCR text: %s | parsed: %s", text, ocr_result)
        except Exception as e:
            logger.info("Tesseract OCR skipped: %s", e)

    context.user_data["ocr"] = ocr_result

    # Build pre-fill message
    pre = []
    if ocr_result.get("duration_min"):
        pre.append(f"Duration: {ocr_result['duration_min']:.1f} min")
    if ocr_result.get("calories"):
        pre.append(f"Calories: {ocr_result['calories']} kcal")
    if ocr_result.get("distance"):
        pre.append(f"Distance: {ocr_result['distance']} km")

    msg = "Photo received."
    if pre:
        msg += " I read:\n" + "\n".join(pre) + "\n\nSelect machine:"
    else:
        msg += " Select machine:"

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
            f"Machine: {machine}\nOCR duration: {ocr['duration_min']:.1f} min\n\nReply with duration in minutes, or send /skip to keep."
        )
    else:
        await query.edit_message_text(f"Machine: {machine}\n\nReply with duration in minutes:")
    return DURATION


async def duration_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/skip" and "duration_min" in context.user_data:
        pass
    else:
        try:
            context.user_data["duration_min"] = float(text)
        except ValueError:
            await update.message.reply_text("Send a number for duration (minutes):")
            return DURATION

    ocr = context.user_data.get("ocr", {})
    if ocr.get("calories"):
        context.user_data["calories"] = ocr["calories"]
        await update.message.reply_text(
            f"Duration: {context.user_data['duration_min']} min\nOCR calories: {ocr['calories']}\n\nReply with calories, or send /skip to keep."
        )
    else:
        await update.message.reply_text("Reply with calories shown on machine:")
    return CALORIES


async def calories_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/skip" and "calories" in context.user_data:
        pass
    else:
        try:
            context.user_data["calories"] = float(text)
        except ValueError:
            await update.message.reply_text("Send a number for calories:")
            return CALORIES

    ocr = context.user_data.get("ocr", {})
    if ocr.get("distance"):
        context.user_data["distance"] = ocr["distance"]
        await update.message.reply_text(
            f"Calories: {context.user_data['calories']}\nOCR distance: {ocr['distance']} km\n\nReply with distance (km), or send /skip to keep, or /none if not applicable."
        )
    else:
        await update.message.reply_text(
            "Reply with distance (km), or send /skip if not applicable:"
        )
    return DISTANCE


async def distance_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text in ("/skip", "/none"):
        pass
    else:
        try:
            context.user_data["distance"] = float(text)
        except ValueError:
            await update.message.reply_text("Send a number for distance, or /skip:")
            return DISTANCE

    await update.message.reply_text(
        "Reply with level/resistance (e.g., 'Level 11', '18 incline'), or send /skip:"
    )
    return LEVEL


async def level_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text != "/skip":
        context.user_data["level"] = text

    await update.message.reply_text(
        "Any notes? Reply with text, or send /skip:"
    )
    return NOTES


async def notes_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text != "/skip":
        context.user_data["notes"] = text

    d = context.user_data
    summary = (
        f"Confirm log:\n"
        f"  • {d['machine']} ({d['workout_type']})\n"
        f"  • {d['duration_min']} min\n"
        f"  • {d['calories']} kcal"
    )
    if "distance" in d:
        summary += f"\n  • {d['distance']} km"
    if "level" in d:
        summary += f"\n  • {d['level']}"
    if "notes" in d:
        summary += f"\n  • Note: {d['notes']}"

    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ]
    ]
    await update.message.reply_text(summary, reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM


async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        await query.edit_message_text("Cancelled.")
        context.user_data.clear()
        return ConversationHandler.END

    d = context.user_data
    wid = db.add_workout(
        date=_today(),
        workout_type=d.get("workout_type", "Cardio"),
        machine=d["machine"],
        duration_min=d["duration_min"],
        calories=d["calories"],
        level=d.get("level"),
        distance=d.get("distance"),
        notes=d.get("notes"),
        photo_path=d.get("photo_path"),
    )

    today_rows = db.get_workouts_for_date(_today())
    avg7 = db.get_7day_avg()
    await query.edit_message_text(fmt_report(today_rows, avg7), parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    context.user_data.clear()
    return ConversationHandler.END


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    import openpyxl
    from openpyxl.utils import get_column_letter

    xlsx_path = "/Users/billkim/.hermes/profiles/berthier/cache/documents/doc_d65413c65ba1_workout_tracker_tuesday_treadmill_309_corrected_dashboard.xlsx"
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb["Workout Log"]

    # Find first empty row
    row = 2
    while ws.cell(row=row, column=1).value is not None:
        row += 1

    # Get workouts not yet in xlsx (heuristic: last N)
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
        ws.cell(row=row, column=7, value=f"=IF(F{row}=\"\",\"\",F{row}*Settings!$B$3)")
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

    wb.save(xlsx_path)
    await update.message.reply_text(f"Exported {added} new workouts to xlsx.")


async def weight_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    text = update.message.text.replace("/weight", "").strip()
    if not text:
        await update.message.reply_text("Usage: /weight 87.5")
        return
    try:
        w = float(text)
    except ValueError:
        await update.message.reply_text("Usage: /weight 87.5")
        return
    db.add_body_metric(_today(), weight_kg=w)
    await update.message.reply_text(f"Logged weight: {w} kg")


async def daily_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    today_rows = db.get_workouts_for_date(_today())
    avg7 = db.get_7day_avg()
    text = fmt_report(today_rows, avg7)
    await context.bot.send_message(chat_id=config.USER_ID, text=text, parse_mode="Markdown")


def main() -> None:
    db.init_db()
    application = Application.builder().token(config.BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("log", log_cmd),
            MessageHandler(filters.PHOTO, photo_received),
        ],
        states={
            TYPE: [CallbackQueryHandler(type_selected)],
            DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, duration_received)],
            CALORIES: [MessageHandler(filters.TEXT & ~filters.COMMAND, calories_received)],
            DISTANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, distance_received)],
            LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, level_received)],
            NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)],
            CONFIRM: [CallbackQueryHandler(confirm_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("today", today_cmd))
    application.add_handler(CommandHandler("week", week_cmd))
    application.add_handler(CommandHandler("export", export_cmd))
    application.add_handler(CommandHandler("weight", weight_cmd))
    application.add_handler(conv)

    job_queue = application.job_queue
    job_queue.run_daily(daily_report, time=datetime.time(hour=config.DAILY_REPORT_HOUR, minute=0))

    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
