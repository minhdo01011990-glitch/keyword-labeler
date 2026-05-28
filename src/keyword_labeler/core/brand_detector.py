from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .data_loader import KeywordRow


__all__ = ["detect_brands", "BrandResult", "BRAND_GROUP_ALL"]

BRAND_GROUP_ALL = "thương hiệu"

BrandMode = Literal[1, 2, 3, 4]
# 1 = separate group per brand: "thương hiệu - {Brand}"
# 2 = all brands merged into one group: "thương hiệu"
# 3 = remove branded keywords from pipeline
# 4 = detect-only (tag is_branded / brand_name, no group/remove action)


@dataclass
class BrandResult:
    branded_count: int                    # total keywords detected as branded
    removed_count: int                    # keywords removed (mode 3 only)
    brand_groups: dict[str, int] = field(default_factory=dict)  # {brand_name: kw_count}


def _build_pattern(brand: str) -> re.Pattern[str]:
    escaped = re.escape(brand)
    # ASCII-only word boundary (negative lookbehind/ahead for [A-Za-z0-9]).
    # Covers most brand names (Vinamilk, Abbott, Enfamil, TH, NAN, ...).
    # Limitations:
    #   - IGNORECASE folds ASCII only; brand names must use the same diacritics
    #     as they appear in keywords (e.g. "Cô Gái Hà Lan" not "co gai ha lan").
    #   - Short 2-char brands (e.g. "AG") may produce false positives.
    #   - Common-word brands (e.g. "Nan") require manual result review.
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


def _match_brand(keyword: str, patterns: list[tuple[str, re.Pattern[str]]]) -> str:
    """Return the first matching brand name (first-match-wins), or '' if none."""
    for brand_name, pattern in patterns:
        if pattern.search(keyword):
            return brand_name
    return ""


def detect_brands(
    keywords: list[KeywordRow],
    brand_mode: BrandMode,
    brands: list[str] | None = None,
) -> BrandResult:
    """
    Tag or remove branded keywords in place.

    All modes always set is_branded=True and brand_name on matched keywords so
    downstream modules and the review UI always have brand info available.
    The mode only controls what action follows detection:

      1 — assign group "thương hiệu - {Brand}" (separate group per brand)
      2 — assign group "thương hiệu" (single group for all brands)
      3 — mark is_removed=True, removal_reason="branded"
      4 — detection-only; no group or remove action

    Multi-brand keywords: first-match-wins — only the first brand in the `brands`
    list that matches the keyword is tagged.

    Skips keywords already marked is_removed=True.

    Raises:
        ValueError: if brand_mode is not 1–4, or if brand_mode in (1, 2, 3)
                    and brands is empty.
    """
    if brand_mode not in (1, 2, 3, 4):
        raise ValueError(f"brand_mode phải là 1–4, nhận được: {brand_mode}")

    brands = brands or []
    clean_brands = [b.strip() for b in brands if b.strip()]

    if brand_mode != 4 and not clean_brands:
        raise ValueError(
            f"brand_mode={brand_mode} yêu cầu danh sách brand không rỗng. "
            "Truyền brands=['TênBrand1', ...] hoặc dùng brand_mode=4 để bỏ qua."
        )

    if not clean_brands:
        # mode 4 with no brands: nothing to detect
        return BrandResult(branded_count=0, removed_count=0)

    patterns = [(b, _build_pattern(b)) for b in clean_brands]

    branded_count = 0
    removed_count = 0
    brand_groups: dict[str, int] = {}

    for row in keywords:
        if row.is_removed:
            continue

        brand = _match_brand(row.keyword, patterns)
        if not brand:
            continue

        row.is_branded = True
        row.brand_name = brand
        branded_count += 1
        brand_groups[brand] = brand_groups.get(brand, 0) + 1

        if brand_mode == 1:
            row.group = f"thương hiệu - {brand.title()}"
        elif brand_mode == 2:
            row.group = BRAND_GROUP_ALL
        elif brand_mode == 3:
            row.is_removed = True
            row.removal_reason = "branded"
            removed_count += 1
        # mode 4: detection only

    return BrandResult(
        branded_count=branded_count,
        removed_count=removed_count,
        brand_groups=brand_groups,
    )
