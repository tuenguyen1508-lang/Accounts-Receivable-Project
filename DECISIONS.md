# Forecasting Decisions

## D1 — Payment lag is measured from the due date, not the issue date

**Decision**

Define payment lag as:

```
lag = payment_date − due_date
```

- **Positive** lag = paid *late* (after the deadline)
- **Zero** lag = paid on the due date
- **Negative** lag = paid *early* (before the deadline)

This lag distribution is the core input to the cash-conversion forecast: for each
open invoice we project its expected payment date as `due_date + expected_lag`.

**Why due date and not issue date**

Collection timing is driven by the payment *deadline*, not by when the invoice was
raised. Members are on different terms — `payment_terms_days` in the customer
dimension is 14 for some, 30 for others — so the same raw "days since issue" means
very different things depending on the terms.

Measuring from the due date normalises this. A member on 30-day terms and one on
14-day terms who both pay "5 days after due" share the same lag of +5, and that
shared behaviour is exactly what we want the forecast to capture. Lag-from-issue
would instead conflate two different things — the length of the terms and the
member's punctuality — and blur the payment-behaviour signal we're modelling.

**Consequence / what to watch**

- Requires a valid `due_date` on every invoice. Where `due_date` is missing it must
  be reconstructed as `issue_date + payment_terms_days` before computing lag.
- Only *paid* invoices (those with a `payment_date`) contribute to the observed lag
  distribution; open invoices are the ones we forecast.

---

## D2 — Use the median lag, not the mean

**Decision**

The single forecast lag applied to open invoices is the **median** of the historical
lag on paid invoices: **+1 day** (paid, on average, one day after the due date).

**Why median and not mean**

The lag distribution on the 1,579 paid invoices is right-skewed:

| statistic | value |
|-----------|-------|
| mean      | 3.87 days |
| **median**| **1.0 day** |
| std       | 10.68 |
| skewness  | +1.66 |
| min / max | −57 / +64 |
| p90 / p95 / p99 | 17 / 27 / 43 days |

The mean (3.87) is almost **4× the median** (1.0), a gap of ~2.9 days. That divergence
is the signature of skew: most members pay right around the deadline (median +1,
p25 = −2, p75 = +6), but a thin tail of very-late payers (p99 = 43 days, max = 64)
drags the mean upward. Roughly a third pay early, an eighth pay on time, and just over
half pay late — but "late" is mostly a few days, with the long tail doing the damage
to the mean.

Using the mean would systematically push every projected payment date ~3 days later
than the typical member actually behaves, biasing the cash-timing forecast to be
pessimistic because of a handful of outliers. The median is robust to that tail and
reflects the behaviour of the typical invoice, so we use it.

**Consequence / what to watch**

- A single median applied to all invoices ignores segment differences (member tier,
  payment method, category). If the forecast needs more precision later, compute the
  median lag *per segment* rather than switching to the mean.
- The tail is real cash, just later — the median forecast is a central estimate, not a
  worst case. A prudence/stress view could layer the mean or a high percentile on top.

---

## D3 — Per-member lag with a tier -> overall fallback, N = 3

**Decision**

Each open invoice gets a lag from a three-level fallback hierarchy, returning a
`(lag, source)` pair so the provenance of every forecast is traceable:

1. **member** — if the member has **≥ 3** paid invoices, use *their own* median lag.
2. **tier**   — else use their `member_tier`'s median lag.
3. **overall** — else (tier missing/empty) use the overall median lag (+1 day).

Median is used at every level, consistent with D2 (the skew argument applies within
a member too, not just globally).

**Why N = 3**

N is a bias/variance dial: too low and we trust noise from a member with one or two
lucky/unlucky payments; too high and almost everyone falls back to tier and we throw
away the personalisation that is the whole point of this step. We tried 3 and 5:

| N | invoice-level: member / tier | customer-level: member / tier |
|---|------------------------------|-------------------------------|
| **3** | **56.7% / 43.3%** | **68.6% / 31.4%** |
| 5 | 16.8% / 83.2% | 25.1% / 74.9% |

At N = 5 personalisation collapses — 83% of open invoices fall back to tier, so the
model is barely using member history at all. At N = 3 a majority of invoices (57%)
and of customers (69%) rest on the member's own behaviour, while still requiring
enough observations (3) that a single payment can't define a member. 3 is the
defensible middle. The median-of-3 is coarse but robust; if we later want to trust
smaller samples we would need a shrinkage estimator, not just a lower N.

