"""Configuration for the single-fact AR star pipeline. All rules in one place."""
from pathlib import Path

ROOT = Path(__file__).parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"

PERIOD_START = "2026-01-01"
PERIOD_END   = "2026-03-31"
SNAPSHOT     = "2026-03-31"
DATE_FMT     = "%d/%m/%Y"        # output format (Australian)
GST_DIVISOR  = 11               # GST = incl / 11  (10%)

TERMS_BY_TIER = {"Corporate": 30, "Premium": 30, "Standard": 14, "Associate": 14}
CREDIT_LIMIT_TO_TIER = {2000: "Associate", 5000: "Standard", 10000: "Premium", 20000: "Corporate"}

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y"]

CATEGORY_CANON = {
    "membership income": "Membership Income", "events income": "Events Income",
    "event sponsorship": "Event Sponsorship", "major sponsorship": "Major Sponsorship",
    "document signing revenue": "Document Signing Revenue",
    "sponsorship & advertising": "Sponsorship & Advertising",
}
INDUSTRY_CANON = {
    "healthcare": "Healthcare", "health care": "Healthcare", "legal": "Legal", "law": "Legal",
    "finance": "Finance", "technology": "Technology", "tech": "Technology", "it/technology": "Technology",
    "construction": "Construction", "construction/trades": "Construction", "government": "Government",
    "govt": "Government", "hospitality": "Hospitality", "food & hospitality": "Hospitality",
    "not-for-profit": "Not-for-profit", "not for profit": "Not-for-profit", "nfp": "Not-for-profit",
    "professional services": "Professional Services", "prof services": "Professional Services",
    "recruitment": "Recruitment", "recruiting": "Recruitment", "education": "Education",
    "edu": "Education", "retail": "Retail",
}
TIER_CANON = {"std": "Standard", "standard": "Standard", "prem": "Premium", "premium": "Premium",
              "assoc": "Associate", "associate": "Associate", "corp": "Corporate", "corporate": "Corporate"}
METHOD_CANON = {"eft": "EFT", "e.f.t.": "EFT", "electronic funds transfer": "EFT", "cc": "Credit Card",
                "credit card": "Credit Card", "creditcard": "Credit Card", "dd": "Direct Debit",
                "direct debit": "Direct Debit", "direct-debit": "Direct Debit", "bpay": "BPAY", "b-pay": "BPAY"}
STATUS_CANON = {"paid": "Paid", "open": "Open", "overdue": "Overdue"}
METHOD_TYPE = {"EFT": "Electronic", "Direct Debit": "Electronic", "BPAY": "Electronic", "Credit Card": "Card"}
REVENUE_GROUP = {"Membership Income": "Membership", "Events Income": "Events",
                 "Event Sponsorship": "Sponsorship", "Major Sponsorship": "Sponsorship",
                 "Sponsorship & Advertising": "Sponsorship", "Document Signing Revenue": "Other",
                 "Unknown": "Unknown"}
