# AR & Cash-Flow Forecast Pipeline

One command turns raw exports into a clean, validated star schema **plus** an
invoice-level and weekly cash-flow forecast, then loads everything into
Supabase/PostgreSQL for Power BI (Import mode).

```
raw CSVs  ->  clean + validate  ->  forecast  ->  DimDate  ->  referential check
          ->  export processed CSVs  ->  truncate + load to Supabase  ->  report
```

## Folder structure

```
ar_star/
├── pipeline.py           # the complete automation script (run this)
├── pipeline_legacy.py    # previous clean-only version, kept for reference
├── requirements.txt
├── .env.template         # copy to .env and fill in DB credentials
├── .env                  # (you create this; never commit it)
├── README.md
├── config.py             # legacy module used by pipeline_legacy.py only
├── src/                  # legacy modules used by pipeline_legacy.py only
├── sql/
│   └── create_schema.sql # DROP+CREATE tables/constraints/indexes (--rebuild-schema)
└── data/
    ├── raw/              # INPUT — put the 5 raw CSVs here
    │   ├── FactInvoice_raw.csv
    │   ├── DimCustomer_raw.csv
    │   ├── DimCategory_raw.csv
    │   ├── DimPaymentMethod_raw.csv
    │   └── DimInvoiceStatus_raw.csv
    └── processed/        # OUTPUT — 8 clean CSVs written here
        ├── DimCustomer.csv        ├── DimDate.csv
        ├── DimCategory.csv        ├── FactInvoice.csv
        ├── DimPaymentMethod.csv   ├── forecast_by_invoice.csv
        ├── DimInvoiceStatus.csv   └── forecast_weekly.csv
```

`DimDate` is **generated**, not read from `raw/`.

## Install

```bash
pip install -r requirements.txt
```

## Configure the database

```bash
cp .env.template .env      # Windows: copy .env.template .env
```

Then edit `.env`. The template uses the Supabase **transaction pooler** (Project
Settings → Database → Connection string → Transaction pooler), where the user is
`postgres.<project-ref>`:

```
SUPABASE_DB_HOST=aws-0-<region>.pooler.supabase.com
SUPABASE_DB_PORT=5432
SUPABASE_DB_NAME=postgres
SUPABASE_DB_USER=postgres.<your-project-ref>
SUPABASE_DB_PASSWORD=<your-database-password>
```

Credentials are read from `.env` at runtime — nothing is hardcoded. Keep `.env`
out of git (it holds your real password).

## Run

```bash
# First-time setup — creates the tables, then loads (drops any existing tables!):
python pipeline.py --rebuild-schema

# Normal refresh — reuses the existing tables, truncates + reloads:
python pipeline.py

# Safe local test — clean/forecast/export only, no database connection:
python pipeline.py --skip-load
```

Other flags:

```bash
python pipeline.py --snapshot 2026-03-31   # override the forecast "as of" date
python pipeline.py --allow-drop-orphans    # drop (don't stop on) unknown-customer fact rows
python pipeline.py --allow-drop-bad-gst    # drop (don't stop on) irregular-GST fact rows
```

> ⚠️ **`--rebuild-schema` DROPs and recreates every table** (from
> `sql/create_schema.sql`) before loading — all existing rows are lost. Use it for
> first-time setup or a deliberate reset only. It is **never** run by default, and it
> is ignored when combined with `--skip-load`.

By default the pipeline **stops** if any FactInvoice row references a `customer_id`
that isn't in DimCustomer — those rows are written to
`data/processed/_quarantine_orphan_fact.csv` for review. Add `--allow-drop-orphans`
to drop them and continue instead.

After a successful run, open Power BI and **Refresh** (Import mode).

## How it works (stage by stage)

1. **Load raw** — reads the 5 raw CSVs as text so nothing is silently coerced.
   Text placeholders `null`/`None`/`NaN`/`""` become real `NaN`.

