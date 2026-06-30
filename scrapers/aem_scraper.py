"""
scrapers/aem_scraper.py — Adobe Experience Manager JSON endpoint scraper.
Ported from SmartManualApp.adaptive_structured_scrape() with improvements.
"""
import json
import logging
from urllib.parse import urlparse
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

AEM_SUFFIXES = [
    "/jcr:content/root/container/container/container/columncontainer/column1/card_list.list.json",
    "/jcr:content/root/responsivegrid/container/card_list.list.json",
    "/jcr:content/root/responsivegrid/card_list.list.json",
    ".model.json",
    ".infinity.json",
    "/_jcr_content.model.json",
]


class AEMScraper(BaseScraper):
    """
    Detects and fetches AEM JSON endpoints to extract product lists.
    Falls back to DOM scraping if no JSON endpoint found.
    """

    def scrape(self, page, url: str) -> list[dict]:
        logger.info(f"[aem_scraper] Starting: {url}")
        page.wait_for_timeout(3_000)

        suffixes_json = json.dumps(AEM_SUFFIXES)
        json_data = page.evaluate(f"""
        async () => {{
            const path = location.pathname.replace('.html','');
            const sfx  = {suffixes_json};
            const pfx  = ["/content" + path, path, ""];
            for (const p of pfx) for (const s of sfx) {{
                try {{
                    const r = await fetch(p + s);
                    if (r.ok) return await r.json();
                }} catch(_) {{}}
            }}
            return null;
        }}""")

        if not json_data:
            logger.debug("[aem_scraper] No JSON endpoint — falling back to DOM")
            from scrapers.dom_scraper import DOMScraper
            return DOMScraper().scrape(page, url)

        def find_list(obj, depth=0):
            if depth > 4:
                return None
            if isinstance(obj, list) and obj:
                return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    r = find_list(v, depth + 1)
                    if r:
                        return r
            return None

        product_list = find_list(json_data)
        if not product_list:
            logger.debug("[aem_scraper] No product list in JSON — falling back to DOM")
            from scrapers.dom_scraper import DOMScraper
            return DOMScraper().scrape(page, url)

        parsed  = urlparse(page.url)
        base_u  = f"{parsed.scheme}://{parsed.netloc}"
        results = []

        for item in product_list:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or item.get("name") or
                     item.get("heading") or item.get("cardTitle") or "")
            link  = (item.get("ctaLink") or item.get("link") or
                     item.get("url") or item.get("href") or "")
            if link.startswith("/"):
                link = base_u + link
            if title:
                results.append({"name": title.strip(), "link": link})

        logger.info(f"[aem_scraper] Done: {len(results)} items")
        return results
