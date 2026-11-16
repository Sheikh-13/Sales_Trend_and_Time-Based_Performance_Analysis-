"""
Data processing for the Afficionado Coffee Roasters sales analysis.

Covers: data ingestion and validation, temporal feature engineering,
and all aggregations used by the Streamlit dashboard (app.py).
"""

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

DATA_FILE = Path(__file__).parent / "Afficionado_Coffee_Roasters.xlsx"

# Calendar date assigned to the first day of the data (see engineer_features).
START_DATE = pd.Timestamp("2025-01-01")

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKEND_DAYS = ["Saturday", "Sunday"]
BUCKET_ORDER = ["Morning", "Afternoon", "Evening", "Late hours"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def hour_label(hour):
    """0-23 -> '08:00'."""
    return f"{int(hour):02d}:00"


def assign_time_bucket(hour):
    """Morning 6-11, Afternoon 12-16, Evening 17-21, Late hours 22-5."""
    if 6 <= hour <= 11:
        return "Morning"
    if 12 <= hour <= 16:
        return "Afternoon"
    if 17 <= hour <= 21:
        return "Evening"
    return "Late hours"


def _parse_time(value):
    """Return a datetime.time for a valid HH:MM:SS value, otherwise None."""
    if isinstance(value, dt.datetime):
        return value.time()
    if isinstance(value, dt.time):
        return value
    if isinstance(value, str):
        try:
            return dt.datetime.strptime(value.strip(), "%H:%M:%S").time()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# 1. Data ingestion and validation
# ---------------------------------------------------------------------------
def load_data(path=DATA_FILE):
    """Load the transaction dataset from the Excel file."""
    return pd.read_excel(path)


def validate(raw):
    """
    Run the validation checks and return (clean_df, report_df, summary).
    Rows that fail any check are excluded from clean_df.
    """
    parsed_time = raw["transaction_time"].apply(_parse_time)
    qty = pd.to_numeric(raw["transaction_qty"], errors="coerce")
    price = pd.to_numeric(raw["unit_price"], errors="coerce")
    ids = raw["transaction_id"]

    checks = {
        "Invalid transaction_time format (expected HH:MM:SS)": parsed_time.isna(),
        "Missing transaction_id": ids.isna(),
        "Duplicate transaction_id": ids.duplicated(keep="first") & ids.notna(),
        "Non-positive or missing transaction_qty": ~(qty > 0),
        "Non-positive or missing unit_price": ~(price > 0),
    }

    report = pd.DataFrame(
        {
            "Check": list(checks.keys()),
            "Rows flagged": [int(mask.sum()) for mask in checks.values()],
        }
    )
    report["Result"] = np.where(report["Rows flagged"] == 0, "Pass", "Fail (rows excluded)")

    bad_rows = pd.concat(checks.values(), axis=1).any(axis=1)
    clean = raw.loc[~bad_rows].copy()
    clean["transaction_time"] = parsed_time[~bad_rows]
    clean["transaction_qty"] = qty[~bad_rows]
    clean["unit_price"] = price[~bad_rows]

    summary = {"rows_loaded": len(raw), "rows_used": len(clean)}
    return clean.reset_index(drop=True), report, summary


# ---------------------------------------------------------------------------
# 2. Feature engineering (temporal)
# ---------------------------------------------------------------------------
def engineer_features(clean):
    """Add revenue, quantity, hour, day number, day of week, week number and time bucket."""
    df = clean.copy()

    df["revenue"] = df["transaction_qty"] * df["unit_price"]
    df["quantity"] = df["transaction_qty"]
    df["transactions"] = 1  # one row = one transaction; summing this column counts transactions

    seconds = df["transaction_time"].apply(lambda t: t.hour * 3600 + t.minute * 60 + t.second)
    df["hour"] = (seconds // 3600).astype(int)

    # ------------------------------------------------------------------
    # NOTE ON DATES
    # The dataset has no date column - only `year` (2025) and the clock time
    # in `transaction_time`. The rows are in chronological order, so a new
    # day starts whenever the clock time drops back to the morning (the time
    # is lower than in the previous row). Counting these resets gives day
    # numbers 1, 2, 3, ... for the whole dataset.
    # Day 1 is ASSUMED to be 1 January 2025; every later day follows
    # consecutively from it. The day of the week is derived from that
    # assumed calendar date. Calendar dates are not shown on the dashboard.
    # ------------------------------------------------------------------
    df["day_number"] = (seconds.diff() < 0).cumsum() + 1
    date = START_DATE + pd.to_timedelta(df["day_number"] - 1, unit="D")
    df["day_of_week"] = date.dt.day_name()
    df["day_type"] = np.where(df["day_of_week"].isin(WEEKEND_DAYS), "Weekend", "Weekday")
    df["week_number"] = (df["day_number"] - 1) // 7 + 1

    df["time_bucket"] = pd.Categorical(
        df["hour"].apply(assign_time_bucket), categories=BUCKET_ORDER, ordered=True
    )
    return df


# ---------------------------------------------------------------------------
# 3. Sales trend analysis
# ---------------------------------------------------------------------------
# `measure` below is "revenue" or "quantity" (the dashboard's Revenue vs Quantity toggle).
def daily_totals(df, by_store=False):
    """Revenue, quantity and transactions per day (optionally per store)."""
    keys = ["day_number", "week_number", "day_of_week", "day_type"]
    if by_store:
        keys = ["store_location"] + keys
    return (
        df.groupby(keys, observed=True)
        .agg(revenue=("revenue", "sum"), quantity=("quantity", "sum"), transactions=("transactions", "sum"))
        .reset_index()
    )


def weekly_totals(df, measure, by_store=False):
    """Weekly revenue, quantity, transactions and week-over-week change in `measure`."""
    keys = ["week_number"]
    if by_store:
        keys = ["store_location"] + keys
    weekly = (
        df.groupby(keys, observed=True)
        .agg(
            revenue=("revenue", "sum"),
            quantity=("quantity", "sum"),
            transactions=("transactions", "sum"),
            days=("day_number", "nunique"),
        )
        .reset_index()
    )
    if by_store:
        change = weekly.groupby("store_location")[measure].pct_change() * 100
    else:
        change = weekly[measure].pct_change() * 100
    weekly["change_pct"] = change
    weekly["direction"] = np.select([change > 0, change < 0], ["Up", "Down"], default="-")
    return weekly


def linear_trend(daily, measure):
    """Straight-line fit of the daily `measure`. Returns (slope per day, fitted values)."""
    slope, intercept = np.polyfit(daily["day_number"], daily[measure], 1)
    fitted = slope * daily["day_number"] + intercept
    return slope, fitted


def trend_by_store(store_daily, measure):
    """Trend direction, slope and fitted change for each store."""
    rows = []
    for store, g in store_daily.groupby("store_location"):
        g = g.sort_values("day_number")
        if len(g) < 2:
            continue
        slope, fitted = linear_trend(g, measure)
        first, last = fitted.iloc[0], fitted.iloc[-1]
        rows.append(
            {
                "Store": store,
                "Trend": "Upward" if slope > 0 else "Downward" if slope < 0 else "Flat",
                "Change per day": round(slope, 2),
                "Fitted change over the period (%)": round((last / first - 1) * 100, 1) if first else np.nan,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. Day-of-week performance analysis
# ---------------------------------------------------------------------------
def dow_summary(daily):
    """Average daily revenue, quantity and transactions for each day of the week."""
    grouped = daily.groupby("day_of_week").agg(
        avg_revenue=("revenue", "mean"),
        avg_quantity=("quantity", "mean"),
        avg_transactions=("transactions", "mean"),
    )
    return grouped.reindex([d for d in DAY_ORDER if d in grouped.index]).reset_index()


def weekday_weekend(daily):
    """Average daily revenue, quantity and transactions for weekdays vs weekends."""
    grouped = daily.groupby("day_type").agg(
        avg_revenue=("revenue", "mean"),
        avg_quantity=("quantity", "mean"),
        avg_transactions=("transactions", "mean"),
    )
    return grouped.reindex([t for t in ["Weekday", "Weekend"] if t in grouped.index]).reset_index()


# ---------------------------------------------------------------------------
# 5. Time-of-day demand analysis
# ---------------------------------------------------------------------------
def hourly_summary(df):
    """Transactions, revenue and quantity per hour, compared with the average hour."""
    hourly = (
        df.groupby("hour")
        .agg(transactions=("transactions", "sum"), revenue=("revenue", "sum"), quantity=("quantity", "sum"))
        .reset_index()
    )
    average = hourly["transactions"].mean()
    hourly["time_bucket"] = hourly["hour"].apply(assign_time_bucket)
    hourly["vs_average_pct"] = (hourly["transactions"] / average - 1) * 100
    hourly["level"] = np.where(hourly["transactions"] > average, "Above average", "Below average")
    return hourly, average


def bucket_summary(df):
    """Transactions, revenue and quantity for each time bucket (all four buckets are listed)."""
    buckets = (
        df.groupby("time_bucket", observed=False)
        .agg(transactions=("transactions", "sum"), revenue=("revenue", "sum"), quantity=("quantity", "sum"))
        .reset_index()
    )
    for col in ["transactions", "revenue", "quantity"]:
        total = buckets[col].sum()
        buckets[f"{col}_share_pct"] = buckets[col] / total * 100 if total else 0.0
    return buckets


# ---------------------------------------------------------------------------
# 6. Cross-location temporal comparison
# ---------------------------------------------------------------------------
def days_per_weekday(df):
    """Number of days of each weekday in the full dataset (used to average the heatmaps)."""
    return df.groupby("day_of_week")["day_number"].nunique().reindex(DAY_ORDER)


def store_hourly(df):
    """Revenue, quantity and transactions per hour for each store."""
    return (
        df.groupby(["store_location", "hour"])
        .agg(transactions=("transactions", "sum"), revenue=("revenue", "sum"), quantity=("quantity", "sum"))
        .reset_index()
    )


def heatmap_data(df, store, measure, weekday_counts):
    """
    Average `measure` per hour for one store: rows = day of week, columns = hour.
    Divided by the number of days of each weekday so weekdays are comparable.
    """
    hours = sorted(df["hour"].unique())
    days = [d for d in DAY_ORDER if d in df["day_of_week"].unique()]
    totals = (
        df[df["store_location"] == store]
        .groupby(["day_of_week", "hour"])[measure]
        .sum()
        .unstack("hour", fill_value=0)
        .reindex(index=days, columns=hours, fill_value=0)
    )
    return totals.div(weekday_counts.reindex(days), axis=0)


def location_insights(df, measure, measure_label):
    """Totals, peak hours, morning share and weekday/weekend behaviour for each store."""
    daily_store = daily_totals(df, by_store=True)
    grand_total = df[measure].sum()
    rows = []
    for store, g in df.groupby("store_location"):
        by_hour = g.groupby("hour")["transactions"].sum().sort_values(ascending=False)
        d = daily_store[daily_store["store_location"] == store]
        by_day = d.groupby("day_of_week")[measure].mean()
        by_day = by_day.reindex([x for x in DAY_ORDER if x in by_day.index])
        by_type = d.groupby("day_type")[measure].mean()
        rows.append(
            {
                "Store": store,
                f"Total {measure_label.lower()}": round(g[measure].sum(), 0),
                "Share of total (%)": round(g[measure].sum() / grand_total * 100, 1),
                "Total transactions": int(g["transactions"].sum()),
                "Peak hour (transactions)": hour_label(by_hour.index[0]),
                "Top 3 hours (transactions)": ", ".join(hour_label(h) for h in by_hour.index[:3]),
                "Morning share of transactions (%)": round(g.loc[g["time_bucket"] == "Morning", "transactions"].sum() / g["transactions"].sum() * 100, 1),
                "Busiest day": by_day.idxmax(),
                "Slowest day": by_day.idxmin(),
                f"Avg daily {measure_label.lower()} - weekday": round(by_type.get("Weekday", np.nan), 1),
                f"Avg daily {measure_label.lower()} - weekend": round(by_type.get("Weekend", np.nan), 1),
            }
        )
    return pd.DataFrame(rows)
