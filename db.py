import sqlite3
import datetime
from typing import Optional, List, Dict, Any
from config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            day TEXT,
            type TEXT,
            machine TEXT,
            duration_min REAL,
            calories REAL,
            adj_calories REAL,
            level TEXT,
            distance REAL,
            floors_steps TEXT,
            weight_load TEXT,
            sets_reps TEXT,
            notes TEXT,
            photo_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS body_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            weight_kg REAL,
            waist_cm REAL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            note TEXT
        );

        INSERT OR IGNORE INTO settings (key, value, note) VALUES
            ('calorie_adjustment_factor', '1.0', 'Dashboard uses 100% of machine calories'),
            ('daily_goal_kcal', '1000', 'Stretch workout calories/day objective'),
            ('target_cardio_min_week', '240', 'Target cardio minutes per week');
        """)


def _today() -> str:
    return datetime.date.today().isoformat()


def _week_start(d: datetime.date) -> str:
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def add_workout(
    date: str,
    workout_type: str,
    machine: str,
    duration_min: float,
    calories: float,
    level: Optional[str] = None,
    distance: Optional[float] = None,
    floors_steps: Optional[str] = None,
    weight_load: Optional[str] = None,
    sets_reps: Optional[str] = None,
    notes: Optional[str] = None,
    photo_path: Optional[str] = None,
) -> int:
    d = datetime.date.fromisoformat(date)
    adj_factor = 1.0
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'calorie_adjustment_factor'"
        ).fetchone()
        if row:
            adj_factor = float(row["value"])
    adj_calories = round(calories * adj_factor, 1)
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO workouts
            (date, day, type, machine, duration_min, calories, adj_calories,
             level, distance, floors_steps, weight_load, sets_reps, notes, photo_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date,
                d.strftime("%a"),
                workout_type,
                machine,
                duration_min,
                calories,
                adj_calories,
                level,
                distance,
                floors_steps,
                weight_load,
                sets_reps,
                notes,
                photo_path,
            ),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]


def get_workouts_for_date(date: str) -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM workouts WHERE date = ? ORDER BY id",
            (date,),
        ).fetchall()


def get_recent_workouts(days: int = 7) -> List[sqlite3.Row]:
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM workouts WHERE date >= ? ORDER BY date DESC, id DESC",
            (since,),
        ).fetchall()


def add_body_metric(date: str, weight_kg: Optional[float] = None, waist_cm: Optional[float] = None, notes: Optional[str] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO body_metrics (date, weight_kg, waist_cm, notes) VALUES (?, ?, ?, ?)",
            (date, weight_kg, waist_cm, notes),
        )
        conn.commit()


def get_7day_avg(date: Optional[str] = None) -> Dict[str, Any]:
    if date is None:
        date = _today()
    d = datetime.date.fromisoformat(date)
    window_start = (d - datetime.timedelta(days=6)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(calories), 0) as total_cal,
                   COALESCE(SUM(duration_min), 0) as total_min,
                   COUNT(DISTINCT date) as days_with_workouts
            FROM workouts
            WHERE date >= ? AND date <= ?
            """,
            (window_start, date),
        ).fetchone()
    total_cal = row["total_cal"] or 0
    total_min = row["total_min"] or 0
    avg_cal = round(total_cal / 7, 1)
    avg_min = round(total_min / 7, 1)
    return {
        "total_cal": round(total_cal, 1),
        "total_min": round(total_min, 1),
        "days_with_workouts": row["days_with_workouts"] or 0,
        "avg_cal": avg_cal,
        "avg_min": avg_min,
    }


def get_machine_prs() -> Dict[str, float]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT machine, MAX(calories) as best
            FROM workouts
            GROUP BY machine
            """
        ).fetchall()
    return {r["machine"]: r["best"] for r in rows}


def get_today_summary(date: Optional[str] = None) -> Dict[str, Any]:
    if date is None:
        date = _today()
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(calories), 0) as total_cal,
                   COALESCE(SUM(duration_min), 0) as total_min,
                   COUNT(*) as count
            FROM workouts
            WHERE date = ?
            """,
            (date,),
        ).fetchone()
    return {
        "total_cal": round(row["total_cal"], 1),
        "total_min": round(row["total_min"], 1),
        "count": row["count"],
    }


def get_streak() -> int:
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT date FROM workouts ORDER BY date DESC").fetchall()
    if not rows:
        return 0
    date_set = {r["date"] for r in rows}
    today = datetime.date.today()
    today_str = today.isoformat()
    yesterday_str = (today - datetime.timedelta(days=1)).isoformat()
    if today_str not in date_set and yesterday_str not in date_set:
        return 0
    streak = 0
    for i in range(365):
        d = (today - datetime.timedelta(days=i)).isoformat()
        if d in date_set:
            streak += 1
        else:
            break
    return streak


def get_week_summary(date: Optional[str] = None) -> Dict[str, Any]:
    if date is None:
        date = _today()
    d = datetime.date.fromisoformat(date)
    week_start = _week_start(d)
    week_end = (datetime.date.fromisoformat(week_start) + datetime.timedelta(days=6)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(calories), 0) as total_cal,
                   COALESCE(SUM(duration_min), 0) as total_min,
                   COUNT(DISTINCT date) as days_with_workouts,
                   COUNT(*) as workout_count
            FROM workouts
            WHERE date >= ? AND date <= ?
            """,
            (week_start, week_end),
        ).fetchone()
        setting = conn.execute(
            "SELECT value FROM settings WHERE key = 'target_cardio_min_week'"
        ).fetchone()
    target_min = float(setting["value"]) if setting else 240.0
    return {
        "week_start": week_start,
        "week_end": week_end,
        "total_cal": round(row["total_cal"] or 0, 1),
        "total_min": round(row["total_min"] or 0, 1),
        "days_with_workouts": row["days_with_workouts"] or 0,
        "workout_count": row["workout_count"] or 0,
        "target_min": target_min,
    }
