"""Smart Grocery Inventory Manager - Flask REST backend (SRM IST, 21CSC203P)."""
import csv, io, os, random, sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

import pandas as pd
from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

import analytics

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("GROCERY_DB", os.path.join(BASE, "grocery.db"))
EXPIRY_WARN_DAYS = 7
TXN_TYPES = ("sale", "purchase", "return", "adjustment")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('owner','staff')));
CREATE TABLE IF NOT EXISTS suppliers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, phone TEXT, email TEXT);
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  category TEXT NOT NULL, price REAL NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
  reorder_level INTEGER NOT NULL DEFAULT 10, expiry_date TEXT,
  supplier_id INTEGER REFERENCES suppliers(id));
CREATE TABLE IF NOT EXISTS transactions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id),
  txn_type TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price REAL NOT NULL,
  txn_date TEXT NOT NULL, note TEXT, user TEXT);
CREATE TABLE IF NOT EXISTS alert_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL REFERENCES products(id),
  alert_type TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL, resolved INTEGER DEFAULT 0);
"""


# ---------- database helpers ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def rows(sql, args=()):
    return [dict(r) for r in db().execute(sql, args).fetchall()]


PRODUCT_SQL = """SELECT p.*, s.name AS supplier FROM products p
                 LEFT JOIN suppliers s ON s.id = p.supplier_id"""


def init_db(seed=True):
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    if seed and conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        seed_data(conn)
    conn.commit()
    conn.close()


def seed_data(conn):
    rnd = random.Random(42)
    today = date.today()
    conn.executemany("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", [
        ("admin", generate_password_hash("admin123"), "owner"),
        ("staff", generate_password_hash("staff123"), "staff")])
    conn.executemany("INSERT INTO suppliers(name,phone,email) VALUES(?,?,?)", [
        ("Fresh Farms Co.", "9840011223", "orders@freshfarms.in"),
        ("Daily Dairy Ltd.", "9840022334", "sales@dailydairy.in"),
        ("Bharat Staples", "9840033445", "supply@bharatstaples.in"),
        ("SnackHub Distributors", "9840044556", "hello@snackhub.in")])
    # name, category, price, qty, reorder, expiry offset(days), supplier, popularity/day
    items = [
        ("Milk 1L", "Dairy", 28, 14, 20, 3, 2, 14), ("Curd 500g", "Dairy", 35, 40, 15, 6, 2, 6),
        ("Butter 100g", "Dairy", 58, 25, 10, 40, 2, 2), ("Tomato 1kg", "Produce", 40, 8, 15, 2, 1, 9),
        ("Onion 1kg", "Produce", 35, 60, 20, 20, 1, 8), ("Banana (dozen)", "Produce", 60, 30, 10, -1, 1, 3),
        ("Basmati Rice 5kg", "Staples", 520, 22, 8, 300, 3, 2), ("Toor Dal 1kg", "Staples", 150, 5, 10, 240, 3, 3),
        ("Sunflower Oil 1L", "Staples", 140, 45, 12, 180, 3, 4), ("Wheat Flour 5kg", "Staples", 240, 18, 8, 120, 3, 2),
        ("Potato Chips", "Snacks", 20, 80, 25, 90, 4, 10), ("Biscuits Pack", "Snacks", 30, 55, 20, 5, 4, 7),
        ("Instant Noodles", "Snacks", 14, 12, 20, 150, 4, 8), ("Soft Drink 750ml", "Beverages", 40, 35, 15, 100, 4, 5),
        ("Tea Powder 250g", "Beverages", 120, 28, 10, 200, 3, 1), ("Bread Loaf", "Bakery", 40, 9, 12, 2, 2, 7),
        ("Honey 500g", "Staples", 260, 20, 5, 400, 3, 0)]
    for i, (n, c, pr, q, rl, exp, sup, pop) in enumerate(items, 1):
        conn.execute("INSERT INTO products(sku,name,category,price,quantity,reorder_level,expiry_date,supplier_id)"
                     " VALUES(?,?,?,?,?,?,?,?)",
                     (f"SKU{i:03d}", n, c, pr, q, rl, (today + timedelta(days=exp)).isoformat(), sup))
        for d in range(60, 0, -1):  # 60 days of sales history, slightly trending
            trend = 1 + (60 - d) / 200
            units = max(0, round(rnd.gauss(pop * trend, max(1, pop * 0.3)))) if pop else 0
            if units:
                conn.execute("INSERT INTO transactions(product_id,txn_type,quantity,unit_price,txn_date,note,user)"
                             " VALUES(?,?,?,?,?,?,?)",
                             (i, "sale", units, pr, (today - timedelta(days=d)).isoformat(), "seed", "system"))


# ---------- auth ----------
def login_required(owner_only=False):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if "user" not in session:
                return jsonify(error="Login required"), 401
            if owner_only and session.get("role") != "owner":
                return jsonify(error="Owner access required"), 403
            return fn(*a, **kw)
        return wrapper
    return deco


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    u = db().execute("SELECT * FROM users WHERE username=?", (str(d.get("username", "")).strip(),)).fetchone()
    if not u or not check_password_hash(u["password_hash"], str(d.get("password", ""))):
        return jsonify(error="Invalid username or password"), 401
    session["user"], session["role"] = u["username"], u["role"]
    return jsonify(user=u["username"], role=u["role"])


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    if "user" not in session:
        return jsonify(error="Not logged in"), 401
    return jsonify(user=session["user"], role=session["role"])


# ---------- validation ----------
def parse_product(d, partial=False):
    """Type / range / format checks. Returns (clean_dict, error)."""
    out = {}
    try:
        if not partial or "name" in d:
            out["name"] = str(d.get("name", "")).strip()
            if not out["name"]: return None, "Product name is required"
        if not partial or "sku" in d:
            out["sku"] = str(d.get("sku", "")).strip().upper()
            if not out["sku"]: return None, "SKU is required"
        if not partial or "category" in d:
            out["category"] = str(d.get("category", "")).strip() or "General"
        if not partial or "price" in d:
            out["price"] = float(d.get("price"))
            if out["price"] < 0: return None, "Price cannot be negative"
        if not partial or "quantity" in d:
            out["quantity"] = int(d.get("quantity", 0))
            if out["quantity"] < 0: return None, "Quantity cannot be negative"
        if not partial or "reorder_level" in d:
            out["reorder_level"] = int(d.get("reorder_level", 10))
            if out["reorder_level"] < 0: return None, "Reorder level cannot be negative"
        if not partial or "expiry_date" in d:
            e = d.get("expiry_date") or None
            if e: datetime.strptime(e, "%Y-%m-%d")
            out["expiry_date"] = e
        if not partial or "supplier_id" in d:
            out["supplier_id"] = int(d["supplier_id"]) if d.get("supplier_id") else None
    except (TypeError, ValueError):
        return None, "Invalid value: check numbers and use YYYY-MM-DD for dates"
    return out, None


# ---------- products ----------
@app.get("/api/products")
@login_required()
def list_products():
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "").strip()
    sql, args = PRODUCT_SQL + " WHERE 1=1", []
    if q:
        sql += " AND (p.name LIKE ? OR p.sku LIKE ?)"; args += [f"%{q}%"] * 2
    if cat:
        sql += " AND p.category = ?"; args.append(cat)
    return jsonify(rows(sql + " ORDER BY p.name", args))


@app.post("/api/products")
@login_required()
def add_product():
    p, err = parse_product(request.get_json(silent=True) or {})
    if err: return jsonify(error=err), 400
    if db().execute("SELECT 1 FROM products WHERE sku=?", (p["sku"],)).fetchone():
        return jsonify(error=f"SKU {p['sku']} already exists"), 409
    cols = ",".join(p)
    cur = db().execute(f"INSERT INTO products({cols}) VALUES({','.join('?' * len(p))})", list(p.values()))
    db().commit(); refresh_alerts()
    return jsonify(id=cur.lastrowid), 201


@app.put("/api/products/<int:pid>")
@login_required()
def edit_product(pid):
    p, err = parse_product(request.get_json(silent=True) or {}, partial=True)
    if err: return jsonify(error=err), 400
    if not p: return jsonify(error="Nothing to update"), 400
    if "sku" in p and db().execute("SELECT 1 FROM products WHERE sku=? AND id<>?", (p["sku"], pid)).fetchone():
        return jsonify(error=f"SKU {p['sku']} already exists"), 409
    sets = ",".join(f"{k}=?" for k in p)
    cur = db().execute(f"UPDATE products SET {sets} WHERE id=?", list(p.values()) + [pid])
    db().commit(); refresh_alerts()
    return (jsonify(ok=True) if cur.rowcount else (jsonify(error="Product not found"), 404))


@app.delete("/api/products/<int:pid>")
@login_required(owner_only=True)
def delete_product(pid):
    db().execute("DELETE FROM alert_log WHERE product_id=?", (pid,))
    db().execute("DELETE FROM transactions WHERE product_id=?", (pid,))
    cur = db().execute("DELETE FROM products WHERE id=?", (pid,))
    db().commit()
    return (jsonify(ok=True) if cur.rowcount else (jsonify(error="Product not found"), 404))


@app.get("/api/suppliers")
@login_required()
def list_suppliers():
    return jsonify(rows("SELECT * FROM suppliers ORDER BY name"))


@app.post("/api/suppliers")
@login_required()
def add_supplier():
    d = request.get_json(silent=True) or {}
    name = str(d.get("name", "")).strip()
    if not name: return jsonify(error="Supplier name is required"), 400
    try:
        cur = db().execute("INSERT INTO suppliers(name,phone,email) VALUES(?,?,?)",
                           (name, d.get("phone"), d.get("email")))
    except sqlite3.IntegrityError:
        return jsonify(error="Supplier already exists"), 409
    db().commit()
    return jsonify(id=cur.lastrowid), 201


# ---------- stock transactions ----------
@app.post("/api/transactions")
@login_required()
def add_transaction():
    d = request.get_json(silent=True) or {}
    try:
        pid, qty = int(d.get("product_id")), int(d.get("quantity"))
    except (TypeError, ValueError):
        return jsonify(error="Product and whole-number quantity are required"), 400
    ttype = d.get("txn_type")
    if ttype not in TXN_TYPES: return jsonify(error="Invalid transaction type"), 400
    if qty == 0 or (ttype != "adjustment" and qty < 0):
        return jsonify(error="Quantity must be positive (adjustments may be negative)"), 400
    prod = db().execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not prod: return jsonify(error="Product not found"), 404
    delta = -qty if ttype == "sale" else qty
    if prod["quantity"] + delta < 0:
        return jsonify(error=f"Insufficient stock: only {prod['quantity']} in stock"), 400
    expiry = d.get("expiry_date") or None
    if expiry:
        try: datetime.strptime(expiry, "%Y-%m-%d")
        except ValueError: return jsonify(error="Expiry must be YYYY-MM-DD"), 400
    db().execute("UPDATE products SET quantity=quantity+? WHERE id=?", (delta, pid))
    if expiry and ttype == "purchase":  # new batch arrived
        db().execute("UPDATE products SET expiry_date=? WHERE id=?", (expiry, pid))
    db().execute("INSERT INTO transactions(product_id,txn_type,quantity,unit_price,txn_date,note,user)"
                 " VALUES(?,?,?,?,?,?,?)",
                 (pid, ttype, abs(qty), prod["price"], date.today().isoformat(), d.get("note"), session["user"]))
    db().commit()
    refresh_alerts()
    return jsonify(ok=True, new_quantity=prod["quantity"] + delta), 201


@app.get("/api/transactions")
@login_required()
def list_transactions():
    limit = min(int(request.args.get("limit", 50)), 500)
    return jsonify(rows("""SELECT t.*, p.name AS product FROM transactions t
                           JOIN products p ON p.id=t.product_id ORDER BY t.id DESC LIMIT ?""", (limit,)))


# ---------- alert engine ----------
def refresh_alerts():
    """Scan stock vs reorder level and expiry dates; write new alerts, resolve cleared ones."""
    today = date.today()
    current = {}
    for p in rows("SELECT * FROM products"):
        if p["quantity"] == 0:
            current[(p["id"], "out_of_stock")] = f"{p['name']} is out of stock"
        elif p["quantity"] <= p["reorder_level"]:
            current[(p["id"], "low_stock")] = (
                f"{p['name']} is low: {p['quantity']} left (reorder level {p['reorder_level']})")
        if p["expiry_date"]:
            days = (datetime.strptime(p["expiry_date"], "%Y-%m-%d").date() - today).days
            if days < 0:
                current[(p["id"], "expired")] = f"{p['name']} expired {-days} day(s) ago"
            elif days <= EXPIRY_WARN_DAYS:
                current[(p["id"], "near_expiry")] = f"{p['name']} expires in {days} day(s)"
    conn = db()
    active = {(r["product_id"], r["alert_type"]): r["id"] for r in
              conn.execute("SELECT * FROM alert_log WHERE resolved=0")}
    for key, msg in current.items():
        if key not in active:
            conn.execute("INSERT INTO alert_log(product_id,alert_type,message,created_at) VALUES(?,?,?,?)",
                         (key[0], key[1], msg, datetime.now().isoformat(timespec="seconds")))
    for key, aid in active.items():
        if key not in current:
            conn.execute("UPDATE alert_log SET resolved=1 WHERE id=?", (aid,))
    conn.commit()


@app.get("/api/alerts")
@login_required()
def alerts():
    refresh_alerts()
    return jsonify(rows("""SELECT a.*, p.name AS product FROM alert_log a
                           JOIN products p ON p.id=a.product_id
                           WHERE a.resolved=? ORDER BY a.id DESC LIMIT 200""",
                        (1 if request.args.get("history") else 0,)))


# ---------- analytics & reports ----------
def get_analysis():
    products = rows(PRODUCT_SQL)
    sales = rows("SELECT product_id, quantity, txn_date FROM transactions WHERE txn_type='sale'")
    return analytics.product_analysis(products, sales, pd.Timestamp(date.today()))


@app.get("/api/analytics")
@login_required()
def analytics_view():
    a = get_analysis()
    return jsonify(
        products=sorted(a, key=lambda r: -r["daily_velocity"]),
        reorder=sorted([r for r in a if r["suggested_reorder"] > 0], key=lambda r: -r["suggested_reorder"]),
        fast=[r for r in a if r["movement"] == "fast"],
        slow=[r for r in a if r["movement"] == "slow"],
        forecast_model="LinearRegression (scikit-learn)" if analytics.HAS_SKLEARN else "moving average")


@app.get("/api/dashboard")
@login_required()
def dashboard():
    refresh_alerts()
    today = date.today()
    p = rows("SELECT * FROM products")
    active = rows("SELECT alert_type FROM alert_log WHERE resolved=0")
    count = lambda *t: sum(1 for x in active if x["alert_type"] in t)
    since = (today - timedelta(days=13)).isoformat()
    daily = rows("""SELECT txn_date AS day, SUM(quantity*unit_price) AS revenue, SUM(quantity) AS units
                    FROM transactions WHERE txn_type='sale' AND txn_date>=? GROUP BY txn_date ORDER BY txn_date""",
                 (since,))
    by_day = {r["day"]: r for r in daily}
    series = []
    for i in range(14):
        k = (today - timedelta(days=13 - i)).isoformat()
        series.append({"day": k, "revenue": round(by_day.get(k, {}).get("revenue", 0) or 0, 2),
                       "units": by_day.get(k, {}).get("units", 0) or 0})
    cats = {}
    for x in p: cats[x["category"]] = cats.get(x["category"], 0) + x["quantity"] * x["price"]
    return jsonify(
        total_products=len(p), total_units=sum(x["quantity"] for x in p),
        inventory_value=round(sum(x["quantity"] * x["price"] for x in p), 2),
        low_stock=count("low_stock", "out_of_stock"), near_expiry=count("near_expiry", "expired"),
        revenue_14d=round(sum(s["revenue"] for s in series), 2), sales_series=series,
        category_value={k: round(v, 2) for k, v in cats.items()})


@app.get("/api/export/<kind>")
@login_required()
def export_csv(kind):
    if kind == "inventory":
        data = rows(PRODUCT_SQL + " ORDER BY p.name")
        cols = ["sku", "name", "category", "price", "quantity", "reorder_level", "expiry_date", "supplier"]
    elif kind == "transactions":
        data = rows("""SELECT t.id, t.txn_date, p.name AS product, t.txn_type, t.quantity, t.unit_price, t.user
                       FROM transactions t JOIN products p ON p.id=t.product_id ORDER BY t.id DESC""")
        cols = ["id", "txn_date", "product", "txn_type", "quantity", "unit_price", "user"]
    elif kind == "reorder":
        data = [r for r in get_analysis() if r["suggested_reorder"] > 0]
        cols = ["name", "supplier", "quantity", "daily_velocity", "forecast_7d", "suggested_reorder"]
    else:
        return jsonify(error="Unknown report"), 404
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader(); w.writerows(data)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={kind}_{date.today()}.csv"})


@app.get("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


if __name__ == "__main__":
    init_db()
    with app.app_context():
        refresh_alerts()
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
