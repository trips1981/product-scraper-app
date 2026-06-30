"""
browser/js_payloads.py — JavaScript snippets injected into the browser.

These are preserved exactly from universal_scraper_improved.py.
"""

# ── Hover-click picker (automatic mode) ────────────────────────────────────────
HOVER_PICK_JS = """
(() => {
  window._allMatches = [];
  document.body.addEventListener("mouseover", e => {
    let a = e.target.closest("a[href]"); if (!a) return;
    a.style.outline = "2px solid red";
  }, true);
  document.body.addEventListener("mouseout", e => {
    let a = e.target.closest("a[href]"); if (!a) return;
    a.style.outline = "";
  }, true);
  document.body.addEventListener("click", e => {
    let a = e.target.closest("a[href]"); if (!a) return;
    e.preventDefault(); e.stopPropagation();
    let cls = a.className; let tag = a.tagName;
    let matches = [...document.querySelectorAll(tag)]
      .filter(x => x.className === cls && x.innerText.trim().length > 0);
    window._allMatches = matches.map(m => ({ name: m.innerText.trim(), link: m.href }));
    matches.forEach(m => m.style.outline = "2px solid orange");
    a.style.outline = "3px solid lime";
  }, true);
})();
"""

SCRAPE_JS        = "() => window._allMatches || []"

# ── Manual picker (user-driven mode) ───────────────────────────────────────────
MANUAL_PICK_JS = """
(() => {
  window._manualMatches = [];
  if (window._manualPickerActive) return;
  window._manualPickerActive = true;
  function getLink(el) {
    let a = el.closest("a[href]"); if (a) return a.href;
    let cur = el;
    while (cur) {
      if (cur.onclick && cur.onclick.toString().includes("location")) {
        let m = cur.onclick.toString().match(/location\\.href=['"]([^'"]+)/);
        if (m) return m[1];
      }
      cur = cur.parentElement;
    }
    cur = el;
    while (cur) {
      if (cur.dataset) {
        if (cur.dataset.href) return cur.dataset.href;
        if (cur.dataset.url)  return cur.dataset.url;
      }
      cur = cur.parentElement;
    }
    return "";
  }
  document.body.addEventListener("mouseover", e => {
    if (!window._manualPickerActive) return;
    e.target.style.outline = "2px solid #9333ea";
  }, true);
  document.body.addEventListener("mouseout", e => {
    if (!window._manualPickerActive) return;
    e.target.style.outline = "";
  }, true);
  document.body.addEventListener("click", e => {
    if (!window._manualPickerActive) return;
    e.preventDefault(); e.stopPropagation();
    let el = e.target; let tag = el.tagName; let cls = el.className;
    let matches = [...document.querySelectorAll(tag)]
      .filter(x => x.className === cls && x.innerText && x.innerText.trim().length > 2);
    window._manualMatches = matches.map(m => ({ name: m.innerText.trim(), link: getLink(m) }));
    matches.forEach(m => m.style.outline = "2px solid orange");
    el.style.outline = "3px solid lime";
  }, true);
})();
"""

MANUAL_SCRAPE_JS = "() => window._manualMatches || []"

RESET_MANUAL_JS  = "() => { window._manualMatches=[]; window._manualPickerActive=false; }"
