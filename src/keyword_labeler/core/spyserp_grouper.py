from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .data_loader import KeywordRow

__all__ = ["group_by_url_overlap", "SpySERPGroupResult"]

_MIN_OVERLAP = 5
_MAX_URL_FREQUENCY = 200  # URLs shared by more keywords than this are too generic to be useful


def _normalize_url(url: str) -> str:
    url = url.strip().lower().rstrip("/")
    for scheme in ("https://", "http://"):
        if url.startswith(scheme):
            url = url[len(scheme):]
            break
    return url


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]  # path halving
            x = self._parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self._rank[rx] < self._rank[ry]:
            rx, ry = ry, rx
        self._parent[ry] = rx
        if self._rank[rx] == self._rank[ry]:
            self._rank[rx] += 1


@dataclass
class SpySERPGroupResult:
    groups_created: int    # distinct pre-groups with ≥2 members
    keywords_tagged: int   # keywords that received a spyserp_group_id
    keywords_skipped: int  # non-removed keywords with no SpySERP URL data
    removed_count: int     # keywords skipped because is_removed=True


def group_by_url_overlap(
    keywords: list[KeywordRow],
    min_overlap: int = _MIN_OVERLAP,
    max_url_frequency: int = _MAX_URL_FREQUENCY,
) -> SpySERPGroupResult:
    """
    Pre-group keywords by SpySERP URL overlap in place.

    Keywords sharing >= min_overlap common URLs receive the same spyserp_group_id
    (e.g. "spy_1", "spy_2", ...). This is a hint for the AI grouper — not a
    hard constraint; the AI may assign a different final group.

    Skips keywords with is_removed=True. Branded keywords are included because
    their spyserp_group_id may still be useful context for the review UI.

    URL normalization: scheme (http/https) and trailing slash are stripped before
    comparison, so http://example.com/page and https://example.com/page count as
    the same URL. Duplicate URLs within a single keyword are counted only once.

    URLs appearing in more than max_url_frequency keywords are skipped — they are
    too generic (e.g. popular homepages) to be meaningful overlap signals.

    Args:
        keywords:          list of KeywordRow with spyserp_urls populated by data_loader
        min_overlap:       minimum shared URL count to put two keywords in the same group
        max_url_frequency: upper bound on how many keywords a URL may appear in; URLs
                           above this threshold are ignored for grouping purposes

    Returns SpySERPGroupResult with aggregate stats.
    """
    if min_overlap < 2:
        raise ValueError(f"min_overlap phải >= 2, got {min_overlap}")

    # Reset before processing so this function is idempotent when called multiple times.
    for row in keywords:
        row.spyserp_group_id = ""

    n = len(keywords)
    removed_count = sum(1 for r in keywords if r.is_removed)
    keywords_skipped = sum(1 for r in keywords if not r.is_removed and not r.spyserp_urls)

    active_indices = [i for i, r in enumerate(keywords) if not r.is_removed and r.spyserp_urls]

    if not active_indices:
        return SpySERPGroupResult(
            groups_created=0,
            keywords_tagged=0,
            keywords_skipped=keywords_skipped,
            removed_count=removed_count,
        )

    # Build inverted index: normalized_url → list[keyword_index]
    # Each keyword contributes at most once per normalized URL (seen_urls dedup).
    url_to_kws: dict[str, list[int]] = defaultdict(list)
    for i in active_indices:
        seen_urls: set[str] = set()
        for url in keywords[i].spyserp_urls:
            norm = _normalize_url(url)
            if norm and norm not in seen_urls:
                seen_urls.add(norm)
                url_to_kws[norm].append(i)

    # Count shared URL count per (i, j) pair.
    # Skip overly common URLs — they inflate pair counts without grouping signal.
    pair_count: dict[tuple[int, int], int] = defaultdict(int)
    for kw_list in url_to_kws.values():
        if len(kw_list) > max_url_frequency:
            continue
        for a in range(len(kw_list)):
            for b in range(a + 1, len(kw_list)):
                pair_count[(kw_list[a], kw_list[b])] += 1

    # Merge pairs with sufficient overlap using Union-Find.
    uf = _UnionFind(n)
    for (i, j), count in pair_count.items():
        if count >= min_overlap:
            uf.union(i, j)

    # Collect connected components; only components with ≥2 members become groups.
    root_to_members: dict[int, list[int]] = defaultdict(list)
    for i in active_indices:
        root_to_members[uf.find(i)].append(i)

    groups_created = 0
    keywords_tagged = 0
    for members in root_to_members.values():
        if len(members) < 2:
            continue
        groups_created += 1
        group_id = f"spy_{groups_created}"
        for i in members:
            keywords[i].spyserp_group_id = group_id
            keywords_tagged += 1

    return SpySERPGroupResult(
        groups_created=groups_created,
        keywords_tagged=keywords_tagged,
        keywords_skipped=keywords_skipped,
        removed_count=removed_count,
    )
