"""clean.py - encodes every cleaning decision approved interactively, per table."""
import re
import numpy as np
import pandas as pd
import config as C


def to_number(s):
    if pd.isna(s) or str(s).strip() == "":
        return np.nan
    return pd.to_numeric(str(s).replace("$", "").replace(",", "").strip(), errors="coerce")


def parse_date(s):
    if pd.isna(s) or str(s).strip() == "":
        return pd.NaT
    s = str(s).strip()
    for fmt in C.DATE_FORMATS:
        try:
            return pd.to_datetime(s, format=fmt)
        except (ValueError, TypeError):
            continue
    return pd.NaT


def norm_customer_id(s):
    d = re.sub(r"\D", "", str(s))
    return f"CUST{int(d):03d}" if d else np.nan


def _canon(v, mapping, missing="Unknown"):
    if pd.isna(v) or str(v).strip() == "":
        return missing
    return mapping.get(str(v).strip().lower(), str(v).strip().title())


def _swap_into_window(ts):
    """Reverse a d/m vs m/d misread: swap day<->month to land inside the period."""
    try:
        return pd.Timestamp(year=ts.year, month=ts.day, day=ts.month)
    except ValueError:
        return ts


def clean_customers(df):
    df = df.copy()
    df["customer_id"] = df["customer_id"].apply(norm_customer_id)
    df = df.drop_duplicates("customer_id", keep="first").reset_index(drop=True)          # Uniqueness
    df["credit_limit"] = df["credit_limit"].apply(to_number)                              # Validity
    df["payment_terms_days"] = df["payment_terms_days"].apply(
        lambda s: to_number(str(s).lower().replace("days", "")) if pd.notna(s) else np.nan)
    member_since = df["member_since"].apply(parse_date)
    miss = df["member_tier"].isna() | df["member_tier"].astype(str).str.strip().eq("")    # Completeness
    df.loc[miss, "member_tier"] = df.loc[miss, "credit_limit"].map(
        lambda v: C.CREDIT_LIMIT_TO_TIER.get(int(v)) if pd.notna(v) else np.nan)
    df["industry"] = df["industry"].apply(lambda v: _canon(v, C.INDUSTRY_CANON))          # Consistency
    df["member_tier"] = df["member_tier"].apply(lambda v: _canon(v, C.TIER_CANON, missing=np.nan))
    df["state"] = df["state"].apply(
        lambda v: "ACT" if "act" in str(v).lower().replace(".", "") or "capital territory" in str(v).lower()
        else str(v).strip())
    df["customer_name"] = df["customer_name"].astype(str).str.strip()
    df["payment_terms_days"] = df.apply(
        lambda r: C.TERMS_BY_TIER.get(r["member_tier"], 14) if pd.isna(r["payment_terms_days"])
        else int(r["payment_terms_days"]), axis=1).astype("Int64")
    df["credit_limit"] = df["credit_limit"].astype("Int64")
    df["member_since"] = member_since.dt.strftime(C.DATE_FMT)
    return df


def clean_fact(df, customers):
    df = df.copy()
    df = df.drop_duplicates().reset_index(drop=True)                                      # Uniqueness
    df["customer_id"] = df["customer_id"].apply(norm_customer_id)
    for c in ["amount_excl_gst", "gst", "amount_incl_gst", "amount_paid"]:               # Validity
        df[c] = df[c].apply(to_number)
    issue = df["issue_date"].apply(parse_date)
    pay = df["payment_date"].apply(parse_date)
    # status (Completeness) - derive blanks from payment + due
    due_raw = df["due_date"].apply(parse_date)
    snap = pd.Timestamp(C.SNAPSHOT)
    is_paid = pay.notna() | (df["amount_paid"].fillna(0) > 0)
    blank = df["status"].isna() | df["status"].astype(str).str.strip().eq("")
    df.loc[blank, "status"] = [
        "Paid" if is_paid[i] else ("Overdue" if (pd.notna(due_raw[i]) and due_raw[i] < snap) else "Open")
        for i in df.index[blank]]
    df["status"] = df["status"].apply(lambda v: _canon(v, C.STATUS_CANON, missing="Open"))
    df["category"] = df["category"].apply(lambda v: _canon(v, C.CATEGORY_CANON))         # Consistency
    df["payment_method"] = df["payment_method"].apply(
        lambda v: v if (pd.isna(v) or str(v).strip() == "") else C.METHOD_CANON.get(str(v).strip().lower(), str(v).strip()))
    # Accuracy - GST recompute; pull dates into window; due = issue + terms
    df["gst"] = (df["amount_incl_gst"] / C.GST_DIVISOR).round(2)
    df["amount_excl_gst"] = (df["amount_incl_gst"] - df["gst"]).round(2)
    ps, pe = pd.Timestamp(C.PERIOD_START), pd.Timestamp(C.PERIOD_END)
    issue = issue.apply(lambda d: _swap_into_window(d) if pd.notna(d) and not (ps <= d <= pe) else d)
    pay = pay.apply(lambda d: _swap_into_window(d) if pd.notna(d) and d > snap else d)
    terms = customers.set_index("customer_id")["payment_terms_days"].to_dict()
    tdays = df["customer_id"].map(lambda c: terms.get(c, 14)).fillna(14).astype(int)
    due = issue + pd.to_timedelta(tdays, unit="D")
    df["amount_paid"] = np.where(df["status"].eq("Paid"), df["amount_incl_gst"], 0.0).round(2)
    df["issue_date"] = issue.dt.strftime(C.DATE_FMT)
    df["due_date"] = due.dt.strftime(C.DATE_FMT)
    df["payment_date"] = pay.dt.strftime(C.DATE_FMT)
    return df


def clean_category(df):
    df = df.drop_duplicates().reset_index(drop=True)
    df["category"] = df["category"].apply(lambda v: _canon(v, C.CATEGORY_CANON))
    blank = df["revenue_group"].isna() | df["revenue_group"].astype(str).str.strip().eq("")
    df.loc[blank, "revenue_group"] = df.loc[blank, "category"].map(C.REVENUE_GROUP)
    if "Unknown" not in set(df["category"]):                                             # ensure Unknown joins
        df = pd.concat([df, pd.DataFrame([{"category_id": "CAT00", "category": "Unknown",
                                           "revenue_group": "Unknown"}])], ignore_index=True)
    return df


def clean_method(df):
    df = df.drop_duplicates().reset_index(drop=True)
    df["payment_method"] = df["payment_method"].apply(
        lambda v: C.METHOD_CANON.get(str(v).strip().lower(), str(v).strip()))
    blank = df["method_type"].isna() | df["method_type"].astype(str).str.strip().eq("")
    df.loc[blank, "method_type"] = df.loc[blank, "payment_method"].map(C.METHOD_TYPE)
    return df


def clean_status(df):
    df = df.drop_duplicates().reset_index(drop=True)
    df["status"] = df["status"].apply(lambda v: _canon(v, C.STATUS_CANON))
    return df
