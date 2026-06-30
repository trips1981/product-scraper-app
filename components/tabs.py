"""
components/tabs.py — Main content area tabs for the Streamlit app.
Tabs: Products | AI Enrichment | CI Monitoring | History | Logs | Architecture | Debug Console
"""
import streamlit as st
import pandas as pd
from exports.csv_exporter import (
    to_raw_csv, to_ai_csv, to_ci_csv, to_history_csv,
)


# ---------------------------------------------------------------------------
# Workflow diff dashboard (shown at the top of the Products tab after Auto Run)
# ---------------------------------------------------------------------------

def render_diff_dashboard(state):
    """
    Render the product comparison dashboard.
    Only shown when state["diff_summary"] is set (after Auto Run).
    """
    summary = state.get("diff_summary")
    if summary is None:
        return

    st.markdown("## 🔄 Change Report")

    # Metrics row
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Previous",  summary.total_previous)
    col2.metric("Current",   summary.total_current)
    col3.metric("🟢 New",     summary.new,     delta=f"+{summary.new}"     if summary.new     else None)
    col4.metric("🔴 Removed", summary.removed, delta=f"-{summary.removed}" if summary.removed else None)
    col5.metric("🟡 Renamed", summary.renamed)
    col6.metric("🟠 URL Chg", summary.url_changed)

    if not summary.has_changes:
        st.success("✅ No changes detected — product catalogue is identical to the previous run.")
    else:
        st.info(
            f"**{summary.new} new** · **{summary.removed} removed** · "
            f"**{summary.renamed} renamed** · **{summary.url_changed} URL changed**"
        )

    # Diff table — changes first, then unchanged
    diffs = summary.diffs
    if diffs:
        rows = []
        for d in diffs:
            rows.append({
                "":         d.icon,
                "Status":   d.label,
                "Product":  d.name,
                "Current URL":  d.url,
                "Previous Name": d.prev_name if d.prev_name != d.name else "",
                "Previous URL":  d.prev_url  if d.prev_url  != d.url  else "",
                "Confidence": f"{d.confidence:.0%}" if d.confidence < 1.0 else "",
                "Notes": d.notes,
            })
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            height=min(60 + len(rows) * 35, 520),
            column_config={
                "":              st.column_config.TextColumn("",           width="small"),
                "Status":        st.column_config.TextColumn("Status",     width="small"),
                "Product":       st.column_config.TextColumn("Product",    width="large"),
                "Current URL":   st.column_config.LinkColumn("Current URL",  width="medium"),
                "Previous Name": st.column_config.TextColumn("Prev Name",  width="medium"),
                "Previous URL":  st.column_config.LinkColumn("Prev URL",   width="medium"),
                "Confidence":    st.column_config.TextColumn("Confidence", width="small"),
                "Notes":         st.column_config.TextColumn("Notes",      width="medium"),
            },
        )

    # Save prompt
    st.divider()
    st.markdown("### 💾 Save as new snapshot?")
    st.caption("Saving records this run as the new baseline for future comparisons.")
    col_y, col_n, _ = st.columns([1, 1, 4])
    with col_y:
        if st.button("✅ Save snapshot", type="primary", key="wf_save_snap"):
            state["pending_save_snapshot"] = True
            st.rerun()
    with col_n:
        if st.button("✖ Dismiss", key="wf_dismiss_diff"):
            state["diff_summary"]          = None
            state["pending_save_snapshot"] = False
            st.rerun()


# ---------------------------------------------------------------------------
# Workflow recording timeline (shown when recording is active)
# ---------------------------------------------------------------------------

def render_recording_timeline(state):
    """Show live recorded steps when recording is active."""
    steps = state.get("recorded_steps", [])
    recording = state.get("workflow_recording", False)
    if not steps and not recording:
        return

    st.markdown("### ⏺️ Recording Timeline")
    if recording:
        st.markdown(
            "<div style='background:rgba(239,68,68,0.1);border-left:3px solid #ef4444;"
            "padding:6px 12px;border-radius:4px;margin-bottom:8px'>"
            "<b style='color:#ef4444'>● RECORDING</b> — every scrape action is being captured</div>",
            unsafe_allow_html=True,
        )

    if steps:
        for i, step in enumerate(steps):
            from workflow.recorder import ACTION_ICONS, ACTION_LABELS
            at   = step.get("action_type", "")
            icon = ACTION_ICONS.get(at, "▶️")
            lbl  = ACTION_LABELS.get(at, at)
            note = step.get("notes") or step.get("target_value") or ""
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:8px;"
                f"padding:4px 0;border-bottom:1px solid rgba(99,102,241,0.1)'>"
                f"<span style='min-width:24px;text-align:center'>{icon}</span>"
                f"<span style='min-width:28px;color:#6366f1;font-size:11px;font-weight:600'>#{i+1}</span>"
                f"<span style='font-size:12px;font-weight:600;min-width:140px'>{lbl}</span>"
                f"<span style='font-size:11px;color:#6b7280;flex:1'>{note}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No steps recorded yet — run a scrape to start capturing actions.")
    st.divider()


