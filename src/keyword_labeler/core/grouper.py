from __future__ import annotations

import hashlib
import json
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:
    import anthropic
except ImportError as e:
    raise ImportError("anthropic is required: pip install anthropic") from e

from .batch_manager import BatchManager, BatchManagerResult, DEFAULT_STATE_DIR
from .data_loader import KeywordRow

__all__ = ["group_keywords", "GrouperResult", "build_vocabulary"]

_VOCAB_SAMPLE_SIZE = 1000
_BATCH_SIZE = 200         # keywords per Phase 5b chunk; 200×~30tok ≈ 6k — safe margin below 8192 limit
_PHASE5A_MODEL = "claude-sonnet-4-6"
_PHASE5B_MODEL = "claude-sonnet-4-6"
_PHASE5A_MAX_TOKENS = 4096
# Phase 5b output: ~30 tokens/entry × 200 entries ≈ 6,000 → use 8192 for safety.
_PHASE5B_MAX_TOKENS = 8192
_TOP_N_GROUPS = 50  # number of popular groups injected into each subsequent batch

_INTENTS = frozenset({"informational", "commercial", "transactional", "navigational"})


@dataclass
class GrouperResult:
    grouped_count: int               # keywords assigned a non-empty group
    skipped_count: int               # is_removed=True keywords skipped entirely
    retry_exhausted_count: int       # Phase 5b chunks where all retries failed
    vocabulary: list[str]            # suffixes extracted during Phase 5a
    failed_chunk_indices: list[int] = field(default_factory=list)  # chunks that exhausted retries


# ──────────────────────────────────────────────────────────────────────────────
# Vocabulary persistence helpers
# ──────────────────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    normalized = re.sub(r"\s+", "_", text.strip().lower())
    h = hashlib.md5(text.strip().lower().encode()).hexdigest()[:6]
    return f"{normalized[:33]}_{h}"


def _vocab_path(state_path: Path) -> Path:
    return state_path.with_name(state_path.stem + "_vocab.json")


def _load_vocab(state_path: Path) -> tuple[list[str], list[str]] | None:
    vp = _vocab_path(state_path)
    if not vp.exists():
        return None
    try:
        with open(vp, encoding="utf-8") as f:
            data = json.load(f)
        return (
            [str(v).strip() for v in data.get("vocabulary", []) if v],
            [str(g).strip() for g in data.get("sample_groups", []) if g],
        )
    except (json.JSONDecodeError, OSError):
        return None


def _save_vocab(state_path: Path, vocabulary: list[str], sample_groups: list[str]) -> None:
    vp = _vocab_path(state_path)
    vp.parent.mkdir(parents=True, exist_ok=True)
    with open(vp, "w", encoding="utf-8") as f:
        json.dump(
            {"vocabulary": vocabulary, "sample_groups": sample_groups},
            f,
            ensure_ascii=False,
            indent=2,
        )


