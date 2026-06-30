"""
scrapers/base.py — Abstract base scraper.
"""
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """All scrapers implement this interface."""

    @abstractmethod
    def scrape(self, page, url: str) -> list[dict]:
        """
        Scrape products from the current `page`.
        Returns list of {"name": str, "link": str}.
        """
        ...

    def name(self) -> str:
        return self.__class__.__name__