def render_products_tab(state, bm=None):
    """Tab 1 — Products: scraped items table with filter and exports."""
    import io

    # ── Workflow diff dashboard (Auto Run result) ──────────────────────────────
    render_diff_dashboard(state)

    # ── Recording timeline ────────────────────────────────────────────────────
    render_recording_timeline(state)

    # ── Page Preview ──────────────────────────────────────────────────────────
    screenshot = state.get("page_screenshot")
    page_title = state.get("page_title", "")
    if screenshot:
        st.markdown("### Page Preview")
        title_display = page_title if page_title else state.get("url", "")
        st.caption(f"Loaded: **{title_display}**")

        # Show screenshot in a scrollable container
        st.image(
            screenshot,
            caption="Loaded page — use Smart Auto Scrape or Manual Picker to extract products",
            use_column_width=True,
        )

        # Refresh screenshot button
        col_r, col_s = st.columns([1, 3])
        with col_r:
            if st.button("Refresh Preview", key="refresh_screenshot", use_container_width=True):
                if bm and bm.has_page:
                    try:
                        state["page_screenshot"] = bm.take_screenshot(full_page=False)
                        state["page_title"]      = bm.get_page_title()
                    except Exception:
                        pass
                st.rerun()

        st.divider()

    items = state.get("scraped_items", [])

    col1, col2 = st.columns([3, 1])
    with col1:
        filter_kw = st.text_input(
            "Filter", placeholder="Search products…",
            label_visibility="collapsed",
            key="filter_kw",
        )
    with col2:
        st.metric("Total", len(items))

    if items:
        filtered = [
            i for i in items
            if filter_kw.lower() in i.get("name", "").lower()
        ] if filter_kw else items

        df = pd.DataFrame(filtered)
        st.dataframe(
            df,
            use_container_width=True,
            height=420,
            column_config={
                "name": st.column_config.TextColumn("Product Name", width="large"),
                "link": st.column_config.LinkColumn("URL", width="large"),
            },
        )

        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button(
                "⬇️ Export Raw CSV",
                data=to_raw_csv(filtered),
                file_name="products_raw.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with col_b:
            if state.get("ai_enriched_items"):
                st.download_button(
                    "⬇️ Export AI CSV",
                    data=to_ai_csv(state["ai_enriched_items"]),
                    file_name="products_ai.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
    else:
        st.markdown("---")
        st.markdown("### How to scrape a product catalogue")
        st.markdown("""
This scraper has two modes. In most cases, **⚡ Smart Scrape** is all you need.

---

#### ⚡ Smart Scrape
Paste a URL, click **Open Page**, then click **Smart Scrape**.
The app inspects the page and automatically picks the right extraction method:

**Card grid pages** — product tiles loaded dynamically via React / Angular / Vue.
Clicks "Show more" buttons until all products are visible, then extracts every card.
Works on: **AWS** (`aws.amazon.com/products`), **Google Cloud** (`cloud.google.com/products`),
**Azure** (`azure.microsoft.com/products`), **Adobe**, **Siemens**, **Shopify stores**, **IBM / SAP**.

**A–Z catalogue pages** — a flat alphabetical list of product names with no cards.
Reads every name from the list, then searches the site to resolve each to its product URL.
Works on: **Schneider Electric** (`se.com/en/en/all-products`),
**Honeywell**, **Rockwell Automation**, **ABB**, **Emerson** product indexes.

You do not need to choose — Smart Scrape detects which type of page you are on and runs the right method automatically.

---

#### 🖱️ Manual Scrape
For pages Smart Scrape cannot handle — unusual layouts, heavy obfuscation, or login-gated content.
Click **Activate Picker**, click each product element in the browser window,
then click **Collect Selection** to save your picks.
        """)
        st.markdown("---")


def render_ai_tab(state):
    """Tab 2 — AI Enrichment: enriched product table."""
    enriched = state.get("ai_enriched_items", [])

    if not enriched:
        st.info("Run **AI Enrichment** from the sidebar after scraping products.")
        return

    df = pd.DataFrame(enriched)
    st.dataframe(df, use_container_width=True, height=450)

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "⬇️ Export AI CSV",
            data=to_ai_csv(enriched),
            file_name="products_ai_enriched.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col2:
        st.metric("Enriched Items", len(enriched))

    # High-signal visualisations
    if "adoption_friction" in df.columns:
        st.subheader("Adoption Friction Distribution")
        friction_counts = df["adoption_friction"].value_counts().reset_index()
        friction_counts.columns = ["friction_score", "count"]
        st.bar_chart(friction_counts.set_index("friction_score"))

    if "sales_motion" in df.columns:
        st.subheader("Sales Motion Split")
        motion_counts = df["sales_motion"].value_counts().reset_index()
        motion_counts.columns = ["motion", "count"]
        st.bar_chart(motion_counts.set_index("motion"))


def render_ci_tab(state):
    """Tab 3 — CI Monitoring: detected changes and intelligence."""
    changes   = state.get("detected_changes", [])
    intel     = state.get("ai_change_intelligence", [])

    if not changes and not intel:
        st.info("CI monitoring runs automatically after each scrape. "
                "No changes detected yet, or this is the first scrape (baseline created).")
        return

    if changes:
        st.subheader(f"Detected Changes ({len(changes)})")
        for change_text, link in changes:
            icon = "🟢" if "NEW:" in change_text else "🔴" if "REMOVED:" in change_text else "🟡"
            st.markdown(f"{icon} `{change_text}`")
            if link:
                st.caption(link)
        st.divider()

    if intel:
        st.subheader("AI Change Intelligence")
        for entry in intel:
            with st.expander(entry.get("change", "Change")[:80]):
                st.markdown(entry.get("intelligence", ""))
                if entry.get("url"):
                    st.caption(entry["url"])

        st.download_button(
            "⬇️ Export CI CSV",
            data=to_ci_csv(intel),
            file_name="ci_intelligence.csv",
            mime="text/csv",
        )


def render_history_tab(state):
    """Tab 4 — History: SQLite change log."""
    from ci.monitor import get_change_history, get_all_domains
    from utils.helpers import extract_domain

    url    = state.get("url", "")
    domain = extract_domain(url) if url else ""

    if not domain:
        domains = get_all_domains()
        if domains:
            domain = st.selectbox("Select domain", domains)
        else:
            st.info("No change history in database yet.")
            return

    history = get_change_history(domain, limit=500)
    if not history:
        st.info(f"No history for **{domain}** yet.")
        return

    st.metric("Total Events", len(history))

    df = pd.DataFrame(history)
    # Colour-code by type
    def _style_row(row):
        colours = {
            "NEW":      "background-color: #0d2e1a",
            "REMOVED":  "background-color: #2e0d0d",
            "RENAMED":  "background-color: #2e2a0d",
            "BASELINE": "background-color: #0d1c2e",
        }
        return [colours.get(row["type"], "")] * len(row)

    st.dataframe(
        df[["recorded_at", "type", "description", "name", "link"]],
        use_container_width=True,
        height=420,
    )

    st.download_button(
        "⬇️ Export History CSV",
        data=to_history_csv(history),
        file_name=f"history_{domain}.csv",
        mime="text/csv",
    )


def render_logs_tab(state):
    """Tab 5 — Logs: in-session scraping log."""
    from utils.logging_config import get_log_handler
    records = get_log_handler().get_records()

    col1, col2 = st.columns([4, 1])
    with col1:
        st.caption(f"{len(records)} log entries")
    with col2:
        if st.button("Clear", key="clear_logs"):
            get_log_handler().clear()
            st.rerun()

    if not records:
        st.info("No log entries yet.")
        return

    level_colours = {
        "DEBUG":    "#8b949e",
        "INFO":     "#3fb950",
        "WARNING":  "#d29922",
        "ERROR":    "#f85149",
        "CRITICAL": "#f85149",
    }

    log_html = ""
    for r in reversed(records[-200:]):  # show last 200
        colour = level_colours.get(r["level"], "#e6edf3")
        log_html += (
            f"<div style='font-family:monospace;font-size:12px;margin:1px 0'>"
            f"<span style='color:#484f58'>{r['ts']}</span> "
            f"<span style='color:{colour};font-weight:bold'>[{r['level']}]</span> "
            f"<span style='color:#e6edf3'>{r['message']}</span>"
            f"</div>"
        )

    st.markdown(
        f"<div style='background:#0d1117;padding:12px;border-radius:6px;"
        f"height:420px;overflow-y:auto;border:1px solid #30363d'>"
        f"{log_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_architecture_tab(state):
    """Tab 6 — Architecture Detection: detected site profile."""
    profile = state.get("architecture_profile")

    if not profile:
        st.info("Run **Smart Auto Scrape** to detect the site architecture.")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Framework", profile.framework)
        st.metric("CMS", profile.cms)
    with col2:
        st.metric("Pagination", profile.pagination)
        st.metric("Navigation", profile.navigation)
    with col3:
        st.metric("Strategy", profile.scraper_strategy)
        st.metric("Confidence", f"{profile.confidence:.0%}")

    if profile.vendor_profile:
        st.success(f"Vendor profile matched: **{profile.vendor_profile}**")

    if profile.signals:
        st.subheader("Detection Signals")
        for sig in profile.signals:
            st.code(sig)


def render_debug_tab(state):
    """Tab 7 — Debug Console: raw session state and architecture details."""
    st.subheader("Session State Summary")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Scraped Items", len(state.get("scraped_items", [])))
    with col2:
        st.metric("AI Enriched", len(state.get("ai_enriched_items", [])))
    with col3:
        st.metric("CI Changes", len(state.get("detected_changes", [])))

    st.divider()
    st.subheader("Raw Architecture Profile")
    profile = state.get("architecture_profile")
    if profile:
        import dataclasses
        st.json(dataclasses.asdict(profile))
    else:
        st.info("No architecture profile yet.")

    st.subheader("Last Scrape URL")
    st.code(state.get("url", "(none)"))
