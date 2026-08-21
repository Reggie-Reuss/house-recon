# Validation: market-temperature & sold-comps stages

How we tested the empirical anchors behind the offer ladder — the zip-level
market stats (Method 27) and sold-comps value estimate (Method 11) — across
all 50 US states, what broke, what we fixed, and what remains a documented
limit.

**TL;DR:** 150 zips (3 per state), ~330 live page loads. Market-stats
parsing: **100% coverage, zero blocks**. Sold-comps parsing: **94.7%** of
zips yielded ≥3 comps (4,789 sold rows total, median 38/zip) after two
parser fixes this campaign itself surfaced. Cross-source accuracy: the
comps median agreed with Redfin's independently computed market median
within the 0.5×–2.0× band in **96.5%** of comparable zips, median ratio
**1.065**. Every remaining failure traces to a real-world data limit
(non-disclosure states, condo-dominant zips, missing-sqft counties), not a
silent parse error — and each now degrades gracefully with a logged gap.

## Why validate these stages

The offer ladder's credibility rests on two scraped inputs: the zip's
median sale-to-list ratio / days-on-market, and the median $/sqft of recent
sold listings. Both come from parsing rendered page text, which can
silently break three ways: the page blocks the browser, the layout differs
by region, or the regexes latch onto the wrong number. A parser that works
in one Ohio zip proves nothing about Honolulu, Brooklyn, or rural Wyoming —
so we tested nationally.

## Methodology

**Sample.** 3 zip codes in every state, 150 total, chosen for diversity
within each state: roughly one major-urban zip, one mid-size/college-town
zip, and one smaller or resort market (e.g. CA: San Francisco 94110,
Beverly Hills 90210, Davis 95616; WY: Cheyenne, Casper, Jackson). Several
were picked *specifically to try to break the parser*: ultra-luxury
(90210, Park City), condo-dominant (Miami Beach, Waikiki, Hoboken),
rowhouse-dominant (Philadelphia 19147, Brooklyn Heights), and
non-disclosure states (UT, ID, NM, MS, TX, ND...). The full list is
embedded in [`validation/validate_market_stages.py`](validation/validate_market_stages.py).

**Procedure.** For each zip, the harness runs the exact production code
paths — `stage_market_temp()` (Redfin housing-market page) and
`stage_sold_comps()` (Redfin sold-6-months search, Zillow fallback) —
through the same headed-Chrome browser the tool normally uses. Runs are
paced politely: one page at a time, human-scale settle times, a random
2–5 s pause between zips, and a 120 s cooldown after 3 consecutive blocks
(~330 page loads over ~1.5 h, comparable to an afternoon of manual
browsing).

**Checks per zip.**

| Check | Pass condition | What it catches |
|---|---|---|
| `market_ok` | any market stat parsed | page blocks, layout changes |
| `stl_plausible` | sale-to-list within 80–130% | regex latching onto wrong number |
| `dom_plausible` | median DOM within 1–365 | same |
| `comps_ok` | ≥3 sold-comp rows parsed | sold-search blocks, row-format drift |
| `ppsf_plausible` | median $/sqft within $25–$2,500 | unit/parse errors |
| `consistent` | comps median price ÷ market-page median sale price within 0.5×–2.0× | **the accuracy check** — two independent page types must agree about the same zip's recent sales |

**About the consistency check.** True offer/sale ground truth requires
licensed MLS data, which an open-source tool can't ship. The strongest
public-data test is internal agreement between independent sources: the
housing-market page's median sale price (a Redfin-computed monthly stat,
all property types) and the median price of the sold house listings we
parse ourselves (a 6-month window, first results page). They measure
slightly different things, so we score agreement loosely (0.5×–2.0×) and
report the full ratio distribution rather than pretending to a precision
the method doesn't have.

**Reproducing.** `python validation/validate_market_stages.py`
(add `--states OH,CA`, `--limit 10`, or `--zips 43206` for a quick pass).
Per-zip results land in `validation/results/results.jsonl` — aggregates
only, no addresses.

## Results (final, after fixes)

| Metric | Result | n |
|---|---|---|
| Market stats parsed (`market_ok`) | **100%** | 150/150 |
| Sale-to-list plausible | **100%** | 143 zips reporting |
| Median DOM plausible | **100%** | 148 zips reporting |
| ≥3 sold comps parsed (`comps_ok`) | **94.7%** | 142/150 |
| Median $/sqft plausible | **100%** | 143 zips reporting |
| Cross-source consistency (0.5×–2.0×) | **96.5%** | 139/144 comparable |
| Sold rows parsed nationally | 4,789 (median 38/zip) | |
| Zips blocked (sold search, both sources) | 4 (2.7%) | |

