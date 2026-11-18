#!/usr/bin/env python3
"""
AI-Enhanced Personal Finance Tracker - backend.

Pure standard library (http.server + sqlite3 + urllib), so there is nothing to install.
Local AI is provided by Ollama (default model: gemma2).

Run:
    python server.py            # http://localhost:8000
    python server.py --seed     # also insert demo data if the DB is empty
Env vars: PORT, DB_PATH, OLLAMA_URL, OLLAMA_MODEL
"""
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).parent
DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "finance.db"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma2")
PORT = int(os.environ.get("PORT", "8000"))

CATEGORIES = [
    "Salary", "Freelance", "Investments", "Other Income",
    "Food & Dining", "Groceries", "Rent & Housing", "Utilities", "Transport",
    "Shopping", "Entertainment", "Health", "Education", "Travel", "Subscriptions", "Other",
]

KEYWORDS = {
    "Food & Dining": ["restaurant", "cafe", "coffee", "swiggy", "zomato", "pizza", "burger", "dinner", "lunch", "starbucks"],
    "Groceries": ["grocery", "supermarket", "bigbasket", "blinkit", "zepto", "vegetables", "dmart", "milk"],
    "Rent & Housing": ["rent", "maintenance", "mortgage", "housing"],
    "Utilities": ["electricity", "water bill", "internet", "broadband", "wifi", "gas", "phone bill", "recharge"],
    "Transport": ["uber", "ola", "fuel", "petrol", "diesel", "metro", "bus", "taxi", "cab", "parking"],
    "Shopping": ["amazon", "flipkart", "myntra", "clothes", "shoes", "mall"],
    "Entertainment": ["movie", "cinema", "netflix", "concert", "game", "bookmyshow"],
    "Health": ["doctor", "pharmacy", "medicine", "hospital", "gym", "clinic"],
    "Education": ["course", "udemy", "tuition", "books", "college", "fees"],
    "Travel": ["flight", "hotel", "airbnb", "train", "irctc", "trip"],
    "Subscriptions": ["subscription", "spotify", "prime", "youtube premium", "icloud", "chatgpt"],
    "Salary": ["salary", "payroll", "paycheck"],
    "Freelance": ["freelance", "client payment", "invoice"],
    "Investments": ["dividend", "interest", "mutual fund", "stocks", "sip"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('income','expense')),
    amount REAL NOT NULL CHECK (amount > 0),
    category TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL CHECK (monthly_limit >= 0)
);
"""


# ---------------------------------------------------------------- database
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript(SCHEMA)


def seed():
    with db() as c:
        if c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]:
            return
        today = date.today()
        rows = []
        for m in range(3):
            first = (today.replace(day=1) - timedelta(days=30 * m)).replace(day=1)
            d = lambda day: (first + timedelta(days=day - 1)).isoformat()
            rows += [
                ("income", 85000, "Salary", "Monthly salary", d(1)),
                ("income", 12000 if m % 2 == 0 else 6000, "Freelance", "Freelance client invoice", d(12)),
                ("expense", 22000, "Rent & Housing", "Rent", d(2)),
                ("expense", 3200 + m * 150, "Utilities", "Electricity and broadband", d(5)),
                ("expense", 6800 + m * 300, "Groceries", "BigBasket groceries", d(8)),
                ("expense", 4200 - m * 200, "Food & Dining", "Swiggy and restaurants", d(10)),
                ("expense", 2500, "Transport", "Uber and fuel", d(14)),
                ("expense", 5600, "Shopping", "Amazon order", d(17)),
                ("expense", 1499, "Subscriptions", "Netflix, Spotify", d(19)),
                ("expense", 1800, "Entertainment", "Movie night", d(21)),
                ("expense", 2200, "Health", "Gym membership", d(23)),
            ]
        c.executemany(
            "INSERT INTO transactions(type, amount, category, description, date) VALUES (?,?,?,?,?)", rows)
        c.executemany("INSERT OR REPLACE INTO budgets VALUES (?,?)", [
            ("Food & Dining", 5000), ("Groceries", 8000), ("Transport", 3000),
            ("Shopping", 5000), ("Entertainment", 2000), ("Utilities", 3500), ("Subscriptions", 1500),
        ])


# ---------------------------------------------------------------- analytics
def valid_month(m):
    return m if m and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m) else date.today().strftime("%Y-%m")


def month_shift(month, delta):
    y, mo = map(int, month.split("-"))
    idx = y * 12 + (mo - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def build_summary(month):
    month = valid_month(month)
    with db() as c:
        totals = {r["type"]: r["t"] for r in c.execute(
            "SELECT type, SUM(amount) t FROM transactions WHERE substr(date,1,7)=? GROUP BY type", (month,))}
        income, expense = totals.get("income", 0) or 0, totals.get("expense", 0) or 0
        by_cat = [dict(r) for r in c.execute(
            "SELECT category, SUM(amount) total, COUNT(*) count FROM transactions "
            "WHERE type='expense' AND substr(date,1,7)=? GROUP BY category ORDER BY total DESC", (month,))]
        budgets = {r["category"]: r["monthly_limit"] for r in c.execute("SELECT * FROM budgets")}
        spent = {r["category"]: r["total"] for r in by_cat}
        budget_status = []
        for cat, limit in sorted(budgets.items()):
            s = spent.get(cat, 0)
            budget_status.append({
                "category": cat, "limit": limit, "spent": s,
                "remaining": limit - s, "pct": round(s / limit * 100, 1) if limit else 0,
                "status": "over" if s > limit else ("warning" if limit and s / limit >= 0.8 else "ok"),
            })
        trend = []
        for i in range(5, -1, -1):
            m = month_shift(month, -i)
            t = {r["type"]: r["t"] for r in c.execute(
                "SELECT type, SUM(amount) t FROM transactions WHERE substr(date,1,7)=? GROUP BY type", (m,))}
            trend.append({"month": m, "income": t.get("income", 0) or 0, "expense": t.get("expense", 0) or 0})
        biggest = [dict(r) for r in c.execute(
            "SELECT description, category, amount, date FROM transactions "
            "WHERE type='expense' AND substr(date,1,7)=? ORDER BY amount DESC LIMIT 5", (month,))]
    prev = trend[-2]["expense"] if len(trend) > 1 else 0
    return {
        "month": month, "income": income, "expense": expense, "balance": income - expense,
        "savings_rate": round((income - expense) / income * 100, 1) if income else 0,
        "expense_change_vs_prev_pct": round((expense - prev) / prev * 100, 1) if prev else None,
        "by_category": by_cat, "budgets": budget_status, "trend": trend, "biggest_expenses": biggest,
    }


# ---------------------------------------------------------------- AI (Ollama)
def ollama_generate(prompt, timeout=120, fmt=None):
    body = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.3}}
    if fmt:
        body["format"] = fmt
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["response"].strip()


def ollama_status():
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as resp:
            models = [m["name"] for m in json.loads(resp.read()).get("models", [])]
        has_model = any(m.split(":")[0] == OLLAMA_MODEL.split(":")[0] for m in models)
        return {"online": True, "model": OLLAMA_MODEL, "model_installed": has_model, "models": models}
    except Exception:
        return {"online": False, "model": OLLAMA_MODEL, "model_installed": False, "models": []}


def rule_categorize(description, kind="expense"):
    text = description.lower()
    for cat, words in KEYWORDS.items():
        is_income = cat in ("Salary", "Freelance", "Investments")
        if is_income != (kind == "income"):
            continue
        if any(w in text for w in words):
            return cat
    return "Other Income" if kind == "income" else "Other"


def ai_categorize(description, kind="expense"):
    rule = rule_categorize(description, kind)
    if rule not in ("Other", "Other Income"):
        return {"category": rule, "source": "rules"}
    options = [c for c in CATEGORIES if (c in ("Salary", "Freelance", "Investments", "Other Income")) == (kind == "income")]
    prompt = (f"Classify this {kind} transaction into exactly one of these categories: {', '.join(options)}.\n"
              f"Transaction: \"{description}\"\nAnswer with only the category name.")
    try:
        answer = ollama_generate(prompt, timeout=30)
        for c in options:
            if c.lower() in answer.lower():
                return {"category": c, "source": "ai"}
    except Exception:
        pass
    return {"category": rule, "source": "rules"}


def build_insight_prompt(summary, question=None):
    data = {
        "month": summary["month"], "income": summary["income"], "expenses": summary["expense"],
        "savings_rate_pct": summary["savings_rate"],
        "expense_change_vs_last_month_pct": summary["expense_change_vs_prev_pct"],
        "spending_by_category": {c["category"]: c["total"] for c in summary["by_category"]},
        "budgets": [{"category": b["category"], "limit": b["limit"], "spent": b["spent"], "status": b["status"]}
                    for b in summary["budgets"]],
        "last_6_months": summary["trend"],
    }
    task = (f"Answer this question from the user using only the data: {question}" if question else
            "Give: 1) a two-sentence overview, 2) three specific observations about spending patterns, "
            "3) three concrete, actionable suggestions to improve savings or stay within budget. "
            "Use short bullet points.")
    return ("You are a careful personal finance assistant. Use ONLY the JSON data below; never invent numbers. "
            "Amounts are in the user's local currency. Be concise and practical. This is general guidance, "
            "not professional financial advice.\n\n"
            f"DATA:\n{json.dumps(data, indent=2)}\n\nTASK: {task}")


def fallback_insights(s):
    """Rule-based insights used when Ollama is unreachable."""
    out = []
    if s["income"]:
        out.append(f"Savings rate this month is {s['savings_rate']}%"
                   + (" - healthy (20%+ is a common target)." if s["savings_rate"] >= 20 else " - below the common 20% target."))
    if s["by_category"]:
        top = s["by_category"][0]
        share = top["total"] / s["expense"] * 100 if s["expense"] else 0
        out.append(f"Biggest spending category is {top['category']} ({share:.0f}% of expenses).")
    for b in s["budgets"]:
        if b["status"] == "over":
            out.append(f"Over budget on {b['category']} by {abs(b['remaining']):.0f}.")
        elif b["status"] == "warning":
            out.append(f"{b['category']} budget is {b['pct']}% used.")
    ch = s["expense_change_vs_prev_pct"]
    if ch is not None:
        out.append(f"Spending is {'up' if ch > 0 else 'down'} {abs(ch)}% vs last month.")
    return "\n".join("- " + o for o in out) or "Add some transactions to see insights."


# ---------------------------------------------------------------- validation
def clean_tx(data):
    kind = data.get("type")
    if kind not in ("income", "expense"):
        raise ValueError("type must be 'income' or 'expense'")
    try:
        amount = round(float(data.get("amount")), 2)
    except (TypeError, ValueError):
        raise ValueError("amount must be a number")
    if amount <= 0:
        raise ValueError("amount must be greater than 0")
    desc = str(data.get("description", "")).strip()[:200]
    category = str(data.get("category") or "").strip() or rule_categorize(desc, kind)
    try:
        d = datetime.strptime(str(data.get("date")), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD")
    return kind, amount, category, desc, d


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, self.path))

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            raise ValueError("invalid JSON body")

    def route(self, method):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        parts = [p for p in url.path.split("/") if p]

        if method == "GET" and not parts:
            return self.serve_file("index.html")
        if parts[:1] != ["api"]:
            if method == "GET":
                return self.serve_file("/".join(parts))
            return self.send_json({"error": "not found"}, 404)

        res = parts[1] if len(parts) > 1 else ""
        if res == "transactions":
            return self.transactions(method, parts, q)
        if res == "budgets":
            return self.budgets(method)
        if res == "summary" and method == "GET":
            return self.send_json(build_summary(q.get("month")))
        if res == "categories" and method == "GET":
            return self.send_json(CATEGORIES)
        if res == "ai" and len(parts) > 2:
            return self.ai(method, parts[2])
        self.send_json({"error": "not found"}, 404)

    def transactions(self, method, parts, q):
        with db() as c:
            if method == "GET" and len(parts) == 2:
                month = valid_month(q.get("month"))
                sql, args = "SELECT * FROM transactions WHERE substr(date,1,7)=?", [month]
                if q.get("category"):
                    sql += " AND category=?"
                    args.append(q["category"])
                if q.get("type") in ("income", "expense"):
                    sql += " AND type=?"
                    args.append(q["type"])
                sql += " ORDER BY date DESC, id DESC"
                return self.send_json([dict(r) for r in c.execute(sql, args)])
            if method == "POST" and len(parts) == 2:
                cur = c.execute("INSERT INTO transactions(type, amount, category, description, date) VALUES (?,?,?,?,?)",
                                clean_tx(self.read_json()))
                row = c.execute("SELECT * FROM transactions WHERE id=?", (cur.lastrowid,)).fetchone()
                return self.send_json(dict(row), 201)
            if len(parts) == 3 and parts[2].isdigit():
                tx_id = int(parts[2])
                if method == "PUT":
                    cur = c.execute("UPDATE transactions SET type=?, amount=?, category=?, description=?, date=? WHERE id=?",
                                    (*clean_tx(self.read_json()), tx_id))
                    if not cur.rowcount:
                        return self.send_json({"error": "not found"}, 404)
                    return self.send_json(dict(c.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()))
                if method == "DELETE":
                    cur = c.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
                    return self.send_json({"deleted": bool(cur.rowcount)}, 200 if cur.rowcount else 404)
        self.send_json({"error": "not found"}, 404)

    def budgets(self, method):
        with db() as c:
            if method == "GET":
                return self.send_json([dict(r) for r in c.execute("SELECT * FROM budgets ORDER BY category")])
            data = self.read_json()
            cat = str(data.get("category", "")).strip()
            if not cat:
                raise ValueError("category required")
            if method == "PUT":
                limit = float(data.get("monthly_limit"))
                if limit < 0:
                    raise ValueError("monthly_limit must be >= 0")
                c.execute("INSERT OR REPLACE INTO budgets VALUES (?,?)", (cat, limit))
                return self.send_json({"category": cat, "monthly_limit": limit})
            if method == "DELETE":
                c.execute("DELETE FROM budgets WHERE category=?", (cat,))
                return self.send_json({"deleted": True})
        self.send_json({"error": "not found"}, 404)

    def ai(self, method, action):
        if action == "status" and method == "GET":
            return self.send_json(ollama_status())
        if action == "categorize" and method == "POST":
            d = self.read_json()
            return self.send_json(ai_categorize(str(d.get("description", "")), d.get("type", "expense")))
        if action == "insights" and method == "POST":
            d = self.read_json()
            summary = build_summary(d.get("month"))
            question = str(d.get("question", "")).strip()[:500] or None
            try:
                text = ollama_generate(build_insight_prompt(summary, question))
                return self.send_json({"insights": text, "source": "ai", "model": OLLAMA_MODEL})
            except (urllib.error.URLError, TimeoutError, OSError, KeyError) as e:
                return self.send_json({
                    "insights": fallback_insights(summary), "source": "rules",
                    "warning": f"Ollama unavailable ({e}). Start it with 'ollama serve' and run 'ollama pull {OLLAMA_MODEL}'."})
        self.send_json({"error": "not found"}, 404)

    def serve_file(self, name):
        static = (BASE_DIR / "static").resolve()
        path = (static / name).resolve()
        if static not in path.parents and path != static or not path.is_file():
            return self.send_json({"error": "not found"}, 404)
        ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(path.suffix, "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self, method):
        try:
            self.route(method)
        except (ValueError, sqlite3.Error) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:  # pragma: no cover
            self.send_json({"error": f"server error: {e}"}, 500)

    do_GET = lambda self: self.handle_any("GET")
    do_POST = lambda self: self.handle_any("POST")
    do_PUT = lambda self: self.handle_any("PUT")
    do_DELETE = lambda self: self.handle_any("DELETE")


if __name__ == "__main__":
    init_db()
    if "--seed" in sys.argv:
        seed()
    print(f"Finance tracker running at http://localhost:{PORT}  (AI model: {OLLAMA_MODEL} @ {OLLAMA_URL})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
