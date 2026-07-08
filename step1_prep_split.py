"""
Step 1 — Prep and split.

Load the fact table, coerce dates to real datetimes and amounts to numeric,
then split into:
  - paid   : status == 'Paid'                 -> training history (learn lag here)
  - open_  : status in {'Open', 'Overdue'}    -> what we forecast


"""

import pandas as pd

FACT_PATH = "FactInvoice_final_v2.csv"

DATE_COLS = ["issue_date", "due_date", "payment_date"]
AMOUNT_COLS = ["amount_excl_gst", "gst", "amount_incl_gst", "amount_paid"]
OPEN_STATUSES = ["Open", "Overdue"]


def load_fact(path: str = FACT_PATH) -> pd.DataFrame:
    """Load the fact table with dates as datetimes and amounts as numeric."""
    df = pd.read_csv(path)

    # Dates are dd/mm/yyyy. dayfirst=True; blanks -> NaT (open invoices unpaid).
    for col in DATE_COLS:
        df[col] = pd.to_datetime(df[col], format="%d/%m/%Y", errors="coerce")

    for col in AMOUNT_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def split(df: pd.DataFrame):
    """Split into (paid, open_) frames."""
    paid = df[df["status"] == "Paid"].copy()
    open_ = df[df["status"].isin(OPEN_STATUSES)].copy()
    return paid, open_


def main():
    df = load_fact()
    paid, open_ = split(df)

    # --- integrity checks ---
    assert paid["payment_date"].notna().all(), "Paid invoice with no payment_date"
    assert paid["due_date"].notna().all(), "Paid invoice with no due_date"
    assert open_["payment_date"].isna().all(), "Open/Overdue invoice already has a payment_date"
    assert len(paid) + len(open_) == len(df), "Rows lost in split (unexpected status?)"

    # --- summary ---
    print(f"Loaded {len(df):,} invoices from {FACT_PATH}")
    print("\nDtypes after coercion:")
    print(df[DATE_COLS + AMOUNT_COLS].dtypes.to_string())

    print("\nStatus breakdown:")
    print(df["status"].value_counts().to_string())

    print(f"\npaid  (training history) : {len(paid):,} rows")
    print(f"open_ (to forecast)      : {len(open_):,} rows")
    print("  " + open_["status"].value_counts().to_string().replace("\n", "\n  "))

    print("\nDate ranges:")
    print(f"  paid  issue_date  : {paid['issue_date'].min():%Y-%m-%d} -> {paid['issue_date'].max():%Y-%m-%d}")
    print(f"  paid  payment_date: {paid['payment_date'].min():%Y-%m-%d} -> {paid['payment_date'].max():%Y-%m-%d}")
    print(f"  open_ due_date    : {open_['due_date'].min():%Y-%m-%d} -> {open_['due_date'].max():%Y-%m-%d}")

    # Persist with dtypes intact (pickle, since no pyarrow for parquet).
    paid.to_pickle("paid.pkl")
    open_.to_pickle("open.pkl")
    print("\nSaved -> paid.pkl, open.pkl")

    return paid, open_


if __name__ == "__main__":
    main()
