"""
scrapers/spa_scraper.py — SPA / JavaScript API scraper.
Ported from SmartManualApp.capture_spa_api() with improvements.
"""
import logging
import threading
from scrapers.base import BaseScraper
from pagination.engine import PaginationEngine
from utils.helpers import dedup

logger = logging.getLogger(__name__)


class SPAScraper(BaseScraper):
    """
    Captures JSON responses from React / Angular / Vue SPAs and
    combines them with DOM scraping as a fallback.
    """

    DOM_SCRAPE_JS = """
    () => {
        const seen = new Set(); const res = [];
        for (const a of document.querySelectorAll("a[href]")) {
            if (!a.href.startsWith(location.origin)) continue;
            const skip = ["#","privacy","cookie","terms","login"];
            if (skip.some(s => a.href.toLowerCase().includes(s))) continue;
            const card = a.closest("div,article,section"); if (!card) continue;
            const h = card.querySelector("h2,h3,h4"); if (!h) continue;
            const title = h.innerText.trim();
            if (!title || title.length < 4 || title.length > 150) continue;
            if (title.toLowerCase().includes("read more")) continue;
            const k = title + "|" + a.href;
            if (!seen.has(k)) { seen.add(k); res.push({name: title, link: a.href}); }
        }
        return res;
    }
    """

    def __init__(self):
        self._pagination = PaginationEngine()
        self._lock       = threading.Lock()

    # ── Directory API probe (AWS and similar) ──────────────────────────────────
    @staticmethod
    def _probe_directory_api(page, url: str) -> list[dict]:
        """
        For AWS: wait for product cards to render, then keep clicking
        'Show X more' until all products are visible, then scrape the DOM.
        Simple and reliable — no API interception needed.
        """
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if "aws.amazon.com" not in parsed.netloc:
            return []

        logger.info("[spa_scraper] AWS — clicking 'Show more' until all cards loaded")

        try:
            # Wait for initial cards to appear
            try:
                page.wait_for_selector("[class*='card'], [class*='Card'], [class*='lb-card']",
                                       timeout=15_000)
            except Exception:
                pass
            page.wait_for_timeout(2_000)

            # Keep clicking any "Show more" / "Show X more" button until gone
            for _ in range(50):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)

                clicked = page.evaluate("""
                () => {
                    for (const el of document.querySelectorAll('button, a, [role="button"]')) {
                        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                        if (t.startsWith('show') && t.includes('more') ||
                            t === 'load more' || t === 'see more' || t === 'view more') {
                            const r = el.getBoundingClientRect();
                            if (r.width === 0 && r.height === 0) continue;
                            el.scrollIntoView({block:'center'});
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
                """)

                if not clicked:
                    break

                page.wait_for_timeout(2_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    pass

            # Scrape all visible product cards from DOM
            results = page.evaluate("""
            () => {
                const seen = new Set();
                const items = [];
                // AWS product cards: each card has a heading and a "Learn more" link
                const cards = document.querySelectorAll(
                    '[class*="card"], [class*="Card"], [class*="lb-card"], ' +
                    'article, [class*="product-card"], [class*="ProductCard"]'
                );
                for (const card of cards) {
                    const h = card.querySelector('h2, h3, h4, [class*="title"], [class*="heading"]');
                    if (!h) continue;
                    const name = (h.innerText || h.textContent || '').trim();
                    if (!name || name.length < 3 || name.length > 150) continue;

                    const a = card.querySelector('a[href]');
                    const link = a ? a.href : '';

                    // Category from badge/tag
                    const badge = card.querySelector('[class*="badge"], [class*="tag"], [class*="category"]');
                    const category = badge ? (badge.innerText || badge.textContent || '').trim() : '';

                    // Description
                    const desc = card.querySelector('p, [class*="desc"], [class*="body"]');
                    const description = desc ? (desc.innerText || desc.textContent || '').trim() : '';

                    const key = name + '|' + link;
                    if (!seen.has(key)) {
                        seen.add(key);
                        items.push({ name, link, category, description });
                    }
                }
                return items;
            }
            """) or []

            logger.info(f"[spa_scraper] AWS DOM scrape: {len(results)} products")
            return results

        except Exception as e:
            logger.warning(f"[spa_scraper] AWS probe failed: {e}")
            return []

    @staticmethod
    def _parse_directory_items(items: list) -> list[dict]:
        """Parse AWS-style directory API items into clean records."""
        import re
        results = []
        for entry in items:
            inner  = entry.get("item", {})
            fields = inner.get("additionalFields", {})
            name   = (fields.get("title") or fields.get("heading") or
                      fields.get("productName") or inner.get("name", ""))
            link   = (fields.get("ctaLink") or fields.get("url") or
                      fields.get("link") or "")
            body   = re.sub(r"<[^>]+>", "", fields.get("body", "")).strip()
            category = next(
                (t.get("name", "") for t in entry.get("tags", [])
                 if "technology-categories" in t.get("tagNamespaceId", "")),
                ""
            )
            if name:
                results.append({
                    "name":        name.strip(),
                    "link":        link.strip(),
                    "category":    category,
                    "description": body,
                })
        return results

    @staticmethod
    def _fetch_directory_api(page, api_url: str, vendor: str) -> list[dict]:
        """Kept for backwards compatibility — not used for AWS."""
        return []

    # ── Directory API re-fetch (AWS / similar patterns) ────────────────────────
    @staticmethod
    def _try_full_fetch(page, api_url: str) -> list[dict]:
        """
        If the page loaded with size=1 or size=N, re-call the same API
        with size=300 to get all items in one shot.
        Returns list of {"name", "link", "category"} or [] if not applicable.
        """
        import re as _re
        try:
            # Replace any existing size param with 300
            full_url = _re.sub(r"size=\d+", "size=300", api_url)
            if "size=" not in full_url:
                sep = "&" if "?" in full_url else "?"
                full_url = full_url + sep + "size=300"
            # Remove pagination offset
            full_url = _re.sub(r"&?from=\d+", "", full_url)
            full_url = _re.sub(r"&?page=\d+", "", full_url)

            logger.info(f"[spa_scraper] Directory API full-fetch: {full_url[:100]}")
            resp = page.request.get(full_url, headers={"Accept": "application/json"})
            data = resp.json()

            items = data.get("items", [])
            total = data.get("metadata", {}).get("totalHits", len(items))
            logger.info(f"[spa_scraper] Directory API returned {len(items)}/{total} items")

            results = []
            for entry in items:
                inner  = entry.get("item", {})
                fields = inner.get("additionalFields", {})
                import re as re2
                name   = (fields.get("title") or fields.get("heading") or
                          fields.get("productName") or inner.get("name", ""))
                link   = (fields.get("ctaLink") or fields.get("url") or
                          fields.get("link") or "")
                body   = re2.sub(r"<[^>]+>", "", fields.get("body", "")).strip()
                category = next(
                    (t.get("name","") for t in entry.get("tags", [])
                     if "technology-categories" in t.get("tagNamespaceId","")),
                    ""
                )
                if name:
                    results.append({
                        "name":        name.strip(),
                        "link":        link.strip(),
                        "category":    category,
                        "description": body,
                    })
            return results
        except Exception as e:
            logger.warning(f"[spa_scraper] Directory API full-fetch failed: {e}")
            return []

    def scrape(self, page, url: str) -> list[dict]:
        logger.info(f"[spa_scraper] Starting SPA scrape: {url}")
        collected: list[dict] = []
        _directory_api_url: list[str] = []   # mutable container for closure

        # ── Fast path: probe for directory API by trying known URL patterns ──
        # This runs BEFORE page.goto so we don't depend on event interception
        directory_results = self._probe_directory_api(page, url)
        if directory_results:
            logger.info(f"[spa_scraper] Directory API probe succeeded: {len(directory_results)} items")
            return directory_results

        def on_response(response):
            try:
                ct   = response.headers.get("content-type", "")
                rurl = response.url.lower()
                if "application/json" not in ct:
                    return
                if not any(k in rurl for k in (
                    "product", "search", "catalog", "items", "solution",
                    "dirs", "directory", "services", "listing", "api/",
                )):
                    return
                # Track directory-style API URLs for full re-fetch later
                if "dirs/items/search" in rurl or "directoryId" in rurl:
                    if not _directory_api_url:
                        _directory_api_url.append(response.url)
                data = response.json()

                # Flatten: support both top-level list and nested {"items": [...]}
                raw_items = []
                if isinstance(data, list):
                    raw_items = data
                elif isinstance(data, dict):
                    # Try common wrapper keys first
                    for key in ("items", "results", "products", "data", "entries"):
                        if isinstance(data.get(key), list):
                            raw_items = data[key]
                            break
                    # Fallback: first list value
                    if not raw_items:
                        raw_items = next(
                            (v for v in data.values() if isinstance(v, list)), []
                        )

                for entry in raw_items:
                    if not isinstance(entry, dict):
                        continue

                    # ── Pattern 1: flat  {"title": ..., "url": ...} ──────────────
                    name = (entry.get("title") or entry.get("name") or
                            entry.get("label") or entry.get("heading"))
                    link = (entry.get("url") or entry.get("link") or
                            entry.get("href") or entry.get("ctaLink"))

                    # ── Pattern 2: AWS/directory  {"item": {"additionalFields": {…}}} ──
                    if not name:
                        inner  = entry.get("item", {})
                        fields = inner.get("additionalFields", {})
                        name   = (fields.get("title") or fields.get("heading") or
                                  fields.get("productName") or inner.get("name"))
                        link   = (fields.get("ctaLink") or fields.get("url") or
                                  fields.get("link") or fields.get("productUrl"))

                    # ── Pattern 3: tags enrichment for category ──────────────────
                    category = ""
                    for tag in entry.get("tags", []):
                        ns = tag.get("tagNamespaceId", "")
                        if "technology-categories" in ns or "category" in ns:
                            category = tag.get("name", "")
                            break

                    if name:
                        record: dict = {"name": str(name).strip()}
                        if link:
                            record["link"] = str(link).strip()
                        if category:
                            record["category"] = category
                        with self._lock:
                            collected.append(record)

            except Exception:
                pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(4_000)
            self._pagination.expand(page, strategy="auto")

            # Extra scrolls to trigger lazy API calls
            for _ in range(6):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2_000)

            # Numeric pagination
            visited: set = set()
            for _ in range(20):
                page.wait_for_timeout(2_500)
                dom = page.evaluate(self.DOM_SCRAPE_JS) or []
                with self._lock:
                    collected.extend(dom)

                # Try next numeric page
                nums = page.evaluate("""
                () => {
                    const s = new Set();
                    for (const el of document.querySelectorAll('a, button')) {
                        const t = (el.innerText || el.textContent || '').trim();
                        if (/^[0-9]+$/.test(t)) s.add(parseInt(t));
                    }
                    return [...s].filter(n => n > 1 && n < 500).sort((a,b) => a-b);
                }
                """) or []

                clicked = False
                for n in nums:
                    if str(n) in visited:
                        continue
                    done = page.evaluate(f"""
                    () => {{
                        for (const el of document.querySelectorAll('a, button')) {{
                            const t = (el.innerText||el.textContent||'').trim();
                            if (t === '{n}' && el.getBoundingClientRect().width > 0) {{
                                el.scrollIntoView({{block:'center'}});
                                el.click(); return true;
                            }}
                        }}
                        return false;
                    }}
                    """)
                    if done:
                        visited.add(str(n))
                        page.wait_for_timeout(3_000)
                        clicked = True
                        break

                if not clicked:
                    break

        finally:
            page.remove_listener("response", on_response)

        # ── Directory API full-fetch (AWS and similar) ──────────────────────────
        # If we captured a directory-style API URL, re-fetch with size=300
        # This replaces partial results (size=1 or size=8) with all items.
        if _directory_api_url:
            full_results = self._try_full_fetch(page, _directory_api_url[0])
            if full_results:
                logger.info(f"[spa_scraper] Using directory API full-fetch: {len(full_results)} items")
                return full_results   # Complete dataset — skip DOM fallback

        # DOM fallback if API capture yielded nothing
        if not collected:
            collected = page.evaluate(self.DOM_SCRAPE_JS) or []

        result = dedup(collected)
        logger.info(f"[spa_scraper] Done: {len(result)} items")
        return result
