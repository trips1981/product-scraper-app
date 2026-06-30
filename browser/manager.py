"""
browser/manager.py - Playwright BrowserContext pool.

On Microsoft Store Python 3.13 / Windows, Streamlit owns an asyncio loop by the
time app.py runs.  We run Playwright in a dedicated daemon thread that creates its
own event loop (or none at all), serialising all PW calls through a queue.Queue.
Callers receive _PWProxy objects that transparently route calls through the thread.
"""
import logging
import queue
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _PWProxy
# ---------------------------------------------------------------------------

class _PWProxy:
    """
    Wraps any Playwright object and routes every method call / property access
    through the _PlaywrightThread queue so the Streamlit main thread never
    calls Playwright API directly.
    """

    __slots__ = ("_obj", "_thread")

    def __init__(self, obj, thread):
        object.__setattr__(self, "_obj",    obj)
        object.__setattr__(self, "_thread", thread)

    @staticmethod
    def _unwrap(x):
        if isinstance(x, _PWProxy):
            return object.__getattribute__(x, "_obj")
        return x

    def __getattr__(self, name):
        obj    = object.__getattribute__(self, "_obj")
        thread = object.__getattribute__(self, "_thread")
        attr   = getattr(obj, name)

        if callable(attr):
            def _bound(*args, **kwargs):
                real_args   = [self._unwrap(a) for a in args]
                real_kwargs = {k: self._unwrap(v) for k, v in kwargs.items()}
                result = thread.call(attr, *real_args, **real_kwargs)
                if _is_pw_object(result):
                    return _PWProxy(result, thread)
                return result
            return _bound

        def _read():
            return getattr(obj, name)
        result = thread.call(_read)
        if _is_pw_object(result):
            return _PWProxy(result, thread)
        return result

    def __setattr__(self, name, value):
        obj    = object.__getattribute__(self, "_obj")
        thread = object.__getattribute__(self, "_thread")
        real   = self._unwrap(value)
        thread.call(setattr, obj, name, real)

    def __repr__(self):
        return "_PWProxy({!r})".format(object.__getattribute__(self, "_obj"))


def _is_pw_object(obj) -> bool:
    if obj is None:
        return False
    mod = type(obj).__module__ or ""
    return mod.startswith("playwright")


# ---------------------------------------------------------------------------
# _PlaywrightThread
# ---------------------------------------------------------------------------

class _PlaywrightThread(threading.Thread):

    def __init__(self):
        super().__init__(name="playwright-worker", daemon=True)
        self._q         = queue.Queue()
        self._ready     = threading.Event()
        self._pw        = None
        self._start_exc = None

    def run(self):
        import sys
        import traceback as _tb

        # On Windows, app.py sets WindowsSelectorEventLoopPolicy on the main
        # thread to satisfy Streamlit. That policy is process-wide and leaks
        # into this thread. Playwright needs ProactorEventLoop on Windows.
        # We set Proactor policy HERE inside the worker thread to fix it.
        import asyncio
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
            logger.info("[pw-thread] ProactorEventLoop installed in worker thread")
        else:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            logger.info("[pw-thread] new event loop installed in worker thread")

        from playwright.sync_api import sync_playwright
        try:
            self._pw = sync_playwright().start()
            logger.info("[pw-thread] Playwright started OK")
        except Exception as exc:
            self._start_exc = exc
            logger.error("[pw-thread] start failed:\n%s", _tb.format_exc())
            self._ready.set()
            return

        self._ready.set()

        while True:
            item = self._q.get()
            if item is None:
                break
            fn, args, kwargs, result_q = item
            try:
                result_q.put((fn(*args, **kwargs), None))
            except Exception as exc:
                result_q.put((None, exc))

        try:
            self._pw.stop()
        except Exception:
            pass
        logger.info("[pw-thread] Playwright stopped")

    def call(self, fn, *args, timeout=300, **kwargs):
        if not self.is_alive():
            raise RuntimeError("Playwright worker thread has died")
        result_q = queue.Queue()
        self._q.put((fn, args, kwargs, result_q))
        result, exc = result_q.get(timeout=timeout)
        if exc is not None:
            raise exc
        return result

    def stop(self):
        if self.is_alive():
            self._q.put(None)

    @property
    def pw(self):
        return self._pw


# ---------------------------------------------------------------------------
# BrowserManager
# ---------------------------------------------------------------------------

