from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta
import os
import math
import bcrypt
import psycopg2
from psycopg2.extras import RealDictCursor

# =====================
# Basic setup
# =====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

DATABASE_URL = os.environ.get("DATABASE_URL")

# =====================
# Database
# =====================
def get_db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor,
        sslmode="require"
    )

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ot_records (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL,
        date DATE NOT NULL,
        day_type TEXT,
        start TIME,
        out TIME,
        ot1 REAL,
        ot15 REAL,
        ot3 REAL,
        ts TIMESTAMP
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

@app.on_event("startup")
def startup():
    init_db()

# =====================
# Utility
# =====================
def round_half_down(hours):
    return math.floor(hours * 2) / 2

def t(hm):
    return datetime.strptime(hm, "%H:%M")

# =====================
# OT CALCULATION
# =====================
def calculate_ot(out_str, start_str=None, day_type="weekday"):
    out_dt = t(out_str)
    ot1 = ot15 = ot3 = 0.0

    # ----- จันทร์-ศุกร์ -----
    if day_type == "weekday":
        ot_start = t("17:20")
        if out_dt > ot_start:
            diff = (out_dt - ot_start).total_seconds() / 3600
            ot15 = round_half_down(diff)
        return ot1, ot15, ot3

    # ----- วันหยุด -----
    if not start_str:
        return 0, 0, 0

    start_dt = t(start_str)
    total = (out_dt - start_dt).total_seconds() / 3600
    if total <= 0:
        return 0, 0, 0

    # พัก 1 ชม ถ้าทำ >= 6 ชม
    if total >= 6:
        total -= 1

    ot1 = min(8, round_half_down(total))

    if total > 8:
        after_8 = total - 8
        after_8 -= (20 / 60)  # พัก 20 นาที
        if after_8 > 0:
            ot3 = round_half_down(after_8)

    return ot1, 0, ot3

# =====================
# Pages
# =====================
@app.get("/", response_class=HTMLResponse)
def login_page():
    return open(os.path.join(BASE_DIR, "static", "login.html"), encoding="utf-8").read()

@app.get("/ot", response_class=HTMLResponse)
def ot_page():
    return open(os.path.join(BASE_DIR, "static", "ot.html"), encoding="utf-8").read()

# =====================
# Login / Register
# =====================
@app.post("/login")
async def login(req: Request):
    data = await req.json()
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT password FROM users WHERE username=%s", (data["username"],))
    user = cur.fetchone()

    cur.close()
    conn.close()

    if not user or not bcrypt.checkpw(
        data["password"].encode(),
        user["password"].encode()
    ):
        raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

    return {"success": True}

@app.post("/register")
async def register(req: Request):
    data = await req.json()
    hashed = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM users WHERE username=%s", (data["username"],))
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="ผู้ใช้นี้มีอยู่แล้ว")

    cur.execute(
        "INSERT INTO users (username, password) VALUES (%s,%s)",
        (data["username"], hashed)
    )

    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}

# =====================
# Save OT
# =====================
@app.post("/save_ot")
async def save_ot(req: Request):
    data = await req.json()

    ot1, ot15, ot3 = calculate_ot(
        data["out"],
        data.get("in"),
        "weekday" if data["day"] == "weekday" else "holiday"
    )

    conn = get_db()
    cur = conn.cursor()

    # วันเดียวกัน = update
    cur.execute("""
    DELETE FROM ot_records
    WHERE username=%s AND date=%s
    """, (data["username"], data["date"]))

    cur.execute("""
    INSERT INTO ot_records
    (username, date, day_type, start, out, ot1, ot15, ot3, ts)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        data["username"],
        data["date"],
        data["day"],
        data.get("in"),
        data["out"],
        ot1, ot15, ot3,
        datetime.now()
    ))

    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}

# =====================
# Summary (16 → 15)
# =====================
@app.get("/summary/{username}/{month}")
def summary(username: str, month: str):
    end_month = datetime.strptime(month, "%Y-%m")
    end_date = end_month.replace(day=15)

    prev_month = end_month - timedelta(days=31)
    start_date = prev_month.replace(day=16)

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    SELECT * FROM ot_records
    WHERE username=%s
      AND date BETWEEN %s AND %s
    ORDER BY date
    """, (username, start_date, end_date))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    total = {"ot1": 0, "ot15": 0, "ot3": 0}
    for r in rows:
        total["ot1"] += r["ot1"]
        total["ot15"] += r["ot15"]
        total["ot3"] += r["ot3"]

    return {
        "period": {
            "from": start_date.strftime("%Y-%m-%d"),
            "to": end_date.strftime("%Y-%m-%d")
        },
        "rows": rows,
        "total": total
    }

# =====================
# Delete all
# =====================
@app.delete("/delete_all/{username}")
def delete_all(username: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM ot_records WHERE username=%s", (username,))
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}
