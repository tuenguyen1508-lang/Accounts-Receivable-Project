"""
Step 3 — Per-member lag with a fallback hierarchy.

For each open invoice we want that member's own typical lag, but a member with
too few paid invoices gives a noisy estimate. So we fall back:

    member  : member has >= N paid invoices   -> use their own median lag
    tier    : else                            -> use their member_tier's median lag
    overall : else (tier missing/empty)       -> use the overall median lag

The lookup returns (lag, source) so we can later report how much of the forecast
rests on member history vs tier fallback vs the global default.

"""

import numpy as np
import pandas as pd

DIM_PATH = "DimCustomer_2500_cleaned.csv"
DEFAULT_N = 3


def build_lookup(paid: pd.DataFrame, dim: pd.DataFrame, n: int = DEFAULT_N):
    """Return a lookup(customer_id) -> (lag, source) closure plus the tables it uses."""
    if "lag" not in paid.columns:
        paid = paid.copy()
        paid["lag"] = (paid["payment_date"] - paid["due_date"]).dt.days

    cust_tier = dim.set_index("customer_id")["member_tier"]

    # --- member level: median lag + count, keep only members with >= n paid ---
    grp = paid.groupby("customer_id")["lag"]
    member_median = grp.median()
    member_count = grp.size()
    member_lag = member_median[member_count >= n]  # eligible members only

    # --- tier level: median lag across all paid invoices in that tier ---
    paid_tier = paid.assign(member_tier=paid["customer_id"].map(cust_tier))
    tier_lag = paid_tier.groupby("member_tier")["lag"].median()

    # --- overall level ---
    overall_lag = float(paid["lag"].median())

    def lookup(customer_id):
        if customer_id in member_lag.index:
            return float(member_lag.loc[customer_id]), "member"
        tier = cust_tier.get(customer_id, np.nan)
        if pd.notna(tier) and tier in tier_lag.index:
            return float(tier_lag.loc[tier]), "tier"
        return overall_lag, "overall"

    tables = {
        "member_lag": member_lag,
        "member_count": member_count,
        "tier_lag": tier_lag,
        "overall_lag": overall_lag,
        "cust_tier": cust_tier,
    }
    return lookup, tables


def source_split(open_: pd.DataFrame, lookup) -> pd.DataFrame:
    """Apply lookup to each open invoice; return frame with lag_used + lag_source."""
    res = open_["customer_id"].apply(lambda c: pd.Series(lookup(c), index=["lag_used", "lag_source"]))
    out = open_.copy()
    out["lag_used"] = res["lag_used"].astype(float)
    out["lag_source"] = res["lag_source"]
    return out


def report(open_: pd.DataFrame, lookup, n: int):
    out = source_split(open_, lookup)
    print(f"--- N = {n} ---")
    # invoice-level: what actually drives the forecast
    inv = out["lag_source"].value_counts().reindex(["member", "tier", "overall"]).fillna(0).astype(int)
    print("invoice-level source split (of", len(out), "open invoices):")
    for src in ["member", "tier", "overall"]:
        print(f"  {src:<8}: {inv[src]:4d}  ({100*inv[src]/len(out):5.1f}%)")
    # customer-level
    cust = out.groupby("customer_id")["lag_source"].first().value_counts()
    cust = cust.reindex(["member", "tier", "overall"]).fillna(0).astype(int)
    print("customer-level source split (of", out["customer_id"].nunique(), "open customers):")
    for src in ["member", "tier", "overall"]:
        print(f"  {src:<8}: {cust[src]:4d}  ({100*cust[src]/out['customer_id'].nunique():5.1f}%)")
    print()
    return out


def main():
    paid = pd.read_pickle("paid.pkl")
    open_ = pd.read_pickle("open.pkl")
    dim = pd.read_csv(DIM_PATH)

    for n in (3, 5):
        lookup, tables = build_lookup(paid, dim, n=n)
        out = report(open_, lookup, n)
        if n == DEFAULT_N:
            chosen = out
            print("tier median lags:")
            print(tables["tier_lag"].to_string())
            print(f"overall median lag: {tables['overall_lag']:.1f}\n")

    chosen.to_pickle("open_with_lag.pkl")
    print(f"Saved open invoices with lag_used/lag_source (N={DEFAULT_N}) -> open_with_lag.pkl")


if __name__ == "__main__":
    main()
