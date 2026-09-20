"""Salary tracker - Flask + PostgreSQL (Supabase) backend, with per-user accounts.

Run:  pip install flask psycopg2-binary
      set DATABASE_URL and SECRET_KEY (see below)
      python app.py
Open: http://127.0.0.1:5000

Environment variables:
  DATABASE_URL  your Supabase / Postgres connection string
  SECRET_KEY    long random string used to sign the login cookie
                (generate one with: python -c "import secrets; print(secrets.token_hex(32))")
  COOKIE_SECURE set to 1 once the app is served over https

Every visitor signs up with an email + their own password. Each account only
ever sees its own salary and accounts.
"""

import csv
import io
import os
import re
import secrets
import time
from datetime import timedelta

import psycopg2
import psycopg2.errors
import psycopg2.extras

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = Flask(__name__)
# If SECRET_KEY isn't set, a random one is used, which logs everyone out on every restart.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)


# ---------- database ----------
def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL)
        g.db.autocommit = False
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with psycopg2.connect(DATABASE_URL) as con:
        with con.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            SERIAL PRIMARY KEY,
                    email         TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE TABLE IF NOT EXISTS months (
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    month   TEXT NOT NULL,
                    salary  REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS items (
                    id        SERIAL PRIMARY KEY,
                    user_id   INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    month     TEXT NOT NULL,
                    name      TEXT NOT NULL,
                    category  TEXT NOT NULL DEFAULT 'Other',
                    amount    REAL NOT NULL DEFAULT 0,
                    recurring INTEGER NOT NULL DEFAULT 0,
                    paid      INTEGER NOT NULL DEFAULT 0
                );

                -- upgrade tables that were created before accounts existed
                ALTER TABLE months ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
                ALTER TABLE items  ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
                ALTER TABLE months DROP CONSTRAINT IF EXISTS months_pkey;

                CREATE UNIQUE INDEX IF NOT EXISTS months_user_month ON months(user_id, month);
                CREATE INDEX IF NOT EXISTS idx_items_user_month ON items(user_id, month);
            """)
        con.commit()


# ---------- accounts & login ----------
MAX_TRIES = 5          # wrong passwords allowed...
LOCKOUT_SECONDS = 300  # ...before that IP is locked out for 5 minutes
failures = {}          # ip -> {"count": int, "locked_until": float}  (in memory)

# Used so a login for an unknown email takes as long as one for a real email.
DUMMY_HASH = generate_password_hash("not-a-real-password")


def client_ip():
    """Best guess at the real visitor IP.

    Behind Render's proxy, request.remote_addr is the proxy for everybody, which
    would make one shared lockout for all visitors. Render sits behind Cloudflare,
    which sets CF-Connecting-IP, so use that when it's there.
    """
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "unknown"


def seconds_locked(ip):
    entry = failures.get(ip)
    if not entry:
        return 0
    left = entry["locked_until"] - time.time()
    if left > 0:
        return int(left) + 1
    if entry["locked_until"]:  # lock has expired, start fresh
        failures.pop(ip, None)
    return 0


def record_failure(ip):
    entry = failures.setdefault(ip, {"count": 0, "locked_until": 0})
    entry["count"] += 1
    if entry["count"] >= MAX_TRIES:
        entry["locked_until"] = time.time() + LOCKOUT_SECONDS


def normalize_email(value):
    return (value or "").strip().lower()


def uid():
    """The logged-in user's id."""
    return session["user_id"]


def start_session(user_id):
    session.clear()
    session.permanent = True
    session["user_id"] = user_id


@app.before_request
def require_login():
    """Everything except login, signup and static files needs a logged-in session."""
    if request.endpoint in ("login", "signup", "static") or session.get("user_id"):
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Please log in again."), 401
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if session.get("user_id"):
        return redirect(url_for("index"))

    error, email = None, ""
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        if not EMAIL_RE.match(email) or len(email) > 254:
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif len(password) > 128:
            error = "Password can't be longer than 128 characters."
        elif password != confirm:
            error = "The two passwords don't match."
        else:
            db = get_db()
            try:
                with cursor(db) as cur:
                    cur.execute("SELECT COUNT(*) AS n FROM users")
                    first_user = cur.fetchone()["n"] == 0
                    cur.execute(
                        "INSERT INTO users(email, password_hash) VALUES (%s, %s) RETURNING id",
                        (email, generate_password_hash(password)),
                    )
                    user_id = cur.fetchone()["id"]
                    if first_user:
                        # data saved before accounts existed goes to the first account
                        cur.execute("UPDATE months SET user_id = %s WHERE user_id IS NULL", (user_id,))
                        cur.execute("UPDATE items SET user_id = %s WHERE user_id IS NULL", (user_id,))
                db.commit()
            except psycopg2.errors.UniqueViolation:
                db.rollback()
                error = "An account with that email already exists. Try logging in."
            else:
                start_session(user_id)
                return redirect(url_for("index"))

    return render_template("login.html", mode="signup", error=error, email=email), (400 if error else 200)


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("index"))

    error, email = None, ""
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        password = (request.form.get("password") or "")[:200]
        ip = client_ip()
        wait = seconds_locked(ip)
        if wait:
            error = f"Too many tries. Wait about {(wait + 59) // 60} min and try again."
        else:
            db = get_db()
            with cursor(db) as cur:
                cur.execute("SELECT id, password_hash FROM users WHERE email = %s", (email,))
                user = cur.fetchone()
            password_ok = check_password_hash(user["password_hash"] if user else DUMMY_HASH, password)
            if user and password_ok:
                failures.pop(ip, None)
                start_session(user["id"])
                return redirect(url_for("index"))
            record_failure(ip)
            time.sleep(0.5)  # slow down guessing a little
            error = "Wrong email or password."

    return render_template("login.html", mode="login", error=error, email=email), (401 if error else 200)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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


