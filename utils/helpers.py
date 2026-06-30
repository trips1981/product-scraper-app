"""
utils/helpers.py — Shared utility functions.
"""
import re
import time
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def dedup(items: list[dict]) -> list[dict]:
    """Deduplicate items by normalised (name, link) key."""
    seen, unique = set(), []
    for item in items:
        key = (
            re.sub(r"\s+", " ", (item.get("name") or "").strip().lower()),
            (item.get("link") or "").strip().rstrip("/").lower(),
        )
        if key not in seen and key[0]:
            seen.add(key)
            unique.append(item)
    return unique


def extract_domain(url: str) -> str:
    """Return bare domain (no www.) from a URL."""
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return "unknown"


def retry(fn, attempts: int = 3, delay: float = 2.0, backoff: float = 2.0):
    """Retry a callable up to `attempts` times with exponential back-off."""
    last_exc = None
    wait = delay
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                logger.debug(f"[retry] {attempt}/{attempts} failed: {exc} — retrying in {wait}s")
                time.sleep(wait)
                wait *= backoff
    raise last_exc


def clean_name(text: str) -> str:
    """Normalise whitespace in a product name."""
    return re.sub(r"\s+", " ", (text or "").strip())


def is_valid_product_name(name: str) -> bool:
    """Return True if `name` looks like a real product name."""
    if not name:
        return False
    n = name.strip()
    if len(n) < 3 or len(n) > 200:
        return False
    if n.lower() in {"read more", "learn more", "explore", "view all", "see all",
                     "get started", "sign up", "log in", "discover", "find out more",
                     "more info", "contact us", "back to homepage", "support and community"}:
        return False
    return True


def build_snapshot_items(items: list[dict], compute_hash_fn) -> list[dict]:
    return [
        {"name": i["name"], "link": i["link"], "hash": compute_hash_fn(i["name"], i["link"])}
        for i in items
    ]
