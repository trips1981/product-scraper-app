"""
scrapers/orchestrator.py — Smart scraper orchestrator.

Uses the ArchitectureDetector to classify the site, then dispatches to the
appropriate scraper.  This replaces SmartManualApp.universal_auto_scrape().
"""
import logging
from architecture.detector import ArchitectureDetector, ArchitectureProfile
from scrapers.dom_scraper import DOMScraper
from scrapers.spa_scraper import SPAScraper
from scrapers.client_filter_scraper import ClientFilterScraper
from scrapers.anchor_nav_scraper import AnchorNavScraper
from scrapers.aem_scraper import AEMScraper

logger = logging.getLogger(__name__)


class ScraperOrchestrator:
    """
    Decision engine:
        Detect architecture → Choose scraper → Execute → Return items.
    """

    def __init__(self):
        self._detector = ArchitectureDetector()
        self._scrapers = {
            "dom":           DOMScraper(),
            "spa":           SPAScraper(),
            "client_filter": ClientFilterScraper(),
            "anchor_nav":    AnchorNavScraper(),
            "aem":           AEMScraper(),
            "shopify":       ClientFilterScraper(),
            "salesforce":    SPAScraper(),
            "sitecore":      SPAScraper(),
            "drupal":        DOMScraper(),
            "wordpress":     DOMScraper(),
        }

    def detect(self, page, url: str) -> ArchitectureProfile:
        """Run detection only (no scraping)."""
        return self._detector.detect(page, url)

    def scrape(self, page, url: str,
               force_strategy: str | None = None) -> tuple[list[dict], ArchitectureProfile]:
        """
        Full pipeline: detect → scrape → return (items, profile).

        force_strategy overrides auto-detection (useful for manual override).
        """
        profile = self._detector.detect(page, url)

        strategy = force_strategy or profile.scraper_strategy
        logger.info(f"[orchestrator] Strategy: {strategy} for {url[:60]}")

        scraper = self._scrapers.get(strategy) or self._scrapers["dom"]
        items   = scraper.scrape(page, url)

        return items, profile

    def scrape_with_strategy(self, page, url: str, strategy: str) -> list[dict]:
        """Scrape with a specific known strategy (skips detection)."""
        scraper = self._scrapers.get(strategy) or self._scrapers["dom"]
        return scraper.scrape(page, url)
