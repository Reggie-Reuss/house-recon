# Store submission — field-by-field

Same zip for both stores: `extension/house-recon-companion.zip`
(rebuild any time with `extension/build.ps1`).

Local assets (in `extension/store-assets/`, not committed):
- `screenshot-1-fetch.png`, `screenshot-2-report.png` — 1280×800, 24-bit PNG
- `store-logo-300.png` — Edge store logo (Chrome uses `icons/icon128.png`)

Shared values used by both stores:

| Value | Text |
|---|---|
| Website | https://reggie-reuss.github.io/house-recon/ |
| Support | https://github.com/Reggie-Reuss/house-recon/issues |
| Privacy policy | https://github.com/Reggie-Reuss/house-recon/blob/main/extension/PRIVACY.md |
| Summary | comes from the zip's manifest `description` — do not retype |

---

## CHROME (chrome.google.com/webstore/devconsole)

Item ID: `hfeipempelbcdegkngoibcbfihpnadfm` → listing URL after approval:
`https://chromewebstore.google.com/detail/hfeipempelbcdegkngoibcbfihpnadfm`

### Package tab
Upload the zip. Title + summary display from the manifest.

### Store listing tab
- **Description** → the [Description text](#description-text) below
- **Category** → Tools
- **Language** → English (United States)
- **Store icon** → `extension/icons/icon128.png`
- **Screenshots** → both 1280×800 PNGs
- Click **Save Draft** after uploads or they don't stick.

### Privacy tab
- **Single purpose:** Fetch the real-estate pages a user explicitly requests
  into the house-recon analyzer page running in their browser.
- **tabs justification:** Open the pages the user asked to fetch, bring the
  analyzer tab back into focus when done, and close the fetched tabs.
- **scripting justification:** Read the visible text of the requested pages
  after they finish loading (equivalent to the user selecting-all and
  copying).
- **Host permission justification:** Fetches the listing (zillow.com) and
  market/sold-comps pages (redfin.com) that the user explicitly requests,
  only when they click "Fetch pages automatically" on the analyzer page. The
  content script runs only on the analyzer page itself
  (reggie-reuss.github.io/house-recon) to relay the user's request and
  return the fetched page text. Nothing is transmitted off the user's
  machine.
- **Remote code:** No.
- **Data usage:** leave all nine collection checkboxes UNCHECKED (nothing is
  collected or transmitted), and CHECK all three certifications at the
  bottom.
- **Privacy policy URL** → shared value above.

### Account Settings (one-time)
Contact email must be set AND verified before publishing. The email is shown
publicly on the listing — use a project/personal address, not an employer
one.

### Distribution tab
Visibility: Public. Free. All regions.

Then **Submit for review** (top-right). Expect the "in-depth review due to
host permissions" notice — normal; adds days, needs nothing from you.

---

## EDGE (partner.microsoft.com/dashboard/microsoftedge)

"+ Create new extension" → work through the left-side steps, then Publish.
The listing URL after approval uses the **CRX ID** from the product
overview's Extension identity block:
`https://microsoftedge.microsoft.com/addons/detail/<crx-id>`

### Packages step
Upload the same zip.

### Availability step
- **Visibility:** Public
- **Markets:** all markets

### Properties step
- **Category:** Productivity
- **Support details → Website:** https://reggie-reuss.github.io/house-recon/
- **Support details → Support contact:**
  https://github.com/Reggie-Reuss/house-recon/issues
- **Mature content:** leave UNCHECKED
- **Privacy policy URL** (on this step in Edge) → shared value above

### Listings step (English (United States))
- **Display name:** house-recon companion
- **Description** → the [Description text](#description-text) below
- **Store logo** → `extension/store-assets/store-logo-300.png` (300×300)
- **Screenshots** → the same two 1280×800 PNGs
- **Short description / summary** (if shown) → the manifest sentence: One
  click fetches a home's listing, market, and sold-comps pages into the
  house-recon analyzer. Everything stays in your browser.
- **Search terms** — only in some Partner Center layouts; if the field isn't
  shown, skip it (the name + description already carry the keywords). If it
  is: real estate, home buying, zillow, due diligence, house, offer,
  property records

### Notes for certification (optional free-text box before Publish)
Paste: This extension acts only when the user clicks "Fetch pages
automatically" on https://reggie-reuss.github.io/house-recon/. It opens the
real-estate pages the user requested, reads their visible text, hands it to
that page in the user's browser, and closes the tabs. No data is collected
or transmitted anywhere. To test: load the analyzer page, click "Load demo
data" for an instant report, or type any US address, pick the match, and
click "Fetch pages automatically".

Then **Publish**. Edge review typically runs longer than Chrome's.

---

## Description text

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

---

## After approval

Send the store URLs over; the page's `#install-chrome` / `#install-edge`
buttons get flipped to them and pushed (Chrome's is already staged locally).
