"""
Step 6 — Validate before trusting the forecast.

Check 1 (reconciliation): the projected schedule must sum to the total open AR
straight from the source fact table. Any gap = dropped or double-counted invoices.

Check 2 (shape): collections should cluster within a few weeks of the snapshot and
tail off. A large amount in a single distant week signals a bad lag.
"""

import pandas as pd

CASH_COL = "amount_incl_gst"
OPEN_STATUSES = ["Open", "Overdue"]
SNAPSHOT = pd.Timestamp("2026-03-31")


def check_reconcile(proj: pd.DataFrame) -> bool:
    # Independent ground truth: recompute open AR from the raw CSV, not from a pickle.
    raw = pd.read_csv("FactInvoice_final_v2.csv")
    raw[CASH_COL] = pd.to_numeric(raw[CASH_COL], errors="coerce")
    ar_rows = raw[raw["status"].isin(OPEN_STATUSES)]

    ar_total = ar_rows[CASH_COL].sum()
    ar_count = len(ar_rows)
    proj_total = proj[CASH_COL].sum()
    proj_count = len(proj)

    # No invoice counted twice, and the set matches the AR set exactly.
    dup = proj["invoice_id"].duplicated().sum()
    id_match = set(proj["invoice_id"]) == set(ar_rows["invoice_id"])

    print("--- Check 1: reconciliation to open AR ---")
    print(f"  open AR (raw CSV) : {ar_count:,} invoices   ${ar_total:,.2f}")
    print(f"  projected         : {proj_count:,} invoices   ${proj_total:,.2f}")
    print(f"  amount diff       : ${proj_total - ar_total:,.2f}")
    print(f"  duplicate ids     : {dup}")
    print(f"  id sets identical : {id_match}")

    ok = (abs(proj_total - ar_total) < 0.01 and proj_count == ar_count
          and dup == 0 and id_match)
    print(f"  RECONCILES        : {ok}")
    return ok


def check_shape(sched: pd.DataFrame, proj: pd.DataFrame) -> bool:
    print("\n--- Check 2: shape ---")
    total = sched["amount"].sum()
    weeks_from_snap = ((sched.index - SNAPSHOT).days // 7)

    within_4w = sched.loc[weeks_from_snap <= 4, "amount"].sum()
    span_weeks = len(sched)
    peak_wk = sched["amount"].idxmax()
    peak_amt = sched["amount"].max()

    # Distant-week red flag: a spike far from the snapshot.
    weeks_out = (peak_wk - SNAPSHOT).days // 7
    tail_start = SNAPSHOT + pd.Timedelta(weeks=8)
    tail_amt = sched.loc[sched.index >= tail_start, "amount"].sum()

    print(f"  total weeks spanned      : {span_weeks}")
    print(f"  within 4 weeks of snap   : ${within_4w:,.0f}  ({100*within_4w/total:.1f}%)")
    print(f"  peak week                : {peak_wk.date()}  ${peak_amt:,.0f}  "
          f"({weeks_out} weeks past snapshot)")
    print(f"  beyond 8 weeks (tail)    : ${tail_amt:,.0f}  ({100*tail_amt/total:.1f}%)")

    # Monotonic-ish decline after the (backlog-loaded) first week?
    after_wk1 = sched["amount"].iloc[1:]
    declining = (after_wk1.diff().dropna() <= 0).mean()
    print(f"  weeks declining after wk1: {100*declining:.0f}% of transitions")

    front_loaded = within_4w / total >= 0.80
    peak_is_early = weeks_out <= 1
    small_tail = tail_amt / total <= 0.05
    print(f"  front-loaded (>=80% in 4w): {front_loaded}")
    print(f"  peak is early (<=1w out)  : {peak_is_early}")
    print(f"  distant tail small (<=5%) : {small_tail}")

    ok = front_loaded and peak_is_early and small_tail
    print(f"  SHAPE SANE                : {ok}")
    return ok


def main():
    proj = pd.read_pickle("open_projected.pkl")
    sched = pd.read_csv("weekly_schedule.csv", parse_dates=["week_start"], index_col="week_start")

    ok1 = check_reconcile(proj)
    ok2 = check_shape(sched, proj)

    print("\n=== VALIDATION " + ("PASSED ===" if (ok1 and ok2) else "FAILED ==="))


if __name__ == "__main__":
    main()
