#!/usr/bin/env python3
# MIT License
#
# Copyright (c) 2026 Reggie Reuss
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""house_recon.py -- automated residential real-estate due diligence.

Python port of a working Node/playwright-core pipeline. Given a street
address it collects data from Zillow, Trulia (via Bing), the Butler
County OH auditor, and Google Maps, then writes a Markdown due-diligence
report with a merged listing timeline, tax projections, red flags, a
photo/condition review checklist, and a raw-data appendix.

Every stage is fault-isolated: a single site failure never kills the
run; gaps are listed in the report. Exit code 0 on any (even partial)
report, 2 on total failure.

Two run modes:

* **Offline (default)** -- no AI, no API key, no network beyond the
  scraped sites. Includes a heuristic seller-motivation score and an
  offer ladder with estimated acceptance odds, derived purely from
  listing-history signals.
* **AI judgment layer (``--ai``)** -- adds a Claude-powered pass
  (default model ``claude-opus-5`` via the official ``anthropic`` SDK):
  per-photo condition review, work items with cost ranges, answered
  visual checklist, an AI-refined offer ladder with rationale, and a
  bottom-line verdict. Requires ``pip install anthropic`` plus API
  credentials (``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile).
  Use ``--resume`` to add AI analysis to a prior run without
  re-scraping.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import math
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Optional

import requests

try:
    from playwright.sync_api import Browser, Page, sync_playwright
except ImportError:  # pragma: no cover
    print("playwright is not installed. Run: pip install -r requirements.txt")
    print("Then: python -m playwright install chromium")
    sys.exit(2)

# --------------------------------------------------------------------------
# Constants: selectors, URLs, vocabularies
# --------------------------------------------------------------------------

ZILLOW_HOME = "https://www.zillow.com/homes/{slug}_rb/"
ZILLOW_PHOTO_RE = re.compile(r"https://photos\.zillowstatic\.com/fp/([a-f0-9]+)-p_d\.jpg")
ZILLOW_PHOTO_FULL = "https://photos.zillowstatic.com/fp/{h}-o_a.jpg"
ZILLOW_NEXT_DATA_JS = (
    "() => { const el = document.querySelector('#__NEXT_DATA__');"
    " return el ? el.textContent : null; }"
)
BODY_TEXT_JS = "() => document.body.innerText"
ALL_LINKS_JS = "() => Array.from(document.querySelectorAll('a')).map(a => a.href)"

BING_SEARCH = "https://www.bing.com/search?q={q}"
DDG_SEARCH = "https://duckduckgo.com/html/?q={q}"

BUTLER_SEARCH_URL = (
    "https://propertysearch.bcohio.gov/search/commonsearch.aspx?mode=address"
)
BUTLER_AGREE_SEL = '#btAgree, input[value*="Agree"], button:has-text("Agree")'
BUTLER_NUMBER_SEL = "#inpNumber"
BUTLER_STREET_SEL = "#inpStreet"
BUTLER_RESULTS_SEL = "tr.SearchResults, #searchResults tbody tr"
BUTLER_TABS = [
    "Residential", "Value History", "Tax Summary", "Current Tax Distribution",
    "Permits", "Out Buildings", "Land", "Special Assessments",
]
BUTLER_UNAVAILABLE = "System is currently unavailable"

MAPS_PLACE_URL = "https://www.google.com/maps/place/{q}/"
MAPS_SAT_URL = "https://www.google.com/maps/@{lat},{lng},90m/data=!3m1!1e3"
MAPS_HERO_SEL = 'button[jsaction*="heroHeaderImage"], div[role="img"] img, .ZKCDEc img'

ZILLOW_EVENTS = (
    "Listed for sale", "Listing removed", "Pending sale", "Back on market",
    "Price change", "Sold", "Contingent", "Listed for rent",
)
LISTING_OPENERS = ("Listed for sale", "Back on market")
LISTING_CLOSERS = ("Listing removed", "Pending sale", "Sold")

STREET_SUFFIXES = {
    "rd", "road", "dr", "drive", "st", "street", "ave", "avenue", "ln", "lane",
    "ct", "court", "blvd", "boulevard", "way", "pl", "place", "cir", "circle",
    "ter", "terrace", "pike", "hwy", "highway", "trl", "trail", "pkwy", "parkway",
}

BUTLER_OH_ZIPS = {
    "45003", "45004", "45011", "45012", "45013", "45014", "45015", "45018",
    "45042", "45043", "45044", "45050", "45053", "45055", "45056", "45061",
    "45062", "45063", "45064", "45067", "45069", "45071",
}
BUTLER_OH_CITIES = {
    "hamilton", "fairfield", "middletown", "oxford", "trenton", "monroe",
    "west chester", "liberty township", "liberty twp", "ross", "seven mile",
    "somerville", "college corner", "new miami", "millville", "okeana",
    "shandon", "collinsville",
}

DWELLING_FIELDS = [
    "Year Built", "Effective Year", "Stories", "Construction", "Basement",
    "Bedrooms", "Full Baths", "Half Baths", "Heat System", "Fuel Type",
    "Total Living Area",
]

