/* house-recon companion — content-script bridge.
 *
 * Runs only on the analyzer page (see manifest matches). Relays fetch
 * requests from the page to the background service worker and streams
 * results back via window.postMessage. The page never talks to the
 * extension APIs directly, and this script accepts jobs only for the
 * sites the extension holds host permissions for.
 */

"use strict";

// Idempotency guard: this file can be injected twice (manifest match plus
// the on-install injection for already-open tabs) — a second copy would
// double-run every fetch job.
if (self.__hrBridgeLoaded) {
  throw new Error("house-recon bridge already loaded");
}
self.__hrBridgeLoaded = true;

const ORIGIN = window.location.origin;

const URL_ALLOWED =
  /^https:\/\/(www\.)?(zillow|redfin)\.com\//i;
const URL_ALLOWED_DEV =
  /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?\//i;

function post(msg) { window.postMessage(msg, ORIGIN); }

function announce() {
  post({ type: "hr-ext-hello", version: chrome.runtime.getManifest().version });
}

let running = false;

async function runJobs(jobs) {
  if (running) return;
  running = true;
  let ok = 0;
  let captchaSeen = false;
  try {
    for (const job of jobs) {
      if (!job || typeof job.url !== "string" || typeof job.kind !== "string" ||
          !/^(listing|market|sold)$/.test(job.kind)) continue;
      if (!URL_ALLOWED.test(job.url) && !URL_ALLOWED_DEV.test(job.url)) {
        post({ type: "hr-fetch-result", kind: job.kind, ok: false,
               error: "URL not allowed" });
        continue;
      }
      post({ type: "hr-fetch-status", kind: job.kind,
             msg: "Opening the page (you'll see the tab work — that's it fetching)…" });
      let res;
      try {
        res = await chrome.runtime.sendMessage({ cmd: "grab", url: job.url });
      } catch (err) {
        res = { ok: false, error: String((err && err.message) || err) };
      }
      if (!res) res = { ok: false, error: "no response from extension" };
      if (res.ok) ok += 1;
      if (res.captcha) captchaSeen = true;
      post({ type: "hr-fetch-result", kind: job.kind,
             ok: !!res.ok, text: res.text, captcha: !!res.captcha,
             error: res.error });
    }
  } finally {
    running = false;
    // Bring the analyzer tab back — unless a human check needs solving.
    if (!captchaSeen) {
      try { await chrome.runtime.sendMessage({ cmd: "focus-me" }); } catch {}
    }
    post({ type: "hr-fetch-done", ok });
  }
}

window.addEventListener("message", e => {
  if (e.source !== window || !e.data || typeof e.data !== "object") return;
  if (e.data.type === "hr-ext-ping") { announce(); return; }
  if (e.data.type === "hr-fetch" && Array.isArray(e.data.jobs)) {
    runJobs(e.data.jobs.slice(0, 5));
  }
});

document.addEventListener("DOMContentLoaded", announce);
announce();
