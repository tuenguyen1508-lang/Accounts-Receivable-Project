# Accounts Receivable & Cash Flow Analysis

## Overview

Accounts receivable is a key part of short-term cash management. Organisations need to understand how much they are owed, which customers or revenue streams create collection risk, whether overdue balances are recoverable, and when outstanding invoices are likely to convert into cash.

This project analyses the accounts receivable position of **Molonglo Business Guild** for **Q3 FY26: January–March 2026**. It combines **Python**, **Supabase/PostgreSQL**, **Power BI**, and **AI-assisted reporting workflows** to create an end-to-end finance analytics solution covering data cleaning, database modelling, dashboard reporting, debtor risk analysis, short-term cash flow forecasting, and business insight generation.

In addition to the technical pipeline, I developed structured Claude skills to support the generation of an analytical report and consultant-style slide deck. These AI-assisted outputs were grounded in the Power BI dashboard findings and used to translate technical results into clear business insights and recommendations.

The Claude skills were developed as reusable reporting assets aligned with key business requirements and reporting standards. Each time the report or slide deck was generated, the instructions were refined to improve structure, accuracy, business storytelling, and recommendation quality. This makes the workflow adaptable to other datasets and analytics projects, where the same skills can be reused to generate consistent analysis reports and presentation decks.

The analysis shows that the Guild’s cash position is broadly sound. The main issue is not widespread collection failure. Instead, the key risks are structural: sponsorship receivables are highly concentrated, and credit limits are not fully aligned with sponsorship-scale invoices.

---
## Dataset

This project uses a synthetic accounts receivable dataset modelled on a realistic accounting export (Xero) for a membership-based organisation. The dataset represents invoice, customer, category, payment, status, and date information for **Molonglo Business Guild** during **Q3 FY26: 1 January 2026 – 31 March 2026**.

The dataset was designed to simulate a real-world finance reporting workflow where a business needs to monitor outstanding receivables, overdue balances, debtor risk, collection performance, and expected cash inflows.

### Main Dataset Tables

#### FactInvoice

The main transaction table containing invoice-level records.

Key fields include:

- `invoice_id`
- `customer_id`
- `category`
- `payment_method`
- `status`
- `issue_date`
- `due_date`
- `payment_date`
- `amount_excl_gst`
- `gst`
- `amount_incl_gst`
- `amount_paid`

This table is used to calculate total invoiced amount, collected amount, open receivables, overdue receivables, collection rate, aging buckets, payment lag, and debtor risk.

---

#### DimCustomer

Contains customer/member-level information.

Key fields include:

- `customer_id`
- `customer_name`
- `industry`
- `member_tier`
- `payment_terms_days`
- `credit_limit`
- `member_since`
- `state`

This table supports analysis by member tier, industry, credit-limit exposure, and customer-level debtor risk.

---

#### DimCategory

Contains invoice category and revenue group information.

Key fields include:

- `category_id`
- `category`
- `revenue_group`

This table supports revenue-stream analysis, including identifying sponsorship concentration.

---

#### DimPaymentMethod

Contains payment method information.

Key fields include:

- `method_id`
- `payment_method`
- `method_type`

This table supports analysis of how collected payments are received, such as EFT, credit card, BPAY, or direct debit.

---

#### DimInvoiceStatus

Contains invoice status values.

Key fields include:

- `status_id`
- `status`
- `description`

This table supports classification of invoices as Paid, Open, or Overdue.

---

#### DimDate

Calendar table used for date-based analysis.

Key fields include:

- `date_key`
- `date`
- `year`
- `quarter`
- `month_no`
- `month_name`

This table supports monthly analysis, aging logic, payment timing, and Power BI time-based filtering.

---

### Forecast Tables

The project also generates two forecast tables using Python.

#### forecast_by_invoice

Invoice-level forecast table showing when each unpaid invoice is expected to be collected.

Key fields include:

- `invoice_id`
- `customer_id`
- `customer_name`
- `member_tier`
- `category`
- `status`
- `issue_date`
- `due_date`
- `amount_incl_gst`
- `lag_used`
- `lag_source`
- `expected_collection`
- `already_overdue`

