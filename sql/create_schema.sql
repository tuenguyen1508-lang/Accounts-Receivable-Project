-- ============================================================================
-- AR & Cash Flow Star Schema
-- Supabase / PostgreSQL
-- Table names matched to Power BI:
-- DimCategory, DimCustomer, DimDate, DimInvoiceStatus,
-- DimPaymentMethod, FactInvoice, forecast_by_invoice, forecast_weekly
-- ============================================================================

BEGIN;

-- Drop child/detail tables first
DROP TABLE IF EXISTS forecast_weekly CASCADE;
DROP TABLE IF EXISTS forecast_by_invoice CASCADE;
DROP TABLE IF EXISTS "FactInvoice" CASCADE;

-- Drop dimension tables after fact/forecast tables
DROP TABLE IF EXISTS "DimCustomer" CASCADE;
DROP TABLE IF EXISTS "DimCategory" CASCADE;
DROP TABLE IF EXISTS "DimPaymentMethod" CASCADE;
DROP TABLE IF EXISTS "DimInvoiceStatus" CASCADE;
DROP TABLE IF EXISTS "DimDate" CASCADE;

-- ============================================================================
-- DIMENSION TABLES
-- ============================================================================

CREATE TABLE "DimCustomer" (
    customer_id        integer PRIMARY KEY,
    customer_name      text NOT NULL,
    industry           text NOT NULL,
    member_tier        text NOT NULL
        CHECK (member_tier IN ('Associate', 'Standard', 'Premium', 'Corporate')),
    payment_terms_days smallint NOT NULL
        CHECK (payment_terms_days IN (14, 30)),
    credit_limit       integer NOT NULL
        CHECK (credit_limit > 0),
    member_since       date,
    state              text
);

CREATE TABLE "DimCategory" (
    category_id   integer PRIMARY KEY,
    category      text NOT NULL UNIQUE,
    revenue_group text NOT NULL
);

CREATE TABLE "DimPaymentMethod" (
    method_id      integer PRIMARY KEY,
    payment_method text NOT NULL UNIQUE,
    method_type    text NOT NULL
);

CREATE TABLE "DimInvoiceStatus" (
    status_id   integer PRIMARY KEY,
    status      text NOT NULL UNIQUE
        CHECK (status IN ('Paid', 'Open', 'Overdue')),
    description text
);

CREATE TABLE "DimDate" (
    date_key       integer NOT NULL UNIQUE,
    date           date PRIMARY KEY,
    year           smallint NOT NULL,
    quarter        text NOT NULL,
    month_no       smallint NOT NULL
        CHECK (month_no BETWEEN 1 AND 12),
    month_name     text NOT NULL
);

-- ============================================================================
-- MAIN FACT TABLE: ACTUAL INVOICES
-- ============================================================================

CREATE TABLE "FactInvoice" (
    invoice_id      integer PRIMARY KEY,

    customer_id     integer NOT NULL
        REFERENCES "DimCustomer"(customer_id),

    category        text NOT NULL
        REFERENCES "DimCategory"(category),

    payment_method  text
        REFERENCES "DimPaymentMethod"(payment_method),

    status          text NOT NULL
        REFERENCES "DimInvoiceStatus"(status),

    issue_date      date NOT NULL
        REFERENCES "DimDate"(date),

    due_date        date NOT NULL
        REFERENCES "DimDate"(date),

    payment_date    date
        REFERENCES "DimDate"(date),

    amount_excl_gst numeric(12,2) NOT NULL
        CHECK (amount_excl_gst >= 0),

    gst             numeric(12,2) NOT NULL
        CHECK (gst >= 0),

    amount_incl_gst numeric(12,2) NOT NULL
        CHECK (amount_incl_gst >= 0),

    amount_paid     numeric(12,2) NOT NULL DEFAULT 0,

    -- GST should be approximately 1/11 of GST-inclusive amount
    CONSTRAINT ck_gst_rule
        CHECK (abs(gst - amount_incl_gst / 11.0) <= 0.02),

    -- Amount including GST should equal amount excluding GST plus GST
    CONSTRAINT ck_incl_sum
        CHECK (abs(amount_incl_gst - (amount_excl_gst + gst)) <= 0.02),

    -- Due date cannot be before issue date
    CONSTRAINT ck_due_after_issue
        CHECK (due_date >= issue_date),

    -- Paid invoices must have payment date and amount paid
    -- Open/Overdue invoices must not have payment date and amount paid
    CONSTRAINT ck_payment_only_when_paid
        CHECK (
            (
                status = 'Paid'
                AND payment_date IS NOT NULL
                AND amount_paid > 0
            )
            OR
            (
                status <> 'Paid'
                AND payment_date IS NULL
                AND amount_paid = 0
            )
        )
);

