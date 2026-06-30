"""
workflow/player.py - Semantic workflow player with self-healing.

Replays a list of WorkflowStep objects against a live Playwright page.
Never uses coordinates - locates elements by text, aria-label, CSS selector,
or XPath using a cascading fallback chain.

Self-healing: if the primary strategy fails, the player tries alternative
selectors and reports recovery. Failed steps prompt the user to update
the workflow rather than silently skipping.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from workflow.recorder import (
    ACTION_NAVIGATE, ACTION_CLICK_MENU, ACTION_EXPAND_ACCORDION,
    ACTION_EXPAND_TREE, ACTION_OPEN_TAB, ACTION_SCROLL,
    ACTION_SEARCH, ACTION_PAGINATION_NEXT, ACTION_FILTER_APPLY,
    ACTION_COLLECT_ALL_URLS, ACTION_WAIT, ACTION_IGNORE_POPUP,
    ACTION_CLOSE_COOKIE_BANNER, ACTION_MANUAL_SELECTION,
    ACTION_HOVER, ACTION_TAB_SWITCH, ACTION_BROWSER_BACK,
    ACTION_BROWSER_FORWARD, ACTION_EXPAND_VIEW_ALL, ACTION_AUTO_SCRAPE,
    ACTION_OPEN_PRODUCT_PAGE, ACTION_COLLECT_PRODUCT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step result enum
# ---------------------------------------------------------------------------

class StepResult(str, Enum):
    SUCCESS   = "success"
    FAILED    = "failed"
    RECOVERED = "recovered"
    SKIPPED   = "skipped"


# ---------------------------------------------------------------------------
# Per-step execution report
# ---------------------------------------------------------------------------

@dataclass
class StepReport:
    step_index:    int
    action_type:   str
    target_value:  str
    result:        StepResult
    message:       str = ""
    recovery_note: str = ""
    elapsed_ms:    int = 0


# ---------------------------------------------------------------------------
# Full playback report
# ---------------------------------------------------------------------------

@dataclass
class PlaybackReport:
    workflow_id:    Optional[int]
    company_domain: str
    total_steps:    int
    step_reports:   list[StepReport] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.step_reports if r.result in (StepResult.SUCCESS, StepResult.RECOVERED))

    @property
    def failed(self) -> int:
        return sum(1 for r in self.step_reports if r.result == StepResult.FAILED)

    @property
    def recovered(self) -> int:
        return sum(1 for r in self.step_reports if r.result == StepResult.RECOVERED)

    @property
    def ok(self) -> bool:
        return self.failed == 0


# ---------------------------------------------------------------------------
# Selector fallback strategies
# ---------------------------------------------------------------------------

def _build_selectors(step_dict: dict) -> list[tuple[str, str]]:
    """
    Return an ordered list of (strategy_name, selector_string) to try.
    Each entry is tried in order until one succeeds.
    """
    tv  = (step_dict.get("target_value") or "").strip()
    tt  = (step_dict.get("target_type")  or "auto").strip()
    sel = step_dict.get("selector") or ""

    strategies: list[tuple[str, str]] = []

    # 1. Explicit selectors stored at record time (pipe-separated)
    for s in (sel or "").split("|"):
        s = s.strip()
        if s:
            strategies.append(("stored_selector", s))

    if not tv:
        return strategies

    # 2. Direct match strategies based on target_type
    if tt == "text":
        strategies += [
            ("text_exact",   f"text={tv}"),
            ("aria_label",   f"[aria-label='{tv}']"),
            ("title",        f"[title='{tv}']"),
            ("placeholder",  f"[placeholder='{tv}']"),
        ]
    elif tt == "aria_label":
        strategies += [
            ("aria_label",  f"[aria-label='{tv}']"),
            ("text_exact",  f"text={tv}"),
        ]
    elif tt == "css_selector":
        strategies.append(("css", tv))
    elif tt == "xpath":
        strategies.append(("xpath", f"xpath={tv}"))
    elif tt == "role":
        strategies += [
            ("role",       f"role={tv}"),
            ("aria_role",  f"[role='{tv}']"),
        ]

    # 3. Universal fallbacks for text-bearing elements
    if tv and tt in ("text", "auto"):
        # partial text match
        strategies.append(("text_partial", f"text={tv[:30]}"))
        # by visible text in common interactive elements
        strategies += [
            ("button_text",    f"button:has-text('{tv}')"),
            ("a_text",         f"a:has-text('{tv}')"),
            ("summary_text",   f"summary:has-text('{tv}')"),
            ("li_text",        f"li:has-text('{tv}')"),
            ("span_text",      f"span:has-text('{tv}')"),
            ("div_text",       f"div:has-text('{tv}')"),
        ]

    return strategies


# ---------------------------------------------------------------------------
# WorkflowPlayer
# ---------------------------------------------------------------------------

class WorkflowPlayer:
    """
    Replays a list of step dicts against a live Playwright _PWProxy page.

    Parameters
    ----------
    page_timeout_ms : int
        Timeout for each element lookup (default 5000ms — intentionally
        shorter than the full page timeout so failures are caught quickly).
    on_step_start : callable
        Optional callback (step_index, step_dict) called before each step.
    on_step_done : callable
        Optional callback (step_index, StepReport) called after each step.
    """

    def __init__(
        self,
        page_timeout_ms: int = 5_000,
        on_step_start:   Optional[Callable] = None,
        on_step_done:    Optional[Callable] = None,
    ):
        self._timeout      = page_timeout_ms
        self._on_start     = on_step_start
        self._on_done      = on_step_done

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def play(
        self,
        page,
        steps: list[dict],
        workflow_id: Optional[int] = None,
        company_domain: str = "",
    ) -> PlaybackReport:
        """
        Replay all steps.  Returns a PlaybackReport with per-step results.
        `page` is a BrowserManager._PWProxy wrapping a Playwright Page.
        """
        report = PlaybackReport(
            workflow_id=workflow_id,
            company_domain=company_domain,
            total_steps=len(steps),
        )

        for i, step in enumerate(steps):
            if self._on_start:
                try:
                    self._on_start(i, step)
                except Exception:
                    pass

            t0      = time.monotonic()
            sr      = self._execute_step(page, i, step)
            sr.elapsed_ms = int((time.monotonic() - t0) * 1000)
            report.step_reports.append(sr)

            if self._on_done:
                try:
                    self._on_done(i, sr)
                except Exception:
                    pass

            # Stop on hard failure
            if sr.result == StepResult.FAILED:
                logger.warning(
                    "[player] step %d FAILED (%s %r): %s",
                    i + 1, step.get("action_type"), step.get("target_value"), sr.message,
                )
                # Do not abort - continue trying remaining steps
                # (scraper will still collect whatever is on the page)

        logger.info(
            "[player] playback done: %d/%d succeeded, %d recovered, %d failed",
            report.succeeded, report.total_steps, report.recovered, report.failed,
        )
        return report

    # ------------------------------------------------------------------
    # Step dispatcher
    # ------------------------------------------------------------------

    def _execute_step(self, page, index: int, step: dict) -> StepReport:
        at = step.get("action_type", "")
        tv = step.get("target_value", "")
        tt = step.get("target_type",  "auto")

        try:
            if at == ACTION_NAVIGATE:
                return self._do_navigate(page, index, step)

            if at in (ACTION_CLICK_MENU, ACTION_OPEN_TAB,
                      ACTION_OPEN_PRODUCT_PAGE, ACTION_TAB_SWITCH):
                return self._do_click(page, index, step)

            if at in (ACTION_EXPAND_ACCORDION, ACTION_EXPAND_TREE, ACTION_EXPAND_VIEW_ALL):
                return self._do_click(page, index, step)

            if at == ACTION_SCROLL:
                return self._do_scroll(page, index, step)

            if at == ACTION_SEARCH:
                return self._do_search(page, index, step)

            if at == ACTION_PAGINATION_NEXT:
                return self._do_pagination(page, index, step)

            if at == ACTION_FILTER_APPLY:
                return self._do_click(page, index, step)

            if at == ACTION_WAIT:
                return self._do_wait(page, index, step)

            if at in (ACTION_CLOSE_COOKIE_BANNER, ACTION_IGNORE_POPUP):
                return self._do_dismiss(page, index, step)

            if at == ACTION_BROWSER_BACK:
                return self._do_browser_nav(page, index, step, direction="back")

            if at == ACTION_BROWSER_FORWARD:
                return self._do_browser_nav(page, index, step, direction="forward")

            if at in (ACTION_COLLECT_ALL_URLS, ACTION_COLLECT_PRODUCT,
                      ACTION_MANUAL_SELECTION, ACTION_AUTO_SCRAPE):
                # These are meta-steps handled by the app layer, not the player
                return StepReport(
                    step_index=index, action_type=at, target_value=tv,
                    result=StepResult.SKIPPED,
                    message="Handled by scraper layer",
                )

            # Unknown action — skip gracefully
            return StepReport(
                step_index=index, action_type=at, target_value=tv,
                result=StepResult.SKIPPED,
                message=f"Unknown action type: {at!r}",
            )

        except Exception as exc:
            return StepReport(
                step_index=index, action_type=at, target_value=tv,
                result=StepResult.FAILED,
                message=str(exc),
            )

    # ------------------------------------------------------------------
    # Individual action executors
    # ------------------------------------------------------------------

    def _do_navigate(self, page, index: int, step: dict) -> StepReport:
        url = step.get("target_value", "")
        if not url:
            return StepReport(index, ACTION_NAVIGATE, url, StepResult.SKIPPED, "No URL in step")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2_000)
            return StepReport(index, ACTION_NAVIGATE, url, StepResult.SUCCESS, f"Navigated to {url}")
        except Exception as e:
            return StepReport(index, ACTION_NAVIGATE, url, StepResult.FAILED, str(e))

    def _do_click(self, page, index: int, step: dict) -> StepReport:
        """Try each selector strategy in order; recover if primary fails."""
        at  = step.get("action_type", "click_menu")
        tv  = step.get("target_value", "")
        strategies = _build_selectors(step)

        if not strategies:
            return StepReport(index, at, tv, StepResult.FAILED, "No selectors available")

        first_error = ""
        for strategy_name, selector in strategies:
            try:
                el = page.locator(selector).first
                el.scroll_into_view_if_needed(timeout=self._timeout)
                el.click(timeout=self._timeout)
                page.wait_for_timeout(1_200)

                recovered = strategy_name != "stored_selector" and strategy_name != strategies[0][0]
                result    = StepResult.RECOVERED if recovered else StepResult.SUCCESS
                note      = f"Recovered via {strategy_name}" if recovered else ""
                logger.debug("[player] step %d click OK via %s: %r", index + 1, strategy_name, selector)
                return StepReport(index, at, tv, result, f"Clicked via {strategy_name}", note)

            except Exception as e:
                if not first_error:
                    first_error = str(e)
                logger.debug("[player] step %d selector %r failed: %s", index + 1, selector, e)
                continue

        return StepReport(
            index, at, tv, StepResult.FAILED,
            f"All {len(strategies)} selectors failed. Last: {first_error}",
        )

    def _do_scroll(self, page, index: int, step: dict) -> StepReport:
        direction = step.get("target_value", "down").lower()
        try:
            if direction == "up":
                page.evaluate("window.scrollTo(0, 0)")
            else:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
            return StepReport(index, ACTION_SCROLL, direction, StepResult.SUCCESS, f"Scrolled {direction}")
        except Exception as e:
            return StepReport(index, ACTION_SCROLL, direction, StepResult.FAILED, str(e))

    def _do_search(self, page, index: int, step: dict) -> StepReport:
        query     = step.get("target_value", "")
        strategies = _build_selectors(step)

        # Search-specific selectors
        search_selectors = [
            ("input_search",  "input[type='search']"),
            ("input_text",    "input[type='text']"),
            ("input_query",   "input[name='q']"),
            ("input_generic", "input:visible"),
        ]

        for strategy_name, selector in (strategies + search_selectors):
            try:
                el = page.locator(selector).first
                el.fill(query, timeout=self._timeout)
                el.press("Enter", timeout=self._timeout)
                page.wait_for_timeout(2_000)
                return StepReport(index, ACTION_SEARCH, query, StepResult.SUCCESS, f"Searched for {query!r}")
            except Exception:
                continue

        return StepReport(index, ACTION_SEARCH, query, StepResult.FAILED, "Could not locate search input")

    def _do_pagination(self, page, index: int, step: dict) -> StepReport:
        """
        Click the Next / Load More button repeatedly until it disappears.
        Returns SUCCESS after all pages are expanded.
        """
        label     = step.get("target_value", "Next")
        strategies = _build_selectors(step)

        # Extra pagination-specific selectors
        extra = [
            ("next_link",      "a[rel='next']"),
            ("next_aria",      "[aria-label='Next page']"),
            ("load_more_btn",  "button:has-text('Load more')"),
            ("load_more_link", "a:has-text('Load more')"),
        ]

        rounds = 0
        max_rounds = 50

        while rounds < max_rounds:
            clicked = False
            for strategy_name, selector in (strategies + extra):
                try:
                    el = page.locator(selector).first
                    if el.is_visible(timeout=1_500):
                        el.scroll_into_view_if_needed(timeout=2_000)
                        el.click(timeout=3_000)
                        page.wait_for_timeout(1_500)
                        rounds += 1
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                break  # No more pages

        msg = f"Pagination complete ({rounds} extra pages loaded)"
        logger.info("[player] %s", msg)
        return StepReport(index, ACTION_PAGINATION_NEXT, label, StepResult.SUCCESS, msg)

    def _do_wait(self, page, index: int, step: dict) -> StepReport:
        condition = step.get("wait_condition") or step.get("target_value") or "networkidle"
        try:
            if condition in ("networkidle", "load", "domcontentloaded", "commit"):
                page.wait_for_load_state(condition, timeout=15_000)
            else:
                page.wait_for_timeout(2_000)
            return StepReport(index, ACTION_WAIT, condition, StepResult.SUCCESS, f"Waited for {condition}")
        except Exception as e:
            return StepReport(index, ACTION_WAIT, condition, StepResult.FAILED, str(e))

    def _do_dismiss(self, page, index: int, step: dict) -> StepReport:
        """Try to dismiss a popup or cookie banner."""
        at = step.get("action_type", "")
        tv = step.get("target_value", "")
        strategies = _build_selectors(step)

        dismiss_selectors = [
            ("cookie_accept",  "button:has-text('Accept')"),
            ("cookie_agree",   "button:has-text('Agree')"),
            ("cookie_ok",      "button:has-text('OK')"),
            ("cookie_close",   "button:has-text('Close')"),
            ("cookie_decline", "button:has-text('Decline')"),
            ("dialog_close",   "[aria-label='Close']"),
            ("popup_x",        "button[class*='close']"),
        ]

        for strategy_name, selector in (strategies + dismiss_selectors):
            try:
                el = page.locator(selector).first
                if el.is_visible(timeout=1_500):
                    el.click(timeout=3_000)
                    page.wait_for_timeout(800)
                    return StepReport(index, at, tv, StepResult.SUCCESS, f"Dismissed via {strategy_name}")
            except Exception:
                continue

        # Not finding a dismiss button is not a failure — popup may not appear
        return StepReport(index, at, tv, StepResult.SKIPPED, "No dismissible element found (OK)")

    def _do_browser_nav(self, page, index: int, step: dict, direction: str = "back") -> StepReport:
        at = step.get("action_type", "")
        try:
            if direction == "back":
                page.go_back(wait_until="domcontentloaded", timeout=10_000)
            else:
                page.go_forward(wait_until="domcontentloaded", timeout=10_000)
            page.wait_for_timeout(1_500)
            return StepReport(index, at, "", StepResult.SUCCESS, f"Browser {direction}")
        except Exception as e:
            return StepReport(index, at, "", StepResult.FAILED, str(e))
