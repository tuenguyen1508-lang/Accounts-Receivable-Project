"""build.py — DimDate generator + validation (referential integrity, GST, window)."""
import pandas as pd
import config as C


def build_dim_date(start, end):
    dr = pd.date_range(start, end, freq="D")
    d = pd.DataFrame({"date": dr})
    x = d["date"]
    d["date_key"] = x.dt.strftime("%Y%m%d").astype(int)
    d["year"] = x.dt.year
    d["quarter"] = "Q" + x.dt.quarter.astype(str)
    d["month_no"] = x.dt.month
    d["month_name"] = x.dt.strftime("%b")
    d["month_year"] = x.dt.strftime("%b %Y")
    d["fin_year"] = "FY" + (x.dt.year + (x.dt.month >= 7).astype(int)).astype(str).str[-2:]
    d["fin_qtr"] = x.dt.month.map({7: "FQ1", 8: "FQ1", 9: "FQ1", 10: "FQ2", 11: "FQ2", 12: "FQ2",
                                   1: "FQ3", 2: "FQ3", 3: "FQ3", 4: "FQ4", 5: "FQ4", 6: "FQ4"})
    d["day_of_week"] = x.dt.strftime("%a")
    d["is_weekend"] = x.dt.dayofweek.isin([5, 6])
    d["is_month_end"] = x.dt.is_month_end
    d["date"] = x.dt.strftime(C.DATE_FMT)
    return d[["date_key", "date", "year", "quarter", "month_no", "month_name", "month_year",
              "fin_year", "fin_qtr", "day_of_week", "is_weekend", "is_month_end"]]


def validate(fact, customers, category, method, status, dim_date):
    errors, rep = [], {}
    dates = set(dim_date["date"])

    def orphans(series, valid, mask=None):
        s = series if mask is None else series[mask]
        return int((~s.isin(valid)).sum())

    rep["fact->customer orphans"] = orphans(fact["customer_id"], set(customers["customer_id"]))
    rep["fact->category orphans"] = orphans(fact["category"], set(category["category"]))
    rep["fact->status orphans"] = orphans(fact["status"], set(status["status"]))
    paid = fact["payment_method"].notna() & fact["payment_method"].astype(str).str.strip().ne("")
    rep["fact->method orphans (paid)"] = orphans(fact["payment_method"], set(method["payment_method"]), paid)
    for col in ["issue_date", "due_date", "payment_date"]:
        nb = fact[col].notna() & fact[col].astype(str).str.strip().ne("")
        rep[f"{col}->DimDate orphans"] = orphans(fact[col], dates, nb)
    for k, v in rep.items():
        if "orphans" in k and v > 0:
            errors.append(f"{k}: {v}")

    inc = fact["amount_incl_gst"]
    rep["gst rule pass"] = int(((fact["gst"] - inc / C.GST_DIVISOR).abs() <= 0.02).sum())
    rep["gst rule total"] = len(fact)
    if rep["gst rule pass"] != len(fact):
        errors.append("GST rule fails on some rows")

    iss = pd.to_datetime(fact["issue_date"], dayfirst=True, errors="coerce")
    rep["issue dates outside Q3"] = int((~iss.between(C.PERIOD_START, C.PERIOD_END)).sum())
    if rep["issue dates outside Q3"]:
        errors.append("issue dates outside Q3")

    rep["total invoiced"] = round(float(fact["amount_incl_gst"].sum()), 2)
    rep["total collected"] = round(float(fact.loc[fact["status"].eq("Paid"), "amount_incl_gst"].sum()), 2)
    rep["open AR"] = round(float(fact.loc[fact["status"].ne("Paid"), "amount_incl_gst"].sum()), 2)

    rep["status"] = "PASS" if not errors else "FAIL"
    rep["errors"] = errors
    return rep
