"""
Step 7 — Export the two deliverables.

  1. forecast_by_invoice.csv  — one row per open invoice, for drill-through.
  2. forecast_weekly.csv      — one row per week, for the chart.
"""

import pandas as pd

CASH_COL = "amount_incl_gst"


def export_invoice(proj: pd.DataFrame, dim: pd.DataFrame) -> pd.DataFrame:
    tier = dim.set_index("customer_id")["member_tier"]
    name = dim.set_index("customer_id")["customer_name"]

    out = pd.DataFrame({
        "invoice_id": proj["invoice_id"],
        "customer_id": proj["customer_id"],
        "customer_name": proj["customer_id"].map(name),
        "member_tier": proj["customer_id"].map(tier),
        "category": proj["category"],
        "status": proj["status"],
        "issue_date": proj["issue_date"].dt.date,
        "due_date": proj["due_date"].dt.date,
        "amount_incl_gst": proj[CASH_COL],   # full source precision; do not round (must reconcile)
        "lag_used": proj["lag_used"],
        "lag_source": proj["lag_source"],            # member / tier / overall
        "expected_collection": proj["expected_collection"].dt.date,
        "already_overdue": proj["already_overdue"],  # clamped into week 1 (see D4)
    }).sort_values(["expected_collection", "customer_id", "invoice_id"])
    return out


def export_weekly(sched: pd.DataFrame) -> pd.DataFrame:
    out = sched.reset_index().rename(columns={"week_start": "week_start"})
    money = ["normal_flow", "overdue_backlog", "amount", "cumulative"]
    out[money] = out[money].round(2)
    out["pct_of_book"] = out["pct_of_book"].round(1)
    out["week_start"] = pd.to_datetime(out["week_start"]).dt.date
    return out[["week_start", "n_invoices", "normal_flow", "overdue_backlog",
                "amount", "cumulative", "pct_of_book"]]


def main():
    proj = pd.read_pickle("open_projected.pkl")
    dim = pd.read_csv("DimCustomer_2500_cleaned.csv")
    sched = pd.read_csv("weekly_schedule.csv", parse_dates=["week_start"], index_col="week_start")

    inv = export_invoice(proj, dim)
    wk = export_weekly(sched)

    inv.to_csv("forecast_by_invoice.csv", index=False)
    wk.to_csv("forecast_weekly.csv", index=False)

    print(f"forecast_by_invoice.csv : {len(inv):,} rows  "
          f"(${inv['amount_incl_gst'].sum():,.2f})")
    print(f"forecast_weekly.csv     : {len(wk):,} rows  "
          f"(cumulative ${wk['cumulative'].iloc[-1]:,.2f})")
    # tie-out: the two files must agree on the total
    assert abs(inv["amount_incl_gst"].sum() - wk["cumulative"].iloc[-1]) < 0.01
    print("tie-out OK: invoice total == weekly cumulative endpoint")

    print("\nforecast_by_invoice.csv — first rows:")
    print(inv.head(6).to_string(index=False))
    print("\nforecast_weekly.csv:")
    print(wk.to_string(index=False))


if __name__ == "__main__":
    main()