2. **Clean** (returns DB-ready frames — **integer keys, ISO `YYYY-MM-DD` dates,
   numeric money**):
   - Keys `INV-1001`, `CUST001`, `CAT01`… are stripped to integers to match the
     Supabase schema (`invoice_id`, `customer_id`, … are `integer`).
   - Money: `$4,235.00` → `4235.0`; GST recomputed as `incl / 11`,
     `excl = incl − gst`.
   - `status` → {Paid, Open, Overdue}; blanks derived from payment/due dates.
   - `member_tier` → {Associate, Standard, Premium, Corporate}; blanks derived
     from `credit_limit`.
   - **GST is taken from the source, not recalculated.** A blank/missing GST means
     the invoice is **GST-free** (`gst = 0`). `amount_excl_gst` is set to `incl − gst`
     so `incl = excl + gst` always holds. Rows whose GST is **neither 0 nor ≈ incl/11**
     (an irregular rate in the source) are written to `_quarantine_gst_fact.csv` and
     the pipeline **stops** — this matches the DB `ck_gst_rule` so nothing that would
     be rejected on load slips through. Override with `--allow-drop-bad-gst` to drop
     them and continue.
   - `payment_terms_days` forced to 14 or 30 (by tier if invalid).
   - `due_date`: **the raw due date is kept** whenever it is present and not earlier
     than the issue date. It is only recomputed (`issue_date + terms`) when it is
     blank, unparseable, or earlier than issue. The run logs how many were kept vs
     recomputed.
   - Paid invoices get a `payment_date` and `amount_paid > 0`; unpaid invoices
     get blank `payment_date` and `amount_paid = 0`.
   - Duplicates dropped on primary keys.
   - **Orphan fact rows** (unknown `customer_id`) are written to
     `_quarantine_orphan_fact.csv` and the pipeline **stops** (override with
     `--allow-drop-orphans`).
   - Non-fatal quality checks printed: GST rule, `incl ≈ excl + gst`, `due ≥ issue`.

3. **Forecast — `forecast_by_invoice`** (one row per unpaid invoice). This follows a
   deliberate, documented method:
   - **Lag is measured against the DUE date**, not the issue date:
     `lag = payment_date − due_date` (positive = late, negative = early). Collection
     timing is driven by the payment deadline, so this is comparable across 14- and
     30-day terms.
   - **Median vs mean:** both are computed on the paid history and their divergence
     is logged. The default `LAG_STAT = "median"` because a few very-late payers make
     the mean unrepresentative (on this data mean ≈ 2.4 vs median = 1.0 → clear skew).
     Set `LAG_STAT = "mean"` at the top of `pipeline.py` if your existing dashboard
     was built on averages.
   - **Fallback ladder** (with the source tracked so you can report how much of the
     forecast rests on real member history):
     - **member** — customer's own lag, if it has ≥ `MIN_CUSTOMER_HISTORY` paid invoices (default 3);
     - **tier** — the member_tier lag, if ≥ `MIN_TIER_HISTORY` (default 3);
     - **overall** — the whole-book lag otherwise.
     The run prints the member/tier/overall percentage mix.
     The run prints the member/tier/overall percentage mix (≈57% / 43% / 0% here).
   - `lag_used` stores the numeric lag; `lag_source` records which rule fired.
   - `expected_collection = normalize(due_date + lag_used)` (normalised to a whole day
     so fractional medians like 1.5 don't land mid-day and corrupt week bucketing).
   - **Overdue handling — the headline caveat (DECISIONS.md D4):** `already_overdue`
     is set when the *projection itself* lands before the first forecast day
     (`normalize(due + lag) < snapshot + 1`), not merely when the due date is past.
     Those invoices are **clamped into the first forecast week** and flagged, so week 1
     can be split into genuine flow vs pulled-forward overdue backlog rather than
     silently inflating. This is the single biggest modelling assumption; on this data
     ~42% of the book (week 1 is ~77% backlog).
   - Snapshot date is configurable (`--snapshot`, default **2026-03-31**);
     `forecast_start = snapshot + 1 day`.

4. **Forecast — `forecast_weekly`** (rolled up from `forecast_by_invoice`):
   - `week_start` = Monday of the week of `expected_collection`.
   - `normal_flow` = Σ amount where **not** already_overdue;
     `overdue_backlog` = Σ amount where already_overdue; `amount` = their sum.
   - `cumulative` = running total by week; `pct_of_book` = cumulative / grand total × 100.
   - Money rounded to 2 dp, `pct_of_book` to 2 dp.

5. **DimDate** — generated to span every date used by FactInvoice
   (issue/due/payment), `forecast_by_invoice.expected_collection` and
   `forecast_weekly.week_start`. Columns: `date_key` (YYYYMMDD int), `date`,
   `year`, `quarter`, `month_no`, `month_name`.

6. **Referential validation (hard gate)** — before anything is written or loaded:
   every FactInvoice `customer_id`/`category`/`status`/`payment_method` (unless
   blank) and every `issue_date`/`due_date`/non-blank `payment_date` must exist in
   its dimension; every `forecast_by_invoice` `customer_id`/`status`/
   `expected_collection` must exist too. Any miss aborts the run.

7. **Compare with previous run** — before overwriting anything, if processed CSVs
   already exist the pipeline prints **old → new** row counts per table and the key
   money totals (total invoiced, forecast total by invoice, forecast total weekly),
   flagging what changed. This lets you see whether the regenerated forecast differs
   from the data currently behind your dashboard. Then the 8 clean CSVs are written
   to `data/processed/`.

8. **(Optional) rebuild schema, then load to Supabase** — via SQLAlchemy
   (`postgresql+psycopg2`).
   - With `--rebuild-schema`, `rebuild_schema()` first runs `sql/create_schema.sql`
     (DROP + CREATE of all tables, constraints, and indexes). The SQL file wraps
     itself in `BEGIN/COMMIT`, so it applies atomically.
   - Then the load runs inside **one transaction**: `TRUNCATE` all tables
     (children → parents) then insert all tables (parents → children). Any failure
     rolls the whole load back, leaving the database unchanged. Skipped with
     `--skip-load`.
   - The cleaning rules are aligned with the schema's CHECK constraints (GST rule,
     `incl = excl + gst`, `due ≥ issue`, the paid/unpaid payment rule, tier/terms
     value sets), so validated output loads without constraint errors.

