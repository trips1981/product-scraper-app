"""
scrapers/anchor_nav_scraper.py — Anchor-nav / category-tab scraper.
Ported from SmartManualApp.scrape_anchor_nav_listing() with improvements.
Handles: Google Cloud, AWS, devsite Web Components, sidebar category navigation.
"""
import json
import logging
from scrapers.base import BaseScraper
from utils.helpers import dedup
import re

logger = logging.getLogger(__name__)

_SKIP = json.dumps([
    "accounts.google", "google.com/search", "support.google",
    "cloud.google.com/blog", "cloud.google.com/learn",
    "cloud.google.com/free", "cloud.google.com/customers",
    "cloud.google.com/solutions", "cloud.google.com/why",
    "cloud.google.com/find", "cloud.google.com/consulting",
    "workspace.google", "cloud.google.com/certification",
    "about.google", "console.cloud.google",
    "cloud.google.com/contact", "cloud.google.com/pricing",
    "cloud.google.com/partners", "cloud.google.com/transform",
])
_GENERIC = json.dumps([
    "view more","learn more","explore","get started","read more",
    "see all","view all","sign up","contact us","try free",
    "start free","documentation","learn","overview","pricing",
    "all products","solutions","resources","products","services",
    "see products","see all products",
])
_ICONS = json.dumps([
    "expand_content","open_in_new","arrow_forward","arrow_back",
    "chevron_right","chevron_left","close","search","menu",
    "more_vert","more_horiz","add","check","info","warning",
    "launch","link","east","west","north","south",
])
_SUB_KW = (
    "platform|service|tool|engine|database|compute|storage|analytics|"
    "registry|api|runtime|network|security|identity|warehouse|machines|"
    "containers|functions|assistant|intelligence|hosting|management|"
    "monitoring|processing|integration|migration|gateway|accelerator|"
    "scheduling|directory|catalog|marketplace|notebook|workbench|workflow|"
    "pipeline|orchestration|cluster|mesh|proxy|cdn|dns|firewall|armor|"
    "scan|secret|key|certificate|logging|tracing|profiling|debugging|"
    "testing|deployment|delivery|repository|build|run|shell|code|sdk|cli"
)

HARVEST_JS = (
    "() => {\n"
    "    const SKIP    = " + _SKIP + ";\n"
    "    const GENERIC = " + _GENERIC + ";\n"
    "    const ICONS   = " + _ICONS + ";\n"
    "    const SUB_RE  = /\\b(" + _SUB_KW + ")\\b/i;\n"
    "    const seen = new Set(); const res = [];\n"
    "    function cleanLines(raw) {\n"
    "        return raw.split(/\\n/).map(l => l.trim()).filter(l => {\n"
    "            if (!l || l.length < 2) return false;\n"
    "            if (ICONS.includes(l.toLowerCase())) return false;\n"
    "            if (/^[a-z][a-z_]{1,30}$/.test(l)) return false;\n"
    "            return true;\n"
    "        });\n"
    "    }\n"
    "    for (const a of document.querySelectorAll('a[href]')) {\n"
    "        const href = a.href || ''; if (!href) continue;\n"
    "        try {\n"
    "            const u = new URL(href);\n"
    "            if (u.hostname !== location.hostname) continue;\n"
    "            if (u.pathname === location.pathname) continue;\n"
    "        } catch(_) { continue; }\n"
    "        if (SKIP.some(p => href.includes(p))) continue;\n"
    "        const st = window.getComputedStyle(a);\n"
    "        if (st.display==='none'||st.visibility==='hidden'||st.opacity==='0') continue;\n"
    "        const rect = a.getBoundingClientRect();\n"
    "        if (rect.width===0 && rect.height===0) continue;\n"
    "        const rawText = (a.innerText||a.textContent||'').trim();\n"
    "        if (!rawText) continue;\n"
    "        const lines = cleanLines(rawText);\n"
    "        if (!lines.length) continue;\n"
    "        let name = lines[0];\n"
    "        if (lines.length >= 2) {\n"
    "            const first = lines[0];\n"
    "            const isSub = first.length < 55 && (\n"
    "                SUB_RE.test(first) || /\\([A-Z]/.test(first) || first === first.toLowerCase()\n"
    "            );\n"
    "            if (isSub) name = lines[1];\n"
    "        }\n"
    "        if (!name || name.length < 3 || name.length > 120) continue;\n"
    "        if (GENERIC.includes(name.toLowerCase())) continue;\n"
    "        const key = name.toLowerCase()+'|'+href;\n"
    "        if (!seen.has(key)) { seen.add(key); res.push({name, link: href}); }\n"
    "    }\n"
    "    return res;\n"
    "}\n"
)

