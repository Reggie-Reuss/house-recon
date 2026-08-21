#!/usr/bin/env python3
"""Regenerate the synthetic demo report in examples/.

Every fact below is FICTIONAL -- the address, owner, parcel, prices, and
comps do not exist. The synthetic page text is deliberately shaped like
real Zillow/Redfin innerText so it exercises the production parsers in
house_recon.py end-to-end; the report is then produced by the normal
`--resume` pipeline, exactly as it would be for a real address.

Run from the repo root:

    python examples/make_demo.py

The demo listing tells a deliberately ugly story so every analysis
feature fires: a 2021 flip (+93% in six months), an assessment jump with
no permits on file, a failed pending, a relist, $40K of cuts, an
absentee LLC owner, and a listing/county fuel contradiction.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import house_recon as hr  # noqa: E402

ADDRESS = "742 Juniper Hollow Ln, Anytown, OH 45099"
ZIP = "45099"

# --- Synthetic Zillow listing innerText (fictional) ------------------------

ZILLOW_TEXT = """\
742 Juniper Hollow Ln, Anytown, OH 45099
$289,900
3 beds
2 baths
1,624 sqft
742 Juniper Hollow Ln, Anytown, OH 45099
Single family residence
Built in 1962
0.34 Acres
$179/sqft
$-- HOA

What's special
Fully renovated ranch on a quiet street! New LVP flooring, quartz counters,
stainless appliances, and a cozy gas fireplace in the family room. New roof
2021. Move-in ready -- nothing to do but unpack. Seller motivated, bring all
offers!
88 days on Zillow

Facts & features
Interior
Bedrooms: 3
Bathrooms: 2
Full bathrooms: 2
Heating: Heat pump
Cooling: Central Air
Appliances included: Dishwasher, Microwave, Range
Flooring: Luxury Vinyl Plank, Carpet
Total interior livable area: 1,624 sqft
Parking
Total spaces: 2
Parking features: Attached Garage
Construction
Home type: Single Family
Foundation: Crawl Space
Roof: Asphalt Shingle
Year built: 1962
Other
Date on market: 5/14/2026
MLS#: 912345678
Parcel number: Z99-0000-000-000
Annual tax amount: $2,610
Tax assessed value: $170,800
Services availability

Price history
6/25/2026
Price change
$289,900 -3.3%
$179/sqft
Source: Sample MLS #912345678
5/14/2026
Listed for sale
$299,900 -4.8%
$185/sqft
Source: Sample MLS #912345678
11/21/2025
Listing removed
$314,900
$194/sqft
Source: Sample MLS #909111222
10/6/2025
Back on market
$314,900
$194/sqft
Source: Sample MLS #909111222
9/2/2025
Pending sale
$314,900
$194/sqft
Source: Sample MLS #909111222
7/11/2025
Price change
$314,900 -4.5%
$194/sqft
Source: Sample MLS #909111222
5/20/2025
Listed for sale
$329,900
$203/sqft
Source: Sample MLS #909111222
12/3/2021
Sold
$228,000 +93.2%
$140/sqft
Source: Public Record
6/18/2021
Sold
$118,000
$73/sqft
Source: Public Record

Public tax history
2025 $2,610 +2.1% $170,800
2024 $2,556 +1.8% $167,300
2023 $2,511 +15.9% $164,900
2022 $2,167 +38.2% $142,300
2021 $1,568 +0.5% $103,900
Monthly payment calculator

Nearby schools
Anytown Elementary School
7/10
Grades: K-5 Distance: 0.8 mi
Anytown Middle School
6/10
Grades: 6-8 Distance: 1.4 mi
Anytown High School
7/10
Grades: 9-12 Distance: 2.1 mi

Climate risks
Flood Factor: Minimal - this property has a minimal chance of flooding
over the next 30 years.

Nearby homes
$276,500
3 bd | 2 ba | 1,590 sqft
$301,200
4 bd | 2 ba | 1,880 sqft
$254,900
3 bd | 1 ba | 1,410 sqft
"""

# --- Synthetic Redfin housing-market innerText (fictional zip) -------------

MARKET_TEXT = """\
Anytown, OH 45099 Housing Market
The 45099 housing market is very competitive. Homes in 45099 receive
3 offers on average and sell in around 21 days. The median sale price of
a home in 45099 was $265K last month, up 4.3% since last year.

