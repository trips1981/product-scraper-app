"""
scrapers/client_filter_scraper.py — Client-side filter listing scraper.
Ported from SmartManualApp.scrape_client_filter_listing() with improvements.
Handles: Shopify, Siemens, Adobe, Microsoft, Salesforce, Angular Material filters.
"""
import json
import logging
from scrapers.base import BaseScraper
from pagination.engine import PaginationEngine
from utils.helpers import dedup

logger = logging.getLogger(__name__)

LOAD_MORE_KWS = [
    "load more", "show more", "view more", "more products",
    "see more", "show 8 more", "show 12 more", "show 15 more",
    "show 20 more", "show 24 more", "load more products",
    "weitere laden", "mehr laden",
]

CARD_SCRAPE_JS = """
() => {
    function isVisible(el) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return false;
        const st = window.getComputedStyle(el);
        return st.display !== 'none' && st.visibility !== 'hidden'
               && parseFloat(st.opacity) > 0.01;
    }
    function headingInside(el) {
        const h = el.querySelector('h2') || el.querySelector('h3') ||
                  el.querySelector('h4') || el.querySelector('h5') ||
                  el.querySelector('h1') ||
                  el.querySelector('[class*="title"],[class*="name"],[class*="heading"],' +
                                   '[class*="Title"],[class*="Name"],[class*="Heading"]');
        if (!h) return "";
        const t = (h.innerText || h.textContent || "").trim().replace(/\\s+/g,' ');
        return (t.length >= 3 && t.length <= 200) ? t : "";
    }
    const seen = new Set(); const res = [];
    const curBase = location.href.split(/[#?]/)[0];
    const skipWords = [
        'privacy','cookie','terms','login','signup','register','cart','blog',
        'news','about','contact','javascript:','mailto:','tel:','company',
        'investor','leadership','press','career','jobs','whistleblow',
        'digital-id','corporate-information','global-locations','sitemap',
        'legal','facebook','twitter','linkedin','youtube','instagram','tiktok',
        'pinterest','x.com'
    ];
    const genericLabels = new Set([
        'read more','learn more','explore','view all','see all','get started',
        'sign up','log in','discover','find out more','more info','contact us',
        'back to homepage','support and community'
    ]);
    for (const a of document.querySelectorAll('a[href]')) {
        const href = a.href || ""; if (!href) continue;
        const hrefBase = href.split(/[#?]/)[0];
        if (hrefBase === curBase && href !== hrefBase) continue;
        if (href === location.href) continue;
        if (skipWords.some(s => href.toLowerCase().includes(s))) continue;
        if (!isVisible(a)) continue;
        if (a.closest('nav, header, footer, [role="navigation"], ' +
                       '[class*="footer"], [class*="header"], [class*="nav-"],' +
                       '[class*="-nav"], [id*="footer"], [id*="header"]')) continue;
        const card =
            a.closest('article') || a.closest('[class*="card"]') ||
            a.closest('[class*="Card"]') || a.closest('[class*="lb-card"]') ||
            a.closest('[class*="product"]') || a.closest('[class*="feature"]') ||
            a.closest('[class*="item"]') || a.closest('[class*="tile"]') ||
            a.closest('[class*="teaser"]') || a.closest('[class*="result"]') ||
            a.closest('[class*="entry"]') || a.closest('[class*="grid"] > div') ||
            a.closest('[class*="list"] > div');
        if (!card || !isVisible(card)) continue;
        let name = headingInside(card);
        if (!name) {
            const lt = (a.innerText || a.textContent || "").trim().replace(/\\s+/g,' ');
            if (lt.length >= 5 && lt.length <= 200 && !genericLabels.has(lt.toLowerCase()))
                name = lt;
        }
        if (!name) {
            const al = (a.getAttribute('aria-label')||"").trim();
            if (al.length >= 5 && al.length <= 200) name = al;
        }
        if (!name) continue;
        if (genericLabels.has(name.toLowerCase())) continue;
        const key = name + '|' + href;
        if (!seen.has(key)) { seen.add(key); res.push({name, link: href}); }
    }
    return res;
}
"""