---

#### forecast_weekly

Weekly summary table showing expected cash inflows.

Key fields include:

- `week_start`
- `n_invoices`
- `normal_flow`
- `overdue_backlog`
- `amount`
- `cumulative`
- `pct_of_book`

---
## Business Problem

<img width="2006" height="1132" alt="image" src="https://github.com/user-attachments/assets/72ddf6a0-a5e7-453e-b5f8-ee0e437aa730" />

Molonglo Business Guild invoiced **$5.25M** in Q3 FY26, with **$2.02M** still outstanding at quarter-end.

At first glance, a **61.5% collection rate** may suggest weak collection performance. However, not all unpaid invoices are overdue; many are still within their normal payment terms. At the same time, sponsorship invoices dominate open receivables and many members appear above their credit limits.

This project answers four key business questions:

1. **How much are we owed?**

<img width="2006" height="1136" alt="Screenshot 2026-07-11 120725" src="https://github.com/user-attachments/assets/7e2cb5f6-a507-43c2-85fa-4c0e37a566e8" />

<img width="2012" height="1136" alt="Screenshot 2026-07-11 120740" src="https://github.com/user-attachments/assets/23c6ad34-1b6d-4e77-8cb4-a2a0e46483a0" />


2. **How well are we collecting?**

<img width="2012" height="1128" alt="Screenshot 2026-07-11 120834" src="https://github.com/user-attachments/assets/f35e8a29-12e1-422d-84ff-033b60dbf2e6" />

<img width="2078" height="1178" alt="Screenshot 2026-07-11 120905" src="https://github.com/user-attachments/assets/2a1f99ed-856a-48ab-9881-8d4eadda0361" />


3. **Where is debtor or credit risk concentrated?**

<img width="2012" height="1128" alt="Screenshot 2026-07-11 120953" src="https://github.com/user-attachments/assets/1e4b8114-89ec-4135-af51-911417812871" />


4. **When is outstanding AR expected to convert into cash?**

<img width="2008" height="1136" alt="Screenshot 2026-07-11 121003" src="https://github.com/user-attachments/assets/bf2e6c3e-64f3-4164-84fe-3989b88903f1" />

---

## Key Recommendations

### 1. Re-size credit limits for sponsorship-scale billing

Credit-limit exposure is mainly a structural issue. Lower-tier limits are too small relative to sponsorship invoice values. The organisation should consider separate sponsorship limits or limits based on invoice size rather than only membership tier.

**Expected outcome:** clearer credit-risk visibility and fewer false over-limit accounts.

---

### 2. Prioritise fresh overdue debt

The **$607K of 1–30 day overdue receivables** should be chased immediately. Fresh overdue balances are usually more recoverable, and routine follow-up can prevent them from aging into harder collection buckets.

**Expected outcome:** faster cash recovery and reduced aging risk.

---

### 3. Build a sponsorship-specific collection process

Since sponsorship drives approximately **80% of outstanding receivables**, the organisation should create a dedicated sponsorship collection process. This may include earlier invoicing, milestone billing, scheduled reminders, and named ownership for major sponsorship accounts.

**Expected outcome:** improved predictability of major cash receipts and reduced working-capital pressure.

---

### 4. Re-measure March invoices after maturity

March’s low issue-month collection rate should not be treated as a collection failure. Many March invoices were still within payment terms at the reporting date. The March cohort should be reviewed again after invoices mature.

**Expected outcome:** more accurate performance interpretation and fewer false conclusions.

---

### 5. Sustain on-time collection improvement

On-time collection improved from approximately **40% to 64%** across the quarter. The organisation should identify what drove this improvement and formalise it into standard collection practice.

**Expected outcome:** stronger collection discipline and improved payment punctuality.

---

### 6. Review Standard-tier payment terms

Standard-tier members average **4.7 days late** on 14-day terms. The organisation should assess whether 14-day terms are realistic for larger sponsorship invoices, or whether alternative terms or early-payment incentives should be used.

**Expected outcome:** reduced overdue classification noise and better alignment between payment terms and invoice size.

---

## Key Insights

