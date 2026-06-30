"""
components/sidebar.py - Streamlit sidebar UI.
Unchanged existing sections + Workflow Recorder layer additions.
"""
import logging
import streamlit as st
from config.settings import BUILD_VERSION

logger = logging.getLogger(__name__)


def render_sidebar(state) -> dict:
    """
    Render the sidebar and return a dict of triggered actions.
    state: st.session_state (passed in to avoid circular imports)
    """
    actions = {}

    with st.sidebar:
        st.markdown(
            f"<h2 style='margin-bottom:0'>Universal Scraper</h2>"
            f"<p style='color:#8b949e;font-size:12px;margin-top:2px'>v{BUILD_VERSION}</p>",
            unsafe_allow_html=True,
        )
        st.divider()

        # URL input
        st.markdown("**TARGET URL**")
        url = st.text_input(
            "URL", value=state.get("url", ""),
            placeholder="https://example.com/products",
            label_visibility="collapsed",
        )
        if url != state.get("url", ""):
            state["url"] = url

        # Workflow status banner (shown once URL is entered)
        current_url = state.get("url", "").strip()
        if current_url:
            try:
                from database.repository import workflow_exists_for_url, get_company_by_url, get_active_workflow
                _wf_exists = workflow_exists_for_url(current_url)
                if _wf_exists:
                    _co  = get_company_by_url(current_url)
                    _wf  = get_active_workflow(_co.id) if _co else None
                    _ver = _wf.version if _wf else "?"
                    _cnt = len(_wf.steps) if _wf else 0
                    _nm  = (_wf.name if _wf else "") or "Default"
                    st.markdown(
                        f"<div style='background:rgba(34,197,94,0.18);border:1px solid "
                        f"rgba(34,197,94,0.5);border-radius:8px;padding:8px 10px;margin:6px 0'>"
                        f"<span style='font-size:13px'>&#x2705; <b>Workflow found</b><br/>"
                        f"<span style='opacity:.8;font-size:11px'>{_nm} &middot; "
                        f"v{_ver} &middot; {_cnt} steps</span></span></div>",
                        unsafe_allow_html=True,
                    )
                    if st.button("Auto Run", use_container_width=True,
                                 help="Replay the saved workflow then scrape and compare changes",
                                 type="primary"):
                        actions["auto_run_workflow"] = True
                else:
                    st.markdown(
                        "<div style='background:rgba(251,191,36,0.15);border:1px solid "
                        "rgba(251,191,36,0.4);border-radius:8px;padding:8px 10px;margin:6px 0'>"
                        "<span style='font-size:12px'>&#x1F195; <b>New URL</b> &mdash; "
                        "no workflow saved yet.<br/>"
                        "<span style='opacity:.8'>Scrape manually, then save the workflow."
                        "</span></span></div>",
                        unsafe_allow_html=True,
                    )
            except Exception as _e:
                # DB not yet initialised or connection error — show neutral banner
                logger.debug("[sidebar] workflow DB check failed: %s", _e)
                st.markdown(
                    "<div style='background:rgba(148,163,184,0.12);border:1px solid "
                    "rgba(148,163,184,0.3);border-radius:8px;padding:8px 10px;margin:6px 0'>"
                    "<span style='font-size:11px;opacity:.7'>&#x1F4BE; Workflow DB initialising&hellip;"
                    "</span></div>",
                    unsafe_allow_html=True,
                )

        headless = st.checkbox("Headless mode", value=state.get("headless", False))
        if headless != state.get("headless", False):
            state["headless"] = headless

        _channel_options = ["chromium", "msedge", "chrome"]
        _channel_idx = _channel_options.index(state.get("channel", "chromium")) \
                       if state.get("channel", "chromium") in _channel_options else 0
        channel = st.selectbox(
            "Browser engine",
            _channel_options,
            index=_channel_idx,
            help="chromium = Playwright bundled. msedge/chrome = system browser.",
        )
        if channel != state.get("channel", "chromium"):
            state["channel"] = channel

        st.divider()

        # Workflow recording controls (before BROWSER so user enables recording first)
        st.markdown("**WORKFLOW RECORDING**")
        _rec_active = state.get("workflow_recording", False)

        if not _rec_active:
            if st.button("Start Recording", use_container_width=True,
                         help="Record your scrape steps as a reusable workflow"):
                actions["start_recording"] = True
        else:
            st.markdown(
                "<div style='background:rgba(239,68,68,0.15);border:1px solid "
                "rgba(239,68,68,0.5);border-radius:8px;padding:6px 10px;margin:4px 0'>"
                "<span style='color:#ef4444;font-size:12px'>"
                "&#x23FA; <b>Recording...</b></span></div>",
                unsafe_allow_html=True,
            )
            col_p, col_s = st.columns(2)
            with col_p:
                if st.button("Pause", use_container_width=True):
                    actions["pause_recording"] = True
            with col_s:
                if st.button("Stop", use_container_width=True):
                    actions["stop_recording"] = True

        _steps_recorded = len(state.get("recorded_steps", []))
        if _steps_recorded > 0:
            st.caption(f"{_steps_recorded} step{'s' if _steps_recorded != 1 else ''} recorded")
            if st.button("Save Workflow", use_container_width=True,
                         help="Save recorded steps as a reusable workflow for this URL",
                         type="primary"):
                actions["save_workflow"] = True
            if st.button("Discard Recording", use_container_width=True):
                actions["discard_recording"] = True

        st.divider()

        # Browser controls
        st.markdown("**BROWSER**")
        if st.button("Open Page", use_container_width=True,
                     help="Navigate browser to URL"):
            actions["open_page"] = True

        if st.button("Expand Pages", use_container_width=True,
                     help="Run universal pagination expansion"):
            actions["expand_pages"] = True

        st.divider()

        # Smart scrape
        st.markdown("**SMART SCRAPE**")
        st.caption("Auto-detects page type: card grids, A-Z lists, SPAs, AEM, Shopify, and more.")
        if st.button("Smart Scrape", use_container_width=True,
                     help="Auto-detect and extract products"):
            actions["auto_scrape"] = True

        st.divider()

        # Manual scrape
        st.markdown("**MANUAL SCRAPE**")
        st.caption("Click individual elements in the browser to select products manually.")
        if st.button("Activate Picker", use_container_width=True,
                     help="Click-to-select mode (activates in browser)"):
            actions["manual_pick"] = True

        if st.button("Collect Selection", use_container_width=True,
                     help="Confirm and collect manual selection"):
            actions["manual_scrape"] = True

        if st.button("Reset Picker", use_container_width=True,
                     help="Clear manual picker state"):
            actions["reset_picker"] = True

        st.divider()

        # AI & Export
        st.markdown("**AI & EXPORT**")
        if st.button("AI Enrichment", use_container_width=True,
                     help="GPT-4o product metadata enrichment"):
            actions["ai_enrich"] = True

        if st.button("Analyze Changes", use_container_width=True,
                     help="Run CI change intelligence"):
            actions["analyze_changes"] = True

        st.divider()

        st.markdown("**SYSTEM**")
        if st.button("History", use_container_width=True,
                     help="View SQLite change log"):
            actions["show_history"] = True

        if st.button("Reload Schema", use_container_width=True,
                     help="Reload enrichment_config.json"):
            actions["reload_schema"] = True

        if st.button("Full Reset", use_container_width=True,
                     help="Clear all session data", type="secondary"):
            actions["reset_app"] = True

        st.divider()

        # Status indicators
        browser_ok = state.get("browser_ready", False)
        page_ok    = state.get("page_ready", False)
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(
                "&#x1F7E2; Browser" if browser_ok else "&#x1F534; Browser",
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown(
                "&#x1F7E2; Page" if page_ok else "&#x26AB; Page",
                unsafe_allow_html=True,
            )

        n = len(state.get("scraped_items", []))
        if n:
            st.caption(f"{n} items scraped")

    return actions