def cursor(db):
    """Return a dict cursor."""
    return db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def ensure_month(db, user_id, month):
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO months(user_id, month, salary) VALUES (%s, %s, 0) "
            "ON CONFLICT (user_id, month) DO NOTHING",
            (user_id, month),
        )


def previous_month(month):
    year, mon = map(int, month.split("-"))
    year, mon = (year - 1, 12) if mon == 1 else (year, mon - 1)
    return f"{year:04d}-{mon:02d}"


# ---------- pages ----------
@app.get("/")
def index():
    db = get_db()
    with cursor(db) as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (uid(),))
        row = cur.fetchone()
    if row is None:  # the account was deleted, so the cookie is stale
        session.clear()
        return redirect(url_for("login"))
    return render_template("index.html", email=row["email"])


# ---------- API ----------
@app.get("/api/month/<month>")
def get_month(month):
    check_month(month)
    db = get_db()
    with cursor(db) as cur:
        cur.execute("SELECT salary FROM months WHERE user_id = %s AND month = %s", (uid(), month))
        row = cur.fetchone()
        cur.execute(
            "SELECT * FROM items WHERE user_id = %s AND month = %s ORDER BY id", (uid(), month)
        )
        items = cur.fetchall()
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
    ensure_month(db, uid(), month)
    with cursor(db) as cur:
        cur.execute(
            "UPDATE months SET salary = %s WHERE user_id = %s AND month = %s",
            (salary, uid(), month),
        )
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
    ensure_month(db, uid(), month)
    with cursor(db) as cur:
        cur.execute(
            "INSERT INTO items(user_id, month, name, category, amount, recurring) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (uid(), month, name, category, amount, recurring),
        )
        row = cur.fetchone()
    db.commit()
    return jsonify(item_to_dict(row)), 201