9. **Validation report** — row counts per table, total invoiced, open-AR total,
   forecast total from both forecast tables, and a **hard assertion** that
   `forecast_by_invoice` and `forecast_weekly` totals agree within tolerance.

## Tuning

All knobs live at the top of `pipeline.py` under **CONFIGURATION**:
`DEFAULT_SNAPSHOT`, `LAG_STAT` (median/mean), `MIN_CUSTOMER_HISTORY`,
`MIN_TIER_HISTORY`, `GST_DIVISOR`, tolerances, canonical value maps, and
load/truncate order.

Sanity check the shape (DECISIONS.md Step 6): the projected amounts must reconcile
to open AR (asserted automatically), and cash should be front-loaded near the
snapshot and tail off. On this data ~98% collects within ~4 weeks, peaking in week 1
(amplified by the D4 overdue-backlog clamp); a thin tail into late 2026 comes from a
few members whose own historical lag is genuinely long — expected, not a bug.

## Notes

- Keys are emitted as **integers** and dates as **ISO `YYYY-MM-DD`** so the CSVs
  load directly into the Supabase column types you defined. (The older
  `pipeline_legacy.py` emitted string keys + `d/m/Y` — kept only for reference.)
- The forecast logic mirrors the standalone `step1`–`step7` scripts and
  `DECISIONS.md`: lag = `payment_date − due_date` (D1), **median** at every level (D2),
  member→tier→overall fallback with **N=3** and no tier count threshold (D3), and the
  **projection-based** `already_overdue` clamp into week 1 (D4). `LAG_STAT` and
  `MIN_CUSTOMER_HISTORY` at the top of `pipeline.py` expose the D2/D3 knobs.
- `forecast_weekly` is Monday-anchored and reindexed to a **continuous** weekly range,
  so quiet weeks appear as explicit zero rows (matches `step5`).
