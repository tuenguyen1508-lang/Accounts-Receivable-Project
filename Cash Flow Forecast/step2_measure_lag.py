"""
Step 2 — Measure historical lag on paid invoices.

lag = payment_date - due_date  

"""

import numpy as np
import pandas as pd


def add_lag(paid: pd.DataFrame) -> pd.DataFrame:
    paid = paid.copy()
    paid["lag"] = (paid["payment_date"] - paid["due_date"]).dt.days
    return paid


def summarise(lag: pd.Series) -> dict:
    return {
        "n": int(len(lag)),
        "mean": float(lag.mean()),
        "median": float(lag.median()),
        "std": float(lag.std()),
        "skew": float(lag.skew()),
        "min": int(lag.min()),
        "max": int(lag.max()),
        "mean_minus_median": float(lag.mean() - lag.median()),
        "pct_early": float((lag < 0).mean() * 100),
        "pct_ontime": float((lag == 0).mean() * 100),
        "pct_late": float((lag > 0).mean() * 100),
    }


def main():
    paid = add_lag(pd.read_pickle("paid.pkl"))
    lag = paid["lag"]
    s = summarise(lag)

    print(f"n            : {s['n']:,}")
    print(f"mean         : {s['mean']:.2f} days")
    print(f"median       : {s['median']:.1f} days")
    print(f"std          : {s['std']:.2f}")
    print(f"skewness     : {s['skew']:.2f}")
    print(f"min / max    : {s['min']} / {s['max']}")
    print(f"mean-median  : {s['mean_minus_median']:.2f} days")
    print()
    print("percentiles:")
    for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        print(f"  p{p:<2} : {np.percentile(lag, p):6.1f}")
    print()
    print(f"early (<0)  : {s['pct_early']:.1f}%")
    print(f"on-time (=0): {s['pct_ontime']:.1f}%")
    print(f"late (>0)   : {s['pct_late']:.1f}%")

    # Decision (see DECISIONS.md D2): right-skewed, so use median.
    forecast_lag = s["median"]
    print(f"\nCHOSEN forecast lag = median = {forecast_lag:.1f} days")

    paid.to_pickle("paid.pkl")  # persist the lag column
    print("Saved lag column -> paid.pkl")
    return paid, forecast_lag


if __name__ == "__main__":
    main()
