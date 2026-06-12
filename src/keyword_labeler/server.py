from __future__ import annotations

import json
import os
import re
import random
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import anthropic
except ImportError as e:
    raise ImportError("anthropic is required: pip install anthropic") from e

_KEY_FILE = Path.home() / ".anthropic_key"

from mcp.server.fastmcp import FastMCP

from .core.data_loader import load_all, load_spyserp_file as _load_spyserp_file, KeywordRow
from .core.brand_detector import detect_brands
from .core.filter import filter_keywords as _filter_keywords
from .core.spyserp_grouper import group_by_url_overlap
from .core.grouper import group_keywords, build_vocabulary
from .core.merge_pass import merge_groups as _merge_groups
from .core.exporter import export_excel, export_markdown

mcp = FastMCP("keyword-labeler")

# ── Session state (single-user MCP server) ────────────────────────────────────
_state: dict[str, Any] = {
    "keywords": [],           # list[KeywordRow] — pipeline's shared data
    "topic": "",
    "output_format": "excel",
    "original_groups": {},    # {kw_idx: (group, intent)} snapshot after merge_pass; for reset
}


# ── Background grouping job ───────────────────────────────────────────────────
@dataclass
class _GroupingJob:
    thread: threading.Thread
    done: bool = False
    error: str = ""
    progress_done: int = 0
    progress_total: int = 0


_grouping_job: _GroupingJob | None = None

# Protects _state["keywords"] replacement (prevents load_keywords resetting the list
# while a background grouping thread is still mutating individual row attributes).
_state_lock = threading.Lock()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and _KEY_FILE.exists():
        api_key = _KEY_FILE.read_text().strip()
    if api_key:
        return anthropic.Anthropic(api_key=api_key)
    return anthropic.Anthropic()


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    text = text.strip()
    if "{" in text:
        text = text[text.find("{"):text.rfind("}") + 1]
    return text


