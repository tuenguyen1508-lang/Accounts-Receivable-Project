"""
pipeline.py — End-to-end AR & Cash-Flow Forecast automation.

    python pipeline.py                 # clean -> forecast -> export -> load to Supabase
    python pipeline.py --skip-load     # everything except the database load
    python pipeline.py --snapshot 2026-03-31

Flow:
    raw CSVs (data/raw/)
      -> clean & validate (data quality)
      -> generate forecast_by_invoice + forecast_weekly
      -> build DimDate over every date used
      -> validate relationships (referential integrity)
      -> export clean CSVs (data/processed/)
      -> truncate + load into Supabase/PostgreSQL inside one transaction
      -> print validation report

All output keys are INTEGER and all dates are ISO 'YYYY-MM-DD', matching the
Supabase table schema. Credentials come from a local .env file, never hardcoded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

# python-dotenv and SQLAlchemy are only needed for the database load. Import them
# lazily inside load_to_supabase() so the clean/forecast steps run without them.


# ======================================================================================
# CONFIGURATION  (everything a future run might need to change lives here)
# ======================================================================================

ROOT = Path(__file__).parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"

# Snapshot ("as of") date the forecast is anchored to. Overridable with --snapshot.
DEFAULT_SNAPSHOT = "2026-03-31"

# Invoice issue-date window used to detect and repair d/m vs m/d misreads.
PERIOD_START = "2026-01-01"
PERIOD_END = "2026-03-31"

GST_DIVISOR = 11          # standard GST is 10% -> gst = amount_incl_gst / 11
MONEY_TOL = 0.05          # tolerance for general money validations
GST_RULE_TOL = 0.02       # matches the DB ck_gst_rule tolerance exactly
FORECAST_TOL = 0.01       # forecast_by_invoice total must match forecast_weekly total within this

# Minimum paid invoices before a member's OWN median lag is trusted; otherwise fall
# back to their tier's median, otherwise the overall median (DECISIONS.md D3, N=3).
# The tier level has no count threshold: a tier is used whenever it has any paid
# history — only a missing/empty tier falls through to overall.
MIN_CUSTOMER_HISTORY = 3

# Lag statistic used at every level: "median" (robust to a few very-late payers,
# DECISIONS.md D2) or "mean". Both are computed and their divergence logged.
LAG_STAT = "median"

# Allowed / canonical value sets ------------------------------------------------------
TERMS_BY_TIER = {"Corporate": 30, "Premium": 30, "Standard": 14, "Associate": 14}
VALID_TERMS = {14, 30}
CREDIT_LIMIT_TO_TIER = {2000: "Associate", 5000: "Standard", 10000: "Premium", 20000: "Corporate"}
EXPECTED_CREDIT_LIMIT_BY_TIER = {tier: limit for limit, tier in CREDIT_LIMIT_TO_TIER.items()}

DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d",
                      "%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y"]

TIER_CANON = {"std": "Standard", "standard": "Standard", "prem": "Premium", "premium": "Premium",
              "assoc": "Associate", "associate": "Associate", "corp": "Corporate",
              "corporate": "Corporate"}
STATUS_CANON = {"paid": "Paid", "open": "Open", "overdue": "Overdue"}
VALID_TIERS = {"Associate", "Standard", "Premium", "Corporate"}
VALID_STATUS = {"Paid", "Open", "Overdue"}

CATEGORY_CANON = {
    "membership income": "Membership Income", "events income": "Events Income",
    "event sponsorship": "Event Sponsorship", "major sponsorship": "Major Sponsorship",
    "document signing revenue": "Document Signing Revenue",
    "sponsorship & advertising": "Sponsorship & Advertising",
}
METHOD_CANON = {"eft": "EFT", "e.f.t.": "EFT", "electronic funds transfer": "EFT",
                "cc": "Credit Card", "credit card": "Credit Card", "creditcard": "Credit Card",
                "dd": "Direct Debit", "direct debit": "Direct Debit", "direct-debit": "Direct Debit",
                "bpay": "BPAY", "b-pay": "BPAY"}
METHOD_TYPE = {"EFT": "Electronic", "Direct Debit": "Electronic", "BPAY": "Electronic",
               "Credit Card": "Card"}
REVENUE_GROUP = {"Membership Income": "Membership", "Events Income": "Events",
                 "Event Sponsorship": "Sponsorship", "Major Sponsorship": "Sponsorship",
                 "Sponsorship & Advertising": "Sponsorship",
                 "Document Signing Revenue": "Other", "Unknown": "Unknown"}
INDUSTRY_CANON = {
    "healthcare": "Healthcare", "health care": "Healthcare", "legal": "Legal", "law": "Legal",
    "finance": "Finance", "technology": "Technology", "tech": "Technology",
    "construction": "Construction", "government": "Government", "govt": "Government",
    "hospitality": "Hospitality", "not-for-profit": "Not-for-profit", "nfp": "Not-for-profit",
    "professional services": "Professional Services", "recruitment": "Recruitment",
    "education": "Education", "retail": "Retail",
}

# Load order (parents first) and truncate order (children first).
LOAD_ORDER = ["DimCustomer", "DimCategory", "DimPaymentMethod", "DimInvoiceStatus",
              "DimDate", "FactInvoice", "forecast_by_invoice", "forecast_weekly"]
TRUNCATE_ORDER = ["forecast_weekly", "forecast_by_invoice", "FactInvoice",
                  "DimCustomer", "DimCategory", "DimPaymentMethod", "DimInvoiceStatus", "DimDate"]

VALIDATION_STATS: dict = {}


# ======================================================================================
# SMALL HELPERS
# ======================================================================================

def log(stage: str, msg: str) -> None:
    """Uniform, greppable log line."""
    print(f"[{stage:<12}] {msg}")


def write_review_csv(filename: str, df: pd.DataFrame) -> None:
    """Write a quarantine/warning CSV every run, including empty files."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(PROCESSED_DIR / filename, index=False)


def add_stat(name: str, value) -> None:
    VALIDATION_STATS[name] = int(value) if isinstance(value, (np.integer, int)) else value


def to_number(value):
    """'$4,235.00' -> 4235.0 ; blanks / 'null' -> NaN."""
    if _is_blank(value):
        return np.nan
    return pd.to_numeric(str(value).replace("$", "").replace(",", "").strip(), errors="coerce")


def to_int_id(value):
    """'CUST001' / 'INV-1001' / '4' -> integer. Blank -> pandas NA."""
    digits = re.sub(r"\D", "", str(value))
    return int(digits) if digits else pd.NA


def _is_blank(value) -> bool:
    """True for real NaN and for the text placeholders that mean 'missing'."""
    if pd.isna(value):
        return True
    return str(value).strip().lower() in {"", "null", "none", "nan"}