def _strip_json_fence(text: str) -> str:
    """Remove markdown code fences that Claude sometimes adds around JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


# ──────────────────────────────────────────────────────────────────────────────
# Phase 5a — vocabulary extraction (synchronous)
# ──────────────────────────────────────────────────────────────────────────────

def build_vocabulary(
    keywords: list[KeywordRow],
    topic: str,
    sample_size: int = _VOCAB_SAMPLE_SIZE,
    client: anthropic.Anthropic | None = None,
    model: str = _PHASE5A_MODEL,
) -> tuple[list[str], list[str]]:
    """
    Send a representative sample of keywords to Claude (synchronous) to extract:
    - vocabulary: fixed list of allowed group-name suffixes (hậu tố), max 50
    - sample_groups: 30-50 example group names (seeds top-50 for Phase 5b round 1)

    Returns (vocabulary, sample_groups). Both lists are empty on API error or
    when no eligible keywords exist — Phase 5b still runs but without vocabulary
    injection (group-name consistency will be lower).
    """
    eligible = [r for r in keywords if not r.is_removed]
    if not eligible:
        return [], []

    sample = random.sample(eligible, min(sample_size, len(eligible)))
    kw_list = [r.keyword for r in sample]

    system = (
        f'Bạn là chuyên gia SEO tiếng Việt. Phân tích keyword cho chủ đề: "{topic}".\n\n'
        "Nhiệm vụ 1 — Vocabulary:\n"
        "Tạo danh sách hậu tố được phép dùng trong tên nhóm keyword.\n"
        '- Format tên nhóm: "chủ đề - hậu tố 1 - hậu tố 2"\n'
        '- Ví dụ hậu tố: ["giá", "lựa chọn", "độ tuổi", "thương hiệu", "review", "mua ở đâu"]\n'
        "- Tối đa 50 hậu tố, ngắn gọn, không trùng nghĩa\n\n"
        "Nhiệm vụ 2 — Sample groups:\n"
        "Đề xuất 30-50 tên nhóm mẫu phù hợp với keyword đại diện.\n"
        '- Ví dụ: "sữa bột - giá - dưới 200k", "sữa bột - lựa chọn - 1 tuổi"\n'
        "- Hậu tố lấy từ vocabulary vừa tạo\n"
        "- Hậu tố 2 chỉ thêm khi cần phân biệt cụ thể hơn\n\n"
        "Quy tắc đặt tên nhóm:\n"
        '- Tên nhóm dùng tiếng Việt (hoặc từ chuyên ngành tiếng Anh như "review", "sale")\n'
        "- Mỗi nhóm đại diện một intent/chiều phân nhóm rõ ràng\n\n"
        'Trả về chỉ JSON (không text khác):\n'
        '{"vocabulary": ["hậu tố 1", ...], "sample_groups": ["tên nhóm 1", ...]}'
    )

    _client = client or anthropic.Anthropic()
    try:
        response = _client.messages.create(
            model=model,
            max_tokens=_PHASE5A_MAX_TOKENS,
            system=system,
            messages=[{
                "role": "user",
                "content": json.dumps(
                    {"topic": topic, "keywords": kw_list},
                    ensure_ascii=False,
                ),
            }],
        )
    except Exception:
        return [], []

    text = _strip_json_fence(
        next((b.text for b in response.content if hasattr(b, "text")), "")
    )
    try:
        data = json.loads(text)
        vocabulary = [str(v).strip() for v in data.get("vocabulary", []) if v][:50]
        sample_groups = [str(g).strip() for g in data.get("sample_groups", []) if g]
    except (json.JSONDecodeError, KeyError, TypeError):
        vocabulary, sample_groups = [], []

    return vocabulary, sample_groups


# ──────────────────────────────────────────────────────────────────────────────
# Phase 5b — prompt builders
# ──────────────────────────────────────────────────────────────────────────────

def _build_phase5b_system(topic: str, vocabulary: list[str], top50: list[str]) -> str:
    vocab_str = (
        ", ".join(f'"{v}"' for v in vocabulary[:50])
        if vocabulary
        else "(chưa có — tự chọn hậu tố phù hợp)"
    )
    top50_section = ""
    if top50:
        groups_list = "\n".join(f"  - {g}" for g in top50[:_TOP_N_GROUPS])
        top50_section = (
            "\n\nNhóm đã có (ưu tiên gộp vào đây, tránh tạo nhóm mới trùng nghĩa):\n"
            + groups_list
        )

    return (
        f'Bạn là chuyên gia SEO tiếng Việt. Phân nhóm keyword cho chủ đề: "{topic}".\n\n'
        f"Vocabulary — hậu tố được phép dùng: [{vocab_str}]{top50_section}\n\n"
        "Quy tắc:\n"
        '1. Phân nhóm TẤT CẢ keyword trong danh sách — không bỏ sót bất kỳ index nào\n'
        '2. Format nhóm: "chủ đề - hậu tố 1 - hậu tố 2" (hậu tố từ vocabulary)\n'
        '   Ví dụ: "sữa bột - giá - dưới 200k", "sữa bột - lựa chọn - 1 tuổi"\n'
        "3. Hậu tố 2 tùy chọn — chỉ thêm khi cần phân biệt cụ thể hơn\n"
        "4. Ưu tiên gộp vào nhóm đã có hơn tạo nhóm mới\n"
        '5. Trường "spy" là gợi ý SpySERP — có thể bỏ qua nếu không phù hợp\n'
        "6. Intent:\n"
        "   - informational: tìm hiểu, học hỏi\n"
        "   - commercial: so sánh, đánh giá trước khi mua\n"
        "   - transactional: muốn mua, đặt hàng ngay\n"
        "   - navigational: tìm trang / thương hiệu cụ thể\n\n"
        'Trả về chỉ JSON (không text khác):\n'
        '{"results": [{"index": 0, "group": "tên nhóm", "intent": "informational"}, ...]}'
    )


def _build_phase5b_user(keywords: list[KeywordRow], kw_indices: list[int]) -> str:
    items: list[dict] = []
    for idx in kw_indices:
        row = keywords[idx]
        item: dict = {"index": idx, "keyword": row.keyword}
        if row.spyserp_group_id:
            item["spy"] = row.spyserp_group_id
        items.append(item)
    return json.dumps({"keywords": items}, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────────────
# Phase 5b — result parser
# ──────────────────────────────────────────────────────────────────────────────

def _extract_partial_items(text: str) -> list[dict]:
    """
    Extract individual JSON objects from a possibly-truncated results array.
    Finds every complete {...} object containing "index" and "group" fields.
    Used as fallback when the outer JSON structure is broken by truncation.
    """
    items = []
    for m in re.finditer(r'\{[^{}]*"index"\s*:\s*\d+[^{}]*\}', text):
        try:
            obj = json.loads(m.group())
            if "index" in obj and "group" in obj:
                items.append(obj)
        except json.JSONDecodeError:
            continue
    return items


def _parse_group_response(text: str) -> dict:
    """
    Parse Phase 5b response.

    First tries strict JSON parse. If that fails (truncated response), falls back
    to partial extraction via regex — salvages all complete entries from the
    truncated text. Only raises if the partial extraction also yields nothing,
    which triggers BatchManager retry.

    Returns {"assignments": {str(index): {"group": str, "intent": str}, ...}}.
    """
    stripped = _strip_json_fence(text)
    try:
        data = json.loads(stripped)
        items = data.get("results", [])
    except json.JSONDecodeError:
        items = _extract_partial_items(stripped)
        if not items:
            raise  # no salvageable data → trigger retry

    assignments: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        idx_raw = item.get("index")
        if idx_raw is None:
            continue
        try:
            idx = str(int(idx_raw))
        except (ValueError, TypeError):
            continue
        group = str(item.get("group", "")).strip()
        intent = str(item.get("intent", "")).strip().lower()
        if intent not in _INTENTS:
            intent = "informational"
        assignments[idx] = {"group": group, "intent": intent}
    return {"assignments": assignments}


# ──────────────────────────────────────────────────────────────────────────────
# Top-50 computation
# ──────────────────────────────────────────────────────────────────────────────

def _compute_top50(partial_result: BatchManagerResult) -> list[str]:
    """Count group name occurrences across all completed chunks; return top N."""
    counter: Counter[str] = Counter()
    for chunk_result in partial_result.results.values():
        for assignment in chunk_result.get("assignments", {}).values():
            g = assignment.get("group", "").strip()
            if g:
                counter[g] += 1
    return [g for g, _ in counter.most_common(_TOP_N_GROUPS)]


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def group_keywords(
    keywords: list[KeywordRow],
    topic: str,
    sample_size: int = _VOCAB_SAMPLE_SIZE,
    state_path: str | None = None,
    api_key: str | None = None,
    client: anthropic.Anthropic | None = None,
    phase5b_model: str = _PHASE5B_MODEL,
    on_progress: Callable[[int, int], None] | None = None,
) -> GrouperResult:
    """
    Phase 5a + 5b: extract vocabulary then batch-group all non-removed keywords.

    Mutates keywords in place — sets row.group and row.intent for each eligible
    keyword. Keywords with is_removed=True are skipped.

    Phase 5a (synchronous): sends up to `sample_size` representative keywords to
    Claude to build a fixed vocabulary (allowed group-name suffixes) and 30-50
    sample group names. Result is persisted to a sidecar JSON file so Phase 5a
    is skipped on resume.

    Phase 5b (Batch API via BatchManager): groups all eligible keywords in chunks
    of 500. Each chunk's system prompt receives the fixed vocabulary from 5a and
    the top-50 most popular groups from all completed chunks so far — this prevents
    naming inconsistency across batches. State is persisted for crash resume.

    IMPORTANT: If the keyword list or eligibility changes between two runs with the
    same state_path, the resume will use the old chunk payloads from the state file.
    Delete the state file (and its _vocab sidecar) to start from scratch.

    Branded keywords (is_branded=True but is_removed=False) are included in
    grouping. If brand_detector already set row.group (e.g. mode-1 brand grouping),
    the AI result will overwrite it. Pre-set row.is_removed=True for branded rows
    you want to exclude before calling this function.

    May raise TimeoutError (propagated from BatchManager) if a batch does not
    complete within 2 hours. State is persisted — resume by calling again with the
    same state_path.

    Args:
        keywords:        list of KeywordRow, mutated in place
        topic:           SEO topic string used in AI prompts and as part of the
                         state file name (e.g. "sữa bột cho trẻ em")
        sample_size:     max keywords sampled for Phase 5a vocabulary building
        state_path:      JSON state file for BatchManager; pass the same path
                         to resume an interrupted run
                         (default: ~/.keyword_labeler/state/grouper_{slug}.json)
        api_key:         Anthropic API key (falls back to ANTHROPIC_API_KEY env)
        client:          pre-built Anthropic client (overrides api_key)
        phase5b_model:   model for Phase 5b batch requests (default: Sonnet 4.6;
                         use Haiku only if cost is critical — Haiku's 8192 output
                         token limit will truncate 500-kw responses)
        on_progress:     optional callable(completed_chunks, total_chunks) — called
                         after each round so the coordinator can report progress

    Returns GrouperResult with aggregate stats.
    """
    _client = client or anthropic.Anthropic(api_key=api_key)

    eligible_indices = [i for i, r in enumerate(keywords) if not r.is_removed]
    skipped_count = len(keywords) - len(eligible_indices)

    slug = _slug(topic)
    job_name = f"grouper_{slug}"
    sp = Path(state_path) if state_path else DEFAULT_STATE_DIR / f"{job_name}.json"

    # ── Phase 5a: load cached or build vocabulary ──────────────────────────
    cached = _load_vocab(sp)
    if cached is not None:
        vocabulary, initial_top50 = cached
    else:
        vocabulary, initial_top50 = build_vocabulary(
            keywords, topic, sample_size=sample_size, client=_client
        )
        _save_vocab(sp, vocabulary, initial_top50)

    # ── Early exit if no keywords to group ────────────────────────────────
    if not eligible_indices:
        return GrouperResult(
            grouped_count=0,
            skipped_count=skipped_count,
            retry_exhausted_count=0,
            vocabulary=vocabulary,
        )

    chunk_payloads = [
        {"kw_indices": eligible_indices[i : i + _BATCH_SIZE]}
        for i in range(0, len(eligible_indices), _BATCH_SIZE)
    ]
    total_chunks = len(chunk_payloads)

    bm = BatchManager.resume_if_exists(sp, job_name=job_name)
    if bm is None:
        bm = BatchManager.create(sp, job_name=job_name, chunk_payloads=chunk_payloads)

    # top50_ref is a mutable list shared between build_requests (read) and
    # on_round_complete (write). build_requests closes over it so each fresh
    # invocation picks up the updated top-50 from the previous round.
    top50_ref: list[str] = list(initial_top50)

    def build_requests(pending_indices: list[int]) -> list[dict]:
        requests = []
        for chunk_idx in pending_indices:
            payload = bm.get_chunk_payload(chunk_idx)
            kw_indices: list[int] = payload["kw_indices"]
            requests.append({
                "custom_id": f"group-chunk-{chunk_idx}",
                "params": {
                    "model": phase5b_model,
                    "max_tokens": _PHASE5B_MAX_TOKENS,
                    "system": _build_phase5b_system(topic, vocabulary, top50_ref),
                    "messages": [{
                        "role": "user",
                        "content": _build_phase5b_user(keywords, kw_indices),
                    }],
                },
            })
        return requests

    def on_round_complete(partial: BatchManagerResult) -> None:
        new_top50 = _compute_top50(partial)
        top50_ref.clear()
        top50_ref.extend(new_top50)
        if on_progress is not None:
            on_progress(partial.completed_count, total_chunks)

    bm.run(
        _client,
        build_requests=build_requests,
        parse_result=_parse_group_response,
        on_round_complete=on_round_complete,
    )

    # ── Apply results to keywords ──────────────────────────────────────────
    final = bm.get_results()
    grouped_count = 0
    for chunk_result in final.results.values():
        for idx_str, assignment in chunk_result.get("assignments", {}).items():
            try:
                row = keywords[int(idx_str)]
            except (ValueError, IndexError):
                continue
            row.group = assignment.get("group", "")
            row.intent = assignment.get("intent", "")
            if row.group:
                grouped_count += 1

    failed_chunk_indices = bm.get_failed_chunk_indices()

    return GrouperResult(
        grouped_count=grouped_count,
        skipped_count=skipped_count,
        retry_exhausted_count=final.retry_exhausted_count,
        vocabulary=vocabulary,
        failed_chunk_indices=failed_chunk_indices,
    )
