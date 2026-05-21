#!/usr/bin/env python3
"""
Daily Gmail brief via Himalaya + Telegram.
Only reports new or noteworthy items. Skips routine noise.
"""
import os
import subprocess
import json
import re
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ.get("GYM_BOT_TOKEN", "").strip()
USER_ID = os.environ.get("USER_ID", "8578040659").strip()
CDT = timezone(timedelta(hours=-5))

# Routine noise: skip these subjects/senders entirely
SKIP_SUBJECT_PATTERNS = [
    r"보안 알림",                      # Google Security (Korean)
    r"security alert",                # Google Security (English)
    r"delivery status notification",  # Bouncebacks
    r"mailer-daemon",
    r"ingredientcompliance\.com receipt",  # Routine receipts
    r"get to know your new paypal",   # Onboarding
    r"recent paypal interaction",
    r"은행계좌를 확인해",               # PayPal bank verify (Korean)
    r"bank account confirmed",
    r"link a bank account",           # PayPal onboarding
    r"your monthly statement",
    r"transaction confirmation",
]

SKIP_SENDER_PATTERNS = [
    r"mailer-daemon",
    r"no-reply@accounts\.google\.com",
]


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


def is_noise(e):
    """Return True if this email is routine noise we should skip."""
    subject = (e.get("subject") or "").lower()
    from_obj = e.get("from", {}) or {}
    sender_addr = (from_obj.get("addr") or "").lower()
    sender_name = (from_obj.get("name") or "").lower()
    combined = f"{subject} {sender_addr} {sender_name}"

    for pat in SKIP_SUBJECT_PATTERNS:
        if re.search(pat, combined, re.IGNORECASE):
            return True
    for pat in SKIP_SENDER_PATTERNS:
        if re.search(pat, combined, re.IGNORECASE):
            return True
    return False


def classify_email(e):
    """Classify email by sender/subject for grouping."""
    from_obj = e.get("from", {}) or {}
    sender_addr = (from_obj.get("addr") or "").lower()
    sender_name = (from_obj.get("name") or "").lower()
    subject = (e.get("subject") or "").lower()
    sender = f"{sender_name} {sender_addr}"

    if "paypal" in sender:
        return "paypal", "💰 PayPal"
    if "ingredientcompliance" in sender or "gerim-sterling" in sender:
        return "business", "🧾 Business"
    if any(k in subject for k in ["receipt", "invoice", "payment", "billing"]):
        return "finance", "💳 Finance"
    if any(k in sender_addr for k in ["noreply", "no-reply", "notification"]):
        return "notification", "🔔 Notification"
    return "other", "📧 Other"


STATE_PATH = "/Users/billkim/gym-tracker/.gmail_brief_state.json"

def _load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_state(state):
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"[STATE ERROR] {e}")


def brief():
    emails = fetch_emails()
    if not emails:
        print("[SKIP] No emails fetched.")
        return

    # Filter out noise
    noteworthy = [e for e in emails if not is_noise(e)]

    if not noteworthy:
        # Silent skip — no spam on quiet days
        print("[SKIP] Nothing noteworthy today.")
        return

    # Group noteworthy items
    groups = {}
    for e in noteworthy:
        cat, label = classify_email(e)
        groups.setdefault(cat, []).append((e, label))

    lines = ["📧 *Gmail Brief* — _" + datetime.now(CDT).strftime("%Y-%m-%d %H:%M CDT") + "_"]

    # Build compact sections
    sections = []
    for cat in ("paypal", "business", "finance", "notification", "other"):
        if cat not in groups:
            continue
        items = groups[cat][:3]  # max 3 per category
        label = items[0][1]
        section_lines = [f"*{label}*"]
        for e, _ in items:
            subj = e.get("subject", "No subject")
            section_lines.append(f"  • {subj}")
        sections.append("\n".join(section_lines))

    if not sections:
        print("[SKIP] All remaining items were noise after classification.")
        return

    msg = "\n".join(lines) + "\n\n" + "\n\n".join(sections)

    # Deduplication: don't send identical content twice
    state = _load_state()
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    import hashlib
    content_hash = hashlib.md5(msg.encode("utf-8")).hexdigest()
    last_hash = state.get("last_content_hash")
    if last_hash == content_hash:
        print("[SKIP] Identical brief already sent previously.")
        return

    print(msg)
    send_telegram(msg)
    _save_state({"last_sent_date": today, "last_content_hash": content_hash})


if __name__ == "__main__":
    brief()
