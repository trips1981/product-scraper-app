"""
app.py — Universal Product Scraper · Streamlit Application
Build: 2026-streamlit-v1

Launch with:
    streamlit run app.py

Architecture:
    app.py                   — entry point, session state, action dispatch
    components/sidebar.py    — sidebar UI
    components/tabs.py       — main content tabs
    browser/manager.py       — Playwright BrowserContext pool
    browser/js_payloads.py   — JS snippets injected into pages
    scrapers/orchestrator.py — smart scraper dispatch
    architecture/detector.py — site architecture detection
    pagination/engine.py     — universal pagination engine
    ci/monitor.py            — SQLite CI monitoring (unchanged from original)
    ai/enrichment.py         — OpenAI enrichment pipeline
    exports/csv_exporter.py  — CSV serialisation
    config/settings.py       — central configuration
    utils/helpers.py         — shared utilities
    utils/logging_config.py  — in-memory log capture
"""
import sys
import os
import logging


import streamlit as st

# ── Path setup ─────────────────────────────────────────────────────────────────
# Allow imports from any sub-package without installing as a package.
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# ── Logging ────────────────────────────────────────────────────────────────────
from utils.logging_config import setup_logging
setup_logging(logging.DEBUG)
logger = logging.getLogger(__name__)

# ── Page config (MUST be first Streamlit call) ─────────────────────────────────
st.set_page_config(
    page_title="Universal Product Scraper",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Imports ────────────────────────────────────────────────────────────────────
from config.settings import BUILD_VERSION, OPENAI_API_KEY
from browser.manager import BrowserManager
from browser.js_payloads import MANUAL_PICK_JS, MANUAL_SCRAPE_JS, RESET_MANUAL_JS
from scrapers.orchestrator import ScraperOrchestrator
from pagination.engine import PaginationEngine
from ci.monitor import generate_changes, compute_hash, get_change_history, get_all_domains
from ai.enrichment import enrich_batch, analyze_change_intelligence
from utils.helpers import extract_domain, build_snapshot_items, dedup
from components.sidebar import render_sidebar
from components.tabs import (
    render_products_tab, render_ai_tab, render_ci_tab,
    render_history_tab, render_logs_tab, render_architecture_tab, render_debug_tab,
)

# ── Workflow Recorder layer ────────────────────────────────────────────────────
from database.db import init_db
from database.repository import (
    get_or_create_company, get_active_workflow,
    save_workflow, save_snapshot, get_previous_snapshot,
)
from workflow.recorder import WorkflowRecorder
from workflow.player import WorkflowPlayer, StepResult
from comparison.engine import compare_snapshots

# Initialise workflow DB schema once at startup
# Run in a try/except so a DB hiccup never prevents the main scraper from loading.
# init_db() is idempotent — safe to call on every cold-start.
try:
    init_db()
    logger.info("[startup] workflow DB initialised OK")
except Exception as _db_exc:
    logger.warning("[startup] workflow DB init failed (will retry on first use): %s", _db_exc)
    # Retry once — helps when picker_snapshots/ dir was just created and the
    # file system needs a moment to settle (e.g. first run after install).
    try:
        from database.db import WORKFLOW_DB
        WORKFLOW_DB.parent.mkdir(parents=True, exist_ok=True)
        init_db()
        logger.info("[startup] workflow DB initialised OK (retry)")
    except Exception as _db_exc2:
        logger.error("[startup] workflow DB unavailable: %s", _db_exc2)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, [data-testid="stAppViewContainer"] {
    font-family: 'Inter', sans-serif !important;
    background: linear-gradient(135deg, #f0f4ff 0%, #faf0ff 50%, #fff0f6 100%) !important;
}

[data-testid="stSidebar"] > div:first-child {
    background: linear-gradient(180deg, #3730a3 0%, #6d28d9 60%, #9333ea 100%) !important;
}

/* Force ALL sidebar text white - wildcard catches every element */
[data-testid="stSidebar"] * {
    color: #ffffff !important;
}

/* Override inputs back to readable */
[data-testid="stSidebar"] input {
    background: rgba(255,255,255,0.18) !important;
    color: #ffffff !important;
    border: 1px solid rgba(255,255,255,0.45) !important;
    border-radius: 8px !important;
}
[data-testid="stSidebar"] input::placeholder { color: rgba(255,255,255,0.6) !important; }

/* Sidebar button styling */
[data-testid="stSidebar"] button {
    background: rgba(255,255,255,0.15) !important;
    border: 1.5px solid rgba(255,255,255,0.4) !important;
    border-radius: 10px !important;
    width: 100% !important;
}
[data-testid="stSidebar"] button:hover {
    background: rgba(255,255,255,0.3) !important;
}

/* Select box in sidebar */
[data-testid="stSidebar"] select,
[data-testid="stSidebar"] [data-baseweb="select"] * {
    background: rgba(255,255,255,0.15) !important;
    color: #ffffff !important;
}

[data-testid="stHeader"] {
    background: rgba(255,255,255,0.8) !important;
    backdrop-filter: blur(10px) !important;
    border-bottom: 1px solid rgba(99,102,241,0.15) !important;
}

div.stButton > button {
    background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
    border: none !important;
    border-radius: 10px !important;
    color: #ffffff !important;
}
div.stButton > button * { color: #ffffff !important; }
div.stButton > button:hover {
    background: linear-gradient(135deg, #4f46e5, #7c3aed) !important;
    box-shadow: 0 4px 16px rgba(99,102,241,0.4) !important;
}

div[data-testid="metric-container"] {
    background: linear-gradient(135deg, #ffffff, #f5f3ff) !important;
    border: 1px solid rgba(99,102,241,0.2) !important;
    border-radius: 14px !important;
    padding: 14px !important;
    box-shadow: 0 2px 10px rgba(99,102,241,0.08) !important;
}
div[data-testid="metric-container"] label { color: #6366f1 !important; font-weight: 600 !important; }
div[data-testid="metric-container"] [data-testid="stMetricValue"] { color: #1e1b4b !important; font-weight: 700 !important; }

.stTabs [data-baseweb="tab-list"] {
    background: rgba(255,255,255,0.9) !important;
    border-radius: 12px !important;
    padding: 4px !important;
    border: 1px solid rgba(99,102,241,0.15) !important;
    gap: 4px !important;
}
.stTabs [data-baseweb="tab"] { border-radius: 8px !important; color: #6b7280 !important; }
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
    color: #ffffff !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] * { color: #ffffff !important; }

.stTextInput input {
    background: #ffffff !important;
    border: 1.5px solid #e0e7ff !important;
    border-radius: 10px !important;
    color: #1e1b4b !important;
}
.stTextInput input:focus {
    border-color: #6366f1 !important;
    box-shadow: 0 0 0 3px rgba(99,102,241,0.15) !important;
}

.stDataFrame { border: 1px solid rgba(99,102,241,0.18) !important; border-radius: 12px !important; }
div[data-testid="stNotification"], div.stAlert { border-radius: 10px !important; }
hr { border-color: rgba(99,102,241,0.12) !important; }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #f0f4ff; }
::-webkit-scrollbar-thumb { background: linear-gradient(#6366f1, #a855f7); border-radius: 3px; }
div.stDownloadButton > button {
    background: linear-gradient(135deg, #0ea5e9, #6366f1) !important;
    color: #ffffff !important;
}
div.stDownloadButton > button * { color: #ffffff !important; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# SESSION STATE INITIALISATION
# ══════════════════════════════════════════════════════════════════════════════

def _init_state():
    defaults = {
        "url":                    "",
        "headless":               False,
        "channel":                "chromium",
        "browser_ready":          False,
        "page_ready":             False,
        "scraped_items":          [],
        "ai_enriched_items":      [],
        "detected_changes":       [],
        "ai_change_intelligence": [],
        "architecture_profile":   None,
        "status":                 "Idle",
        "page_screenshot":        None,
        "page_title":             "",
        "enrichment_progress":    0,
        # ── Workflow Recorder ──────────────────────────────────────────────────
        "workflow_recording":     False,   # True while recording
        "recorded_steps":         [],      # list of step dicts accumulated during recording
        "diff_summary":           None,    # DiffSummary after Auto Run
        "pending_save_snapshot":  False,   # True when user clicked "Save snapshot"
        "last_workflow_id":       None,    # id of the workflow used in last Auto Run
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # Singletons -- survive Streamlit reruns via session_state
    # BrowserManager is created but NOT started here -- Playwright starts lazily
    # when the user clicks Open Page.  Starting eagerly caused a 60s hang on
    # Windows Store Python because orphaned threads from failed attempts block
    # the next _ready.wait(timeout=60).
    if "browser" not in st.session_state:
        st.session_state["browser"]       = BrowserManager()
        st.session_state["browser_ready"] = False
        st.session_state.pop("browser_error", None)

    if "orchestrator" not in st.session_state:
        st.session_state["orchestrator"] = ScraperOrchestrator()

    if "pagination_engine" not in st.session_state:
        st.session_state["pagination_engine"] = PaginationEngine()

    if "workflow_recorder" not in st.session_state:
        st.session_state["workflow_recorder"] = WorkflowRecorder()


_init_state()
bm:    BrowserManager      = st.session_state["browser"]
orch:  ScraperOrchestrator  = st.session_state["orchestrator"]
pag:   PaginationEngine     = st.session_state["pagination_engine"]
rec:   WorkflowRecorder     = st.session_state["workflow_recorder"]
state  = st.session_state


# ══════════════════════════════════════════════════════════════════════════════
# CI HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _run_ci():
    """Run CI monitoring on current scraped_items and update state."""
    items = state["scraped_items"]
    url   = state["url"]
    if not items or not url:
        return
    domain  = extract_domain(url)
    snap    = build_snapshot_items(items, compute_hash)
    changes = generate_changes(domain, snap)
    logger.info(f"[ci] {len(changes)} changes for {domain}")

    state["detected_changes"] = []
    for c in changes:
        if c.strip().startswith(("NEW:", "REMOVED:", "RENAMED:")):
            import re
            m    = re.search(r'(?:NEW|REMOVED|RENAMED):\s+"?([^"→\n]+)', c)
            name = m.group(1).strip() if m else ""
            link = next((i["link"] for i in items if i["name"] == name), url)
            state["detected_changes"].append((c, link))


# ══════════════════════════════════════════════════════════════════════════════
# ACTION HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def handle_open_page():
    url = state["url"].strip()
    if not url:
        st.warning("Enter a URL first.")
        return
    with st.spinner("Opening browser…"):
        try:
            # Lazy-start Playwright on first use
            if not bm.is_ready:
                bm.start(channel=state.get("channel", "chromium"))
                state["browser_ready"] = True
            msg = bm.navigate(url, headless=state["headless"])
            state["page_ready"] = True
            state["status"]     = msg
            logger.info(f"[action] open_page: {msg}")
            # Capture screenshot for in-app preview
            try:
                state["page_screenshot"] = bm.take_screenshot(full_page=False)
                state["page_title"]      = bm.get_page_title()
            except Exception as ss_exc:
                logger.warning(f"[action] screenshot failed: {ss_exc}")
                state["page_screenshot"] = None
            st.success(msg)
            # Record navigation step if recording is active
            if state.get("workflow_recording"):
                rec.record_navigate(url, notes=f"Navigate to {url}")
                state["recorded_steps"] = rec.get_steps()
        except Exception as exc:
            import traceback as _tb
            err      = str(exc)
            full_err = _tb.format_exc()
            state["status"]     = f"Error: {err}"
            state["page_ready"] = False
            logger.error(f"[action] open_page error:\n{full_err}")
            st.error(err)
            with st.expander("Traceback", expanded=False):
                st.code(full_err, language="python")


def handle_expand_pages():
    if not bm.has_page:
        st.warning("Open a page first.")
        return
    with st.spinner("Running universal pagination expansion…"):
        try:
            rounds = pag.expand(bm.page, strategy="auto")
            rounds += pag.expand_tabs_and_accordions(bm.page)
            msg = f"Expansion complete ({rounds} extra rounds)"
            state["status"] = msg
            logger.info(f"[action] expand_pages: {msg}")
            st.success(msg)
        except Exception as exc:
            logger.error(f"[action] expand_pages error: {exc}")
            st.error(str(exc))


def _detect_az_page(page) -> bool:
    """
    Returns True if the current page looks like an A–Z catalogue list
    (many <li> items with short text, few product cards).
    """
    result = page.evaluate("""
    () => {
        // Count plain <li> text items (single-line, short, no children)
        const lis = [...document.querySelectorAll('li')];
        const plainLis = lis.filter(li => {
            const t = (li.innerText || li.textContent || '').trim();
            return t.length >= 4 && t.length <= 80 && !t.includes('\\n') && li.children.length <= 1;
        });
        // Count product cards
        const cards = document.querySelectorAll(
            'article, [class*="card"], [class*="Card"], [class*="product-card"], [class*="tile"]'
        );
        return { plainLis: plainLis.length, cards: cards.length };
    }
    """) or {}
    plain_lis = result.get("plainLis", 0)
    cards     = result.get("cards", 0)
    # A–Z page: many list items, few or no cards
    return plain_lis >= 20 and cards < 10


def handle_auto_scrape():
    url = state.get("url", "").strip()
    if not url:
        st.warning("Enter a URL first.")
        return

    # Auto-open page if not already open
    if not bm.has_page:
        state["status"] = "Opening page…"
        progress_bar = st.progress(0, text="Opening page…")
        try:
            bm.navigate(url, headless=state.get("headless", False))
            state["browser_ready"] = True
            state["page_ready"]    = True
            try:
                state["page_screenshot"] = bm.take_screenshot(full_page=False)
                state["page_title"]      = bm.get_page_title()
            except Exception:
                pass
        except Exception as exc:
            st.error(f"Could not open page: {exc}")
            progress_bar.empty()
            return
    else:
        progress_bar = st.progress(0, text="Detecting page type…")

    state["status"] = "Scraping…"
    try:
        progress_bar.progress(10, text="Detecting page type…")

        # Auto-detect: A–Z list vs product card grid
        is_az = _detect_az_page(bm.page)

        if is_az:
            progress_bar.progress(20, text="A–Z catalogue detected — collecting names…")
            logger.info("[action] auto_scrape: A–Z page detected, switching to catalogue mode")
            _run_az_catalogue(progress_bar)
        else:
            progress_bar.progress(20, text="Card grid detected — scraping products…")
            items, profile = orch.scrape(bm.page, url)
            progress_bar.progress(80, text=f"Got {len(items)} items, running CI…")
            state["scraped_items"]        = items
            state["architecture_profile"] = profile
            _run_ci()
            progress_bar.progress(100, text="Done")
            msg = f"Smart scrape complete — {len(items)} items ({profile.scraper_strategy})"
            state["status"] = msg
            logger.info(f"[action] auto_scrape: {msg}")
            st.success(msg)

    except Exception as exc:
        logger.error(f"[action] auto_scrape error: {exc}", exc_info=True)
        state["status"] = f"Error: {exc}"
        st.error(f"Scrape failed: {exc}")
    finally:
        progress_bar.empty()


def _run_az_catalogue(progress_bar=None):
    """
    A–Z catalogue logic (previously handle_scrape_schneider).
    Collects product names from <li> elements, resolves each to a URL via site search.
    Called either directly or from handle_auto_scrape when an A–Z page is detected.
    """
    import re
    from urllib.parse import quote, urlparse

    current_url = state.get("url", "").strip()
    parsed      = urlparse(current_url)
    base_url    = f"{parsed.scheme}://{parsed.netloc}"
    hostname    = parsed.netloc

    BLACKLIST = {
        "cookie","login","register","support","privacy","terms","search",
        "menu","home","products","software","solutions","services",
        "company","window","uxa","opens in new window","filter","range","brand",
    }

    def ok(t):
        t = (t or "").strip()
        if len(t) < 4 or len(t) > 80: return False
        if re.fullmatch(r"[A-Z]", t):  return False
        if any(b in t.lower() for b in BLACKLIST): return False
        return not t.isdigit()

    def _collect_names(pg):
        pg.wait_for_timeout(4_000)
        for _ in range(4):
            pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            pg.wait_for_timeout(800)
        texts = []
        for li in pg.query_selector_all("li"):
            try:
                t = li.inner_text() or ""
            except Exception:
                continue
            if "\n" not in t and ok(t):
                texts.append(t.strip())
        return sorted(set(texts))

    with st.spinner("Collecting product names from page…"):
        names = bm._run(_collect_names, bm._page_r)

    if not names:
        st.warning(
            "No product names found. "
            "Make sure you are on an A–Z catalogue listing page."
        )
        return

    st.info(f"Found **{len(names)} product names** — resolving URLs…")

    results    = []
    unresolved = []
    prog = progress_bar or st.progress(0, text=f"Resolving 0 / {len(names)}…")

    def _resolve(pg, search_url):
        pg.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
        pg.wait_for_timeout(2_500)
        first_same_domain = None
        for a in pg.query_selector_all("a"):
            try:
                href = a.get_attribute("href") or ""
            except Exception:
                continue
            if any(p in href for p in ("/product", "/products/", "/catalog/", "/item/")):
                return href
            if not first_same_domain and hostname in href:
                first_same_domain = href
        return first_same_domain

    for i, name in enumerate(names, 1):
        pct  = int(20 + (i / len(names)) * 75)
        prog.progress(pct, text=f"Resolving {i}/{len(names)}: {name}")
        try:
            search_url = f"{base_url}/search?q={quote(name)}"
            href = bm._run(_resolve, bm._page_r, search_url)
            if href:
                full_href = href if href.startswith("http") else base_url + href
                results.append({"name": name, "link": full_href})
            else:
                unresolved.append(name)
        except Exception as e:
            logger.warning(f"[az_catalogue] resolve failed for {name!r}: {e}")
            unresolved.append(name)

    state["scraped_items"] = results
    try:
        state["page_screenshot"] = bm.take_screenshot(full_page=False)
    except Exception:
        pass
    _run_ci()

    msg = f"A–Z catalogue complete — {len(results)} resolved"
    if unresolved:
        msg += f", {len(unresolved)} unresolved"
    state["status"] = msg
    logger.info(f"[action] az_catalogue: {msg}")
    st.success(msg)
    if unresolved:
        st.caption(
            f"Could not resolve URLs for: {', '.join(unresolved[:10])}"
            + (" …and more" if len(unresolved) > 10 else "")
        )


def handle_manual_pick():
    if not bm.has_page:
        st.warning("Open a page first.")
        return
    try:
        bm._run(bm._page_r.evaluate, MANUAL_PICK_JS)
        state["status"] = "Manual picker active — click any element in the browser"
        st.info("✅ **Manual picker activated!**  "
                "Click any product element in the browser window.  "
                "Then click **Manual Scrape** here to collect the results.")
        logger.info("[action] manual_pick: activated")
    except Exception as exc:
        logger.error(f"[action] manual_pick error: {exc}")
        st.error(str(exc))


def handle_manual_scrape():
    if not bm.has_page:
        st.warning("Open a page first.")
        return
    try:
        new_items = bm._run(bm._page_r.evaluate, MANUAL_SCRAPE_JS) or []
        existing  = {(i["name"], i["link"]) for i in state["scraped_items"]}
        added     = 0
        for item in new_items:
            k = (item.get("name", ""), item.get("link", ""))
            if k not in existing and k[0]:
                state["scraped_items"].append(item)
                existing.add(k)
                added += 1
        _run_ci()
        msg = f"Manual scrape — added {added} items (total {len(state['scraped_items'])})"
        state["status"] = msg
        logger.info(f"[action] manual_scrape: {msg}")
        st.success(msg)
    except Exception as exc:
        logger.error(f"[action] manual_scrape error: {exc}")
        st.error(str(exc))


def handle_reset_picker():
    if not bm.has_page:
        return
    try:
        bm._run(bm._page_r.evaluate, RESET_MANUAL_JS)
        st.success("Manual picker reset (scraped results preserved)")
        logger.info("[action] reset_picker")
    except Exception as exc:
        st.error(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW RECORDER HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

def handle_start_recording():
    url = state.get("url", "").strip()
    rec.start(url=url)
    state["workflow_recording"] = True
    state["recorded_steps"]     = []
    state["diff_summary"]       = None
    logger.info("[action] start_recording")
    st.info("Recording started — every scrape action will be captured as a semantic step.")


def handle_pause_recording():
    rec.pause()
    state["workflow_recording"] = False
    logger.info("[action] pause_recording")
    st.info("Recording paused.")


def handle_stop_recording():
    rec.stop()
    state["workflow_recording"] = False
    state["recorded_steps"]     = rec.get_steps()
    logger.info("[action] stop_recording: %d steps", len(state["recorded_steps"]))
    st.success(f"Recording stopped — {len(state['recorded_steps'])} steps captured.")


def handle_save_workflow():
    steps = state.get("recorded_steps", [])
    if not steps:
        st.warning("No steps recorded. Run a scrape while recording is active first.")
        return
    url = state.get("url", "").strip()
    if not url:
        st.warning("No URL set.")
        return
    try:
        company = get_or_create_company(url)
        wf      = save_workflow(company.id, steps, name=f"Workflow for {company.domain}")
        rec.discard()
        state["recorded_steps"]     = []
        state["workflow_recording"] = False
        state["last_workflow_id"]   = wf.id
        msg = f"Workflow v{wf.version} saved — {len(wf.steps)} steps for {company.domain}"
        state["status"] = msg
        logger.info("[action] save_workflow: %s", msg)
        st.success(msg)
    except Exception as exc:
        logger.error("[action] save_workflow error: %s", exc, exc_info=True)
        st.error(f"Save failed: {exc}")


def handle_discard_recording():
    rec.discard()
    state["workflow_recording"] = False
    state["recorded_steps"]     = []
    logger.info("[action] discard_recording")
    st.info("Recording discarded.")


def handle_auto_run_workflow():
    """
    Core Auto Run loop:
      1. Load saved workflow for this URL
      2. Open page (if not already open)
      3. Replay all navigation/expansion steps
      4. Run auto scraper
      5. Compare against previous snapshot
      6. Show diff dashboard — ask user to save
    """
    url = state.get("url", "").strip()
    if not url:
        st.warning("Enter a URL first.")
        return

    progress = st.progress(0, text="Looking up saved workflow...")
    try:
        company = get_or_create_company(url)
        wf      = get_active_workflow(company.id)
        if not wf:
            st.warning("No saved workflow found for this URL. Scrape manually first.")
            progress.empty()
            return

        logger.info("[auto_run] workflow v%s, %d steps", wf.version, len(wf.steps))
        progress.progress(5, text=f"Workflow v{wf.version} found ({len(wf.steps)} steps) — opening page...")

        # Open page if needed
        if not bm.has_page:
            bm.navigate(url, headless=state.get("headless", False))
            state["browser_ready"] = True
            state["page_ready"]    = True
            try:
                state["page_screenshot"] = bm.take_screenshot(full_page=False)
                state["page_title"]      = bm.get_page_title()
            except Exception:
                pass

        progress.progress(15, text="Replaying workflow steps...")

        # Filter to only replayable navigation steps
        NAV_ACTIONS = {
            "navigate", "click_menu", "expand_accordion", "expand_tree",
            "open_tab", "scroll", "search", "pagination_next", "filter_apply",
            "ignore_popup", "close_cookie_banner", "hover", "tab_switch",
            "browser_back", "browser_forward", "expand_view_all", "wait",
        }
        steps_to_play = [s.to_dict() for s in wf.steps if s.action_type in NAV_ACTIONS]

        # Progress callback for each step
        _total_nav = len(steps_to_play) or 1

        def _on_step_done(idx, report):
            pct = 15 + int((idx + 1) / _total_nav * 40)
            icon = "✓" if report.result in ("success", "recovered") else "✗"
            progress.progress(min(pct, 55),
                              text=f"{icon} Step {idx+1}/{_total_nav}: {report.action_type}")

        player   = WorkflowPlayer(page_timeout_ms=5_000, on_step_done=_on_step_done)
        playback = player.play(
            page=bm.page,
            steps=steps_to_play,
            workflow_id=wf.id,
            company_domain=company.domain,
        )

        if playback.failed:
            st.warning(
                f"{playback.failed} step(s) failed during replay — "
                "results may be incomplete. Consider updating the workflow."
            )
        if playback.recovered:
            st.info(f"{playback.recovered} step(s) self-healed via fallback selector.")

        progress.progress(60, text=f"Replay done — scraping products...")

        # Run scraper
        items, profile = orch.scrape(bm.page, url)
        state["scraped_items"]        = items
        state["architecture_profile"] = profile
        progress.progress(85, text=f"Got {len(items)} products — comparing with previous run...")

        # Compare
        prev_snap = get_previous_snapshot(company.id)
        if prev_snap and prev_snap.products:
            prev_list = [{"name": p.product_name, "link": p.product_url}
                         for p in prev_snap.products]
            diff = compare_snapshots(items, prev_list)
        else:
            from comparison.engine import DiffSummary, ProductDiff, DiffStatus
            diff = DiffSummary(
                total_current=len(items), total_previous=0,
                unchanged=0, new=len(items), removed=0,
                renamed=0, url_changed=0, updated=0,
                diffs=[ProductDiff(DiffStatus.NEW, i.get("name", ""), i.get("link", ""))
                       for i in items],
            )

        state["diff_summary"]     = diff
        state["last_workflow_id"] = wf.id
        _run_ci()
        progress.progress(100, text="Done")

        msg = (
            f"Auto Run complete — {len(items)} products · "
            f"{diff.new} new · {diff.removed} removed · {diff.renamed} renamed"
        )
        state["status"] = msg
        logger.info("[action] auto_run: %s", msg)
        st.success(msg)

    except Exception as exc:
        logger.error("[action] auto_run error: %s", exc, exc_info=True)
        state["status"] = f"Auto Run error: {exc}"
        st.error(f"Auto Run failed: {exc}")
    finally:
        try:
            progress.empty()
        except Exception:
            pass


def handle_pending_save_snapshot():
    """Save current scraped items as new snapshot baseline."""
    items = state.get("scraped_items", [])
    url   = state.get("url", "").strip()
    if not items or not url:
        state["pending_save_snapshot"] = False
        return
    try:
        company = get_or_create_company(url)
        snap    = save_snapshot(company.id, items, workflow_id=state.get("last_workflow_id"))
        state["pending_save_snapshot"] = False
        state["diff_summary"]          = None
        msg = f"Snapshot saved — {snap.product_count} products as new baseline for {company.domain}"
        state["status"] = msg
        logger.info("[action] save_snapshot: %s", msg)
        st.success(msg)
    except Exception as exc:
        logger.error("[action] save_snapshot error: %s", exc, exc_info=True)
        state["pending_save_snapshot"] = False
        st.error(f"Snapshot save failed: {exc}")


def handle_ai_enrich():
    items = state["scraped_items"]
    if not items:
        st.warning("No items to enrich. Scrape first.")
        return
    if not OPENAI_API_KEY:
        st.error("OPENAI_API_KEY environment variable not set.")
        return

    state["ai_enriched_items"] = []
    prog = st.progress(0, text=f"Enriching 0 / {len(items)}")
    done_count = [0]

    def on_done(idx, result):
        done_count[0] += 1
        prog.progress(done_count[0] / len(items),
                      text=f"Enriching {done_count[0]} / {len(items)}")

    with st.spinner("Running AI enrichment…"):
        enriched = enrich_batch(items, on_item_done=on_done)

    state["ai_enriched_items"] = enriched
    prog.empty()
    msg = f"AI enrichment complete — {len(enriched)} items"
    state["status"] = msg
    logger.info(f"[action] ai_enrich: {msg}")
    st.success(msg)


def handle_analyze_changes():
    changes = state["detected_changes"]
    if not changes:
        st.warning("No changes detected. Run a scrape first.")
        return

    state["ai_change_intelligence"] = []
    prog    = st.progress(0, text="Preparing change intelligence…")
    page    = bm.page
    inputs  = []

    def _fetch_page_text(pg, url):
        pg.goto(url, wait_until="domcontentloaded", timeout=60_000)
        pg.wait_for_timeout(1_500)
        return pg.inner_text("body")[:10_000]

    for ct, lnk in changes:
        pc = ""
        if lnk and bm.has_page:
            try:
                pc = bm._run(_fetch_page_text, bm._page_r, lnk)
            except Exception:
                pc = "Unable to retrieve page content."
        inputs.append((ct, pc, lnk))

    for i, (ct, pc, lnk) in enumerate(inputs):
        prog.progress((i + 1) / len(inputs), text=f"Analyzing change {i+1}/{len(inputs)}")
        intel = analyze_change_intelligence(ct, pc, lnk)
        state["ai_change_intelligence"].append({"change": ct, "url": lnk, "intelligence": intel})

    prog.empty()
    msg = f"Change intelligence complete — {len(inputs)} items"
    state["status"] = msg
    logger.info(f"[action] analyze_changes: {msg}")
    st.success(msg)


def handle_reload_schema():
    from config import settings
    import importlib
    importlib.reload(settings)
    st.success("Enrichment schema reloaded from enrichment_config.json")
    logger.info("[action] reload_schema")


def handle_reset_app():
    state["scraped_items"]          = []
    state["ai_enriched_items"]      = []
    state["detected_changes"]       = []
    state["ai_change_intelligence"] = []
    state["architecture_profile"]   = None
    state["url"]                    = ""
    state["status"]                 = "Reset complete"
    state["page_ready"]             = False
    state["page_screenshot"]        = None
    state["page_title"]             = ""
    # Workflow recorder state
    state["workflow_recording"]     = False
    state["recorded_steps"]         = []
    state["diff_summary"]           = None
    state["pending_save_snapshot"]  = False
    state["last_workflow_id"]       = None
    rec.discard()
    logger.info("[action] reset_app")
    st.success("Session reset.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LAYOUT
# ══════════════════════════════════════════════════════════════════════════════

# Render sidebar and capture triggered actions
actions = render_sidebar(state)

# Dispatch actions
ACTION_MAP = {
    # ── Existing actions (unchanged) ──────────────────────────────────────────
    "open_page":       handle_open_page,
    "expand_pages":    handle_expand_pages,
    "auto_scrape":     handle_auto_scrape,
    "manual_pick":     handle_manual_pick,
    "manual_scrape":   handle_manual_scrape,
    "reset_picker":    handle_reset_picker,
    "ai_enrich":       handle_ai_enrich,
    "analyze_changes": handle_analyze_changes,
    "reload_schema":   handle_reload_schema,
    "reset_app":       handle_reset_app,
    # ── Workflow Recorder layer ───────────────────────────────────────────────
    "auto_run_workflow":   handle_auto_run_workflow,
    "start_recording":     handle_start_recording,
    "pause_recording":     handle_pause_recording,
    "stop_recording":      handle_stop_recording,
    "save_workflow":       handle_save_workflow,
    "discard_recording":   handle_discard_recording,
}

for action_key, handler in ACTION_MAP.items():
    if actions.get(action_key):
        handler()
        # After auto_scrape or expand_pages, record steps if recording is on
        if action_key == "auto_scrape" and state.get("workflow_recording"):
            strategy = state.get("architecture_profile")
            strat_s  = strategy.scraper_strategy if strategy else "auto"
            rec.record_auto_scrape(strategy=strat_s, page_url=state.get("url", ""))
            rec.record_collect_all_urls(page_url=state.get("url", ""))
            state["recorded_steps"] = rec.get_steps()
        elif action_key == "expand_pages" and state.get("workflow_recording"):
            rec.record_pagination_next(page_url=state.get("url", ""))
            state["recorded_steps"] = rec.get_steps()
        st.rerun()

# ── Pending snapshot save (triggered from diff dashboard "Save snapshot" btn) ─
if state.get("pending_save_snapshot"):
    handle_pending_save_snapshot()
    st.rerun()

# ── Top status bar ─────────────────────────────────────────────────────────────
status     = state.get("status", "Idle")
n_items    = len(state.get("scraped_items", []))
n_enriched = len(state.get("ai_enriched_items", []))
n_changes  = len(state.get("detected_changes", []))

col1, col2, col3, col4 = st.columns(4)
_status_short = status[:60] + ("…" if len(status) > 60 else "")
col1.metric("Status", _status_short)
col2.metric("Scraped Items", n_items)
col3.metric("AI Enriched",   n_enriched)
col4.metric("CI Changes",    n_changes)

# Show full error inline when status starts with Error
if status.startswith("Error:"):
    with st.expander("Full error details", expanded=True):
        st.code(status, language="text")

st.divider()

# Main tabs
tab_names = [
    "\U0001f4e6 Products",
    "\U0001f916 AI Enrichment",
    "\U0001f50d CI Monitoring",
    "\U0001f4dc History",
    "\U0001f4cb Logs",
    "\U0001f3d7 Architecture",
    "\U0001f41b Debug Console",
]

(tab_products, tab_ai, tab_ci,
 tab_history, tab_logs, tab_arch, tab_debug) = st.tabs(tab_names)

with tab_products:
    render_products_tab(state, bm)

with tab_ai:
    render_ai_tab(state)

with tab_ci:
    render_ci_tab(state)

with tab_history:
    render_history_tab(state)

with tab_logs:
    render_logs_tab(state)

with tab_arch:
    render_architecture_tab(state)

with tab_debug:
    render_debug_tab(state)