FILTER_DISCOVER_JS = """
() => {
    const candidates = [...document.querySelectorAll(
        'button[aria-pressed], button[aria-selected], [role="tab"], [role="checkbox"], ' +
        'input[type="checkbox"] + label, label[for], ' +
        '[class*="filter"] button, [class*="filter"] label, [class*="filter"] a, ' +
        '[class*="tab"] button, [class*="tab"] a, ' +
        '[class*="category"] button, [class*="category"] a, ' +
        '[class*="chip"] button, [class*="chip"] a, button[class*="chip"], ' +
        '[class*="facet"] button, [class*="facet"] a, ' +
        '[class*="filter-tag"], [data-filter-value], [data-category], [data-filter], [data-tab]'
    )];
    const result = []; const seen = new Set();
    for (const el of candidates) {
        const label = (el.innerText || el.textContent || "").trim().replace(/\\s+/g,' ');
        if (label.length < 2 || label.length > 80) continue;
        if (el.closest('nav, header, footer, [role="navigation"]')) continue;
        const low = label.toLowerCase();
        if (['clear all','clear','reset','close','cancel','select all',
             'deselect all','show all','all products'].some(s => low === s)) continue;
        if (el.tagName === 'A') {
            const href = el.href || "";
            if (href) {
                try {
                    const u = new URL(href);
                    if (u.origin !== location.origin) continue;
                    const curDepth = location.pathname.replace(/\\/+$/,'').split('/').length;
                    const hDepth   = u.pathname.replace(/\\/+$/,'').split('/').length;
                    if (hDepth > curDepth + 1) continue;
                } catch(_) { continue; }
            }
        }
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        const dedup_key = label.toLowerCase();
        if (seen.has(dedup_key)) continue;
        seen.add(dedup_key);
        result.push({label, tag: el.tagName, index: result.length});
    }
    return result;
}
"""

CLEAR_ALL_JS = """
() => {
    const labels = ['clear all','clear','reset filters','all','show all',
                    'all products','alle','tout','todos'];
    for (const btn of document.querySelectorAll('button, [role="button"], a, label')) {
        const t = (btn.innerText || btn.textContent || "").trim().toLowerCase();
        if (labels.includes(t)) { btn.click(); return true; }
    }
    return false;
}
"""