class BrowserManager:
    """
    Manages a single Playwright BrowserContext for the Streamlit session.

    Internally stores real Playwright objects; all mutations run through the
    _PlaywrightThread.  Callers receive _PWProxy objects so chained calls also
    route through the thread automatically.
    """

    def __init__(self):
        self._t         = None
        self._browser_r = None
        self._context_r = None
        self._page_r    = None
        self._headless  = False
        self._channel   = "chromium"

    # internal helpers -------------------------------------------------------

    def _run(self, fn, *args, **kwargs):
        return self._t.call(fn, *args, **kwargs)

    def _proxy(self, obj):
        return _PWProxy(obj, self._t)

    # lifecycle --------------------------------------------------------------

    def start(self, channel="chromium"):
        if self._t is not None and self._t.is_alive():
            return

        self._channel = channel
        self._t = _PlaywrightThread()
        self._t.start()
        self._t._ready.wait(timeout=60)

        if self._t._pw is None:
            exc = self._t._start_exc
            msg = repr(exc) if exc is not None else "timeout after 60s"
            raise RuntimeError(
                "Playwright failed to start in worker thread: {}\n\n"
                "Tip: run  python -m playwright install chromium  then restart.".format(msg)
            )
        logger.info("[browser] BrowserManager ready (channel=%s)", channel)

    def stop(self):
        self._close_context()
        if self._t:
            self._t.stop()
            self._t.join(timeout=10)
            self._t = None
        logger.info("[browser] BrowserManager stopped")

    # context management -----------------------------------------------------

    def _close_context(self):
        if self._t is None or not self._t.is_alive():
            self._page_r = self._context_r = self._browser_r = None
            return
        pg, ctx, bw = self._page_r, self._context_r, self._browser_r
        self._page_r = self._context_r = self._browser_r = None

        def _teardown():
            if pg:
                try:
                    pg.close()
                except Exception:
                    pass
            if ctx:
                try:
                    ctx.close()
                except Exception:
                    pass
            if bw:
                try:
                    bw.close()
                except Exception:
                    pass

        self._run(_teardown)

    def get_or_create_context(self, headless=False):
        if self._t is None or not self._t.is_alive():
            self.start(self._channel)
        if self._context_r is not None and self._headless != headless:
            logger.info("[browser] headless changed - recreating context")
            self._close_context()
        if self._context_r is None:
            self._headless = headless
            pw      = self._t.pw
            channel = self._channel

            def _launch():
                # channel="" or "chromium" → use Playwright's bundled Chromium
                # channel="msedge"/"chrome" → use system-installed browser
                launch_kwargs = {"headless": headless}
                if channel and channel not in ("chromium", ""):
                    launch_kwargs["channel"] = channel
                browser = pw.chromium.launch(**launch_kwargs)
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                return browser, context

            self._browser_r, self._context_r = self._run(_launch)
            logger.info("[browser] new context created (headless=%s)", headless)
        return self._proxy(self._context_r)

    def new_page(self, headless=False):
        self.get_or_create_context(headless=headless)
        if self._page_r:
            pg = self._page_r
            self._page_r = None
            try:
                self._run(pg.close)
            except Exception:
                pass
        self._page_r = self._run(self._context_r.new_page)
        return self._proxy(self._page_r)

    @property
    def page(self):
        return self._proxy(self._page_r) if self._page_r is not None else None

    @property
    def is_ready(self):
        return (
            self._t is not None
            and self._t.is_alive()
            and self._t._pw is not None
        )

    @property
    def has_page(self):
        return self._page_r is not None

    def close_page(self):
        if self._page_r:
            pg           = self._page_r
            self._page_r = None
            try:
                self._run(pg.close)
            except Exception:
                pass

    # navigation -------------------------------------------------------------

    def navigate(self, url, headless=False, timeout_ms=120_000):
        if "/utm_" in url and "?" not in url:
            base, params = url.split("/utm_", 1)
            url = "{0}?utm_{1}".format(base, params)

        self.new_page(headless=headless)
        pg_r = self._page_r

        from browser.js_payloads import HOVER_PICK_JS
        _js  = HOVER_PICK_JS
        _url = url

        logger.info("[browser] navigating to %s", url)

        def _nav():
            pg_r.goto(_url, wait_until="domcontentloaded", timeout=timeout_ms)
            pg_r.wait_for_timeout(5_000)
            pg_r.evaluate(_js)

        self._run(_nav)
        logger.info("[browser] page loaded: %s", url)
        return "Page loaded: {0}".format(url)

    def take_screenshot(self, full_page=True) -> bytes:
        """Return a PNG screenshot of the current page as bytes."""
        if self._page_r is None:
            raise RuntimeError("No page open — call navigate() first")
        pg_r = self._page_r

        def _shot():
            return pg_r.screenshot(full_page=full_page, type="png")

        return self._run(_shot)

    def get_page_title(self) -> str:
        """Return the current page title."""
        if self._page_r is None:
            return ""
        pg_r = self._page_r
        def _title():
            return pg_r.title()
        try:
            return self._run(_title)
        except Exception:
            return ""
