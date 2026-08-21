/* house-recon web UI: address normalization via the U.S. Census Bureau
 * geocoder (the page's only network call — pasted text never leaves the
 * browser), then the recon.js engine, then the report renderer. */

"use strict";

// County adapters shipped in the CLI, keyed by county FIPS (GEOID).
const CLI_COUNTY_ADAPTERS = { "39017": "butler-oh (Butler County, OH)" };

// County Worker (workers/county): fetches the county auditor record with
// CORS so this page can use it. Set after `wrangler deploy`, e.g.
// "https://house-recon-county.<account>.workers.dev". The ?county_worker=
// URL param overrides it (handy for local `wrangler dev` testing).
const COUNTY_WORKER_DEFAULT = "https://house-recon-county.county.workers.dev";
const COUNTY_WORKER_URL = ((new URLSearchParams(location.search)
  .get("county_worker")) || COUNTY_WORKER_DEFAULT).replace(/\/+$/, "");
// County GEOID -> Worker route, loaded from the Worker's own registry so the
// page never hard-codes a county list.
let countyRoutes = null;
async function loadCountyRoutes() {
  countyRoutes = {};
  if (!COUNTY_WORKER_URL) return;
  try {
    const res = await fetch(`${COUNTY_WORKER_URL}/counties`);
    const data = await res.json();
    for (const c of (data && data.counties) || []) {
      if (c.geoid && c.route) countyRoutes[c.geoid] = c.route;
    }
  } catch { /* county feature silently unavailable */ }
}
const countyRoutesReady = loadCountyRoutes();

