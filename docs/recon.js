/* house-recon browser engine — a faithful JavaScript port of the
 * deterministic pipeline in house_recon.py (parsers + analysis + offer
 * engine). Everything here runs locally in the page; no network calls.
 * house_recon.py is the reference implementation — keep ports in sync. */

"use strict";

const ZILLOW_EVENTS = [
  "Listed for sale", "Listing removed", "Pending sale", "Back on market",
  "Price change", "Sold", "Contingent", "Listed for rent",
];
const LISTING_OPENERS = ["Listed for sale", "Back on market"];
const LISTING_CLOSERS = ["Listing removed", "Pending sale", "Sold"];
const MONTHS = { JAN: 1, FEB: 2, MAR: 3, APR: 4, MAY: 5, JUN: 6,
                 JUL: 7, AUG: 8, SEP: 9, OCT: 10, NOV: 11, DEC: 12 };

// ---------- small utilities ----------

function slugify(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function parseMoney(raw) {
  if (!raw) return null;
  const digits = String(raw).replace(/[^\d]/g, "");
  return digits ? parseInt(digits, 10) : null;
}

function parseDateAny(raw) {
  if (!raw) return null;
  raw = String(raw).trim();
  let m = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (m) return Date.UTC(+m[3], +m[1] - 1, +m[2], 12);
  m = raw.match(/^(\d{1,2})-([A-Za-z]{3})-(\d{4})$/);
  if (m) {
    const mo = MONTHS[m[2].toUpperCase()];
    if (mo) return Date.UTC(+m[3], mo - 1, +m[1], 12);
  }
  return null;
}

function daysBetween(a, b) { return Math.round((b - a) / 86400000); }

function todayUTC() {
  const now = new Date();
  return Date.UTC(now.getFullYear(), now.getMonth(), now.getDate(), 12);
}

function isoDate(ts) { return new Date(ts).toISOString().slice(0, 10); }

function median(values) {
  if (!values.length) return null;
  const v = [...values].sort((a, b) => a - b);
  const mid = Math.floor(v.length / 2);
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
}

function fmtMoney(v) {
  return Number.isFinite(v) ? "$" + Math.round(v).toLocaleString("en-US") : "n/a";
}

// ---------- parsers (ports of parse_* in house_recon.py) ----------

function parseZillowText(text) {
  const out = {};
  const top = text.slice(0, 5000);
  const rx = (pattern, src) => {
    const m = (src === undefined ? text : src).match(pattern);
    return m ? m[1] : null;
  };
  out.price = parseMoney(rx(/\$([\d,]+)/, top));
  out.beds = rx(/([\d.]+)\s*beds?\b/i, top);
  out.baths = rx(/([\d.]+)\s*baths?\b/i, top);
  out.sqft = rx(/([\d,]+)\s*sqft\b/i, top);
  out.year_built = rx(/Built in (\d{4})/);
  out.lot_acres = rx(/([\d.]+) Acres/i);
  out.price_per_sqft = rx(/\$([\d,]+)\/sqft/);
  out.mls = rx(/MLS#?:?\s*(\d+)/);
  out.parcel = rx(/Parcel number:\s*(\S+)/);
  out.date_on_market = rx(/Date on market:\s*([\d/]+)/);
  out.tax_assessed = parseMoney(rx(/Tax assessed value:\s*\$([\d,]+)/));
  out.annual_tax = parseMoney(rx(/Annual tax amount:\s*\$([\d,]+)/));

  let desc = null;
  const ws = text.indexOf("What's special");
  if (ws >= 0) {
    const tail = text.slice(ws + "What's special".length);
    const m = tail.match(/\n\s*\d+\s+days?\s+on Zillow/);
    desc = (m ? tail.slice(0, m.index) : tail.slice(0, 800)).trim();
  }
  out.description = desc;

  let factsRaw = null;
  const fs = text.indexOf("Facts & features");
  if (fs >= 0) {
    const fe = text.indexOf("Services availability", fs);
    factsRaw = text.slice(fs, fe > fs ? fe : fs + 6000);
  }
  out.facts_raw = factsRaw;
  out.facts = factsRaw
    ? factsRaw.split("\n").map(l => l.trim()).filter(l => l.includes(": "))
    : [];
  return out;
}

function parsePriceHistoryBlock(text) {
  const events = [];
  const start = text.indexOf("Price history");
  if (start < 0) return events;
  const end = text.indexOf("Public tax history", start);
  const block = text.slice(start, end > start ? end : start + 8000);
  const lines = block.split("\n").map(l => l.trim());
  for (let i = 0; i < lines.length; i++) {
    const dm = lines[i].match(/^(\d{1,2}\/\d{1,2}\/\d{4})\b\s*(.*)$/);
    if (!dm) continue;
    const dateS = dm[1];
    let event = null, price = null, mls = null, source = null;
    for (const ev of ZILLOW_EVENTS) {
      if (dm[2].startsWith(ev)) { event = ev; break; }
    }
    for (let j = i + 1; j < Math.min(i + 8, lines.length); j++) {
      const ln = lines[j];
      if (/^\d{1,2}\/\d{1,2}\/\d{4}\b/.test(ln)) break;
      if (event === null) {
        for (const ev of ZILLOW_EVENTS) {
          if (ln.startsWith(ev)) { event = ev; break; }
        }
      }
      if (price === null) {
        const pm = ln.match(/^\$([\d,]+)/);
        if (pm && !ln.slice(pm[0].length).startsWith("/sqft")) {
          price = parseMoney(pm[1]);
        }
      }
      if (ln.includes("Source:")) {
        const sm = ln.match(/Source:\s*(.+)$/);
        if (sm) {
          source = sm[1].trim().replace(/\s*Report$/, "");
          const im = source.match(/#(\w+)/);
          if (im) mls = im[1];
        }
      }
    }
    if (event) events.push({ date: dateS, event, price, mls,
                             source: source || "Zillow" });
  }
  return events;
}

function parseTaxHistoryBlock(text) {
  const rows = [];
  const start = text.indexOf("Public tax history");
  if (start < 0) return rows;
  const end = text.indexOf("Monthly payment", start);
  const block = text.slice(start, end > start ? end : start + 4000);
  const pat = /(\d{4})\s+\$([\d,]+)(?:\s*\(?\s*([+-][\d.]+)%\)?)?\s+\$([\d,]+)/g;
  for (const m of block.matchAll(pat)) {
    rows.push({ year: +m[1], taxes: parseMoney(m[2]),
                change_pct: m[3] !== undefined ? parseFloat(m[3]) : null,
                assessment: parseMoney(m[4]) });
  }
  return rows;
}

function parseMarketText(text) {
  const out = {};
  let m = text.match(/sale[-\s]?to[-\s]?list(?:\s*price)?\D{0,40}?([\d.]+)\s*%/i);
  if (m) {
    const val = parseFloat(m[1]);
    if (val >= 50 && val <= 150) out.sale_to_list_pct = val;
  }
  m = text.match(/median days on market\D{0,20}?(\d{1,3})\b/i);
  if (m) out.median_dom = +m[1];
  m = text.match(/median sale price\D{0,20}?\$([\d,]+)K?/i);
  if (m) {
    const price = parseMoney(m[1]);
    if (price) out.median_sale_price = price < 10000 ? price * 1000 : price;
  }
  m = text.match(/\b(buyer'?s market|seller'?s market|neutral market|most competitive|very competitive|somewhat competitive|not very competitive)\b/i);
  if (m) out.label = m[1];
  m = text.match(/sell(?:ing)?\s+(?:for\s+)?(?:about|around)?\s*([\d.]+)\s*%\s*(below|above)\s+list/i);
  if (m && !("sale_to_list_pct" in out)) {
    const delta = parseFloat(m[1]);
    out.sale_to_list_pct = Math.round(
      (m[2].toLowerCase() === "below" ? 100 - delta : 100 + delta) * 10) / 10;
  }
  return out;
}

function parseSoldComps(text, zipCode) {
  const rows = [];
  const seen = new Set();
  const pat = new RegExp(
    "\\$([\\d,]+)[\\s\\S]{0,90}?(\\d+(?:\\.\\d)?)\\s*(?:bds?|beds?)" +
    "[\\s\\S]{0,60}?([\\d,]+|[—–-]{1,3})\\s*(?:sq\\.? ?ft|sqft)" +
    "[\\s\\S]{0,160}?([0-9][^\\n$]{6,70}?" + zipCode + ")", "gi");
  for (const m of text.matchAll(pat)) {
    const price = parseMoney(m[1]);
    const sqft = /\d/.test(m[3][0]) ? parseMoney(m[3]) : null;
    if (!price || price < 30000 || price > 25000000) continue;
    if (sqft !== null && (sqft < 300 || sqft > 20000)) continue;
    const ppsf = sqft ? Math.round(price / sqft * 10) / 10 : null;
    if (ppsf !== null && (ppsf < 10 || ppsf > 5000)) continue;
    const address = m[4].replace(/\s+/g, " ").trim();
    const key = price + "|" + address;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({ price, beds: parseFloat(m[2]), sqft, ppsf, address });
  }
  return rows;
}

// ---------- analysis (ports of the Stage-5 pure functions) ----------

// ---------- county record (via the optional county Worker) ----------

function countyDateToUs(s) {
  const m = s.match(/^(\d{1,2})-([A-Z]{3})-(\d{4})$/);
  if (!m || !MONTHS[m[2]]) return null;
  return `${MONTHS[m[2]]}/${+m[1]}/${m[3]}`;
}

// Parses the tab-separated text sections the county Worker returns from an
// iasWorld portal (Butler, Franklin, Montgomery, ... — Ohio-style labels;
// fields that don't appear on a given county's pages simply come back null).
function parseIasWorldCounty(sections, name) {
  const prof = sections.profileall || sections.profile || "";
  const all = Object.values(sections).join("\n");
  const out = { county: name || "County record" };
  const g = (text, rx) => {
    const m = text.match(rx);
    return m ? m[1].trim() : null;
  };
  const ID_RX = /(?:PARID|Parcel Number|Parcel ID):[ \t]*([^\n\t]+)/;
  out.parcel_id = g(prof, ID_RX) || g(all, ID_RX);
  // Owner + address labels vary by county:
  //   Lake:       "Parcel Owner: X" / "Parcel Address: Y"
  //   Franklin:   "Owner <tab> X"           Montgomery: "Owner/Name/X" lines
  //   Butler:     "X <tab> Y" on the line after the parcel-id header
  //   Clermont:   "X Y" (no tab) after the header + "Address <tab> Y"
  const own = prof.match(
    /(?:PARID|Parcel Number|Parcel ID):[^\n]*\n+([^\n\t:]{3,70})(?:\t([^\n]*))?/);
  out.owner = g(prof, /Parcel Owner:\s*([^\n]+)/) ||
              g(prof, /^Owner\s*\t([^\n]+)/m) ||
              g(prof, /Owner\n+Name\n+([^\n\t:]{3,70})\n/) ||
              (own ? own[1].trim() : null);
  out.situs = g(prof, /Parcel Address:\s*([^\n]+)/) ||
              g(prof, /PARCEL LOCATION:\s*([^\n\t]+)/i) ||
              g(prof, /^Address\s*\t([^\n]+)/m) ||
              (own && own[2] ? own[2].trim() : null);
  // Clermont glues "OWNER ADDRESS" into one line — peel the address off.
  if (out.owner && out.situs && !out.owner.includes("\t") &&
      out.owner.endsWith(out.situs)) {
    out.owner = out.owner.slice(0, -out.situs.length).trim() || out.owner;
  }
  out.land_use = g(prof, /Land Use (?:Code|Description)\*?\*?\s*\t?([^\n]+)/);
  out.acres = g(prof, /(?:Total )?Acres\s*\t?\s*([\d.]+)/);
  out.district = g(prof, /District Name\s*\t?([^\n]+)/);
  out.gross_rate =
    parseFloat(g(all, /Gross Tax Rate\s*\t?\s*([\d.]+)/)) || null;
  out.effective_rate =
    parseFloat(g(all, /Effective Tax Rate\s*\t?\s*([\d.]+)/)) || null;
  out.credit_non_business =
    parseFloat(g(all, /Non Business Credit\s*\t?\s*(\.?[\d.]+)/)) || 0;
  out.credit_owner_occ =
    parseFloat(g(all, /Owner Occupied Credit\s*\t?\s*(\.?[\d.]+)/)) || 0;

  out.transfers = [];
  const tIdx = all.indexOf("Transfers (Date");
  if (tIdx >= 0) {
    const rx = /(\d{1,2}-[A-Z]{3}-\d{4})(?:\t\$?([\d,]+))?/g;
    for (const m of all.slice(tIdx, tIdx + 3000).matchAll(rx)) {
      const date = countyDateToUs(m[1]);
      if (date) out.transfers.push({ date,
        amount: m[2] ? parseMoney(m[2]) : null });
    }
  }

  // Value-history table shapes vary; try both and keep the richer read.
  // Butler-style: Taxyr, Land, Building, Total appraised, then 35% columns.
  const v7rows = [];
  const v7 =
    /^(\d{4})\t\$([\d,]+)\t\$([\d,]+)\t\$([\d,]+)\t\$([\d,]+)\t\$([\d,]+)\t\$([\d,]+)/gm;
  for (const m of all.matchAll(v7)) {
    v7rows.push({ year: +m[1], appraised: parseMoney(m[4]),
                  assessed: parseMoney(m[7]) });
  }
  // Lake-style: year, parcel, land, building, total appraised.
  const v4rows = [];
  const v4 = /^(\d{4})\t[\w.\-]+\t\$([\d,]+)\t\$([\d,]+)\t\$([\d,]+)/gm;
  for (const m of all.matchAll(v4)) {
    v4rows.push({ year: +m[1], appraised: parseMoney(m[4]), assessed: null });
  }
  out.values = v7rows.length >= v4rows.length ? v7rows : v4rows;
  const seenYears = new Set();
  out.values = out.values.filter(v =>
    seenYears.has(v.year) ? false : seenYears.add(v.year));
  out.values.sort((a, b) => b.year - a.year);

  out.tax_total = null;
  const taxLine = all.split("\n").find(l => l.startsWith("Real Estate\t"));
  if (taxLine) {
    const cols = taxLine.split("\t").map(c => c.trim().replace(/,/g, ""));
    const tot = cols[cols.length - 1];
    if (/^-?[\d.]+$/.test(tot)) out.tax_total = Math.round(parseFloat(tot));
  }

  out.permits_count =
    ((sections.permits || "").match(/\d{1,2}-[A-Z]{3}-\d{4}/g) || []).length;
  return out.parcel_id ? out : null;
}

// Ohio: net annual tax ≈ price × 35% × effective millage − rollback credits.
function ohioTaxProjection(price, county) {
  if (!price || !county || !county.effective_rate) return null;
  const gross = price * 0.35 * county.effective_rate / 1000;
  const net = gross * (1 - (county.credit_non_business || 0) -
                           (county.credit_owner_occ || 0));
  return Math.round(net);
}

function countyReappraisalJumpPct(values) {
  if (!values || values.length < 2) return null;
  const latest = values[0], prev = values[1];
  if (!latest.appraised || !prev.appraised) return null;
  return Math.round((latest.appraised - prev.appraised) /
                    prev.appraised * 1000) / 10;
}

function mergeTimeline(zillowEvents) {
  const merged = [];
  const seen = new Set();
  for (const ev of zillowEvents) {
    const d = parseDateAny(ev.date);
    const key = (d !== null ? isoDate(d) : String(ev.date)) + "|" +
                String(ev.event || "").toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push({ ...ev, _date: d });
  }
  merged.sort((a, b) => (a._date ?? Infinity) - (b._date ?? Infinity));
  return merged;
}

function cumulativeMarketDays(timeline, today) {
  today = today ?? todayUTC();
  const byMls = new Map();
  for (const ev of timeline) {
    const k = ev.mls ?? "";
    if (!byMls.has(k)) byMls.set(k, []);
    byMls.get(k).push(ev);
  }
  let total = 0;
  for (const events of byMls.values()) {
    let openStart = null;
    for (const ev of events) {
      const d = ev._date ?? parseDateAny(ev.date);
      if (d === null) continue;
      if (LISTING_OPENERS.includes(ev.event) && openStart === null) {
        openStart = d;
      } else if (LISTING_CLOSERS.includes(ev.event) && openStart !== null) {
        total += Math.max(daysBetween(openStart, d), 0);
        openStart = null;
      }
    }
    if (openStart !== null) total += Math.max(daysBetween(openStart, today), 0);
  }
  return total;
}

function countRelists(timeline) {
  const n = timeline.filter(ev => ev.event === "Listed for sale").length;
  return Math.max(n - 1, 0);
}

function analyzePriceCuts(timeline) {
  const cuts = [];
  let peak = null, lastAsk = null;
  for (const ev of timeline) {
    const price = ev.price;
    if (!price || !["Listed for sale", "Price change", "Back on market"]
        .includes(ev.event)) continue;
    if (lastAsk !== null && price < lastAsk) {
      cuts.push({ date: ev.date, from: lastAsk, to: price });
    }
    lastAsk = price;
    peak = peak === null ? price : Math.max(peak, price);
  }
  const totalCut = cuts.reduce((s, c) => s + (c.from - c.to), 0);
  const pct = (peak && lastAsk !== null && lastAsk < peak)
    ? Math.round((peak - lastAsk) / peak * 1000) / 10 : 0;
  return { cuts, total_cut: totalCut, pct_from_peak: pct,
           peak, current_ask: lastAsk };
}

function countFailedPendings(timeline) {
  let failed = 0;
  const events = timeline.filter(ev => ev.event);
  for (let i = 0; i < events.length; i++) {
    if (events[i].event !== "Pending sale") continue;
    for (const nxt of events.slice(i + 1)) {
      if (nxt.event.startsWith("Sold")) break;
      if (["Listed for sale", "Back on market", "Price change",
           "Listing removed"].includes(nxt.event)) { failed++; break; }
    }
  }
  return failed;
}

function detectFlip(timeline) {
  let sales = timeline
    .filter(ev => String(ev.event || "").startsWith("Sold") && ev.price)
    .map(ev => [ev._date ?? parseDateAny(ev.date), ev.price])
    .filter(([d]) => d !== null);
  sales.sort((a, b) => a[0] - b[0]);
  const dates = sales.map(([d]) => d);
  for (const d of new Set(dates)) {
    if (dates.filter(x => x === d).length >= 2) {
      return `Two or more transfers recorded on ${isoDate(d)}`;
    }
  }
  for (let i = 1; i < sales.length; i++) {
    const [d1, p1] = sales[i - 1], [d2, p2] = sales[i];
    if (daysBetween(d1, d2) < 365 && p1 && p2 > p1 * 1.30) {
      const gain = Math.round((p2 - p1) / p1 * 100);
      return `Sold ${isoDate(d1)} for ${fmtMoney(p1)} then ${isoDate(d2)} ` +
             `for ${fmtMoney(p2)} (+${gain}% in <12 months)`;
    }
  }
  return null;
}

// Browser adaptation of assessment_jump_years: the county building-value
// history isn't reachable from a static page, but the listing's own tax
// history carries assessment changes.
function taxAssessmentJumpYears(taxHistory) {
  return taxHistory
    .filter(r => r.change_pct !== null && r.change_pct >= 15)
    .map(r => r.year)
    .sort((a, b) => a - b);
}

function buildRedFlags(analysis) {
  const flags = [];
  if (analysis.relist_count >= 1) {
    flags.push(`Re-listed ${analysis.relist_count} time(s) after the first listing.`);
  }
  const cuts = analysis.price_cuts || {};
  if (cuts.total_cut) {
    flags.push(`Price cuts total ${fmtMoney(cuts.total_cut)} ` +
               `(${cuts.pct_from_peak}% below peak ask).`);
  }
  if (analysis.failed_pendings) {
    flags.push(`${analysis.failed_pendings} pending sale(s) fell through.`);
  }
  if (analysis.flip_flag) flags.push(`Possible flip: ${analysis.flip_flag}`);
  if (analysis.tax_jump_years && analysis.tax_jump_years.length) {
    flags.push(`Tax assessment jumped 15%+ in ${analysis.tax_jump_years.join(", ")} ` +
               `— ask the county whether permits were filed for that work.`);
  }
  if (analysis.cumulative_market_days > 180) {
    flags.push(`Cumulative time on market is ${analysis.cumulative_market_days} days.`);
  }
  return flags;
}

// Port of seller_motivation, minus the county-record signals (absentee
// owner, county-value gap, cost basis) which need the CLI's auditor pull.
function sellerMotivation(analysis) {
  let score = 0;
  const reasons = [];
  const dom = analysis.cumulative_market_days || 0;
  if (dom) {
    score += Math.min(dom, 365) / 365 * 30;
    if (dom >= 60) reasons.push(`${dom} cumulative days on market`);
  }
  const pct = (analysis.price_cuts || {}).pct_from_peak || 0;
  if (pct) {
    score += Math.min(pct, 15) / 15 * 20;
    reasons.push(`ask already cut ${pct}% from peak`);
  }
  const failed = analysis.failed_pendings || 0;
  if (failed) { score += 15; reasons.push(`${failed} pending sale(s) fell through`); }
  const relists = analysis.relist_count || 0;
  if (relists) { score += Math.min(relists * 5, 10); reasons.push(`re-listed ${relists} time(s)`); }
  if (analysis.flip_flag) { score += 5; reasons.push("flip pattern (short hold / same-day deeds)"); }
  return { score: Math.round(Math.min(score, 100) * 10) / 10, reasons,
           partial: "county-record signals (absentee owner, county-value gap, " +
                    "cost basis) need the CLI's auditor pull — up to 25 further points" };
}

function compsValueEstimate(comps, subjectSqft, subjectBeds) {
  const rows = ((comps || {}).rows || []).filter(r => r.sqft && r.ppsf);
  if (!rows.length || !subjectSqft) return null;
  const similar = rows.filter(r =>
    Math.abs(r.sqft - subjectSqft) <= subjectSqft * 0.35 &&
    (subjectBeds == null || r.beds == null || Math.abs(r.beds - subjectBeds) <= 1));
  let basis, ppsf;
  if (similar.length >= 3) {
    basis = "similar"; ppsf = median(similar.map(r => r.ppsf));
  } else {
    basis = "all"; ppsf = comps.median_ppsf;
  }
  if (!ppsf) return null;
  const value = Math.round(ppsf * subjectSqft / 500) * 500;
  return { value, ppsf: Math.round(ppsf * 10) / 10, basis,
           n_similar: similar.length, n_all: rows.length };
}

function ladderAnchoring(motivation, market, valueGapPct) {
  let base = 2.0, slope = 2.8, mode = "heuristic";
  const notes = [];
  const stl = (market || {}).sale_to_list_pct;
  if (stl) {
    base = Math.max(0, Math.round((100 - stl) * 10) / 10);
    mode = "empirical";
    notes.push(`zip median sale-to-list ${stl}% -> base discount ${base}%`);
  }
  const dom = (market || {}).median_dom;
  if (dom) {
    slope = Math.round(Math.min(4.5, Math.max(2.0, 2.0 + dom / 60)) * 100) / 100;
    notes.push(`zip median DOM ${dom} -> curve slope ${slope}`);
  }
  const score = (motivation || {}).score || 0;
  let d50 = base + 10 * (score / 100);
  if (score) notes.push(`motivation ${score}/100 -> +${(10 * score / 100).toFixed(1)} pts discount`);
  if (valueGapPct !== null && valueGapPct !== undefined) {
    const shift = Math.max(-5, Math.min(5, 0.5 * valueGapPct));
    d50 += shift;
    if (mode === "heuristic") mode = "comps-adjusted";
    notes.push(`ask is ${valueGapPct >= 0 ? "+" : ""}${valueGapPct.toFixed(1)}% ` +
               `vs sold-comps value -> d50 ${shift >= 0 ? "+" : ""}${shift.toFixed(1)} pts`);
  }
  return { mode, d50: Math.round(Math.max(0.5, Math.min(20, d50)) * 10) / 10,
           slope, notes };
}

function estimateOfferLadder(listPrice, motivation, anchoring) {
  if (!listPrice) return [];
  let d50, slope;
  if (anchoring) {
    ({ d50, slope } = anchoring);
  } else {
    const score = (motivation || {}).score || 0;
    d50 = 2 + 10 * (score / 100);
    slope = 2.8;
  }
  const odds = d => 1 / (1 + Math.exp((d - d50) / slope));
  const rungs = [...new Set([0, Math.round(d50 * 5) / 10,
                             Math.round(d50 * 10) / 10,
                             Math.round(d50 * 15) / 10])].sort((a, b) => a - b);
  return rungs.map(d => {
    const offer = Math.round(listPrice * (1 - d / 100) / 500) * 500;
    const p = odds(d);
    return { discount_pct: d, offer,
             pct_of_list: Math.round((100 - d) * 10) / 10,
             acceptance_probability_pct: Math.round(p * 100),
             expected_savings: Math.round((listPrice - offer) * p) };
  });
}

// ---------- top-level: run everything on pasted text ----------

function analyzeAll({ address, listingText, marketText, soldText, zipCode,
                      countySections, countyName, countyState }) {
  const gaps = [];
  const zillow = parseZillowText(listingText);
  zillow.price_history = parsePriceHistoryBlock(listingText);
  zillow.tax_history = parseTaxHistoryBlock(listingText);

  let county = null;
  if (countySections) {
    county = parseIasWorldCounty(countySections, countyName);
    if (!county) gaps.push("County record fetched but could not be parsed.");
  }
  if (!zillow.price) gaps.push("No asking price found in the listing text — offer ladder unavailable.");
  if (!zillow.price_history.length) gaps.push("No 'Price history' rows found — scroll the whole listing before copying.");

  let market = null;
  if (marketText && marketText.trim()) {
    market = parseMarketText(marketText);
    if (!Object.keys(market).length) {
      market = null;
      gaps.push("Market page text pasted but no stats parsed.");
    }
  }

  let comps = null;
  if (soldText && soldText.trim() && zipCode) {
    const rows = parseSoldComps(soldText, zipCode);
    if (rows.length) {
      const ppsfVals = rows.filter(r => r.ppsf).map(r => r.ppsf);
      comps = { source: "pasted sold search", n: rows.length,
                n_with_sqft: ppsfVals.length,
                median_ppsf: ppsfVals.length
                  ? Math.round(median(ppsfVals) * 10) / 10 : null,
                rows: rows.slice(0, 40) };
    } else {
      gaps.push(`Sold-search text pasted but no rows with ZIP ${zipCode} parsed.`);
    }
  } else if (soldText && soldText.trim() && !zipCode) {
    gaps.push("Sold comps need a 5-digit ZIP (pick an address in step 1).");
  }

  const events = zillow.price_history.slice();
  if (county) {
    for (const tr of county.transfers) {
      events.push({ date: tr.date, event: tr.amount ? "Sold" : "Transferred",
                    price: tr.amount, mls: null,
                    source: "County transfer record" });
    }
  }
  const timeline = mergeTimeline(events);
  const a = { timeline };
  a.cumulative_market_days = cumulativeMarketDays(timeline);
  a.relist_count = countRelists(timeline);
  a.price_cuts = analyzePriceCuts(timeline);
  a.failed_pendings = countFailedPendings(timeline);
  a.flip_flag = detectFlip(timeline);
  a.tax_jump_years = taxAssessmentJumpYears(zillow.tax_history);
  a.red_flags = buildRedFlags(a);
  a.county_reappraisal_jump_pct =
    county ? countyReappraisalJumpPct(county.values) : null;
  if (a.county_reappraisal_jump_pct !== null &&
      a.county_reappraisal_jump_pct >= 15 && county.values.length >= 2) {
    a.red_flags.push(
      `County appraised value jumped +${a.county_reappraisal_jump_pct}% ` +
      `(${county.values[1].year}→${county.values[0].year})` +
      (county.permits_count === 0 ? " with no permits on file" : "") +
      " — county-wide reappraisal or unpermitted improvements; " +
      "expect higher taxes either way.");
  }
  a.tax_projection = countyState === "OH"
    ? ohioTaxProjection(zillow.price, county) : null;
  a.motivation = sellerMotivation(a);

  const subjectSqft = zillow.sqft ? parseMoney(zillow.sqft) : null;
  const subjectBeds = zillow.beds ? parseFloat(zillow.beds) : null;
  a.comp_value_estimate = compsValueEstimate(comps, subjectSqft, subjectBeds);
  a.value_gap_pct = (zillow.price && a.comp_value_estimate)
    ? Math.round((zillow.price - a.comp_value_estimate.value) /
                 a.comp_value_estimate.value * 1000) / 10
    : null;
  a.ladder_anchoring = ladderAnchoring(a.motivation, market, a.value_gap_pct);
  a.offer_ladder = estimateOfferLadder(zillow.price, a.motivation, a.ladder_anchoring);

  return { address, zillow, market, comps, county, analysis: a, gaps,
           generated: isoDate(todayUTC()) };
}

// ---------- Markdown report (browser subset of build_report) ----------

function buildReportMd(r) {
  const z = r.zillow, a = r.analysis;
  const L = [];
  L.push(`# Due Diligence Report: ${r.address}`, "");
  L.push(`*Generated ${r.generated} by the house-recon browser analyzer — ` +
         `listing-only mode. The [CLI](https://github.com/Reggie-Reuss/house-recon) ` +
         `adds county records, photos, maps, and the AI judgment layer.*`, "");
  L.push("## Snapshot", "", "| Field | Value |", "|---|---|");
  const snap = [
    ["Asking price", fmtMoney(z.price)],
    ["Beds / Baths", `${z.beds || "n/a"} / ${z.baths || "n/a"}`],
    ["Square feet", z.sqft || "n/a"],
    ["Year built", z.year_built || "n/a"],
    ["Lot", z.lot_acres ? `${z.lot_acres} acres` : "n/a"],
    ["$ / sqft", z.price_per_sqft ? `$${z.price_per_sqft}` : "n/a"],
    ["MLS #", z.mls || "n/a"],
    ["Parcel", z.parcel || "n/a"],
    ["Date on market", z.date_on_market || "n/a"],
    ["Tax assessed value", fmtMoney(z.tax_assessed)],
    ["Annual tax (listed)", fmtMoney(z.annual_tax)],
  ];
  for (const [k, v] of snap) L.push(`| ${k} | ${v} |`);
  L.push("", "## Dated History Timeline", "",
         "| Date | Event | Price | Source |", "|---|---|---|---|");
  for (const ev of a.timeline) {
    L.push(`| ${ev.date} | ${ev.event} | ${ev.price ? fmtMoney(ev.price) : ""} | ${ev.source || ""} |`);
  }
  if (!a.timeline.length) L.push("| n/a | no events captured | | |");
  const cuts = a.price_cuts || {};
  L.push("", "**Timeline analysis:**", "");
  L.push(`- Cumulative days on market: ${a.cumulative_market_days}`);
  L.push(`- Re-lists after first listing: ${a.relist_count}`);
  L.push(cuts.cuts && cuts.cuts.length
    ? `- Price cuts: ${cuts.cuts.map(c => `${c.date}: ${fmtMoney(c.from)} -> ${fmtMoney(c.to)}`).join("; ")} ` +
      `(total ${fmtMoney(cuts.total_cut)}, ${cuts.pct_from_peak}% off peak ask ${fmtMoney(cuts.peak)})`
    : "- Price cuts: none detected");
  L.push(`- Failed pendings: ${a.failed_pendings}`);
  L.push(`- Flip indicator: ${a.flip_flag || "none"}`);
  L.push("", "## Red Flags", "");
  L.push(...(a.red_flags.length ? a.red_flags.map(f => `- ${f}`)
                                : ["- None detected automatically."]));
  L.push("", "## Offer Strategy", "");
  if (r.market) {
    const bits = [];
    if (r.market.label) bits.push(r.market.label);
    if (r.market.sale_to_list_pct) bits.push(`median sale-to-list ${r.market.sale_to_list_pct}%`);
    if (r.market.median_dom) bits.push(`median ${r.market.median_dom} days on market`);
    if (r.market.median_sale_price) bits.push(`median sale ${fmtMoney(r.market.median_sale_price)}`);
    L.push(`**Zip market temperature:** ${bits.join("; ")}`);
  }
  const est = a.comp_value_estimate;
  if (est) {
    const gapTxt = a.value_gap_pct !== null
      ? `; ask is ${a.value_gap_pct >= 0 ? "+" : ""}${a.value_gap_pct}% vs this estimate` : "";
    L.push(`**Sold-comps value estimate:** ${fmtMoney(est.value)} ` +
           `($${est.ppsf}/sqft median of ${est.basis === "similar" ? est.n_similar : est.n_all} ` +
           `${est.basis === "similar" ? "similar-size " : ""}sold comps)${gapTxt}`);
  }
  if (r.market || est) L.push("");
  if (a.offer_ladder.length) {
    L.push(`**Seller-motivation score:** ${a.motivation.score}/100 (listing signals only)`);
    L.push(...a.motivation.reasons.map(x => `- ${x}`));
    L.push("", `**Ladder anchoring (${a.ladder_anchoring.mode}):**`);
    L.push(...a.ladder_anchoring.notes.map(n => `- ${n}`));
    L.push("", "| Offer | % of list | Est. acceptance odds | Expected savings |",
           "|---|---|---|---|");
    for (const rung of a.offer_ladder) {
      L.push(`| ${fmtMoney(rung.offer)} | ${rung.pct_of_list}% | ` +
             `~${rung.acceptance_probability_pct}% | ${fmtMoney(rung.expected_savings)} |`);
    }
    L.push("", "*Calibrated estimates from public listing signals — not a " +
           "fitted statistical model. Refine with your agent.*");
  } else {
    L.push("*(no list price captured — offer ladder unavailable)*");
  }
  if (z.tax_history.length) {
    L.push("", "## Listing Tax History", "",
           "| Year | Taxes | Change | Assessment |", "|---|---|---|---|");
    for (const t of z.tax_history) {
      L.push(`| ${t.year} | ${fmtMoney(t.taxes)} | ` +
             `${t.change_pct !== null ? t.change_pct + "%" : ""} | ${fmtMoney(t.assessment)} |`);
    }
  }
  if (r.comps && r.comps.rows.length) {
    L.push("", `## Sold Comps (${r.comps.n} parsed)`, "",
           "| Price | Beds | Sqft | $/sqft | Address |", "|---|---|---|---|---|");
    for (const c of r.comps.rows.slice(0, 15)) {
      L.push(`| ${fmtMoney(c.price)} | ${c.beds ?? ""} | ` +
             `${c.sqft ? c.sqft.toLocaleString("en-US") : "n/a"} | ` +
             `${c.ppsf ? "$" + c.ppsf : "n/a"} | ${c.address} |`);
    }
  }
  if (r.county) {
    const c = r.county;
    L.push("", `## County Record — ${c.county}`, "",
           "| Field | Value |", "|---|---|",
           `| Owner | ${c.owner || "n/a"} |`,
           `| Parcel | ${c.parcel_id || "n/a"} |`,
           `| Land use | ${c.land_use || "n/a"} |`,
           `| Acres | ${c.acres || "n/a"} |`,
           `| Taxing district | ${c.district || "n/a"} |`,
           `| Current annual tax | ${fmtMoney(c.tax_total)} |`,
           `| Effective rate | ${c.effective_rate ?? "n/a"} mills |`,
           `| Permits on file | ${c.permits_count} |`);
    if (c.transfers.length) {
      L.push("", "### Transfers", "", "| Date | Amount |", "|---|---|");
      for (const t of c.transfers) {
        L.push(`| ${t.date} | ${t.amount ? fmtMoney(t.amount) : "(no consideration)"} |`);
      }
    }
    if (c.values.length) {
      L.push("", "### Value History (county)", "",
             "| Year | Appraised (100%) | Assessed (35%) |", "|---|---|---|");
      for (const v of c.values.slice(0, 6)) {
        L.push(`| ${v.year} | ${fmtMoney(v.appraised)} | ${fmtMoney(v.assessed)} |`);
      }
    }
    if (a.tax_projection) {
      L.push("", `**Ohio tax projection at asking price:** ~${fmtMoney(a.tax_projection)}/yr ` +
             `after reappraisal to the sale price` +
             (c.tax_total ? ` (current: ${fmtMoney(c.tax_total)}/yr)` : "") + ".");
    }
  }
  L.push("", "## Data Gaps", "");
  L.push(...(r.gaps.length ? r.gaps.map(g => `- ${g}`) : ["- None."]));
  L.push("", "---",
         "*Automated analysis of user-supplied page text; data may be stale, " +
         "incomplete, or mis-parsed. Verify everything independently before " +
         "any purchase decision. Not professional advice.*", "");
  return L.join("\n");
}
