from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import anthropic
except ImportError as e:
    raise ImportError("anthropic is required: pip install anthropic") from e

from .data_loader import KeywordRow

__all__ = ["filter_keywords", "FilterResult"]

_BATCH_SIZE = 500
_MAX_RETRIES = 3
_POLL_INTERVAL = 30           # seconds between Batch API status polls
_MAX_POLLS = 240              # 240 × 30s = 120 minutes max wait per batch
_FILTER_MODEL = "claude-haiku-4-5-20251001"
_FILTER_MAX_TOKENS = 8192     # ~35 tokens/entry × 500 entries ≈ 17,500 → use 8192 (safe for Haiku)


@dataclass
class FilterResult:
    kept_count: int
    removed_count: int
    no_diacritic_removed: int     # removed by volume threshold (ASCII-only keywords)
    batch_api_removed: int        # removed by AI decision
    skipped_branded: int          # branded keywords bypassed (not sent to AI)
    retry_exhausted_count: int    # keywords kept by fallback (max retries hit)


# ---------------------------------------------------------------------------
# Phase 1 — no-diacritic volume filter (synchronous, no AI)
# ---------------------------------------------------------------------------

def _is_ascii_only(text: str) -> bool:
    """
    True if text contains only ASCII characters.

    Used to detect likely "Vietnamese without diacritics" keywords (e.g. "sua bot
    cho tre em").  Note: pure English keywords (e.g. "SEO tools 2024") also return
    True here. They survive if their volume >= threshold, which is the correct
    behavior for a Vietnamese SEO context — low-volume English keywords are
    typically noise. Set no_diacritic_threshold=0 to disable Phase 1 entirely.
    """
    return text.isascii()


def _apply_no_diacritic_filter(keywords: list[KeywordRow], threshold: int) -> int:
    """
    Remove ASCII-only keywords whose volume is below threshold.
    Skips keywords already removed or branded.
    Returns count removed.
    """
    removed = 0
    for row in keywords:
        if row.is_removed or row.is_branded:
            continue
        if _is_ascii_only(row.keyword) and row.volume < threshold:
            row.is_removed = True
            row.removal_reason = f"không dấu, volume {row.volume} < ngưỡng {threshold}"
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# Phase 2 — AI filter via Claude Batch API
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    return re.sub(r"\s+", "_", text.strip().lower())[:40]


def _build_system_prompt(topic: str) -> str:
    return (
        f'Bạn là chuyên gia SEO tiếng Việt. Lọc keyword cho chủ đề: "{topic}".\n\n'
        "Loại bỏ keyword khi:\n"
        "1. Sai chính tả tiếng Việt nghiêm trọng (không thể hiểu hoặc sửa được)\n"
        "2. Hoàn toàn không liên quan đến chủ đề\n"
        "3. Nội dung vô nghĩa, spam\n\n"
        "Giữ lại:\n"
        "- Keyword hợp lệ dù là tiếng Anh hay từ lai Anh-Việt\n"
        "- Keyword liên quan gián tiếp đến chủ đề\n\n"
        'Trả về chỉ JSON (không text khác):\n'
        '{"results": [\n'
        '  {"id": "0", "decision": "keep"},\n'
        '  {"id": "1", "decision": "remove", "reason": "lý do ngắn"}\n'
        "]}"
    )