@app.patch("/api/items/<int:item_id>")
def update_item(item_id):
    data = request.get_json(silent=True) or {}
    fields, values = [], []
    if "name" in data:
        fields.append("name = %s")
        values.append(clean_text(data["name"], "Name", 80))
    if "category" in data:
        fields.append("category = %s")
        values.append(clean_text(data["category"], "Category", 40))
    if "amount" in data:
        fields.append("amount = %s")
        values.append(parse_amount(data["amount"]))
    if "recurring" in data:
        fields.append("recurring = %s")
        values.append(1 if data["recurring"] else 0)
    if "paid" in data:
        fields.append("paid = %s")
        values.append(1 if data["paid"] else 0)
    if not fields:
        raise BadRequest("Nothing to update.")
    db = get_db()
    with cursor(db) as cur:
        cur.execute(
            f"UPDATE items SET {', '.join(fields)} WHERE id = %s AND user_id = %s RETURNING *",
            (*values, item_id, uid()),
        )
        row = cur.fetchone()
    db.commit()
    if row is None:
        return jsonify(error="Account not found."), 404
    return jsonify(item_to_dict(row))


@app.delete("/api/items/<int:item_id>")
def delete_item(item_id):
    db = get_db()
    with cursor(db) as cur:
        cur.execute("DELETE FROM items WHERE id = %s AND user_id = %s", (item_id, uid()))
    db.commit()
    return jsonify(deleted=item_id)


@app.post("/api/month/<month>/copy")
def copy_recurring(month):
    """Copy last month's recurring accounts (and salary, if not set yet) into this month."""
    check_month(month)
    prev = previous_month(month)
    db = get_db()
    ensure_month(db, uid(), month)

    with cursor(db) as cur:
        cur.execute("SELECT salary FROM months WHERE user_id = %s AND month = %s", (uid(), month))
        current = cur.fetchone()
        cur.execute("SELECT salary FROM months WHERE user_id = %s AND month = %s", (uid(), prev))
        last = cur.fetchone()

        if current["salary"] == 0 and last and last["salary"] > 0:
            cur.execute(
                "UPDATE months SET salary = %s WHERE user_id = %s AND month = %s",
                (last["salary"], uid(), month),
            )

        cur.execute("SELECT name, category FROM items WHERE user_id = %s AND month = %s", (uid(), month))
        existing = {(r["name"].lower(), r["category"]) for r in cur.fetchall()}

        cur.execute(
            "SELECT * FROM items WHERE user_id = %s AND month = %s AND recurring = 1 ORDER BY id",
            (uid(), prev),
        )
        prev_items = cur.fetchall()

        copied = 0
        for r in prev_items:
            if (r["name"].lower(), r["category"]) in existing:
                continue
            cur.execute(
                "INSERT INTO items(user_id, month, name, category, amount, recurring) "
                "VALUES (%s,%s,%s,%s,%s,1)",
                (uid(), month, r["name"], r["category"], r["amount"]),
            )
            copied += 1

    db.commit()
    return jsonify(copied=copied, source=prev)


@app.get("/api/history")
def history():
    db = get_db()
    with cursor(db) as cur:
        cur.execute("""
            SELECT m.month,
                   m.salary,
                   COALESCE(SUM(i.amount), 0) AS spent
            FROM months m
            LEFT JOIN items i ON i.month = m.month AND i.user_id = m.user_id
            WHERE m.user_id = %s
            GROUP BY m.month, m.salary
            ORDER BY m.month
        """, (uid(),))
        rows = cur.fetchall()
    return jsonify([
        {
            "month": r["month"],
            "salary": r["salary"],
            "spent": round(r["spent"], 2),
            "left": round(r["salary"] - r["spent"], 2),
        }
        for r in rows
    ])


@app.get("/api/export.csv")
def export_csv():
    db = get_db()
    with cursor(db) as cur:
        cur.execute("""
            SELECT m.month, m.salary, i.name, i.category, i.amount, i.recurring, i.paid
            FROM months m
            LEFT JOIN items i ON i.month = m.month AND i.user_id = m.user_id
            WHERE m.user_id = %s
            ORDER BY m.month, i.id
        """, (uid(),))
        rows = cur.fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["month", "salary", "account", "category", "amount", "monthly", "paid"])
    for r in rows:
        writer.writerow([
            r["month"],
            r["salary"],
            r["name"] or "",
            r["category"] or "",
            r["amount"] if r["name"] else "",
            "yes" if r["recurring"] else "no" if r["name"] else "",
            "yes" if r["paid"] else "no" if r["name"] else "",
        ])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=salary-tracker.csv"},
    )


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