def clean_blanks(df: pd.DataFrame) -> pd.DataFrame:
    """Replace 'null'/'None'/'nan'/'NaN'/'' text with real NaN across every cell."""
    return df.replace(r"^\s*(?i:null|none|nan)\s*$", np.nan, regex=True).replace(r"^\s*$", np.nan, regex=True)


def parse_date(value):
    """Parse a single date string against known formats -> Timestamp (NaT if unparseable)."""
    if _is_blank(value):
        return pd.NaT
    s = str(value).strip()
    for fmt in DATE_INPUT_FORMATS:
        try:
            return pd.to_datetime(s, format=fmt)
        except (ValueError, TypeError):
            continue
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.to_datetime(s, errors="coerce", dayfirst=True)


def iso(series: pd.Series) -> pd.Series:
    """Datetime series -> 'YYYY-MM-DD' strings, NaT -> None (loads cleanly as SQL NULL/date)."""
    out = series.dt.strftime("%Y-%m-%d")
    return out.where(series.notna(), None)


def canon(value, mapping: dict, missing="Unknown"):
    """Map a raw value to its canonical form; unknown non-blanks are Title-cased."""
    if _is_blank(value):
        return missing
    return mapping.get(str(value).strip().lower(), str(value).strip().title())


def canon_known(value, mapping: dict, valid_values: set, missing="Unknown"):
    """Canonicalise to a controlled set; nonblank unmapped values become Unknown."""
    if _is_blank(value):
        return missing
    mapped = mapping.get(str(value).strip().lower())
    return mapped if mapped in valid_values else "Unknown"


def suspicious_customer_name(value) -> bool:
    if _is_blank(value):
        return True
    s = str(value).strip()
    sl = s.lower()
    return len(s) < 2 or sl in {"unknown", "n/a", "na", "test", "dummy"} or s.isdigit()


def swap_into_window(ts, start, end):
    """Reverse a d/m vs m/d misread by swapping day<->month, if that lands in-window."""
    if pd.isna(ts):
        return ts
    if start <= ts <= end:
        return ts
    try:
        swapped = pd.Timestamp(year=ts.year, month=ts.day, day=ts.month)
        return swapped if start <= swapped <= end else ts
    except ValueError:
        return ts


# ======================================================================================
# 1. LOAD RAW
# ======================================================================================

def read_raw(name: str) -> pd.DataFrame:
    path = RAW_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Missing required input: {path}")
    return clean_blanks(pd.read_csv(path, dtype=str))


# ======================================================================================
# 2. CLEAN each table  (returns DB-ready frames: integer keys, ISO dates, numeric money)
# ======================================================================================

def clean_customers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    before_rows = len(df)
    df["customer_id"] = df["customer_id"].apply(to_int_id)
    df = df.dropna(subset=["customer_id"])
    dupes = df[df["customer_id"].duplicated(keep=False)].copy()
    write_review_csv("_quarantine_duplicate_customer.csv", dupes)
    add_stat("duplicate_customer_rows", len(dupes))
    if len(dupes):
        log("clean", f"{len(dupes)} duplicate customer row(s) -> _quarantine_duplicate_customer.csv")
    df = df.drop_duplicates("customer_id", keep="first")
    df["customer_id"] = df["customer_id"].astype(int)

    df["credit_limit"] = df["credit_limit"].apply(to_number)
    df["payment_terms_days"] = df["payment_terms_days"].apply(
        lambda s: to_number(str(s).lower().replace("days", "")) if not _is_blank(s) else np.nan)

    # member_tier: canonicalise, else derive from credit_limit.
    tier_missing = df["member_tier"].apply(_is_blank)
    df.loc[tier_missing, "member_tier"] = df.loc[tier_missing, "credit_limit"].map(
        lambda v: CREDIT_LIMIT_TO_TIER.get(int(v)) if pd.notna(v) else np.nan)
    df["member_tier"] = df["member_tier"].apply(lambda v: canon(v, TIER_CANON, missing="Standard"))
    df.loc[~df["member_tier"].isin(VALID_TIERS), "member_tier"] = "Standard"

    df["industry"] = df["industry"].apply(lambda v: canon_known(v, INDUSTRY_CANON, set(INDUSTRY_CANON.values())))
    df["state"] = df["state"].apply(
        lambda v: "ACT" if not _is_blank(v) and ("act" in str(v).lower().replace(".", "")
                                                 or "capital territory" in str(v).lower())
        else (np.nan if _is_blank(v) else str(v).strip()))
    blank_name = df["customer_name"].apply(_is_blank)
    suspicious_name = df["customer_name"].apply(suspicious_customer_name)
    name_warnings = df.loc[suspicious_name, ["customer_id", "customer_name"]].copy()
    name_warnings["warning_reason"] = np.where(
        name_warnings["customer_name"].apply(_is_blank), "blank_customer_name", "suspicious_customer_name")
    write_review_csv("_warning_customer_name.csv", name_warnings)
    if len(name_warnings):
        log("clean", f"{len(name_warnings)} blank/suspicious customer name row(s) -> _warning_customer_name.csv")
    df["customer_name"] = df["customer_name"].where(~blank_name, None)
    df["customer_name"] = df.apply(
        lambda r: f"Unknown Customer {int(r['customer_id'])}" if _is_blank(r["customer_name"])
        else str(r["customer_name"]).strip(), axis=1)

    # payment_terms_days: keep only 14/30, else derive from tier.
    df["payment_terms_days"] = df.apply(
        lambda r: int(r["payment_terms_days"]) if pd.notna(r["payment_terms_days"])
        and int(r["payment_terms_days"]) in VALID_TERMS else TERMS_BY_TIER[r["member_tier"]], axis=1)

    expected_limit = df["member_tier"].map(EXPECTED_CREDIT_LIMIT_BY_TIER)
    tier_limit_mismatch = df["credit_limit"].notna() & expected_limit.notna() & (df["credit_limit"] != expected_limit)
    mismatch = df.loc[tier_limit_mismatch, ["customer_id", "customer_name", "member_tier", "credit_limit"]].copy()
    mismatch["expected_credit_limit"] = expected_limit.loc[tier_limit_mismatch].astype("Int64").values
    write_review_csv("_warning_tier_limit_mismatch.csv", mismatch)
    add_stat("tier_credit_limit_mismatches", len(mismatch))
    if len(mismatch):
        log("clean", f"{len(mismatch)} member_tier/credit_limit mismatch row(s) -> _warning_tier_limit_mismatch.csv")

    df["credit_limit"] = df["credit_limit"].astype("Int64")
    df["member_since"] = iso(df["member_since"].apply(parse_date))
    add_stat("customer_rows_before_cleaning", before_rows)
    add_stat("customer_rows_after_cleaning", len(df))
    return df[["customer_id", "customer_name", "industry", "member_tier",
               "payment_terms_days", "credit_limit", "member_since", "state"]].reset_index(drop=True)


