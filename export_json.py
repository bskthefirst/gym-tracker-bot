import json
import os
import sqlite3
import datetime

DB_PATH = os.getenv("GYM_DB_PATH", "/Users/billkim/gym-tracker/gym.db")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "data")
OUT_FILE = os.path.join(OUT_DIR, "workouts.json")


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def export():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = dict_factory
    cur = conn.cursor()

    workouts = cur.execute(
        "SELECT * FROM workouts ORDER BY date DESC, id DESC"
    ).fetchall()

    body_metrics = cur.execute(
        "SELECT * FROM body_metrics ORDER BY date DESC"
    ).fetchall()

    rest_days = cur.execute(
        "SELECT date FROM rest_days ORDER BY date DESC"
    ).fetchall()
    rest_dates = {r["date"] for r in rest_days}

    settings = {}
    for row in cur.execute("SELECT key, value FROM settings"):
        settings[row["key"]] = row["value"]

    conn.close()

    # Compute streak (workouts + rest days = active)
    streak = 0
    dates = {w["date"] for w in workouts}
    active_dates = dates | rest_dates
    today = datetime.date.today()
    today_str = today.isoformat()
    yesterday_str = (today - datetime.timedelta(days=1)).isoformat()
    if today_str in active_dates or yesterday_str in active_dates:
        for i in range(365):
            d = (today - datetime.timedelta(days=i)).isoformat()
            if d in active_dates:
                streak += 1
            else:
                break

    # Compute PRs
    prs = {}
    for w in workouts:
        m = w["machine"]
        c = w["calories"] or 0
        if m not in prs or c > prs[m]:
            prs[m] = c

    payload = {
        "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
        "workouts": workouts,
        "body_metrics": body_metrics,
        "settings": settings,
        "streak": streak,
        "prs": prs,
    }

    with open(OUT_FILE, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"Exported {len(workouts)} workouts to {OUT_FILE}")


if __name__ == "__main__":
    export()