class ClientFilterScraper(BaseScraper):
    """
    Scrapes filter-based product listings (Shopify, Siemens, Angular Material, etc.)
    by clicking each filter, collecting products, then resetting.
    """

    def __init__(self):
        self._pagination = PaginationEngine()

    def scrape(self, page, url: str) -> list[dict]:
        logger.info(f"[client_filter] Starting: {url}")
        collected: list[dict] = []

        # Initial page prep
        page.wait_for_timeout(2_000)
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1_500)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)

        # Initial scrape (unfiltered)
        initial = self._scrape_all_pages(page)
        collected.extend(initial)
        logger.debug(f"[client_filter] Initial: {len(initial)} items")

        # Discover filters
        filter_controls = page.evaluate(FILTER_DISCOVER_JS) or []
        logger.debug(f"[client_filter] Filters found: {[f['label'] for f in filter_controls]}")

        if not filter_controls:
            result = dedup(collected)
            logger.info(f"[client_filter] No filters — done: {len(result)} items")
            return result

        # Click each filter, scrape, reset
        for idx, fc in enumerate(filter_controls):
            label = fc["label"]
            try:
                items = self._scrape_after_filter(page, label, idx, len(filter_controls))
                collected.extend(items)
            except Exception as exc:
                logger.debug(f"[client_filter] Filter '{label}' error: {exc}")
                continue

            # Reset to unfiltered state
            try:
                cleared = page.evaluate(CLEAR_ALL_JS)
                if not cleared:
                    # Toggle off by clicking the same filter again
                    page.evaluate(f"""
                    () => {{
                        const label = {json.dumps(label)};
                        for (const el of document.querySelectorAll(
                                'button[aria-pressed], button[aria-selected], [role="tab"], ' +
                                '[class*="filter"] button, [class*="chip"] button, button')) {{
                            const t = (el.innerText||el.textContent||"").trim().replace(/\\s+/g,' ');
                            if (t === label) {{ el.click(); return; }}
                        }}
                    }}
                    """)
                page.wait_for_timeout(800)
            except Exception:
                pass

        result = dedup(collected)
        logger.info(f"[client_filter] Done: {len(result)} items")
        return result

    def _scrape_all_pages(self, page) -> list[dict]:
        """Scrape current DOM + exhaust load-more + numeric pagination."""
        items = page.evaluate(CARD_SCRAPE_JS) or []
        logger.debug(f"[client_filter]   visible: {len(items)}")

        # Load-more rounds
        no_new = 0
        prev_n = len(items)
        kws_json = json.dumps(LOAD_MORE_KWS)

        for _ in range(300):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)

            clicked = page.evaluate(f"""
            () => {{
                const kws = {kws_json};
                // Regex catches "Show N more", "Load N more" etc. with any number
                const dynRe = /^(show|load|see|view)\\s+\\d+\\s+more/i;
                for (const el of document.querySelectorAll('button, a, [role="button"]')) {{
                    if (!el.offsetParent) continue;
                    const t = (el.innerText||el.textContent||"").trim().replace(/\\s+/g,' ');
                    const tl = t.toLowerCase();
                    if (kws.some(k => tl === k || tl.startsWith(k)) || dynRe.test(t)) {{
                        el.scrollIntoView({{block:'center',behavior:'instant'}});
                        el.click(); return true;
                    }}
                }}
                return false;
            }}
            """)
            if not clicked:
                break

            page.wait_for_timeout(1_800)
            try:
                page.wait_for_load_state("networkidle", timeout=6_000)
            except Exception:
                pass
            page.wait_for_timeout(500)

            new_items = page.evaluate(CARD_SCRAPE_JS) or []
            new_n = len(new_items)
            if new_n > prev_n:
                items    = new_items
                prev_n   = new_n
                no_new   = 0
            else:
                no_new += 1
                if no_new >= 3:
                    break

        # Numeric pages
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        page_nums = page.evaluate("""
        () => {
            const nums = new Set();
            for (const a of document.querySelectorAll('a, button')) {
                const t = (a.innerText||a.textContent||"").trim();
                if (/^[0-9]+$/.test(t)) {
                    const n = parseInt(t);
                    if (n > 1 && n < 500) nums.add(n);
                }
            }
            return [...nums].sort((a,b) => a-b);
        }
        """) or []

        visited = {1}
        for pnum in page_nums:
            if pnum in visited:
                continue
            visited.add(pnum)
            clicked = page.evaluate(f"""
            () => {{
                for (const a of document.querySelectorAll('a, button')) {{
                    const t = (a.innerText||a.textContent||"").trim();
                    if (t === '{pnum}') {{
                        a.scrollIntoView({{block:'center',behavior:'instant'}}); a.click(); return true;
                    }}
                }}
                return false;
            }}
            """)
            if not clicked:
                continue
            page.wait_for_timeout(2_000)
            try:
                page.wait_for_load_state("networkidle", timeout=6_000)
            except Exception:
                pass
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(300)
            p_items = page.evaluate(CARD_SCRAPE_JS) or []
            items.extend(p_items)

        return items

    def _scrape_after_filter(self, page, label: str, idx: int, total: int) -> list[dict]:
        logger.debug(f"[client_filter] Filter {idx+1}/{total}: '{label}'")

        clicked = page.evaluate(f"""
        () => {{
            const label = {json.dumps(label)};
            const SELECTORS = [
                'button[aria-pressed], button[aria-selected]',
                '[role="tab"], [role="checkbox"]',
                'input[type="checkbox"] + label, label[for]',
                '[class*="filter"] button, [class*="filter"] label, [class*="filter"] a',
                '[class*="tab"] button, [class*="tab"] a',
                '[class*="category"] button, [class*="category"] a',
                '[class*="chip"] button, [class*="chip"] a, button[class*="chip"]',
                '[class*="facet"] button, [class*="facet"] a',
                '[class*="filter-tag"], [data-filter-value], [data-category]',
                'button, [role="button"]'
            ].join(', ');
            for (const el of document.querySelectorAll(SELECTORS)) {{
                const t = (el.innerText||el.textContent||"").trim().replace(/\\s+/g,' ');
                if (t === label) {{
                    el.scrollIntoView({{block:"center",behavior:"instant"}});
                    el.click(); return true;
                }}
            }}
            return false;
        }}
        """)

        if not clicked:
            logger.debug(f"[client_filter] Could not click filter: '{label}'")
            return []

        page.wait_for_timeout(2_500)
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        page.wait_for_timeout(800)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1_000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)

        return self._scrape_all_pages(page)