**Consistency-ratio distribution** (comps median ÷ market-page median):
median **1.065**, p10 0.887, p90 1.483; 59% of zips within ±15%, 76%
within ±25%. The upward skew is expected: our comps are houses-only over
6 months (first page, Redfin's default sort), while the market-page median
is a 1-month all-property-type stat.

Per-state coverage: [`validation/results/per_state.md`](validation/results/per_state.md).
Raw aggregates: [`validation/results/summary.json`](validation/results/summary.json).

## Findings

### 1. The campaign caught two real parser bugs (both fixed, then re-verified live)

- **The $2M price cap silently deleted luxury markets.** The comp parser
  originally discarded rows outside $30K–$2M as probable parse errors.
  In Beverly Hills that filtered *every* sale: 0 comps. Fixed by raising
  the cap to $25M and replacing it with a $/sqft sanity guard
  ($10–$5,000). Re-run: 90210 went **0 → 37 comps** (median $1,810/sqft,
  consistency ratio 1.48 ✓); New Canaan 2 → 39; Miami Beach 2 → 38;
  Park City 1 → 5.
- **Some counties don't publish square footage.** Minneapolis, Rochester
  MN, and Fargo render sold cards with "— sq ft", and the parser required
  numeric sqft — so all three parsed **zero** rows. Fixed by making sqft
  optional: price-only rows now feed the median-price context (and the
  consistency check), while only sqft-bearing rows feed the $/sqft value
  anchor. Re-run: all three went **0 → 40 rows**; the tool logs a gap
  noting "price context only, no $/sqft anchor."

Overall `comps_ok` moved **89.3% → 94.7%** from these two fixes.

### 2. A "failure" that was actually the parser being right

San Francisco 94110 tripped the original ≤115% sale-to-list plausibility
bound at **118.7%**. The raw page confirms it: *"average homes sell for
about 19% above list price."* The parse was correct; the bound was too
conservative. Widened to 80–130%.

### 3. Non-disclosure states are a data limit, not a parse limit

In states where sale prices aren't public record (UT, ID, NM, MS, TX, ND,
and others), Redfin's sold listings often show "Unknown — last sold
price." Metro zips usually still have agent-reported prices (all 3 TX
zips passed; Park City yielded 5), but smaller markets can bottom out:
Salt Lake City 84103 and Provo 84604 parsed 0 priced rows, Rexburg ID 2,
Las Cruces NM 1, Oxford MS 1. The tool degrades gracefully — the ladder
falls back to the market-stats anchor with a logged gap. **If you're
buying in a non-disclosure state, expect the sold-comps anchor to be
thin or absent outside metros.**

### 4. Condo- and rowhouse-dominant zips diverge by design

The 5 zips outside the consistency band — Miami Beach (14.9×!), Waikiki
(2.7×), Wilmington 19806 (2.9×), Evanston (2.2×), Hoboken (2.0×) — share
one signature: the zip's overall median is set by condos/rowhomes, while
our `property-type=house` comps catch the few (expensive) detached houses.
Miami Beach is the extreme: a ~$600K all-type median vs $5M+ waterfront
houses. **This is the right behavior for the tool's use case** (you
analyze a house, so house comps are the right anchor) but it means the
"consistency" divergence is expected there — and in such zips the
similar-size/similar-bed comp filter matters most. Related: Philadelphia
19147 and Brooklyn Heights parse ≤2 comps because rowhouses/townhouses
aren't Redfin's "house" type.

### 5. Bot-blocking reality

The Redfin **housing-market page never blocked once** in ~166 loads. The
Redfin **sold search** never hard-blocked either; every zero-row case
traced to data (above). The **Zillow sold-search fallback, however,
rescued zero zips** — it hard-blocked (Press & Hold) on every attempt from
this session. It remains in the code as a second chance, but don't count
on it; the paced single-browser pattern is what keeps the primary path
clean. 4/150 zips ended with no comps from either source.

### 6. What this means for the offer ladder

The empirical anchors are trustworthy in the common case: a typical
suburban/urban single-family zip yields full market stats plus ~38 usable
comps, and two independent sources agree on the market's price level
within ~6% at the median. Trust the anchors least — and lean on your
agent's MLS comps most — in exactly the cases above: non-disclosure-state
small markets, condo towers, and rowhouse cores.

## Limitations

- **Not MLS-verified.** Consistency between two scraped sources is
  necessary, not sufficient, for accuracy. Where precision matters, verify
  against your agent's MLS comps.
- **First-page comps bias.** The sold search parses the listings visible
  on the first results page (~40 rows), in Redfin's default sort order —
  not a random sample of all sales in the window.
- **Point-in-time.** Run date 2026-08-01, one residential IP, headed
  Chrome on Windows. These pages change; a layout redesign can zero out
  coverage overnight. Re-run the harness before trusting a fresh install.
- **Zip-level blur.** One zip can span distinct micro-markets; the anchor
  is a coarse correction, not an appraisal.

---
*Raw aggregates: `validation/results/summary.json` ·
per-state table: `validation/results/per_state.md` ·
per-zip records: `validation/results/results.jsonl` (no addresses).*
