"""Salary tracker - Flask + SQLite backend.

Run:  pip install flask  &&  python app.py
Open: http://127.0.0.1:5000
"""

import csv
import io
import os
import re
import sqlite3

from flask import Flask, Response, g, jsonify, render_template, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "salary.db")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

app = Flask(__name__)


# ---------- database ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS months (
                month  TEXT PRIMARY KEY,
                salary REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS items (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                month     TEXT NOT NULL,
                name      TEXT NOT NULL,
                category  TEXT NOT NULL DEFAULT 'Other',
                amount    REAL NOT NULL DEFAULT 0,
                recurring INTEGER NOT NULL DEFAULT 0,
                paid      INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_items_month ON items(month);
            """)


# ---------- helpers ----------
class BadRequest(Exception):
    pass


@app.errorhandler(BadRequest)
def handle_bad_request(err):
    return jsonify(error=str(err)), 400


def check_month(month):
    if not MONTH_RE.match(month):
        raise BadRequest("Month must look like 2026-09.")
    return month


def parse_amount(value):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise BadRequest("Amount must be a number.")
    if amount < 0 or amount > 1e12:
        raise BadRequest("Amount must be zero or more.")
    return round(amount, 2)


def clean_text(value, label, limit):
    text = str(value or "").strip()
    if not text:
        raise BadRequest(f"{label} is required.")
    return text[:limit]


def item_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "amount": row["amount"],
        "recurring": bool(row["recurring"]),
        "paid": bool(row["paid"]),
    }


def ensure_month(db, month):
    db.execute("INSERT OR IGNORE INTO months(month, salary) VALUES (?, 0)", (month,))


def previous_month(month):
    year, mon = map(int, month.split("-"))
    year, mon = (year - 1, 12) if mon == 1 else (year, mon - 1)
    return f"{year:04d}-{mon:02d}"


# ---------- pages ----------
@app.get("/")
def index():
    return render_template("index.html")


# ---------- API ----------
@app.get("/api/month/<month>")
def get_month(month):
    check_month(month)
    db = get_db()
    row = db.execute("SELECT salary FROM months WHERE month = ?", (month,)).fetchone()
    items = db.execute(
        "SELECT * FROM items WHERE month = ? ORDER BY id", (month,)
    ).fetchall()
    return jsonify(
        month=month,
        salary=row["salary"] if row else 0,
        items=[item_to_dict(i) for i in items],
    )


@app.put("/api/month/<month>")
def set_salary(month):
    check_month(month)
    salary = parse_amount((request.get_json(silent=True) or {}).get("salary"))
    db = get_db()
    ensure_month(db, month)
    db.execute("UPDATE months SET salary = ? WHERE month = ?", (salary, month))
    db.commit()
    return jsonify(month=month, salary=salary)


@app.post("/api/month/<month>/items")
def add_item(month):
    check_month(month)
    data = request.get_json(silent=True) or {}
    name = clean_text(data.get("name"), "Name", 80)
    category = clean_text(data.get("category") or "Other", "Category", 40)
    amount = parse_amount(data.get("amount"))
    recurring = 1 if data.get("recurring") else 0
    db = get_db()
    ensure_month(db, month)
    cur = db.execute(
        "INSERT INTO items(month, name, category, amount, recurring) VALUES (?,?,?,?,?)",
        (month, name, category, amount, recurring),
    )
    db.commit()
    row = db.execute("SELECT * FROM items WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(item_to_dict(row)), 201


@app.patch("/api/items/<int:item_id>")
def update_item(item_id):
    data = request.get_json(silent=True) or {}
    fields, values = [], []
    if "name" in data:
        fields.append("name = ?")
        values.append(clean_text(data["name"], "Name", 80))
    if "category" in data:
        fields.append("category = ?")
        values.append(clean_text(data["category"], "Category", 40))
    if "amount" in data:
        fields.append("amount = ?")
        values.append(parse_amount(data["amount"]))
    if "recurring" in data:
        fields.append("recurring = ?")
        values.append(1 if data["recurring"] else 0)
    if "paid" in data:
        fields.append("paid = ?")
        values.append(1 if data["paid"] else 0)
    if not fields:
        raise BadRequest("Nothing to update.")
    db = get_db()
    cur = db.execute(
        f"UPDATE items SET {', '.join(fields)} WHERE id = ?", (*values, item_id)
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify(error="Account not found."), 404
    row = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return jsonify(item_to_dict(row))


@app.delete("/api/items/<int:item_id>")
def delete_item(item_id):
    db = get_db()
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    return jsonify(deleted=item_id)


@app.post("/api/month/<month>/copy")
def copy_recurring(month):
    """Copy last month's recurring accounts (and salary, if not set yet) into this month."""
    check_month(month)
    prev = previous_month(month)
    db = get_db()
    ensure_month(db, month)

    current = db.execute(
        "SELECT salary FROM months WHERE month = ?", (month,)
    ).fetchone()
    last = db.execute("SELECT salary FROM months WHERE month = ?", (prev,)).fetchone()
    if current["salary"] == 0 and last and last["salary"] > 0:
        db.execute(
            "UPDATE months SET salary = ? WHERE month = ?", (last["salary"], month)
        )

    existing = {
        (r["name"].lower(), r["category"])
        for r in db.execute(
            "SELECT name, category FROM items WHERE month = ?", (month,)
        )
    }
    copied = 0
    for r in db.execute(
        "SELECT * FROM items WHERE month = ? AND recurring = 1 ORDER BY id", (prev,)
    ).fetchall():
        if (r["name"].lower(), r["category"]) in existing:
            continue
        db.execute(
            "INSERT INTO items(month, name, category, amount, recurring) VALUES (?,?,?,?,1)",
            (month, r["name"], r["category"], r["amount"]),
        )
        copied += 1
    db.commit()
    return jsonify(copied=copied, source=prev)


@app.get("/api/history")
def history():
    rows = get_db().execute("""
        SELECT m.month,
               m.salary,
               COALESCE(SUM(i.amount), 0) AS spent
        FROM months m
        LEFT JOIN items i ON i.month = m.month
        GROUP BY m.month
        ORDER BY m.month
        """).fetchall()
    return jsonify(
        [
            {
                "month": r["month"],
                "salary": r["salary"],
                "spent": round(r["spent"], 2),
                "left": round(r["salary"] - r["spent"], 2),
            }
            for r in rows
        ]
    )


@app.get("/api/export.csv")
def export_csv():
    rows = get_db().execute("""
        SELECT m.month, m.salary, i.name, i.category, i.amount, i.recurring, i.paid
        FROM months m LEFT JOIN items i ON i.month = m.month
        ORDER BY m.month, i.id
        """).fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["month", "salary", "account", "category", "amount", "monthly", "paid"]
    )
    for r in rows:
        writer.writerow(
            [
                r["month"],
                r["salary"],
                r["name"] or "",
                r["category"] or "",
                r["amount"] if r["name"] else "",
                "yes" if r["recurring"] else "no" if r["name"] else "",
                "yes" if r["paid"] else "no" if r["name"] else "",
            ]
        )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=salary-tracker.csv"},
    )


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
