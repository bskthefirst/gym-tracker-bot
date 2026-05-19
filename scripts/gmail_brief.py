#!/usr/bin/env python3
"""
Daily Gmail brief via Himalaya + Telegram.
Fetches recent emails, groups them, sends summary to Telegram.
"""
import os
import subprocess
import json
import re
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ.get("GYM_BOT_TOKEN", "").strip()
USER_ID = os.environ.get("USER_ID", "8578040659").strip()
CDT = timezone(timedelta(hours=-5))


def sh(cmd, timeout=30):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def send_telegram(msg):
    if not TELEGRAM_TOKEN or not USER_ID:
        print("[SKIP] No token or user_id")
        return
    import urllib.request
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": USER_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")


def fetch_emails():
    """Fetch last 30 emails via Himalaya JSON output."""
    out, err, rc = sh("/opt/homebrew/bin/himalaya envelope list --page-size 30 --output json", timeout=30)
    if rc != 0 or not out:
        print(f"[HIMALAYA ERROR] rc={rc} err={err}")
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[JSON ERROR] {e}")
        return []


def classify_email(e):
    """Classify email by sender/subject for grouping."""
    from_obj = e.get("from", {}) or {}
    sender_addr = (from_obj.get("addr") or "").lower()
    sender_name = (from_obj.get("name") or "").lower()
    sender = f"{sender_name} {sender_addr}"
    subject = (e.get("subject") or "").lower()

    if "delivery status" in subject or "mailer-daemon" in sender_addr:
        return "system", "📤 System"
    if "google" in sender or "no-reply@accounts.google.com" in sender_addr:
        return "security", "🚨 Google Security"
    if "paypal" in sender:
        return "paypal", "💰 PayPal"
    if "ingredientcompliance" in sender or "gerim-sterling" in sender:
        return "business", "🧾 Business"
    if any(k in subject for k in ["receipt", "invoice", "payment", "billing"]):
        return "finance", "💳 Finance"
    if any(k in sender_addr for k in ["noreply", "no-reply", "notification"]):
        return "notification", "🔔 Notification"
    return "other", "📧 Other"


def brief():
    emails = fetch_emails()
    if not emails:
        send_telegram("📧 *Gmail Brief*\n\nNo emails fetched (Himalaya error or empty inbox).")
        return

    # Group by category
    groups = {}
    flagged = []
    today_cdt = datetime.now(CDT).date()

    for e in emails:
        cat, label = classify_email(e)
        if cat not in groups:
            groups[cat] = []
        groups[cat].append((e, label))

        # Flag recent security alerts or failures
        date_str = e.get("date", "")
        if cat in ("security", "system"):
            flagged.append(e)

    lines = ["📧 *Daily Gmail Brief*"]
    lines.append(f"_{datetime.now(CDT).strftime('%Y-%m-%d %H:%M CDT')}_")
    lines.append("")

    # Security / System alerts first
    if "security" in groups or "system" in groups:
        lines.append("*⚠️ Alerts & Issues:*")
        for cat in ("security", "system"):
            if cat in groups:
                for e, label in groups[cat][:3]:
                    subj = e.get("subject", "No subject")
                    sender = e.get("from", "Unknown")
                    lines.append(f"  • {label}: _{subj}_")
        lines.append("")

    # Business / Finance
    if "business" in groups or "finance" in groups:
        lines.append("*💼 Business & Finance:*")
        for cat in ("business", "finance"):
            if cat in groups:
                for e, label in groups[cat][:3]:
                    subj = e.get("subject", "No subject")
                    lines.append(f"  • {subj}")
        lines.append("")

    # PayPal
    if "paypal" in groups:
        lines.append("*💰 PayPal:*")
        for e, label in groups["paypal"][:3]:
            subj = e.get("subject", "No subject")
            lines.append(f"  • {subj}")
        lines.append("")

    # Summary stats
    total = len(emails)
    lines.append(f"*📊 Total checked: {total} emails*")

    # Flag if nothing important
    important_cats = {"security", "system", "business", "finance"}
    has_important = any(c in groups for c in important_cats)
    if not has_important:
        lines.append("Nothing urgent. All routine.")

    msg = "\n".join(lines)
    print(msg)
    send_telegram(msg)


if __name__ == "__main__":
    brief()