const $ = id => document.getElementById(id);
const state = { address: null, zip: null, countySections: null,
                countyMeta: null };

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function setStatus(el, msg, isErr) {
  el.textContent = msg;
  el.classList.toggle("err", !!isErr);
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ---------- Census geocoder ----------

const CENSUS_BASE = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress";

function censusJsonp(q) {
  return new Promise((resolve, reject) => {
    const cb = "hrCensusCb" + Date.now();
    const script = document.createElement("script");
    const timer = setTimeout(() => { cleanup(); reject(new Error("timeout")); }, 12000);
    function cleanup() {
      clearTimeout(timer); delete window[cb]; script.remove();
    }
    window[cb] = data => { cleanup(); resolve(data); };
    script.src = `${CENSUS_BASE}?address=${encodeURIComponent(q)}` +
      "&benchmark=Public_AR_Current&vintage=Current_Current&layers=Counties" +
      `&format=jsonp&callback=${cb}`;
    script.onerror = () => { cleanup(); reject(new Error("jsonp failed")); };
    document.head.appendChild(script);
  });
}

async function censusLookup(q) {
  // The Census geocoder sends no CORS headers; JSONP is its browser transport.
  const data = await censusJsonp(q);
  const matches = (data.result && data.result.addressMatches) || [];
  return matches.map(m => {
    const geo = m.geographies || {};
    const countyKey = Object.keys(geo).find(k => k.includes("Counties"));
    const county = countyKey && geo[countyKey] && geo[countyKey][0];
    const zipM = (m.matchedAddress || "").match(/\b(\d{5})(?:-\d{4})?\s*$/);
    return {
      address: m.matchedAddress,
      zip: zipM ? zipM[1] : null,
      county: county
        ? { name: county.NAME || county.BASENAME, geoid: county.GEOID }
        : null,
    };
  });
}

// ---------- step 1: address ----------

function marketUrl(zip) { return `https://www.redfin.com/zipcode/${zip}/housing-market`; }
function soldUrl(zip) {
  return `https://www.redfin.com/zipcode/${zip}/filter/include=sold-6mo,property-type=house`;
}

function setOpenLink(id, href) {
  const a = $(id);
  a.href = href;
  a.removeAttribute("aria-disabled");
  refreshAttention();
}

function applyZip(zip) {
  if (!zip) return;
  state.zip = zip;
  setOpenLink("open-market", marketUrl(zip));
  setOpenLink("open-sold", soldUrl(zip));
  $("ex-market").textContent = `redfin.com/zipcode/${zip}/housing-market`;
  $("ex-sold").textContent =
    `redfin.com/zipcode/${zip}/filter/include=sold-6mo,property-type=house`;
}

function renderMatches(matches) {
  const box = $("addr-matches");
  box.innerHTML = "";
  matches.forEach((m, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "match";
    const adapter = m.county && CLI_COUNTY_ADAPTERS[m.county.geoid];
    const auto = m.county && COUNTY_WORKER_URL && countyRoutes &&
      countyRoutes[m.county.geoid];
    btn.innerHTML = `${esc(m.address)}<span class="county">${
      m.county ? esc(m.county.name) : "county unknown"}${
      auto ? " — ✅ county record fetched automatically"
           : adapter ? ` — ✅ CLI county adapter: ${esc(adapter)}`
                     : " — no county adapter yet (browser analysis unaffected)"}</span>`;
    btn.addEventListener("click", () => selectMatch(m, btn));
    box.appendChild(btn);
    if (matches.length === 1 && i === 0) selectMatch(m, btn);
  });
}

// ---------- county record via the Worker ----------

const STREET_SUFFIXES = new Set([
  "st", "street", "ave", "avenue", "dr", "drive", "rd", "road", "ln", "lane",
  "ct", "court", "cir", "circle", "blvd", "boulevard", "way", "pl", "place",
  "trl", "trail", "pike", "hwy", "ter", "terrace"]);

const STREET_DIRECTIONS = new Set([
  "n", "s", "e", "w", "ne", "nw", "se", "sw",
  "north", "south", "east", "west"]);

function splitStreetAddress(addr) {
  const first = addr.split(",")[0].trim();
  const m = first.match(/^(\d+)\s+(.+)$/);
  if (!m) return null;
  const tokens = m[2].split(/\s+/);
  while (tokens.length > 1 && STREET_SUFFIXES.has(
      tokens[tokens.length - 1].toLowerCase().replace(/\.$/, ""))) {
    tokens.pop();
  }
  const street = tokens.join(" ");
  // County portals search street names begins-with, and most index the
  // direction separately — so "S High St" finds nothing while "High" hits.
  // Try the fuller form first, then progressively barer ones.
  const variants = [street];
  if (tokens.length > 1 &&
      STREET_DIRECTIONS.has(tokens[0].toLowerCase().replace(/\.$/, ""))) {
    variants.push(tokens.slice(1).join(" "));
  }
  return { number: m[1], variants };
}

async function fetchCountyRecord(address, county) {
  const status = $("county-status");
  state.countySections = null;
  state.countyMeta = null;
  await countyRoutesReady;
  const route = county && countyRoutes[county.geoid];
  if (!route || !COUNTY_WORKER_URL) { setStatus(status, ""); return; }
  const parts = splitStreetAddress(address);
  if (!parts) return;
  setStatus(status, `🏛 Fetching the ${county.name} auditor record ` +
    "(owner, transfers, values, taxes)…");
  try {
    let data = null;
    for (const street of parts.variants) {
      const res = await fetch(`${COUNTY_WORKER_URL}/${route}` +
        `?number=${encodeURIComponent(parts.number)}` +
        `&street=${encodeURIComponent(street)}`);
      data = await res.json();
      if (data.ok) break;
    }
    if (data.ok && data.sections) {
      state.countySections = data.sections;
      state.countyMeta = { name: data.name || county.name,
                           state: data.state || null,
                           matches: data.matches || 1 };
      setStatus(status, `🏛 ${state.countyMeta.name} record fetched ✓` +
        (state.countyMeta.matches > 1
          ? ` (closest of ${state.countyMeta.matches} matches — check the ` +
            "owner/address in the report)"
          : "") +
        " — it will appear in the report" +
        (state.countyMeta.state === "OH" ? " with an Ohio tax projection." : "."));
    } else {
      setStatus(status, "🏛 County record unavailable (" +
        (data.detail || data.error || "no data") +
        ") — analysis continues without it.");
    }
  } catch {
    setStatus(status,
      "🏛 County lookup unreachable — analysis continues without it.");
  }
}

function selectMatch(m, btn) {
  document.querySelectorAll(".match.selected")
    .forEach(el => el.classList.remove("selected"));
  if (btn) btn.classList.add("selected");
  state.address = m.address;
  fetchCountyRecord(m.address, m.county);
  $("addr-input").value = m.address;
  setOpenLink("open-listing",
    `https://www.zillow.com/homes/${slugify(m.address)}_rb/`);
  $("ex-listing").textContent =
    `zillow.com/homedetails/${slugify(m.address)}/…`;
  if (m.zip) applyZip(m.zip);
  setStatus($("addr-status"),
    "Address confirmed. Now use each Open ↗ button in step 2: copy that " +
    "page's text (Ctrl+A, Ctrl+C) and paste it into the matching box.");
  validatePastes();
}

async function findAddress() {
  const q = $("addr-input").value.trim();
  const status = $("addr-status");
  $("addr-matches").innerHTML = "";
  state.address = null;
  if (q.length < 8) {
    setStatus(status, "Type a full street address, city, and state.", true);
    return;
  }
  setStatus(status, "Looking up address (U.S. Census geocoder)…");
  try {
    const matches = await censusLookup(q);
    if (!matches.length) {
      setStatus(status,
        "No exact match found. Check the spelling/number, or continue — " +
        "paste the listing in step 2 and analysis still works.", true);
      return;
    }
    setStatus(status, matches.length === 1
      ? "One match found:" : `${matches.length} matches — pick the right one:`);
    renderMatches(matches);
  } catch (e) {
    setStatus(status,
      "Address lookup unreachable right now. No problem — paste the listing " +
      "text in step 2 and analysis still works.", true);
  }
}

// ---------- step 2: paste validation & guidance ----------

// The #1 mistake: pasting the page's URL instead of the page's text.
function pastedUrl(text) {
  const t = text.trim();
  if (!t || t.length > 400 || t.split(/\s+/).length > 3) return null;
  const m = t.match(/(?:https?:\/\/)?(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}\/\S*/i);
  if (!m) return null;
  return m[0].startsWith("http") ? m[0] : "https://" + m[0];
}

const URL_FIX = "click the page so it's focused, press Ctrl+A (the whole " +
  "page highlights — that's right), Ctrl+C, then come back and paste here.";

function urlPasteMessage(url, openId) {
  // Make the mistaken paste useful: aim the Open button at their URL.
  setOpenLink(openId, url);
  $(openId).classList.add("attn");
  return "✗ That's the page's link (URL), not its text — this page can't " +
    "download other websites (browsers forbid it). I pointed the Open ↗ " +
    "button at your link: click it, then on that page " + URL_FIX;
}

function setPasteState(id, cls, msg) {
  const el = $(id);
  el.className = "pastestate" + (cls ? " " + cls : "");
  el.textContent = msg || "";
}

function validateListing() {
  const text = $("paste-listing").value;
  if (!text.trim()) { setPasteState("state-listing", "", ""); return; }
  const url = pastedUrl(text);
  if (url) {
    const zipM = url.match(/(\d{5})(?:[/_-]|$)/);
    if (zipM && !state.zip) applyZip(zipM[1]);
    const slug = url.match(/(?:homedetails|homes)\/([A-Za-z0-9-]+)/);
    if (slug && !$("addr-input").value.trim()) {
      $("addr-input").value = slug[1].replace(/-/g, " ").replace(/_rb$/, "");
    }
    setPasteState("state-listing", "err", urlPasteMessage(url, "open-listing"));
    return;
  }
  const z = parseZillowText(text);
  const events = parsePriceHistoryBlock(text).length;
  if (!z.price && !events) {
    setPasteState("state-listing", "err",
      "✗ No price or price history found in that text. Make sure you're on " +
      "the house's own page (URL like zillow.com/homedetails/…), scroll it " +
      "to the bottom once so everything loads, then " + URL_FIX);
    return;
  }
  const bits = [];
  if (z.price) bits.push(fmtMoney(z.price));
  if (z.beds) bits.push(z.beds + " bd");
  if (z.sqft) bits.push(z.sqft + " sqft");
  bits.push(events + " price-history events");
  if (!events) {
    setPasteState("state-listing", "warn",
      `⚠ Found ${bits.join(" · ")} — but no “Price history” section. ` +
      "Zillow loads it as you scroll: scroll that page to the bottom, then " +
      "re-copy. (Analysis still runs; the timeline will be thin.)");
  } else {
    setPasteState("state-listing", "ok", `✓ Listing text detected: ${bits.join(" · ")}`);
  }
}

function validateMarket() {
  const text = $("paste-market").value;
  if (!text.trim()) { setPasteState("state-market", "", ""); return; }
  const url = pastedUrl(text);
  if (url) {
    const zipM = url.match(/zipcode\/(\d{5})/);
    if (zipM && !state.zip) applyZip(zipM[1]);
    setPasteState("state-market", "err", urlPasteMessage(url, "open-market"));
    return;
  }
  const m = parseMarketText(text);
  if (!Object.keys(m).length) {
    setPasteState("state-market", "err",
      "✗ No market stats in that text — expected the page at " +
      `redfin.com/zipcode/${state.zip || "YOURZIP"}/housing-market. ` +
      "Open it, then " + URL_FIX);
    return;
  }
  const bits = [];
  if (m.sale_to_list_pct) bits.push(`sale-to-list ${m.sale_to_list_pct}%`);
  if (m.median_dom) bits.push(`median ${m.median_dom} days on market`);
  if (m.label) bits.push(m.label);
  setPasteState("state-market", "ok", `✓ Market stats found: ${bits.join(" · ")}`);
}

function validateSold() {
  const text = $("paste-sold").value;
  if (!text.trim()) { setPasteState("state-sold", "", ""); return; }
  const url = pastedUrl(text);
  if (url) {
    const zipM = url.match(/zipcode\/(\d{5})/);
    if (zipM && !state.zip) applyZip(zipM[1]);
    setPasteState("state-sold", "err", urlPasteMessage(url, "open-sold"));
    return;
  }
  if (!state.zip) {
    setPasteState("state-sold", "warn",
      "⚠ Need the ZIP to match sold rows — pick the address in step 1 first.");
    return;
  }
  const rows = parseSoldComps(text, state.zip);
  if (!rows.length) {
    setPasteState("state-sold", "err",
      `✗ No sold rows with ZIP ${state.zip} found — expected the page at ` +
      `redfin.com/zipcode/${state.zip}/filter/include=sold-6mo,property-type=house. ` +
      "Scroll through the results once, then " + URL_FIX);
    return;
  }
  const withSqft = rows.filter(r => r.sqft).length;
  setPasteState("state-sold", "ok",
    `✓ ${rows.length} sold comps parsed (${withSqft} with sqft)`);
}

// ---------- clipboard auto-fill ----------

let lastOpened = null;   // which Open ↗ was clicked last: listing|market|sold
let lastClip = "";       // last clipboard text we already handled

// The bookmarklet and the extension prefix grabbed text with a marker line
// carrying the source URL, so we can route and enrich without guessing.
const GRAB_MARK = "[house-recon page grab] url: ";

function grabMarkUrl(text) {
  if (!text || !text.startsWith(GRAB_MARK)) return null;
  const nl = text.indexOf("\n");
  return text.slice(GRAB_MARK.length, nl === -1 ? undefined : nl).trim();
}

function kindFromUrl(url) {
  if (!url) return null;
  if (/housing-market/i.test(url)) return "market";
  if (/include=sold/i.test(url)) return "sold";
  if (/homedetails|zillow\.com\/homes\//i.test(url)) return "listing";
  return null;
}

// Absorb what a grab-marker URL tells us (zip, address slug) into state.
function absorbGrabUrl(url) {
  if (!url) return;
  const zipM = url.match(/zipcode\/(\d{5})/) ||
    url.match(/-(\d{5})(?:[/_]|$)/) || url.match(/\b(\d{5})\b(?!.*\d{5})/);
  if (zipM && !state.zip) applyZip(zipM[1]);
  const slug = url.match(/homedetails\/([A-Za-z0-9-]+)/);
  if (slug && !$("addr-input").value.trim()) {
    $("addr-input").value = slug[1].replace(/-/g, " ");
  }
}

// Recognize which page a copied text came from, so it lands in the right box.
function classifyPaste(text) {
  const marked = kindFromUrl(grabMarkUrl(text));
  if (marked) return marked;
  if (text.includes("Price history") || text.includes("What's special")
      || /zestimate/i.test(text)) return "listing";
  const soldMarks = (text.match(/\bSOLD\s+[A-Z]{3}\b/gi) || []).length;
  if (soldMarks >= 3 || /include=sold/i.test(text)) return "sold";
  if (Object.keys(parseMarketText(text)).length >= 2) return "market";
  if (parseZillowText(text).price && /\bbeds?\b/i.test(text)) return "listing";
  return null;
}

function revealManual() {
  const d = $("manual-mode");
  if (d && !d.open) d.open = true;
}

function fillBox(kind, txt, note, reveal) {
  $("paste-" + kind).value = txt;
  absorbGrabUrl(grabMarkUrl(txt));
  validatePastes();
  if (reveal) revealManual();
  if (note) setStatus($("analyze-status"), note);
}

async function tryAutoFill() {
  // Only read the clipboard once the user is mid-flow (clicked an Open ↗
  // link or confirmed an address) — never on a cold first visit.
  if (document.hidden || (!lastOpened && !state.address)) return;
  if (!(navigator.clipboard && navigator.clipboard.readText)) return;
  let txt;
  try { txt = await navigator.clipboard.readText(); } catch { return; }
  if (!txt || txt === lastClip || txt.trim().length < 300 || pastedUrl(txt)) return;
  const kind = classifyPaste(txt) || lastOpened;
  if (!kind) return;
  const box = $("paste-" + kind);
  lastClip = txt;
  if (box.value === txt) return;
  if (box.value.trim() &&
      !confirm(`Replace what's in the "${kind}" box with the page you just copied?`)) return;
  fillBox(kind, txt, `Auto-filled the ${kind} box from your clipboard ✓ — ` +
    (kind === "listing" ? "hit Analyze, or add the optional pages first."
                        : "copy the next page, or hit Analyze."), true);
}

async function clipButton(kind) {
  try {
    const txt = await navigator.clipboard.readText();
    if (!txt || !txt.trim()) throw new Error("empty");
    lastClip = txt;
    fillBox(kind, txt);
  } catch {
    setPasteState("state-" + kind, "warn",
      "⚠ Clipboard access was blocked — click the box and press Ctrl+V instead.");
  }
}

function refreshAttention() {
  // Pulse an enabled Open ↗ button while its paste box is still empty.
  for (const [openId, boxId] of [["open-listing", "paste-listing"],
                                 ["open-market", "paste-market"],
                                 ["open-sold", "paste-sold"]]) {
    const btn = $(openId);
    const enabled = !btn.hasAttribute("aria-disabled");
    btn.classList.toggle("attn", enabled && !$(boxId).value.trim());
  }
}

function validatePastes() {
  validateListing();
  validateMarket();
  validateSold();
  refreshAttention();
}

// ---------- step 3: analyze + render ----------

function currentAddress(listingText) {
  if (state.address) return state.address;
  const typed = $("addr-input").value.trim();
  if (typed) return typed;
  const first = (listingText.split("\n").find(l => l.trim()) || "").trim();
  return first.slice(0, 90) || "unknown address";
}

function currentZip(address) {
  if (state.zip) return state.zip;
  const m = address.match(/\b(\d{5})(?:-\d{4})?\s*$/);
  return m ? m[1] : null;
}

function analyze() {
  const listingText = $("paste-listing").value;
  const status = $("analyze-status");
  validatePastes();
  if (!listingText.trim()) {
    setStatus(status, "Paste the listing page's text first (box 1 above).", true);
    return;
  }
  if (pastedUrl(listingText)) {
    setStatus(status,
      "Box 1 contains a link, not the page's text. Click Open listing ↗, and " +
      "on the page that opens press Ctrl+A then Ctrl+C, then paste that here. " +
      "This page can't download the link for you — browsers forbid it.", true);
    return;
  }
  const z = parseZillowText(listingText);
  if (!z.price && !parsePriceHistoryBlock(listingText).length) {
    setStatus(status,
      "That text doesn't look like a listing page (no price or price history " +
      "found), so the report would be empty. See the note under box 1.", true);
    return;
  }
  const address = currentAddress(listingText);
  const result = analyzeAll({
    address,
    listingText,
    marketText: $("paste-market").value,
    soldText: $("paste-sold").value,
    zipCode: currentZip(address),
    countySections: state.countySections,
    countyName: state.countyMeta && state.countyMeta.name,
    countyState: state.countyMeta && state.countyMeta.state,
  });
  setStatus(status, "");
  renderReport(result);
  window.lastResult = result;
  $("panel-report").classList.remove("hidden");
  $("panel-report").scrollIntoView({ behavior: "smooth" });
}

function tableHtml(headers, rows) {
  return `<table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}` +
    `</tr></thead><tbody>${rows.map(r =>
      `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function renderReport(r) {
  const z = r.zillow, a = r.analysis, H = [];
  H.push(`<h1>Due Diligence Report: ${esc(r.address)}</h1>`);
  H.push(`<p class="genline">Generated ${r.generated} in your browser — listing-only mode. ` +
    `The <a href="https://github.com/Reggie-Reuss/house-recon">CLI</a> adds county ` +
    `records, photos, maps, and the optional AI judgment layer.</p>`);

  const snap = [
    ["Asking price", fmtMoney(z.price)],
    ["Beds / Baths", esc(`${z.beds || "n/a"} / ${z.baths || "n/a"}`)],
    ["Square feet", esc(z.sqft || "n/a")],
    ["Year built", esc(z.year_built || "n/a")],
    ["Lot", z.lot_acres ? esc(z.lot_acres) + " acres" : "n/a"],
    ["$ / sqft", z.price_per_sqft ? "$" + esc(z.price_per_sqft) : "n/a"],
    ["MLS #", esc(z.mls || "n/a")],
    ["Date on market", esc(z.date_on_market || "n/a")],
    ["Tax assessed value", fmtMoney(z.tax_assessed)],
    ["Annual tax (listed)", fmtMoney(z.annual_tax)],
  ];
  const naCount = snap.filter(([, v]) => String(v).includes("n/a")).length;
  if (naCount >= 6) {
    H.push(`<div class="flag">⚠ ${naCount} of ${snap.length} listing fields ` +
      `could not be parsed from the pasted text — the copy probably caught ` +
      `only part of the page. Go back to the listing, scroll it to the ` +
      `bottom once (Zillow loads sections as you scroll), press ` +
      `Ctrl+A then Ctrl+C, and analyze again.</div>`);
  }
  H.push("<h2>Snapshot</h2>");
  H.push(tableHtml(["Field", "Value"], snap));

  H.push("<h2>Dated History Timeline</h2>");
  H.push(tableHtml(["Date", "Event", "Price", "Source"],
    a.timeline.length
      ? a.timeline.map(ev => [esc(ev.date), esc(ev.event),
          ev.price ? fmtMoney(ev.price) : "", esc(ev.source || "")])
      : [["n/a", "no events captured", "", ""]]));
  const cuts = a.price_cuts || {};
  H.push("<ul>");
  H.push(`<li>Cumulative days on market: <b>${a.cumulative_market_days}</b></li>`);
  H.push(`<li>Re-lists after first listing: <b>${a.relist_count}</b></li>`);
  H.push(`<li>Price cuts: ${cuts.cuts && cuts.cuts.length
    ? esc(cuts.cuts.map(c => `${c.date}: ${fmtMoney(c.from)} → ${fmtMoney(c.to)}`).join("; ")) +
      ` (total <b>${fmtMoney(cuts.total_cut)}</b>, ${cuts.pct_from_peak}% off peak)`
    : "none detected"}</li>`);
  H.push(`<li>Failed pendings: <b>${a.failed_pendings}</b></li>`);
  H.push(`<li>Flip indicator: ${a.flip_flag ? "<b>" + esc(a.flip_flag) + "</b>" : "none"}</li>`);
  H.push("</ul>");

  H.push("<h2>Red Flags</h2>");
  H.push(a.red_flags.length
    ? a.red_flags.map(f => `<div class="flag">${esc(f)}</div>`).join("")
    : `<div class="okflag">None detected automatically.</div>`);

  H.push("<h2>Offer Strategy</h2>");
  if (r.market) {
    const bits = [];
    if (r.market.label) bits.push(esc(r.market.label));
    if (r.market.sale_to_list_pct) bits.push(`median sale-to-list ${r.market.sale_to_list_pct}%`);
    if (r.market.median_dom) bits.push(`median ${r.market.median_dom} days on market`);
    if (r.market.median_sale_price) bits.push(`median sale ${fmtMoney(r.market.median_sale_price)}`);
    H.push(`<p><b>Zip market temperature:</b> ${bits.join("; ")}</p>`);
  }
  const est = a.comp_value_estimate;
  if (est) {
    const gapTxt = a.value_gap_pct !== null
      ? `; ask is ${a.value_gap_pct >= 0 ? "+" : ""}${a.value_gap_pct}% vs this estimate` : "";
    H.push(`<p><b>Sold-comps value estimate:</b> ${fmtMoney(est.value)} ` +
      `($${est.ppsf}/sqft median of ${est.basis === "similar" ? est.n_similar : est.n_all} ` +
      `${est.basis === "similar" ? "similar-size " : ""}sold comps)${gapTxt}</p>`);
  }
  if (a.offer_ladder.length) {
    H.push(`<p><b>Seller-motivation score:</b> ${a.motivation.score}/100 ` +
      `<span class="caveat">(listing signals only — ${esc(a.motivation.partial)})</span></p>`);
    if (a.motivation.reasons.length) {
      H.push("<ul>" + a.motivation.reasons.map(x => `<li>${esc(x)}</li>`).join("") + "</ul>");
    }
    H.push(`<p><b>Ladder anchoring (${esc(a.ladder_anchoring.mode)}):</b></p>`);
    H.push("<ul>" + a.ladder_anchoring.notes.map(n => `<li>${esc(n)}</li>`).join("") + "</ul>");
    H.push(tableHtml(["Offer", "% of list", "Est. acceptance odds", "Expected savings"],
      a.offer_ladder.map(rung => [fmtMoney(rung.offer), rung.pct_of_list + "%",
        "~" + rung.acceptance_probability_pct + "%", fmtMoney(rung.expected_savings)])));
    H.push(`<p class="caveat">Calibrated estimates from public listing signals — ` +
      `not a fitted statistical model (<a href="https://github.com/Reggie-Reuss/house-recon/blob/main/VALIDATION.md">validation</a>). ` +
      `Refine with your agent.</p>`);
  } else {
    H.push(`<p class="caveat">(no list price captured — offer ladder unavailable)</p>`);
  }

  if (z.tax_history.length) {
    H.push("<h2>Listing Tax History</h2>");
    H.push(tableHtml(["Year", "Taxes", "Change", "Assessment"],
      z.tax_history.map(t => [t.year, fmtMoney(t.taxes),
        t.change_pct !== null ? t.change_pct + "%" : "", fmtMoney(t.assessment)])));
  }
  if (r.comps && r.comps.rows.length) {
    H.push(`<h2>Sold Comps (${r.comps.n} parsed)</h2>`);
    H.push(tableHtml(["Price", "Beds", "Sqft", "$/sqft", "Address"],
      r.comps.rows.slice(0, 15).map(c => [fmtMoney(c.price), c.beds ?? "",
        c.sqft ? c.sqft.toLocaleString("en-US") : "n/a",
        c.ppsf ? "$" + c.ppsf : "n/a", esc(c.address)])));
  }

  if (r.county) {
    const c = r.county;
    H.push(`<h2>County Record — ${esc(c.county)}</h2>`);
    H.push(tableHtml(["Field", "Value"], [
      ["Owner", esc(c.owner || "n/a")],
      ["Parcel", esc(c.parcel_id || "n/a")],
      ["Land use", esc(c.land_use || "n/a")],
      ["Acres", esc(c.acres || "n/a")],
      ["Taxing district", esc(c.district || "n/a")],
      ["Current annual tax", fmtMoney(c.tax_total)],
      ["Effective rate", c.effective_rate != null
        ? esc(c.effective_rate + " mills") : "n/a"],
      ["Permits on file", String(c.permits_count)],
    ]));
    if (c.transfers.length) {
      H.push("<h3>Transfers</h3>");
      H.push(tableHtml(["Date", "Amount"], c.transfers.map(t =>
        [esc(t.date), t.amount ? fmtMoney(t.amount) : "(no consideration)"])));
    }
    if (c.values.length) {
      H.push("<h3>Value History (county)</h3>");
      H.push(tableHtml(["Year", "Appraised (100%)", "Assessed (35%)"],
        c.values.slice(0, 6).map(v =>
          [String(v.year), fmtMoney(v.appraised), fmtMoney(v.assessed)])));
    }
    if (a.tax_projection) {
      H.push(`<p><b>Ohio tax projection at asking price:</b> ` +
        `~${fmtMoney(a.tax_projection)}/yr after reappraisal to the sale price` +
        (c.tax_total ? ` (current: ${fmtMoney(c.tax_total)}/yr)` : "") +
        `</p>`);
    }
  }

  H.push("<h2>Data Gaps</h2>");
  H.push(r.gaps.length
    ? "<ul>" + r.gaps.map(g => `<li>${esc(g)}</li>`).join("") + "</ul>"
    : "<ul><li>None.</li></ul>");
  H.push(`<p class="caveat">Automated analysis of the page text you supplied; ` +
    `data may be stale, incomplete, or mis-parsed. Verify everything ` +
    `independently before any purchase decision. Not professional advice.</p>`);

  $("report").innerHTML = H.join("\n");
}

function downloadMd() {
  if (!window.lastResult) return;
  const md = buildReportMd(window.lastResult);
  const blob = new Blob([md], { type: "text/markdown" });
  const aEl = document.createElement("a");
  aEl.href = URL.createObjectURL(blob);
  aEl.download = slugify(window.lastResult.address || "house") + "-report.md";
  aEl.click();
  URL.revokeObjectURL(aEl.href);
}

function loadDemo() {
  $("addr-input").value = DEMO_ADDRESS;
  state.address = DEMO_ADDRESS;
  state.countySections = null;
  state.countyMeta = null;
  setStatus($("county-status"), "");
  applyZip(DEMO_ZIP);
  setOpenLink("open-listing",
    `https://www.zillow.com/homes/${slugify(DEMO_ADDRESS)}_rb/`);
  $("paste-listing").value = DEMO_ZILLOW_TEXT;
  $("paste-market").value = DEMO_MARKET_TEXT;
  $("paste-sold").value = DEMO_SOLD_TEXT;
  revealManual();
  validatePastes();
  setStatus($("analyze-status"),
    "Synthetic demo data loaded (fictional address — see examples/make_demo.py).");
  analyze();
}

// ---------- bookmarklet ----------

function installBookmarklet() {
  const a = $("bookmarklet");
  if (!a || typeof hrPageGrab !== "function") return;
  a.href = "javascript:(" + encodeURIComponent(hrPageGrab.toString()) + ")()";
  // Clicking it here would grab THIS page — it's meant for the bookmarks bar.
  a.addEventListener("click", e => {
    e.preventDefault();
    setStatus($("analyze-status"),
      "Drag the 🏠 Grab page link up to your bookmarks bar (Ctrl+Shift+B " +
      "shows the bar). Then on any listing page, click it once — come back " +
      "here and the box fills itself.");
  });
}

// ---------- companion extension (optional) ----------

const ext = { present: false, busy: false, ok: 0 };

function extUrls() {
  const urls = {};
  if (state.address) {
    urls.listing = `https://www.zillow.com/homes/${slugify(state.address)}_rb/`;
  }
  if (state.zip) {
    urls.market = marketUrl(state.zip);
    urls.sold = soldUrl(state.zip);
  }
  return urls;
}

function startAutoFetch() {
  if (!ext.present || ext.busy) return;
  const status = $("ext-status");
  const urls = extUrls();
  if (!urls.listing) {
    setStatus(status,
      "Do step 1 first — type the address and click Find address. Then I " +
      "know which pages to fetch.", true);
    return;
  }
  const jobs = Object.entries(urls).map(([kind, url]) => ({ kind, url }));
  ext.busy = true;
  ext.ok = 0;
  $("ext-fetch").disabled = true;
  setStatus(status, `Fetching ${jobs.length} page${jobs.length > 1 ? "s" : ""} ` +
    "in background tabs (your own browser session — expect 10–30 seconds)…");
  for (const j of jobs) setPasteState("state-" + j.kind, "warn", "⏳ Fetching…");
  window.postMessage({ type: "hr-fetch", jobs }, window.location.origin);
}

function handleExtMessage(d) {
  if (d.type === "hr-ext-hello") {
    if (ext.present) return;
    ext.present = true;
    const install = $("ext-install"), ready = $("ext-ready");
    if (install) install.classList.add("hidden");
    if (ready) ready.classList.remove("hidden");
    return;
  }
  if (d.type === "hr-fetch-status") {
    if (d.kind) setPasteState("state-" + d.kind, "warn", "⏳ " + d.msg);
    else setStatus($("ext-status"), d.msg);
    return;
  }
  if (d.type === "hr-fetch-result") {
    if (d.ok && d.text) {
      ext.ok += 1;
      lastClip = d.text;               // don't double-handle via auto-fill
      fillBox(d.kind, d.text);
    } else if (d.captcha) {
      setPasteState("state-" + d.kind, "err",
        "⚠ The site showed a human check — I've opened that tab for you. " +
        "Complete the check there (you're a real visitor, it passes), then " +
        "click Fetch pages again.");
    } else {
      revealManual();
      setPasteState("state-" + d.kind, "err",
        "✗ Couldn't fetch this page automatically (" + (d.error || "unknown") +
        "). Use Open ↗ and copy it by hand — analysis is unaffected.");
    }
    return;
  }
  if (d.type === "hr-fetch-done") {
    ext.busy = false;
    $("ext-fetch").disabled = false;
    if (ext.ok > 0 && $("paste-listing").value.trim()) {
      setStatus($("ext-status"),
        `Fetched ${ext.ok} page${ext.ok > 1 ? "s" : ""} ✓ — analyzing…`);
      analyze();
    } else if (ext.ok > 0) {
      setStatus($("ext-status"),
        `Fetched ${ext.ok} page(s) — the listing still needs a manual copy.`);
    } else {
      setStatus($("ext-status"),
        "Automatic fetch didn't get through — the manual Open ↗ + copy flow " +
        "below always works.", true);
    }
  }
}

window.addEventListener("message", e => {
  if (e.source !== window || !e.data || typeof e.data !== "object") return;
  if (typeof e.data.type !== "string" || !e.data.type.startsWith("hr-")) return;
  handleExtMessage(e.data);
});

const validateDebounced = debounce(validatePastes, 350);
$("addr-find").addEventListener("click", findAddress);
$("addr-input").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); findAddress(); }
});
$("analyze").addEventListener("click", analyze);
$("download-md").addEventListener("click", downloadMd);
$("load-demo").addEventListener("click", loadDemo);
for (const kind of ["listing", "market", "sold"]) {
  $("paste-" + kind).addEventListener("input", validateDebounced);
  $("open-" + kind).addEventListener("click", () => { lastOpened = kind; });
}
document.querySelectorAll(".clip").forEach(btn =>
  btn.addEventListener("click", () => clipButton(btn.dataset.target)));
window.addEventListener("focus", tryAutoFill);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) tryAutoFill();
});
$("ext-fetch").addEventListener("click", startAutoFetch);
installBookmarklet();
// If the extension's content script loaded before us, ask it to re-announce.
window.postMessage({ type: "hr-ext-ping" }, window.location.origin);