Median Sale Price
$265K
+4.3% year-over-year

Median Days on Market
21
-3 year-over-year

Sale-to-List Price
98.4%
+0.6 pt year-over-year

Homes Sold Above List Price
31.2%

Compete Score
Very Competitive
Homes in 45099 sell for about 1.6% below list price and go pending in
around 21 days. Hot homes can sell for around list price and go pending
in around 7 days.
"""

# --- Synthetic Redfin sold-search innerText (fictional comps) ---------------

SOLD_TEXT = """\
Recently sold homes in 45099 -- houses, last 6 months

SOLD JUL 18, 2026
$272,000
3 beds 2 baths 1,540 sq ft
418 Meadowlark Ct, Anytown, OH 45099

SOLD JUL 2, 2026
$296,500
3 beds 2 baths 1,710 sq ft
1203 Old Quarry Rd, Anytown, OH 45099

SOLD JUN 27, 2026
$248,000
3 beds 1.5 baths 1,390 sq ft
77 Sycamore Bend, Anytown, OH 45099

SOLD JUN 12, 2026
$315,000
4 beds 2.5 baths 1,940 sq ft
2145 Harvest Moon Dr, Anytown, OH 45099

SOLD JUN 3, 2026
$262,400
3 beds 2 baths 1,505 sq ft
930 Foxglove Ln, Anytown, OH 45099

SOLD MAY 22, 2026
$289,900
3 beds 2 baths 1,660 sq ft
501 Willow Gate Dr, Anytown, OH 45099

SOLD MAY 9, 2026
$233,500
2 beds 1 baths 1,120 sq ft
16 Carriage Hill Ct, Anytown, OH 45099

SOLD APR 30, 2026
$305,000
4 beds 2 baths 1,875 sq ft
88 Timber Ridge Trl, Anytown, OH 45099

SOLD APR 17, 2026
$585,000
5 beds 3.5 baths 3,420 sq ft
4 Stonebrook Estates Dr, Anytown, OH 45099

SOLD APR 4, 2026
$268,000
3 beds 2 baths 1,580 sq ft
1420 Prairie Rose Ln, Anytown, OH 45099

SOLD MAR 27, 2026
$199,900
2 beds 1 baths — sq ft
309 Mill Race Ave, Anytown, OH 45099

SOLD MAR 12, 2026
$281,750
3 beds 2 baths 1,630 sq ft
655 Juniper Hollow Ln, Anytown, OH 45099

SOLD FEB 26, 2026
$310,500
4 beds 3 baths 2,050 sq ft
1808 Beacon Field Rd, Anytown, OH 45099

SOLD FEB 6, 2026
$243,000
3 beds 2 baths 1,470 sq ft
242 Larkspur Ct, Anytown, OH 45099
"""

# --- Synthetic county auditor record (parse_auditor_text output shape) -----

COUNTY = {
    "owner": "SAMPLE HOLDINGS LLC",
    "mailing": "Mailing Name 1: SAMPLE HOLDINGS LLC; PO BOX 4821; "
               "COLUMBUS OH 43004",
    "parcel_id": "Z99-0000-000-000",
    "total_acres": "0.3400",
    "taxing_district": "T99",
    "district_name": "ANYTOWN TWP",
    "gross_tax_rate": "62.400000",
    "effective_tax_rate": "52.104213",
    "non_business_credit": "8.6512%",
    "owner_occupied_credit": "0.0000%",
    "dwelling": {
        "Year Built": "1962", "Effective Year": "2021", "Stories": "1.0",
        "Construction": "FRAME", "Basement": "CRAWL", "Bedrooms": "3",
        "Full Baths": "2", "Half Baths": "0", "Heat System": "HEAT PUMP",
        "Fuel Type": "ELECTRIC", "Total Living Area": "1,624",
    },
    "tentative_value": {"land": 24500, "building": 187300, "total": 211800,
                        "total_assessed": 74130},
    "current_value": {"land": 21000, "building": 149800, "total": 170800,
                      "total_assessed": 59780},
    "transfers": [
        {"date": "21-JUN-2021", "amount": 118000},
        {"date": "07-DEC-2021", "amount": 228000},
    ],
    "value_history": [
        {"year": 2020, "land": 19000, "building": 84200, "total": 103200},
        {"year": 2021, "land": 19000, "building": 84900, "total": 103900},
        {"year": 2022, "land": 21000, "building": 121300, "total": 142300},
        {"year": 2023, "land": 21000, "building": 143900, "total": 164900},
        {"year": 2024, "land": 21000, "building": 146300, "total": 167300},
        {"year": 2025, "land": 21000, "building": 149800, "total": 170800},
    ],
    "permits": "none on file",
    "tax_distribution": ["ANYTOWN CSD Subtotal 58.70%", "TOTAL 100.00%"],
    "topography": "LEVEL",
}

DEMO_BANNER = """\
> **SYNTHETIC DEMO** -- every fact in this report is fictional. The address,
> owner, parcel, prices, and comps do not exist; they were generated by
> [`examples/make_demo.py`](../make_demo.py) to exercise every analysis
> feature (flip detection, failed pending, relist, price cuts, unpermitted
> renovation, absentee owner, listing/county contradiction, empirical offer
> ladder). This is what a real run's `report.md` looks like.