### 1. Receivables are healthier than the headline collection rate suggests

The Guild invoiced **$5.25M** during the quarter and collected **$3.23M**, representing a **61.5% collection rate**. However, of the remaining **$2.02M outstanding receivables**, more than half is still current and not yet due.

Key figures:

- Outstanding receivables: **$2.02M**
- Current / not yet due: **$1.07M**
- Overdue receivables: **$945K**
- Only **17.9% of invoices** are genuinely past their deadline

This means the outstanding balance should not be interpreted as broad collection failure.

---

### 2. Sponsorship creates revenue-stream concentration risk

Sponsorship accounts for approximately **80% of outstanding receivables**, making it the main concentration risk in the receivables book.

However, customer concentration is low. The largest individual debtor represents only **1.7%** of utstanding receivables. This suggests that risk is concentrated by **revenue stream**, not by one major customer.

---

### 3. Credit-limit exposure is a policy design issue

A significant number of members exceed their credit limits, but the underlying cause is mainly structural. Sponsorship invoices are large relative to lower-tier credit limits, especially for Associate and Standard members.

This suggests that credit limits may need to be resized for sponsorship-scale billing rather than interpreted purely as bad payer behaviour.

---

### 4. Collection discipline improved across the quarter

On-time collection improved from approximately **40% in January** to **64% in March**. This suggests collection discipline strengthened during the quarter.

March’s lower issue-month collection rate should be interpreted carefully. Many March invoices had not yet reached their due dates by the reporting cut-off, so the low March collection rate is partly a maturity artifact rather than a true collection decline.

---

### 5. Cash flow forecast remains manageable

The short-term cash flow forecast projects that most open receivables will clear within a few weeks:

- **80%** of outstanding receivables expected by mid-April
- **98%** expected by end-April
- Even under stress scenarios, the analysis does not indicate a multi-month cash crisis

---

## Tools & Skills Used

- **Python:** Built an automated data pipeline to clean raw invoice data, validate data quality, generate forecast tables, and load refreshed outputs into Supabase.

- **Supabase / PostgreSQL:** Used as the cloud database layer to store the cleaned star schema, including fact tables, dimension tables, forecast tables, relationships, constraints, and indexes.

- **Power BI:** Built the interactive dashboard covering AR overview, collections performance, debtor risk, and cash flow forecasting.

- **Power Query:** Used for additional data preparation, type handling, and import-mode shaping inside Power BI.

- **Excel:** Used to review raw CSV exports, inspect invoice fields, check data quality issues, and perform quick validation before running the Python pipeline.

- **DAX:** Developed business measures such as Open AR, Collection Rate, Overdue AR, Credit Utilisation %, aging buckets, and forecast KPIs.

- **Data Modelling:** Designed a star-schema model linking invoice facts, customer/category/status/date dimensions, and forecast outputs.

- **AI-Assisted Reporting / Claude:** Used structured skills to support the generation of a business analysis report and consultant-style slide deck, then reviewed and refined outputs for accuracy.

---

## Project Architecture

The project follows a structured flow from database setup to automated loading and Power BI reporting.

The main design is:

```text
1. Supabase PostgreSQL schema is created first
   - sql/create_schema.sql creates the database tables
   - tables include dimensions, fact table, and forecast tables
   - relationships, constraints, and indexes are defined in PostgreSQL

2. Python pipeline processes the raw data
   - reads raw CSV files
   - cleans and validates invoice/customer data
   - checks accounting logic and relationship integrity
   - generates DimDate
   - generates forecast_by_invoice and forecast_weekly

3. Python loads data into Supabase
   - existing Supabase tables are truncated/refreshed
   - cleaned dimension and fact tables are inserted
   - forecast tables are inserted
   - Supabase becomes the central reporting database

4. Power BI connects to Supabase
   - Power BI imports tables through ODBC
   - relationships and DAX measures support dashboard reporting
   - users refresh Power BI after the Python pipeline updates Supabase

5. Backup / optional schema rebuild
   - if tables do not exist, Python can optionally rebuild the schema
   - this uses sql/create_schema.sql
   - this is only for first-time setup or schema reset, not normal refresh
