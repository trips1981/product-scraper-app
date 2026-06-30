"""
architecture/detector.py — Site Architecture Detection Engine.

Decision pipeline:
    1. Vendor profile lookup (exact domain match)
    2. Framework detection (React, Angular, Vue, Next.js, Remix, etc.)
    3. CMS detection (AEM, Shopify, Salesforce, Sitecore, Drupal, WP, headless)
    4. Pagination type detection
    5. Navigation type detection
    6. Product layout detection
    → Returns ArchitectureProfile with all signals populated.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ArchitectureProfile:
    """Full architecture profile for a single URL."""
    url:            str  = ""
    framework:      str  = "unknown"   # react | angular | vue | nextjs | remix | svelte | static
    cms:            str  = "unknown"   # aem | shopify | salesforce | sitecore | drupal | wordpress | headless | custom
    pagination:     str  = "unknown"   # infinite_scroll | load_more | numeric | next | client_filter | spa | anchor_nav | none
    navigation:     str  = "unknown"   # tabs | accordion | mega_menu | sidebar | flat
    product_layout: str  = "unknown"   # cards | list | grid | table
    vendor_profile: str  = ""          # e.g. "microsoft", "adobe", "google_cloud"
    confidence:     float = 0.0
    signals:        list  = field(default_factory=list)
    scraper_strategy: str = "dom"      # dom | spa | client_filter | anchor_nav | aem | shopify | salesforce | sitecore | drupal | wp


class ArchitectureDetector:
    """
    Detects website architecture from a Playwright page.

    Usage:
        detector = ArchitectureDetector()
        profile  = detector.detect(page, url)
    """

    def detect(self, page, url: str) -> ArchitectureProfile:
        profile = ArchitectureProfile(url=url)

        # 1. Vendor profile
        vendor = self._detect_vendor_profile(url)
        if vendor:
            profile.vendor_profile = vendor
            profile.signals.append(f"vendor:{vendor}")

        # 2. Full JS-based detection in one evaluate call
        signals = page.evaluate(self._BUILD_DETECTION_JS()) or {}
        self._apply_signals(profile, signals, url)

        # 3. Choose scraper strategy
        profile.scraper_strategy = self._choose_strategy(profile)

        logger.info(
            f"[architecture] {url[:60]} → framework={profile.framework} "
            f"cms={profile.cms} pagination={profile.pagination} "
            f"strategy={profile.scraper_strategy}"
        )
        return profile

    # ── Vendor profile ─────────────────────────────────────────────────────────

    # Domain → vendor profile name
    VENDOR_PROFILES: dict[str, str] = {
        "microsoft.com":        "microsoft",
        "azure.microsoft.com":  "microsoft",
        "sap.com":              "sap",
        "oracle.com":           "oracle",
        "salesforce.com":       "salesforce",
        "adobe.com":            "adobe",
        "cisco.com":            "cisco",
        "vmware.com":           "vmware",
        "servicenow.com":       "servicenow",
        "snowflake.com":        "snowflake",
        "atlassian.com":        "atlassian",
        "mongodb.com":          "mongodb",
        "cloudflare.com":       "cloudflare",
        "hubspot.com":          "hubspot",
        "workday.com":          "workday",
        "autodesk.com":         "autodesk",
        "nvidia.com":           "nvidia",
        "cloud.google.com":     "google_cloud",
        "aws.amazon.com":       "aws",
        "ibm.com":              "ibm",
        "redhat.com":           "redhat",
        "elastic.co":           "elastic",
        "datadoghq.com":        "datadog",
        "crowdstrike.com":      "crowdstrike",
        "fortinet.com":         "fortinet",
        "paloaltonetworks.com": "palo_alto",
        "okta.com":             "okta",
        "genesys.com":          "genesys",
        "shopify.com":          "shopify",
        "siemens.com":          "siemens",
        "se.com":               "schneider",
        "3dsystems.com":        "3d_systems",
    }

    def _detect_vendor_profile(self, url: str) -> str:
        try:
            from urllib.parse import urlparse
            hostname = urlparse(url).hostname or ""
            hostname = re.sub(r"^www\.", "", hostname).lower()
            # Exact match
            if hostname in self.VENDOR_PROFILES:
                return self.VENDOR_PROFILES[hostname]
            # Partial match (subdomain)
            for domain, profile in self.VENDOR_PROFILES.items():
                if hostname.endswith("." + domain) or hostname == domain:
                    return profile
        except Exception:
            pass
        return ""

    # ── JS detection payload ───────────────────────────────────────────────────

    @staticmethod
    def _BUILD_DETECTION_JS() -> str:
        return r"""
        () => {
            const body   = document.body;
            const html   = document.documentElement;
            const host   = location.hostname.replace(/^www\./, '');
            const signals = {};

            // ── Google Cloud / devsite (must be first) ─────────────────────────
            if (host === 'cloud.google.com') {
                signals.cms = 'google_devsite';
                signals.pagination = 'anchor_nav';
                signals.framework  = 'angular';
                return signals;
            }

            // ── Framework detection ────────────────────────────────────────────
            // React
            if (body.querySelector('#root, #app, [data-reactroot], [id*="react"]') ||
                typeof window.__REACT_DEVTOOLS_GLOBAL_HOOK__ !== 'undefined') {
                // Next.js
                if (body.querySelector('#__next') || window.__NEXT_DATA__ !== undefined ||
                    document.querySelector('script#__NEXT_DATA__')) {
                    signals.framework = 'nextjs';
                } else if (window.__remixContext !== undefined) {
                    signals.framework = 'remix';
                } else {
                    signals.framework = 'react';
                }
            }
            // Angular
            else if (body.querySelector('[ng-version], [_nghost], [_ngcontent]') ||
                     body.getAttribute('ng-version') ||
                     window.ng !== undefined ||
                     document.querySelector('app-root, [approot]')) {
                signals.framework = 'angular';
            }
            // Vue
            else if (body.querySelector('[data-v-app], #__nuxt, #__vue') ||
                     window.__vue_store__ !== undefined ||
                     window.__nuxt !== undefined) {
                signals.framework  = 'vue';
                if (body.querySelector('#__nuxt')) signals.subframework = 'nuxt';
            }
            // Svelte
            else if (body.querySelector('[data-svelte], .svelte-')) {
                signals.framework = 'svelte';
            }

            // ── CMS detection ──────────────────────────────────────────────────
            // AEM
            if (body.querySelector('[data-cmp-data-layer], [data-sly-use], .cmp-container, ' +
                                   '[class*="cmp-"], [data-path*="/jcr:content"]')) {
                signals.cms = 'aem';
            }
            // Shopify
            else if (window.Shopify !== undefined || window.ShopifyAnalytics !== undefined ||
                     body.querySelector('[data-shopify], .shopify-section, #shopify-section-header')) {
                signals.cms = 'shopify';
            }
            // Salesforce Experience Cloud
            else if (window.Sfdc !== undefined || window.sforce !== undefined ||
                     body.querySelector('.forcePage, .siteforce-layout, [class*="salesforce"]') ||
                     document.querySelector('meta[name="salesforce"]')) {
                signals.cms = 'salesforce_experience';
            }
            // Sitecore
            else if (window.Sitecore !== undefined ||
                     body.querySelector('[data-sc-id], [sc-id], .scEditor')) {
                signals.cms = 'sitecore';
            }
            // Drupal
            else if (window.drupal !== undefined || window.Drupal !== undefined ||
                     body.querySelector('[data-drupal-messages], .drupal-js-added, .views-element-container')) {
                signals.cms = 'drupal';
            }
            // WordPress
            else if (body.classList.contains('home') && body.querySelector('.wp-block-query') ||
                     window.wp !== undefined ||
                     document.querySelector('meta[name="generator"][content*="WordPress"]') ||
                     body.querySelector('.wp-site-blocks, .wp-block')) {
                signals.cms = 'wordpress';
            }
            // Contentful / Headless CMS (detect via meta or common patterns)
            else if (document.querySelector('meta[name="generator"][content*="Contentful"]') ||
                     window.__NEXT_DATA__ && window.__NEXT_DATA__.props) {
                signals.cms = 'headless';
            }

            // ── Pagination detection ───────────────────────────────────────────
            // AEM JSON endpoint detection
            if (signals.cms === 'aem') {
                signals.pagination = 'aem_json';
            }

            // Client-side filter listing
            const filterBtns = [...document.querySelectorAll(
                'button[aria-pressed], [role="tab"], [role="checkbox"], ' +
                'input[type="checkbox"], [data-filter], [data-tab], ' +
                '[class*="chip"], [class*="filter-btn"], [class*="facet"]'
            )].filter(el => {
                const t = (el.innerText || el.textContent || '').trim();
                return t.length > 1 && t.length < 60;
            });
            const cards = document.querySelectorAll(
                'article, [class*="card"], [class*="product"], ' +
                '[class*="feature"], [class*="item"], [class*="tile"]'
            );
            const numericLinks = [...document.querySelectorAll('a')].filter(a => {
                const t = (a.innerText || '').trim();
                return /^[0-9]+$/.test(t) && parseInt(t) > 1;
            });

            if (filterBtns.length >= 2 && cards.length >= 4 && numericLinks.length === 0) {
                if (!signals.pagination) signals.pagination = 'client_filter';
            }

            // Anchor nav (section-based)
            const anchorNavLinks = [...document.querySelectorAll(
                'nav a[href^="#"], aside a[href^="#"], [class*="sidebar"] a[href^="#"]'
            )].filter(a => document.getElementById(a.getAttribute('href').slice(1)));
            if (anchorNavLinks.length >= 3) {
                if (!signals.pagination) signals.pagination = 'anchor_nav';
            }

            // Infinite scroll detection
            if (document.querySelector('[class*="infinite"], [data-infinite-scroll], ' +
                                       '[class*="lazyload"], [class*="lazy-load"]')) {
                if (!signals.pagination) signals.pagination = 'infinite_scroll';
            }

            // Load more button
            const LM_KWS = ['load more','show more','view more','more products','see more'];
            const hasLoadMore = [...document.querySelectorAll('button, a')].some(el => {
                const t = (el.innerText||el.textContent||'').trim().toLowerCase();
                return LM_KWS.some(k => t === k);
            });
            if (hasLoadMore) {
                if (!signals.pagination) signals.pagination = 'load_more';
            }

            // Numeric pagination
            if (numericLinks.length >= 2) {
                if (!signals.pagination) signals.pagination = 'numeric';
            }

            // SPA (React/Angular/Vue with no static pagination)
            if (!signals.pagination && signals.framework && signals.framework !== 'unknown') {
                signals.pagination = 'spa';
            }

            // DOM fallback
            if (!signals.pagination) {
                signals.pagination = document.querySelectorAll('a[href]').length > 40
                    ? 'dom' : 'none';
            }

            // ── Navigation type ────────────────────────────────────────────────
            const hasTabs = document.querySelectorAll('[role="tab"], .tab, [class*="tab-nav"]').length >= 2;
            const hasAccordion = document.querySelectorAll(
                '[aria-expanded], details, .accordion, [class*="accordion"]').length >= 2;
            const hasMegaMenu = document.querySelectorAll(
                '.mega-menu, [class*="megamenu"], [class*="mega-nav"]').length >= 1;
            const hasSidebar  = document.querySelectorAll(
                'aside, [class*="sidebar"], [class*="side-nav"]').length >= 1;

            signals.navigation = hasMegaMenu ? 'mega_menu' :
                                 hasTabs     ? 'tabs' :
                                 hasAccordion? 'accordion' :
                                 hasSidebar  ? 'sidebar' : 'flat';

            // ── Product layout ─────────────────────────────────────────────────
            const hasGrid = document.querySelector(
                '[class*="grid"], [class*="cards"], [class*="product-list"]');
            const hasTable = document.querySelector('table[class*="product"], .comparison-table');
            signals.product_layout = hasTable ? 'table' : hasGrid ? 'grid' : 'list';

            // ── Shadow DOM ─────────────────────────────────────────────────────
            signals.has_shadow_dom = !![...document.querySelectorAll('*')]
                .slice(0, 500).find(el => el.shadowRoot);

            // ── GraphQL ────────────────────────────────────────────────────────
            // Presence of Apollo or Relay
            signals.has_graphql = !!(
                window.__APOLLO_CLIENT__ || window.__APOLLO_STATE__ ||
                window.__relay_store__ || document.querySelector('script[src*="graphql"]')
            );

            return signals;
        }
        """

    def _apply_signals(self, profile: ArchitectureProfile, signals: dict, url: str):
        if not signals:
            return

        if signals.get("framework"):
            profile.framework = signals["framework"]
            profile.signals.append(f"framework:{profile.framework}")

        if signals.get("cms"):
            profile.cms = signals["cms"]
            profile.signals.append(f"cms:{profile.cms}")

        if signals.get("pagination"):
            profile.pagination = signals["pagination"]
            profile.signals.append(f"pagination:{profile.pagination}")

        if signals.get("navigation"):
            profile.navigation = signals["navigation"]
            profile.signals.append(f"navigation:{profile.navigation}")

        if signals.get("product_layout"):
            profile.product_layout = signals["product_layout"]

        if signals.get("has_shadow_dom"):
            profile.signals.append("shadow_dom:true")

        if signals.get("has_graphql"):
            profile.signals.append("graphql:true")

        profile.confidence = self._compute_confidence(profile)

    def _compute_confidence(self, p: ArchitectureProfile) -> float:
        score = 0.0
        if p.framework   != "unknown": score += 0.3
        if p.cms         != "unknown": score += 0.3
        if p.pagination  != "unknown": score += 0.2
        if p.vendor_profile:           score += 0.2
        return min(score, 1.0)

    def _choose_strategy(self, p: ArchitectureProfile) -> str:
        """Map architecture signals → scraper strategy."""
        # Explicit CMS/vendor overrides
        if p.cms == "aem":                      return "aem"
        if p.cms == "shopify":                  return "client_filter"
        if p.cms == "salesforce_experience":    return "salesforce"
        if p.cms == "sitecore":                 return "spa"
        if p.cms == "drupal":                   return "dom"
        if p.cms == "wordpress":                return "dom"
        if p.cms == "google_devsite":           return "anchor_nav"

        # Pagination-based
        if p.pagination == "client_filter":     return "client_filter"
        if p.pagination == "anchor_nav":        return "anchor_nav"
        if p.pagination == "aem_json":          return "aem"
        if p.pagination == "spa":               return "spa"

        # Framework-based fallback
        if p.framework in ("react", "nextjs", "remix"):  return "spa"
        if p.framework == "angular":                     return "client_filter"
        if p.framework in ("vue", "nuxt"):               return "spa"

        # Vendor profiles with known strategies
        VENDOR_STRATEGY = {
            "google_cloud": "anchor_nav",
            "aws":          "client_filter",  # DOM card scraping + load-more pagination
            "adobe":        "client_filter",
            "microsoft":    "client_filter",
            "salesforce":   "client_filter",
            "shopify":      "client_filter",
            "siemens":      "client_filter",
            "schneider":    "dom",
        }
        if p.vendor_profile in VENDOR_STRATEGY:
            return VENDOR_STRATEGY[p.vendor_profile]

        return "dom"
