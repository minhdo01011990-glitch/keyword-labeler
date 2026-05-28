from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path


try:
    import openpyxl
except ImportError as e:
    raise ImportError("openpyxl is required: pip install openpyxl") from e


# ---------------------------------------------------------------------------
# Data types — shared across the entire pipeline
# ---------------------------------------------------------------------------

@dataclass
class KeywordRow:
    keyword: str
    volume: int
    spyserp_urls: list[str] = field(default_factory=list)  # up to 10 URLs

    # brand_detector fills these
    is_branded: bool = False
    brand_name: str = ""

    # filter fills these
    is_removed: bool = False
    removal_reason: str = ""

    # grouper fills these
    spyserp_group_id: str = ""   # pre-group hint from spyserp_grouper
    group: str = ""              # final group name "chủ đề - hậu tố 1 - hậu tố 2"
    intent: str = ""             # informational / commercial / transactional / navigational


@dataclass
class LoadResult:
    keywords: list[KeywordRow]
    total_raw: int           # rows in source file before dedup
    duplicates_removed: int
    spyserp_matched: int     # keywords that have SpySERP URL data
    zero_volume_count: int   # keywords with volume == 0


# ---------------------------------------------------------------------------
# Column name matching
# ---------------------------------------------------------------------------

_KW_ALIASES = {
    "keyword", "keywords", "kw",
    "search term", "search terms",
    "query", "queries",
    "từ khóa", "từ khoá",
    "tu khoa",               # no-diacritic form of both "từ khóa" and "từ khoá"
}

_VOL_ALIASES = {
    "volume", "vol",
    "search volume", "monthly searches",
    "avg. monthly searches", "avg monthly searches",
    "lượt tìm kiếm", "luot tim kiem", "lượt/tháng",
}

_URL_COL_RE = re.compile(r"^url[_ ]?(\d{1,2})$")


def _norm(s: str) -> str:
    return s.strip().lower()


def _find_col(headers: list[str], aliases: set[str]) -> int:
    """Return 0-based index of first matching header, or -1 if not found."""
    for i, h in enumerate(headers):
        if _norm(h) in aliases:
            return i
    return -1


def _find_url_cols(headers: list[str]) -> list[int]:
    """
    Return sorted indices of URL columns (url_1..url_10, url1..url10, etc.).
    Uses strict regex to avoid matching unrelated columns like url_type.
    """
    matched: list[tuple[int, int]] = []  # (col_index, url_number)
    for i, h in enumerate(headers):
        m = _URL_COL_RE.match(_norm(h))
        if m:
            n = int(m.group(1))
            if 1 <= n <= 10:
                matched.append((i, n))
    matched.sort(key=lambda x: x[1])
    return [i for i, _ in matched[:10]]


# ---------------------------------------------------------------------------
# Low-level readers
# ---------------------------------------------------------------------------

