"""Analytics layer: Pandas for sales analysis, Scikit-learn for optional demand forecast."""
import math
import pandas as pd

try:
    from sklearn.linear_model import LinearRegression
    HAS_SKLEARN = True
except ImportError:  # forecast is optional, per the architecture
    HAS_SKLEARN = False

WINDOW_DAYS = 30
COVER_DAYS = 14


def _forecast_next_7(series):
    """Forecast units for the next 7 days from a daily sales series (list of ints)."""
    n = len(series)
    if n == 0 or sum(series) == 0:
        return 0.0
    if HAS_SKLEARN:
        X = [[i] for i in range(n)]
        model = LinearRegression().fit(X, series)
        preds = model.predict([[n + i] for i in range(7)])
        return float(sum(max(0.0, p) for p in preds))
    return float(sum(series) / n * 7)


def product_analysis(products, sales_rows, today):
    """products: list of dict; sales_rows: list of dict(product_id, quantity, txn_date).
    Returns one analysis dict per product: velocity, class, forecast, reorder suggestion."""
    start = today - pd.Timedelta(days=WINDOW_DAYS - 1)
    days = pd.date_range(start, today)
    df = pd.DataFrame(sales_rows, columns=["product_id", "quantity", "txn_date"])
    if not df.empty:
        df["txn_date"] = pd.to_datetime(df["txn_date"])
        df = df[df["txn_date"] >= start]
    result = []
    for p in products:
        if df.empty:
            daily = pd.Series(0, index=days)
        else:
            s = df[df["product_id"] == p["id"]].groupby("txn_date")["quantity"].sum()
            daily = s.reindex(days, fill_value=0)
        units = int(daily.sum())
        velocity = units / WINDOW_DAYS
        forecast7 = _forecast_next_7(daily.tolist())
        days_left = (p["quantity"] / velocity) if velocity > 0 else None
        suggest = 0
        if p["quantity"] <= p["reorder_level"] or (days_left is not None and days_left < 7):
            need = (forecast7 / 7) * COVER_DAYS + p["reorder_level"] - p["quantity"]
            suggest = max(0, math.ceil(need))
        result.append({
            "id": p["id"], "name": p["name"], "category": p["category"],
            "quantity": p["quantity"], "units_30d": units,
            "daily_velocity": round(velocity, 2),
            "days_of_stock": None if days_left is None else round(days_left, 1),
            "forecast_7d": round(forecast7, 1), "suggested_reorder": suggest,
            "supplier": p.get("supplier"),
        })
    # classify movers by velocity terciles among products that sell
    sellers = sorted(r["daily_velocity"] for r in result if r["daily_velocity"] > 0)
    hi = sellers[int(len(sellers) * 2 / 3)] if sellers else 0
    lo = sellers[int(len(sellers) / 3)] if sellers else 0
    for r in result:
        v = r["daily_velocity"]
        r["movement"] = "slow" if v == 0 or v <= lo else ("fast" if v >= hi else "medium")
    return result
