from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta
import json
import os
import math
import bcrypt


# =====================
# Basic setup
# =====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

USERS_FILE = os.path.join(BASE_DIR, "users.json")
OT_FILE = os.path.join(BASE_DIR, "ot_data.json")

# =====================
# Utility
# =====================
def load(file, default):
    if not os.path.exists(file):
        return default
    with open(file, "r", encoding="utf-8") as f:
        return json.load(f)

def save(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def round_half_down(hours):
    return math.floor(hours * 2) / 2

def t(hm):
    return datetime.strptime(hm, "%H:%M")

# =====================
# OT CALCULATION (แก้วันหยุดให้ถูก)
# =====================
def calculate_ot(date_str, out_str, start_str=None, day_type="weekday"):
    out_dt = t(out_str)

    ot1 = ot15 = ot3 = 0.0

    # ---------- จันทร์ – ศุกร์ ----------
    if day_type == "weekday":
        ot_start = t("17:20")
        if out_dt > ot_start:
            diff = (out_dt - ot_start).total_seconds() / 3600
            ot15 = round_half_down(diff)
        return ot1, ot15, ot3

    # ---------- เสาร์ / อาทิตย์ / นักขัตฤกษ์ ----------
    if not start_str:
        return 0, 0, 0

    start_dt = t(start_str)

    total_work = (out_dt - start_dt).total_seconds() / 3600
    if total_work <= 0:
        return 0, 0, 0

    # พัก 1 ชั่วโมง (ถ้าทำงาน >= 6 ชม.)
    if total_work >= 6:
        total_work -= 1

    # ---- OT1 (สูงสุด 8 ชม) ----
    ot1 = min(8, round_half_down(total_work))

    # ---- OT3 (หลัง OT1 + พัก 20 นาที) ----
    if total_work > 8:
        after_8 = total_work - 8

        # พัก 20 นาที = 0.333 ชม
        after_8 -= (20 / 60)

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
    users = load(USERS_FILE, {})

    if data.get("username") not in users or users[data["username"]]["password"] != data.get("password"):
        raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

    return {"success": True}

@app.post("/register")
async def register(req: Request):
    data = await req.json()
    users = load(USERS_FILE, {})

    if data["username"] in users:
        raise HTTPException(status_code=400, detail="ผู้ใช้นี้มีอยู่แล้ว")

    users[data["username"]] = {"password": data["password"]}
    save(USERS_FILE, users)
    return {"success": True}

# =====================
# Save OT (insert / update)
# =====================
@app.post("/save_ot")
async def save_ot(req: Request):
    data = await req.json()

    for k in ["username", "date", "out", "day"]:
        if k not in data:
            raise HTTPException(status_code=400, detail="ข้อมูลไม่ครบ")

    if data["day"] == "weekend" and "in" not in data:
        raise HTTPException(status_code=400, detail="ข้อมูลไม่ครบ")

    ot1, ot15, ot3 = calculate_ot(
        data["date"],
        data["out"],
        data.get("in"),
        "weekday" if data["day"] == "weekday" else "holiday"
    )

    record = {
        "username": data["username"],
        "date": data["date"],
        "day_type": data["day"],
        "start": data.get("in"),
        "out": data["out"],
        "ot1": ot1,
        "ot15": ot15,
        "ot3": ot3,
        "ts": datetime.now().isoformat()
    }

    ot = load(OT_FILE, [])

    for i, r in enumerate(ot):
        if r["username"] == record["username"] and r["date"] == record["date"]:
            ot[i] = record
            save(OT_FILE, ot)
            return {"success": True, "mode": "update"}

    ot.append(record)
    save(OT_FILE, ot)
    return {"success": True, "mode": "insert"}

# =====================
# Summary
# =====================
@app.get("/summary/{username}/{month}")
def summary(username: str, month: str):
    ot = load(OT_FILE, [])
    rows = []
    total = {"ot1": 0, "ot15": 0, "ot3": 0}

    for r in ot:
        if r["username"] != username:
            continue
        if not r["date"].startswith(month):
            continue

        rows.append({
            "date": r["date"],
            "start": r.get("start"),
            "out": r["out"],
            "ot1": r["ot1"],
            "ot15": r["ot15"],
            "ot3": r["ot3"]
        })

        total["ot1"] += r["ot1"]
        total["ot15"] += r["ot15"]
        total["ot3"] += r["ot3"]

    return {"rows": rows, "total": total}

# =====================
# Delete all OT
# =====================
@app.delete("/delete_all/{username}")
def delete_all(username: str):
    ot = load(OT_FILE, [])
    ot = [r for r in ot if r["username"] != username]
    save(OT_FILE, ot)
    return {"success": True}
