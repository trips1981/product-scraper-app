"""
config/settings.py — Central configuration for the Universal Product Scraper Streamlit app.
"""
import os
import json
from pathlib import Path

# ── Paths ───────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent.parent
CONFIG_DIR      = BASE_DIR / "config"
SNAPSHOT_DIR    = BASE_DIR / "picker_snapshots"
DB_PATH         = SNAPSHOT_DIR / "ci_history.db"
ENRICHMENT_CFG  = CONFIG_DIR / "enrichment_config.json"

SNAPSHOT_DIR.mkdir(exist_ok=True)

# ── Build version ───────────────────────────────────────────────────────────────
BUILD_VERSION = "2026-streamlit-v1"

# ── OpenAI ──────────────────────────────────────────────────────────────────────
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL     = "gpt-4o-mini"
OPENAI_ENRICH_MAX_WORKERS = 3

# ── Playwright ──────────────────────────────────────────────────────────────────
# Use "chromium" in Docker/server environments; "msedge" or "chrome" only on Windows desktop
BROWSER_CHANNEL  = os.getenv("BROWSER_CHANNEL", "chromium")
DEFAULT_HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"
PAGE_TIMEOUT_MS  = 120_000
IDLE_WAIT_MS     = 2_500

# ── Pagination ──────────────────────────────────────────────────────────────────
MAX_PAGINATION_ROUNDS = 50
LOAD_MORE_KEYWORDS = [
    "load more", "show more", "view more", "more products", "see more",
    "show 8 more", "show 12 more", "show 15 more", "show 20 more",
    "show 24 more", "load more products", "explore more", "continue",
    "see all", "show all", "load additional", "weitere laden", "mehr laden",
]

# ── CI ──────────────────────────────────────────────────────────────────────────
RENAME_SIMILARITY_THRESHOLD = 0.72

# ── Enrichment schema ───────────────────────────────────────────────────────────
_DEFAULT_ENRICHMENT_CONFIG = {
    "fields": [
        {"key": "product_type",        "label": "Product Type",        "aliases": ["product type", "type"]},
        {"key": "primary_category",    "label": "Primary Category",    "aliases": ["primary category", "category"]},
        {"key": "primary_use_case",    "label": "Primary Use Case",    "aliases": ["primary use case", "use case"]},
        {"key": "target_customer",     "label": "Target Customer",     "aliases": ["target customer", "customer", "audience"]},
        {"key": "technology_domain",   "label": "Technology Domain",   "aliases": ["technology domain", "domain", "tech domain"]},
        {"key": "short_description",   "label": "Short Description",   "aliases": ["short description", "description"]},
        {"key": "deployment_type",     "label": "Deployment Type",     "aliases": ["deployment type", "deployment"]},
        {"key": "supported_platforms", "label": "Supported Platforms", "aliases": ["supported platforms", "platforms"]},
        {"key": "pricing_model",       "label": "Pricing Model",       "aliases": ["pricing model", "pricing"]},
        {"key": "pricing_page_url",    "label": "Pricing Page URL",    "aliases": ["pricing page url", "pricing url"]},
    ]
}


def load_enrichment_config() -> dict:
    if ENRICHMENT_CFG.exists():
        try:
            cfg = json.loads(ENRICHMENT_CFG.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and isinstance(cfg.get("fields"), list):
                return cfg
        except Exception:
            pass
    ENRICHMENT_CFG.write_text(json.dumps(_DEFAULT_ENRICHMENT_CONFIG, indent=2), encoding="utf-8")
    return _DEFAULT_ENRICHMENT_CONFIG


def get_enrichment_fields() -> list[str]:
    return [f["key"] for f in load_enrichment_config()["fields"]]


def get_enrichment_labels() -> list[str]:
    return [f["label"] for f in load_enrichment_config()["fields"]]


def get_field_aliases() -> dict[str, list]:
    return {f["key"]: f["aliases"] for f in load_enrichment_config()["fields"]}
