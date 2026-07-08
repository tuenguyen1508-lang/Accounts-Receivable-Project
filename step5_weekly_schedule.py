"""
Step 5 — Aggregate to a weekly collection schedule.

Bucket each open invoice's expected_collection into a calendar week (Monday-anchored
week-start), then per week: sum the expected cash, count invoices, and carry a
running cumulative total. The cumulative column is the "cash build-up" view.

We keep the normal-flow / overdue-backlog split from D4 so week 1 stays honest.
"""

import pandas as pd

CASH_COL = "amount_incl_gst"


def weekly_schedule(proj: pd.DataFrame) -> pd.DataFrame:
    df = proj.copy()
    # Monday-anchored calendar week that contains the expected collection date.
    df["week_start"] = df["expected_collection"].dt.to_period("W-SUN").dt.start_time

    g = df.groupby("week_start")
    sched = pd.DataFrame({
        "n_invoices": g.size(),
        "amount": g[CASH_COL].sum(),
        "normal_flow": g.apply(lambda x: x.loc[~x["already_overdue"], CASH_COL].sum(),
                               include_groups=False),
        "overdue_backlog": g.apply(lambda x: x.loc[x["already_overdue"], CASH_COL].sum(),
                                   include_groups=False),
    })

    # Reindex to a continuous weekly range so empty weeks show as zero, not gaps.
    full = pd.date_range(sched.index.min(), sched.index.max(), freq="W-MON")
    sched = sched.reindex(full, fill_value=0.0)
    sched.index.name = "week_start"
    sched["n_invoices"] = sched["n_invoices"].astype(int)

    sched["cumulative"] = sched["amount"].cumsum()
    sched["pct_of_book"] = 100 * sched["cumulative"] / sched["amount"].sum()
    return sched


def main():
    proj = pd.read_pickle("open_projected.pkl")
    sched = weekly_schedule(proj)

    print(f"Weekly collection schedule — {len(proj):,} open invoices, "
          f"${proj[CASH_COL].sum():,.0f} total\n")
    disp = sched.copy()
    disp.index = disp.index.strftime("%Y-%m-%d")
    with pd.option_context("display.float_format", lambda v: f"{v:,.0f}"):
        print(disp[["n_invoices", "normal_flow", "overdue_backlog",
                    "amount", "cumulative", "pct_of_book"]].to_string())

    # milestones on the cumulative build-up
    print("\nCash build-up milestones:")
    for pct in (50, 80, 90, 100):
        reached = sched.index[sched["pct_of_book"] >= pct - 1e-6]
        hit = reached[0] if len(reached) else sched.index[-1]
        print(f"  {pct:3d}% of the book collected by week of {hit.date()}")

    sched.to_csv("weekly_schedule.csv")
    print("\nSaved -> weekly_schedule.csv")


if __name__ == "__main__":
    main()