def _ok(data: dict) -> str:
    return json.dumps({"ok": True, **data}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _keywords() -> list[KeywordRow]:
    with _state_lock:
        return _state["keywords"]


def _group_summary_rows() -> list[dict]:
    """Aggregate non-removed keywords by group. Returns list of dicts sorted by count desc."""
    counter: Counter[str] = Counter()
    volumes: dict[str, int] = {}
    for row in _keywords():
        if row.is_removed:
            continue
        g = row.group or "Chưa phân nhóm"
        counter[g] += 1
        volumes[g] = volumes.get(g, 0) + row.volume
    return [
        {"group": g, "count": c, "volume": volumes[g]}
        for g, c in counter.most_common()
    ]


# ── Tool 0: check_api_key ─────────────────────────────────────────────────────
@mcp.tool()
def check_api_key() -> str:
    """
    Check if ANTHROPIC_API_KEY is configured and working.

    Reads from env var first, then ~/.anthropic_key file.
    Returns: has_key (bool), is_valid (bool), source ("env" | "file" | "none").
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    source = "env" if api_key else "none"

    if not api_key and _KEY_FILE.exists():
        api_key = _KEY_FILE.read_text().strip()
        source = "file" if api_key else "none"

    if not api_key:
        return _ok({"has_key": False, "is_valid": False, "source": "none"})

    try:
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        return _ok({"has_key": True, "is_valid": True, "source": source})
    except anthropic.AuthenticationError:
        return _ok({"has_key": True, "is_valid": False, "source": source,
                    "error": "API key không hợp lệ"})
    except Exception as e:
        return _ok({"has_key": True, "is_valid": False, "source": source, "error": str(e)})


# ── Tool 0b: save_api_key ─────────────────────────────────────────────────────
@mcp.tool()
def save_api_key(api_key: str) -> str:
    """
    Validate and save ANTHROPIC_API_KEY to ~/.anthropic_key.

    Tests the key before saving. On success, also sets it in the current process
    environment so subsequent tools use it without restart.
    Returns: saved (bool), is_valid (bool), file (path).
    """
    key = api_key.strip()
    if not key:
        return _err("API key không được để trống.")

    try:
        client = anthropic.Anthropic(api_key=key)
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
    except anthropic.AuthenticationError:
        return _err("API key không hợp lệ. Kiểm tra lại tại https://console.anthropic.com/settings/keys")
    except Exception as e:
        return _err(f"Lỗi kiểm tra API key: {e}")

    try:
        _KEY_FILE.write_text(key)
        os.environ["ANTHROPIC_API_KEY"] = key
    except Exception as e:
        return _err(f"Lỗi lưu API key: {e}")

    return _ok({"saved": True, "is_valid": True, "file": str(_KEY_FILE)})


# ── MCP Prompt ────────────────────────────────────────────────────────────────
@mcp.prompt(name="keyword")
def keyword_prompt() -> str:
    """Phân nhóm keyword SEO tự động"""
    return """Bạn đang sử dụng **Keyword Labeler** — plugin phân nhóm keyword SEO tự động.

Hãy cung cấp thông tin sau để bắt đầu:

**1. Chủ đề SEO** *(bắt buộc)*
   Ví dụ: "sữa bột cho trẻ em", "laptop gaming", "bất động sản Hà Nội"

**2. File keyword** *(bắt buộc)*
   Đường dẫn tới file Excel hoặc CSV (cột: keyword, volume)
   Ví dụ: /Users/me/keywords.xlsx

**3. File SpySERP** *(tuỳ chọn)*
   Đường dẫn tới file export SpySERP (cột: keyword, url_1..url_10)
   Nhập "bỏ qua" nếu không có

**4. Xử lý branded keyword**
   [1] Tạo nhóm riêng theo từng thương hiệu
   [2] Gộp tất cả vào nhóm "thương hiệu"
   [3] Loại bỏ hoàn toàn
   [4] Giữ nguyên, không xử lý đặc biệt

**5. Ngưỡng volume keyword không dấu** *(default: 100)*
   Keyword không dấu (ví dụ: "sua bot") có volume ≥ ngưỡng sẽ được giữ lại

**6. Output format**
   [1] Excel (.xlsx) — 3 sheet: Labeled / Removed / Summary
   [2] Markdown (.md)

**7. Chạy sample trước?**
   Chạy thử 100 keyword để kiểm tra chất lượng trước khi xử lý toàn bộ (y/n)

---
*Sau khi bạn cung cấp thông tin, plugin sẽ ước tính chi phí API và xin xác nhận trước khi chạy.*
"""


# ── Tool 1: load_keywords ─────────────────────────────────────────────────────
@mcp.tool()
def load_keywords(keyword_path: str, spyserp_path: str = "") -> str:
    """
    Load keyword file (Excel/CSV) and optional SpySERP file into session.

    Deduplicates keywords (keeps max volume). Must be called before any other tools.
    Returns stats: total_raw, duplicates_removed, keyword_count, spyserp_matched,
    zero_volume_count, sample_keywords (first 5 for sanity check).
    """
    if _grouping_job is not None and _grouping_job.thread.is_alive():
        return _err("Grouping đang chạy. Không thể load file mới. Chờ grouping hoàn thành hoặc restart server.")

    try:
        result = load_all(keyword_path, spyserp_path or None)
    except (FileNotFoundError, ValueError) as e:
        return _err(str(e))

    with _state_lock:
        _state["keywords"] = result.keywords
        _state["topic"] = ""
        _state["original_groups"] = {}

    sample = [{"keyword": r.keyword, "volume": r.volume} for r in result.keywords[:5]]
    return _ok({
        "total_raw": result.total_raw,
        "duplicates_removed": result.duplicates_removed,
        "keyword_count": len(result.keywords),
        "spyserp_matched": result.spyserp_matched,
        "zero_volume_count": result.zero_volume_count,
        "sample_keywords": sample,
    })


# ── Tool 2: estimate_cost ─────────────────────────────────────────────────────
@mcp.tool()
def estimate_cost(topic: str, no_diacritic_threshold: int = 100) -> str:
    """
    Estimate Anthropic API cost for processing the currently loaded keywords.

    Estimates are approximate; actual cost depends on keyword length and model output.
    Returns breakdown: filter_usd, grouper_usd, total_usd, estimated_minutes.
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword. Gọi load_keywords() trước.")

    # Count keywords that will go through each phase.
    # No-diacritic filter reduces count first (rough estimate: ~5% of keywords are ASCII-only).
    ascii_removed_est = sum(
        1 for r in kws
        if r.keyword.isascii() and r.volume < no_diacritic_threshold
    )
    n_filter = len(kws) - ascii_removed_est          # keywords sent to AI filter
    n_group = int(n_filter * 0.85)                    # rough: ~15% removed by filter

    # Pricing (Batch API = 50% off standard):
    #   Haiku 4.5 batch: $0.40/Mtok input, $2.00/Mtok output
    #   Sonnet 4.6 batch: $1.50/Mtok input, $7.50/Mtok output

    filter_input_tok = n_filter * 50      # ~50 tokens/kw (system + kw list overhead)
    filter_output_tok = n_filter * 35
    filter_usd = (filter_input_tok / 1_000_000 * 0.40
                  + filter_output_tok / 1_000_000 * 2.00)

    grouper_input_tok = n_group * 50     # ~50 tokens/kw (system + kw + spyserp hints)
    grouper_output_tok = n_group * 30    # ~30 tokens/kw output
    grouper_usd = (grouper_input_tok / 1_000_000 * 1.50
                   + grouper_output_tok / 1_000_000 * 7.50)

    total_usd = filter_usd + grouper_usd
    # Rough time: filter batch ~20 min + grouper batch ~30 min
    est_minutes = 50 if len(kws) > 5000 else 30

    _state["topic"] = topic

    return _ok({
        "keyword_count": len(kws),
        "filter_keywords": n_filter,
        "grouper_keywords": n_group,
        "filter_usd": round(filter_usd, 2),
        "grouper_usd": round(grouper_usd, 2),
        "total_usd": round(total_usd, 2),
        "estimated_minutes": est_minutes,
        "note": "Ước tính bao gồm filter + grouping. Merge pass không đáng kể.",
    })


# ── Tool 3: preprocess_keywords ───────────────────────────────────────────────
@mcp.tool()
def preprocess_keywords(
    topic: str,
    brand_mode: int = 4,
    brands_csv: str = "",
    min_url_overlap: int = 5,
) -> str:
    """
    Run brand detection and SpySERP URL-overlap pre-grouping.

    brand_mode: 1=separate group per brand, 2=all brands → "thương hiệu",
                3=remove branded, 4=detect-only (no action).
    brands_csv: comma-separated brand names (required for modes 1/2/3).
    min_url_overlap: min shared URLs to form a SpySERP pre-group (default 5).

    Returns stats: branded_count, removed_count, spyserp_groups_created, keywords_tagged.
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword. Gọi load_keywords() trước.")

    _state["topic"] = topic

    brands = [b.strip() for b in brands_csv.split(",") if b.strip()] if brands_csv else []

    # Brand detection
    try:
        brand_result = detect_brands(kws, brand_mode, brands)  # type: ignore[arg-type]
    except ValueError as e:
        return _err(str(e))

    # SpySERP pre-grouping (only if any keyword has SpySERP URL data)
    has_spyserp = any(r.spyserp_urls for r in kws)
    spy_result = None
    if has_spyserp:
        try:
            spy_result = group_by_url_overlap(kws, min_overlap=min_url_overlap)
        except ValueError as e:
            return _err(str(e))

    return _ok({
        "branded_count": brand_result.branded_count,
        "brand_removed_count": brand_result.removed_count,
        "brand_groups": brand_result.brand_groups,
        "spyserp_applied": has_spyserp,
        "spyserp_groups_created": spy_result.groups_created if spy_result else 0,
        "spyserp_keywords_tagged": spy_result.keywords_tagged if spy_result else 0,
    })


# ── Tool 4: run_filter ────────────────────────────────────────────────────────
@mcp.tool()
def run_filter(topic: str = "", no_diacritic_threshold: int = 100) -> str:
    """
    Filter bad keywords (misspellings, off-topic, low-volume no-diacritic) via Batch API.

    Blocking — may take 20-30 minutes for large keyword lists.
    State is persisted: if interrupted, call again to resume.
    Returns: kept_count, removed_count, breakdown by removal type.
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword. Gọi load_keywords() trước.")

    t = topic or _state.get("topic", "")
    if not t:
        return _err("Cần cung cấp topic để filter.")

    _state["topic"] = t

    try:
        result = _filter_keywords(kws, topic=t, no_diacritic_threshold=no_diacritic_threshold, client=_get_client())
    except TimeoutError as e:
        return _err(f"Timeout: {e}")
    except Exception as e:
        return _err(f"Lỗi filter: {e}")

    return _ok({
        "kept_count": result.kept_count,
        "removed_count": result.removed_count,
        "no_diacritic_removed": result.no_diacritic_removed,
        "batch_api_removed": result.batch_api_removed,
        "skipped_branded": result.skipped_branded,
        "retry_exhausted_count": result.retry_exhausted_count,
    })


# ── Tool 5: run_sample_grouping ───────────────────────────────────────────────
@mcp.tool()
def run_sample_grouping(topic: str = "", sample_size: int = 100) -> str:
    """
    Group a sample of up to `sample_size` keywords synchronously (one API call).

    Use this to preview grouping quality before running the full Batch API job.
    Returns: groups_created, sample (group_name → keyword_count), vocabulary.
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword. Gọi load_keywords() trước.")

    t = topic or _state.get("topic", "")
    if not t:
        return _err("Cần cung cấp topic.")

    _state["topic"] = t

    eligible = [r for r in kws if not r.is_removed]
    if not eligible:
        return _err("Không có keyword hợp lệ (tất cả đã bị lọc).")

    sample = random.sample(eligible, min(sample_size, len(eligible)))

    client = _get_client()

    # Phase 5a: extract vocabulary + initial group names from sample
    vocabulary, _ = build_vocabulary(sample, t, sample_size=len(sample), client=client)

    vocab_str = ", ".join(f'"{v}"' for v in vocabulary[:50]) if vocabulary else "(tự chọn)"
    system = (
        f'Bạn là chuyên gia SEO tiếng Việt. Phân nhóm keyword cho chủ đề: "{t}".\n\n'
        f"Vocabulary — hậu tố được phép dùng: [{vocab_str}]\n\n"
        "Quy tắc:\n"
        '1. Format nhóm: "chủ đề - hậu tố 1 - hậu tố 2" (hậu tố từ vocabulary)\n'
        "2. Hậu tố 2 tùy chọn — chỉ thêm khi cần phân biệt cụ thể hơn\n"
        "3. Intent: informational / commercial / transactional / navigational\n\n"
        'Trả về chỉ JSON (không text khác):\n'
        '{"results": [{"index": 0, "group": "tên nhóm", "intent": "informational"}, ...]}'
    )
    items = [{"index": i, "keyword": row.keyword} for i, row in enumerate(sample)]

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=system,
            messages=[{
                "role": "user",
                "content": json.dumps({"keywords": items}, ensure_ascii=False),
            }],
        )
        text = _strip_json_fence(
            next((b.text for b in response.content if hasattr(b, "text")), "")
        )
        data = json.loads(text)
    except Exception as e:
        return _err(f"Lỗi API sample grouping: {e}")

    groups: dict[str, list[str]] = {}
    for item in data.get("results", []):
        g = str(item.get("group", "")).strip()
        idx = item.get("index", -1)
        if g and 0 <= idx < len(sample):
            groups.setdefault(g, []).append(sample[idx].keyword)

    group_preview = [
        {"group": g, "keyword_count": len(kws_in_g), "example": kws_in_g[0]}
        for g, kws_in_g in sorted(groups.items(), key=lambda x: -len(x[1]))
    ]

    return _ok({
        "sample_size": len(sample),
        "groups_created": len(groups),
        "vocabulary": vocabulary[:20],
        "groups": group_preview,
    })


# ── Tool 6: start_full_grouping ───────────────────────────────────────────────
@mcp.tool()
def start_full_grouping(topic: str = "") -> str:
    """
    Start Phase 5a (vocabulary building) + Phase 5b (Batch API grouping) in the background.

    Returns immediately. Call poll_grouping_status() to monitor progress.
    If a previous grouping is still running, returns an error.
    State is persisted to disk — if the server restarts mid-job, call this again
    with the same topic to resume.
    """
    global _grouping_job

    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword. Gọi load_keywords() trước.")

    t = topic or _state.get("topic", "")
    if not t:
        return _err("Cần cung cấp topic.")

    if _grouping_job is not None and _grouping_job.thread.is_alive():
        return _err("Grouping đang chạy. Gọi poll_grouping_status() để xem tiến độ.")

    _state["topic"] = t

    job = _GroupingJob(thread=threading.Thread(daemon=True, target=lambda: None))

    def run_grouping() -> None:
        def on_progress(done: int, total: int) -> None:
            job.progress_done = done
            job.progress_total = total

        try:
            group_keywords(kws, t, on_progress=on_progress, client=_get_client())
        except TimeoutError as e:
            job.error = f"Timeout: {e}. Gọi start_full_grouping() để resume."
        except Exception as e:
            job.error = str(e)
        finally:
            job.done = True

    job.thread = threading.Thread(target=run_grouping, daemon=True)
    _grouping_job = job
    job.thread.start()

    eligible_count = sum(1 for r in kws if not r.is_removed)
    n_batches = (eligible_count + 199) // 200

    return _ok({
        "status": "started",
        "eligible_keywords": eligible_count,
        "estimated_batches": n_batches,
        "message": (
            f"Đã bắt đầu grouping {eligible_count} keyword (~{n_batches} batch). "
            "Gọi poll_grouping_status() để theo dõi tiến độ."
        ),
    })


# ── Tool 7: poll_grouping_status ──────────────────────────────────────────────
@mcp.tool()
def poll_grouping_status() -> str:
    """
    Check background grouping status. Returns ONLY lightweight metadata — never keyword data.

    Call this at intervals (5–30 min) to avoid accumulating large conversation context.
    Returns one of:
      status="not_started"  — start_full_grouping() has not been called
      status="running"      — still in progress; fields: progress_done, progress_total
      status="error"        — grouping failed; field: error (string)
      status="complete"     — all done; fields: grouped_count, skipped_count, distinct_groups

    After receiving status="complete", call run_merge_pass() then get_group_summary()
    to retrieve actual group data. Do NOT call this tool in a tight loop.
    """
    global _grouping_job

    if _grouping_job is None:
        return _ok({"status": "not_started"})

    if _grouping_job.thread.is_alive():
        return _ok({
            "status": "running",
            "progress_done": _grouping_job.progress_done,
            "progress_total": _grouping_job.progress_total,
        })

    if _grouping_job.error:
        return _ok({"status": "error", "error": _grouping_job.error})

    # Done — summarise result
    kws = _keywords()
    grouped = sum(1 for r in kws if not r.is_removed and r.group)
    skipped = sum(1 for r in kws if r.is_removed)

    return _ok({
        "status": "complete",
        "grouped_count": grouped,
        "skipped_count": skipped,
        "distinct_groups": len({r.group for r in kws if not r.is_removed and r.group}),
    })


# ── Tool 8: run_merge_pass ────────────────────────────────────────────────────
@mcp.tool()
def run_merge_pass(topic: str = "") -> str:
    """
    Phase 5c: merge semantically duplicate group names (synchronous, one API call).

    Must be called after grouping is complete. Snapshots current groups for reset().
    Returns: groups_before, groups_after, merges_performed, reassigned_count.
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword.")

    t = topic or _state.get("topic", "")
    if not t:
        return _err("Cần cung cấp topic.")

    has_groups = any(r.group for r in kws if not r.is_removed)
    if not has_groups:
        return _err("Chưa có kết quả grouping. Chạy start_full_grouping() và chờ hoàn thành trước.")

    _state["topic"] = t

    try:
        result = _merge_groups(kws, topic=t, client=_get_client())
    except Exception as e:
        return _err(f"Lỗi merge pass: {e}")

    # Snapshot for reset
    _state["original_groups"] = {
        i: (r.group, r.intent) for i, r in enumerate(kws)
    }

    return _ok({
        "groups_before": result.groups_before,
        "groups_after": result.groups_after,
        "merges_performed": result.merges_performed,
        "reassigned_count": result.reassigned_count,
        "merge_map": result.merge_map,
    })


# ── Tool 9: get_group_summary ─────────────────────────────────────────────────
@mcp.tool()
def get_group_summary() -> str:
    """
    Return summary of all groups: name, keyword count, total volume.

    Also includes quality warnings: groups >500 keywords or <5 keywords.
    Sorted by keyword count descending.
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword.")

    rows = _group_summary_rows()
    if not rows:
        return _ok({"groups": [], "total_keywords": 0, "removed_count": 0})

    warnings: list[str] = []
    for r in rows:
        if r["count"] > 500:
            warnings.append(f"Nhóm '{r['group']}' có {r['count']} keyword — cân nhắc chia nhỏ")
        elif r["count"] < 5:
            warnings.append(f"Nhóm '{r['group']}' chỉ có {r['count']} keyword — cân nhắc gộp")

    total_kw = sum(r["count"] for r in rows)
    removed = sum(1 for r in kws if r.is_removed)

    return _ok({
        "group_count": len(rows),
        "total_keywords": total_kw,
        "removed_count": removed,
        "groups": rows,
        "warnings": warnings,
    })


# ── Tool 10: get_group_detail ─────────────────────────────────────────────────
@mcp.tool()
def get_group_detail(group_name: str) -> str:
    """
    Return all keywords in a specific group, sorted by volume descending.

    Returns: group_name, keyword_count, total_volume, keywords list.
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword.")

    members = [
        {"keyword": r.keyword, "volume": r.volume, "intent": r.intent, "brand": r.brand_name}
        for r in kws
        if not r.is_removed and (r.group or "Chưa phân nhóm") == group_name
    ]
    if not members:
        return _err(f"Không tìm thấy nhóm: '{group_name}'")

    members.sort(key=lambda x: -x["volume"])
    total_vol = sum(m["volume"] for m in members)

    return _ok({
        "group_name": group_name,
        "keyword_count": len(members),
        "total_volume": total_vol,
        "keywords": members,
    })


# ── Tool 11: merge_groups_manual ──────────────────────────────────────────────
@mcp.tool()
def merge_groups_manual(group_a: str, group_b: str, new_name: str = "") -> str:
    """
    Merge group_a into group_b. If new_name is given, rename the merged result to new_name.

    All keywords from group_a are reassigned to group_b (or new_name).
    Returns: merged_count, final_group_name.
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword.")

    target = new_name.strip() if new_name.strip() else group_b

    merged = 0
    for row in kws:
        if row.is_removed:
            continue
        if (row.group or "Chưa phân nhóm") == group_a:
            row.group = target
            merged += 1

    if merged == 0:
        return _err(f"Không tìm thấy nhóm '{group_a}'")

    return _ok({"merged_count": merged, "final_group_name": target})


# ── Tool 12: rename_group ─────────────────────────────────────────────────────
@mcp.tool()
def rename_group(old_name: str, new_name: str) -> str:
    """
    Rename a group. All keywords in old_name are reassigned to new_name.

    Returns: renamed_count.
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword.")

    new = new_name.strip()
    if not new:
        return _err("new_name không được để trống.")

    renamed = 0
    for row in kws:
        if not row.is_removed and (row.group or "Chưa phân nhóm") == old_name:
            row.group = new
            renamed += 1

    if renamed == 0:
        return _err(f"Không tìm thấy nhóm '{old_name}'")

    return _ok({"old_name": old_name, "new_name": new, "renamed_count": renamed})


# ── Tool 13: reset_groups ─────────────────────────────────────────────────────
@mcp.tool()
def reset_groups() -> str:
    """
    Reset all groups to the state immediately after merge_pass (before manual edits).

    Only works if run_merge_pass() was called at least once in this session.
    Returns: reset_count.
    """
    kws = _keywords()
    with _state_lock:
        snapshot = dict(_state.get("original_groups", {}))

    if not snapshot:
        return _err("Chưa có snapshot để reset. Chạy run_merge_pass() trước — snapshot bị xoá nếu load_keywords() đã được gọi lại.")

    reset_count = 0
    for i, row in enumerate(kws):
        if i in snapshot:
            group, intent = snapshot[i]
            row.group = group
            row.intent = intent
            reset_count += 1

    return _ok({"reset_count": reset_count, "message": "Đã khôi phục về kết quả sau merge_pass."})


# ── Tool 14: export_results ───────────────────────────────────────────────────
@mcp.tool()
def export_results(output_path: str, topic: str = "") -> str:
    """
    Export labeled results to Excel or Markdown.

    The output format was set when load_keywords() was called (via keyword_prompt).
    Infers format from output_path extension (.xlsx → excel, .md → markdown).
    Returns: output_path, labeled_count, removed_count, group_count.
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword.")

    t = topic or _state.get("topic", "")
    path = Path(output_path)

    try:
        ext = path.suffix.lower()
        if ext in (".xlsx", ".xls"):
            result = export_excel(kws, path)
        elif ext == ".md":
            result = export_markdown(kws, path, topic=t)
        else:
            # Fallback: check _state["output_format"]
            fmt = _state.get("output_format", "excel")
            if fmt == "markdown":
                if not path.suffix:
                    path = path.with_suffix(".md")
                result = export_markdown(kws, path, topic=t)
            else:
                if not path.suffix:
                    path = path.with_suffix(".xlsx")
                result = export_excel(kws, path)
    except Exception as e:
        return _err(f"Lỗi export: {e}")

    return _ok({
        "output_path": str(result.output_path),
        "format": result.format,
        "labeled_count": result.labeled_count,
        "removed_count": result.removed_count,
        "group_count": result.group_count,
    })


# ── SKILL.md compatibility aliases ───────────────────────────────────────────
# SKILL.md references these exact tool names. The aliases forward to the
# canonical implementations above.

@mcp.tool()
def load_spyserp_file(spyserp_path: str) -> str:
    """
    Load SpySERP export and attach URL data to already-loaded keywords.

    Call load_keywords() first. Matches keywords case-insensitively.
    Returns: spyserp_rows (total rows in file), matched_count (keywords updated).
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword. Gọi load_keywords() trước.")
    try:
        spy_data = _load_spyserp_file(spyserp_path)
    except (FileNotFoundError, ValueError) as e:
        return _err(str(e))

    matched = 0
    for row in kws:
        urls = spy_data.get(row.keyword.lower(), [])
        if urls:
            row.spyserp_urls = urls
            matched += 1

    return _ok({"spyserp_rows": len(spy_data), "matched_count": matched})


@mcp.tool()
def load_keyword_file(keyword_path: str, spyserp_path: str = "") -> str:
    """Alias for load_keywords(). Referenced in SKILL.md."""
    return load_keywords(keyword_path, spyserp_path)


@mcp.tool()
def filter_keywords(topic: str = "", no_diacritic_threshold: int = 100) -> str:
    """Alias for run_filter(). Referenced in SKILL.md."""
    return run_filter(topic, no_diacritic_threshold)


@mcp.tool()
def expose_build_vocabulary(topic: str = "", sample_size: int = 1000) -> str:
    """
    Phase 5a: build fixed vocabulary (allowed group-name suffixes) from keyword sample.

    Run this before submit_grouping_batches() to see what suffixes will be used.
    Returns: vocabulary (up to 50 suffixes), sample_groups (30-50 example group names).
    """
    kws = _keywords()
    if not kws:
        return _err("Chưa load keyword. Gọi load_keywords() trước.")

    t = topic or _state.get("topic", "")
    if not t:
        return _err("Cần cung cấp topic.")

    client = _get_client()
    try:
        vocab, sample_groups = build_vocabulary(kws, t, sample_size=sample_size, client=client)
    except Exception as e:
        return _err(f"Lỗi build vocabulary: {e}")

    return _ok({
        "vocabulary_count": len(vocab),
        "vocabulary": vocab,
        "sample_groups": sample_groups[:20],
    })


@mcp.tool()
def submit_grouping_batches(topic: str = "") -> str:
    """Alias for start_full_grouping(). Referenced in SKILL.md."""
    return start_full_grouping(topic)


@mcp.tool()
def poll_batch_status() -> str:
    """Alias for poll_grouping_status(). Returns only metadata, never keyword data. Referenced in SKILL.md."""
    return poll_grouping_status()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