VISUAL_CHECKLIST = [
    "Roof uniformity: shingle color/age consistent? Patches suggest spot repairs.",
    "Water heater: fuel type (electric vs gas) and manufacture-date sticker age.",
    "Drainage/downspouts: extensions present? Grading slopes away from foundation?",
    "Driveway cracking: settlement, heaving, or root damage.",
    "Vegetation vs structure: ivy/shrubs contacting siding or foundation.",
    "Trees over roofline: overhanging limbs, gutter debris load, fall risk.",
    "Lot slope: steep grade, retaining walls, erosion, standing water.",
    "Floor plan: choppy layout, converted spaces, odd room transitions.",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

TODAY = dt.date.today()

# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------


def log(msg: str) -> None:
    """Print a progress line to stdout."""
    print(f"[house-recon] {msg}", flush=True)


def slugify(text: str) -> str:
    """Lowercase; every run of non-alphanumerics becomes a single '-'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_money(raw: Optional[str]) -> Optional[int]:
    """'$123,456' or '123,456' -> 123456; None on miss."""
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def parse_pct(raw: Optional[str]) -> Optional[float]:
    """Extract a fraction from '9.0640%' / '0.0906' style strings."""
    if not raw:
        return None
    m = re.search(r"([\d.]+)", raw)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return val / 100.0 if val > 0.5 else val


def parse_date_any(raw: Optional[str]) -> Optional[dt.date]:
    """Parse 'MM/DD/YYYY' or 'DD-MON-YYYY'; None on failure."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime(raw.title() if "-" in raw else raw, fmt).date()
        except ValueError:
            continue
    return None


def detect_county(address: str) -> str:
    """Auto-detect county adapter: 'butler-oh' on ZIP/city match, else 'none'."""
    low = address.lower()
    if any(z in address for z in BUTLER_OH_ZIPS):
        return "butler-oh"
    if any(city in low for city in BUTLER_OH_CITIES) and ("oh" in low or "ohio" in low):
        return "butler-oh"
    return "none"


def _strip_underscore_dates(obj: Any) -> None:
    """Remove private '_date' keys in-place after a JSON round-trip.

    parsed.json serializes dt.date values as strings; downstream date math
    (e.g. detect_flip) must re-parse from the public 'date' field instead.
    """
    if isinstance(obj, dict):
        obj.pop("_date", None)
        for value in obj.values():
            _strip_underscore_dates(value)
    elif isinstance(obj, list):
        for value in obj:
            _strip_underscore_dates(value)


def safe_stage(name: str, gaps: list[str], fn: Callable[..., Any],
               *args: Any, **kwargs: Any) -> Any:
    """Run a stage; on any exception record a data gap and return None."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- one failure never kills the run
        log(f"STAGE FAILED ({name}): {exc}")
        gaps.append(f"{name}: {exc}")
        return None


# --------------------------------------------------------------------------
# Browser plumbing
# --------------------------------------------------------------------------


STEALTH_ARGS = ["--disable-blink-features=AutomationControlled"]
STEALTH_IGNORE = ["--enable-automation"]


def launch_browser(pw: Any, headless: bool) -> Browser:
    """Launch Chrome, falling back to Edge, then bundled Chromium."""
    for channel in ("chrome", "msedge"):
        try:
            browser = pw.chromium.launch(channel=channel, headless=headless,
                                         args=STEALTH_ARGS,
                                         ignore_default_args=STEALTH_IGNORE)
            log(f"Browser launched: channel={channel} headless={headless}")
            return browser
        except Exception as exc:  # noqa: BLE001
            log(f"Could not launch channel={channel}: {exc}")
    try:
        browser = pw.chromium.launch(headless=headless, args=STEALTH_ARGS,
                                     ignore_default_args=STEALTH_IGNORE)
        log("Browser launched: bundled chromium")
        return browser
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "No browser available. Run: python -m playwright install chromium"
        ) from exc


def read_page(page: Page, want_next_data: bool = False,
              scrolls: int = 12, settle: float = 7.0) -> tuple[str, Optional[str]]:
    """Wait, lazy-load scroll, then return (body innerText, #__NEXT_DATA__)."""
    time.sleep(settle)
    for _ in range(scrolls):
        page.mouse.wheel(0, 1100)
        time.sleep(0.5)
    text: str = page.evaluate(BODY_TEXT_JS) or ""
    next_data: Optional[str] = None
    if want_next_data:
        next_data = page.evaluate(ZILLOW_NEXT_DATA_JS)
    return text, next_data


def capture_page(page: Page, url: str, screenshot: Optional[Path] = None,
                 want_next_data: bool = False, scrolls: int = 12,
                 settle: float = 7.0) -> tuple[str, Optional[str]]:
    """goto + read_page + optional screenshot (proven capture logic)."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    text, next_data = read_page(page, want_next_data, scrolls, settle)
    if screenshot is not None:
        try:
            page.screenshot(path=str(screenshot))
        except Exception:  # noqa: BLE001
            pass
    return text, next_data


# --------------------------------------------------------------------------
# Stage 1 -- Zillow
# --------------------------------------------------------------------------


def build_zillow_url(address: str) -> str:
    """Spaces -> '-', commas kept then URL-encoded; Zillow redirects to
    /homedetails/ on an exact match."""
    slug = urllib.parse.quote(address.strip().replace(" ", "-"), safe="-")
    return ZILLOW_HOME.format(slug=slug)


def try_press_and_hold(page: Page) -> bool:
    """Attempt the PerimeterX 'Press & Hold' challenge; True if attempted."""
    for sel in ("#px-captcha", 'div[id*="px-captcha"]'):
        el = page.locator(sel)
        try:
            if el.count() == 0:
                continue
            box = el.first.bounding_box()
            if not box:
                continue
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            log("Attempting Press & Hold challenge...")
            page.mouse.move(x, y)
            page.mouse.down()
            time.sleep(11)
            page.mouse.up()
            time.sleep(6)
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"Press & Hold attempt failed: {exc}")
    return False


def stage_zillow(page: Page, address: str, url: str, raw_dir: Path,
                 gaps: list[str]) -> dict[str, Any]:
    """Capture and parse the Zillow home-details page."""
    log(f"Stage 1: Zillow -> {url}")
    text, next_data = capture_page(page, url, raw_dir / "zillow.png",
                                   want_next_data=True)
    if len(text) < 2000 or "Press & Hold" in text:
        log("Zillow bot-check suspected; retrying once after 8s...")
        if "Press & Hold" in text:
            try_press_and_hold(page)
        time.sleep(8)
        text, next_data = capture_page(page, url, raw_dir / "zillow.png",
                                       want_next_data=True)
    if "Press & Hold" in text and try_press_and_hold(page):
        text, next_data = read_page(page, want_next_data=True, settle=3.0)
        if "/homedetails/" not in page.url and len(text) < 2000:
            text, next_data = capture_page(page, url, raw_dir / "zillow.png",
                                           want_next_data=True)
    result: dict[str, Any] = {"url": page.url, "blocked": False}
    if len(text) < 2000 or "Press & Hold" in text:
        result["blocked"] = True
        gaps.append("Zillow: blocked by bot check (Press & Hold)")
        log("Zillow blocked by bot check; continuing.")
    if "/homedetails/" not in page.url and not result["blocked"]:
        num_match = re.match(r"\s*(\d+)", address)
        number = num_match.group(1) if num_match else ""
        link = page.locator(f'a[href*="/homedetails/"]:has-text("{number}")')
        try:
            if number and link.count() > 0:
                log("Search page shown; clicking first matching result...")
                link.first.click()
                page.wait_for_load_state("domcontentloaded")
                text, next_data = read_page(page, want_next_data=True, settle=5.0)
                result["url"] = page.url
            else:
                gaps.append("Zillow: no /homedetails/ redirect and no result link")
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Zillow: result click failed: {exc}")
    (raw_dir / "zillow.txt").write_text(text, encoding="utf-8", errors="replace")
    if next_data:
        (raw_dir / "zillow_next_data.json").write_text(
            next_data, encoding="utf-8", errors="replace")
    result["text"] = text
    result["next_data"] = next_data
    if not result["blocked"]:
        result.update(parse_zillow_text(text))
        result["price_history"] = parse_price_history_block(text)
        result["tax_history"] = parse_tax_history_block(text)
        log(f"Zillow parsed: price={result.get('price')} "
            f"history events={len(result['price_history'])} "
            f"tax rows={len(result['tax_history'])}")
    return result


def parse_zillow_text(text: str) -> dict[str, Any]:
    """Tolerant regex parse of Zillow innerText; every field may be None."""
    out: dict[str, Any] = {}

    def rx(pattern: str, src: str = text, flags: int = 0) -> Optional[str]:
        m = re.search(pattern, src, flags)
        return m.group(1) if m else None

    top = text[:5000]
    out["price"] = parse_money(rx(r"\$([\d,]+)", top))
    out["beds"] = rx(r"([\d.]+)\s*\n?\s*beds?\b", top, re.IGNORECASE)
    out["baths"] = rx(r"([\d.]+)\s*\n?\s*baths?\b", top, re.IGNORECASE)
    out["sqft"] = rx(r"([\d,]+)\s*\n?\s*sqft\b", top, re.IGNORECASE)
    out["year_built"] = rx(r"Built in (\d{4})")
    out["lot_acres"] = rx(r"([\d.]+) Acres", flags=re.IGNORECASE)
    out["price_per_sqft"] = rx(r"\$([\d,]+)/sqft")
    out["mls"] = rx(r"MLS#?:?\s*(\d+)")
    out["parcel"] = rx(r"Parcel number:\s*(\S+)")
    out["date_on_market"] = rx(r"Date on market:\s*([\d/]+)")
    out["tax_assessed"] = parse_money(rx(r"Tax assessed value:\s*\$([\d,]+)"))
    out["annual_tax"] = parse_money(rx(r"Annual tax amount:\s*\$([\d,]+)"))

    desc = None
    ws = text.find("What's special")
    if ws >= 0:
        tail = text[ws + len("What's special"):]
        m = re.search(r"\n\s*\d+\s+days?\s+on Zillow", tail)
        desc = (tail[:m.start()] if m else tail[:800]).strip()
    out["description"] = desc

    facts_raw = None
    fs = text.find("Facts & features")
    if fs >= 0:
        fe = text.find("Services availability", fs)
        facts_raw = text[fs:fe if fe > fs else fs + 6000]
    out["facts_raw"] = facts_raw
    out["facts"] = ([ln.strip() for ln in facts_raw.splitlines() if ": " in ln]
                    if facts_raw else [])
    return out


def parse_price_history_block(text: str) -> list[dict[str, Any]]:
    """Parse Zillow 'Price history' rows: date, event, price, mls, source."""
    events: list[dict[str, Any]] = []
    start = text.find("Price history")
    if start < 0:
        return events
    end = text.find("Public tax history", start)
    block = text[start:end if end > start else start + 8000]
    lines = [ln.strip() for ln in block.splitlines()]
    for i, line in enumerate(lines):
        dm = re.match(r"^(\d{1,2}/\d{1,2}/\d{4})\b\s*(.*)$", line)
        if not dm:
            continue
        date_s, rest = dm.group(1), dm.group(2)
        event = price = mls = source = None
        for ev in ZILLOW_EVENTS:  # event may share the date's line
            if rest.startswith(ev):
                event = ev
                break
        for j in range(i + 1, min(i + 8, len(lines))):
            ln = lines[j]
            if re.match(r"^\d{1,2}/\d{1,2}/\d{4}\b", ln):
                break
            if event is None:
                for ev in ZILLOW_EVENTS:
                    if ln.startswith(ev):
                        event = ev
                        break
            if price is None:
                pm = re.match(r"^\$([\d,]+)", ln)
                if pm and not ln[pm.end():].startswith("/sqft"):
                    price = parse_money(pm.group(1))
            if "Source:" in ln:
                sm = re.search(r"Source:\s*(.+)$", ln)
                if sm:
                    source = re.sub(r"\s*Report$", "", sm.group(1).strip())
                    im = re.search(r"#(\w+)", source)
                    if im:
                        mls = im.group(1)
        if event:
            events.append({"date": date_s, "event": event, "price": price,
                           "mls": mls, "source": source or "Zillow"})
    return events


def parse_tax_history_block(text: str) -> list[dict[str, Any]]:
    """Parse Zillow 'Public tax history' rows: year, taxes, change%, assessment."""
    rows: list[dict[str, Any]] = []
    start = text.find("Public tax history")
    if start < 0:
        return rows
    end = text.find("Monthly payment", start)
    block = text[start:end if end > start else start + 4000]
    pat = re.compile(
        r"(\d{4})\s+\$([\d,]+)(?:\s*\(?\s*([+-][\d.]+)%\)?)?\s+\$([\d,]+)")
    for m in pat.finditer(block):
        rows.append({
            "year": int(m.group(1)),
            "taxes": parse_money(m.group(2)),
            "change_pct": float(m.group(3)) if m.group(3) else None,
            "assessment": parse_money(m.group(4)),
        })
    return rows


def extract_photo_hashes(next_data: Optional[str]) -> list[str]:
    """Ordered, de-duplicated photo hashes from the #__NEXT_DATA__ blob."""
    if not next_data:
        return []
    seen: dict[str, None] = {}
    for m in ZILLOW_PHOTO_RE.finditer(next_data):
        seen.setdefault(m.group(1))
    return list(seen)


def download_photos(hashes: list[str], photos_dir: Path) -> int:
    """Download full-size photos as photos/NN.jpg; skip failures."""
    photos_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    count = 0
    for idx, h in enumerate(hashes, 1):
        url = ZILLOW_PHOTO_FULL.format(h=h)
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 200 and resp.content:
                (photos_dir / f"{idx:02d}.jpg").write_bytes(resp.content)
                count += 1
        except Exception:  # noqa: BLE001 -- skip failures
            continue
    return count


# --------------------------------------------------------------------------
# Stage 2 -- Trulia (found via Bing)
# --------------------------------------------------------------------------


def normalize_event(raw: str) -> str:
    """Map Trulia/other event wording onto Zillow's vocabulary."""
    low = raw.lower()
    if "sold" in low:
        return "Sold"
    if ("pendingtoactive" in low or "pending to active" in low
            or "relist" in low or "back on market" in low):
        return "Back on market"
    if "pending" in low:
        return "Pending sale"
    if "contingent" in low:
        return "Contingent"
    if "price change" in low or "price changed" in low or "pricechange" in low:
        return "Price change"
    if "delisted" in low or "removed" in low:
        return "Listing removed"
    if "rent" in low:
        return "Listed for rent"
    if "listed" in low or "for sale" in low:
        return "Listed for sale"
    return raw.strip()


def _find_trulia_link(links: list[str], text: str) -> Optional[str]:
    """First trulia.com/p/ or /home/ URL in a link list or page text."""
    url = next((u for u in links
                if "trulia.com/p/" in u or "trulia.com/home/" in u), None)
    if not url:
        m = re.search(r"https://www\.trulia\.com/(?:p|home)/[^\s\"&]+", text)
        url = m.group(0) if m else None
    return url


def stage_trulia(page: Page, address: str, raw_dir: Path,
                 gaps: list[str]) -> dict[str, Any]:
    """Locate the Trulia page via a Bing search, capture and parse it."""
    street = address.split(",")[0].strip()
    city = address.split(",")[1].strip() if "," in address else ""
    query = f'"{street}" {city} trulia'
    bing_url = BING_SEARCH.format(q=urllib.parse.quote_plus(query))
    log(f"Stage 2: Trulia via Bing -> {bing_url}")
    page.goto(bing_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    text: str = page.evaluate(BODY_TEXT_JS) or ""
    links: list[str] = page.evaluate(ALL_LINKS_JS) or []
    (raw_dir / "bing.txt").write_text(
        text + "\n\n" + "\n".join(links), encoding="utf-8", errors="replace")
    trulia_url = _find_trulia_link(links, text)
    if not trulia_url:
        log("Bing gave no Trulia link; falling back to DuckDuckGo...")
        ddg_url = DDG_SEARCH.format(q=urllib.parse.quote_plus(query))
        page.goto(ddg_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        dtext: str = page.evaluate(BODY_TEXT_JS) or ""
        dlinks: list[str] = page.evaluate(ALL_LINKS_JS) or []
        decoded = []
        for u in dlinks:
            m = re.search(r"[?&]uddg=([^&]+)", u)
            decoded.append(urllib.parse.unquote(m.group(1)) if m else u)
        (raw_dir / "ddg.txt").write_text(
            dtext + "\n\n" + "\n".join(decoded), encoding="utf-8",
            errors="replace")
        trulia_url = _find_trulia_link(decoded, dtext)
    if not trulia_url:
        gaps.append("Trulia: no trulia.com/p/ or /home/ link found via Bing/DDG")
        return {"events": []}
    log(f"Trulia page: {trulia_url}")
    ttext, _ = capture_page(page, trulia_url, raw_dir / "trulia.png")
    (raw_dir / "trulia.txt").write_text(ttext, encoding="utf-8", errors="replace")
    events = parse_trulia_history(ttext)
    log(f"Trulia parsed: {len(events)} history events")
    return {"url": trulia_url, "events": events}


def parse_trulia_history(text: str) -> list[dict[str, Any]]:
    """Parse the 'Price History for' table: MM/DD/YYYY  $X  Event  Source."""
    events: list[dict[str, Any]] = []
    idx = text.find("Price History for")
    if idx < 0:
        return events
    block = text[idx:idx + 5000]
    for line in block.splitlines():
        m = re.match(r"^\s*(\d{1,2}/\d{1,2}/\d{4})\s+(.*)$", line)
        if not m:
            continue
        date, rest = m.group(1), m.group(2)
        pm = re.search(r"\$([\d,]+)", rest)
        price = parse_money(pm.group(1)) if pm else None
        rest_no_price = re.sub(r"\$[\d,]+[KM]?", "", rest).strip()
        parts = [p for p in re.split(r"\t|\s{2,}", rest_no_price) if p.strip()]
        event = parts[0].strip() if parts else rest_no_price
        source = parts[1].strip() if len(parts) > 1 else "Trulia"
        im = re.search(r"#(\w+)", source)
        mls = im.group(1) if im else None
        if event:
            events.append({"date": date, "event": normalize_event(event),
                           "price": price, "mls": mls, "source": source})
    return events


# --------------------------------------------------------------------------
# Stage 3 -- County adapter: butler-oh
# --------------------------------------------------------------------------


def split_street_address(address: str) -> tuple[str, str]:
    """'123 Maple Ave, Anytown, OH 45000' -> ('123', 'Maple')."""
    street_part = address.split(",")[0].strip()
    m = re.match(r"^(\d+)\s+(.*)$", street_part)
    if not m:
        return "", street_part
    number, rest = m.group(1), m.group(2)
    tokens = rest.split()
    while tokens and tokens[-1].rstrip(".").lower() in STREET_SUFFIXES:
        tokens = tokens[:-1]
    return number, " ".join(tokens)


def _butler_once(page: Page, number: str, street: str) -> str:
    """One pass of the proven Butler County auditor flow; returns profile text."""
    page.goto(BUTLER_SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(2.5)
    agree = page.locator(BUTLER_AGREE_SEL)
    if agree.count() > 0:
        agree.first.click()
        time.sleep(2.5)
    page.fill(BUTLER_NUMBER_SEL, number)
    page.fill(BUTLER_STREET_SEL, street)
    page.keyboard.press("Enter")
    time.sleep(4)
    rows = page.locator(BUTLER_RESULTS_SEL)
    if rows.count() > 0:
        rows.first.click()
        time.sleep(3.5)
    return page.evaluate(BODY_TEXT_JS) or ""


def stage_county_butler_oh(page: Page, address: str, raw_dir: Path,
                           gaps: list[str]) -> dict[str, Any]:
    """Butler County OH auditor: profile page + every data tab, then parse."""
    number, street = split_street_address(address)
    log(f"Stage 3: Butler County auditor -> number={number!r} street={street!r}")
    text = _butler_once(page, number, street)
    if BUTLER_UNAVAILABLE in text:
        log("Auditor system unavailable; sleeping 60s and retrying once...")
        time.sleep(60)
        text = _butler_once(page, number, street)
    combined = text
    tabs_hit = 0
    for label in BUTLER_TABS:
        try:
            tab = page.locator(f'a:has-text("{label}")')
            if tab.count() > 0:
                tab.first.click()
                time.sleep(2.2)
                tab_text = page.evaluate(BODY_TEXT_JS) or ""
                combined += f"\n========== TAB: {label} ==========\n{tab_text}"
                tabs_hit += 1
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"County tab {label}: {exc}")
    (raw_dir / "auditor.txt").write_text(combined, encoding="utf-8",
                                         errors="replace")
    log(f"County captured: profile + {tabs_hit} tabs")
    parsed = parse_auditor_text(combined)
    parsed["tabs_captured"] = tabs_hit
    return parsed


def _field_value(text: str, label: str) -> Optional[str]:
    """Value after 'Label' on the same line, else the next non-empty line."""
    pat = re.compile(rf"^\s*{re.escape(label)}\s*:?\s*(.*)$", re.MULTILINE)
    for m in pat.finditer(text):
        val = m.group(1).strip()
        if val:
            return val
        for nxt in text[m.end():].split("\n"):
            if nxt.strip():
                return nxt.strip()
    return None


def _tab_section(text: str, label: str) -> str:
    """Text of one '========== TAB: label ==========' section."""
    marker = f"========== TAB: {label} =========="
    idx = text.find(marker)
    if idx < 0:
        return ""
    rest = text[idx + len(marker):]
    nxt = rest.find("========== TAB:")
    return rest[:nxt] if nxt >= 0 else rest


_VALUE_BLOCK_PATTERNS = {
    "land": r"Land\s*(?:Value)?\s*\(100%\)\s*\$?\s*([\d,]+)",
    "building": r"Building\s*(?:Value)?\s*\(100%\)\s*\$?\s*([\d,]+)",
    "total": r"Total\s*(?:Value)?\s*\(100%\)\s*\$?\s*([\d,]+)",
    "land_assessed": r"Land\s*\(35%\)\s*\$?\s*([\d,]+)",
    "building_assessed": r"Building\s*\(35%\)\s*\$?\s*([\d,]+)",
    "total_assessed":
        r"(?:Assessed\s+Total|Total\s+Assessed)\s*\(35%\)\s*\$?\s*([\d,]+)",
}


def _value_block(text: str, heading: str) -> Optional[dict[str, Any]]:
    """Parse Land/Building/Total appraised(100%)/assessed(35%) after heading."""
    idx = text.find(heading)
    if idx < 0:
        return None
    block = text[idx:idx + 800]
    out: dict[str, Any] = {}
    for key, pat in _VALUE_BLOCK_PATTERNS.items():
        m = re.search(pat, block)
        if m:
            out[key] = parse_money(m.group(1))
    return out or None


def parse_auditor_text(text: str) -> dict[str, Any]:
    """Tolerant parse of the combined Butler County auditor text."""
    out: dict[str, Any] = {}
    out["owner"] = _field_value(text, "Owner 1")
    mailing: list[str] = []
    mm = re.search(r"^\s*Mailing Name 1.*$", text, re.MULTILINE)
    if mm:
        for ln in text[mm.start():].split("\n")[:8]:
            ln = ln.strip()
            if ln and not ln.startswith("=========="):
                mailing.append(ln)
            if len(mailing) >= 5:
                break
    out["mailing"] = "; ".join(mailing) if mailing else None
    for label, key in [
        ("Parcel Id", "parcel_id"), ("Total Acres", "total_acres"),
        ("Taxing District", "taxing_district"), ("District Name", "district_name"),
        ("Gross Tax Rate", "gross_tax_rate"),
        ("Effective Tax Rate", "effective_tax_rate"),
        ("Non Business Credit", "non_business_credit"),
        ("Owner Occupied Credit", "owner_occupied_credit"),
    ]:
        out[key] = _field_value(text, label)
    residential = _tab_section(text, "Residential") or text
    out["dwelling"] = {f: _field_value(residential, f) for f in DWELLING_FIELDS}
    out["tentative_value"] = _value_block(text, "2026 Tentative Value")
    out["current_value"] = _value_block(text, "Current Value")
    out["transfers"] = [
        {"date": m.group(1), "amount": parse_money(m.group(2))}
        for m in re.finditer(r"(\d{2}-[A-Z]{3}-\d{4})\s+\$([\d,]+)", text)
    ]
    out["transfers"].sort(
        key=lambda t: parse_date_any(t["date"]) or dt.date.min)
    vh_section = _tab_section(text, "Value History") or text
    out["value_history"] = [
        {"year": int(m.group(1)), "land": parse_money(m.group(2)),
         "building": parse_money(m.group(3)), "total": parse_money(m.group(4))}
        for m in re.finditer(
            r"^(\d{4})\s+\$([\d,]+)\s+\$([\d,]+)\s+\$([\d,]+)",
            vh_section, re.MULTILINE)
    ]
    permits_section = _tab_section(text, "Permits")
    if "-- No Data --" in permits_section:
        out["permits"] = "none on file"
    elif permits_section.strip():
        out["permits"] = "see raw/auditor.txt (Permits tab)"
    else:
        out["permits"] = None
    dist_section = _tab_section(text, "Current Tax Distribution")
    out["tax_distribution"] = [
        ln.strip() for ln in dist_section.split("\n")
        if "subtotal" in ln.lower() or ln.strip().upper().startswith("TOTAL")
    ]
    # Topography 1/2/3 rows live on the Residential tab's Factors card.
    topo_vals: list[str] = []
    for ln in text.splitlines():
        m = re.match(r"^\s*Topography\s*\d*\s*:?\s*(.*)$", ln)
        if m:
            val = m.group(1).strip()
            if val and val not in topo_vals:
                topo_vals.append(val)
    out["topography"] = "; ".join(topo_vals) if topo_vals else None
    return out


# --------------------------------------------------------------------------
# Stage 4 -- Google Maps street view + satellite
# --------------------------------------------------------------------------


def _drag(page: Page, x1: int, y1: int, x2: int, y2: int, steps: int) -> None:
    page.mouse.move(x1, y1)
    page.mouse.down()
    page.mouse.move(x2, y2, steps=steps)
    page.mouse.up()


def stage_maps(page: Page, address: str, maps_dir: Path,
               gaps: list[str]) -> dict[str, Any]:
    """Street-view pans + satellite screenshot via Google Maps."""
    maps_dir.mkdir(parents=True, exist_ok=True)
    url = MAPS_PLACE_URL.format(q=urllib.parse.quote_plus(address))
    log(f"Stage 4: Google Maps -> {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(9)

    def _coords_from_url() -> Optional[tuple[str, str]]:
        m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", page.url)
        if not m:
            m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", page.url)
        return (m.group(1), m.group(2)) if m else None

    coords = _coords_from_url()
    for _ in range(4):
        if coords:
            break
        time.sleep(3)
        coords = _coords_from_url()
    if coords:
        log(f"Coordinates: {coords[0]},{coords[1]}")
    try:
        page.locator(MAPS_HERO_SEL).first.click()
        time.sleep(9)
        page.screenshot(path=str(maps_dir / "streetview_1.png"))
        for i in (2, 3):
            _drag(page, 990, 500, 1410, 500, steps=12)
            time.sleep(3.5)
            page.screenshot(path=str(maps_dir / f"streetview_{i}.png"))
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"Maps street view: {exc}")
    if not coords:
        coords = _coords_from_url()  # street-view URLs carry @lat,lng
        if coords:
            log(f"Coordinates (from street view URL): {coords[0]},{coords[1]}")
    if coords:
        try:
            page.goto(MAPS_SAT_URL.format(lat=coords[0], lng=coords[1]),
                      wait_until="domcontentloaded", timeout=60000)
            time.sleep(10)
            page.screenshot(path=str(maps_dir / "satellite.png"))
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Maps satellite: {exc}")
    else:
        gaps.append("Maps: could not extract coordinates from URL")
    shots = sorted(p.name for p in maps_dir.glob("*.png"))
    log(f"Maps screenshots: {shots}")
    return {"coords": coords, "screenshots": shots}


# --------------------------------------------------------------------------
# Stage 4b -- Market temperature + sold comps (zip-level, empirical anchors)
# --------------------------------------------------------------------------

MARKET_REDFIN_URL = "https://www.redfin.com/zipcode/{zip}/housing-market"
SOLD_REDFIN_URL = ("https://www.redfin.com/zipcode/{zip}/filter/"
                   "include=sold-6mo,property-type=house")
SOLD_ZILLOW_URL = "https://www.zillow.com/{zip}/sold/"

_BLOCK_MARKERS = ("Access Denied", "Press & Hold", "denied", "unusual traffic")


def zip_from_address(address: str) -> Optional[str]:
    m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address.strip())
    return m.group(1) if m else None


def _looks_blocked(text: str) -> bool:
    return len(text) < 1500 or any(k.lower() in text.lower()
                                   for k in _BLOCK_MARKERS)


def parse_market_text(text: str) -> dict[str, Any]:
    """Zip-level market stats from a Redfin housing-market page's innerText."""
    out: dict[str, Any] = {}
    m = re.search(r"sale[-\s]?to[-\s]?list(?:\s*price)?\D{0,40}?([\d.]+)\s*%",
                  text, re.IGNORECASE | re.DOTALL)
    if m:
        val = float(m.group(1))
        if 50.0 <= val <= 150.0:
            out["sale_to_list_pct"] = val
    m = re.search(r"median days on market\D{0,20}?(\d{1,3})\b",
                  text, re.IGNORECASE | re.DOTALL)
    if m:
        out["median_dom"] = int(m.group(1))
    m = re.search(r"median sale price\D{0,20}?\$([\d,]+)K?",
                  text, re.IGNORECASE | re.DOTALL)
    if m:
        price = parse_money(m.group(1))
        if price:
            out["median_sale_price"] = price * 1000 if price < 10000 else price
    m = re.search(r"\b(buyer'?s market|seller'?s market|neutral market|"
                  r"most competitive|very competitive|somewhat competitive|"
                  r"not very competitive)\b", text, re.IGNORECASE)
    if m:
        out["label"] = m.group(1)
    m = re.search(r"sell(?:ing)?\s+(?:for\s+)?(?:about|around)?\s*"
                  r"([\d.]+)\s*%\s*(below|above)\s+list",
                  text, re.IGNORECASE)
    if m and "sale_to_list_pct" not in out:
        delta = float(m.group(1))
        out["sale_to_list_pct"] = round(
            100.0 - delta if m.group(2).lower() == "below" else 100.0 + delta, 1)
    return out


def parse_sold_comps(text: str, zip_code: str) -> list[dict[str, Any]]:
    """Sold rows (price/beds/[sqft]/address) from a sold-search page's innerText.

    Square footage is optional: several regions render sold cards with
    "-- sq ft" (counties/MLSs that don't publish it). Those rows still
    carry a usable sold price for median-price context; only rows with
    numeric sqft contribute $/sqft. National validation (VALIDATION.md)
    drove the wide price cap (ultra-luxury zips) and the ppsf sanity
    guard that replaced it as the parse-error filter.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    pattern = re.compile(
        r"\$([\d,]+)[\s\S]{0,90}?(\d+(?:\.\d)?)\s*(?:bds?|beds?)"
        r"[\s\S]{0,60}?([\d,]+|[—–-]{1,3})\s*(?:sq\.? ?ft|sqft)"
        r"[\s\S]{0,160}?([0-9][^\n$]{6,70}?" + re.escape(zip_code) + r")",
        re.IGNORECASE)
    for m in pattern.finditer(text):
        price = parse_money(m.group(1))
        sqft = parse_money(m.group(3)) if m.group(3)[0].isdigit() else None
        if not price or not (30_000 <= price <= 25_000_000):
            continue
        if sqft is not None and not (300 <= sqft <= 20_000):
            continue
        ppsf = round(price / sqft, 1) if sqft else None
        if ppsf is not None and not (10.0 <= ppsf <= 5_000.0):
            continue  # parse-error guard (replaces the old tight price cap)
        address = re.sub(r"\s+", " ", m.group(4)).strip()
        key = (price, address)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"price": price, "beds": float(m.group(2)), "sqft": sqft,
                     "ppsf": ppsf, "address": address})
    return rows


def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    return (values[mid] if len(values) % 2
            else (values[mid - 1] + values[mid]) / 2.0)


def stage_market_temp(page: Page, zip_code: str, raw_dir: Path,
                      gaps: list[str]) -> dict[str, Any]:
    """Zip market stats (sale-to-list, DOM, label) from Redfin."""
    url = MARKET_REDFIN_URL.format(zip=zip_code)
    log(f"Stage 4b: market temperature ({url})")
    text, _ = capture_page(page, url, raw_dir / "market.png",
                           scrolls=6, settle=5.0)
    (raw_dir / "market.txt").write_text(text, encoding="utf-8",
                                        errors="replace")
    if _looks_blocked(text):
        gaps.append("Market temp: Redfin housing-market page blocked/empty")
        return {}
    market = parse_market_text(text)
    if not market:
        gaps.append("Market temp: page loaded but no stats parsed")
        return {}
    market["source"] = url
    log(f"Market temp: {market}")
    return market


def stage_sold_comps(page: Page, zip_code: str, raw_dir: Path,
                     gaps: list[str]) -> dict[str, Any]:
    """Recent sold comps in the zip: Redfin sold search, Zillow fallback."""
    for source, url in (("redfin", SOLD_REDFIN_URL.format(zip=zip_code)),
                        ("zillow", SOLD_ZILLOW_URL.format(zip=zip_code))):
        log(f"Stage 4b: sold comps ({url})")
        try:
            text, _ = capture_page(page, url, raw_dir / f"sold_{source}.png",
                                   scrolls=8, settle=5.0)
            (raw_dir / f"sold_{source}.txt").write_text(
                text, encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Sold comps ({source}): {exc}")
            continue
        if _looks_blocked(text):
            gaps.append(f"Sold comps ({source}): page blocked/empty")
            continue
        rows = parse_sold_comps(text, zip_code)
        if not rows:
            gaps.append(f"Sold comps ({source}): no rows parsed")
            continue
        ppsf_vals = [r["ppsf"] for r in rows if r.get("ppsf")]
        median_ppsf = _median(ppsf_vals)
        if median_ppsf is None:
            gaps.append(f"Sold comps ({source}): {len(rows)} rows but none "
                        "carry sqft -- price context only, no $/sqft anchor")
        log(f"Sold comps: {len(rows)} rows from {source}, "
            f"median ${median_ppsf}/sqft ({len(ppsf_vals)} with sqft)")
        return {"source": source, "url": url, "n": len(rows),
                "n_with_sqft": len(ppsf_vals),
                "median_ppsf": round(median_ppsf, 1) if median_ppsf else None,
                "rows": rows[:40]}
    return {}


def comps_value_estimate(comps: dict[str, Any],
                         subject_sqft: Optional[int],
                         subject_beds: Optional[float] = None
                         ) -> Optional[dict[str, Any]]:
    """Subject market-value estimate from sold-comp $/sqft.

    Prefers comps within +/-35% of the subject's size (and +/-1 bed when
    both bed counts are known); falls back to the whole-zip median.
    """
    rows = [r for r in (comps or {}).get("rows") or []
            if r.get("sqft") and r.get("ppsf")]
    if not rows or not subject_sqft:
        return None
    similar = [r for r in rows
               if abs(r["sqft"] - subject_sqft) <= subject_sqft * 0.35
               and (subject_beds is None or r.get("beds") is None
                    or abs(r["beds"] - subject_beds) <= 1)]
    basis, ppsf = ("similar", _median([r["ppsf"] for r in similar])) \
        if len(similar) >= 3 else ("all", comps.get("median_ppsf"))
    if not ppsf:
        return None
    value = int(round(ppsf * subject_sqft / 500.0) * 500)
    return {"value": value, "ppsf": round(ppsf, 1), "basis": basis,
            "n_similar": len(similar), "n_all": len(rows)}


# --------------------------------------------------------------------------
# Stage 5 -- Analysis (pure functions; unit-testable)
# --------------------------------------------------------------------------


def merge_timeline(zillow_events: list[dict[str, Any]],
                   trulia_events: list[dict[str, Any]],
                   county_transfers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge sources into one chronological timeline, deduped on (date, event)."""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ev in list(zillow_events) + list(trulia_events):
        d = parse_date_any(ev.get("date"))
        key = (d.isoformat() if d else str(ev.get("date")),
               str(ev.get("event", "")).lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append({**ev, "_date": d})
    for tr in county_transfers:
        d = parse_date_any(tr.get("date"))
        key = (d.isoformat() if d else str(tr.get("date")),
               f"sold (county transfer) {tr.get('amount')}")
        if key in seen:
            continue
        seen.add(key)
        merged.append({"date": tr.get("date"), "event": "Sold (county transfer)",
                       "price": tr.get("amount"), "mls": None,
                       "source": "Butler County Auditor", "_date": d})
    merged.sort(key=lambda e: e["_date"] or dt.date.max)
    return merged


def cumulative_market_days(timeline: list[dict[str, Any]],
                           today: dt.date = TODAY) -> int:
    """Sum listed->removed/pending/sold spans per MLS id; open spans run to today."""
    by_mls: dict[Optional[str], list[dict[str, Any]]] = {}
    for ev in timeline:
        by_mls.setdefault(ev.get("mls"), []).append(ev)
    total = 0
    for events in by_mls.values():
        open_start: Optional[dt.date] = None
        for ev in events:
            d = ev.get("_date") or parse_date_any(ev.get("date"))
            if d is None:
                continue
            if ev.get("event") in LISTING_OPENERS and open_start is None:
                open_start = d
            elif ev.get("event") in LISTING_CLOSERS and open_start is not None:
                total += max((d - open_start).days, 0)
                open_start = None
        if open_start is not None:
            total += max((today - open_start).days, 0)
    return total


def count_relists(timeline: list[dict[str, Any]]) -> int:
    """Count 'Listed for sale' events after the first."""
    n = sum(1 for ev in timeline if ev.get("event") == "Listed for sale")
    return max(n - 1, 0)


def analyze_price_cuts(timeline: list[dict[str, Any]]) -> dict[str, Any]:
    """Event-to-event ask decreases: cut list, total $, and % from peak ask."""
    cuts: list[dict[str, Any]] = []
    peak: Optional[int] = None
    last_ask: Optional[int] = None
    for ev in timeline:
        price = ev.get("price")
        if not price or ev.get("event") not in ("Listed for sale", "Price change",
                                                "Back on market"):
            continue
        if last_ask is not None and price < last_ask:
            cuts.append({"date": ev.get("date"), "from": last_ask, "to": price})
        last_ask = price
        peak = price if peak is None else max(peak, price)
    total_cut = sum(c["from"] - c["to"] for c in cuts)
    pct = (round((peak - last_ask) / peak * 100, 1)
           if peak and last_ask is not None and last_ask < peak else 0.0)
    return {"cuts": cuts, "total_cut": total_cut, "pct_from_peak": pct,
            "peak": peak, "current_ask": last_ask}


def count_failed_pendings(timeline: list[dict[str, Any]]) -> int:
    """Pending sale followed by any non-Sold event before a Sold."""
    failed = 0
    events = [ev for ev in timeline if ev.get("event")]
    for i, ev in enumerate(events):
        if ev["event"] != "Pending sale":
            continue
        for nxt in events[i + 1:]:
            if nxt["event"].startswith("Sold"):
                break
            if nxt["event"] in ("Listed for sale", "Back on market",
                                "Price change", "Listing removed"):
                failed += 1
                break
    return failed


def detect_flip(timeline: list[dict[str, Any]]) -> Optional[str]:
    """Two sales <12mo apart with >30% gain, or 2+ transfers on the same day."""
    sales = [(ev.get("_date") or parse_date_any(ev.get("date")), ev.get("price"))
             for ev in timeline
             if str(ev.get("event", "")).startswith("Sold") and ev.get("price")]
    sales = [(d, p) for d, p in sales if d is not None]
    sales.sort()
    dates = [d for d, _ in sales]
    for d in set(dates):
        if dates.count(d) >= 2:
            return f"Two or more transfers recorded on {d.isoformat()}"
    for (d1, p1), (d2, p2) in zip(sales, sales[1:]):
        if (d2 - d1).days < 365 and p1 and p2 > p1 * 1.30:
            gain = round((p2 - p1) / p1 * 100)
            return (f"Sold {d1.isoformat()} for ${p1:,} then {d2.isoformat()} "
                    f"for ${p2:,} (+{gain}% in <12 months)")
    return None


def assessment_jump_years(value_history: list[dict[str, Any]]) -> list[int]:
    """Years where the building value rose 15%+ over the prior year."""
    jumps: list[int] = []
    rows = sorted((r for r in value_history if r.get("year")),
                  key=lambda r: r["year"])
    for prev, cur in zip(rows, rows[1:]):
        pb, cb = prev.get("building"), cur.get("building")
        if pb and cb and cb >= pb * 1.15:
            jumps.append(cur["year"])
    return jumps


def contradiction_checks(zillow: dict[str, Any],
                         county: dict[str, Any]) -> list[str]:
    """Listing-vs-county mismatches: bedrooms, foundation, fuel/heat."""
    issues: list[str] = []
    dwelling = county.get("dwelling") or {}
    z_beds, c_beds = zillow.get("beds"), dwelling.get("Bedrooms")
    if z_beds and c_beds:
        try:
            if abs(float(z_beds) - float(re.sub(r"[^\d.]", "", c_beds))) >= 1:
                issues.append(f"Bedrooms: listing says {z_beds}, county says {c_beds}")
        except ValueError:
            pass
    listing_text = " ".join(
        filter(None, [zillow.get("description") or "",
                      zillow.get("facts_raw") or ""])).lower()
    basement = (dwelling.get("Basement") or "").upper()
    if "slab" in listing_text and any(k in basement for k in ("CRAWL", "FULL", "PART")):
        issues.append(f"Foundation: listing mentions slab; county basement={basement}")
    fuel = (dwelling.get("Fuel Type") or "").upper()
    if "ELEC" in fuel and re.search(r"\b(?:natural )?gas\b", listing_text):
        issues.append(f"Fuel: listing mentions gas; county fuel={fuel}")
    if "GAS" in fuel and re.search(r"\belectric (heat|furnace)\b", listing_text):
        issues.append(f"Fuel: listing mentions electric heat; county fuel={fuel}")
    return issues


def steep_lot_flag(county: dict[str, Any]) -> bool:
    """Topography contains STEEP or ABOVE/BELOW STREET."""
    topo = (county.get("topography") or "").upper()
    return any(k in topo for k in ("STEEP", "ABOVE STREET", "BELOW STREET"))


def project_taxes(county: dict[str, Any],
                  current_annual_tax: Optional[int]) -> Optional[dict[str, Any]]:
    """assessed x effective_rate/1000, then x(1 - credits); show the math."""
    tent = county.get("tentative_value") or {}
    cur = county.get("current_value") or {}
    assessed = tent.get("total_assessed") or cur.get("total_assessed")
    if assessed is None:
        total = tent.get("total") or cur.get("total")
        assessed = round(total * 0.35) if total else None
    rate_raw = county.get("effective_tax_rate") or county.get("gross_tax_rate")
    if not assessed or not rate_raw:
        return None
    m = re.search(r"([\d.]+)", rate_raw)
    if not m:
        return None
    rate = float(m.group(1))
    gross = assessed * rate / 1000.0
    nbc = parse_pct(county.get("non_business_credit")) or 0.0
    occ = parse_pct(county.get("owner_occupied_credit")) or 0.0
    net = gross * (1 - nbc - occ)
    math = (f"${assessed:,} (assessed 35%) x {rate} mills / 1000 = "
            f"${gross:,.0f} gross; x (1 - {nbc:.4f} - {occ:.4f}) = "
            f"${net:,.0f} projected annual")
    delta = round(net - current_annual_tax) if current_annual_tax else None
    return {"assessed": assessed, "rate": rate, "gross": round(gross),
            "net": round(net), "math": math, "current": current_annual_tax,
            "delta": delta}


def build_red_flags(analysis: dict[str, Any], zillow: dict[str, Any],
                    county: dict[str, Any]) -> list[str]:
    """Auto-generated red-flag bullet list."""
    flags: list[str] = []
    if zillow.get("blocked"):
        flags.append("Zillow was bot-blocked; listing data incomplete.")
    if analysis.get("relist_count", 0) >= 1:
        flags.append(f"Re-listed {analysis['relist_count']} time(s) after the "
                     "first listing.")
    cuts = analysis.get("price_cuts") or {}
    if cuts.get("total_cut"):
        flags.append(f"Price cuts total ${cuts['total_cut']:,} "
                     f"({cuts['pct_from_peak']}% below peak ask).")
    if analysis.get("failed_pendings"):
        flags.append(f"{analysis['failed_pendings']} pending sale(s) fell through.")
    if analysis.get("flip_flag"):
        flags.append(f"Possible flip: {analysis['flip_flag']}")
    if analysis.get("unpermitted_reno_flag"):
        flags.append("Assessment jump with no permits on file -- possible "
                     f"unpermitted renovation (years: "
                     f"{analysis.get('assessment_jump_years')}).")
    for issue in analysis.get("contradictions") or []:
        flags.append(f"Listing/county contradiction -- {issue}")
    if analysis.get("steep_lot"):
        flags.append(f"Steep/offset lot per county topography: "
                     f"{county.get('topography')}")
    if analysis.get("cumulative_market_days", 0) > 180:
        flags.append(f"Cumulative time on market is "
                     f"{analysis['cumulative_market_days']} days.")
    return flags


# --------------------------------------------------------------------------
# Stage 5b: Offer strategy (offline heuristic -- no AI required)
# --------------------------------------------------------------------------

def absentee_owner_flag(address: str, county: dict[str, Any]) -> bool:
    """Owner tax-mailing address does not mention the property's street."""
    mailing = county.get("mailing") or ""
    number, street_name = split_street_address(address)
    probe = street_name or number
    return bool(mailing and probe and not re.search(
        rf"\b{re.escape(probe)}\b", mailing, re.IGNORECASE))


def seller_motivation(analysis: dict[str, Any], zillow: dict[str, Any],
                      county: dict[str, Any], absentee: bool) -> dict[str, Any]:
    """0-100 heuristic seller-motivation score with human-readable reasons.

    Weights: days on market (30), cuts off peak (20), failed pending (15),
    relists (10), absentee owner (10), ask >=25% over county value (10),
    flip pattern (5), large margin over cost basis (5).
    """
    score = 0.0
    reasons: list[str] = []
    dom = analysis.get("cumulative_market_days") or 0
    if dom:
        score += min(dom, 365) / 365 * 30
        if dom >= 60:
            reasons.append(f"{dom} cumulative days on market")
    cuts = analysis.get("price_cuts") or {}
    pct = cuts.get("pct_from_peak") or 0.0
    if pct:
        score += min(pct, 15.0) / 15.0 * 20
        reasons.append(f"ask already cut {pct}% from peak")
    failed = analysis.get("failed_pendings") or 0
    if failed:
        score += 15
        reasons.append(f"{failed} pending sale(s) fell through")
    relists = analysis.get("relist_count") or 0
    if relists:
        score += min(relists * 5, 10)
        reasons.append(f"re-listed {relists} time(s)")
    if absentee:
        score += 10
        reasons.append("absentee owner (tax mailing differs from property)")
    if analysis.get("flip_flag"):
        score += 5
        reasons.append("flip pattern (short hold / same-day deeds)")
    price = zillow.get("price")
    county_total = ((county.get("tentative_value") or {}).get("total")
                    or (county.get("current_value") or {}).get("total"))
    if price and county_total and price >= county_total * 1.25:
        score += 10
        reasons.append(f"ask is {round((price / county_total - 1) * 100)}% "
                       f"above county value (${county_total:,})")
    transfers = county.get("transfers") or []
    basis = transfers[-1].get("amount") if transfers else None
    if price and basis and price >= basis * 1.5:
        score += 5
        reasons.append(f"large margin over owner cost basis (${basis:,})")
    return {"score": round(min(score, 100.0), 1), "reasons": reasons}


def ladder_anchoring(motivation: dict[str, Any],
                     market: Optional[dict[str, Any]],
                     value_gap_pct: Optional[float]) -> dict[str, Any]:
    """Derive the ladder's d50/slope, empirically anchored when possible.

    Base discount: the zip's observed median sale-to-list discount
    (Method 27) when captured, else the 2%-fresh heuristic floor.
    Slope: stretched by the zip's median days on market (slower market
    -> flatter curve, more discount tolerance). The seller-motivation
    score adds up to 10 points of discount on top of the base, and the
    ask's gap vs the sold-comps value estimate (Method 11) shifts d50 by
    half the gap, capped at +/-5 points.
    """
    base, slope, mode = 2.0, 2.8, "heuristic"
    notes: list[str] = []
    stl = (market or {}).get("sale_to_list_pct")
    if stl:
        base = max(0.0, round(100.0 - stl, 1))
        mode = "empirical"
        notes.append(f"zip median sale-to-list {stl}% -> base discount "
                     f"{base}%")
    dom = (market or {}).get("median_dom")
    if dom:
        slope = round(min(4.5, max(2.0, 2.0 + dom / 60.0)), 2)
        notes.append(f"zip median DOM {dom} -> curve slope {slope}")
    score = (motivation or {}).get("score") or 0.0
    d50 = base + 10.0 * (score / 100.0)
    if score:
        notes.append(f"motivation {score}/100 -> +{10.0 * score / 100.0:.1f} "
                     "pts discount")
    if value_gap_pct is not None:
        shift = max(-5.0, min(5.0, 0.5 * value_gap_pct))
        d50 += shift
        if mode == "heuristic":
            mode = "comps-adjusted"
        notes.append(f"ask is {value_gap_pct:+.1f}% vs sold-comps value -> "
                     f"d50 {shift:+.1f} pts")
    return {"mode": mode, "d50": round(max(0.5, min(20.0, d50)), 1),
            "slope": slope, "notes": notes}


def estimate_offer_ladder(list_price: Optional[int],
                          motivation: dict[str, Any],
                          anchoring: Optional[dict[str, Any]] = None
                          ) -> list[dict[str, Any]]:
    """Offer rungs with estimated acceptance odds from a logistic curve.

    Without `anchoring`, the discount with ~50% acceptance odds (d50)
    scales with the motivation score: 2% for a fresh, clean listing up
    to 12% for a maximally distressed one. With `anchoring` (see
    ladder_anchoring), d50/slope come from observed zip-level stats and
    the sold-comps value gap. Either way these are calibrated estimates,
    not a fitted statistical model.
    """
    if not list_price:
        return []
    if anchoring:
        d50, slope = anchoring["d50"], anchoring["slope"]
    else:
        score = (motivation or {}).get("score") or 0.0
        d50 = 2.0 + 10.0 * (score / 100.0)
        slope = 2.8

    def odds(discount_pct: float) -> float:
        return 1.0 / (1.0 + math.exp((discount_pct - d50) / slope))

    rungs = sorted({0.0, round(d50 * 0.5, 1), round(d50, 1),
                    round(d50 * 1.5, 1)})
    ladder: list[dict[str, Any]] = []
    for d in rungs:
        offer = int(round(list_price * (1 - d / 100.0) / 500.0) * 500)
        p = odds(d)
        ladder.append({
            "discount_pct": d,
            "offer": offer,
            "pct_of_list": round(100.0 - d, 1),
            "acceptance_probability_pct": int(round(p * 100)),
            "expected_savings": int(round((list_price - offer) * p)),
        })
    return ladder


# --------------------------------------------------------------------------
# Stage 7: AI judgment layer (optional -- requires `pip install anthropic`)
# --------------------------------------------------------------------------

AI_DEFAULT_MODEL = "claude-opus-5"


def _schema_obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": required, "additionalProperties": False}


_AI_PHOTO_SCHEMA = _schema_obj({
    "photos": {"type": "array", "items": _schema_obj({
        "file": {"type": "string",
                 "description": "photo file name exactly as given"},
        "view": {"type": "string",
                 "description": "room or exterior view shown"},
        "notes": {"type": "string",
                  "description": "detailed condition observations"},
        "defects": {"type": "array", "items": {"type": "string"}},
    }, ["file", "view", "notes", "defects"])},
}, ["photos"])

_AI_SYNTHESIS_SCHEMA = _schema_obj({
    "condition_grade": {"type": "string",
                        "enum": ["excellent", "good", "fair", "poor", "mixed"]},
    "interior_summary": {"type": "string"},
    "exterior_summary": {"type": "string"},
    "work_items": {"type": "array", "items": _schema_obj({
        "item": {"type": "string"},
        "urgency": {"type": "string", "enum": ["now", "1-2 years", "monitor"]},
        "est_cost_range": {"type": "string"},
    }, ["item", "urgency", "est_cost_range"])},
    "visual_red_flags": {"type": "array", "items": {"type": "string"}},
    "checklist_answers": {"type": "array", "items": _schema_obj({
        "item": {"type": "string",
                 "description": "checklist item text, verbatim"},
        "answer": {"type": "string"},
    }, ["item", "answer"])},
    "offer_ladder": {"type": "array", "items": _schema_obj({
        "offer_price": {"type": "integer"},
        "pct_of_list": {"type": "number"},
        "success_probability_pct": {"type": "integer"},
        "rationale": {"type": "string"},
    }, ["offer_price", "pct_of_list", "success_probability_pct", "rationale"])},
    "negotiation_points": {"type": "array", "items": {"type": "string"}},
    "bottom_line": {"type": "string"},
}, ["condition_grade", "interior_summary", "exterior_summary", "work_items",
    "visual_red_flags", "checklist_answers", "offer_ladder",
    "negotiation_points", "bottom_line"])

_AI_PHOTO_PROMPT = (
    "You are a home-inspection-minded real-estate analyst reviewing listing "
    "photos for a buyer. For EACH photo above (identified by the file name "
    "immediately preceding it), report: the room or exterior view shown; "
    "detailed condition observations -- renovation era and quality "
    "(flip-grade vs owner-grade), mechanicals (water heater brand/fuel/"
    "apparent age, expansion tank, furnace era, electrical panel), flooring, "
    "windows, roof condition, siding, gutters/downspout routing and any "
    "loose drainage pipes, driveway cracking, decks/patios (verify claimed "
    "materials), vegetation touching or overhanging the structure, lot "
    "slope, and floor-plan layout if the image is a floor plan; and any "
    "defects, staining, patches, or things a marketing photo may be "
    "framing around. Be specific and skeptical."
)

_AI_SYNTH_SYSTEM = (
    "You are a residential real-estate due-diligence analyst producing an "
    "honest, evidence-grounded assessment for a buyer. Ground every claim "
    "in the dossier or photo findings; never invent facts. Success "
    "probabilities are calibrated estimates, not guarantees: anchor on the "
    "heuristic offer ladder provided, then adjust using days on market, "
    "price cuts, failed pendings, seller cost basis, county valuation, "
    "condition findings, and any comp context in the dossier -- and state "
    "the specific evidence in each rationale. For checklist_answers, "
    "answer every checklist item using its exact text as `item`."
)


def _anthropic_client() -> tuple[Any, Any]:
    """Import the official Anthropic SDK and build a zero-arg client.

    The client resolves credentials from ANTHROPIC_API_KEY,
    ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile.
    """
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "--ai requires the official Anthropic SDK. "
            "Install it with: pip install anthropic") from exc
    try:
        client = anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001 -- no credentials resolvable
        raise RuntimeError(
            "Could not initialize the Anthropic client -- set "
            f"ANTHROPIC_API_KEY or run `ant auth login`. ({exc})") from exc
    return anthropic, client


def _structured_json(response: Any) -> dict[str, Any]:
    """Extract the guaranteed-JSON text block from a structured response."""
    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("model declined the request (stop_reason=refusal)")
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise RuntimeError(
            f"no text block in response (stop_reason="
            f"{getattr(response, 'stop_reason', None)})")
    return json.loads(text)


def _image_block(path: Path, max_bytes: int = 4_500_000) -> Optional[dict[str, Any]]:
    data = path.read_bytes()
    if len(data) > max_bytes:
        return None
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image",
            "source": {"type": "base64", "media_type": media,
                       "data": base64.standard_b64encode(data).decode("ascii")}}


def ai_review_photos(anthropic_mod: Any, client: Any, model: str,
                     photos: list[Path], batch_size: int,
                     gaps: list[str]) -> list[dict[str, Any]]:
    """Vision pass: batches of photos -> structured per-photo findings."""
    findings: list[dict[str, Any]] = []
    for start in range(0, len(photos), batch_size):
        batch = photos[start:start + batch_size]
        content: list[dict[str, Any]] = []
        for p in batch:
            block = _image_block(p)
            if block is None:
                gaps.append(f"AI photos: {p.name} exceeds size cap, skipped")
                continue
            content.append({"type": "text", "text": f"Photo file: {p.name}"})
            content.append(block)
        if not content:
            continue
        content.append({"type": "text", "text": _AI_PHOTO_PROMPT})
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8000,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema",
                                          "schema": _AI_PHOTO_SCHEMA}},
            )
            findings.extend(_structured_json(response).get("photos") or [])
            log(f"AI photos: {min(start + batch_size, len(photos))}"
                f"/{len(photos)} reviewed")
        except anthropic_mod.AuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"AI photo batch {start // batch_size + 1}: {exc}")
    return findings


