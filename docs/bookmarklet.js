/* house-recon "Grab page" bookmarklet — readable source.
 *
 * app.js turns hrPageGrab.toString() into the javascript: href users drag to
 * their bookmarks bar, so this file is the single source of truth. The
 * extension's background.js pageGrabber() uses the same core logic — keep
 * them in sync when changing readiness/scroll behavior.
 *
 * Why it looks this way (learned on real Zillow pages):
 * - Zillow renders Price history 2-3s AFTER load via its own fetch, no
 *   scrolling required — so we POLL for the section's rows instead of
 *   scrolling on a timer and hoping.
 * - Zillow scrolls in an inner container, not the window, so window.scrollBy
 *   is a no-op there — we find the tallest scrollable element and drive that.
 * - The clipboard write needs the click's user activation (~5s); if content
 *   arrives too late the text is cached and a second click copies instantly.
 */

"use strict";

function hrPageGrab() {
  var MARK = "[house-recon page grab] url: ";
  var BUDGET_MS = 7000;
  function toast(msg, isErr) {
    var old = document.getElementById("hr-grab-toast");
    if (old) old.remove();
    var el = document.createElement("div");
    el.id = "hr-grab-toast";
    el.textContent = msg;
    el.style.cssText = "position:fixed;top:16px;left:50%;transform:translateX(-50%);" +
      "z-index:2147483647;max-width:min(560px,92vw);padding:12px 18px;" +
      "border-radius:10px;font:600 14px/1.45 system-ui,sans-serif;" +
      "box-shadow:0 6px 24px rgba(0,0,0,.35);white-space:pre-line;" +
      (isErr ? "background:#b3402f;color:#fff;" : "background:#0e7a6c;color:#fff;");
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 8000);
  }
  function copy(text) {
    function legacy() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      ta.remove();
      return ok;
    }
    function done(ok) {
      if (ok) {
        window.__hrGrabText = null;
        toast("✓ Page copied (" + Math.round(text.length / 1000) +
          "k chars).\nSwitch back to the house-recon tab — the box fills itself.");
      } else {
        window.__hrGrabText = text;
        toast("Page is ready — click the Grab page bookmark once more to copy.",
          true);
      }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { done(true); },
        function () { done(legacy()); });
    } else {
      done(legacy());
    }
  }
  // Second click after a blocked write: copy immediately, activation is fresh.
  if (window.__hrGrabText) { copy(window.__hrGrabText); return; }

  var t0 = Date.now();
  function txt() { return document.body ? document.body.innerText : ""; }
  // On a listing page, "done" means the Price history rows exist; elsewhere
  // we can't know what to expect, so "done" means the text stopped growing.
  function ready(t) {
    if (location.href.indexOf("homedetails") === -1) return null;
    var i = t.indexOf("Price history");
    return i === -1 ? false : /\d{1,2}\/\d{1,2}\/\d{4}/.test(t.slice(i, i + 8000));
  }
  // The page may scroll in an inner container (Zillow does) — find it.
  var root = document.scrollingElement || document.documentElement;
  if (root.scrollHeight < window.innerHeight + 200) {
    var els = document.querySelectorAll("div,main,section");
    var best = null;
    for (var k = 0; k < els.length; k++) {
      var d = els[k];
      if (d.clientHeight > 200 && d.scrollHeight > d.clientHeight + 500 &&
          (!best || d.scrollHeight > best.scrollHeight)) best = d;
    }
    if (best) root = best;
  }
  var y0 = root.scrollTop, lastLen = 0, stable = 0;
  toast("Grabbing page… (waiting for every section to load)");
  function step() {
    root.scrollTop += Math.max(700, window.innerHeight * 0.8);
    window.scrollBy(0, Math.max(700, window.innerHeight * 0.8));
    var t = txt();
    var r = ready(t);
    if (t.length > lastLen) { lastLen = t.length; stable = 0; } else { stable += 1; }
    var bottom = root.scrollTop + root.clientHeight >= root.scrollHeight - 80;
    var done = r === true || (Date.now() - t0) >= BUDGET_MS ||
      (bottom && stable >= (r === false ? 6 : 3));
    if (!done) { setTimeout(step, 400); return; }
    root.scrollTop = y0;
    window.scrollTo(0, 0);
    copy(MARK + location.href + "\n" + txt());
  }
  step();
}
