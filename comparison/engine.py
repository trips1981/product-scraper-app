"""
comparison/engine.py - Product diff engine.

Compares a current scrape result against the previous snapshot and classifies
every product as one of:
    UNCHANGED         - same name, same URL
    NEW               - appears in current, not in previous
    REMOVED           - in previous, not in current
    RENAMED           - URL matches but name changed
    URL_CHANGED       - name matches but URL changed
    DESCRIPTION_UPDATED - name+URL match, metadata changed

Uses difflib.SequenceMatcher for fuzzy rename/URL detection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Optional

RENAME_THRESHOLD = 0.72   # minimum similarity to call something a rename


class DiffStatus(str, Enum):
    UNCHANGED           = "unchanged"
    NEW                 = "new"
    REMOVED             = "removed"
    RENAMED             = "renamed"
    URL_CHANGED         = "url_changed"
    DESCRIPTION_UPDATED = "description_updated"


STATUS_ICON = {
    DiffStatus.UNCHANGED:           "⚪",
    DiffStatus.NEW:                 "🟢",
    DiffStatus.REMOVED:             "🔴",
    DiffStatus.RENAMED:             "🟡",
    DiffStatus.URL_CHANGED:         "🟠",
    DiffStatus.DESCRIPTION_UPDATED: "🔵",
}

STATUS_LABEL = {
    DiffStatus.UNCHANGED:           "Unchanged",
    DiffStatus.NEW:                 "New",
    DiffStatus.REMOVED:             "Removed",
    DiffStatus.RENAMED:             "Renamed",
    DiffStatus.URL_CHANGED:         "URL Changed",
    DiffStatus.DESCRIPTION_UPDATED: "Updated",
}


@dataclass
class ProductDiff:
    status:       DiffStatus
    name:         str
    url:          str
    prev_name:    str = ""
    prev_url:     str = ""
    confidence:   float = 1.0
    notes:        str = ""

    @property
    def icon(self) -> str:
        return STATUS_ICON.get(self.status, "?")

    @property
    def label(self) -> str:
        return STATUS_LABEL.get(self.status, str(self.status))

    def to_dict(self) -> dict:
        return {
            "status":     self.label,
            "icon":       self.icon,
            "name":       self.name,
            "url":        self.url,
            "prev_name":  self.prev_name,
            "prev_url":   self.prev_url,
            "confidence": round(self.confidence, 2),
            "notes":      self.notes,
        }


@dataclass
class DiffSummary:
    total_current:  int
    total_previous: int
    unchanged:      int
    new:            int
    removed:        int
    renamed:        int
    url_changed:    int
    updated:        int
    diffs:          list[ProductDiff] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return (self.new + self.removed + self.renamed + self.url_changed + self.updated) > 0

    def to_rows(self) -> list[dict]:
        return [d.to_dict() for d in self.diffs]

    def changed_only(self) -> list[ProductDiff]:
        return [d for d in self.diffs if d.status != DiffStatus.UNCHANGED]


def _norm(s: str) -> str:
    """Normalise for comparison: lowercase, collapse whitespace."""
    return " ".join(s.lower().split())


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _url_key(url: str) -> str:
    """Strip scheme + trailing slash for URL matching."""
    u = url.lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    return u


def compare_snapshots(
    current:  list[dict],
    previous: list[dict],
) -> DiffSummary:
    """
    Compare two flat product lists (each item: {"name": str, "link": str}).

    Returns a DiffSummary with a ProductDiff per row.
    """
    # Build lookup tables
    prev_by_url  = {_url_key(p.get("link", "")): p for p in previous}
    prev_by_name = {_norm(p.get("name", "")):     p for p in previous}
    curr_by_url  = {_url_key(c.get("link", "")): c for c in current}
    curr_by_name = {_norm(c.get("name", "")):     c for c in current}

    diffs: list[ProductDiff] = []
    matched_prev_urls: set[str]  = set()
    matched_prev_names: set[str] = set()

    # Pass 1: classify each current product
    for item in current:
        name     = item.get("name", "")
        url      = item.get("link", "")
        url_key  = _url_key(url)
        name_key = _norm(name)

        # Exact match (name + URL)
        if url_key in prev_by_url and _norm(prev_by_url[url_key].get("name", "")) == name_key:
            diffs.append(ProductDiff(DiffStatus.UNCHANGED, name, url, prev_name=name, prev_url=url))
            matched_prev_urls.add(url_key)
            matched_prev_names.add(name_key)
            continue

        # URL matches but name changed -> RENAMED
        if url_key in prev_by_url:
            prev = prev_by_url[url_key]
            sim  = _sim(name, prev.get("name", ""))
            diffs.append(ProductDiff(
                DiffStatus.RENAMED, name, url,
                prev_name=prev.get("name", ""), prev_url=prev.get("link", ""),
                confidence=sim,
                notes=f"Same URL, name changed from {prev.get('name','')!r}",
            ))
            matched_prev_urls.add(url_key)
            matched_prev_names.add(_norm(prev.get("name", "")))
            continue

        # Name matches but URL changed -> URL_CHANGED
        if name_key in prev_by_name:
            prev = prev_by_name[name_key]
            diffs.append(ProductDiff(
                DiffStatus.URL_CHANGED, name, url,
                prev_name=prev.get("name", ""), prev_url=prev.get("link", ""),
                confidence=1.0,
                notes=f"Same name, URL changed from {prev.get('link','')!r}",
            ))
            matched_prev_urls.add(_url_key(prev.get("link", "")))
            matched_prev_names.add(name_key)
            continue

        # Fuzzy name match (potential rename + URL change)
        best_score = 0.0
        best_prev  = None
        for p in previous:
            pk = _url_key(p.get("link", ""))
            if pk in matched_prev_urls:
                continue
            s = _sim(name, p.get("name", ""))
            if s > best_score:
                best_score = s
                best_prev  = p

        if best_prev and best_score >= RENAME_THRESHOLD:
            matched_prev_urls.add(_url_key(best_prev.get("link", "")))
            matched_prev_names.add(_norm(best_prev.get("name", "")))
            diffs.append(ProductDiff(
                DiffStatus.RENAMED, name, url,
                prev_name=best_prev.get("name", ""), prev_url=best_prev.get("link", ""),
                confidence=best_score,
                notes=f"Fuzzy match ({best_score:.0%}): was {best_prev.get('name','')!r}",
            ))
            continue

        # No match at all -> NEW
        diffs.append(ProductDiff(DiffStatus.NEW, name, url, confidence=1.0))

    # Pass 2: previous products not matched -> REMOVED
    for p in previous:
        pk = _url_key(p.get("link", ""))
        pn = _norm(p.get("name", ""))
        if pk not in matched_prev_urls and pn not in matched_prev_names:
            diffs.append(ProductDiff(
                DiffStatus.REMOVED, p.get("name", ""), p.get("link", ""),
                prev_name=p.get("name", ""), prev_url=p.get("link", ""),
                confidence=1.0,
            ))

    # Sort: changes first, then unchanged; within groups alphabetically
    order = {
        DiffStatus.NEW: 0, DiffStatus.REMOVED: 1,
        DiffStatus.RENAMED: 2, DiffStatus.URL_CHANGED: 3,
        DiffStatus.DESCRIPTION_UPDATED: 4, DiffStatus.UNCHANGED: 5,
    }
    diffs.sort(key=lambda d: (order.get(d.status, 9), d.name.lower()))

    counts = {s: 0 for s in DiffStatus}
    for d in diffs:
        counts[d.status] += 1

    return DiffSummary(
        total_current=len(current),
        total_previous=len(previous),
        unchanged=counts[DiffStatus.UNCHANGED],
        new=counts[DiffStatus.NEW],
        removed=counts[DiffStatus.REMOVED],
        renamed=counts[DiffStatus.RENAMED],
        url_changed=counts[DiffStatus.URL_CHANGED],
        updated=counts[DiffStatus.DESCRIPTION_UPDATED],
        diffs=diffs,
    )
