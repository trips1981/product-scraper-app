"""
ai/enrichment.py — AI product enrichment pipeline.
Ported from universal_scraper_improved.py with streaming support for Streamlit.
"""
import json
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import httpx
from openai import OpenAI

from config.settings import (
    OPENAI_API_KEY, OPENAI_MODEL, OPENAI_ENRICH_MAX_WORKERS,
    get_enrichment_fields, get_enrichment_labels, get_field_aliases,
)
from utils.helpers import retry

logger = logging.getLogger(__name__)


# ── OpenAI client ──────────────────────────────────────────────────────────────

def _get_client() -> OpenAI:
    return OpenAI(
        api_key=OPENAI_API_KEY,
        http_client=httpx.Client(trust_env=True),
    )


# ── JSON/text parsing ──────────────────────────────────────────────────────────

def extract_json_safe(text: str) -> dict:
    if not text:
        return {}
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    for sc, ec in [('{', '}'), ('[', ']')]:
        idx = text.find(sc)
        if idx == -1:
            continue
        depth = 0
        for i, ch in enumerate(text[idx:], start=idx):
            if ch == sc:
                depth += 1
            elif ch == ec:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[idx:i + 1])
                    except json.JSONDecodeError:
                        break
    return {}


def normalize_enrichment(data: dict) -> dict:
    fields = get_enrichment_fields()
    base = {k: "unknown" for k in fields}
    if not isinstance(data, dict):
        return base
    for field in fields:
        val = data.get(field)
        if isinstance(val, str) and val.strip():
            base[field] = val.strip()
        elif isinstance(val, list):
            base[field] = ", ".join(str(v) for v in val)
    return base


def parse_freehand(text: str) -> dict:
    if not text:
        return {}
    result: dict = {}
    aliases = get_field_aliases()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        low   = line.lower()
        colon = line.find(":")
        if colon == -1:
            continue
        value = line[colon + 1:].strip()
        if not value:
            continue
        for fld, alts in aliases.items():
            if any(a in low[:colon] for a in alts):
                if fld not in result:
                    result[fld] = value
                break
    fields = get_enrichment_fields()
    if len(result) < 3 and len(lines) >= 5:
        for i, fld in enumerate(fields[:5]):
            if i < len(lines):
                val = lines[i].split(":", 1)[-1].strip()
                if val:
                    result.setdefault(fld, val)
    return result


def derive_high_signal_enrichment(base: dict) -> dict:
    pt       = (base.get("product_type") or "").lower()
    domain   = (base.get("technology_domain") or "").lower()
    customer = (base.get("target_customer") or "").lower()
    is_api      = "api" in pt
    is_platform = "platform" in pt
    is_ent      = "enterprise" in customer
    complexity  = "High" if is_api else "Medium" if is_platform else "Low"
    pii_kws     = {"identity", "fraud", "authentication", "payments", "kyc"}
    return {
        "integration_complexity":     complexity,
        "time_to_first_value":        "Hours" if is_api else "Days",
        "developer_experience_level": "Moderate" if is_api else "Low",
        "data_sensitivity_level":     "PII" if any(k in domain for k in pii_kws) else "Metadata",
        "sales_motion":               "Sales-led" if is_ent else "PLG",
    }


def compute_adoption_friction(h: dict) -> int:
    s = {"Low": 10, "Medium": 30, "High": 60}.get(h.get("integration_complexity", ""), 30)
    s += 20 if h.get("developer_experience_level") == "Moderate" else 10
    return min(s, 100)


def compute_enterprise_readiness(h: dict) -> int:
    s = 20
    s += 30 if h.get("data_sensitivity_level") == "PII" else 10
    s += 30 if h.get("sales_motion") == "Sales-led" else 15
    return min(s, 100)


# ── Per-item enrichment ────────────────────────────────────────────────────────

def enrich_one(item: dict) -> dict:
    """Enrich a single product item via OpenAI. Returns enriched dict."""
    fields = get_enrichment_fields()
    fl     = ", ".join(fields)
    SYS    = (f"You are a B2B product intelligence analyst. "
              f"Return ONLY a valid JSON object. Keys: {fl}. "
              f"Values: concise strings. Use 'unknown' if unsure.")
    USR    = f"Product name: {item['name']}\nProduct URL: {item['link']}\n\nReturn the JSON."

    client = _get_client()

    def _call():
        return client.chat.completions.create(
            model=OPENAI_MODEL, temperature=0.1, timeout=30,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYS},
                      {"role": "user",   "content": USR}],
        )

    try:
        resp = retry(_call, attempts=3, delay=2.0)
        text = resp.choices[0].message.content or ""
        logger.debug(f"[ai] response: {text[:200]}")
        raw  = extract_json_safe(text) or parse_freehand(text)
    except Exception as exc:
        logger.warning(f"[ai] enrich_one error: {exc}")
        raw = {}

    base = normalize_enrichment(raw)
    high = derive_high_signal_enrichment(base)
    return {
        "name": item["name"],
        "link": item["link"],
        **base,
        **high,
        "adoption_friction":    compute_adoption_friction(high),
        "enterprise_readiness": compute_enterprise_readiness(high),
    }


# ── Batch enrichment ───────────────────────────────────────────────────────────

def enrich_batch(
    items: list[dict],
    on_item_done: Optional[Callable[[int, dict], None]] = None,
    max_workers: int = OPENAI_ENRICH_MAX_WORKERS,
) -> list[dict]:
    """
    Enrich a batch of items in parallel.

    on_item_done(index, enriched_item) — called after each item completes.
    Useful for Streamlit progress updates (call st.session_state updates here).
    """
    if not OPENAI_API_KEY:
        logger.error("[ai] OPENAI_API_KEY not set — skipping enrichment")
        return items

    results: list[dict] = [{}] * len(items)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(enrich_one, item): idx
            for idx, item in enumerate(items)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.warning(f"[ai] item {idx} failed: {exc}")
                result = {"name": items[idx]["name"], "link": items[idx]["link"]}
            results[idx] = result
            if on_item_done:
                try:
                    on_item_done(idx, result)
                except Exception:
                    pass

    return results


# ── Change intelligence ────────────────────────────────────────────────────────

def analyze_change_intelligence(change_text: str, page_content: str, link: str) -> str:
    """Generate AI intelligence for a detected CI change."""
    SYS = """You are an elite corporate change intelligence analyst.
Determine page type (News/Media, Press Release, SaaS/Software, Corporate, E-commerce, Other).

For ALL pages:
- 3-line Summary
- Strategic Signal
- Risk Level (Low/Medium/High)
- Competitive Signal (None/Weak/Strong)
- Regulatory Exposure (None/Emerging/Active)
- Reputation Impact (Low/Medium/High)
- Revenue Impact (Low/Medium/High)
- Industry Category

For SaaS/Product also: Feature Change, Pricing Change, ICP Shift, GTM Signal.
For News/Press also: Topic Category, Region, Political Sensitivity, Macroeconomic Signal.
Return structured bullet points only.""".strip()

    USR = (f"Detected Change: {change_text}\n"
           f"Page URL: {link}\n"
           f"Content:\n{page_content[:8000]}")

    client = _get_client()

    def _call():
        return client.chat.completions.create(
            model=OPENAI_MODEL, temperature=0.2,
            messages=[{"role": "system", "content": SYS},
                      {"role": "user",   "content": USR}],
        )

    try:
        return retry(_call, attempts=3).choices[0].message.content or "No response."
    except Exception as exc:
        return f"AI Change Analysis Error: {exc}"