-- ============================================================================
-- FORECAST DETAIL TABLE: ONE ROW PER FORECAST INVOICE
-- Relationships:
--   DimCustomer[customer_id]      → forecast_by_invoice[customer_id]
--   DimInvoiceStatus[status]      → forecast_by_invoice[status]
--   DimDate[date]                 → forecast_by_invoice[expected_collection]
-- ============================================================================

CREATE TABLE forecast_by_invoice (
    invoice_id            integer PRIMARY KEY,
    customer_id           integer NOT NULL,
    customer_name         text NOT NULL,
    member_tier           text NOT NULL,
    category              text NOT NULL,
    status                text NOT NULL,
    issue_date            date NOT NULL,
    due_date              date NOT NULL,
    amount_incl_gst       numeric(12,3) NOT NULL,
    lag_used              numeric(8,2),
    lag_source            text,
    expected_collection   date NOT NULL,
    already_overdue       boolean NOT NULL,

    CONSTRAINT fk_forecast_customer
        FOREIGN KEY (customer_id)
        REFERENCES "DimCustomer"(customer_id),

    CONSTRAINT fk_forecast_status
        FOREIGN KEY (status)
        REFERENCES "DimInvoiceStatus"(status),

    CONSTRAINT ck_forecast_amount_positive
        CHECK (amount_incl_gst >= 0)
);

-- ============================================================================
-- FORECAST WEEKLY TABLE: WEEKLY SUMMARY
-- This table is standalone in Power BI.
-- No foreign key to DimDate, because your forecast dates may extend beyond
-- the current DimDate range.
-- ============================================================================

CREATE TABLE forecast_weekly (
    week_start       date PRIMARY KEY,
    n_invoices       integer NOT NULL
        CHECK (n_invoices >= 0),
    normal_flow      numeric(14,2) NOT NULL
        CHECK (normal_flow >= 0),
    overdue_backlog  numeric(14,2) NOT NULL
        CHECK (overdue_backlog >= 0),
    amount           numeric(14,2) NOT NULL
        CHECK (amount >= 0),
    cumulative       numeric(14,2) NOT NULL
        CHECK (cumulative >= 0),
    pct_of_book      numeric(5,2) NOT NULL
        CHECK (pct_of_book >= 0 AND pct_of_book <= 100)
);

-- ============================================================================
-- INDEXES
-- These improve filtering, joins, and Power BI refresh/query performance
-- ============================================================================

-- FactInvoice indexes
CREATE INDEX ix_factinvoice_status
ON "FactInvoice"(status);

CREATE INDEX ix_factinvoice_customer
ON "FactInvoice"(customer_id);

CREATE INDEX ix_factinvoice_category
ON "FactInvoice"(category);

CREATE INDEX ix_factinvoice_payment_method
ON "FactInvoice"(payment_method);

CREATE INDEX ix_factinvoice_issue_date
ON "FactInvoice"(issue_date);

CREATE INDEX ix_factinvoice_due_date
ON "FactInvoice"(due_date);

CREATE INDEX ix_factinvoice_payment_date
ON "FactInvoice"(payment_date);

-- DimCustomer indexes
CREATE INDEX ix_dimcustomer_member_tier
ON "DimCustomer"(member_tier);

CREATE INDEX ix_dimcustomer_industry
ON "DimCustomer"(industry);

-- DimDate indexes
CREATE INDEX ix_dimdate_year
ON "DimDate"(year);

CREATE INDEX ix_dimdate_quarter
ON "DimDate"(quarter);

CREATE INDEX ix_dimdate_month_no
ON "DimDate"(month_no);

-- Forecast detail indexes
CREATE INDEX ix_forecast_by_invoice_customer
ON forecast_by_invoice(customer_id);

CREATE INDEX ix_forecast_by_invoice_status
ON forecast_by_invoice(status);

CREATE INDEX ix_forecast_by_invoice_expected_collection
ON forecast_by_invoice(expected_collection);

-- Forecast weekly indexes
CREATE INDEX ix_forecast_weekly_week_start
ON forecast_weekly(week_start);

COMMIT;