def _parse_chunk_response(text: str, expected_ids: list[str]) -> dict[str, dict]:
    """
    Parse Claude JSON response for one chunk.
    Returns {kw_index_str: {"decision": "keep"|"remove", "reason": str}}.
    Falls back to "keep" for any missing or unparseable ID — safe default.
    """
    try:
        data = json.loads(text)
        raw: dict[str, dict] = {
            str(item["id"]): item for item in data.get("results", [])
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        return {id_: {"decision": "keep", "reason": ""} for id_ in expected_ids}

    results: dict[str, dict] = {}
    for id_ in expected_ids:
        item = raw.get(id_, {})
        results[id_] = {
            "decision": item.get("decision", "keep"),
            "reason": item.get("reason", ""),
        }
    return results


# ---------------------------------------------------------------------------
# Batch state persistence
# ---------------------------------------------------------------------------
#
# State file schema (JSON):
#   version: int
#   topic: str
#   num_chunks: int               — total chunk count (validated on resume)
#   chunks: list of:
#     index: int                  — 0-based chunk index
#     kw_indices: list[int]       — indices into the keywords list
#     retry_count: int
#     batch_id: str | null        — Anthropic batch ID (shared if submitted together)
#     custom_id: str | null       — per-request custom_id within the batch
#     done: bool
#     results: dict               — {kw_index_str: {decision, reason}}

class _BatchState:

    VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict = {}

    def init(self, topic: str, kw_indices_per_chunk: list[list[int]]) -> None:
        self._data = {
            "version": self.VERSION,
            "topic": topic,
            "num_chunks": len(kw_indices_per_chunk),
            "chunks": [
                {
                    "index": i,
                    "kw_indices": indices,
                    "retry_count": 0,
                    "batch_id": None,
                    "custom_id": None,
                    "done": False,
                    "results": {},
                }
                for i, indices in enumerate(kw_indices_per_chunk)
            ],
        }
        self._save()

    def load(self, topic: str, num_chunks: int) -> None:
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)

        if data.get("version") != self.VERSION:
            raise ValueError(
                f"State file {self.path} có version={data.get('version')}, "
                f"cần version={self.VERSION}. Xóa file này để chạy lại từ đầu."
            )
        if data.get("topic") != topic:
            raise ValueError(
                f"State file {self.path} thuộc topic '{data.get('topic')}', "
                f"khác topic hiện tại '{topic}'. Xóa file này hoặc dùng state_path khác."
            )
        if data.get("num_chunks") != num_chunks:
            raise ValueError(
                f"State file {self.path} có {data.get('num_chunks')} chunks, "
                f"keyword list hiện tại cần {num_chunks} chunks. "
                "Xóa file state để chạy lại từ đầu."
            )
        self._data = data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    @property
    def chunks(self) -> list[dict]:
        return self._data["chunks"]

    def update_submitted(self, chunk_index: int, batch_id: str, custom_id: str) -> None:
        c = self.chunks[chunk_index]
        c["batch_id"] = batch_id
        c["custom_id"] = custom_id
        self._save()

    def update_done(self, chunk_index: int, results: dict[str, dict]) -> None:
        c = self.chunks[chunk_index]
        c["done"] = True
        c["results"] = results
        self._save()

    def update_retry(self, chunk_index: int) -> None:
        c = self.chunks[chunk_index]
        c["retry_count"] += 1
        c["batch_id"] = None
        c["custom_id"] = None
        self._save()

    def needs_submission(self) -> list[dict]:
        """Pending chunks (no batch_id) with retries remaining."""
        return [
            c for c in self.chunks
            if not c["done"]
            and c["batch_id"] is None
            and c["retry_count"] < _MAX_RETRIES
        ]

    def in_flight(self) -> list[dict]:
        """Submitted but not yet done."""
        return [c for c in self.chunks if not c["done"] and c["batch_id"] is not None]

    def in_flight_batch_ids(self) -> set[str]:
        return {c["batch_id"] for c in self.in_flight() if c["batch_id"]}

    def is_complete(self) -> bool:
        return all(
            c["done"] or c["retry_count"] >= _MAX_RETRIES
            for c in self.chunks
        )

    def retry_exhausted_kw_count(self) -> int:
        return sum(
            len(c["kw_indices"])
            for c in self.chunks
            if not c["done"] and c["retry_count"] >= _MAX_RETRIES
        )


# ---------------------------------------------------------------------------
# Batch API orchestration
# ---------------------------------------------------------------------------

def _submit_pending(
    client: anthropic.Anthropic,
    state: _BatchState,
    keywords: list[KeywordRow],
    topic: str,
) -> str:
    """Submit all pending chunks as one Anthropic batch. Returns batch_id."""
    system = _build_system_prompt(topic)
    pending = state.needs_submission()

    requests = []
    for c in pending:
        custom_id = f"chunk-{c['index']}-r{c['retry_count']}"
        kw_items = [
            {"id": str(idx), "keyword": keywords[idx].keyword}
            for idx in c["kw_indices"]
        ]
        requests.append({
            "custom_id": custom_id,
            "params": {
                "model": _FILTER_MODEL,
                "max_tokens": _FILTER_MAX_TOKENS,
                "system": system,
                "messages": [{
                    "role": "user",
                    "content": json.dumps(
                        {"topic": topic, "keywords": kw_items},
                        ensure_ascii=False,
                    ),
                }],
            },
        })

    batch = client.messages.batches.create(requests=requests)
    batch_id = batch.id

    for c in pending:
        custom_id = f"chunk-{c['index']}-r{c['retry_count']}"
        state.update_submitted(c["index"], batch_id, custom_id)

    return batch_id


def _poll_until_ended(client: anthropic.Anthropic, batch_id: str) -> None:
    """Block until batch processing_status == 'ended'. Raises TimeoutError after 2 hours."""
    for _ in range(_MAX_POLLS):
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            return
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(
        f"Batch {batch_id} chưa hoàn thành sau {_MAX_POLLS * _POLL_INTERVAL // 60} phút. "
        "Chạy lại để resume từ state file."
    )