**Findings / what to watch**

- The **overall** fallback never fires on this data: all four tiers have paid history
  and every open customer maps to a tier. It stays as a safety net for future data.
- Tier medians are tight — Associate 0, Premium 1, Standard 1.5, Corporate 2 — all
  close to the overall median of 1. So the 43% of invoices on tier fallback are not
  being pushed far from the global default; the fallback is low-risk here.
- Report line for stakeholders: **~57% of the (invoice-level) forecast rests on the
  member's own payment history; ~43% on their tier's average.**

Output: `open_with_lag.pkl` carries `lag_used` and `lag_source` per open invoice.

---

## D4 — Snapshot date, and how overdue invoices are treated (the headline caveat)

**Snapshot = 2026-03-31**, derived from the data (not the system clock). Overdue
invoices are all due ≤ 2026-03-30 and Open invoices all due ≥ 2026-03-31, and the
last observed payment is 2026-03-31 — so the status labels were assigned as of that
date. The forecast horizon starts the next day, **2026-04-01** (week 1).

**Projection**

```
expected_collection = normalize_to_day(due_date + lag_used)
```

Normalising to whole days matters: some member medians are fractional (e.g. 1.5 from
an even-count median), which otherwise produce mid-day timestamps and corrupt the
day/week bucketing.

**The judgment call: past-due projections**

400 of 941 open invoices (**42.5%**, **$861k of the $2.02M book — 42.6%**) project to
a date *before* the first forecast day. These are almost entirely the Overdue set,
and they land a median of ~21 days (up to 75) in the past: the lag model has *already
failed* to time them — the member is later than their own history predicted.

You cannot collect money in the past, so these must be moved forward. Three options
were on the table:

1. **Clamp all into the first forecast week** — simple, but inflates week 1.
2. **Spread by how overdue** — assumes "older debt collects proportionally slower."
3. **Flag separately as already-expected** — keeps them out of the normal flow.

**Chosen: clamp into the first forecast week, AND flag every clamped invoice
(`already_overdue`) so week 1 is decomposable** — a hybrid of (1) and (3).

*Why not (2):* spreading invents a second lag model on top of a first one that has
already failed for exactly these invoices. We have no evidence that a 40-days-overdue
debt collects on a different curve than a 10-days-overdue one; fabricating that curve
would be less defensible than admitting "expected imminently, timing uncertain."

*Why clamp at all rather than leave in the past:* a forward cash forecast is about
future weeks; a past date is not a valid answer. Clamping says the honest thing — this
money is owed *now* and expected as soon as possible.

*Why the flag is non-negotiable:* clamping without flagging would silently triple
week 1 and make a backlog look like a normal week.

**Impact — this directly shapes the first bar**

| Week 1 (2026-04-01) | amount |
|---------------------|--------|
| Normal flow (genuinely due this week) | $333,654 |
| Overdue backlog (clamped) | $861,486 |
| **Total** | **$1,195,140** |

**72% of week 1 is clamped overdue backlog, not this week's genuine collections.**

**How week 1 must be read / what to watch**

- Week 1 is **not** a normal week. Report it as two numbers: ~$334k of genuine week-1
  collections plus ~$861k of overdue backlog pulled forward.
- The backlog carries **elevated non-collection risk** — some of it may be disputed or
  never pay. The clamped date is "expected ASAP," not "certain in week 1." A prudent
  view would haircut the backlog (collection-probability weighting) before treating it
  as cash. That is the natural next refinement.
- Weeks 2+ are unaffected by the clamp and reflect normal projected flow.

Outputs: `open_projected.pkl` (per-invoice projection + flags),
`cash_timeline_weekly.csv` (normal / backlog / total by week).

---

## Validation (Step 6) — forecast is trustworthy

Re-run any time via `step6_validate.py`.

**Check 1 — reconciliation (must hold, else the forecast is broken):** the projected
schedule sums to the open AR straight from the raw CSV — **$2,020,388.59 across 941
invoices**, diff **$0.00**, zero duplicate invoice_ids, and the projected id set is
*identical* to the open-AR id set. Nothing dropped or double-counted.

**Check 2 — shape is sane:** collections are front-loaded and tail off, as expected —
**99.2% within 4 weeks** of the snapshot, peak in week 1, **$0 beyond 8 weeks**, and
86% of week-over-week transitions decline. No large amount lands in a distant week, so
no runaway lag. (The front-loading is amplified by the D4 overdue-backlog clamp, which
is disclosed, not a defect.)
