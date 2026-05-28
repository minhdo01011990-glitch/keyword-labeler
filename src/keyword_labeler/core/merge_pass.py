from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

try:
    import anthropic
except ImportError as e:
    raise ImportError("anthropic is required: pip install anthropic") from e

from .data_loader import KeywordRow

__all__ = ["merge_groups", "MergeResult"]

_MERGE_MODEL = "claude-sonnet-4-6"
_MERGE_MAX_TOKENS = 8192
# Each group name ≈ 6–10 tokens; 2,000 groups × 10 tokens ≈ 20k input tokens.
# Output is a flat mapping (canonical_name → [alias, ...]), much smaller.


@dataclass
class MergeResult:
    groups_before: int           # distinct group names before merge
    groups_after: int            # distinct canonical group names after merge
    merges_performed: int        # number of groups that were renamed/merged
    reassigned_count: int        # keywords that received a new (canonical) group name
    merge_map: dict[str, str]    # old_name → canonical_name (identity mappings excluded)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    text = text.strip()
    # Handle preamble text before the JSON object (e.g. Claude adds a sentence first).
    if "{" in text:
        text = text[text.find("{"):text.rfind("}") + 1]
    return text


def _build_merge_system(topic: str) -> str:
    return (
        f'Bạn là chuyên gia SEO tiếng Việt. Chủ đề: "{topic}".\n\n'
        "Nhiệm vụ: Gộp các tên nhóm keyword trùng nghĩa thành một tên chuẩn.\n\n"
        "Quy tắc:\n"
        "1. Chỉ gộp khi hai nhóm thực sự trùng nghĩa hoặc là biến thể cùng intent\n"
        '   Ví dụ: "giá cả - rẻ" + "chi phí - bình dân" → "giá - bình dân"\n'
        "2. Tên chuẩn (canonical) nên là tên ngắn gọn, rõ nghĩa nhất\n"
        "3. Không gộp nhóm chỉ vì tên gần giống — phải cùng intent thực sự\n"
        "4. Nhóm không cần gộp → KHÔNG đưa vào output\n"
        '5. Nếu không cần gộp gì cả → trả về {"merges": []}\n\n'
        'Trả về chỉ JSON (không text khác):\n'
        '{"merges": [{"canonical": "tên chuẩn", "aliases": ["tên cũ 1", "tên cũ 2"]}, ...]}\n\n'
        "Chú ý: mỗi tên nhóm chỉ xuất hiện trong tối đa 1 merge entry."
    )


def _call_merge_api(
    client: anthropic.Anthropic,
    topic: str,
    group_names: list[str],
) -> dict[str, str]:
    """
    Call Claude synchronously to find semantically duplicate group names.

    Returns merge_map: {alias → canonical} for groups that should be renamed.
    Groups not mentioned are left unchanged. Returns {} on API error or parse failure.
    """
    system = _build_merge_system(topic)
    user_content = json.dumps({"groups": group_names}, ensure_ascii=False)

    try:
        response = client.messages.create(
            model=_MERGE_MODEL,
            max_tokens=_MERGE_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception:
        return {}

    text = _strip_json_fence(
        next((b.text for b in response.content if hasattr(b, "text")), "")
    )
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}

    merge_map: dict[str, str] = {}
    seen_aliases: set[str] = set()

    for entry in data.get("merges", []):
        if not isinstance(entry, dict):
            continue
        canonical = str(entry.get("canonical", "")).strip()
        aliases = entry.get("aliases", [])
        if not canonical or not isinstance(aliases, list):
            continue
        for alias in aliases:
            alias = str(alias).strip()
            if not alias or alias == canonical:
                continue
            # Only remap if alias actually exists as a current group name.
            # Check this before seen_aliases so we never "reserve" a non-existent alias.
            if alias not in group_names:
                continue
            if alias in seen_aliases:
                continue
            merge_map[alias] = canonical
            seen_aliases.add(alias)

    return merge_map


def merge_groups(
    keywords: list[KeywordRow],
    topic: str,
    api_key: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> MergeResult:
    """
    Phase 5c: merge semantically duplicate group names and re-assign keywords.

    Collects all distinct non-empty group names from keywords (excluding removed
    rows), sends only the names (not the keywords) to Claude in a single synchronous
    call, then re-maps row.group for any keyword whose group was merged.

    Mutates keywords in place — only row.group is modified.

    Safe to call even if some keywords have empty row.group (they are skipped).
    Returns an empty MergeResult (no-op) if there are no groups to process.

    Args:
        keywords:  list of KeywordRow, mutated in place
        topic:     SEO topic string used in the AI prompt
        api_key:   Anthropic API key (falls back to ANTHROPIC_API_KEY env)
        client:    pre-built Anthropic client (overrides api_key)
    """
    _client = client or anthropic.Anthropic(api_key=api_key)

    # Collect distinct group names from non-removed keywords that have a group.
    group_names_set: set[str] = set()
    for row in keywords:
        if not row.is_removed and row.group:
            group_names_set.add(row.group)

    group_names = sorted(group_names_set)
    groups_before = len(group_names)

    if groups_before == 0:
        return MergeResult(
            groups_before=0,
            groups_after=0,
            merges_performed=0,
            reassigned_count=0,
            merge_map={},
        )

    merge_map = _call_merge_api(_client, topic, group_names)

    # Apply merge_map to keywords.
    reassigned_count = 0
    for row in keywords:
        if not row.is_removed and row.group in merge_map:
            row.group = merge_map[row.group]
            reassigned_count += 1

    # Compute final distinct group count after merge.
    groups_after_set: set[str] = set()
    for row in keywords:
        if not row.is_removed and row.group:
            groups_after_set.add(row.group)
    groups_after = len(groups_after_set)

    return MergeResult(
        groups_before=groups_before,
        groups_after=groups_after,
        merges_performed=len(merge_map),
        reassigned_count=reassigned_count,
        merge_map=merge_map,
    )