"""


def main() -> int:
    zillow = hr.parse_zillow_text(ZILLOW_TEXT)
    zillow["price_history"] = hr.parse_price_history_block(ZILLOW_TEXT)
    zillow["tax_history"] = hr.parse_tax_history_block(ZILLOW_TEXT)
    zillow["url"] = "https://www.zillow.com/homes/(synthetic-demo)"

    market = hr.parse_market_text(MARKET_TEXT)
    market["source"] = f"https://www.redfin.com/zipcode/{ZIP}/housing-market"

    rows = hr.parse_sold_comps(SOLD_TEXT, ZIP)
    ppsf_vals = [r["ppsf"] for r in rows if r.get("ppsf")]
    comps = {
        "source": "redfin",
        "url": f"https://www.redfin.com/zipcode/{ZIP}/filter/"
               "include=sold-6mo,property-type=house",
        "n": len(rows), "n_with_sqft": len(ppsf_vals),
        "median_ppsf": round(hr._median(ppsf_vals), 1) if ppsf_vals else None,
        "rows": rows,
    }

    data = {"zillow": zillow, "trulia": {"events": []}, "county": COUNTY,
            "maps": {}, "market": market, "comps": comps}

    out_dir = REPO / "examples" / hr.slugify(ADDRESS)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "parsed.json").write_text(
        json.dumps(json.loads(json.dumps(data, default=str)), indent=2),
        encoding="utf-8")
    (raw_dir / "zillow.txt").write_text(ZILLOW_TEXT, encoding="utf-8")
    print(f"[make_demo] synthetic inputs written to {raw_dir}")

    # Produce the report through the real pipeline (analysis + report only).
    proc = subprocess.run(
        [sys.executable, str(REPO / "house_recon.py"), ADDRESS,
         "--out", str(REPO / "examples"), "--resume"],
        cwd=REPO)
    if proc.returncode != 0:
        return proc.returncode

    report_path = out_dir / "report.md"
    body = report_path.read_text(encoding="utf-8")
    title, _, rest = body.partition("\n")
    report_path.write_text(f"{title}\n\n{DEMO_BANNER}{rest.lstrip()}",
                           encoding="utf-8")
    print(f"[make_demo] demo banner prepended to {report_path}")

    # Keep the web analyzer's "Load demo data" button in sync (docs/).
    demo_js = REPO / "docs" / "demo-data.js"
    demo_js.write_text(
        "/* GENERATED by examples/make_demo.py -- do not edit by hand.\n"
        " * Synthetic, fictional demo inputs for the browser analyzer. */\n\n"
        f"const DEMO_ADDRESS = {json.dumps(ADDRESS)};\n"
        f"const DEMO_ZIP = {json.dumps(ZIP)};\n"
        f"const DEMO_ZILLOW_TEXT = {json.dumps(ZILLOW_TEXT)};\n"
        f"const DEMO_MARKET_TEXT = {json.dumps(MARKET_TEXT)};\n"
        f"const DEMO_SOLD_TEXT = {json.dumps(SOLD_TEXT)};\n",
        encoding="utf-8")
    print(f"[make_demo] web demo data written to {demo_js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
