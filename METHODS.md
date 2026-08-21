# house-recon — Methods

The definitive methodology reference for **house-recon**, an open-source due-diligence toolkit for single-family residential property research. Every method below can be run by a human researcher or an LLM agent. The workflow was validated end-to-end on a real Butler County, Ohio purchase investigation; all examples here use placeholder addresses (e.g. `123 Main St, Anytown OH`).

**Part 1** documents the core methods the toolkit implements today. **Part 2** documents untapped methods queued for future implementation. Each method follows the same shape: *What it tells you / How to do it / Data source specifics / Automatable? / Gotchas*.

---

## Table of Contents

**Part 1 — Core Methods**
1. [Listing aggregation & cross-referencing](#1-listing-aggregation--cross-referencing)
2. [Full dated listing/price history reconstruction](#2-full-dated-listingprice-history-reconstruction)
3. [County auditor / assessor record pull](#3-county-auditor--assessor-record-pull)
4. [Ohio property-tax math & projection](#4-ohio-property-tax-math--projection)
5. [Value-history forensics](#5-value-history-forensics)
6. [Listing-photo forensics](#6-listing-photo-forensics)
7. [Satellite + street-view analysis](#7-satellite--street-view-analysis)
8. [Utility determination & cost baseline](#8-utility-determination--cost-baseline)
9. [Schools, mobility & hazard scores](#9-schools-mobility--hazard-scores)
10. [Comp context & negotiation synthesis](#10-comp-context--negotiation-synthesis)
10a. [Offer-ladder modeling (heuristic — implemented, offline)](#10a-offer-ladder-modeling-heuristic--implemented-offline)
10b. [AI judgment layer (implemented, optional — `--ai`)](#10b-ai-judgment-layer-implemented-optional----ai)

**Part 2 — Untapped Methods**
11. [Sold-comps analysis](#11-sold-comps-analysis)
12. [Municipal permit search](#12-municipal-permit-search)
13. [County GIS / zoning / plat maps](#13-county-gis--zoning--plat-maps)
14. [Recorder deed & lien search](#14-recorder-deed--lien-search)
15. [CLUE insurance-claims report](#15-clue-insurance-claims-report)
16. [Radon zone + test](#16-radon-zone--test)
17. [Environmental screens](#17-environmental-screens)
18. [Historic aerial imagery](#18-historic-aerial-imagery)
19. [Oblique aerial roof review](#19-oblique-aerial-roof-review)
20. [Crime & registry](#20-crime--registry)
21. [Noise & nuisance](#21-noise--nuisance)
22. [Rental & investment analysis](#22-rental--investment-analysis)
23. [Insurance pre-quote](#23-insurance-pre-quote)
24. [Sewer lateral scope & water-main age](#24-sewer-lateral-scope--water-main-age)
25. [Utility bill history](#25-utility-bill-history)
26. [Title pre-search & seller-side docs](#26-title-pre-search--seller-side-docs)
27. [Market-temperature stats](#27-market-temperature-stats)
28. [School-boundary verification](#28-school-boundary-verification)
29. [Demographic & trend data](#29-demographic--trend-data)
30. [Absentee/portfolio-owner screen](#30-absenteeportfolio-owner-screen)

[Ethics & Terms](#ethics--terms)

---

# Part 1 — Core Methods

## 1. Listing aggregation & cross-referencing

**What it tells you:** The full public-facing picture of the listing — and, more importantly, where the portals *disagree*, which is where the interesting facts live.

**How to do it:**
1. Search the address on every major portal: Zillow, Trulia, Redfin, Realtor.com, RE/MAX, Homes.com.
2. Also check local MLS mirrors — regional brokerage sites that syndicate the local MLS directly (e.g. Sibcy Cline for the Cincinnati MLS). These often expose fields the national portals drop: agent remarks, showing instructions, exact MLS status.
3. Capture the **MLS number** from every source. Log every distinct MLS number you find for the same address.
4. Build a field-by-field comparison table: price, beds/baths, sqft, lot size, year built, heating type/fuel, foundation, days on market, HOA, taxes.
5. Flag every discrepancy for resolution against county records (Method 3).

**Data source specifics:**
- Each portal exposes different fields. Zillow: price history, Zestimate, photo set. Trulia: often deeper history and neighborhood commentary. Redfin: MLS-faithful data, sold comps. Realtor.com: usually the fastest to reflect status changes. Homes.com / RE/MAX: occasionally retain photos or remarks the others purge.
- **A NEW MLS number on the same house = a relist.** Relisting resets the portal's "days on market" counter. Reconstruct true cumulative market time yourself by summing across MLS numbers (see Method 2).

**Automatable?** Yes — HTTP fetch + HTML/JSON parsing per portal. Zillow and Redfin embed structured JSON in the page (`__NEXT_DATA__` and similar); the rest are scrapeable HTML.

**Gotchas:**
- Portals sometimes show stale caches of *withdrawn* listings — verify status on the freshest source.
- Sqft definitions differ (finished vs total vs above-grade). Never average discrepant numbers; resolve them.
- "Days on Zillow" ≠ days on market. Trust only your own reconstruction.

## 2. Full dated listing/price history reconstruction

**What it tells you:** The property's true market story: how long it has *actually* been for sale, how motivated the seller is, and whether previous buyers walked away — the single best negotiation input.

**How to do it:**
1. Scrape Zillow's price history table (list, cuts, pending, sold events with dates).
2. Scrape Trulia's price history — **Trulia often shows older events without expanding**, including events Zillow collapses or drops.
3. Pull county transfer records (Method 3) for every recorded sale with date and amount.
4. Merge all three into ONE chronological timeline, deduplicating by date+event+price.
5. Annotate the timeline with detections (below).

**What to detect:**
- **Price cuts** — size and cadence. Frequent small cuts = agent-driven drip pricing; one large cut = seller capitulation event.
- **Delist/relist cycles** — gaps where the listing disappears and returns under a new MLS number. Sum the segments for cumulative days on market.
- **Pending → back-to-Active transitions** — failed contracts. These are usually inspection or appraisal failures. **Always ask the listing agent why the prior contract fell through** — they are generally obligated to disclose known material defects, and the answer is free.
- **Wholesale double-closings** — two transfers on the *same day* at different prices. The spread is the wholesaler's fee; the lower price is what the house was actually worth to a cash buyer.
- **Flips** — resale <12 months after purchase at >30% markup. Scrutinize renovation quality (Method 6) and permits (Methods 5, 12).

**Data source specifics:** Zillow history is in the listing page's embedded JSON. Trulia's is server-rendered HTML. County transfers come from the auditor portal and are the ground truth for recorded sales.

**Automatable?** Yes — pure parsing + merge + rules engine. Detection rules above are simple date/price arithmetic.

**Gotchas:**
- Portals silently drop pre-2010 events; the county has all recorded transfers forever.
- $0 or nominal ($1, $10) transfers are usually intra-family or trust moves, not sales — tag, don't treat as comps.
- A "sold" event on a portal without a matching county transfer may be an MLS status error or an unrecorded land contract — investigate.

## 3. County auditor / assessor record pull

**What it tells you:** Ground truth. This is the **highest-value free source** in the entire workflow — everything the seller's marketing can't spin.

**How to do it:**
1. Identify the county, then its auditor/assessor property-search portal. For Ohio, most counties run **iasWorld** portals (e.g. `propertysearch.bcohio.gov` for Butler County).
2. Search by address or owner; capture the **parcel ID** — the primary key for every county system (GIS, recorder, treasurer).
3. Pull every tab and archive it:
   - **Owner + tax mailing address** — mailing ≠ property address ⇒ absentee owner / investor. Negotiation-relevant.
   - **Transfer history** — dates, amounts, conveyance types. Feeds Method 2.
   - **Dwelling card** — year built, **EFFECTIVE year** (a later effective year = county-recognized major renovation), foundation type (**SLAB / CRAWL / FULL**), bedroom/bath counts, heat system + fuel type, construction class, finished sqft.
   - **Land factors** — topography and influence codes such as `STEEP` or `ABOVE STREET`. **These reveal what listing photos hide.**
   - **Value history by year** — feeds Method 5.
   - **Permits tab** — county-known permits (municipal permits are separate; see Method 12).
   - **Outbuildings, homestead credits, taxing district.**

**KEY TECHNIQUE — the listing-vs-county contradiction check:** When the listing and the dwelling card disagree on foundation type, bedroom count, or heating fuel, *one of them is wrong* — and finding out which one, before you offer, is always worth the effort. A listing that says "3 bed" over a county card that says "2 bed" may mean an unpermitted conversion; "gas heat" on the listing vs "electric" on the card may mean a recent (permitted?) fuel switch — or a lie.

**Data source specifics:** Ohio iasWorld portals share a common URL/DOM structure across counties, so one parser covers many counties. Other states use Tyler, Vision, Schneider/Beacon (qPublic) — each needs its own adapter but the field taxonomy above holds.

**Automatable?** Yes — fully. iasWorld pages are static HTML tables; parse by tab.

**Gotchas:**
- Dwelling cards lag reality by an assessment cycle — a 2025 renovation may not appear until the next reappraisal.
- Bath counts use full/half conventions inconsistently across counties.
- "-- No Data --" in a tab means *no records*, not *page failed to load* — but verify your scraper distinguishes the two.

## 4. Ohio property-tax math & projection

**What it tells you:** Your actual future tax bill — not the stale number on the listing, which reflects the *seller's* credits and *last cycle's* value.

**How to do it:**
1. From the auditor record, get market (appraised) value and the taxing district's **effective millage** (from the county's rate table or the parcel's tax detail).
2. Compute:
   - `assessed value = market value × 0.35` (Ohio statewide)
   - `gross annual tax ≈ assessed × effective millage ÷ 1000`
   - Apply the **non-business credit (~8.5%)** and, if you'll live there, the **owner-occupied credit (~2.1%)**.
3. **Reappraisal projection:** Ohio counties reappraise on a triennial/sexennial cycle. During reappraisal years the auditor posts a **TENTATIVE value** — compute the future bill from it *before you buy*. A 20%+ revaluation is a real monthly-payment change (taxes are escrowed into most mortgage payments).
4. Read the **levy distribution table** for the parcel: it lists exactly which city/township/school district/JVS/library taxes it. **This is the definitive way to determine city vs township services** — utilities, income tax liability, trash — when the mailing address is ambiguous (mailing city names frequently span multiple taxing jurisdictions).

**Data source specifics:** County auditor tax detail page (per-parcel), county rate tables (per-district), Ohio Dept. of Taxation for credit percentages.

**Automatable?** Yes — arithmetic over scraped fields. Flag reappraisal years automatically from the county's published schedule.

**Gotchas:**
- Effective millage ≠ voted millage (HB 920 reduction factors) — always use *effective*.
- The seller may have homestead or CAUV credits you won't qualify for; recompute without them.
- School-district income tax (separate from property tax) applies in some Ohio districts — check the district from the levy table.

## 5. Value-history forensics

**What it tells you:** Whether major work was done on the house — and whether it was done *with permits*.

**How to do it:**
1. From the auditor's value-history table, chart **building (improvement) value** by year — ignore land value for this purpose.
2. Flag any **building-value jump >15% in one assessment cycle** = the county caught a major renovation (addition, finished basement, gut rehab).
3. Cross-reference each jump against the **permits tab** for the same window.
4. **Big value jump + "-- No Data --" permits = unpermitted renovation.** Treat everything behind the walls as suspect: electrical, plumbing, structural. Inspect more invasively, and **ask the seller for contractor invoices** — a legit contractor leaves a paper trail even when the permit was skipped.

**Data source specifics:** Auditor value-history and permits tabs (Method 3). Municipal permits (Method 12) can rescue a "no county permit" finding — check both before concluding "unpermitted."

**Automatable?** Yes — threshold rule over the value series + join against permit records.

**Gotchas:**
- County-wide reappraisals raise *everything*; compare the jump against neighboring parcels' same-year change before flagging.
- A jump can also mean the county corrected a prior error (e.g. discovered sqft) — the dwelling card's change log sometimes shows this.

## 6. Listing-photo forensics

**What it tells you:** Renovation quality and era, mechanical-systems condition, and — in the exterior shots buyers skim — drainage, roof, and site problems the seller didn't think to hide.

**How to do it:**
1. **Download ALL full-resolution photos.** Zillow hosts originals at `photos.zillowstatic.com`; the listing page's `__NEXT_DATA__` JSON contains every photo hash; the **`-o_a.jpg` suffix retrieves the uncropped original**. Archive locally — photos vanish when listings close.
2. Judge each photo systematically:
   - **Renovation era & quality** — flip-grade (gray LVP + white shaker + builder-grade dome lights everywhere, same finish in every room) vs owner-grade (mixed eras, higher-spend kitchens/baths).
   - **Mechanicals** — water heater brand/fuel/age sticker; presence of an **expansion tank = recent code-compliant install**. Furnace closets, filter condition, panel brand and breaker fill.
   - **Floors, windows, paint** — window brand/age, floor transitions (hides subfloor issues), fresh paint over only *some* ceilings (leak history).
   - **EXTERIOR shots — the most telling and most skimmed:** roof uniformity (patched sections = repairs, not replacement), gutters and downspout routing, **loose drainage pipes lying in the yard = someone is actively fighting a water problem**, driveway cracking/patches, vegetation touching siding, trees overhanging the roofline, lot slope toward the house.
   - **Floor-plan image** — sanity-check the layout and *count the closets* against the bedroom claim (a "bedroom" with no closet often isn't one, legally or practically).

**Data source specifics:** Zillow (best resolution via the `-o_a` trick), plus every other portal — older/removed photos sometimes survive on Homes.com, RE/MAX, or the local MLS mirror even after Zillow purges them.

**Automatable?** Partially. Photo *download* is fully automatable. Photo *judgment* is LLM-vision territory: a per-photo prompt checklist (the bullets above) works well as an agent task; final synthesis benefits from human review.

**Gotchas:**
- Wide-angle lenses distort room size; use the floor plan for real proportions.
- Photos may predate the listing by months (staging shoots) — cross-check against street-view capture dates (Method 7).
- Virtually staged photos are usually labeled — but not always. Look for repeated identical furniture across "different" listings.

## 7. Satellite + street-view analysis

**What it tells you:** The lot and neighborhood context no listing photo will show — slope, exposure, tree load, neighbor condition, and what changed over time.

**How to do it:**
1. **Google Maps satellite:** lot shape and true boundaries (vs the plat, Method 13), corner exposure, tree canopy count/size/placement (roof and gutter load, foundation-adjacent roots), rear-line encroachment (neighbor's shed on the line?), brush lines, neighbor upkeep, debris piles.
2. **Street view — note the capture date** (shown in the corner): elevation vs street (water flows downhill; is the house below grade?), retaining slopes and walls, driveway apron condition, street pavement condition (repaving assessments), overhead lines and pole placement.
3. **Compare the street-view capture date vs listing photos** to see what changed: new roof? removed tree? fresh siding on one wall only?

**Data source specifics:** Google Maps (satellite + street view), Bing Maps (different capture dates — a free second timestamp), county GIS aerials (Method 13) for a third.

**Automatable?** Partially. Static-map/street-view imagery can be fetched programmatically (mind API terms); the analysis is LLM-vision plus a checklist. Change detection between dated captures is a strong agent task.

**Gotchas:**
- Satellite imagery may be years old — always read the capture date before drawing conclusions.
- Leaf-on vs leaf-off season changes what's visible; check both if multiple captures exist.
- Street-view geolocation can be off by a house; confirm you're looking at the right structure via roofline/driveway match with listing photos.

## 8. Utility determination & cost baseline

**What it tells you:** Who your actual utility providers will be and a realistic monthly utility stack — **don't guess from the city name**; mailing addresses lie about jurisdictions.

**How to do it:**
1. Start from the **tax-distribution city/township** (Method 4) — that tells you whose water/sewer/trash you'll actually pay.
2. Find the municipality's official **"local utility providers"** page for electric and gas. College towns often have surprises — local gas co-ops, municipal gas or electric companies — that beat or differ from the regional default (Duke, AES, etc.).
3. Pull **rate schedules** from the municipal code (many Ohio municipalities publish via `codelibrary.amlegal.com`) or the current fee ordinance: water base + volumetric, sewer (often billed on water usage), trash, stormwater fees.
4. Check **broadband by address**: FCC National Broadband Map, then the specific providers' address checkers (altafiber, Spectrum, etc.) — the FCC map overstates availability; the provider checker is the truth.
5. Assemble a full monthly estimate: electric + gas + water/sewer + trash + stormwater + internet, sized to the house's sqft, heat fuel (from the dwelling card, Method 3), and insulation era.

**Data source specifics:** Municipal websites, amlegal/Municode code libraries, FCC National Broadband Map (`broadbandmap.fcc.gov`), provider address checkers, PUCO rate comparisons for deregulated electric/gas supply in Ohio.

**Automatable?** Mostly. Provider determination and broadband checks automate cleanly; rate-schedule extraction from ordinances is semi-structured (good LLM extraction task).

**Gotchas:**
- Heating fuel from the dwelling card drives the estimate — electric resistance heat in an older house can double winter bills vs gas. Verify fuel via the contradiction check (Method 3).
- Township parcels near a city may be on wells/septic — no water/sewer bill, but a different due-diligence branch entirely (well test, septic inspection).
- Some municipalities levy income tax on residents — the levy table (Method 4) tells you if you're inside the city.

## 9. Schools, mobility & hazard scores

**What it tells you:** Third-party quality-of-life and risk scores — cheap to collect, useful as context, never dispositive.

**How to do it:**
1. **GreatSchools ratings** — scrape from listing pages (Zillow/Trulia/Realtor.com embed them), noting assigned vs nearby schools.
2. **Walk / Transit / Bike scores** — from listing pages or walkscore.com.
3. **FEMA flood zone** — FEMA Map Service Center by address; **Zone X = minimal risk**; anything in an A/AE zone means mandatory flood insurance with a mortgage.
4. Record each against **your actual use case**: a 3/10 school rating is irrelevant to a no-kids buyer but crushes resale in family neighborhoods; a low walk score is meaningless if you drive everywhere.

**Data source specifics:** GreatSchools (via listing embeds), Walk Score, FEMA MSC (`msc.fema.gov`), plus First Street risk factors (flood/fire/heat) which Redfin and Realtor.com embed.

**Automatable?** Yes — all scrapeable or API-accessible by address.

**Gotchas:**
- Listing-page school assignments are frequently wrong — verify against district GIS before relying on them (Method 28).
- FEMA zones lag real drainage behavior; Zone X does not mean the crawl space stays dry (see Methods 6–7 for the actual water evidence).

## 10. Comp context & negotiation synthesis

**What it tells you:** What the house is worth relative to its neighbors, and exactly how much leverage you have — the deliverable everything above feeds into.

**How to do it:**
1. Pull the **"nearby homes" values** from listing pages (Zillow/Redfin embed neighbor estimates) for a fast comp band; compute the subject's **$/sqft vs neighbors**.
2. Combine with the history timeline (Method 2):
   - **Cumulative days on market** across all relists — real market time.
   - **Price-cut trajectory** — where is the seller's floor trending?
   - **Failed contracts** — prior buyers found something; you get to price that in.
   - **Seller's original purchase price = their cost basis** — the psychological and financial floor. A seller sitting on a large gain can cut; a seller near break-even is rigid.
3. Produce a **leverage assessment** (high/medium/low with reasons) and an **offer strategy**: opening number, justification points to hand the agent (each tied to a documented finding), contingencies to keep, and walk-away price.

**Data source specifics:** Everything above; no new sources. This is the synthesis layer.

**Automatable?** Mostly — the numeric assembly fully; the strategy memo is a strong LLM task over the collected evidence, with human sign-off on the actual offer.

**Gotchas:**
- Neighbor Zestimates are noisy for atypical parcels; prefer real sold comps (Method 11) as they come online.
- Leverage cuts both ways: a well-priced fresh listing with zero cuts means your findings are inspection-negotiation material, not offer-price material.

---

## 10a. Offer-ladder modeling (heuristic — implemented, offline)

**What it tells you:** A concrete set of offer prices with estimated acceptance odds, so "offer below ask" becomes "here are four numbers and roughly what each one risks."

**How to do it:**
1. Compute a **seller-motivation score (0–100)** from documented listing-history signals, weighted: cumulative days on market (up to 30), % cut from peak ask (20), a failed pending (15), relists (10), absentee owner (10), ask ≥25% above county valuation (10), flip pattern (5), large margin over the owner's cost basis (5).
2. Anchor **d50 — the discount with ~50% acceptance odds**. Empirically when the data is available: base discount = the zip's *observed* median sale-to-list discount (Method 27) and curve slope stretched by the zip's median days on market; heuristically otherwise (≈2% fresh listing floor). The motivation score adds up to 10 points of discount on top.
3. Shift d50 by the **sold-comps value gap** (Method 11): half the percentage gap between the ask and the comp-derived value estimate, capped at ±5 points. A house priced *under* comp value supports fewer/shallower discounts than its motivation score alone suggests — and vice versa.
4. Place offer rungs on a logistic curve around d50 (full ask, half-d50, d50, 1.5×d50), each with acceptance odds and expected savings (discount × odds). Read the ladder as a **negotiation map**, not a prediction: the steep part of the curve is where one more dollar of discount costs the most acceptance probability.

**Data source specifics:** Methods 2–5 and 10 plus, when captured, the zip market stats (Method 27) and sold comps (Method 11). Falls back to pure listing-history heuristics with no network or AI; the report labels which anchoring mode ran.

**Automatable?** Fully — pure arithmetic over the merged timeline, county record, and (when present) zip stats + comps.

**Gotchas:**
- Even empirically anchored, these are *calibrated estimates, not a fitted statistical model*: true offer-acceptance data is unobservable from public records (rejected offers are never published), so the honest statistical ceiling is modeling the distribution of sale-to-list outcomes — which is what the zip anchor approximates at coarse grain. Seller psychology and multiple-offer dynamics remain invisible.
- The score-to-discount mapping and the ±5 gap cap are judgment, not regression coefficients. A fitted upgrade would use quantile regression over closed sales with censoring for withdrawn listings (survival analysis) and calibration checks.
- The model is deliberately blind to condition findings; in AI mode (Method 10b) the ladder is refined against them.

## 10b. AI judgment layer (implemented, optional — `--ai`)

**What it tells you:** The 20% a script can't do: what the photos actually show (condition grade, work items with cost ranges, defects marketing photos frame around), the answered visual-review checklist, an offer ladder refined against condition and comp evidence with per-rung rationale, negotiation points, and a bottom-line verdict.

**How to do it:**
1. **Vision pass** — send the downloaded full-resolution listing photos (batched) to a vision-capable LLM with a home-inspection-minded prompt; demand structured per-photo output (view, condition notes, defects) so nothing is skimmed.
2. **Synthesis pass** — feed the full parsed dossier (timeline, county record, red flags, tax projection, heuristic ladder) plus the photo findings to the model with a buyer-side analyst system prompt; demand structured output covering condition grade, work items with urgency and cost ranges, checklist answers, a refined offer ladder with evidence-based rationale per rung, negotiation points, and a bottom line.
3. Ground rules that matter: the model must **anchor on the heuristic ladder and adjust with cited evidence** (never invent probabilities from vibes), and every claim must trace to the dossier or a photo.

**Data source specifics:** Implemented against the Claude API (official `anthropic` SDK, default model `claude-opus-5`, structured JSON-schema outputs, vision via base64 image blocks). Any capable vision LLM could substitute — the prompts and schemas are the method.

**Automatable?** Fully automated in `--ai` mode; the offline mode leaves the same sections as fill-in-the-blank for a human. Use `--resume --ai` to add the layer to a previous run without re-scraping.

**Gotchas:**
- Costs real money per run (a few dollars at ~25 photos + synthesis on a frontier model) and requires API credentials; the tool stays fully functional without it.
- LLM condition judgments are from marketing photos only — they narrow what your inspector should chase, they don't replace the inspection.
- Success probabilities remain calibrated estimates; the AI refines the heuristic, it doesn't mint truth.

---

# Part 2 — Untapped Methods

Documented for future implementation. Same structure; each marked automatable yes/no.

## 11. Sold-comps analysis

**What it tells you:** Real market value from *closed* sales — the appraiser's-eye view, not asking prices.

**How to do it:** Pull closed sales within ~0.5–1 mi, last 6–12 months, similar sqft/beds/era. Run a $/sqft regression (adjusting for sqft, lot, garage, condition tier) and compute DOM statistics for the segment.

**Data source specifics:** Redfin "sold" filter (best free structured source), county conveyance/transfer records for ground-truth prices and dates.

**Automatable?** **Yes** — Redfin sold data is scrapeable; county conveyances feed Method 3's parser.

**Status: partially implemented.** `house_recon.py` now scrapes the zip's last-6-months sold listings (Redfin sold search via the headed browser, Zillow fallback), computes a median-$/sqft value estimate from similar-size/similar-bed comps, and feeds the ask-vs-value gap into the offer ladder (Method 10a). Still untapped from this method: distance-weighted comp selection, per-comp condition adjustments, and county-conveyance cross-checks.

**Gotchas:** Exclude non-arm's-length transfers (nominal amounts, family names matching). Condition adjustment is the weak link — photo review of comps helps. Zip-median $/sqft is a coarse anchor: small houses skew high on $/sqft, and one street can differ from the next.

## 12. Municipal permit search

**What it tells you:** Roof, HVAC, water-heater, and electrical work dates that **county permit tabs miss** — cities run their own building departments.

**How to do it:** Identify the permitting authority from the taxing district (Method 4). Search the city/township building department's online permit portal by address, or submit a public-records request for the permit file.

**Data source specifics:** City building dept portals (varies wildly: Accela, Citizenserve, OpenGov, or PDFs by email). Records requests are free in Ohio under the Public Records Act.

**Automatable?** **Partially** — portal-dependent; records requests need a human sender.

**Gotchas:** Jurisdiction confusion is the #1 error — a township parcel may be permitted by the county, not the nearby city. The levy table resolves it.

## 13. County GIS / zoning / plat maps

**What it tells you:** Easements, right-of-ways, setback problems, floodplain overlays, and true parcel dimensions.

**How to do it:** Open the county GIS viewer, locate the parcel by ID, toggle layers: zoning, easements/ROW, floodplain, aerials, contours. Pull the recorded plat for the subdivision; note utility easements crossing the buildable area and any encroachments visible against the aerial.

**Data source specifics:** County GIS portals (often ArcGIS-based with queryable REST endpoints), recorded plats via the recorder or engineer's office.

**Automatable?** **Yes** — ArcGIS REST APIs return parcel geometry and layer intersections as JSON.

**Gotchas:** GIS parcel lines are approximate, not survey-grade. An easement absent from GIS may still exist in the deed (Method 14).

## 14. Recorder deed & lien search

**What it tells you:** Open mortgages, mechanic's liens, easements written into deed language, and HOA covenants that bind the property.

**How to do it:** Search the county recorder's index by owner name and parcel; pull the current deed, any mortgages/releases, liens, and recorded covenants. Read the deed's exception language ("subject to…") for easements.

**Data source specifics:** County recorder online indexes (many Ohio counties have free search + paid or free image pulls).

**Automatable?** **Partially** — index search yes; document reading is OCR + LLM extraction with human verification for anything material.

**Gotchas:** Name-based indexes miss misspellings; search prior owners too. This supplements — never replaces — a professional title search (Method 26).

## 15. CLUE insurance-claims report

**What it tells you:** The property's insurance-claim history (water, fire, wind) for the past ~7 years — past water claims predict future water problems and raise your premiums.

**How to do it:** Only the owner can order it: ask the seller to request their free annual CLUE report from LexisNexis and share it. Make it a standard document ask alongside disclosures.

**Data source specifics:** LexisNexis Personal Reports (free to the owner annually).

**Automatable?** **No** — seller-provided document; automate only the *reminder to ask* and the parsing of the delivered PDF.

**Gotchas:** A clean CLUE doesn't mean no damage — it means no *claims*. Sellers who self-repaired leave no trace here.

## 16. Radon zone + test

**What it tells you:** Whether the county's geology predicts elevated radon and whether this house actually has it.

**How to do it:** Check the EPA radon zone map for the county — **much of Ohio is Zone 1** (highest predicted). If Zone 1, or if the house sits over a crawl space or basement, include a 48-hour radon test in the inspection contingency.

**Data source specifics:** EPA radon zone map, Ohio Dept. of Health radon program (licensed testers, county-level result stats).

**Automatable?** **Partially** — zone lookup yes; the test itself is physical.

**Gotchas:** Zone 2/3 counties still produce hot houses. Mitigation is cheap (~$1–2k) — a failed test is a negotiation item, not a walk-away.

## 17. Environmental screens

**What it tells you:** Contamination and soil risks near the parcel: regulated facilities, Superfund proximity, soil suitability, wetlands, leaking underground storage tanks.

**How to do it:** Run the address through EPA ECHO (regulated facilities within radius) and the Superfund site list; USDA Web Soil Survey for the parcel's soil series (shrink-swell, drainage class); USFWS Wetlands Mapper; Ohio BUSTR database for underground storage tanks (former gas stations near the lot).

**Data source specifics:** EPA ECHO + Envirofacts (APIs), USDA WSS, USFWS Wetlands Mapper, Ohio BUSTR (State Fire Marshal).

**Automatable?** **Yes** — all have queryable endpoints or exportable data.

**Gotchas:** Proximity ≠ impact; use these as flags for professional follow-up (Phase I ESA territory), not verdicts.

## 18. Historic aerial imagery

**What it tells you:** Prior structures, filled swimming pools, lot-line changes, additions, and tree growth over decades — buried surprises literally.

**How to do it:** Open the parcel in Google Earth Pro and step through the historical-imagery timeline. Note demolished outbuildings, a pool that vanished between captures (filled pools cause settling), driveway relocations, and tree growth near the foundation.

**Data source specifics:** Google Earth Pro (free, imagery back to ~1990s for most areas), county GIS historic aerial layers (some Ohio counties go back to the 1950s), historicaerials.com.

**Automatable?** **Partially** — Earth Pro is desktop-interactive; county historic layers with REST endpoints automate. Frame-to-frame change detection is a good vision task.

**Gotchas:** Imagery dates are approximate; resolution before ~2005 may be too coarse for small structures.

## 19. Oblique aerial roof review

**What it tells you:** Roof condition from four compass angles at high resolution — without a drone or a ladder.

**How to do it:** Many county GIS/auditor portals expose Pictometry/EagleView oblique imagery. Open the parcel, cycle N/S/E/W obliques, and inspect: shingle uniformity, patched sections, flashing at penetrations, chimney condition, gutter debris, sagging ridge lines.

**Data source specifics:** County portals with embedded Pictometry/EagleView viewers (availability varies by county); Bing Maps "bird's eye" as a fallback.

**Automatable?** **Partially** — where the viewer exposes tile URLs, capture automates; analysis is vision + checklist.

**Gotchas:** Oblique capture dates lag 1–3 years — a "bad" roof may have been replaced since (cross-check permits, Method 12).

## 20. Crime & registry

**What it tells you:** Incident density around the address and registered-offender proximity.

**How to do it:** Query the local PD's crime dashboard (or CityProtect/LexisNexis Community Crime Map) for a radius around the address, 12-month window, by category. Search the state sex-offender registry by address radius.

**Data source specifics:** Local PD dashboards, CityProtect, Ohio eSORN registry (address-radius search).

**Automatable?** **Partially** — some dashboards have APIs; many are interactive-only. Registry radius searches are scrapeable.

**Gotchas:** Reporting rates and dashboard coverage vary by agency — compare *within* a town, not across towns. Registry data changes; check again before closing.

## 21. Noise & nuisance

**What it tells you:** Chronic noise sources the seller won't mention: trains, flight paths, highways, and student-rental party density.

**How to do it:** Map rail lines within ~1 mi (trains horn at crossings); check flight paths via FAA sectionals and flight-tracker heatmaps (FlightAware/Flightradar24) for approach corridors; measure highway distance and prevailing wind; in college towns, assess fraternity/student-rental density around the block (rental registration data, Method 22, helps).

**Data source specifics:** OpenStreetMap/FRA rail layers, FAA sectionals, flight-tracker heatmaps, DOT traffic-count maps.

**Automatable?** **Yes** — mostly geospatial distance queries against public layers.

**Gotchas:** Nothing beats standing on the porch at 7am and 11pm on a weekend. Automate the screen; verify with your ears.

## 22. Rental & investment analysis

**What it tells you:** What the house rents for, whether the municipality regulates rentals, and whether the numbers work as an investment or house-hack.

**How to do it:** Pull rent comps (Zestimate rent, RentCast, Zumper listings). Check the municipal code for a **rental-permit/registration ordinance** — common in college towns: inspection requirements, occupancy caps ("no more than X unrelated"), fees. Model student-housing demand cycles (pre-leasing seasons) if applicable. Compute cap rate: NOI (rent − taxes − insurance − maintenance − vacancy − permit fees) ÷ price.

**Data source specifics:** Zillow rent Zestimate, RentCast API, municipal codes (amlegal/Municode), university enrollment trends.

**Automatable?** **Yes** — rent comps and cap-rate math fully; ordinance extraction is LLM-friendly.

**Gotchas:** Occupancy caps can make a "5-bedroom near campus" legally a 3-tenant house — read the ordinance before underwriting rent.

## 23. Insurance pre-quote

**What it tells you:** Whether the house is bindable at a sane price *before* you waive contingencies — roof age, knob-and-tube wiring, wood stoves, and dog-breed lists all cause declines or surcharges.

**How to do it:** Get 2–3 preliminary quotes with accurate inputs (roof age from permits/photos, wiring era from year built + panel photos, foundation type from the dwelling card). Ask specifically about roof-age surcharges and any exclusions.

**Data source specifics:** Carrier online quote flows, independent agents (fastest for edge cases).

**Automatable?** **Partially** — input assembly automates from earlier methods; quoting itself involves carrier forms and human agents.

**Gotchas:** Online quotes assume; agents verify. A pre-1960 house with original wiring may be surplus-lines-only — find out before, not at, closing.

## 24. Sewer lateral scope & water-main age

**What it tells you:** The condition of the buried pipe you own (house to main) and the age of the main serving the street — the two biggest surprise-repair line items ($5–15k+).

**How to do it:** Records-request the municipality/water district for the main's install date and material and any lateral records; then pay for a camera scope of the lateral during inspection (root intrusion, bellies, clay/Orangeburg pipe).

**Data source specifics:** Municipal public-works records (Ohio Public Records Act requests), private scope contractors (~$150–350).

**Automatable?** **No** — the records request can be templated, but the scope is physical and essential for any pre-1980 house with mature trees.

**Gotchas:** Orangeburg pipe (1945–1972 installs) fails by collapse with no warning — if the era matches, scope it, period.

## 25. Utility bill history

**What it tells you:** Real operating costs and anomalies (a water bill spike = leak; huge winter gas = insulation problems).

**How to do it:** Ask the seller for 12 months of bills for every utility; alternatively, some utilities provide address-level 12-month averages to prospective buyers on request.

**Data source specifics:** Seller-provided statements; utility customer-service address-average programs (availability varies).

**Automatable?** **No** — human ask; automate the request template and the parsing of delivered statements against the Method 8 estimate.

**Gotchas:** Normalize for occupancy — a vacant flip's bills tell you nothing about living costs.

## 26. Title pre-search & seller-side docs

**What it tells you:** What the seller already has that de-risks you for free: existing survey, HOA docs, warranties, transferable service contracts (HVAC plans, termite bonds, foundation warranties).

**How to do it:** Standard document request at offer time: prior survey, title policy, HOA covenants/budget/minutes, appliance and roof warranties, service contracts, disclosure forms. Read the prior title policy's exceptions list — it previews what your title search will find.

**Data source specifics:** Seller/listing agent; HOA management company.

**Automatable?** **No** — human request; automate the checklist and document parsing.

**Gotchas:** Many warranties (foundation, termite bond) are transferable only with a fee and a deadline — transfer them *at* closing or lose them.

## 27. Market-temperature stats

**What it tells you:** Whether you're in a buyer's or seller's market *at the zip level* — which calibrates how aggressive Method 10's strategy can be.

**How to do it:** Pull zip-level median DOM, list-to-sale price ratio, inventory months, and price trend. List-to-sale >100% and DOM <10 = offer strong and fast; ratio <97% and rising inventory = grind on price.

**Data source specifics:** Realtor.com research data (downloadable zip-level CSVs), Redfin Data Center (excellent free downloads), local MLS market reports.

**Automatable?** **Yes** — both sources publish machine-readable files.

**Status: partially implemented.** `house_recon.py` now scrapes the zip's Redfin housing-market page (median sale-to-list %, median DOM, median sale price, competitiveness label) and uses it to anchor the offer ladder's base discount and curve slope (Method 10a). Still untapped: trend direction, inventory months, price-band segmentation, and the bulk Redfin Data Center / Realtor.com downloads for historical calibration.

**Gotchas:** Zip-level stats blur distinct micro-markets (campus-adjacent vs suburban in the same zip). Segment by price band when sample size allows.

## 28. School-boundary verification

**What it tells you:** The *actual* assigned schools — listing claims are wrong often enough to matter.

**How to do it:** Use the district's own GIS/boundary-lookup tool (or call the district office with the address). Confirm the attendance zone for each school level, and check for pending rezoning proposals.

**Data source specifics:** District GIS lookups, county school-district GIS layers, board-meeting minutes for rezoning.

**Automatable?** **Partially** — where districts expose lookup endpoints, yes; many are phone-call territory.

**Gotchas:** District ≠ attendance zone — the right district with the wrong elementary is still a surprise. Boundaries change; verify near decision time.

## 29. Demographic & trend data

**What it tells you:** Whether the area is growing or declining — income, age mix, owner-occupancy rate, and new-construction activity as a leading indicator.

**How to do it:** Pull Census ACS 5-year data for the tract (median income, owner-occupancy, vacancy, population trend). Pull building permits issued in the jurisdiction over 3–5 years (growth signal).

**Data source specifics:** Census ACS API (tract-level), Census Building Permits Survey, county/municipal permit counts.

**Automatable?** **Yes** — Census APIs are free and well documented.

**Gotchas:** Tract boundaries don't match neighborhoods; check which tract the parcel actually sits in. ACS small-area estimates carry wide margins of error.

## 30. Absentee/portfolio-owner screen

**What it tells you:** Whether the seller is an investor — and how big their portfolio is. Portfolio owners negotiate on spreadsheet math, not emotion; distressed portfolio owners are motivated sellers.

**How to do it:** From the auditor record, compare owner mailing address vs property address (mismatch = absentee). Then run a **county-wide owner search** on the owner name/LLC to count their parcels. Cross-reference LLC names via the Ohio Secretary of State business search to find the principal and related entities.

**Data source specifics:** County auditor owner search (Method 3's portal), Ohio SoS business search (free).

**Automatable?** **Yes** — owner-name queries against the same auditor portal; SoS search is scrapeable.

**Gotchas:** LLCs obscure common ownership — matching the statutory agent or mailing address across LLCs finds the real portfolio. A one-property LLC may just be a family trust, not an investor.

---

## Ethics & Terms

This toolkit automates what a human can already view in a normal web browser, for **personal due-diligence use** on properties you are genuinely evaluating. Respect each site's terms of service and rate limits — fetch politely, cache aggressively, and don't hammer endpoints. Public records are public: county auditor, recorder, GIS, and census data exist precisely so citizens can inspect them. Nothing here replaces licensed professionals — **verify everything material with a home inspector, a real-estate agent, and an attorney** before you sign. Findings from these methods are leads and leverage, not conclusions.
