"""
scrapers/dom_scraper.py — DOM-based product scraper.
Ported directly from SmartManualApp.scrape() in universal_scraper_improved.py.
"""
import logging
from scrapers.base import BaseScraper
from pagination.engine import PaginationEngine
from utils.helpers import dedup

logger = logging.getLogger(__name__)


class DOMScraper(BaseScraper):
    """
    Generic DOM scraper.  Iterates pages via Next button, collecting
    all anchor elements that are co-located with a heading inside a card.
    """

    SCRAPE_JS = """
    () => {
        const SKIP  = ["#","privacy","cookie","terms","login","javascript:","mailto:","tel:"];
        const HINTS = ["product","solution","software","platform"];
        const ptxt  = document.body.innerText.toLowerCase();
        const hasCtx = HINTS.some(h => ptxt.includes(h));
        const seen = new Set(); const res = [];
        for (const a of document.querySelectorAll("a[href]")) {
            const href = a.href || ""; const lh = href.toLowerCase();
            if (SKIP.some(s => lh.includes(s))) continue;
            if (!hasCtx && !HINTS.some(s => lh.includes(s))) continue;
            const c = a.closest("div,article,section,li"); if (!c) continue;
            const h = c.querySelector("h1,h2,h3,h4"); if (!h) continue;
            const name = h.innerText.trim();
            if (!name || name.length < 4 || name.length > 150) continue;
            if (name.toLowerCase().includes("read more")) continue;
            const k = name + "|" + href;
            if (!seen.has(k)) { seen.add(k); res.push({name, link: href}); }
        }
        return res;
    }
    """

    def __init__(self):
        self._pagination = PaginationEngine()

    def scrape(self, page, url: str) -> list[dict]:
        logger.info(f"[dom_scraper] Starting DOM scrape: {url}")
        self._pagination.reset()

        # Hover over product nav link if present
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        page.wait_for_timeout(2_000)

        # Try hovering nav links that say "products"
        PRODUCT_NAV_LABELS = {
            "products", "products & services", "products and services",
            "products & solutions", "products and solutions",
        }
        try:
            for lnk in page.query_selector_all("a"):
                try:
                    if (lnk.inner_text() or "").strip().lower() in PRODUCT_NAV_LABELS:
                        lnk.hover()
                        page.wait_for_timeout(2_000)
                        break
                except Exception:
                    continue
        except Exception:
            pass

        # Scrape loop with Next navigation
        visited_fp: set = set()
        collected: list[dict] = []

        while True:
            page.wait_for_timeout(2_000)
            fp = page.evaluate("""
                () => {
                    const t = document.body.innerText.slice(0, 4000); let h = 0;
                    for (let i = 0; i < t.length; i++) h = (Math.imul(31,h) + t.charCodeAt(i)) | 0;
                    return String(h);
                }""")
            if fp in visited_fp:
                break
            visited_fp.add(fp)

            items = page.evaluate(self.SCRAPE_JS) or []
            ex = {(i["name"], i["link"]) for i in collected}
            for item in items:
                if (item["name"], item["link"]) not in ex:
                    collected.append(item)
                    ex.add((item["name"], item["link"]))

            # Try "Next" navigation
            clicked = self._pagination._click_next(page)
            if not clicked:
                break

        result = dedup(collected)
        logger.info(f"[dom_scraper] Done: {len(result)} items")
        return result
