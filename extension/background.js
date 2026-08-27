/* house-recon companion — background service worker.
 *
 * Grabs a page the same way a person does: open a real tab, let it render,
 * poll until its content is actually there, take document.body.innerText,
 * close the tab. Tabs load in a separate minimized window so the user isn't
 * yanked around; the readiness polling is wall-clock based, so background
 * timer throttling only slows the polls, not the result. The window comes
 * to the front only when a page needs a human (captcha).
 */

"use strict";

const GRAB_MARK = "[house-recon page grab] url: ";
const LOAD_TIMEOUT_MS = 35000;
const SETTLE_MS = 1200;

function waitForComplete(tabId) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error("page load timed out"));
    }, LOAD_TIMEOUT_MS);
    function listener(id, info) {
      if (id === tabId && info.status === "complete") { cleanup(); resolve(); }
    }
    function cleanup() {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
    }
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then(t => {
      if (t.status === "complete") { cleanup(); resolve(); }
    }).catch(() => {});
  });
}

// Injected into the visited page — must be fully self-contained.
// Same readiness/scroll logic as docs/bookmarklet.js hrPageGrab(); keep in
// sync. Poll for the Price history rows on listing pages (Zillow renders
// them ~2-3s after load, no scroll needed), drive the page's real scroll
// container (Zillow scrolls an inner element, not the window), and elsewhere
// stop once the text stops growing.
function pageGrabber(mark) {
  return new Promise(resolve => {
    const BUDGET_MS = 20000; // throttled background tabs poll at >=1s
    const CAPTCHA_RX = new RegExp(
      "press & hold|press and hold|px-captcha|perimeterx|" +
      "access to this page has been denied|are you a human|" +
      "verify you are a human|unusual activity", "i");
    const t0 = Date.now();
    const txt = () => (document.body ? document.body.innerText : "");
    const isCaptcha = t => CAPTCHA_RX.test(t.slice(0, 4000)) ||
      !!document.querySelector("#px-captcha");
    function ready(t) {
      if (location.href.indexOf("homedetails") === -1) return null;
      const i = t.indexOf("Price history");
      return i === -1 ? false
        : /\d{1,2}\/\d{1,2}\/\d{4}/.test(t.slice(i, i + 8000));
    }
    let root = document.scrollingElement || document.documentElement;
    if (root.scrollHeight < window.innerHeight + 200) {
      let best = null;
      for (const d of document.querySelectorAll("div,main,section")) {
        if (d.clientHeight > 200 && d.scrollHeight > d.clientHeight + 500 &&
            (!best || d.scrollHeight > best.scrollHeight)) best = d;
      }
      if (best) root = best;
    }
    let lastLen = 0, stable = 0;
    function finish(t, captcha) {
      root.scrollTop = 0;
      window.scrollTo(0, 0);
      resolve({
        text: mark + location.href + "\n" + t,
        captcha,
        chars: t.length,
      });
    }
    function step() {
      root.scrollTop += Math.max(700, window.innerHeight * 0.8);
      window.scrollBy(0, Math.max(700, window.innerHeight * 0.8));
      const t = txt();
      if (isCaptcha(t)) { finish(t, true); return; }
      const r = ready(t);
      if (t.length > lastLen) { lastLen = t.length; stable = 0; } else { stable += 1; }
      const bottom = root.scrollTop + root.clientHeight >= root.scrollHeight - 80;
      const done = r === true || (Date.now() - t0) >= BUDGET_MS ||
        (bottom && stable >= (r === false ? 8 : 3));
      if (!done) { setTimeout(step, 400); return; }
      finish(t, false);
    }
    step();
  });
}

// Pages load in a separate MINIMIZED window so fetching doesn't yank the
// user around (true headless isn't possible for extensions). The grabber
// polls on wall-clock time, so background-tab timer throttling (>=1s) only
// slows the polls, not the outcome. The window pops to the front only when
// a page needs a human (captcha).
let fetchWindowId = null;

// Content scripts don't auto-inject into tabs that were open before the
// extension was installed — inject the bridge into any open analyzer tabs
// so the Fetch button appears without a refresh. content.js guards against
// double-injection.
chrome.runtime.onInstalled.addListener(async () => {
  try {
    const tabs = await chrome.tabs.query(
      { url: "https://reggie-reuss.github.io/house-recon/*" });
    for (const t of tabs) {
      try {
        await chrome.scripting.executeScript(
          { target: { tabId: t.id }, files: ["content.js"] });
      } catch {}
    }
  } catch {}
});

async function fetchTab(url) {
  if (fetchWindowId !== null) {
    try {
      return await chrome.tabs.create({ windowId: fetchWindowId, url,
                                        active: false });
    } catch { fetchWindowId = null; } // user closed the window mid-run
  }
  try {
    const win = await chrome.windows.create({ url, state: "minimized",
                                              focused: false });
    fetchWindowId = win.id;
    return win.tabs[0];
  } catch { /* some environments reject minimized creation */ }
  try {
    const win = await chrome.windows.create({ url, focused: false });
    fetchWindowId = win.id;
    return win.tabs[0];
  } catch { /* fall back to a tab in the current window */ }
  return chrome.tabs.create({ url, active: false });
}

async function closeFetchWindow() {
  if (fetchWindowId === null) return;
  const id = fetchWindowId;
  fetchWindowId = null;
  try { await chrome.windows.remove(id); } catch {}
}

async function grab(url, openerTabId) {
  let tab = null;
  try {
    tab = await fetchTab(url);
    await waitForComplete(tab.id);
    await new Promise(r => setTimeout(r, SETTLE_MS));
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: pageGrabber,
      args: [GRAB_MARK],
    });
    const r = results && results[0] && results[0].result;
    if (!r) throw new Error("could not read the page");
    if (r.captcha) {
      // Bring the window forward and leave the tab for the human check.
      try {
        await chrome.windows.update(tab.windowId,
                                    { state: "normal", focused: true });
        await chrome.tabs.update(tab.id, { active: true });
      } catch {}
      fetchWindowId = null; // don't close it under the user later
      tab = null;
      return { ok: false, captcha: true };
    }
    if (r.chars < 500) {
      throw new Error("page text too short (" + r.chars +
        " chars) — it may not have loaded fully");
    }
    const text = r.text;
    await chrome.tabs.remove(tab.id);
    tab = null;
    return { ok: true, text };
  } catch (err) {
    if (tab) { try { await chrome.tabs.remove(tab.id); } catch {} }
    return { ok: false, error: String((err && err.message) || err) };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !sender.tab) return;
  if (msg.cmd === "grab" && typeof msg.url === "string") {
    grab(msg.url, sender.tab.id).then(sendResponse);
    return true; // keep the channel open for the async response
  }
  if (msg.cmd === "focus-me") {
    closeFetchWindow()
      .then(() => chrome.tabs.update(sender.tab.id, { active: true }))
      .then(() => chrome.windows.update(sender.tab.windowId, { focused: true }))
      .then(() => sendResponse({ ok: true }), () => sendResponse({ ok: false }));
    return true;
  }
});
