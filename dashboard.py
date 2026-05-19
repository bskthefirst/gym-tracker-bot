import os
import sqlite3
from datetime import datetime, date, timedelta
from flask import Flask, render_template, jsonify
import json

import db as db_module

app = Flask(__name__)

DB_PATH = os.getenv("GYM_DB_PATH", "/Users/billkim/gym-tracker/gym.db")
DAILY_GOAL = int(os.getenv("GYM_DAILY_GOAL", "1000"))


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = dict_factory
    return conn


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/today")
def api_today():
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM workouts WHERE date = ? ORDER BY id", (today,)
        ).fetchall()
        total_cal = sum(r["calories"] or 0 for r in rows)
        total_min = sum(r["duration_min"] or 0 for r in rows)
    return jsonify({
        "date": today,
        "workouts": rows,
        "total_cal": round(total_cal, 1),
        "total_min": round(total_min, 1),
        "goal": DAILY_GOAL,
        "remaining": max(0, round(DAILY_GOAL - total_cal, 1)),
        "pct": min(100, round((total_cal / DAILY_GOAL) * 100, 1)),
    })


@app.route("/api/week")
def api_week():
    since = (date.today() - timedelta(days=6)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, day, SUM(calories) as cal, SUM(duration_min) as mins "
            "FROM workouts WHERE date >= ? GROUP BY date ORDER BY date",
            (since,),
        ).fetchall()
    avg_cal = round(sum(r["cal"] or 0 for r in rows) / 7, 1)
    avg_min = round(sum(r["mins"] or 0 for r in rows) / 7, 1)
    return jsonify({
        "days": rows,
        "avg_cal": avg_cal,
        "avg_min": avg_min,
        "days_logged": len(rows),
    })


@app.route("/api/recent")
def api_recent():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM workouts ORDER BY date DESC, id DESC LIMIT 30"
        ).fetchall()
    return jsonify({"workouts": rows})


@app.route("/api/weight")
def api_weight():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM body_metrics ORDER BY date DESC LIMIT 30"
        ).fetchall()
    return jsonify({"entries": rows})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
