"""
pipeline.py — one command: raw six-table star in -> clean, joinable star out.

    python pipeline.py

Reads data/raw/*.csv, writes data/processed/*.csv (Power BI-ready).
Stops before writing if validation fails.
"""
import sys
import json
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import config as C
import clean
import build as B


def log(stage, msg):
    print(f"[{stage:<10}] {msg}")


def run():
    C.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    R = lambda n: pd.read_csv(C.RAW_DIR / n, dtype=str)

    # 1. LOAD (5 raw tables; DimDate is generated)
    raw_fact = R("FactInvoice_raw.csv")
    raw_cust = R("DimCustomer_raw.csv")
    raw_cat = R("DimCategory_raw.csv")
    raw_pm = R("DimPaymentMethod_raw.csv")
    raw_st = R("DimInvoiceStatus_raw.csv")
    log("load", f"fact={len(raw_fact)} customer={len(raw_cust)} +3 small dims")

    # 2. CLEAN  (customers first — fact needs their terms)
    customers = clean.clean_customers(raw_cust)
    fact = clean.clean_fact(raw_fact, customers)
    category = clean.clean_category(raw_cat)
    method = clean.clean_method(raw_pm)
    status = clean.clean_status(raw_st)
    log("clean", f"fact={len(fact)} customer={len(customers)} "
                 f"category={len(category)} method={len(method)} status={len(status)}")

    # 2b. QUARANTINE unmodelable rows (fact pointing to a non-existent customer)
    orphan_mask = ~fact["customer_id"].isin(set(customers["customer_id"]))
    if orphan_mask.any():
        fact.loc[orphan_mask].to_csv(C.PROCESSED_DIR / "_quarantine_orphan_fact.csv", index=False)
        log("quarantine", f"set aside {int(orphan_mask.sum())} fact row(s) with unknown customer_id "
                          f"-> _quarantine_orphan_fact.csv")
        fact = fact.loc[~orphan_mask].reset_index(drop=True)

    # 3. BUILD DimDate over the data's full date span (+buffer), then VALIDATE
    all_dates = pd.concat([pd.to_datetime(fact[c], dayfirst=True, errors="coerce")
                           for c in ["issue_date", "due_date", "payment_date"]])
    dim_date = B.build_dim_date(all_dates.min(), all_dates.max() + pd.Timedelta(days=2))
    rep = B.validate(fact, customers, category, method, status, dim_date)
    log("validate", f"status={rep['status']}")
    for k, v in rep.items():
        if k not in ("status", "errors"):
            log("validate", f"  {k}: {v}")
    if rep["status"] == "FAIL":
        for e in rep["errors"]:
            log("validate", f"  ERROR: {e}")
        (C.PROCESSED_DIR / "_validation_report.json").write_text(json.dumps(rep, indent=2))
        log("ABORT", "validation failed — nothing written.")
        sys.exit(1)

    # 4. WRITE the six joinable tables
    outputs = {
        "FactInvoice.csv": fact, "DimCustomer.csv": customers, "DimDate.csv": dim_date,
        "DimCategory.csv": category, "DimPaymentMethod.csv": method, "DimInvoiceStatus.csv": status,
    }
    for name, df in outputs.items():
        df.to_csv(C.PROCESSED_DIR / name, index=False)
    rep["outputs"] = list(outputs)
    (C.PROCESSED_DIR / "_validation_report.json").write_text(json.dumps(rep, indent=2))
    log("write", f"{len(outputs)} tables -> {C.PROCESSED_DIR}")
    log("done", "star is clean, joinable, and Power BI-ready.")


if __name__ == "__main__":
    run()
