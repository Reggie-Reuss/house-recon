# house-recon companion extension

A tiny browser extension (Chrome / Edge / Brave, Manifest V3) that removes the
copy-paste step from the [browser analyzer](https://reggie-reuss.github.io/house-recon/):
type an address, click **Fetch pages automatically**, and the extension visits
the listing, the zip's market page, and the sold-comps search **in your own
browser**, hands their text to the analyzer page, and the report renders.

## Why an extension (and not a server)

Listing sites aggressively block scraping servers, but they serve normal
visitors happily. The extension keeps the analyzer's core property — *you*
visit the pages, with your own browser, cookies, and IP — while automating the
"open, scroll, copy" busywork. Tabs open **in the foreground on purpose**: you
watch your browser do the visit (hidden tabs also break the sites' lazy-loaded
sections, like Zillow's price history). It is the CLI's headed-Chrome
philosophy, minus the CLI.

## Privacy

- Does nothing until the analyzer page asks it to fetch. No browsing history
  access, no tracking, no analytics, no network calls of its own.
- Page text goes straight from the visited tab to the analyzer page in your
  browser. Nothing is uploaded anywhere.
- Host permissions are limited to `zillow.com`, `redfin.com`, and
  `localhost` (for local development). The bridge runs only on the analyzer
  page itself.

## Install

Until the Chrome Web Store listing is up, load it unpacked (~2 minutes):

1. Download this repository ([ZIP](https://github.com/Reggie-Reuss/house-recon/archive/refs/heads/main.zip))
   and unzip it — or `git clone` it.
2. Open `chrome://extensions` (Edge: `edge://extensions`).
3. Turn on **Developer mode** (top-right toggle).
4. Click **Load unpacked** and select the `extension/` folder.
5. Open (or reload) the [analyzer page](https://reggie-reuss.github.io/house-recon/) —
   step 2 now shows **⚡ Extension detected** with a
   **Fetch pages automatically** button.

## Use

1. On the analyzer page, do step 1 (Find address).
2. Click **Fetch pages automatically**. Tabs will open, scroll themselves,
   and close — about 10–30 seconds for all three pages.
3. The report renders on its own when the listing parses.

If a site shows a human check ("Press & Hold"), the extension leaves that tab
open for you — complete the check (you're a real visitor, it passes), then
click **Fetch pages automatically** again.

## Known limitations

- The listing fetch uses Zillow's address search URL. If Zillow lands on a
  search-results page instead of the house's own page, the analyzer will say
  no price history was found — open the listing yourself and use the Grab page
  bookmarklet or Ctrl+A / Ctrl+C for that one box.
- Firefox needs an MV3 port (event page instead of service worker) — not done
  yet.

## Publishing to stores (maintainer notes)

- **Chrome Web Store**: one-time $5 developer fee → upload `house-recon-companion.zip`
  (built by `build.ps1` in this folder, or just zip the folder contents,
  manifest at the zip root). Category: Tools. Justify `tabs` + `scripting` +
  host permissions as "fetches real-estate pages the user explicitly requests,
  in their own session".
- **Edge Add-ons**: free developer registration, same zip.
- Once live, replace the "Install — takes 2 minutes" link in
  `docs/index.html` with the store URL.
