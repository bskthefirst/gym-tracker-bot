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

        CREATE TABLE IF NOT EXISTS rest_days (
            date TEXT PRIMARY KEY,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'calorie_adjustment_factor'"
        ).fetchone()
        adj_factor = float(row["value"] or "1.0") if row else 1.0
        adj_calories = round(calories * adj_factor, 1)
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


def set_goal_weight(weight_kg: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("goal_weight_kg", str(weight_kg)),
        )
        conn.commit()


def get_goal_weight() -> Optional[float]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'goal_weight_kg'"
        ).fetchone()
    return float(row["value"]) if row else None


def get_body_metrics() -> List[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM body_metrics WHERE weight_kg IS NOT NULL ORDER BY date"
        ).fetchall()


def _moving_average(values: List[float], window: int = 7) -> List[float]:
    result = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        window_vals = values[start:i + 1]
        result.append(sum(window_vals) / len(window_vals))
    return result


def weight_projection() -> Optional[Dict[str, Any]]:
    """Compute 7-day MA, linear regression slope, and ETA to goal.
    Returns dict with current_ma, slope_kg_week, weeks_to_goal, goal, message,
    or None if insufficient data.
    """
    rows = get_body_metrics()
    if len(rows) < 7:
        return None
    goal = get_goal_weight()
    if goal is None:
        return None

    weights = [r["weight_kg"] for r in rows]
    ma = _moving_average(weights, window=7)

    # Use last 30 days of MA (or all available)
    n = min(30, len(ma))
    x = list(range(n))  # day indices 0, 1, 2, ...
    y = ma[-n:]

    # Simple linear regression: y = mx + b
    n_val = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi * xi for xi in x)
    denom = n_val * sum_x2 - sum_x * sum_x
    if denom == 0:
        return None
    slope = (n_val * sum_xy - sum_x * sum_y) / denom  # kg per day index
    slope_kg_week = slope * 7  # kg per week

    current_ma = ma[-1]
    delta = current_ma - goal

    if abs(slope_kg_week) < 0.05:  # less than 50g/week = essentially flat
        return {
            "current_ma": round(current_ma, 1),
            "slope_kg_week": round(slope_kg_week, 2),
            "weeks_to_goal": None,
            "goal": round(goal, 1),
            "message": "Weight trend is flat. No ETA available.",
        }

    at_goal = abs(delta) < 0.5
    moving_toward = (delta > 0 and slope_kg_week < 0) or (delta < 0 and slope_kg_week > 0)

    if at_goal:
        return {
            "current_ma": round(current_ma, 1),
            "slope_kg_week": round(slope_kg_week, 2),
            "weeks_to_goal": None,
            "goal": round(goal, 1),
            "message": f"At goal weight ({goal} kg).",
        }
    elif moving_toward:
        weeks_to_goal = abs(delta) / abs(slope_kg_week)
        return {
            "current_ma": round(current_ma, 1),
            "slope_kg_week": round(slope_kg_week, 2),
            "weeks_to_goal": round(weeks_to_goal, 1),
            "goal": round(goal, 1),
            "message": None,
        }
    else:
        direction = "gaining" if slope_kg_week > 0 else "losing"
        return {
            "current_ma": round(current_ma, 1),
            "slope_kg_week": round(slope_kg_week, 2),
            "weeks_to_goal": None,
            "goal": round(goal, 1),
            "message": f"Currently {direction} {abs(slope_kg_week):.2f} kg/week — away from goal.",
        }


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


def get_yesterday_summary() -> Dict[str, Any]:
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    return get_today_summary(yesterday)


def get_streak() -> int:
    with get_conn() as conn:
        workout_dates = {r["date"] for r in conn.execute("SELECT DISTINCT date FROM workouts").fetchall()}
        rest_dates = {r["date"] for r in conn.execute("SELECT date FROM rest_days").fetchall()}
    active_dates = workout_dates | rest_dates
    if not active_dates:
        return 0
    today = datetime.date.today()
    today_str = today.isoformat()
    yesterday_str = (today - datetime.timedelta(days=1)).isoformat()
    if today_str not in active_dates and yesterday_str not in active_dates:
        return 0
    streak = 0
    for i in range(365):
        d = (today - datetime.timedelta(days=i)).isoformat()
        if d in active_dates:
            streak += 1
        else:
            break
    return streak


def mark_rest_day(date: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO rest_days (date) VALUES (?)",
            (date,),
        )
        conn.commit()


def is_rest_day(date: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM rest_days WHERE date = ?", (date,)
        ).fetchone()
    return bool(row)


def set_profile(height_cm: float, age: int, gender: str, pal: float) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("profile_height_cm", str(height_cm)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("profile_age", str(age)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("profile_gender", gender.lower()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("profile_pal", str(pal)),
        )
        conn.commit()


def get_profile() -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'profile_%'"
        ).fetchall()
    if not rows:
        return None
    d = {r["key"].replace("profile_", ""): r["value"] for r in rows}
    try:
        return {
            "height_cm": float(d.get("height_cm", 0)),
            "age": int(d.get("age", 0)),
            "gender": d.get("gender", ""),
            "pal": float(d.get("pal", 1.4)),
        }
    except (ValueError, TypeError):
        return None


def set_target_date(target_date: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("target_date", target_date),
        )
        conn.commit()


def get_target_date() -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'target_date'"
        ).fetchone()
    return row["value"] if row else None


def get_weight_math_inputs() -> Optional[Dict[str, Any]]:
    profile = get_profile()
    goal = get_goal_weight()
    target = get_target_date()
    metrics = get_body_metrics()
    if not profile or goal is None or not metrics:
        return None
    latest = metrics[-1]
    current_weight = latest["weight_kg"]
    return {
        "profile": profile,
        "goal": goal,
        "target_date": target,
        "current_weight": current_weight,
        "latest_date": latest["date"],
        "metrics": metrics,
    }


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
