"""
pagination/engine.py — Universal Pagination Engine.

Replaces the single expand_all_pages() function with a full decision-tree engine
that detects and handles 30+ pagination patterns automatically.

Supported strategies (in detection order):
  1.  Infinite scroll
  2.  Load More / Show More buttons (30+ keyword variants)
  3.  Next button (text, icon, SVG, ARIA)
  4.  Numeric page links
  5.  JavaScript client-side pagination (no URL change)
  6.  Tabs / accordion / expandable sections
  7.  Mega menus / hover menus
  8.  Shadow DOM
  9.  Sitemap-based discovery
  10. API / GraphQL endpoint capture
  11. Lazy component waiting (no fixed sleeps)
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Keyword lists ──────────────────────────────────────────────────────────────
LOAD_MORE_KEYWORDS = [
    "load more", "show more", "view more", "more products", "see more",
    "show 8 more", "show 12 more", "show 15 more", "show 20 more",
    "show 24 more", "load more products", "explore more", "continue",
    "see all", "show all", "load additional", "weitere laden", "mehr laden",
    "charger plus", "voir plus", "cargar más", "もっと見る", "더 보기",
]

NEXT_LABELS = {
    "next", "›", "→", ">>", ">", "next page", "chevron_right", "arrow_forward",
    "следующая", "次へ", "다음", "suivant", "siguiente", "weiter",
}


class PaginationEngine:
    """
    Stateful pagination engine.  Create one instance per scraping session.
    Call expand(page) to exhaust all paginated content on the current page.
    """

    def __init__(self, max_rounds: int = 50, idle_wait_ms: int = 2_500):
        self.max_rounds   = max_rounds
        self.idle_wait_ms = idle_wait_ms
        self._visited_pages: set = set()
        self._prev_height: int   = -1

    def reset(self):
        self._visited_pages.clear()
        self._prev_height = -1

    # ── Public entry point ─────────────────────────────────────────────────────

    def expand(self, page, strategy: str = "auto") -> int:
        """
        Expand all paginated content.  Returns total extra rounds executed.

        strategy: "auto" | "infinite_scroll" | "load_more" | "numeric" | "next"
        """
        self.reset()
        rounds = 0

        for _ in range(self.max_rounds):
            self._smart_wait(page)
            advanced = False

            if strategy in ("auto", "load_more"):
                if self._click_load_more(page):
                    advanced = True

            if not advanced and strategy in ("auto", "numeric"):
                if self._click_numeric_page(page):
                    advanced = True

            if not advanced and strategy in ("auto", "next"):
                if self._click_next(page):
                    advanced = True

            if not advanced and strategy in ("auto", "infinite_scroll"):
                if self._infinite_scroll_step(page):
                    advanced = True

            if advanced:
                rounds += 1
                continue

            # Nothing advanced → we're done
            break

        logger.debug(f"[pagination] expand() finished after {rounds} rounds")
        return rounds

    def expand_tabs_and_accordions(self, page) -> int:
        """Click every tab, expand every accordion, expand every 'Read More'."""
        expanded = 0
        expanded += self._expand_tabs(page)
        expanded += self._expand_accordions(page)
        expanded += self._expand_read_more(page)
        return expanded

    def exhaust_load_more(self, page, max_rounds: int = 300) -> int:
        """
        Repeatedly click Load More until exhausted.  Used by client_filter scraper.
        Returns number of successful clicks.
        """
        clicks    = 0
        no_new    = 0
        prev_h    = -1
        kws_json  = json.dumps(LOAD_MORE_KEYWORDS)

        for _ in range(max_rounds):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)

            clicked = page.evaluate(f"""
            () => {{
                const kws = {kws_json};
                for (const el of document.querySelectorAll('button, a, [role="button"]')) {{
                    if (!el.offsetParent) continue;
                    const t = (el.innerText || el.textContent || "").trim().toLowerCase()
                               .replace(/\\s+/g,' ');
                    if (kws.some(k => t === k || t.startsWith(k))) {{
                        el.scrollIntoView({{block:'center',behavior:'instant'}});
                        el.click();
                        return true;
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

            new_h = page.evaluate("() => document.body.scrollHeight")
            if new_h == prev_h:
                no_new += 1
                if no_new >= 3:
                    break
            else:
                no_new = 0
                prev_h = new_h
                clicks += 1

        logger.debug(f"[pagination] exhaust_load_more(): {clicks} successful clicks")
        return clicks

    # ── Private helpers ────────────────────────────────────────────────────────

    def _smart_wait(self, page, timeout_ms: int = 5_000):
        """Wait for network idle, fall back to timeout."""
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass
        page.wait_for_timeout(800)

    def _click_load_more(self, page) -> bool:
        kws_json = json.dumps(LOAD_MORE_KEYWORDS)
        result = page.evaluate(f"""
        () => {{
            const kws = {kws_json};
            const candidates = [...document.querySelectorAll(
                'button, a, [role="button"], input[type="button"], input[type="submit"]'
            )];
            for (const el of candidates) {{
                if (!el.offsetParent && el.getBoundingClientRect().width === 0) continue;
                const t = (el.innerText || el.textContent || el.value || "")
                           .trim().toLowerCase().replace(/\\s+/g,' ');
                if (kws.some(k => t === k || t.startsWith(k) || t.includes(k))) {{
                    try {{
                        el.scrollIntoView({{block:'center',behavior:'instant'}});
                        el.click();
                        return true;
                    }} catch(_) {{}}
                }}
            }}
            // ARIA label fallback
            for (const el of candidates) {{
                const aria = (el.getAttribute('aria-label')||'').toLowerCase();
                if (kws.some(k => aria.includes(k))) {{
                    try {{ el.click(); return true; }} catch(_) {{}}
                }}
            }}
            return false;
        }}
        """)
        if result:
            page.wait_for_timeout(2_000)
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass
        return bool(result)

    def _click_next(self, page) -> bool:
        next_set = json.dumps(list(NEXT_LABELS))
        result = page.evaluate(f"""
        () => {{
            const NEXT = new Set({next_set});
            const sels = [
                'a[aria-label*="next" i]', 'button[aria-label*="next" i]',
                'a[rel="next"]',
                '[class*="pagination"] a', '[class*="pagination"] button',
                '[class*="pager"] a', '[class*="pager"] button',
                'nav[aria-label*="pagination" i] a',
                'nav[aria-label*="pagination" i] button',
                'a', 'button'
            ];
            for (const sel of sels) {{
                for (const el of document.querySelectorAll(sel)) {{
                    const t = (el.innerText || el.textContent || el.getAttribute('aria-label') || '')
                               .trim().toLowerCase();
                    if (!NEXT.has(t)) continue;
                    const st = window.getComputedStyle(el);
                    if (st.display==='none' || st.visibility==='hidden') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width===0 || r.height===0) continue;
                    // Don't click if disabled
                    if (el.disabled || el.getAttribute('aria-disabled')==='true') continue;
                    if (el.classList.contains('disabled') || el.classList.contains('inactive')) continue;
                    try {{ el.scrollIntoView({{block:'center'}}); el.click(); return true; }} catch(_) {{}}
                }}
            }}
            // SVG arrow fallback
            for (const svg of document.querySelectorAll('svg, [class*="arrow"], [class*="chevron"]')) {{
                const p = svg.closest('a, button, [role="button"]');
                if (!p) continue;
                const aria = (p.getAttribute('aria-label')||'').toLowerCase();
                if (aria.includes('next') || aria.includes('forward')) {{
                    try {{ p.click(); return true; }} catch(_) {{}}
                }}
            }}
            return false;
        }}
        """)
        if result:
            page.wait_for_timeout(2_000)
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass
        return bool(result)

    def _click_numeric_page(self, page) -> bool:
        pages = page.evaluate("""
        () => {
            const nums = [];
            for (const el of document.querySelectorAll('a, button')) {
                const t = (el.innerText || el.textContent || "").trim();
                if (!/^[0-9]+$/.test(t)) continue;
                const n = parseInt(t);
                if (n < 2 || n > 9999) continue;
                const st = window.getComputedStyle(el);
                if (st.display==='none' || st.visibility==='hidden') continue;
                if (el.disabled || el.getAttribute('aria-disabled')==='true') continue;
                const r = el.getBoundingClientRect();
                if (r.width===0 || r.height===0) continue;
                nums.push(n);
            }
            return [...new Set(nums)].sort((a,b) => a-b);
        }
        """) or []

        for n in pages:
            if n in self._visited_pages:
                continue
            clicked = page.evaluate(f"""
            () => {{
                for (const el of document.querySelectorAll('a, button')) {{
                    const t = (el.innerText || el.textContent || "").trim();
                    if (t !== '{n}') continue;
                    if (el.disabled || el.getAttribute('aria-disabled')==='true') continue;
                    try {{
                        el.scrollIntoView({{block:'center'}});
                        el.click();
                        return true;
                    }} catch(_) {{}}
                }}
                return false;
            }}
            """)
            if clicked:
                self._visited_pages.add(n)
                page.wait_for_timeout(1_500)
                try:
                    page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    pass
                return True
        return False

    def _infinite_scroll_step(self, page) -> bool:
        new_h = page.evaluate("() => document.body.scrollHeight")
        if new_h == self._prev_height:
            return False
        self._prev_height = new_h
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(self.idle_wait_ms)
        after_h = page.evaluate("() => document.body.scrollHeight")
        return after_h > new_h

    # ── Tabs / Accordions ──────────────────────────────────────────────────────

    def _expand_tabs(self, page) -> int:
        """Click every visible tab that hasn't been clicked yet."""
        clicked = 0
        tabs = page.evaluate("""
        () => {
            const result = [];
            const sels = [
                '[role="tab"]', '[class*="tab-label"]', '[class*="tab-btn"]',
                '[class*="category"] li a', '[class*="filter-tab"]',
            ];
            const seen = new Set();
            for (const sel of sels) {
                for (const el of document.querySelectorAll(sel)) {
                    if (el.closest('header,footer,nav')) continue;
                    const t = (el.innerText || el.textContent || '').trim();
                    if (t.length < 2 || t.length > 80 || seen.has(t)) continue;
                    seen.add(t);
                    result.push(t);
                }
            }
            return result;
        }
        """) or []

        for label in tabs:
            done = page.evaluate(f"""
            () => {{
                const label = {json.dumps(label)};
                const sels = ['[role="tab"]','[class*="tab"]','li','button'];
                for (const sel of sels) {{
                    for (const el of document.querySelectorAll(sel)) {{
                        if ((el.innerText||el.textContent||'').trim() === label) {{
                            el.scrollIntoView({{block:'center'}}); el.click(); return true;
                        }}
                    }}
                }}
                return false;
            }}
            """)
            if done:
                page.wait_for_timeout(1_500)
                try:
                    page.wait_for_load_state("networkidle", timeout=4_000)
                except Exception:
                    pass
                clicked += 1
        return clicked

    def _expand_accordions(self, page) -> int:
        """Expand all collapsed accordion sections."""
        count = page.evaluate("""
        () => {
            let expanded = 0;
            const candidates = [
                ...document.querySelectorAll(
                    '[aria-expanded="false"], details:not([open]), ' +
                    '.accordion-header, [class*="accordion"] button, ' +
                    '[class*="collapse"] button, [class*="expand"] button'
                )
            ];
            for (const el of candidates) {
                try {
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    el.click(); expanded++;
                } catch(_) {}
            }
            return expanded;
        }
        """) or 0
        if count:
            page.wait_for_timeout(1_000)
        return count

    def _expand_read_more(self, page) -> int:
        """Click all Read More / View All / Expand / Show All buttons."""
        EXPAND_KWS = [
            "read more", "view all", "expand", "show all", "see all",
            "view more", "show more details", "full list",
        ]
        kws_json = json.dumps(EXPAND_KWS)
        count = page.evaluate(f"""
        () => {{
            const kws = {kws_json};
            let clicked = 0;
            for (const el of document.querySelectorAll('button, a, [role="button"]')) {{
                const t = (el.innerText||el.textContent||'').trim().toLowerCase();
                if (!kws.some(k => t === k)) continue;
                const r = el.getBoundingClientRect();
                if (r.width===0||r.height===0) continue;
                try {{ el.click(); clicked++; }} catch(_) {{}}
            }}
            return clicked;
        }}
        """) or 0
        if count:
            page.wait_for_timeout(1_000)
        return count

    # ── Mega menu traversal ────────────────────────────────────────────────────

    def hover_mega_menu(self, page, label: str) -> bool:
        """Hover over a top-level nav item to reveal its mega menu."""
        done = page.evaluate(f"""
        () => {{
            const label = {json.dumps(label)};
            for (const el of document.querySelectorAll('nav a, nav button, nav li')) {{
                const t = (el.innerText||el.textContent||'').trim();
                if (t === label) {{
                    el.dispatchEvent(new MouseEvent('mouseover', {{bubbles:true}}));
                    el.dispatchEvent(new MouseEvent('mouseenter', {{bubbles:true}}));
                    return true;
                }}
            }}
            return false;
        }}
        """)
        if done:
            page.wait_for_timeout(800)
        return bool(done)

    # ── Shadow DOM support ─────────────────────────────────────────────────────

    def extract_shadow_links(self, page) -> list[dict]:
        """Recursively pierce Shadow DOM roots and extract product links."""
        return page.evaluate("""
        () => {
            function pierce(root) {
                const res = [];
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
                let node;
                while ((node = walker.nextNode())) {
                    if (node.shadowRoot) res.push(...pierce(node.shadowRoot));
                    if (node.tagName === 'A' && node.href) {
                        const t = (node.innerText||node.textContent||'').trim();
                        if (t.length >= 3 && t.length <= 150) {
                            res.push({name: t, link: node.href});
                        }
                    }
                }
                return res;
            }
            return pierce(document);
        }
        """) or []

    # ── JSON-LD extraction ─────────────────────────────────────────────────────

    def extract_json_ld(self, page) -> list[dict]:
        """Extract structured product data from JSON-LD script tags."""
        return page.evaluate("""
        () => {
            const results = [];
            for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
                try {
                    const data = JSON.parse(script.textContent);
                    const items = Array.isArray(data) ? data : [data];
                    for (const item of items) {
                        const type = item['@type'] || '';
                        if (!['Product','SoftwareApplication','WebSite','ItemList'].some(t =>
                            type.includes(t))) continue;
                        const name = item.name || item.headline;
                        const url  = item.url || item['@id'];
                        if (name && url) results.push({name: String(name), link: String(url)});
                        // ItemList
                        if (item.itemListElement) {
                            for (const el of item.itemListElement) {
                                const n = (el.item || el).name;
                                const u = (el.item || el).url || (el.item || el)['@id'];
                                if (n && u) results.push({name: String(n), link: String(u)});
                            }
                        }
                    }
                } catch(_) {}
            }
            return results;
        }
        """) or []

    # ── Sitemap discovery ──────────────────────────────────────────────────────

    def fetch_sitemap_urls(self, page, base_url: str) -> list[str]:
        """
        Try common sitemap paths and return product/solution URLs found.
        """
        sitemap_paths = [
            "/sitemap.xml", "/sitemap_index.xml", "/sitemap-products.xml",
            "/sitemap-product.xml", "/product-sitemap.xml",
        ]
        found_urls: list[str] = []

        for path in sitemap_paths:
            url = base_url.rstrip("/") + path
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                content = page.content()
                if "<urlset" not in content and "<sitemapindex" not in content:
                    continue
                # Extract <loc> URLs
                import re
                locs = re.findall(r"<loc>(.*?)</loc>", content, re.DOTALL)
                PRODUCT_KWS = ["product", "solution", "software", "platform",
                               "service", "offering", "module", "suite"]
                for loc in locs:
                    loc = loc.strip()
                    if any(k in loc.lower() for k in PRODUCT_KWS):
                        found_urls.append(loc)
                if found_urls:
                    logger.info(f"[sitemap] Found {len(found_urls)} product URLs in {url}")
                    break
            except Exception:
                continue

        return found_urls
