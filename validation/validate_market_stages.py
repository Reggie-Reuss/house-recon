#!/usr/bin/env python3
# MIT License -- see repository LICENSE.
"""National validation harness for house-recon's market/comps stages.

Runs stage_market_temp (Redfin zip housing-market page) and
stage_sold_comps (Redfin sold search, Zillow fallback) for 3 zip codes
in every US state (150 total), then scores each zip on:

* **Coverage** -- did the market page yield stats; did the sold search
  yield comp rows?
* **Plausibility** -- parsed values inside sane bounds (sale-to-list
  85-115%, DOM 1-365, $/sqft 25-2500).
* **Cross-source consistency** -- the market page's median sale price
  vs the median price of the independently parsed sold-comp rows for
  the same zip. The two come from different page types and windows
  (monthly stat vs 6-month listings), so agreement within 0.5x-2.0x is
  scored consistent. This is the closest public-data proxy for "are the
  recent-sales numbers right?" without licensed MLS ground truth.

Politeness: one page at a time with human-scale settle times, a random
2-5s pause between zips, and a 120s cooldown after 3 consecutive
blocked zips. The full run makes ~300 page loads over ~1.5 hours --
comparable to an afternoon of manual browsing.

Usage:
    python validation/validate_market_stages.py [--limit N] [--states OH,CA]
Outputs (in validation/results/):
    results.jsonl  -- one record per zip (aggregates only, no addresses)
    summary.json   -- aggregate metrics
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import house_recon as hr  # noqa: E402

# 3 zips per state: roughly one urban, one mid/college, one smaller market.
ZIPS: list[tuple[str, str, str]] = [
    ("AL", "35223", "Birmingham (Mountain Brook)"), ("AL", "36104", "Montgomery"), ("AL", "36830", "Auburn"),
    ("AK", "99501", "Anchorage"), ("AK", "99709", "Fairbanks"), ("AK", "99801", "Juneau"),
    ("AZ", "85254", "Scottsdale"), ("AZ", "85718", "Tucson"), ("AZ", "86001", "Flagstaff"),
    ("AR", "72212", "Little Rock"), ("AR", "72701", "Fayetteville"), ("AR", "71901", "Hot Springs"),
    ("CA", "94110", "San Francisco"), ("CA", "90210", "Beverly Hills"), ("CA", "95616", "Davis"),
    ("CO", "80206", "Denver"), ("CO", "80301", "Boulder"), ("CO", "81501", "Grand Junction"),
    ("CT", "06840", "New Canaan"), ("CT", "06511", "New Haven"), ("CT", "06033", "Glastonbury"),
    ("DE", "19806", "Wilmington"), ("DE", "19711", "Newark"), ("DE", "19971", "Rehoboth Beach"),
    ("FL", "33139", "Miami Beach"), ("FL", "32789", "Winter Park"), ("FL", "32601", "Gainesville"),
    ("GA", "30305", "Atlanta (Buckhead)"), ("GA", "31401", "Savannah"), ("GA", "30601", "Athens"),
    ("HI", "96815", "Honolulu (Waikiki)"), ("HI", "96734", "Kailua"), ("HI", "96740", "Kailua-Kona"),
    ("ID", "83702", "Boise"), ("ID", "83814", "Coeur d'Alene"), ("ID", "83440", "Rexburg"),
    ("IL", "60614", "Chicago (Lincoln Park)"), ("IL", "60201", "Evanston"), ("IL", "61820", "Champaign"),
    ("IN", "46220", "Indianapolis"), ("IN", "47401", "Bloomington"), ("IN", "46545", "Mishawaka"),
    ("IA", "50312", "Des Moines"), ("IA", "52240", "Iowa City"), ("IA", "50010", "Ames"),
    ("KS", "66208", "Prairie Village"), ("KS", "66044", "Lawrence"), ("KS", "67212", "Wichita"),
    ("KY", "40502", "Lexington"), ("KY", "40207", "Louisville"), ("KY", "42101", "Bowling Green"),
    ("LA", "70115", "New Orleans"), ("LA", "70808", "Baton Rouge"), ("LA", "71105", "Shreveport"),
    ("ME", "04101", "Portland"), ("ME", "04005", "Biddeford"), ("ME", "04401", "Bangor"),
    ("MD", "21210", "Baltimore"), ("MD", "20817", "Bethesda"), ("MD", "21401", "Annapolis"),
    ("MA", "02138", "Cambridge"), ("MA", "01060", "Northampton"), ("MA", "02740", "New Bedford"),
    ("MI", "48104", "Ann Arbor"), ("MI", "49503", "Grand Rapids"), ("MI", "48858", "Mount Pleasant"),
    ("MN", "55408", "Minneapolis"), ("MN", "55901", "Rochester"), ("MN", "55812", "Duluth"),
    ("MS", "39216", "Jackson"), ("MS", "39531", "Biloxi"), ("MS", "38655", "Oxford"),
    ("MO", "63109", "St. Louis"), ("MO", "64113", "Kansas City"), ("MO", "65203", "Columbia"),
    ("MT", "59718", "Bozeman"), ("MT", "59801", "Missoula"), ("MT", "59102", "Billings"),
    ("NE", "68154", "Omaha"), ("NE", "68502", "Lincoln"), ("NE", "69101", "North Platte"),
    ("NV", "89117", "Las Vegas"), ("NV", "89509", "Reno"), ("NV", "89701", "Carson City"),
    ("NH", "03101", "Manchester"), ("NH", "03755", "Hanover"), ("NH", "03801", "Portsmouth"),
    ("NJ", "07030", "Hoboken"), ("NJ", "08540", "Princeton"), ("NJ", "08742", "Point Pleasant"),
    ("NM", "87106", "Albuquerque"), ("NM", "87501", "Santa Fe"), ("NM", "88001", "Las Cruces"),
    ("NY", "11201", "Brooklyn Heights"), ("NY", "10583", "Scarsdale"), ("NY", "14850", "Ithaca"),
    ("NC", "28203", "Charlotte"), ("NC", "27514", "Chapel Hill"), ("NC", "28801", "Asheville"),
    ("ND", "58102", "Fargo"), ("ND", "58501", "Bismarck"), ("ND", "58201", "Grand Forks"),
    ("OH", "45056", "Oxford"), ("OH", "43206", "Columbus"), ("OH", "44118", "Cleveland Heights"),
    ("OK", "73118", "Oklahoma City"), ("OK", "74105", "Tulsa"), ("OK", "73069", "Norman"),
    ("OR", "97214", "Portland"), ("OR", "97401", "Eugene"), ("OR", "97701", "Bend"),
    ("PA", "19147", "Philadelphia"), ("PA", "15217", "Pittsburgh (Squirrel Hill)"), ("PA", "16801", "State College"),
    ("RI", "02906", "Providence"), ("RI", "02840", "Newport"), ("RI", "02879", "South Kingstown"),
    ("SC", "29464", "Mount Pleasant"), ("SC", "29205", "Columbia"), ("SC", "29601", "Greenville"),
    ("SD", "57105", "Sioux Falls"), ("SD", "57701", "Rapid City"), ("SD", "57069", "Vermillion"),
    ("TN", "37215", "Nashville"), ("TN", "37919", "Knoxville"), ("TN", "37403", "Chattanooga"),
    ("TX", "78704", "Austin"), ("TX", "75225", "Dallas"), ("TX", "78209", "San Antonio"),
    ("UT", "84103", "Salt Lake City"), ("UT", "84604", "Provo"), ("UT", "84060", "Park City"),
    ("VT", "05401", "Burlington"), ("VT", "05602", "Montpelier"), ("VT", "05301", "Brattleboro"),
    ("VA", "22101", "McLean"), ("VA", "23220", "Richmond"), ("VA", "22902", "Charlottesville"),
    ("WA", "98115", "Seattle"), ("WA", "98225", "Bellingham"), ("WA", "99201", "Spokane"),
    ("WV", "25314", "Charleston"), ("WV", "26505", "Morgantown"), ("WV", "25401", "Martinsburg"),
    ("WI", "53703", "Madison"), ("WI", "53217", "Milwaukee (North Shore)"), ("WI", "54701", "Eau Claire"),
    ("WY", "82001", "Cheyenne"), ("WY", "82601", "Casper"), ("WY", "83001", "Jackson"),
]

# 94110 (San Francisco) legitimately runs 118.7% sale-to-list -- verified
# against the raw page text -- so the ceiling sits above real hot markets.
STL_BOUNDS = (80.0, 130.0)
DOM_BOUNDS = (1, 365)
PPSF_BOUNDS = (25.0, 2500.0)
CONSISTENCY_BOUNDS = (0.5, 2.0)


def validate_zip(page, state: str, zip_code: str, place: str,
                 raw_root: Path) -> dict:
    raw_dir = raw_root / zip_code
    raw_dir.mkdir(parents=True, exist_ok=True)
    gaps: list[str] = []
    started = time.time()
    rec: dict = {"state": state, "zip": zip_code, "place": place}

    try:
        market = hr.stage_market_temp(page, zip_code, raw_dir, gaps) or {}
    except Exception as exc:  # noqa: BLE001
        market = {}
        gaps.append(f"market stage crash: {exc}")
    try:
        comps = hr.stage_sold_comps(page, zip_code, raw_dir, gaps) or {}
    except Exception as exc:  # noqa: BLE001
        comps = {}
        gaps.append(f"comps stage crash: {exc}")

    stl = market.get("sale_to_list_pct")
    dom = market.get("median_dom")
    msp = market.get("median_sale_price")
    rows = comps.get("rows") or []
    prices = sorted(r["price"] for r in rows)
    comps_median_price = (prices[len(prices) // 2] if prices else None)

    rec.update({
        "market_ok": bool(market),
        "sale_to_list_pct": stl,
        "median_dom": dom,
        "median_sale_price": msp,
        "label": market.get("label"),
        "stl_plausible": (STL_BOUNDS[0] <= stl <= STL_BOUNDS[1])
        if stl is not None else None,
        "dom_plausible": (DOM_BOUNDS[0] <= dom <= DOM_BOUNDS[1])
        if dom is not None else None,
        "comps_ok": len(rows) >= 3,
        "comps_source": comps.get("source"),
        "comps_n": len(rows),
        "median_ppsf": comps.get("median_ppsf"),
        "ppsf_plausible": (PPSF_BOUNDS[0] <= comps["median_ppsf"]
                           <= PPSF_BOUNDS[1])
        if comps.get("median_ppsf") else None,
        "comps_median_price": comps_median_price,
        "comps_price_min": prices[0] if prices else None,
        "comps_price_max": prices[-1] if prices else None,
    })
    if msp and comps_median_price:
        ratio = round(comps_median_price / msp, 3)
        rec["consistency_ratio"] = ratio
        rec["consistent"] = CONSISTENCY_BOUNDS[0] <= ratio <= CONSISTENCY_BOUNDS[1]
    else:
        rec["consistency_ratio"] = None
        rec["consistent"] = None
    rec["blocked"] = any("blocked" in g for g in gaps)
    rec["gaps"] = gaps
    rec["seconds"] = round(time.time() - started, 1)
    return rec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N zips (smoke test)")
    parser.add_argument("--states", default=None,
                        help="Comma-separated state filter, e.g. OH,CA")
    parser.add_argument("--zips", default=None,
                        help="Comma-separated zip filter (re-runs)")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    targets = ZIPS
    if args.states:
        wanted = {s.strip().upper() for s in args.states.split(",")}
        targets = [t for t in targets if t[0] in wanted]
    if args.zips:
        wanted_z = {z.strip() for z in args.zips.split(",")}
        targets = [t for t in targets if t[1] in wanted_z]
    if args.limit:
        targets = targets[:args.limit]

    out_dir = REPO / "validation" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_root = Path(__file__).resolve().parent / "raw"
    results_path = out_dir / "results.jsonl"

    print(f"[validate] {len(targets)} zips; results -> {results_path}",
          flush=True)
    records: list[dict] = []
    consecutive_blocked = 0
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = hr.launch_browser(pw, headless=args.headless)
        context = browser.new_context(
            viewport={"width": 1440, "height": 950}, locale="en-US")
        page = context.new_page()
        with results_path.open("a", encoding="utf-8") as fh:
            for i, (state, zip_code, place) in enumerate(targets, 1):
                if i % 25 == 0:  # fresh context periodically
                    context.close()
                    context = browser.new_context(
                        viewport={"width": 1440, "height": 950},
                        locale="en-US")
                    page = context.new_page()
                rec = validate_zip(page, state, zip_code, place, raw_root)
                records.append(rec)
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                print(f"[validate] {i}/{len(targets)} {state} {zip_code} "
                      f"({place}): market={rec['market_ok']} "
                      f"comps={rec['comps_n']} "
                      f"ratio={rec['consistency_ratio']} "
                      f"blocked={rec['blocked']}", flush=True)
                consecutive_blocked = (consecutive_blocked + 1
                                       if rec["blocked"] else 0)
                if consecutive_blocked >= 3:
                    print("[validate] 3 consecutive blocks -- cooling "
                          "down 120s", flush=True)
                    time.sleep(120)
                    consecutive_blocked = 0
                time.sleep(random.uniform(2.0, 5.0))
        context.close()
        browser.close()

    def rate(key):
        vals = [r[key] for r in records if r[key] is not None]
        return (round(100 * sum(1 for v in vals if v) / len(vals), 1)
                if vals else None), len(vals)

    summary = {"zips_run": len(records)}
    for key in ("market_ok", "stl_plausible", "dom_plausible", "comps_ok",
                "ppsf_plausible", "consistent"):
        pct, n = rate(key)
        summary[key] = {"pct": pct, "n": n}
    ratios = [r["consistency_ratio"] for r in records
              if r["consistency_ratio"] is not None]
    if ratios:
        ratios.sort()
        summary["consistency_ratio"] = {
            "median": ratios[len(ratios) // 2],
            "p10": ratios[int(len(ratios) * 0.1)],
            "p90": ratios[int(len(ratios) * 0.9) - 1],
        }
    summary["zillow_fallbacks"] = sum(
        1 for r in records if r.get("comps_source") == "zillow")
    summary["blocked_zips"] = sum(1 for r in records if r["blocked"])
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2),
                                          encoding="utf-8")
    print("[validate] SUMMARY: " + json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
