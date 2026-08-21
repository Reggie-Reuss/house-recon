# Store listing copy (Chrome Web Store / Edge Add-ons)

Copy-paste for the developer consoles. Screenshots: generate with
`store-assets/` (1280×800, not committed) — or retake any time; they show
only the analyzer page with synthetic demo data.

## Basics

- **Name:** house-recon companion
- **Summary (≤132 chars):**
  One click fetches a home's listing, market, and sold-comps pages into the
  house-recon analyzer. Everything stays in your browser.
- **Category:** Tools (Chrome) / Productivity → Tools (Edge)
- **Language:** English (United States)
- **Website / support:** https://github.com/Reggie-Reuss/house-recon
- **Privacy policy URL:**
  https://github.com/Reggie-Reuss/house-recon/blob/main/extension/PRIVACY.md

## Description

house-recon is a free, open-source due-diligence analyzer for home buyers:
point it at a house and get a dated history timeline, automated red flags,
county records where supported, a seller-motivation score, and an offer
ladder with estimated acceptance odds — computed entirely in your browser.

This companion extension removes the copy-paste step. On the analyzer page
(reggie-reuss.github.io/house-recon), pick an address and click "Fetch pages
automatically": the extension opens the house's listing page, your zip's
market page, and the recent-sold search in tabs — in your own browser, like
any shopper — reads their text once they load, hands it to the analyzer, and
closes the tabs. The report renders itself.

Privacy: the extension does nothing until you click Fetch, collects nothing,
and sends nothing anywhere — page text goes straight to the analyzer page in
your browser. No accounts, no analytics, no remote code. Source:
https://github.com/Reggie-Reuss/house-recon

## Privacy tab answers

- **Single purpose:** Fetch the real-estate pages a user explicitly requests
  into the house-recon analyzer page running in their browser.
- **Permission justifications:**
  - `tabs` — open the pages the user asked to fetch, bring the analyzer tab
    back into focus when done, and close the fetched tabs.
  - `scripting` — read the visible text of the requested pages after they
    finish loading (equivalent to the user selecting-all and copying).
  - Host `zillow.com` / `redfin.com` — the listing, market, and sold-comps
    pages the analyzer works from; accessed only on the user's click.
  - Content script on `reggie-reuss.github.io/house-recon/*` — the analyzer
    page itself; relays the user's fetch request and returns the page text.
- **Remote code:** none.
- **Data collection:** none (check "does not collect user data" for every
  category).

## After approval

Replace both install-button hrefs in `docs/index.html` (`#install-chrome`,
`#install-edge`) with the store URLs and push.
