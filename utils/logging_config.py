"""
utils/logging_config.py — In-memory log capture for the Streamlit debug console.
"""
import logging
from collections import deque
from datetime import datetime


class StreamlitLogHandler(logging.Handler):
    """Captures log records into a bounded in-memory deque for display in the UI."""

    def __init__(self, maxlen: int = 500):
        super().__init__()
        self._records: deque[dict] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord):
        self._records.append({
            "ts":      datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "level":   record.levelname,
            "name":    record.name,
            "message": self.format(record),
        })

    def get_records(self) -> list[dict]:
        return list(self._records)

    def clear(self):
        self._records.clear()


# Singleton handler reused across the Streamlit session
_handler = StreamlitLogHandler()
_handler.setFormatter(logging.Formatter("%(message)s"))


def get_log_handler() -> StreamlitLogHandler:
    return _handler


def setup_logging(level: int = logging.DEBUG):
    root = logging.getLogger()
    if _handler not in root.handlers:
        root.addHandler(_handler)
    root.setLevel(level)
    # Suppress noisy third-party loggers
    for name in ("playwright", "httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