def _apply_batch_results(
    client: anthropic.Anthropic,
    state: _BatchState,
    keywords: list[KeywordRow],
    batch_id: str,
) -> None:
    """Stream batch results and update state; increment retry for errored requests."""
    custom_id_to_chunk: dict[str, int] = {
        c["custom_id"]: c["index"]
        for c in state.in_flight()
        if c["batch_id"] == batch_id and c["custom_id"]
    }

    for result in client.messages.batches.results(batch_id):
        chunk_index = custom_id_to_chunk.get(result.custom_id)
        if chunk_index is None:
            continue

        chunk = state.chunks[chunk_index]
        if result.result.type == "succeeded":
            text = next(
                (b.text for b in result.result.message.content if hasattr(b, "text")),
                "",
            )
            expected_ids = [str(idx) for idx in chunk["kw_indices"]]
            parsed = _parse_chunk_response(text, expected_ids)
            state.update_done(chunk_index, parsed)
        else:
            state.update_retry(chunk_index)


def _run_ai_filter(
    client: anthropic.Anthropic,
    state: _BatchState,
    keywords: list[KeywordRow],
    topic: str,
) -> None:
    """
    Resume any in-flight batches from a previous run, then submit+poll+retry
    until all chunks are done or max retries are exhausted.
    """
    for batch_id in state.in_flight_batch_ids():
        _poll_until_ended(client, batch_id)
        _apply_batch_results(client, state, keywords, batch_id)

    while not state.is_complete():
        if not state.needs_submission():
            break
        batch_id = _submit_pending(client, state, keywords, topic)
        _poll_until_ended(client, batch_id)
        _apply_batch_results(client, state, keywords, batch_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_STATE_DIR = Path.home() / ".keyword_labeler" / "state"


def filter_keywords(
    keywords: list[KeywordRow],
    topic: str,
    no_diacritic_threshold: int = 100,
    state_path: str | None = None,
    api_key: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> FilterResult:
    """
    Filter keywords in place: volume threshold + Claude Batch API.

    Phase 1 (sync): ASCII-only keywords with volume < no_diacritic_threshold are
    removed. Set threshold=0 to disable. Note: pure English keywords are also
    ASCII; they survive if their volume >= threshold (usually the right behavior).

    Phase 2 (Batch API): remaining keywords sent to Claude Haiku in chunks of 500.
    Removes: severe misspellings, off-topic, spam.
    Batch state is persisted to state_path for resume on crash.
    Failed chunks are retried up to _MAX_RETRIES times; if all retries fail,
    those keywords are kept (safe fallback — not silently dropped).

    Skips keywords already marked is_removed=True or is_branded=True.

    Args:
        keywords:               list of KeywordRow, mutated in place
        topic:                  SEO topic for AI context (e.g. "sữa bột cho trẻ em")
        no_diacritic_threshold: min volume for ASCII-only keywords to survive
        state_path:             JSON state file path; reuse same path to resume an
                                interrupted run (default: ~/.keyword_labeler/state/)
        api_key:                Anthropic API key (falls back to ANTHROPIC_API_KEY env var)
        client:                 pre-built Anthropic client (overrides api_key if given)

    Returns FilterResult with aggregate stats.
    """
    # Phase 1
    no_diacritic_removed = _apply_no_diacritic_filter(keywords, no_diacritic_threshold)

    skipped_branded = sum(1 for r in keywords if r.is_branded and not r.is_removed)
    eligible: list[int] = [
        i for i, r in enumerate(keywords)
        if not r.is_removed and not r.is_branded
    ]

    if not eligible:
        kept = sum(1 for r in keywords if not r.is_removed)
        return FilterResult(
            kept_count=kept,
            removed_count=sum(1 for r in keywords if r.is_removed),
            no_diacritic_removed=no_diacritic_removed,
            batch_api_removed=0,
            skipped_branded=skipped_branded,
            retry_exhausted_count=0,
        )

    kw_chunks: list[list[int]] = [
        eligible[i : i + _BATCH_SIZE]
        for i in range(0, len(eligible), _BATCH_SIZE)
    ]

    sp = (
        Path(state_path)
        if state_path
        else _DEFAULT_STATE_DIR / f"filter_{_slug(topic)}.json"
    )
    state = _BatchState(sp)
    if sp.exists():
        state.load(topic, len(kw_chunks))
    else:
        state.init(topic, kw_chunks)

    _client = client or anthropic.Anthropic(api_key=api_key)
    _run_ai_filter(_client, state, keywords, topic)

    # Apply AI decisions
    batch_api_removed = 0
    for chunk in state.chunks:
        if not chunk["done"]:
            continue  # max retries exhausted — keep these (safe fallback)
        for idx_str, decision in chunk["results"].items():
            row = keywords[int(idx_str)]
            if decision["decision"] == "remove" and not row.is_removed:
                row.is_removed = True
                row.removal_reason = decision.get("reason") or "lọc AI"
                batch_api_removed += 1

    kept_count = sum(1 for r in keywords if not r.is_removed)
    return FilterResult(
        kept_count=kept_count,
        removed_count=sum(1 for r in keywords if r.is_removed),
        no_diacritic_removed=no_diacritic_removed,
        batch_api_removed=batch_api_removed,
        skipped_branded=skipped_branded,
        retry_exhausted_count=state.retry_exhausted_kw_count(),
    )