def clean_fact(df: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    before_rows = len(df)
    df["invoice_id"] = df["invoice_id"].apply(to_int_id)
    df["customer_id"] = df["customer_id"].apply(to_int_id)
    df = df.dropna(subset=["invoice_id"])
    duplicate_invoice_rows = df[df["invoice_id"].duplicated(keep=False)].copy()
    write_review_csv("_quarantine_duplicate_invoice.csv", duplicate_invoice_rows)
    add_stat("duplicate_invoice_rows", len(duplicate_invoice_rows))
    add_stat("duplicate_invoice_ids", int(duplicate_invoice_rows["invoice_id"].nunique()) if len(duplicate_invoice_rows) else 0)
    if len(duplicate_invoice_rows):
        conflicting_ids = [
            invoice_id for invoice_id, group in duplicate_invoice_rows.groupby("invoice_id", dropna=False)
            if len(group.drop_duplicates()) > 1
        ]
        log("clean", f"{len(duplicate_invoice_rows)} duplicate invoice row(s) -> _quarantine_duplicate_invoice.csv")
        if conflicting_ids:
            raise ValueError(
                f"{len(conflicting_ids)} duplicate invoice_id value(s) have conflicting row values. "
                f"Review data/processed/_quarantine_duplicate_invoice.csv and fix the source data.")
        log("clean", f"duplicate invoice_id rows were exact duplicates; kept one per invoice_id")
    df = df.drop_duplicates("invoice_id", keep="first")
    df["invoice_id"] = df["invoice_id"].astype(int)

    for c in ["amount_excl_gst", "gst", "amount_incl_gst", "amount_paid"]:
        df[c] = df[c].apply(to_number)

    invalid_amount = (
        df["amount_incl_gst"].isna()
        | (df["amount_incl_gst"] <= 0)
        | (df["amount_paid"].notna() & (df["amount_paid"] > df["amount_incl_gst"] + MONEY_TOL))
    )
    invalid_amount_rows = df.loc[invalid_amount].copy()
    write_review_csv("_quarantine_invalid_amount.csv", invalid_amount_rows)
    add_stat("invalid_amount_rows", len(invalid_amount_rows))
    if len(invalid_amount_rows):
        log("clean", f"{len(invalid_amount_rows)} invalid invoice amount row(s) -> _quarantine_invalid_amount.csv")
        df = df.loc[~invalid_amount].copy()

    if len(df):
        p99 = df["amount_incl_gst"].quantile(0.99)
        large_amount = df["amount_incl_gst"] > (3 * p99)
    else:
        p99 = np.nan
        large_amount = pd.Series(False, index=df.index)
    large_amount_rows = df.loc[large_amount].copy()
    if len(large_amount_rows):
        large_amount_rows["p99_amount_incl_gst"] = round(float(p99), 2)
        large_amount_rows["outlier_threshold"] = round(float(3 * p99), 2)
    write_review_csv("_warning_large_invoice_amount.csv", large_amount_rows)
    add_stat("large_invoice_amount_warnings", len(large_amount_rows))

    start, end = pd.Timestamp(PERIOD_START), pd.Timestamp(PERIOD_END)
    snap = pd.Timestamp(SNAPSHOT)
    issue = pd.to_datetime(
        df["issue_date"].apply(parse_date).apply(lambda d: swap_into_window(d, start, end)))
    pay = df["payment_date"].apply(parse_date)
    pay = pd.to_datetime(
        pay.apply(lambda d: swap_into_window(d, start, snap) if pd.notna(d) and d > snap else d))
    due_raw = pd.to_datetime(df["due_date"].apply(parse_date))

    # status: fill blanks from payment / due, then canonicalise to {Paid, Open, Overdue}.
    is_paid = pay.notna() | (df["amount_paid"].fillna(0) > 0)
    blank = df["status"].apply(_is_blank)
    df.loc[blank, "status"] = [
        "Paid" if is_paid[i] else ("Overdue" if (pd.notna(due_raw[i]) and due_raw[i] < snap) else "Open")
        for i in df.index[blank]]
    df["status"] = df["status"].apply(lambda v: canon(v, STATUS_CANON, missing="Open"))

    df["category"] = df["category"].apply(lambda v: canon_known(v, CATEGORY_CANON, set(REVENUE_GROUP.keys())))
    df["payment_method"] = df["payment_method"].apply(
        lambda v: np.nan if _is_blank(v) else METHOD_CANON.get(str(v).strip().lower(), "Unknown"))

    # GST is taken straight from the source data — it is NOT recalculated. A blank /
    # missing GST means the invoice is GST-free (gst = 0). amount_excl_gst is set to
    # (incl - gst) so that incl = excl + gst always holds for the DB constraint,
    # without touching the source GST value.
    df["gst"] = df["gst"].fillna(0.0).round(2)
    df["amount_excl_gst"] = (df["amount_incl_gst"] - df["gst"]).round(2)

    # due_date: KEEP the raw due date when it is present and not earlier than the
    # issue date. Only recompute (issue + customer terms) when it is blank, invalid,
    # or earlier than issue_date.
    terms = customers.set_index("customer_id")["payment_terms_days"].to_dict()
    tdays = df["customer_id"].map(lambda c: terms.get(c, 14) if pd.notna(c) else 14).fillna(14).astype(int)
    computed_due = issue + pd.to_timedelta(tdays.values, unit="D")
    need_recalc = due_raw.isna() | (issue.notna() & (due_raw < issue))
    due = due_raw.copy()
    due.loc[need_recalc] = computed_due.loc[need_recalc]
    log("clean", f"due_date: kept raw for {int((~need_recalc).sum())}, "
                 f"recomputed {int(need_recalc.sum())} (blank/invalid/earlier than issue)")
    add_stat("due_dates_kept", int((~need_recalc).sum()))
    add_stat("due_dates_recomputed", int(need_recalc.sum()))

    invalid_date = (
        issue.isna()
        | ~issue.between(start, end)
        | due.isna()
        | (due < issue)
        | (pay.notna() & (pay < issue))
    )
    invalid_date_rows = df.loc[invalid_date].copy()
    invalid_date_rows["parsed_issue_date"] = iso(issue.loc[invalid_date])
    invalid_date_rows["parsed_due_date"] = iso(due.loc[invalid_date])
    invalid_date_rows["parsed_payment_date"] = iso(pay.loc[invalid_date])
    write_review_csv("_quarantine_invalid_dates.csv", invalid_date_rows)
    add_stat("invalid_date_rows", len(invalid_date_rows))
    if len(invalid_date_rows):
        log("clean", f"{len(invalid_date_rows)} invalid invoice date row(s) -> _quarantine_invalid_dates.csv")
        keep = ~invalid_date
        df = df.loc[keep].copy()
        issue, due, pay = issue.loc[keep], due.loc[keep], pay.loc[keep]
        need_recalc = need_recalc.loc[keep]

    original_status = df["status"].copy()
    payment_date_amount_missing = pay.notna() & (df["amount_paid"].isna() | (df["amount_paid"] <= 0))
    df.loc[payment_date_amount_missing, "amount_paid"] = df.loc[payment_date_amount_missing, "amount_incl_gst"]
    has_payment = pay.notna() & (df["amount_paid"].fillna(0) > 0)
    derived_status = pd.Series(
        np.where(has_payment, "Paid", np.where(due < snap, "Overdue", "Open")),
        index=df.index,
    )
    conflict = df["status"].ne(derived_status)
    corrected = df.loc[conflict, ["invoice_id", "customer_id", "status", "payment_method",
                                  "issue_date", "due_date", "payment_date",
                                  "amount_incl_gst", "amount_paid"]].copy()
    if len(corrected):
        corrected["original_status"] = original_status.loc[conflict].values
        corrected["corrected_status"] = derived_status.loc[conflict].values
    write_review_csv("_warning_status_corrected.csv", corrected)
    add_stat("status_corrected_rows", len(corrected))
    if len(corrected):
        log("clean", f"{len(corrected)} status/date consistency correction row(s) -> _warning_status_corrected.csv")
    df["status"] = derived_status

    paid_mask = df["status"].eq("Paid")
    paid_amount_missing = paid_mask & (df["amount_paid"].isna() | (df["amount_paid"] <= 0))
    df.loc[paid_amount_missing, "amount_paid"] = df.loc[paid_amount_missing, "amount_incl_gst"]
    df.loc[~paid_mask, "amount_paid"] = 0.0
    df["amount_paid"] = df["amount_paid"].round(2)
    pay = pay.where(paid_mask, pd.NaT)

    df["customer_id"] = df["customer_id"].astype("Int64")
    df["issue_date"], df["due_date"], df["payment_date"] = iso(issue), iso(due), iso(pay)
    add_stat("fact_rows_before_cleaning", before_rows)
    add_stat("fact_rows_after_cleaning", len(df))
    return df[["invoice_id", "customer_id", "category", "payment_method", "status",
               "issue_date", "due_date", "payment_date",
               "amount_excl_gst", "gst", "amount_incl_gst", "amount_paid"]].reset_index(drop=True)


def clean_category(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().drop_duplicates()
    df["category_id"] = df["category_id"].apply(to_int_id).astype("Int64")
    df["category"] = df["category"].apply(lambda v: canon(v, CATEGORY_CANON))
    blank = df["revenue_group"].apply(_is_blank)
    df.loc[blank, "revenue_group"] = df.loc[blank, "category"].map(REVENUE_GROUP)
    df = df.drop_duplicates("category", keep="first")
    if "Unknown" not in set(df["category"]):        # ensure imputed 'Unknown' fact rows can join
        df = pd.concat([df, pd.DataFrame([{"category_id": 0, "category": "Unknown",
                                           "revenue_group": "Unknown"}])], ignore_index=True)
    return df[["category_id", "category", "revenue_group"]].reset_index(drop=True)


def clean_method(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().drop_duplicates()
    df["method_id"] = df["method_id"].apply(to_int_id).astype("Int64")
    df["payment_method"] = df["payment_method"].apply(
        lambda v: METHOD_CANON.get(str(v).strip().lower(), str(v).strip()) if not _is_blank(v) else np.nan)
    blank = df["method_type"].apply(_is_blank)
    df.loc[blank, "method_type"] = df.loc[blank, "payment_method"].map(METHOD_TYPE)
    df = df[["method_id", "payment_method", "method_type"]].dropna(
        subset=["payment_method"]).drop_duplicates("payment_method").reset_index(drop=True)
    if "Unknown" not in set(df["payment_method"]):
        df = pd.concat([df, pd.DataFrame([{"method_id": 0, "payment_method": "Unknown",
                                           "method_type": "Unknown"}])], ignore_index=True)
    return df.reset_index(drop=True)


def clean_status(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().drop_duplicates()
    df["status_id"] = df["status_id"].apply(to_int_id).astype("Int64")
    df["status"] = df["status"].apply(lambda v: canon(v, STATUS_CANON, missing="Open"))
    return df[["status_id", "status", "description"]].drop_duplicates("status").reset_index(drop=True)


# ======================================================================================
# 3. FORECAST GENERATION
# ======================================================================================

def _lag_tables(fact: pd.DataFrame, customers: pd.DataFrame):
    """Historical collection lag (payment_date - due_date, in days) from paid invoices.

    Lag is defined against the DUE date (not issue date) so it captures collection
    behaviour relative to the payment deadline, regardless of 14- vs 30-day terms.
    Returns per-customer and per-tier tables of (value=LAG_STAT, count) plus the
    overall LAG_STAT, and logs mean-vs-median divergence so the choice is defensible.
    """
    paid = fact[fact["status"].eq("Paid") & fact["payment_date"].notna()].copy()
    paid["lag"] = (pd.to_datetime(paid["payment_date"]) - pd.to_datetime(paid["due_date"])).dt.days
    paid = paid.dropna(subset=["lag"])
    paid = paid.merge(customers[["customer_id", "member_tier"]], on="customer_id", how="left")

    if len(paid):
        log("forecast", f"lag stat = {LAG_STAT}; overall mean={paid['lag'].mean():.2f} "
                        f"median={paid['lag'].median():.2f} (n={len(paid)} paid invoices) "
                        f"-> large gap = skew, median preferred")

    cust = paid.groupby("customer_id")["lag"].agg(value=LAG_STAT, count="count")
    tier = paid.groupby("member_tier")["lag"].agg(value=LAG_STAT, count="count")
    overall = float(getattr(paid["lag"], LAG_STAT)()) if len(paid) else 0.0
    return cust, tier, overall


def generate_forecast_by_invoice(fact: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    """Build forecast_by_invoice from every unpaid invoice, with tiered lag fallback."""
    snap = pd.Timestamp(SNAPSHOT)
    next_date = snap + pd.Timedelta(days=1)          # next practical collection date after snapshot
    cust_lag, tier_lag, overall_lag = _lag_tables(fact, customers)

    unpaid = fact[fact["status"].ne("Paid")].merge(
        customers[["customer_id", "customer_name", "member_tier"]], on="customer_id", how="left")

    def pick_lag(row):
        cid, tier = row["customer_id"], row["member_tier"]
        if cid in cust_lag.index and cust_lag.loc[cid, "count"] >= MIN_CUSTOMER_HISTORY:
            return round(float(cust_lag.loc[cid, "value"]), 2), "member"
        if pd.notna(tier) and tier in tier_lag.index:          # tier used if it has any history
            return round(float(tier_lag.loc[tier, "value"]), 2), "tier"
        return round(overall_lag, 2), "overall"

    lags = unpaid.apply(pick_lag, axis=1, result_type="expand")
    unpaid["lag_used"], unpaid["lag_source"] = lags[0], lags[1]

    # Report how much of the forecast rests on each fallback level (defensibility).
    mix = unpaid["lag_source"].value_counts(normalize=True).mul(100).round(1)
    log("forecast", "lag source mix: " + ", ".join(f"{k}={v}%" for k, v in mix.items()))

    # Project each open invoice, then handle the past-due case (DECISIONS.md D4):
    #   exp_raw = normalize(due_date + lag_used)   (normalise so fractional medians
    #                                               like 1.5 don't land mid-day)
    #   already_overdue = exp_raw < forecast_start (i.e. on/before the snapshot day)
    #   expected_collection = forecast_start for those (CLAMP into week 1), else exp_raw.
    # The flag lets week 1 be split into genuine flow vs clamped overdue backlog.
    due = pd.to_datetime(unpaid["due_date"])
    exp_raw = (due + pd.to_timedelta(unpaid["lag_used"], unit="D")).dt.normalize()
    unpaid["already_overdue"] = exp_raw < next_date
    expected = exp_raw.where(~unpaid["already_overdue"], next_date)
    unpaid["expected_collection"] = iso(expected)

    out = unpaid[["invoice_id", "customer_id", "customer_name", "member_tier", "category",
                  "status", "issue_date", "due_date", "amount_incl_gst",
                  "lag_used", "lag_source", "expected_collection", "already_overdue"]]
    return out.sort_values("invoice_id").reset_index(drop=True)


def generate_forecast_weekly(fbi: pd.DataFrame) -> pd.DataFrame:
    """Aggregate forecast_by_invoice into a Monday-anchored weekly collection schedule
    (DECISIONS.md Step 5): expected cash per week, split into normal flow vs clamped
    overdue backlog, with a cumulative 'cash build-up' and pct-of-book."""
    df = fbi.copy()
    exp = pd.to_datetime(df["expected_collection"])
    # Monday-anchored calendar week that contains the expected collection date.
    df["week_start"] = exp.dt.to_period("W-SUN").dt.start_time

    n = df.groupby("week_start").size()
    amount = df.groupby("week_start")["amount_incl_gst"].sum()
    normal = df[~df["already_overdue"]].groupby("week_start")["amount_incl_gst"].sum()
    backlog = df[df["already_overdue"]].groupby("week_start")["amount_incl_gst"].sum()

    sched = pd.DataFrame({"n_invoices": n, "amount": amount})
    sched["normal_flow"] = normal
    sched["overdue_backlog"] = backlog
    sched[["normal_flow", "overdue_backlog"]] = sched[["normal_flow", "overdue_backlog"]].fillna(0.0)

    # Reindex to a continuous weekly range so empty weeks show as zeros, not gaps.
    full = pd.date_range(sched.index.min(), sched.index.max(), freq="W-MON")
    sched = sched.reindex(full, fill_value=0.0)
    sched.index.name = "week_start"
    sched["n_invoices"] = sched["n_invoices"].astype(int)
    sched["cumulative"] = sched["amount"].cumsum()
    total = float(sched["amount"].sum())
    sched["pct_of_book"] = (100 * sched["cumulative"] / total) if total else 0.0

    # Round like the exported deliverable: money to 2 dp, pct_of_book to 1 dp.
    weekly = sched.reset_index()
    for c in ["normal_flow", "overdue_backlog", "amount", "cumulative"]:
        weekly[c] = weekly[c].round(2)
    weekly["pct_of_book"] = weekly["pct_of_book"].round(1)
    weekly["week_start"] = iso(pd.to_datetime(weekly["week_start"]))
    return weekly[["week_start", "n_invoices", "normal_flow", "overdue_backlog",
                   "amount", "cumulative", "pct_of_book"]]


# ======================================================================================
# 3b. DimDate  (must cover every date used anywhere)
# ======================================================================================

def build_dim_date(all_dates: pd.Series) -> pd.DataFrame:
    valid = pd.to_datetime(all_dates, errors="coerce").dropna()
    dr = pd.date_range(valid.min(), valid.max(), freq="D")
    d = pd.DataFrame({"date": dr})
    x = d["date"]
    d["date_key"] = x.dt.strftime("%Y%m%d").astype(int)
    d["year"] = x.dt.year
    d["quarter"] = "Q" + x.dt.quarter.astype(str)
    d["month_no"] = x.dt.month
    d["month_name"] = x.dt.strftime("%b")
    d["date"] = iso(x)
    return d[["date_key", "date", "year", "quarter", "month_no", "month_name"]]


# ======================================================================================
# 4. RELATIONSHIP VALIDATION  (referential integrity — hard gate before load)
# ======================================================================================

def validate_relationships(t: dict) -> None:
    errors = []

    def check(name, series, valid_set, mask=None):
        s = series if mask is None else series[mask]
        bad = int((~s.isin(valid_set)).sum())
        if bad:
            errors.append(f"{name}: {bad} value(s) not found in the referenced dimension")

    cust_ids = set(t["DimCustomer"]["customer_id"])
    cats = set(t["DimCategory"]["category"])
    methods = set(t["DimPaymentMethod"]["payment_method"])
    statuses = set(t["DimInvoiceStatus"]["status"])
    dates = set(t["DimDate"]["date"])
    f = t["FactInvoice"]

    check("FactInvoice.customer_id -> DimCustomer", f["customer_id"], cust_ids)
    check("FactInvoice.category -> DimCategory", f["category"], cats)
    check("FactInvoice.status -> DimInvoiceStatus", f["status"], statuses)
    check("FactInvoice.payment_method -> DimPaymentMethod", f["payment_method"], methods,
          mask=f["payment_method"].notna())
    for col in ["issue_date", "due_date", "payment_date"]:
        check(f"FactInvoice.{col} -> DimDate", f[col], dates, mask=f[col].notna())

    fbi = t["forecast_by_invoice"]
    check("forecast_by_invoice.customer_id -> DimCustomer", fbi["customer_id"], cust_ids)
    check("forecast_by_invoice.status -> DimInvoiceStatus", fbi["status"], statuses)
    check("forecast_by_invoice.expected_collection -> DimDate", fbi["expected_collection"], dates)

    if errors:
        for e in errors:
            log("validate", f"  ERROR: {e}")
        raise ValueError("Referential-integrity validation FAILED — nothing exported or loaded.")
    log("validate", "referential integrity OK for all relationships")


def quality_report(fact: pd.DataFrame) -> None:
    """Non-fatal data-quality checks on the cleaned fact table."""
    incl = fact["amount_incl_gst"]
    gst_free = fact["gst"] == 0                     # blank GST in source -> GST-free
    gst_ok = int((gst_free | ((fact["gst"] - incl / GST_DIVISOR).abs() <= GST_RULE_TOL)).sum())
    sum_ok = int(((incl - (fact["amount_excl_gst"] + fact["gst"])).abs() <= MONEY_TOL).sum())
    due_ok = int((pd.to_datetime(fact["due_date"]) >= pd.to_datetime(fact["issue_date"])).sum())
    n = len(fact)
    log("quality", f"GST (0=free, else ~incl/11): {gst_ok}/{n} pass ({int(gst_free.sum())} GST-free)")
    log("quality", f"total (incl ~= excl+gst):    {sum_ok}/{n} pass")
    log("quality", f"due_date >= issue_date:      {due_ok}/{n} pass")


def export_unknown_mapping_warnings(customers: pd.DataFrame, fact: pd.DataFrame) -> None:
    warnings = []
    unknown_industry = customers["industry"].eq("Unknown")
    for _, row in customers.loc[unknown_industry].iterrows():
        warnings.append({
            "table": "DimCustomer",
            "row_id": row["customer_id"],
            "warning_field": "industry",
            "warning_value": row["industry"],
        })

    unknown_category = fact["category"].eq("Unknown")
    for _, row in fact.loc[unknown_category].iterrows():
        warnings.append({
            "table": "FactInvoice",
            "row_id": row["invoice_id"],
            "warning_field": "category",
            "warning_value": row["category"],
        })

    unknown_method = fact["payment_method"].eq("Unknown")
    for _, row in fact.loc[unknown_method].iterrows():
        warnings.append({
            "table": "FactInvoice",
            "row_id": row["invoice_id"],
            "warning_field": "payment_method",
            "warning_value": row["payment_method"],
        })

    out = pd.DataFrame(warnings, columns=["table", "row_id", "warning_field", "warning_value"])
    write_review_csv("_warning_unknown_mapping.csv", out)
    add_stat("unknown_industry_rows", int(unknown_industry.sum()))
    add_stat("unknown_category_rows", int(unknown_category.sum()))
    add_stat("unknown_payment_method_rows", int(unknown_method.sum()))
    add_stat("unknown_mapping_warning_rows", len(out))
    if len(out):
        log("clean", f"{len(out)} unknown mapping warning row(s) -> _warning_unknown_mapping.csv")


# ======================================================================================
# 6b. COMPARE WITH PREVIOUS RUN  (did the regenerated data change vs the dashboard?)
# ======================================================================================

def compare_with_previous(tables: dict) -> None:
    """If processed CSVs already exist, print old-vs-new row counts and key totals.
    Runs BEFORE export overwrites them, so you can see what changed since last time."""
    def total(df, col):
        return round(float(pd.to_numeric(df[col], errors="coerce").sum()), 2)

    existing = {n for n in LOAD_ORDER if (PROCESSED_DIR / f"{n}.csv").exists()}
    if not existing:
        log("compare", "no previous processed CSVs found — this is a first run, nothing to compare.")
        return

    log("compare", "row counts (previous -> new):")
    for name in LOAD_ORDER:
        new_rows = len(tables[name])
        if name in existing:
            old_rows = len(pd.read_csv(PROCESSED_DIR / f"{name}.csv"))
            flag = "" if old_rows == new_rows else "  <-- changed"
            log("compare", f"  {name:<20} {old_rows:>6} -> {new_rows:>6} ({new_rows - old_rows:+d}){flag}")
        else:
            log("compare", f"  {name:<20}    n/a -> {new_rows:>6} (new table)")

    log("compare", "key totals (previous -> new):")
    for name, col, label in [("FactInvoice", "amount_incl_gst", "total invoiced"),
                             ("forecast_by_invoice", "amount_incl_gst", "forecast total (by invoice)"),
                             ("forecast_weekly", "amount", "forecast total (weekly)")]:
        new_t = total(tables[name], col)
        if name in existing:
            old_t = total(pd.read_csv(PROCESSED_DIR / f"{name}.csv"), col)
            flag = "" if abs(old_t - new_t) < 0.01 else "  <-- changed"
            log("compare", f"  {label:<28} {old_t:>14,.2f} -> {new_t:>14,.2f} ({new_t - old_t:+,.2f}){flag}")
        else:
            log("compare", f"  {label:<28} {'n/a':>14} -> {new_t:>14,.2f}")


# ======================================================================================
# 7. EXPORT
# ======================================================================================

def export_processed(tables: dict) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    for name in LOAD_ORDER:
        path = PROCESSED_DIR / f"{name}.csv"
        tables[name].to_csv(path, index=False)
        log("export", f"{name:<20} {len(tables[name]):>5} rows -> {path.name}")


# ======================================================================================
# 8. SUPABASE / POSTGRES  (engine, optional schema rebuild, truncate + insert)
# ======================================================================================

def build_engine():
    """Create a SQLAlchemy engine from the .env credentials (nothing hardcoded)."""
    from dotenv import load_dotenv
    from sqlalchemy import create_engine

    load_dotenv(ROOT / ".env")
    required = ["SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_NAME",
                "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"Missing DB env var(s): {', '.join(missing)}. "
                               f"Copy .env.template to .env and fill it in (or run --skip-load).")
    url = (f"postgresql+psycopg2://{os.getenv('SUPABASE_DB_USER')}:{os.getenv('SUPABASE_DB_PASSWORD')}"
           f"@{os.getenv('SUPABASE_DB_HOST')}:{os.getenv('SUPABASE_DB_PORT')}"
           f"/{os.getenv('SUPABASE_DB_NAME')}")
    return create_engine(url)


def rebuild_schema(engine, schema_path="sql/create_schema.sql") -> None:
    """DROP and CREATE every table/constraint/index from the schema SQL file.

    WARNING: this destroys all existing data in those tables. The SQL file wraps
    itself in BEGIN/COMMIT, so it runs atomically — any failure leaves the schema
    untouched. Only invoked when the user passes --rebuild-schema.
    """
    path = Path(schema_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"Schema SQL not found: {path}")

    sql = path.read_text(encoding="utf-8")
    log("schema", "rebuilding database schema...")
    log("schema", "WARNING: dropping and recreating ALL tables — existing data will be lost")
    # AUTOCOMMIT so the script's own BEGIN/COMMIT controls the (single) transaction.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(sql)
    log("schema", "schema rebuild complete.")


def load_to_supabase(tables: dict, engine) -> None:
    """Truncate all tables then insert all tables, inside one transaction (rolls back on error)."""
    from sqlalchemy import text

    # Frames -> None for NaN/NA/NaT so they land as SQL NULL.
    to_load = {name: tables[name].astype(object).where(pd.notna(tables[name]), None)
               for name in LOAD_ORDER}

    log("load", "connecting to Supabase/PostgreSQL ...")
    try:
        # One transaction: truncate everything, then insert everything. Any error rolls back.
        with engine.begin() as conn:
            quoted = ", ".join(f'"{t}"' for t in TRUNCATE_ORDER)
            conn.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
            log("load", f"truncated {len(TRUNCATE_ORDER)} tables")
            for name in LOAD_ORDER:
                to_load[name].to_sql(name, conn, if_exists="append", index=False,
                                     method="multi", chunksize=500)
                log("load", f"inserted {name:<20} {len(to_load[name]):>5} rows")
        log("load", "COMMIT — all tables loaded successfully")
    except Exception as exc:
        log("load", f"ROLLBACK — load failed, database unchanged: {exc}")
        raise


# ======================================================================================
# 9. VALIDATION REPORT
# ======================================================================================

def validation_report(tables: dict) -> None:
    log("report", "row counts:")
    for name in LOAD_ORDER:
        log("report", f"  {name:<20} {len(tables[name]):>6}")

    fact = tables["FactInvoice"]
    fbi = tables["forecast_by_invoice"]
    weekly = tables["forecast_weekly"]

    total_invoiced = round(float(fact["amount_incl_gst"].sum()), 2)
    open_ar = round(float(fact.loc[fact["status"].ne("Paid"), "amount_incl_gst"].sum()), 2)
    fbi_total = round(float(fbi["amount_incl_gst"].sum()), 2)
    # Reconcile against the weekly cumulative endpoint (step7 tie-out), which is robust
    # to per-week 2dp rounding, rather than the sum of rounded weekly amounts.
    weekly_total = round(float(weekly["cumulative"].iloc[-1]), 2)

    log("report", f"total amount_incl_gst (FactInvoice): {total_invoiced:,.2f}")
    log("report", f"open AR total (status != Paid):      {open_ar:,.2f}")
    log("report", f"forecast total (forecast_by_invoice):{fbi_total:,.2f}")
    log("report", f"forecast total (forecast_weekly):    {weekly_total:,.2f}")

    diff = round(fbi_total - weekly_total, 2)
    reconciliation = {
        "status": "PASS" if abs(fbi_total - weekly_total) <= FORECAST_TOL else "FAIL",
        "forecast_by_invoice_total": fbi_total,
        "forecast_weekly_total": weekly_total,
        "difference": diff,
        "tolerance": FORECAST_TOL,
    }

    if reconciliation["status"] == "FAIL":
        raise ValueError(f"Forecast totals disagree: forecast_by_invoice={fbi_total} "
                         f"vs forecast_weekly={weekly_total} (tol {FORECAST_TOL})")
    log("report", "forecast_by_invoice total matches forecast_weekly total [OK]")

    fact_dates = {}
    dates = set(tables["DimDate"]["date"])
    for col in ["issue_date", "due_date", "payment_date"]:
        mask = fact[col].notna()
        fact_dates[f"{col}_dimdate_orphans"] = int((~fact.loc[mask, col].isin(dates)).sum())

    report = {
        "status": "PASS",
        "row_counts": {name: len(tables[name]) for name in LOAD_ORDER},
        "row_counts_before_after_cleaning": {
            "FactInvoice": {
                "before": VALIDATION_STATS.get("fact_rows_before_cleaning", len(fact)),
                "after": VALIDATION_STATS.get("fact_rows_after_cleaning", len(fact)),
            },
            "DimCustomer": {
                "before": VALIDATION_STATS.get("customer_rows_before_cleaning", len(tables["DimCustomer"])),
                "after": VALIDATION_STATS.get("customer_rows_after_cleaning", len(tables["DimCustomer"])),
            },
        },
        "due_dates": {
            "kept": VALIDATION_STATS.get("due_dates_kept", 0),
            "recomputed": VALIDATION_STATS.get("due_dates_recomputed", 0),
        },
        "orphan_rows": VALIDATION_STATS.get("orphan_rows", 0),
        "duplicate_invoices": {
            "rows": VALIDATION_STATS.get("duplicate_invoice_rows", 0),
            "invoice_ids": VALIDATION_STATS.get("duplicate_invoice_ids", 0),
        },
        "invalid_amount_rows": VALIDATION_STATS.get("invalid_amount_rows", 0),
        "invalid_date_rows": VALIDATION_STATS.get("invalid_date_rows", 0),
        "unknown_category_rows": VALIDATION_STATS.get("unknown_category_rows", 0),
        "unknown_industry_rows": VALIDATION_STATS.get("unknown_industry_rows", 0),
        "unknown_payment_method_rows": VALIDATION_STATS.get("unknown_payment_method_rows", 0),
        "tier_credit_limit_mismatches": VALIDATION_STATS.get("tier_credit_limit_mismatches", 0),
        "large_invoice_amount_warnings": VALIDATION_STATS.get("large_invoice_amount_warnings", 0),
        "status_corrected_rows": VALIDATION_STATS.get("status_corrected_rows", 0),
        "gst_rule_violations": VALIDATION_STATS.get("bad_gst_rows", 0),
        "relationship_validation": {
            "hard_gate": "PASS",
            **fact_dates,
        },
        "totals": {
            "total_invoiced": total_invoiced,
            "open_ar": open_ar,
        },
        "forecast_total_reconciliation": reconciliation,
        "outputs": [f"{name}.csv" for name in LOAD_ORDER],
        "review_files": [
            "_quarantine_duplicate_customer.csv",
            "_quarantine_duplicate_invoice.csv",
            "_quarantine_invalid_amount.csv",
            "_quarantine_invalid_dates.csv",
            "_quarantine_orphan_fact.csv",
            "_quarantine_gst_fact.csv",
            "_warning_customer_name.csv",
            "_warning_tier_limit_mismatch.csv",
            "_warning_large_invoice_amount.csv",
            "_warning_status_corrected.csv",
            "_warning_unknown_mapping.csv",
        ],
    }
    (PROCESSED_DIR / "_validation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log("report", "_validation_report.json written")


# ======================================================================================
# ORCHESTRATION
# ======================================================================================

def run(skip_load: bool, allow_drop_orphans: bool, allow_drop_bad_gst: bool,
        rebuild_schema_flag: bool) -> None:
    VALIDATION_STATS.clear()

    # 1. LOAD RAW
    raw_fact = read_raw("FactInvoice_raw.csv")
    raw_cust = read_raw("DimCustomer_raw.csv")
    raw_cat = read_raw("DimCategory_raw.csv")
    raw_pm = read_raw("DimPaymentMethod_raw.csv")
    raw_st = read_raw("DimInvoiceStatus_raw.csv")
    log("load", f"read raw: fact={len(raw_fact)} customer={len(raw_cust)} +3 dims")

    # 2. CLEAN (customers first — fact needs their payment terms)
    customers = clean_customers(raw_cust)
    fact = clean_fact(raw_fact, customers)
    category = clean_category(raw_cat)
    method = clean_method(raw_pm)
    status = clean_status(raw_st)
    log("clean", f"fact={len(fact)} customer={len(customers)} category={len(category)} "
                 f"method={len(method)} status={len(status)}")

    # 2b. Fact rows whose customer_id has no matching customer cannot be modelled or
    # loaded. Quarantine them to a CSV and STOP — unless --allow-drop-orphans is given.
    orphans = ~fact["customer_id"].isin(set(customers["customer_id"]))
    add_stat("orphan_rows", int(orphans.sum()))
    if orphans.any():
        qpath = PROCESSED_DIR / "_quarantine_orphan_fact.csv"
        write_review_csv("_quarantine_orphan_fact.csv", fact.loc[orphans])
        log("clean", f"{int(orphans.sum())} FactInvoice row(s) reference a customer_id not in "
                     f"DimCustomer -> written to {qpath.name}")
        if allow_drop_orphans:
            fact = fact.loc[~orphans].reset_index(drop=True)
            log("clean", "  dropping them and continuing (--allow-drop-orphans)")
        else:
            raise ValueError(
                f"{int(orphans.sum())} orphan FactInvoice row(s) found. Review "
                f"data/processed/_quarantine_orphan_fact.csv and fix the source data, "
                f"or re-run with --allow-drop-orphans to drop them.")
    else:
        write_review_csv("_quarantine_orphan_fact.csv", fact.iloc[0:0])

    # 2c. GST integrity: source GST is trusted, but every row must satisfy the DB's
    # ck_gst_rule — gst is 0 (GST-free) OR ~= incl/11. Rows that are neither (an
    # irregular GST rate in the source) are quarantined and the pipeline STOPS,
    # unless --allow-drop-bad-gst is given.
    bad_gst = (fact["gst"] != 0) & \
              ((fact["gst"] - fact["amount_incl_gst"] / GST_DIVISOR).abs() > GST_RULE_TOL)
    add_stat("bad_gst_rows", int(bad_gst.sum()))
    if bad_gst.any():
        qpath = PROCESSED_DIR / "_quarantine_gst_fact.csv"
        write_review_csv("_quarantine_gst_fact.csv", fact.loc[bad_gst])
        log("clean", f"{int(bad_gst.sum())} FactInvoice row(s) have GST that is neither 0 "
                     f"(GST-free) nor ~= incl/11 -> written to {qpath.name}")
        if allow_drop_bad_gst:
            fact = fact.loc[~bad_gst].reset_index(drop=True)
            log("clean", "  dropping them and continuing (--allow-drop-bad-gst)")
        else:
            raise ValueError(
                f"{int(bad_gst.sum())} FactInvoice row(s) violate the GST rule (gst not 0 and "
                f"not ~= incl/11). Review data/processed/_quarantine_gst_fact.csv and fix the "
                f"source gst, or re-run with --allow-drop-bad-gst to drop them.")
    else:
        write_review_csv("_quarantine_gst_fact.csv", fact.iloc[0:0])

    export_unknown_mapping_warnings(customers, fact)
    add_stat("fact_rows_after_cleaning", len(fact))
    quality_report(fact)

    # 5 & 6. FORECAST
    fbi = generate_forecast_by_invoice(fact, customers)
    weekly = generate_forecast_weekly(fbi)
    log("forecast", f"forecast_by_invoice={len(fbi)} (overdue backlog "
                    f"{int(fbi['already_overdue'].sum())}) | forecast_weekly={len(weekly)} weeks")

    # 3. DimDate over every date used anywhere.
    all_dates = pd.concat([
        fact["issue_date"], fact["due_date"], fact["payment_date"],
        fbi["expected_collection"], weekly["week_start"],
    ])
    dim_date = build_dim_date(all_dates)
    log("dimdate", f"{len(dim_date)} days: {dim_date['date'].iloc[0]} -> {dim_date['date'].iloc[-1]}")

    tables = {
        "DimCustomer": customers, "DimCategory": category, "DimPaymentMethod": method,
        "DimInvoiceStatus": status, "DimDate": dim_date, "FactInvoice": fact,
        "forecast_by_invoice": fbi, "forecast_weekly": weekly,
    }

    # 4. VALIDATE relationships (hard gate).
    validate_relationships(tables)

    # 6b. COMPARE with the previous run's CSVs (before we overwrite them).
    compare_with_previous(tables)

    # 7. EXPORT
    export_processed(tables)

    # 8. (optional schema rebuild) + LOAD
    if skip_load:
        log("load", "skipped (--skip-load)")
        if rebuild_schema_flag:
            log("schema", "--rebuild-schema ignored because --skip-load was set (no DB connection)")
    else:
        engine = build_engine()
        try:
            if rebuild_schema_flag:
                rebuild_schema(engine)          # DROP + CREATE everything, then load below
            load_to_supabase(tables, engine)
        finally:
            engine.dispose()

    # 9. REPORT
    validation_report(tables)
    log("done", "pipeline complete.")


def main() -> None:
    global SNAPSHOT
    parser = argparse.ArgumentParser(description="AR & Cash-Flow Forecast automation pipeline.")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT,
                        help="Forecast 'as of' date, YYYY-MM-DD (default 2026-03-31).")
    parser.add_argument("--skip-load", action="store_true",
                        help="Run clean/forecast/export but do NOT load into the database.")
    parser.add_argument("--allow-drop-orphans", action="store_true",
                        help="Drop FactInvoice rows with an unknown customer_id instead of "
                             "stopping (they are always written to _quarantine_orphan_fact.csv).")
    parser.add_argument("--allow-drop-bad-gst", action="store_true",
                        help="Drop FactInvoice rows whose gst is neither 0 nor ~= incl/11 instead "
                             "of stopping (they are always written to _quarantine_gst_fact.csv).")
    parser.add_argument("--rebuild-schema", action="store_true",
                        help="DANGER: drop and recreate ALL tables/constraints/indexes from "
                             "sql/create_schema.sql before loading. First-time setup only. "
                             "Not run by default.")
    args = parser.parse_args()
    SNAPSHOT = args.snapshot

    try:
        run(skip_load=args.skip_load, allow_drop_orphans=args.allow_drop_orphans,
            allow_drop_bad_gst=args.allow_drop_bad_gst,
            rebuild_schema_flag=args.rebuild_schema)
    except Exception as exc:
        log("FATAL", str(exc))
        sys.exit(1)


# SNAPSHOT is set from the CLI in main(); this default keeps the module importable.
SNAPSHOT = DEFAULT_SNAPSHOT

if __name__ == "__main__":
    main()