def _build_dossier(address: str, data: dict[str, Any]) -> dict[str, Any]:
    """Slim, JSON-safe subset of the run data for the synthesis prompt."""
    z = data.get("zillow") or {}
    county = data.get("county") or {}
    analysis = data.get("analysis") or {}
    ztext = z.get("text") or ""
    return {
        "address": address,
        "listing": {k: z.get(k) for k in (
            "price", "beds", "baths", "sqft", "year_built", "lot_acres",
            "price_per_sqft", "mls", "date_on_market", "tax_assessed",
            "annual_tax", "description")},
        "county": {k: county.get(k) for k in (
            "owner", "mailing", "district_name", "taxing_district",
            "total_acres", "dwelling", "current_value", "tentative_value",
            "transfers", "permits", "topography", "value_history",
            "effective_tax_rate", "gross_tax_rate")},
        "analysis": {k: analysis.get(k) for k in (
            "timeline", "cumulative_market_days", "relist_count",
            "price_cuts", "failed_pendings", "flip_flag",
            "assessment_jump_years", "unpermitted_reno_flag",
            "contradictions", "steep_lot", "tax_projection", "red_flags",
            "absentee_owner", "motivation", "offer_ladder",
            "ladder_anchoring", "comp_value_estimate", "value_gap_pct")},
        "zip_market_stats": data.get("market") or {},
        "sold_comps": {
            **{k: (data.get("comps") or {}).get(k)
               for k in ("source", "n", "median_ppsf")},
            "rows": ((data.get("comps") or {}).get("rows") or [])[:15],
        },
        "schools": extract_schools(ztext),
        "flood_mentions": extract_flood_lines(ztext),
        "nearby_home_values": extract_nearby_values(ztext),
        "visual_checklist": list(VISUAL_CHECKLIST),
    }


