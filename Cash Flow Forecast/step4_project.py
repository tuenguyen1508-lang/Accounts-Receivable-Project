"""
Step 4 — Project each open invoice to an expected collection date, and handle
the overdue case.

    expected_collection_raw = due_date + lag_used          (lag from Step 3)

Snapshot (the "as of" date the book was cut): 2026-03-31.
Derived from the data, not the system clock: Overdue invoices are all due
<= 2026-03-30 and Open invoices all due >= 2026-03-31, and the last observed
payment is 2026-03-31 -- so the status labels were assigned as of 2026-03-31.


Some invoices (mostly Overdue) project to a date on or before the snapshot -- you
cannot collect money in the past. We CLAMP any such projection into the first
forecast week AND FLAG it (`already_overdue`) so the first bar can be decomposed
into normal week-1 flow vs overdue backlog, rather than silently inflating it.
"""

import pandas as pd

SNAPSHOT = pd.Timestamp("2026-03-31")
FORECAST_START = SNAPSHOT + pd.Timedelta(days=1)   # 2026-04-01, first forecast day
CASH_COL = "amount_incl_gst"                        # GST-inclusive = what is collected


def project(open_: pd.DataFrame,
            snapshot: pd.Timestamp = SNAPSHOT,
            start: pd.Timestamp = FORECAST_START) -> pd.DataFrame:
    out = open_.copy()
    # Normalize to whole days: fractional lags (e.g. a 1.5-day median) otherwise
    # land at mid-day timestamps and muddle the day/week bucketing.
    out["exp_raw"] = (out["due_date"] + pd.to_timedelta(out["lag_used"], unit="D")).dt.normalize()

    # Flag projections that land before the first forecast day (can't collect in
    # the past / on the snapshot day itself). Equivalent to exp_raw <= snapshot.
    out["already_overdue"] = out["exp_raw"] < start

    # Clamp those into the first forecast day; genuine-future projections untouched.
    out["expected_collection"] = out["exp_raw"].where(~out["already_overdue"], start)

    # Weekly bucket index from the forecast start (week 0 = first forecast week).
    out["week"] = ((out["expected_collection"] - start).dt.days // 7).astype(int)
    out["week_start"] = start + pd.to_timedelta(out["week"] * 7, unit="D")
    return out


def cash_timeline(proj: pd.DataFrame) -> pd.DataFrame:
    """Weekly expected cash, split into overdue-backlog vs normal flow."""
    g = proj.groupby(["week_start", "already_overdue"])[CASH_COL].sum().unstack(fill_value=0.0)
    g = g.rename(columns={True: "overdue_backlog", False: "normal_flow"})
    for c in ["overdue_backlog", "normal_flow"]:
        if c not in g:
            g[c] = 0.0
    g["total"] = g["overdue_backlog"] + g["normal_flow"]
    return g[["normal_flow", "overdue_backlog", "total"]]


def main():
    open_ = pd.read_pickle("open_with_lag.pkl")
    proj = project(open_)

    n_over = int(proj["already_overdue"].sum())
    cash = proj[CASH_COL].sum()
    over_cash = proj.loc[proj["already_overdue"], CASH_COL].sum()

    print(f"snapshot = {SNAPSHOT.date()}   forecast starts {FORECAST_START.date()}")
    print(f"open invoices        : {len(proj):,}   (${cash:,.0f})")
    print(f"already-overdue (clamped -> week 1): {n_over:,} "
          f"({100*n_over/len(proj):.1f}%)   (${over_cash:,.0f}, "
          f"{100*over_cash/cash:.1f}% of book)")

    tl = cash_timeline(proj)
    print("\nWeekly expected cash (normal flow | overdue backlog | total):")
    with pd.option_context("display.float_format", lambda v: f"{v:,.0f}"):
        print(tl.head(12).to_string())

    wk1 = tl.iloc[0]
    print(f"\nWEEK 1 ({tl.index[0].date()}): total ${wk1['total']:,.0f} = "
          f"${wk1['normal_flow']:,.0f} normal + ${wk1['overdue_backlog']:,.0f} overdue backlog "
          f"({100*wk1['overdue_backlog']/wk1['total']:.0f}% is backlog).")
    print("Read week 1 as two things: this week's genuine collections PLUS the "
          "clamped overdue backlog, which carries elevated non-collection risk.")

    proj.to_pickle("open_projected.pkl")
    tl.to_csv("cash_timeline_weekly.csv")
    print("\nSaved -> open_projected.pkl, cash_timeline_weekly.csv")


if __name__ == "__main__":
    main()
