"""
Afficionado Coffee Roasters - Sales Trend and Time-Based Performance Analysis
Streamlit dashboard.  Run with:  streamlit run app.py
"""

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import data_processing as dp

st.set_page_config(page_title="Afficionado Coffee Roasters - Sales Analysis", layout="wide")

MEASURES = {"Revenue": "revenue", "Quantity": "quantity"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def fmt(value):
    return f"{value:,.0f}"


def format_hours(hours):
    """[7, 8, 9, 10] -> '07:00-10:59'  (consecutive hours are merged into one range)."""
    hours = sorted(hours)
    if not hours:
        return "none"
    groups, start, prev = [], hours[0], hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
            continue
        groups.append((start, prev))
        start = prev = h
    groups.append((start, prev))
    return ", ".join(f"{s:02d}:00-{e:02d}:59" for s, e in groups)


@st.cache_data(show_spinner="Loading and processing transactions...")
def load_everything():
    raw = dp.load_data()
    clean, report, summary = dp.validate(raw)
    df = dp.engineer_features(clean)
    return summary, df, dp.days_per_weekday(df)


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
try:
    summary, df_all, weekday_counts = load_everything()
except FileNotFoundError:
    st.error(f"Dataset not found. Place '{dp.DATA_FILE.name}' in the same folder as app.py.")
    st.stop()

st.title("Afficionado Coffee Roasters")
st.caption("Sales Trend and Time-Based Performance Analysis")

excluded = summary["rows_loaded"] - summary["rows_used"]
if excluded:
    st.warning(f"{excluded:,} rows failed the data validation checks and were excluded.")

# ---------------------------------------------------------------------------
# Sidebar: user capabilities
# ---------------------------------------------------------------------------
st.sidebar.header("Filters")
all_stores = sorted(df_all["store_location"].unique())
selected_stores = st.sidebar.multiselect("Store location", all_stores, default=all_stores)
selected_days = st.sidebar.multiselect("Day of week", dp.DAY_ORDER, default=dp.DAY_ORDER)
min_hour, max_hour = int(df_all["hour"].min()), int(df_all["hour"].max())
hour_range = st.sidebar.slider("Hour range", min_hour, max_hour, (min_hour, max_hour), format="%02d:00")
measure_name = st.sidebar.radio("Revenue vs quantity", list(MEASURES.keys()), horizontal=True)
measure = MEASURES[measure_name]
unit = measure_name.lower()

df = df_all[
    df_all["store_location"].isin(selected_stores)
    & df_all["day_of_week"].isin(selected_days)
    & df_all["hour"].between(hour_range[0], hour_range[1])
]
if df.empty:
    st.warning("No transactions match the selected filters. Adjust the filters in the sidebar.")
    st.stop()

daily = dp.daily_totals(df)

tab_trend, tab_dow, tab_heat, tab_loc = st.tabs(
    ["Overall Sales Trend", "Day-of-Week Performance", "Hourly Demand Heatmaps", "Location Comparison"]
)

# ---------------------------------------------------------------------------
# Module 1: Overall sales trend
# ---------------------------------------------------------------------------
with tab_trend:
    st.header("Overall Sales Trend")

    st.subheader(f"Daily {unit}")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily["day_number"], y=daily[measure], mode="lines", name=f"Daily {unit}"))
    if len(daily) >= 2:
        slope, fitted = dp.linear_trend(daily, measure)
        fig.add_trace(
            go.Scatter(x=daily["day_number"], y=fitted, mode="lines", name="Linear trend", line=dict(dash="dash"))
        )
    fig.update_layout(xaxis_title="Day", yaxis_title=measure_name, margin=dict(t=20))
    st.plotly_chart(fig, width="stretch", key="daily_trend")

    if len(daily) >= 2:
        direction = "upward" if slope > 0 else "downward" if slope < 0 else "flat"
        first, last = fitted.iloc[0], fitted.iloc[-1]
        change = f" ({(last / first - 1) * 100:+.0f}%)" if first else ""
        st.markdown(
            f"**Pattern:** daily {unit} follows an **{direction}** trend. The fitted trend line moves from "
            f"{fmt(first)} on the first day to {fmt(last)} on the last day{change}, "
            f"a change of {slope:+,.1f} per day."
        )

    st.subheader("Weekly aggregation")
    weekly = dp.weekly_totals(df, measure)
    c1, c2 = st.columns(2)
    c1.plotly_chart(
        px.bar(weekly, x="week_number", y=measure, title=f"Weekly {unit}", labels={"week_number": "Week", measure: measure_name}),
        width="stretch",
        key="weekly_measure",
    )
    c2.plotly_chart(
        px.bar(weekly, x="week_number", y="transactions", title="Weekly transactions", labels={"week_number": "Week", "transactions": "Transactions"}),
        width="stretch",
        key="weekly_transactions",
    )
    comparisons = int(weekly["direction"].isin(["Up", "Down"]).sum())
    if comparisons:
        ups = int((weekly["direction"] == "Up").sum())
        st.markdown(f"**Weekly pattern:** {unit} rose in {ups} of {comparisons} week-over-week comparisons.")
    weekly_table = weekly[["week_number", "days", "revenue", "quantity", "transactions", "change_pct", "direction"]].rename(
        columns={
            "week_number": "Week",
            "days": "Days in week",
            "revenue": "Revenue",
            "quantity": "Quantity",
            "transactions": "Transactions",
            "change_pct": f"{measure_name} change vs previous week (%)",
            "direction": "Direction",
        }
    )
    weekly_table["Revenue"] = weekly_table["Revenue"].round(2)
    weekly_table[f"{measure_name} change vs previous week (%)"] = weekly_table[f"{measure_name} change vs previous week (%)"].round(1)
    st.dataframe(weekly_table, width="stretch", hide_index=True)

    st.subheader("Store-level trend comparison")
    store_daily = dp.daily_totals(df, by_store=True)
    weekly_store = dp.weekly_totals(df, measure, by_store=True)
    c1, c2 = st.columns(2)
    c1.plotly_chart(
        px.line(store_daily, x="day_number", y=measure, color="store_location", title=f"Daily {unit} by store",
                labels={"day_number": "Day", measure: measure_name, "store_location": "Store"}),
        width="stretch",
        key="store_daily",
    )
    c2.plotly_chart(
        px.line(weekly_store, x="week_number", y=measure, color="store_location", markers=True, title=f"Weekly {unit} by store",
                labels={"week_number": "Week", measure: measure_name, "store_location": "Store"}),
        width="stretch",
        key="store_weekly",
    )
    store_trends = dp.trend_by_store(store_daily, measure)
    if not store_trends.empty:
        st.dataframe(store_trends, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Module 2: Day-of-week performance
# ---------------------------------------------------------------------------
with tab_dow:
    st.header("Day-of-Week Performance")
    dow = dp.dow_summary(daily)
    avg_col = f"avg_{measure}"
    day_list = dow["day_of_week"].tolist()

    fig_dow_measure = px.line(
        dow, x="day_of_week", y=avg_col, markers=True, category_orders={"day_of_week": day_list},
        title=f"Average daily {unit} by day of week", labels={"day_of_week": "Day of week", avg_col: f"Average {unit}"},
    )
    fig_dow_transactions = px.line(
        dow, x="day_of_week", y="avg_transactions", markers=True, category_orders={"day_of_week": day_list},
        title="Average daily transactions by day of week", labels={"day_of_week": "Day of week", "avg_transactions": "Average transactions"},
    )
    for fig in (fig_dow_measure, fig_dow_transactions):
        fig.update_yaxes(rangemode="tozero")  # start at zero so small differences are not exaggerated
    c1, c2 = st.columns(2)
    c1.plotly_chart(fig_dow_measure, width="stretch", key="dow_measure")
    c2.plotly_chart(fig_dow_transactions, width="stretch", key="dow_transactions")

    if len(dow) >= 2:
        for column, label in [(avg_col, unit), ("avg_transactions", "transactions")]:
            top = dow.loc[dow[column].idxmax()]
            low = dow.loc[dow[column].idxmin()]
            gap = (top[column] / low[column] - 1) * 100 if low[column] else 0
            st.markdown(
                f"**Busiest day ({label}):** {top['day_of_week']} with {fmt(top[column])} per day on average. "
                f"**Slowest day:** {low['day_of_week']} with {fmt(low[column])}. "
                f"The busiest day is {gap:.1f}% above the slowest."
            )
    st.subheader("Weekday vs weekend")
    ww = dp.weekday_weekend(daily)
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        colors = {
            "Weekday": "#3498DB",
            "Weekend": "#E67E22"
        }
   
        for _, row in ww.iterrows():
            fig.add_trace(go.Scatter(
                x=[0, row[avg_col]],
                y=[row["day_type"], row["day_type"]],
                mode="lines",
                line=dict(color=colors[row["day_type"]], width=3),
                showlegend=False
            ))

            fig.add_trace(go.Scatter(
                x=[row[avg_col]],
                y=[row["day_type"]],
                mode="markers+text",
                marker=dict(
                    size=14,
                    color=colors[row["day_type"]]
                ),
                text=[f"{row[avg_col]:.2f}"],
                textposition="top center",
                showlegend=False
            ))
        
        fig.update_layout(
            title=f"Average Daily {unit}",
            xaxis_title=f"Average {unit}",
            yaxis_title="",
            template="plotly_white",
            showlegend=False,
            height=290,
            margin=dict(
                t=130,
                b=50,
                l=80,
                r=40
            )
        )
       
        st.plotly_chart(fig, width="stretch")
    with c2:
        fig = go.Figure()
        colors = {
                "Weekday": "#3498DB",
                "Weekend": "#E67E22"
            }
        for _, row in ww.iterrows():
                fig.add_trace(go.Scatter(
                    x=[0, row["avg_transactions"]],
                    y=[row["day_type"], row["day_type"]],
                    mode="lines",
                    line=dict(color=colors[row["day_type"]], width=3),
                    showlegend=False
                ))
                fig.add_trace(go.Scatter(
                    x=[row["avg_transactions"]],
                    y=[row["day_type"]],
                    mode="markers+text",
                    marker=dict(
                        size=14,
                        color=colors[row["day_type"]]
                    ),                    
                    text=[f"{row['avg_transactions']:.2f}"],
                    textposition="top center",
                    showlegend=False
                ))
                    
                
            
        fig.update_layout(
            title="Average daily transactions",
            xaxis_title="Average transactions",
            yaxis_title="",
            template="plotly_white",
            showlegend=False,
            height=290,
            margin=dict(
                t=130,
                b=50,
                l=80,
                r=40
            )
        )
            
        st.plotly_chart(fig, width="stretch")
    if len(ww) == 2:
        wd = ww.loc[ww["day_type"] == "Weekday"].iloc[0]
        we = ww.loc[ww["day_type"] == "Weekend"].iloc[0]
        diff_measure = (we[avg_col] / wd[avg_col] - 1) * 100
        diff_txn = (we["avg_transactions"] / wd["avg_transactions"] - 1) * 100
        st.markdown(
            f"**Workdays vs leisure days:** weekend days average {fmt(we[avg_col])} {unit} per day against "
            f"{fmt(wd[avg_col])} on weekdays ({diff_measure:+.1f}%). In transactions, weekend days average "
            f"{fmt(we['avg_transactions'])} per day against {fmt(wd['avg_transactions'])} on weekdays ({diff_txn:+.1f}%)."
        )
    else:
        st.info("Select at least one weekday and one weekend day to compare weekdays with weekends.")

# ---------------------------------------------------------------------------
# Module 3: Hourly demand heatmaps
# ---------------------------------------------------------------------------
with tab_heat:
    st.header("Hourly Demand Heatmaps")
    st.subheader(f"Average {unit} per hour, by store")

    stores_in_view = sorted(df["store_location"].unique())
    heatmaps = {s: dp.heatmap_data(df, s, measure, weekday_counts) for s in stores_in_view}
    vmax = max(m.values.max() for m in heatmaps.values())
    for col, store in zip(st.columns(len(stores_in_view)), stores_in_view):
        hm = heatmaps[store]
        fig = px.imshow(
            hm.values,
            x=[dp.hour_label(h) for h in hm.columns],
            y=list(hm.index),
            color_continuous_scale="YlOrBr",
            range_color=(0, vmax),
            aspect="auto",
            labels=dict(x="Hour", y="Day of week", color=f"Avg {unit}"),
        )
        fig.update_layout(title=store, margin=dict(t=50), coloraxis_showscale=(store == stores_in_view[-1]))
        col.plotly_chart(fig, width="stretch", key=f"heatmap_{store}")

    st.subheader("Hourly demand curves")
    hourly, avg_hour = dp.hourly_summary(df)
    hourly["label"] = hourly["hour"].map(dp.hour_label)
    c1, c2 = st.columns(2)
    c1.plotly_chart(
        px.line(hourly, x="label", y="transactions", markers=True, title="Hourly transaction volume",
                labels={"label": "Hour", "transactions": "Transactions"}),
        width="stretch",
        key="hourly_transactions",
    )
    c2.plotly_chart(
        px.bar(hourly, x="label", y=measure, title=f"Hourly {unit} distribution",
               labels={"label": "Hour", measure: measure_name}),
        width="stretch",
        key="hourly_measure",
    )

    st.subheader("Rush hours, slow periods and peaks")
    top_hours = hourly.nlargest(3, "transactions")["hour"].tolist()
    st.markdown(
        f"**Peak transaction hours:** {', '.join(dp.hour_label(h) for h in top_hours)} "
        f"(the average hour has {fmt(avg_hour)} transactions)."
    )
    rush = hourly[(hourly["time_bucket"] == "Morning") & (hourly["level"] == "Above average")]["hour"].tolist()
    slow = hourly[(hourly["time_bucket"] == "Afternoon") & (hourly["level"] == "Below average")]["hour"].tolist()
    st.markdown(f"**Morning rush hours:** {format_hours(rush)} (Morning-bucket hours busier than the average hour).")
    st.markdown(f"**Midday slow periods:** {format_hours(slow)} (Afternoon-bucket hours quieter than the average hour).")
    evening = hourly[hourly["time_bucket"] == "Evening"]
    if evening.empty:
        st.markdown("**Evening peak:** no Evening-bucket hours in the selected range.")
    else:
        best = evening.loc[evening["transactions"].idxmax()]
        st.markdown(
            f"**Evening peak:** {dp.hour_label(best['hour'])} is the busiest Evening-bucket hour, with "
            f"{fmt(best['transactions'])} transactions ({best['vs_average_pct']:+.0f}% vs the average hour)."
        )

    st.subheader("Time buckets")
    buckets = dp.bucket_summary(df)
    bucket_table = buckets[["time_bucket", "transactions", "transactions_share_pct", measure, f"{measure}_share_pct"]].rename(
        columns={
            "time_bucket": "Time bucket",
            "transactions": "Transactions",
            "transactions_share_pct": "Share of transactions (%)",
            measure: measure_name,
            f"{measure}_share_pct": f"Share of {unit} (%)",
        }
    )
    bucket_table["Share of transactions (%)"] = bucket_table["Share of transactions (%)"].round(1)
    bucket_table[f"Share of {unit} (%)"] = bucket_table[f"Share of {unit} (%)"].round(1)
    bucket_table[measure_name] = bucket_table[measure_name].round(2)
    st.dataframe(bucket_table, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Module 4: Location comparison
# ---------------------------------------------------------------------------
with tab_loc:
    st.header("Location Comparison")
    insights = dp.location_insights(df, measure, measure_name)

    for col, (_, row) in zip(st.columns(len(insights)), insights.iterrows()):
        with col.container(border=True):
            st.subheader(row["Store"])
            st.metric(f"Total {unit}", fmt(row[f"Total {unit}"]))
            st.metric("Share of total", f"{row['Share of total (%)']:.1f}%")
            st.metric("Peak hour (transactions)", row["Peak hour (transactions)"])
            st.metric("Busiest day", row["Busiest day"])
            st.metric("Morning share of transactions", f"{row['Morning share of transactions (%)']:.1f}%")

    st.subheader(f"Hourly {unit} by store")
    store_hours = dp.store_hourly(df)
    store_hours["label"] = store_hours["hour"].map(dp.hour_label)
    st.plotly_chart(
        px.line(store_hours, x="label", y=measure, color="store_location", markers=True,
                labels={"label": "Hour", measure: measure_name, "store_location": "Store"}),
        width="stretch",
        key="store_hourly",
    )

    peaks = insights.set_index("Store")["Peak hour (transactions)"]
    if len(peaks) < 2:
        st.info("Select more than one store to compare peak hours across locations.")
    elif peaks.nunique() == 1:
        st.markdown(f"**Peak-hour alignment:** all selected stores peak at the same hour, {peaks.iloc[0]}.")
    else:
        listing = ", ".join(f"{store} at {hour}" for store, hour in peaks.items())
        st.markdown(f"**Peak-hour divergence:** the stores peak at different hours - {listing}.")

    st.subheader("Location-specific behaviour")
    st.dataframe(insights, width="stretch", hide_index=True)