CAT_DISCOVER_JS = """
() => {
    const result = []; const seen = new Set();
    const tabSels = [
        '[role="tab"]','devsite-tabs tab','glue-tab','cloudx-tab',
        '[class*="tab-label"]','[class*="category-label"]',
        '[class*="filter-label"]','[class*="sidebar"] li',
        '[class*="sidenav"] li','[class*="side-nav"] li'
    ];
    for (const sel of tabSels) {
        for (const el of document.querySelectorAll(sel)) {
            if (el.closest('header,footer,nav,[role="navigation"],[role="banner"]')) continue;
            const t = (el.innerText||el.textContent||'').trim();
            if (t.length>=2 && t.length<=60 && !t.includes('\\n') && !seen.has(t)) {
                seen.add(t); result.push({label: t});
            }
        }
        if (result.length >= 3) break;
    }
    if (result.length >= 3) return result;
    result.length = 0; seen.clear();
    for (const ul of document.querySelectorAll('ul,ol,[role="list"]')) {
        if (ul.closest('header,footer,[role="banner"],[role="contentinfo"]')) continue;
        const children = [...ul.children];
        if (children.length < 3) continue;
        const textItems = children.filter(li => {
            const t = (li.innerText||li.textContent||'').trim();
            return t.length>=2 && t.length<=40 && !t.includes('\\n');
        });
        if (textItems.length < 3) continue;
        const extLinks = [...ul.querySelectorAll('a[href]')].filter(a => {
            const h = a.getAttribute('href')||'';
            return h && !h.startsWith('#') && !h.startsWith(location.pathname);
        }).length;
        if (extLinks > children.length/2) continue;
        textItems.forEach(li => {
            const label = (li.innerText||li.textContent||'').trim();
            if (!seen.has(label)) { seen.add(label); result.push({label}); }
        });
        if (result.length >= 3) break;
        result.length = 0; seen.clear();
    }
    return result;
}
"""

CLICK_CAT_JS = """
(label) => {
    const sels = [
        'devsite-tabs tab','glue-tab','cloudx-tab',
        '[role="tab"]','[role="listitem"] button',
        '[class*="tab-label"]','[class*="category-label"]',
        '[class*="sidebar"] li','[class*="sidenav"] li',
        'li','[role="listitem"]','[role="option"]','button'
    ];
    for (const sel of sels) {
        for (const el of document.querySelectorAll(sel)) {
            if ((el.innerText||el.textContent||'').trim()===label) {
                el.scrollIntoView({block:'center'}); el.click(); return true;
            }
        }
    }
    return false;
}
"""

NEXT_ARROW_JS = """
() => {
    const NEXT = new Set(['→','›','>>','>','chevron_right','arrow_forward','next','next page']);
    for (const el of document.querySelectorAll('button,[role="button"],a')) {
        const t = (el.innerText||el.textContent||el.getAttribute('aria-label')||'')
            .trim().toLowerCase();
        if (!NEXT.has(t)) continue;
        const st = window.getComputedStyle(el);
        if (st.display==='none'||st.visibility==='hidden') continue;
        const r = el.getBoundingClientRect();
        if (r.width===0||r.height===0) continue;
        el.click(); return true;
    }
    return false;
}
"""


class AnchorNavScraper(BaseScraper):
    """
    Three-pass scraper for Google Cloud, AWS, and sidebar-nav catalogue pages.
    Pass 1: Featured / all-products section
    Pass 2: Category tabs
    Pass 3: Full-page fallback
    """

    def scrape(self, page, url: str) -> list[dict]:
        logger.info(f"[anchor_nav] Starting: {url}")

        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        page.wait_for_timeout(2_000)

        collected: list[dict] = []

        def scroll_full():
            for _ in range(4):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_000)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(600)

        def harvest(cat_label: str) -> list[dict]:
            raw = page.evaluate(HARVEST_JS) or []
            return [{"name": r["name"], "link": r["link"], "_cat": cat_label} for r in raw]

        # Pass 1 — Featured
        logger.debug("[anchor_nav] Pass 1: featured products")
        scroll_full()
        pass1 = harvest("Featured Products")
        collected.extend(pass1)
        logger.debug(f"[anchor_nav] Pass 1: {len(pass1)} items")

        # Pass 2 — Category tabs
        cats = page.evaluate(CAT_DISCOVER_JS) or []
        logger.debug(f"[anchor_nav] Found {len(cats)} categories")

        for ci, cat in enumerate(cats):
            label = cat["label"]
            logger.debug(f"[anchor_nav] Category {ci+1}/{len(cats)}: '{label}'")

            clicked = page.evaluate(CLICK_CAT_JS, label)
            if not clicked:
                continue

            page.wait_for_timeout(1_500)
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            scroll_full()

            batch = harvest(label)
            collected.extend(batch)

            # Carousel arrows
            prev_count = len(collected)
            for _ in range(30):
                if not page.evaluate(NEXT_ARROW_JS):
                    break
                page.wait_for_timeout(800)
                extra = harvest(label)
                collected.extend(extra)
                if len(collected) == prev_count:
                    break
                prev_count = len(collected)

        # Pass 3 — Fallback
        if not collected:
            logger.debug("[anchor_nav] Pass 3: full-page fallback")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2_000)
            collected = harvest("Products")

        # Deduplicate with category prefix
        seen_keys: set = set()
        final: list[dict] = []
        for item in collected:
            cat_label = item.get("_cat", "")
            raw_name  = item["name"]
            link      = item["link"]
            display   = f"[{cat_label}] {raw_name}" if cat_label else raw_name
            key = (
                re.sub(r"\s+", " ", raw_name.strip().lower()),
                link.strip().rstrip("/").lower(),
            )
            if key not in seen_keys and key[0]:
                seen_keys.add(key)
                final.append({"name": display, "link": link})

        logger.info(f"[anchor_nav] Done: {len(final)} items")
        return final