def ai_synthesize(client: Any, model: str, dossier: dict[str, Any],
                  photo_findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Synthesis pass: dossier + photo findings -> judgment + offer ladder."""
    prompt = (
        "Below is the full due-diligence dossier for a house a buyer is "
        "considering, followed by per-photo condition findings from a "
        "vision review of the listing photos.\n\n"
        "## DOSSIER (parsed listing, county record, timeline analysis, "
        "heuristic offer ladder)\n\n"
        f"{json.dumps(dossier, indent=1, default=str)}\n\n"
        "## PHOTO FINDINGS\n\n"
        f"{json.dumps(photo_findings, indent=1, default=str)}\n\n"
        "Produce the buyer-side judgment: condition grade, interior and "
        "exterior summaries, concrete work items with urgency and cost "
        "ranges, visual red flags, an answer for every visual-checklist "
        "item, a refined offer ladder (3-5 rungs with success probability "
        "and evidence-based rationale each), negotiation points, and a "
        "bottom-line verdict."
    )
    with client.messages.stream(
        model=model,
        max_tokens=32000,
        system=_AI_SYNTH_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema",
                                  "schema": _AI_SYNTHESIS_SCHEMA}},
    ) as stream:
        response = stream.get_final_message()
    return _structured_json(response)


def stage_ai_judgment(address: str, data: dict[str, Any], out_dir: Path,
                      gaps: list[str], model: str, batch_size: int,
                      max_photos: int) -> dict[str, Any]:
    """Run the optional Claude judgment layer over a completed run."""
    anthropic_mod, client = _anthropic_client()
    photo_dir = out_dir / "photos"
    photos = sorted(photo_dir.glob("*.jpg"))[:max_photos] \
        if photo_dir.exists() else []
    log(f"Stage 7: AI judgment ({model}; {len(photos)} photos)")
    try:
        photo_findings = ai_review_photos(
            anthropic_mod, client, model, photos, batch_size, gaps) \
            if photos else []
        dossier = _build_dossier(address, data)
        synthesis = ai_synthesize(client, model, dossier, photo_findings)
    except anthropic_mod.AuthenticationError as exc:
        raise RuntimeError(
            "Anthropic authentication failed. Set ANTHROPIC_API_KEY or run "
            "`ant auth login`, then retry with --resume --ai.") from exc
    result = {"model": model, "photo_findings": photo_findings}
    result.update(synthesis)
    return result


# --------------------------------------------------------------------------
# Stage 6 -- Report
# --------------------------------------------------------------------------


def extract_schools(text: str) -> list[str]:
    """Lines around 'N/10' GreatSchools ratings in the Zillow innerText."""
    lines = text.splitlines()
    found: list[str] = []
    for i, ln in enumerate(lines):
        if re.search(r"\b\d{1,2}\s*/\s*10\b", ln):
            ctx = " ".join(x.strip() for x in lines[max(0, i - 1):i + 2] if x.strip())
            if ctx not in found:
                found.append(ctx)
    return found[:8]


def extract_flood_lines(text: str) -> list[str]:
    """Distinct lines mentioning flood risk."""
    out: list[str] = []
    for ln in text.splitlines():
        if "flood" in ln.lower() and ln.strip() and ln.strip() not in out:
            out.append(ln.strip())
    return out[:6]


def extract_nearby_values(text: str) -> list[str]:
    """Dollar values in the 'Nearby homes' section."""
    idx = text.find("Nearby homes")
    if idx < 0:
        return []
    block = text[idx:idx + 2500]
    return [f"${m}" for m in re.findall(r"\$([\d,]{6,})", block)][:12]


def _local_utilities(district: Optional[str]) -> Optional[list[str]]:
    """Utility-provider table for a taxing district, from an optional,
    deliberately untracked local_utilities.json next to this script:
    {"DISTRICT SUBSTRING": [["Service", "Provider"], ...], ...}"""
    cfg_path = Path(__file__).with_name("local_utilities.json")
    if not district or not cfg_path.exists():
        return None
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    for key, rows in cfg.items():
        if key.upper() in district.upper():
            return (["| Service | Provider |", "|---|---|"] +
                    [f"| {svc} | {prov} |" for svc, prov in rows])
    return None


def _generic_utilities() -> list[str]:
    return [
        "- [ ] Water/sewer: check the municipality or township website.",
        "- [ ] Trash: municipal contract vs private hauler.",
        "- [ ] Electric/gas: confirm territory with the PUC utility-territory map.",
        "- [ ] Internet: check the FCC National Broadband Map "
        "(broadbandmap.fcc.gov) for this address.",
        "- [ ] Septic/well: if outside a service district, ask for inspection "
        "records from the county health department.",
    ]


def build_report(address: str, data: dict[str, Any], gaps: list[str],
                 out_dir: Path) -> str:
    """Assemble the full Markdown report."""
    z = data.get("zillow") or {}
    county = data.get("county") or {}
    analysis = data.get("analysis") or {}
    ai = data.get("ai") or {}
    timeline = analysis.get("timeline") or []
    lines: list[str] = [f"# Due Diligence Report: {address}", ""]
    mode = (f"AI judgment layer: {ai.get('model')}" if ai
            else "offline mode (no AI)")
    lines += [f"*Generated {TODAY.isoformat()} by house_recon.py -- {mode}*", ""]

    # Snapshot
    lines += ["## Snapshot", "", "| Field | Value |", "|---|---|"]

    def money(v: Optional[int]) -> str:
        return f"${v:,}" if isinstance(v, int) else "n/a"

    snapshot = [
        ("Asking price", money(z.get("price"))),
        ("Beds / Baths", f"{z.get('beds') or 'n/a'} / {z.get('baths') or 'n/a'}"),
        ("Square feet", z.get("sqft") or "n/a"),
        ("Year built", z.get("year_built") or
         (county.get("dwelling") or {}).get("Year Built") or "n/a"),
        ("Lot", f"{z.get('lot_acres')} acres" if z.get("lot_acres") else
         (county.get("total_acres") or "n/a")),
        ("$ / sqft", f"${z.get('price_per_sqft')}" if z.get("price_per_sqft")
         else "n/a"),
        ("MLS #", z.get("mls") or "n/a"),
        ("Parcel", z.get("parcel") or county.get("parcel_id") or "n/a"),
        ("Date on market", z.get("date_on_market") or "n/a"),
        ("Tax assessed value", money(z.get("tax_assessed"))),
        ("Annual tax (listed)", money(z.get("annual_tax"))),
        ("County owner", county.get("owner") or "n/a"),
    ]
    lines += [f"| {k} | {v} |" for k, v in snapshot]
    lines.append("")

    # Timeline
    lines += ["## Dated History Timeline", "",
              "| Date | Event | Price | Source |", "|---|---|---|---|"]
    for ev in timeline:
        price = money(ev.get("price")) if ev.get("price") else ""
        lines.append(f"| {ev.get('date')} | {ev.get('event')} | {price} | "
                     f"{ev.get('source') or ''} |")
    if not timeline:
        lines.append("| n/a | no events captured | | |")
    lines.append("")
    cuts = analysis.get("price_cuts") or {}
    lines += ["**Timeline analysis:**", ""]
    lines.append(f"- Cumulative days on market: "
                 f"{analysis.get('cumulative_market_days', 'n/a')}")
    lines.append(f"- Re-lists after first listing: "
                 f"{analysis.get('relist_count', 'n/a')}")
    if cuts.get("cuts"):
        detail = "; ".join(f"{c['date']}: {money(c['from'])} -> {money(c['to'])}"
                           for c in cuts["cuts"])
        lines.append(f"- Price cuts: {detail} (total {money(cuts['total_cut'])}, "
                     f"{cuts['pct_from_peak']}% off peak ask {money(cuts['peak'])})")
    else:
        lines.append("- Price cuts: none detected")
    lines.append(f"- Failed pendings: {analysis.get('failed_pendings', 'n/a')}")
    lines.append(f"- Flip indicator: {analysis.get('flip_flag') or 'none'}")
    transfers = county.get("transfers") or []
    if transfers:
        last = transfers[-1]
        lines.append(f"- Current owner cost basis (last county transfer): "
                     f"{money(last.get('amount'))} on {last.get('date')}")
    lines.append("")

    # County & tax
    lines += ["## County & Tax", ""]
    dwelling = county.get("dwelling") or {}
    if any(dwelling.values()):
        lines += ["**Dwelling card (county):**", "", "| Field | Value |", "|---|---|"]
        lines += [f"| {k} | {v or 'n/a'} |" for k, v in dwelling.items()]
        lines.append("")
    cur, tent = county.get("current_value") or {}, county.get("tentative_value") or {}
    if cur or tent:
        lines += ["| Value | Current | 2026 Tentative | Change |",
                  "|---|---|---|---|"]
        for key in ("land", "building", "total"):
            c, t = cur.get(key), tent.get(key)
            chg = (f"{(t - c) / c * 100:+.1f}%" if c and t else "n/a")
            lines.append(f"| {key.title()} (100%) | {money(c)} | {money(t)} | {chg} |")
        ca, ta = cur.get("total_assessed"), tent.get("total_assessed")
        if ca or ta:
            chg = f"{(ta - ca) / ca * 100:+.1f}%" if ca and ta else "n/a"
            lines.append(f"| Total assessed (35%) | {money(ca)} | {money(ta)} | {chg} |")
        lines.append("")
    proj = analysis.get("tax_projection")
    if proj:
        lines.append(f"**Projected tax:** {proj['math']}")
        if proj.get("current"):
            lines.append(f"Current annual tax {money(proj['current'])} -> "
                         f"projected {money(proj['net'])} "
                         f"(delta {money(proj['delta'])}).")
        lines.append("")
    district = county.get("district_name") or county.get("taxing_district") or "n/a"
    lines.append(f"- Taxing district: {district}")
    if absentee_owner_flag(address, county):
        lines.append("- Owner mailing address differs from property address: "
                     "likely absentee owner / rental history.")
    if county.get("permits"):
        lines.append(f"- Permits: {county['permits']}")
    for dl in county.get("tax_distribution") or []:
        lines.append(f"- Tax distribution: {dl}")
    lines.append("")

    # Red flags
    lines += ["## Red Flags", ""]
    flags = analysis.get("red_flags") or []
    lines += [f"- {f}" for f in flags] if flags else ["- None detected automatically."]
    lines.append("")

    # Offer strategy
    lines += ["## Offer Strategy", ""]
    motivation = analysis.get("motivation") or {}
    ladder = analysis.get("offer_ladder") or []
    market = data.get("market") or {}
    comps = data.get("comps") or {}
    estimate = analysis.get("comp_value_estimate")
    if market:
        bits = []
        if market.get("label"):
            bits.append(market["label"])
        if market.get("sale_to_list_pct"):
            bits.append(f"median sale-to-list {market['sale_to_list_pct']}%")
        if market.get("median_dom"):
            bits.append(f"median {market['median_dom']} days on market")
        if market.get("median_sale_price"):
            bits.append(f"median sale {money(market['median_sale_price'])}")
        lines.append(f"**Zip market temperature:** {'; '.join(bits)} "
                     f"([source]({market.get('source', '')}))")
    if estimate:
        gap = analysis.get("value_gap_pct")
        gap_txt = (f"; ask is {gap:+.1f}% vs this estimate"
                   if gap is not None else "")
        lines.append(f"**Sold-comps value estimate:** "
                     f"{money(estimate['value'])} "
                     f"(${estimate['ppsf']}/sqft median of "
                     f"{estimate['n_similar'] if estimate['basis'] == 'similar' else estimate['n_all']} "
                     f"{'similar-size ' if estimate['basis'] == 'similar' else ''}"
                     f"sold comps, {comps.get('source', 'n/a')}){gap_txt}")
    if market or estimate:
        lines.append("")
    if ladder:
        anchoring = analysis.get("ladder_anchoring") or {}
        lines.append(f"**Seller-motivation score:** "
                     f"{motivation.get('score', 'n/a')}/100")
        lines += [f"- {r}" for r in motivation.get("reasons") or []]
        if anchoring:
            lines += ["", f"**Ladder anchoring ({anchoring.get('mode')}):**"]
            lines += [f"- {n}" for n in anchoring.get("notes") or []]
        lines += ["", "| Offer | % of list | Est. acceptance odds | "
                  "Expected savings |", "|---|---|---|---|"]
        for rung in ladder:
            lines.append(f"| {money(rung['offer'])} | {rung['pct_of_list']}% "
                         f"| ~{rung['acceptance_probability_pct']}% | "
                         f"{money(rung['expected_savings'])} |")
        caveat = ("*Anchored to observed zip-level stats and sold comps "
                  "where shown above; still calibrated estimates, not a "
                  "fitted statistical model. Refine with your agent.*"
                  if anchoring.get("mode") != "heuristic" else
                  "*Heuristic estimates derived only from listing-history "
                  "signals (days on market, cuts, failed pendings, relists, "
                  "owner profile, county value gap) -- not a statistical "
                  "model. Refine with sold comps and your agent.*")
        lines += ["", caveat, ""]
    else:
        lines += ["*(no list price captured -- offer ladder unavailable)*", ""]
    if ai.get("offer_ladder"):
        lines += [f"### AI-refined offer ladder ({ai.get('model', 'AI')})", "",
                  "| Offer | % of list | Est. success | Rationale |",
                  "|---|---|---|---|"]
        for rung in ai["offer_ladder"]:
            lines.append(f"| {money(rung.get('offer_price'))} | "
                         f"{rung.get('pct_of_list')}% | "
                         f"~{rung.get('success_probability_pct')}% | "
                         f"{rung.get('rationale', '')} |")
        lines.append("")
    if ai.get("negotiation_points"):
        lines += ["**Negotiation points (AI):**"]
        lines += [f"- {p}" for p in ai["negotiation_points"]]
        lines.append("")

    # Utilities
    lines += ["## Utilities", ""]
    lines += _local_utilities(district) or _generic_utilities()
    lines.append("")

    # Condition review
    lines += ["## Condition Review", ""]
    photo_files = sorted((out_dir / "photos").glob("*.jpg")) \
        if (out_dir / "photos").exists() else []
    if photo_files:
        lines.append("**Photo index:**")
        lines += [f"- [photo {p.stem}](photos/{p.name})" for p in photo_files]
    else:
        lines.append("*(no photos downloaded)*")
    if ai:
        lines += ["", f"### AI Condition Judgment ({ai.get('model', 'AI')})", ""]
        lines.append(f"**Grade:** {ai.get('condition_grade', 'n/a')}")
        if ai.get("interior_summary"):
            lines += ["", f"**Interior:** {ai['interior_summary']}"]
        if ai.get("exterior_summary"):
            lines += ["", f"**Exterior:** {ai['exterior_summary']}"]
        work = ai.get("work_items") or []
        if work:
            lines += ["", "| Work item | Urgency | Est. cost |", "|---|---|---|"]
            lines += [f"| {w.get('item')} | {w.get('urgency')} | "
                      f"{w.get('est_cost_range')} |" for w in work]
        visual_flags = ai.get("visual_red_flags") or []
        if visual_flags:
            lines += ["", "**Visual red flags:**"]
            lines += [f"- {f}" for f in visual_flags]
        photo_notes = ai.get("photo_findings") or []
        if photo_notes:
            lines += ["", "<details><summary>Per-photo notes "
                      f"({len(photo_notes)} photos)</summary>", ""]
            for note in photo_notes:
                defects = note.get("defects") or []
                suffix = ("; DEFECTS: " + "; ".join(defects)) if defects else ""
                lines.append(f"- **{note.get('file')}** ({note.get('view')}): "
                             f"{note.get('notes')}{suffix}")
            lines += ["", "</details>"]
    lines += ["", "## HUMAN/LLM VISUAL REVIEW CHECKLIST", ""]
    answers = {a.get("item"): a.get("answer")
               for a in (ai.get("checklist_answers") or []) if a.get("item")}
    for item in VISUAL_CHECKLIST:
        answer = answers.get(item)
        if answer:
            lines.append(f"- [x] {item} -- {answer}")
        else:
            lines.append(f"- [ ] {item}")
    lines.append("")

    # Maps
    lines += ["## Maps", ""]
    maps_files = sorted((out_dir / "maps").glob("*.png")) \
        if (out_dir / "maps").exists() else []
    if maps_files:
        lines += [f"- [{p.stem}](maps/{p.name})" for p in maps_files]
    else:
        lines.append("*(no map captures)*")
    coords = (data.get("maps") or {}).get("coords")
    if coords:
        lines.append(f"- Coordinates: {coords[0]}, {coords[1]}")
    lines.append("")

    # Bottom line (AI)
    if ai.get("bottom_line"):
        lines += ["## Bottom Line (AI)", "", ai["bottom_line"], ""]

    # Data appendix
    lines += ["## Data Appendix", ""]
    ztext = z.get("text") or ""
    schools = extract_schools(ztext)
    if schools:
        lines += ["**Schools:**"] + [f"- {s}" for s in schools] + [""]
    floods = extract_flood_lines(ztext)
    if floods:
        lines += ["**Flood mentions:**"] + [f"- {f}" for f in floods] + [""]
    nearby = extract_nearby_values(ztext)
    if nearby:
        lines += ["**Nearby home values:** " + ", ".join(nearby), ""]
    tax_rows = z.get("tax_history") or []
    if tax_rows:
        lines += ["**Listing tax history:**", "",
                  "| Year | Taxes | Change | Assessment |", "|---|---|---|---|"]
        lines += [f"| {r['year']} | {money(r['taxes'])} | "
                  f"{r['change_pct'] if r['change_pct'] is not None else ''}% | "
                  f"{money(r['assessment'])} |" for r in tax_rows]
        lines.append("")
    vh = county.get("value_history") or []
    if vh:
        lines += ["**County value history:**", "",
                  "| Year | Land | Building | Total |", "|---|---|---|---|"]
        lines += [f"| {r['year']} | {money(r['land'])} | {money(r['building'])} "
                  f"| {money(r['total'])} |" for r in vh]
        lines.append("")
    comp_rows = (data.get("comps") or {}).get("rows") or []
    if comp_rows:
        lines += [f"**Sold comps ({(data.get('comps') or {}).get('source')}, "
                  f"{len(comp_rows)} shown):**", "",
                  "| Price | Beds | Sqft | $/sqft | Address |",
                  "|---|---|---|---|---|"]
        for r in comp_rows[:15]:
            sqft_txt = f"{r['sqft']:,}" if r.get("sqft") else "n/a"
            ppsf_txt = f"${r['ppsf']}" if r.get("ppsf") else "n/a"
            lines.append(f"| {money(r['price'])} | {r.get('beds', '')} | "
                         f"{sqft_txt} | {ppsf_txt} | {r.get('address', '')} |")
        lines.append("")
    raw_dir = out_dir / "raw"
    inventory = sorted(p.name for p in raw_dir.glob("*")) if raw_dir.exists() else []
    lines += ["**Raw file inventory:** " + (", ".join(inventory) or "none"), ""]

    # Gaps + disclaimer
    lines += ["## Data Gaps", ""]
    lines += [f"- {g}" for g in gaps] if gaps else ["- None."]
    lines += ["", "---",
              "*Automated report from public web sources; data may be stale, "
              "incomplete, or mis-parsed. Verify everything independently before "
              "any purchase decision. Not professional advice.*", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="house_recon.py",
        description="Automated residential real-estate due diligence: Zillow, "
                    "Trulia, county auditor, and Google Maps into one Markdown "
                    "report.",
        epilog="NOTE: the browser runs HEADED by default on purpose -- the "
               "target sites tolerate a visible browser far better than "
               "headless. Pass --headless to override.",
    )
    parser.add_argument("address", help='Street address, e.g. '
                        '"123 Main St, Columbus, OH 43215"')
    parser.add_argument("--out", default="./reports", help="Output root dir "
                        "(default ./reports)")
    parser.add_argument("--county", choices=["butler-oh", "none"], default=None,
                        help="County adapter (default: auto-detect butler-oh "
                        "from Butler Co OH ZIPs/cities, else none)")
    parser.add_argument("--headless", action="store_true",
                        help="Run headless (default is headed for bot tolerance)")
    parser.add_argument("--skip-photos", action="store_true")
    parser.add_argument("--skip-maps", action="store_true")
    parser.add_argument("--skip-county", action="store_true")
    parser.add_argument("--skip-trulia", action="store_true")
    parser.add_argument("--zillow-url", default=None,
                        help="Explicit Zillow /homedetails/ URL override")
    parser.add_argument("--resume", action="store_true",
                        help="Skip scraping; reload raw/parsed.json and "
                             "photos from this address's previous run in "
                             "--out (e.g. to add --ai after the fact)")
    parser.add_argument("--skip-market", action="store_true",
                        help="Skip the zip-level market-temperature and "
                             "sold-comps stages")
    parser.add_argument("--refresh-market", action="store_true",
                        help="With --resume: re-scrape only the zip market "
                             "stats and sold comps (light browser session), "
                             "keeping everything else from the prior run")
    ai = parser.add_argument_group(
        "AI judgment layer (optional; offline without it)")
    ai.add_argument("--ai", action="store_true",
                    help="Enable the Claude-powered judgment layer: "
                         "per-photo condition review, work items with cost "
                         "ranges, answered visual checklist, AI-refined "
                         "offer ladder, and a bottom-line verdict. Requires "
                         "`pip install anthropic` and API credentials "
                         "(ANTHROPIC_API_KEY or `ant auth login`).")
    ai.add_argument("--ai-model", default=AI_DEFAULT_MODEL,
                    help=f"Claude model id (default {AI_DEFAULT_MODEL})")
    ai.add_argument("--ai-photos", type=int, default=30,
                    help="Max photos sent for AI review (default 30)")
    ai.add_argument("--ai-batch", type=int, default=10,
                    help="Photos per vision request (default 10)")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    address: str = args.address.strip()
    county_mode = args.county or detect_county(address)
    out_dir = Path(args.out) / slugify(address)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    log(f"Address: {address}")
    log(f"County adapter: {county_mode}  Output: {out_dir}")

    data: dict[str, Any] = {}
    gaps: list[str] = []
    parsed_path = raw_dir / "parsed.json"
    if args.resume:
        if not parsed_path.exists():
            print(f"[house-recon] --resume: {parsed_path} not found; "
                  "run once without --resume first.")
            return 2
        data = json.loads(parsed_path.read_text(encoding="utf-8"))
        _strip_underscore_dates(data)
        ztxt = raw_dir / "zillow.txt"
        if ztxt.exists() and isinstance(data.get("zillow"), dict):
            data["zillow"]["text"] = ztxt.read_text(encoding="utf-8",
                                                    errors="replace")
        log(f"Resume: reloaded prior run from {parsed_path} "
            "(scraping skipped)")
        if args.refresh_market:
            zip_code = zip_from_address(address)
            if not zip_code:
                gaps.append("Market/comps refresh: no ZIP in address")
            else:
                try:
                    with sync_playwright() as pw:
                        browser = launch_browser(pw, headless=args.headless)
                        context = browser.new_context(
                            viewport={"width": 1440, "height": 950},
                            locale="en-US")
                        page = context.new_page()
                        data["market"] = safe_stage(
                            "Market temp", gaps, stage_market_temp,
                            page, zip_code, raw_dir, gaps) or {}
                        data["comps"] = safe_stage(
                            "Sold comps", gaps, stage_sold_comps,
                            page, zip_code, raw_dir, gaps) or {}
                        context.close()
                        browser.close()
                except Exception as exc:  # noqa: BLE001
                    gaps.append(f"Market/comps refresh: {exc}")
    else:
        try:
            with sync_playwright() as pw:
                browser = launch_browser(pw, headless=args.headless)
                context = browser.new_context(
                    viewport={"width": 1440, "height": 950}, locale="en-US")
                page = context.new_page()

                # Stage 1: Zillow
                zurl = args.zillow_url or build_zillow_url(address)
                data["zillow"] = safe_stage(
                    "Zillow", gaps, stage_zillow,
                    page, address, zurl, raw_dir, gaps) or {}
                if not args.skip_photos:
                    hashes = extract_photo_hashes(
                        data["zillow"].get("next_data"))
                    log(f"Photos: {len(hashes)} found in __NEXT_DATA__")
                    if hashes:
                        n = safe_stage("Photos", gaps, download_photos,
                                       hashes, out_dir / "photos") or 0
                        log(f"Photos downloaded: {n}")
                    else:
                        gaps.append(
                            "Photos: none found in Zillow __NEXT_DATA__")

                # Stage 2: Trulia
                if not args.skip_trulia:
                    data["trulia"] = safe_stage(
                        "Trulia", gaps, stage_trulia,
                        page, address, raw_dir, gaps) or {}
                else:
                    data["trulia"] = {}
                    log("Stage 2: Trulia skipped")

                # Stage 3: County
                if not args.skip_county and county_mode == "butler-oh":
                    data["county"] = safe_stage(
                        "County (butler-oh)", gaps, stage_county_butler_oh,
                        page, address, raw_dir, gaps) or {}
                else:
                    data["county"] = {}
                    log("Stage 3: County skipped "
                        f"(mode={county_mode}, skip={args.skip_county})")

                # Stage 4: Maps
                if not args.skip_maps:
                    data["maps"] = safe_stage("Maps", gaps, stage_maps,
                                              page, address, out_dir / "maps",
                                              gaps) or {}
                else:
                    data["maps"] = {}
                    log("Stage 4: Maps skipped")

                # Stage 4b: Market temperature + sold comps
                zip_code = zip_from_address(address)
                if args.skip_market:
                    log("Stage 4b: market/comps skipped")
                elif not zip_code:
                    gaps.append("Market/comps: no ZIP in address")
                else:
                    data["market"] = safe_stage(
                        "Market temp", gaps, stage_market_temp,
                        page, zip_code, raw_dir, gaps) or {}
                    data["comps"] = safe_stage(
                        "Sold comps", gaps, stage_sold_comps,
                        page, zip_code, raw_dir, gaps) or {}

                context.close()
                browser.close()
        except Exception as exc:  # noqa: BLE001
            log(f"Browser pipeline failed: {exc}")
            gaps.append(f"Browser pipeline: {exc}")
            if not data:
                print(f"[house-recon] TOTAL FAILURE: {exc}")
                return 2

    # Stage 5: Analysis
    log("Stage 5: Analysis")
    analysis: dict[str, Any] = {}
    z = data.get("zillow") or {}
    county = data.get("county") or {}

    def _analyze() -> dict[str, Any]:
        timeline = merge_timeline(z.get("price_history") or [],
                                  (data.get("trulia") or {}).get("events") or [],
                                  county.get("transfers") or [])
        a: dict[str, Any] = {"timeline": timeline}
        a["cumulative_market_days"] = cumulative_market_days(timeline)
        a["relist_count"] = count_relists(timeline)
        a["price_cuts"] = analyze_price_cuts(timeline)
        a["failed_pendings"] = count_failed_pendings(timeline)
        a["flip_flag"] = detect_flip(timeline)
        a["assessment_jump_years"] = assessment_jump_years(
            county.get("value_history") or [])
        a["unpermitted_reno_flag"] = bool(
            a["assessment_jump_years"] and county.get("permits") == "none on file")
        a["contradictions"] = contradiction_checks(z, county)
        a["steep_lot"] = steep_lot_flag(county)
        a["tax_projection"] = project_taxes(county, z.get("annual_tax"))
        a["red_flags"] = build_red_flags(a, z, county)
        a["absentee_owner"] = absentee_owner_flag(address, county)
        a["motivation"] = seller_motivation(a, z, county, a["absentee_owner"])
        market = data.get("market") or {}
        comps = data.get("comps") or {}
        subject_sqft = parse_money(str(z.get("sqft"))) if z.get("sqft") else None
        try:
            subject_beds = float(z.get("beds")) if z.get("beds") else None
        except (TypeError, ValueError):
            subject_beds = None
        estimate = comps_value_estimate(comps, subject_sqft, subject_beds)
        a["comp_value_estimate"] = estimate
        price = z.get("price")
        a["value_gap_pct"] = (
            round((price - estimate["value"]) / estimate["value"] * 100, 1)
            if price and estimate else None)
        a["ladder_anchoring"] = ladder_anchoring(
            a["motivation"], market, a["value_gap_pct"])
        a["offer_ladder"] = estimate_offer_ladder(
            price, a["motivation"], a["ladder_anchoring"])
        return a

    analysis = safe_stage("Analysis", gaps, _analyze) or {}
    data["analysis"] = analysis
    log(f"Analysis: {len(analysis.get('timeline') or [])} timeline events, "
        f"{len(analysis.get('red_flags') or [])} red flags")

    # Stage 7: AI judgment layer (optional)
    if args.ai:
        data["ai"] = safe_stage(
            "AI judgment", gaps, stage_ai_judgment,
            address, data, out_dir, gaps, args.ai_model,
            args.ai_batch, args.ai_photos) or {}

    # Persist parsed data (drop bulky raw text -- it lives in raw/*.txt)
    slim = json.loads(json.dumps(
        {k: v for k, v in data.items()}, default=str))
    for key in ("text", "next_data"):
        slim.get("zillow", {}).pop(key, None)
    try:
        (raw_dir / "parsed.json").write_text(
            json.dumps(slim, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"parsed.json: {exc}")

    # Stage 6: Report
    log("Stage 6: Report")
    try:
        report = build_report(address, data, gaps, out_dir)
        report_path = out_dir / "report.md"
        report_path.write_text(report, encoding="utf-8", errors="replace")
        log(f"Report written: {report_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"[house-recon] TOTAL FAILURE writing report: {exc}")
        return 2
    if gaps:
        log(f"Completed with {len(gaps)} data gap(s); see report Data Gaps.")
    else:
        log("Completed with no data gaps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