def _read_excel(path: Path) -> list[tuple]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        if ws is None:
            raise ValueError(f"File Excel không có sheet active: {path}")
        return [tuple(row) for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _read_csv(path: Path) -> list[tuple]:
    for encoding in ("utf-8-sig", "utf-8", "cp1258", "latin-1"):
        try:
            with open(path, encoding=encoding, newline="") as f:
                return [tuple(row) for row in csv.reader(f)]
        except UnicodeDecodeError:
            continue
    raise ValueError(
        f"Không thể đọc file CSV — vui lòng convert sang UTF-8: {path}"
    )


def _read_file(path: Path) -> list[tuple]:
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls", ".xlsm"):
        return _read_excel(path)
    if ext == ".csv":
        return _read_csv(path)
    raise ValueError(
        f"Định dạng không hỗ trợ: '{ext}'. Dùng .xlsx hoặc .csv"
    )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_keyword_rows(raw: list[tuple]) -> list[tuple[str, int]]:
    """Return list of (keyword, volume) from raw rows (row[0] = header)."""
    if not raw:
        return []

    headers = [str(c) if c is not None else "" for c in raw[0]]
    kw_idx = _find_col(headers, _KW_ALIASES)
    vol_idx = _find_col(headers, _VOL_ALIASES)

    if kw_idx == -1:
        raise ValueError(
            f"Không tìm thấy cột keyword trong file. "
            f"Tên cột hiện có: {headers}. "
            f"Tên cột được nhận dạng: {sorted(_KW_ALIASES)}"
        )

    result: list[tuple[str, int]] = []
    for row in raw[1:]:
        if len(row) <= kw_idx:
            continue
        kw = str(row[kw_idx]).strip() if row[kw_idx] is not None else ""
        if not kw or _norm(kw) in ("none", "nan", "#n/a"):
            continue

        vol = 0
        if vol_idx != -1 and len(row) > vol_idx and row[vol_idx] is not None:
            try:
                vol = max(0, int(float(str(row[vol_idx]).replace(",", "").strip())))
            except (ValueError, TypeError):
                vol = 0

        result.append((kw, vol))

    return result


def _parse_spyserp_rows(raw: list[tuple]) -> dict[str, list[str]]:
    """Return {keyword_lower: [url1, ..., url_n]} (max 10 URLs per keyword)."""
    if not raw:
        return {}

    headers = [str(c) if c is not None else "" for c in raw[0]]
    kw_idx = _find_col(headers, _KW_ALIASES)

    if kw_idx == -1:
        raise ValueError(
            f"SpySERP: không tìm thấy cột keyword. Cột hiện có: {headers}"
        )

    url_indices = _find_url_cols(headers)

    result: dict[str, list[str]] = {}
    for row in raw[1:]:
        if len(row) <= kw_idx:
            continue
        kw = str(row[kw_idx]).strip() if row[kw_idx] is not None else ""
        if not kw or _norm(kw) in ("none", "nan"):
            continue

        urls: list[str] = []
        for ui in url_indices:
            if len(row) > ui and row[ui] is not None:
                url = str(row[ui]).strip()
                if url and _norm(url) not in ("none", "nan", ""):
                    urls.append(url)

        result[kw.lower()] = urls

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_keyword_file(path: str) -> list[tuple[str, int]]:
    """
    Load keyword + volume from an Excel or CSV file.

    Returns list of (keyword, volume) — raw, before dedup.
    Volume is clamped to >= 0.
    Raises FileNotFoundError or ValueError on bad input.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File không tồn tại: {path}")
    return _parse_keyword_rows(_read_file(p))


def load_spyserp_file(path: str) -> dict[str, list[str]]:
    """
    Load SpySERP export (Excel or CSV).

    Returns {keyword_lowercase: [url1, ..., url_n]} (max 10 URLs per keyword).
    Raises FileNotFoundError or ValueError on bad input.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File SpySERP không tồn tại: {path}")
    return _parse_spyserp_rows(_read_file(p))


def merge_and_dedup(
    keyword_pairs: list[tuple[str, int]],
    spyserp: dict[str, list[str]] | None = None,
) -> LoadResult:
    """
    Deduplicate keywords (case-insensitive, keep max volume) and attach
    SpySERP URLs if available.

    Dedup rule: first-seen casing is kept; volume = max across all duplicates.

    Args:
        keyword_pairs: output of load_keyword_file()
        spyserp:       output of load_spyserp_file(), or None

    Returns LoadResult with a clean, deduped keyword list.
    """
    total_raw = len(keyword_pairs)

    # keyword_lower → (original_casing_first_seen, max_volume)
    seen: dict[str, tuple[str, int]] = {}
    for kw, vol in keyword_pairs:
        key = kw.lower()
        if key not in seen:
            seen[key] = (kw, vol)
        elif vol > seen[key][1]:
            seen[key] = (seen[key][0], vol)  # keep original casing, update volume

    duplicates_removed = total_raw - len(seen)
    spyserp = spyserp or {}

    keywords: list[KeywordRow] = []
    spyserp_matched = 0
    zero_volume_count = 0

    for key, (kw, vol) in seen.items():
        urls = spyserp.get(key, [])
        if urls:
            spyserp_matched += 1
        if vol == 0:
            zero_volume_count += 1
        keywords.append(KeywordRow(keyword=kw, volume=vol, spyserp_urls=urls))

    return LoadResult(
        keywords=keywords,
        total_raw=total_raw,
        duplicates_removed=duplicates_removed,
        spyserp_matched=spyserp_matched,
        zero_volume_count=zero_volume_count,
    )


def load_all(
    keyword_path: str,
    spyserp_path: str | None = None,
) -> LoadResult:
    """
    Convenience wrapper: load keyword file + optional SpySERP, then dedup.

    This is the primary entry point for the pipeline.
    """
    pairs = load_keyword_file(keyword_path)
    spyserp = load_spyserp_file(spyserp_path) if spyserp_path else None
    return merge_and_dedup(pairs, spyserp)
