"""
workflow/recorder.py - Semantic workflow recorder.

Records WHAT the user intended to do (semantic actions), never HOW the browser
got there (no coordinates, no pixel positions, no timing-dependent clicks).

Every action is described by:
    action_type  - what kind of action (click_menu, expand_accordion, ...)
    target_type  - how to locate the target (text, aria_label, css_selector, ...)
    target_value - the actual value to match (e.g. "Products", "Networking")
    selector     - optional pipe-separated fallback CSS/XPath selectors
    notes        - human-readable description of intent

The recorder is a pure data accumulator - it does not drive the browser itself.
It listens to signals from the app (URL changes, explicit record() calls) and
builds an ordered list of WorkflowStep dicts ready for save_workflow().
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Semantic action catalogue
# ---------------------------------------------------------------------------

ACTION_NAVIGATE              = "navigate"
ACTION_CLICK_MENU            = "click_menu"
ACTION_EXPAND_ACCORDION      = "expand_accordion"
ACTION_EXPAND_TREE           = "expand_tree"
ACTION_OPEN_TAB              = "open_tab"
ACTION_SCROLL                = "scroll"
ACTION_SEARCH                = "search"
ACTION_PAGINATION_NEXT       = "pagination_next"
ACTION_FILTER_APPLY          = "filter_apply"
ACTION_OPEN_PRODUCT_PAGE     = "open_product_page"
ACTION_COLLECT_PRODUCT       = "collect_product"
ACTION_COLLECT_ALL_URLS      = "collect_all_product_urls"
ACTION_WAIT                  = "wait"
ACTION_IGNORE_POPUP          = "ignore_popup"
ACTION_CLOSE_COOKIE_BANNER   = "close_cookie_banner"
ACTION_MANUAL_SELECTION      = "manual_product_selection"
ACTION_HOVER                 = "hover"
ACTION_TAB_SWITCH            = "tab_switch"
ACTION_BROWSER_BACK          = "browser_back"
ACTION_BROWSER_FORWARD       = "browser_forward"
ACTION_EXPAND_VIEW_ALL       = "expand_view_all"
ACTION_AUTO_SCRAPE           = "auto_scrape"

# Human-readable labels shown in the recorder UI timeline
ACTION_LABELS = {
    ACTION_NAVIGATE:            "Navigate",
    ACTION_CLICK_MENU:          "Click menu",
    ACTION_EXPAND_ACCORDION:    "Expand accordion",
    ACTION_EXPAND_TREE:         "Expand tree",
    ACTION_OPEN_TAB:            "Open tab",
    ACTION_SCROLL:              "Scroll",
    ACTION_SEARCH:              "Search",
    ACTION_PAGINATION_NEXT:     "Pagination — Next",
    ACTION_FILTER_APPLY:        "Apply filter",
    ACTION_OPEN_PRODUCT_PAGE:   "Open product page",
    ACTION_COLLECT_PRODUCT:     "Collect product",
    ACTION_COLLECT_ALL_URLS:    "Collect all product URLs",
    ACTION_WAIT:                "Wait",
    ACTION_IGNORE_POPUP:        "Ignore popup",
    ACTION_CLOSE_COOKIE_BANNER: "Close cookie banner",
    ACTION_MANUAL_SELECTION:    "Manual product selection",
    ACTION_HOVER:               "Hover",
    ACTION_TAB_SWITCH:          "Switch tab",
    ACTION_BROWSER_BACK:        "Browser back",
    ACTION_BROWSER_FORWARD:     "Browser forward",
    ACTION_EXPAND_VIEW_ALL:     "Expand 'View All'",
    ACTION_AUTO_SCRAPE:         "Auto scrape (card grid / A-Z)",
}

# Icons for the UI timeline
ACTION_ICONS = {
    ACTION_NAVIGATE:            "🌐",
    ACTION_CLICK_MENU:          "🖱️",
    ACTION_EXPAND_ACCORDION:    "📂",
    ACTION_EXPAND_TREE:         "🌳",
    ACTION_OPEN_TAB:            "📑",
    ACTION_SCROLL:              "↕️",
    ACTION_SEARCH:              "🔍",
    ACTION_PAGINATION_NEXT:     "➡️",
    ACTION_FILTER_APPLY:        "🔧",
    ACTION_OPEN_PRODUCT_PAGE:   "📄",
    ACTION_COLLECT_PRODUCT:     "📦",
    ACTION_COLLECT_ALL_URLS:    "🗂️",
    ACTION_WAIT:                "⏳",
    ACTION_IGNORE_POPUP:        "🙈",
    ACTION_CLOSE_COOKIE_BANNER: "🍪",
    ACTION_MANUAL_SELECTION:    "✋",
    ACTION_HOVER:               "👆",
    ACTION_TAB_SWITCH:          "🔀",
    ACTION_BROWSER_BACK:        "⬅️",
    ACTION_BROWSER_FORWARD:     "➡️",
    ACTION_EXPAND_VIEW_ALL:     "👁️",
    ACTION_AUTO_SCRAPE:         "⚡",
}


# ---------------------------------------------------------------------------
# WorkflowRecorder
# ---------------------------------------------------------------------------

class WorkflowRecorder:
    """
    Accumulates semantic steps during a scraping session.

    Usage
    -----
        recorder = WorkflowRecorder()
        recorder.start(url="https://example.com/products")

        # As the user drives the browser:
        recorder.record_navigate("https://example.com/products")
        recorder.record_click_menu("Products")
        recorder.record_expand_accordion("Networking")
        recorder.record_auto_scrape()

        steps = recorder.get_steps()   # list of dicts ready for save_workflow()
        recorder.stop()
    """

    def __init__(self):
        self._steps: list[dict] = []
        self._recording: bool   = False
        self._started_at: Optional[datetime] = None
        self._base_url: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, url: str = "") -> None:
        self._steps       = []
        self._recording   = True
        self._started_at  = datetime.utcnow()
        self._base_url    = url
        logger.info("[recorder] started for %s", url)

    def stop(self) -> None:
        self._recording = False
        logger.info("[recorder] stopped (%d steps)", len(self._steps))

    def pause(self) -> None:
        self._recording = False

    def resume(self) -> None:
        self._recording = True

    def discard(self) -> None:
        self._steps     = []
        self._recording = False
        logger.info("[recorder] discarded")

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def step_count(self) -> int:
        return len(self._steps)

    # ------------------------------------------------------------------
    # Internal step builder
    # ------------------------------------------------------------------

    def _add(
        self,
        action_type:    str,
        target_type:    str,
        target_value:   str,
        selector:       Optional[str] = None,
        page_url:       Optional[str] = None,
        wait_condition: Optional[str] = None,
        notes:          Optional[str] = None,
    ) -> dict:
        step = {
            "action_type":    action_type,
            "target_type":    target_type,
            "target_value":   target_value,
            "selector":       selector,
            "page_url":       page_url or self._base_url,
            "wait_condition": wait_condition,
            "notes":          notes or self._default_note(action_type, target_value),
        }
        self._steps.append(step)
        logger.debug("[recorder] step %d: %s %r", len(self._steps), action_type, target_value)
        return step

    def _default_note(self, action_type: str, target_value: str) -> str:
        label = ACTION_LABELS.get(action_type, action_type)
        if target_value:
            return f"{label}: {target_value}"
        return label

    # ------------------------------------------------------------------
    # Semantic record methods
    # ------------------------------------------------------------------

    def record_navigate(self, url: str, notes: str = "") -> dict:
        return self._add(
            ACTION_NAVIGATE, "url_pattern", url,
            notes=notes or f"Navigate to {url}",
        )

    def record_click_menu(self, label: str, selector: str = "", page_url: str = "") -> dict:
        return self._add(
            ACTION_CLICK_MENU, "text", label,
            selector=selector or None,
            page_url=page_url,
            notes=f"Click {label!r} menu item",
        )

    def record_expand_accordion(self, label: str, selector: str = "", page_url: str = "") -> dict:
        return self._add(
            ACTION_EXPAND_ACCORDION, "text", label,
            selector=selector or None,
            page_url=page_url,
            notes=f"Expand {label!r} section",
        )

    def record_expand_tree(self, label: str, selector: str = "", page_url: str = "") -> dict:
        return self._add(
            ACTION_EXPAND_TREE, "text", label,
            selector=selector or None,
            page_url=page_url,
            notes=f"Expand tree node {label!r}",
        )

    def record_expand_view_all(self, label: str = "View All", page_url: str = "") -> dict:
        return self._add(
            ACTION_EXPAND_VIEW_ALL, "text", label,
            page_url=page_url,
            notes=f"Expand '{label}'",
        )

    def record_open_tab(self, label: str, page_url: str = "") -> dict:
        return self._add(
            ACTION_OPEN_TAB, "text", label,
            page_url=page_url,
            notes=f"Open tab {label!r}",
        )

    def record_search(self, query: str, page_url: str = "") -> dict:
        return self._add(
            ACTION_SEARCH, "text", query,
            page_url=page_url,
            notes=f"Search for {query!r}",
        )

    def record_pagination_next(self, label: str = "Next", page_url: str = "") -> dict:
        return self._add(
            ACTION_PAGINATION_NEXT, "text", label,
            page_url=page_url,
            notes="Continue until pagination ends",
        )

    def record_filter(self, label: str, value: str = "", page_url: str = "") -> dict:
        return self._add(
            ACTION_FILTER_APPLY, "text", label,
            page_url=page_url,
            notes=f"Apply filter {label!r}" + (f" = {value}" if value else ""),
        )

    def record_ignore_popup(self, description: str = "popup", page_url: str = "") -> dict:
        return self._add(
            ACTION_IGNORE_POPUP, "auto", description,
            page_url=page_url,
            notes=f"Ignore {description}",
        )

    def record_close_cookie_banner(self, page_url: str = "") -> dict:
        return self._add(
            ACTION_CLOSE_COOKIE_BANNER, "auto", "cookie banner",
            page_url=page_url,
            notes="Close cookie banner",
        )

    def record_manual_selection(self, count: int, page_url: str = "") -> dict:
        return self._add(
            ACTION_MANUAL_SELECTION, "auto", str(count),
            page_url=page_url,
            notes=f"Manual product selection ({count} items picked)",
        )

    def record_auto_scrape(self, strategy: str = "auto", page_url: str = "") -> dict:
        return self._add(
            ACTION_AUTO_SCRAPE, "auto", strategy,
            page_url=page_url,
            notes=f"Auto scrape ({strategy})",
        )

    def record_collect_all_urls(self, page_url: str = "") -> dict:
        return self._add(
            ACTION_COLLECT_ALL_URLS, "auto", "",
            page_url=page_url,
            notes="Collect every product URL",
        )

    def record_browser_back(self, page_url: str = "") -> dict:
        return self._add(ACTION_BROWSER_BACK, "auto", "", page_url=page_url)

    def record_browser_forward(self, page_url: str = "") -> dict:
        return self._add(ACTION_BROWSER_FORWARD, "auto", "", page_url=page_url)

    def record_scroll(self, direction: str = "down", page_url: str = "") -> dict:
        return self._add(
            ACTION_SCROLL, "auto", direction,
            page_url=page_url,
            notes=f"Scroll {direction}",
        )

    def record_wait(self, condition: str = "networkidle", page_url: str = "") -> dict:
        return self._add(
            ACTION_WAIT, "auto", condition,
            wait_condition=condition,
            page_url=page_url,
            notes=f"Wait for {condition}",
        )

    def record_hover(self, label: str, selector: str = "", page_url: str = "") -> dict:
        return self._add(
            ACTION_HOVER, "text", label,
            selector=selector or None,
            page_url=page_url,
            notes=f"Hover over {label!r}",
        )

    # ------------------------------------------------------------------
    # Edit / remove
    # ------------------------------------------------------------------

    def remove_last(self) -> Optional[dict]:
        if self._steps:
            removed = self._steps.pop()
            logger.debug("[recorder] removed step: %s", removed.get("action_type"))
            return removed
        return None

    def remove_step(self, index: int) -> Optional[dict]:
        if 0 <= index < len(self._steps):
            removed = self._steps.pop(index)
            logger.debug("[recorder] removed step[%d]: %s", index, removed.get("action_type"))
            return removed
        return None

    def update_step_note(self, index: int, note: str) -> None:
        if 0 <= index < len(self._steps):
            self._steps[index]["notes"] = note

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def get_steps(self) -> list[dict]:
        """Return a copy of recorded steps ready for save_workflow()."""
        return list(self._steps)

    def get_timeline(self) -> list[dict]:
        """Return steps enriched with icon and label for the UI timeline."""
        result = []
        for i, step in enumerate(self._steps):
            at = step.get("action_type", "")
            result.append({
                **step,
                "index":  i,
                "icon":   ACTION_ICONS.get(at, "▶️"),
                "label":  ACTION_LABELS.get(at, at),
                "order":  i + 1,
            })
        return result

    def to_export_dict(self) -> dict:
        return {
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "base_url":   self._base_url,
            "steps":      self.get_steps(),
        }

    def load_from_workflow(self, workflow) -> None:
        """
        Pre-populate the recorder from an existing Workflow object
        (e.g. when showing the user what was previously recorded).
        """
        self._steps = [s.to_dict() for s in workflow.steps]
        logger.info("[recorder] loaded %d steps from workflow v%s",
                    len(self._steps), workflow.version)


# ---------------------------------------------------------------------------
# Helper: infer semantic action from a raw Playwright-style event dict
# ---------------------------------------------------------------------------

def infer_action_from_event(event: dict) -> Optional[dict]:
    """
    Convert a raw browser event dict into a semantic recorder step.

    event keys expected (all optional):
        type      - 'click' | 'navigate' | 'keypress' | 'input'
        url       - current page URL
        tag       - element tag name (a, button, div ...)
        text      - visible text of the element
        aria      - aria-label value
        role      - ARIA role
        href      - link href (for <a> elements)
        value     - input value (for search boxes)
        selector  - CSS selector

    Returns a step dict or None if the event is not meaningful.
    """
    etype   = event.get("type", "")
    text    = (event.get("text") or "").strip()
    aria    = (event.get("aria") or "").strip()
    role    = (event.get("role") or "").strip().lower()
    tag     = (event.get("tag") or "").strip().lower()
    href    = (event.get("href") or "").strip()
    value   = (event.get("value") or "").strip()
    url     = event.get("url", "")
    sel     = event.get("selector")

    label   = text or aria or value or href

    if etype == "navigate":
        return {
            "action_type":  ACTION_NAVIGATE,
            "target_type":  "url_pattern",
            "target_value": url,
            "page_url":     url,
            "notes":        f"Navigate to {url}",
        }

    if etype == "click":
        lower = label.lower()

        # Cookie banner
        if any(k in lower for k in ("accept", "cookie", "consent", "agree")):
            return {
                "action_type":  ACTION_CLOSE_COOKIE_BANNER,
                "target_type":  "text",
                "target_value": label,
                "selector":     sel,
                "page_url":     url,
                "notes":        "Close cookie banner",
            }

        # Pagination
        if any(k in lower for k in ("next", "load more", "show more", "view more")):
            return {
                "action_type":  ACTION_PAGINATION_NEXT,
                "target_type":  "text",
                "target_value": label,
                "selector":     sel,
                "page_url":     url,
                "notes":        "Continue until pagination ends",
            }

        # View All / expand
        if any(k in lower for k in ("view all", "see all", "show all", "expand")):
            return {
                "action_type":  ACTION_EXPAND_VIEW_ALL,
                "target_type":  "text",
                "target_value": label,
                "selector":     sel,
                "page_url":     url,
                "notes":        f"Expand '{label}'",
            }

        # Navigation / menu (top-level <a> or role=navigation)
        if tag == "a" and href and role in ("", "link"):
            return {
                "action_type":  ACTION_CLICK_MENU,
                "target_type":  "text",
                "target_value": label or href,
                "selector":     sel,
                "page_url":     url,
                "notes":        f"Click {label!r} menu item",
            }

        # Accordion / details toggle
        if role in ("button", "tab") or tag in ("summary", "details"):
            return {
                "action_type":  ACTION_EXPAND_ACCORDION,
                "target_type":  "text",
                "target_value": label,
                "selector":     sel,
                "page_url":     url,
                "notes":        f"Expand {label!r} section",
            }

        # Generic button click — store as click_menu for replay
        if label:
            return {
                "action_type":  ACTION_CLICK_MENU,
                "target_type":  "text",
                "target_value": label,
                "selector":     sel,
                "page_url":     url,
                "notes":        f"Click '{label}'",
            }

    if etype == "input" and value:
        return {
            "action_type":  ACTION_SEARCH,
            "target_type":  "text",
            "target_value": value,
            "selector":     sel,
            "page_url":     url,
            "notes":        f"Search for {value!r}",
        }

    return None
