# Project Memory

## Project Overview

Plugin Claude Desktop tên **"keyword"**, gọi bằng lệnh `/keyword`.  
Mục đích: tự động phân nhóm danh sách keyword SEO thô (tối đa 10.000 dòng từ Excel).  
Cài đặt tương tự dự án `url-labeler` của cùng tác giả.

## Architecture & Key Decisions

**Stack:** Python + FastMCP + Anthropic SDK + openpyxl + pandas  
**Mô hình:** Skills layer (Claude Code) + MCP Server layer (FastMCP)  
**Batch strategy:** Claude Batch API (rẻ hơn 50%) — user chấp nhận chờ tối đa 1 giờ  
**SpySERP input:** File export CSV/Excel (không dùng API), URL overlap ≥5 → pre-group hint  

**Quyết định thiết kế quan trọng:**
- Phase 5a: chạy 1.000 keyword đại diện trước để build vocabulary cố định → inject vào tất cả batch sau (tránh naming inconsistency)
- Phase 5c: merge pass synchronous sau khi tất cả batch xong (gộp tên nhóm trùng nghĩa)
- SpySERP pre-group là **hint**, không phải hard constraint — AI có thể override
- Brand keyword: user chọn 1 trong 4 cách xử lý khi nhập input form
- Keyword không dấu tiếng Việt: giữ nếu volume ≥ ngưỡng (user config, default 100)

**Format nhóm output:** `chủ đề - hậu tố 1 - hậu tố 2`  
Ví dụ: "chọn sữa bột cho trẻ 1 tuổi" → `sữa bột - cách lựa chọn - 1 tuổi`

**Excel output:** 3 sheet — Labeled / Removed / Summary  
- Labeled: Keyword, Volume, Nhóm, Chủ đề, Hậu tố 1, Hậu tố 2, Intent, Thương hiệu  
- Removed: Keyword, Volume, Lý do lọc  
- Summary: Nhóm, Số keyword, Tổng volume, Informational/Commercial/Transactional/Navigational

## Data & Workflow

```
/keyword (input form)
  → data_loader (Excel + SpySERP, dedup)
  → brand_detector (tag branded kw)
  → filter (Batch API: lọc sai chính tả, không dấu, off-topic)
  → spyserp_grouper (URL overlap pre-group)
  → grouper Phase 5a (vocabulary building, 1k sample, synchronous)
  → grouper Phase 5b (Batch API, 500 kw/batch, inject vocab + top-50 groups)
  → merge_pass (synchronous, gộp tên nhóm trùng nghĩa)
  → review UI (xem / merge / rename nhóm trong Claude)
  → exporter (Excel 3 sheet hoặc MD)
```

## Coding TODO (thứ tự triển khai)

- [x] **1.** Scaffold: `pyproject.toml`, `install.sh`, `install.py`, `server.py`, `.mcp.json` ✓ reviewed & fixed
- [x] **2.** `skills/keyword/SKILL.md` — định nghĩa lệnh `/keyword` và input form ✓ reviewed
- [x] **3.** `core/data_loader.py` — đọc Excel/CSV keyword + SpySERP, dedup volume ✓ reviewed & fixed
- [x] **4.** `core/brand_detector.py` — detect & tag branded keyword theo lựa chọn user ✓ reviewed & fixed
- [x] **5.** `core/filter.py` — lọc keyword xấu (sai chính tả, không dấu, off-topic) qua Batch API ✓ reviewed & fixed
- [x] **6.** `core/spyserp_grouper.py` — pre-group theo URL overlap ≥5 ✓ reviewed & fixed
- [x] **7.** `core/batch_manager.py` — Batch API submit/poll/retry, persist state JSON ✓ reviewed & fixed
- [x] **8.** `core/grouper.py` — Phase 5a vocabulary building + Phase 5b AI grouping ✓ reviewed & fixed
- [x] **9.** `core/merge_pass.py` — post-process merge tên nhóm trùng nghĩa ✓ reviewed & fixed
- [x] **10.** `core/exporter.py` — xuất Excel 3 sheet + Markdown ✓ reviewed & fixed
- [x] **11.** `agents/keyword-coordinator/` — orchestration + review UI ✓ reviewed & fixed
- [ ] **12.** Test end-to-end: sample 100 keyword → full 10.000 keyword

## Review Log

### Bước 1 & 2 — reviewed by subagent

Lỗi tìm được và đã fix:
- `install.sh`: `keyword-labeler-install` không trong PATH → crash → dùng `$PYTHON -m keyword_labeler.install` làm fallback, export `KEYWORD_LABELER_PROJECT_DIR`
- `install.py`: path `.mcp.json` sai khi non-editable install → đọc từ env var
- `install.py`: Windows APPDATA `KeyError` → dùng `.get()` với fallback
- `pyproject.toml`: `mcp[cli]>=1.0.0` không có upper bound → thêm `<2.0.0`
- `plugin_dir.py`: dead code → xóa
- `.mcp.json`: bị ghi đè sau install → thêm vào `.gitignore`

Reviewer agent: `agents/code-reviewer/AGENT.md`

### Bước 3 — reviewed by subagent

Lỗi tìm được và đã fix:
- `_read_excel`: `wb.active` có thể là `None` → thêm guard, dùng `try/finally` để đảm bảo `wb.close()` luôn được gọi
- `_parse_spyserp_rows`: URL column detection dùng `startswith("url_")` → false positive với cột `url_type`, `url_source` → thay bằng regex `^url[_ ]?(\d{1,2})$`
- `_read_csv`: encoding chỉ handle UTF-8 → thêm fallback `cp1258`, `latin-1` cho file Windows
- `_parse_keyword_rows`: volume âm không bị reject → clamp về 0 bằng `max(0, ...)`
- `_KW_ALIASES`: duplicate alias `"tu khoa"` → xóa bản trùng, thêm comment giải thích
- `KeywordRow`: thiếu field cho downstream pipeline → mở rộng thêm `is_branded`, `brand_name`, `is_removed`, `removal_reason`, `spyserp_group_id`, `group`, `intent`
- `LoadResult`: thiếu stats cho cost estimation → thêm `spyserp_matched`, `zero_volume_count`
- Thiếu `load_all()` convenience function → thêm vào làm entry point chính cho pipeline

Quyết định thiết kế: `KeywordRow` là data class dùng xuyên suốt pipeline (không tạo type riêng mỗi module). `pandas` giữ lại cho `exporter.py`.

### Bước 4 — reviewed by subagent

Lỗi tìm được và đã fix:
- `brands=[]` với mode 1/2/3 → silent failure, không gì bị tagged → raise `ValueError`
- `brand_mode` không validate → mode 0/5 silently no-op → validate `not in (1,2,3,4)` và raise
- Brand name casing không normalize → "vinamilk" vs "Vinamilk" tạo 2 group → dùng `brand.title()` khi gán group
- Mode 4: `is_branded` không được set → review UI mù thông tin → tách detection khỏi action, luôn set `is_branded`/`brand_name`
- `brands=None` từ caller sơ ý → thêm `brands = brands or []` guard
- Comment sai về Vietnamese diacritics với ASCII boundary → sửa comment, document limitation rõ
- Thiếu `Literal[1,2,3,4]` type hint → thêm `BrandMode` type alias
- Thiếu `__all__` → thêm

Quyết định thiết kế: mode 4 vẫn set `is_branded`/`brand_name` (detection-only) để review UI có thông tin brand sau này.

### Bước 7 — reviewed by subagent

Lỗi tìm được và đã fix:
- Thiếu `on_round_complete` hook trong `run()` → top-50 injection không hoạt động (build_requests được gọi fresh nhưng caller không có điểm hook để update top-50 giữa các round) → thêm optional callback `on_round_complete: Callable[[BatchManagerResult], None] | None`
- `resume()` không guard `FileNotFoundError` → confusing error khi file không tồn tại → thêm guard rõ ràng
- Constants `_MAX_RETRIES`, `_POLL_INTERVAL`, `_MAX_POLLS` duplicate với `filter.py` → thêm comment cảnh báo

Thiếu sót thấp đã thêm:
- `get_completed_chunk_indices()` method — tiện cho grouper.py khi cần biết chunks nào done
- `resume_if_exists(path, job_name)` class method — không cần biết num_chunks trước khi resume

Quyết định thiết kế: `batch_manager.py` là generic module cho grouper.py Phase 5b. `filter.py` giữ nguyên `_BatchState` nội bộ riêng (không refactor). Cả hai cùng dùng `~/.keyword_labeler/state/` làm default state dir.

### Bước 6 — reviewed by subagent

Lỗi tìm được và đã fix:
- Không reset `spyserp_group_id` trước khi chạy → stale data nếu gọi lại hàm → thêm reset loop ở đầu hàm (idempotency)
- Thiếu validate `min_overlap >= 2` → `min_overlap=1` tạo hàng nghìn group rác → thêm `ValueError`
- `SpySERPGroupResult` thiếu `removed_count` → stats không đầy đủ cho caller → thêm field

Quyết định thiết kế: Union-Find với global keyword indices (không dùng coordinate compression) — đủ cho 10.000 keyword, code đơn giản hơn. Group ID stable theo thứ tự input list (chấp nhận vì là hint, không phải hard constraint).

### Bước 5 — reviewed by subagent

Lỗi tìm được và đã fix:
- `max_tokens=2048` quá nhỏ với 500 kw (~17,500 tokens cần) → JSON bị truncate, phase 2 vô hiệu → tăng lên 8192
- `_poll_until_ended` loop vô hạn nếu batch bị stuck → thêm `_MAX_POLLS=240` và raise `TimeoutError` sau 2 giờ
- State file lưu vào `cwd` không ổn định khi MCP server restart → dùng `~/.keyword_labeler/state/` làm default
- Resume không validate version/topic/num_chunks → data corruption âm thầm nếu topic đổi → thêm validation trong `_BatchState.load()`
- `FilterResult` thiếu `retry_exhausted_count` → thêm field
- `filter_keywords` không nhận `client` từ ngoài → thêm param `client: anthropic.Anthropic | None`

Quyết định thiết kế: `_is_ascii_only` giữ nguyên (không thêm heuristic phân biệt "không dấu tiếng Việt" vs "tiếng Anh") — set `threshold=0` để disable Phase 1 nếu cần. State file batch được lưu vào `~/.keyword_labeler/state/` để persist qua restart.

### Bước 8 — reviewed by subagent

Lỗi tìm được và đã fix:
- Early-exit condition sai `if not eligible_indices and not sp.exists()` → đổi thành `if not eligible_indices: return` đơn giản
- `keywords[int(idx_str)]` không có guard → thêm `try/except (ValueError, IndexError): continue`
- `json.loads()` crash nếu Claude bọc response trong markdown fence → thêm `_strip_json_fence()` helper, dùng ở cả Phase 5a và 5b
- `_slug()` 40 ký tự có thể collision giữa 2 topic khác nhau → thêm hash suffix md5[:6]
- Vocabulary không clamp tại source → thêm `[:50]` trong `build_vocabulary()`
- Thiếu `on_progress` callback → thêm `on_progress: Callable[[int, int], None] | None` param cho coordinator report tiến độ
- `GrouperResult` thiếu `failed_chunk_indices` → thêm field, thêm `get_failed_chunk_indices()` vào `BatchManager`

Quyết định thiết kế: Phase 5a vocabulary persist vào sidecar file `{slug}_vocab.json` (cùng thư mục state) để skip khi resume. `_PHASE5B_MAX_TOKENS = 16000` vì 500 kw × ~30 tokens/entry ≈ 15,000 tokens (Haiku limit 8192 sẽ truncate — dùng Sonnet 4.6 mặc định). `_parse_group_response` cố ý raise `json.JSONDecodeError` để trigger BatchManager retry. Thay đổi input giữa 2 lần chạy cùng state_path → phải xóa state file thủ công (documented trong docstring).

### Bước 10 — reviewed by subagent

Lỗi tìm được:
- Document: memory.md ghi "hậu tố" (singular) nhưng Labeled sheet xuất 2 cột riêng "Hậu tố 1" + "Hậu tố 2" (phù hợp format `chủ đề - hậu tố 1 - hậu tố 2`) → đã cập nhật memory.md cho đúng

Không có lỗi crash hay lỗi quan trọng về code.

Quyết định thiết kế: `_df_to_markdown()` tự implement (không dùng `pd.to_markdown()`) để tránh thêm dependency `tabulate`. Markdown output bỏ bớt cột (Chủ đề, Hậu tố 1/2) để bảng gọn hơn. Intent breakdown trong Summary sheet là 4 cột riêng: Informational / Commercial / Transactional / Navigational.

### Bước 11 — reviewed by subagent

Lỗi tìm được và đã fix:
- Race condition: `load_keywords()` không guard khi grouping thread đang chạy → thêm check + `_state_lock` bảo vệ thay thế `_state["keywords"]`
- Tool names không khớp SKILL.md → thêm alias tools: `load_keyword_file`, `load_spyserp_file`, `filter_keywords`, `expose_build_vocabulary`, `submit_grouping_batches`, `poll_batch_status`
- Logic bug `merge_groups_manual()`: `elif` branch âm thầm rename cả group_b → xoá branch đó, chỉ move group_a → target
- `reset_groups()` thiếu guard khi snapshot rỗng → thêm kiểm tra + thông báo rõ ràng
- `load_spyserp_file` tool thiếu (SKILL.md yêu cầu riêng, không gộp vào load_keywords) → thêm tool riêng
- `expose_build_vocabulary` tool thiếu → thêm (Phase 5a preview trước khi submit batches)

Quyết định thiết kế: Background thread dùng cho Phase 5a+5b grouping (`group_keywords()` blocking). `_state_lock` chỉ bảo vệ việc thay thế list reference (không lock toàn bộ mutation vì CPython GIL đủ an toàn cho attribute reads/writes trên objects). SKILL.md tool names được giữ bằng alias wrappers (không đổi tên canonical functions).

### Bước 9 — reviewed by subagent

Lỗi tìm được và đã fix:
- `_strip_json_fence` không handle text preamble trước `{` (Claude đôi khi thêm câu dẫn trước JSON) → thêm fallback extract từ `{` đến `}` cuối cùng
- Điều kiện alias dedup: check `seen_aliases` trước `group_names` → alias không tồn tại sẽ "chiếm" slot trong `seen_aliases` → reorder: check `group_names` trước, rồi mới check `seen_aliases`
- System prompt không quy định rõ behavior khi không cần gộp → thêm rule "Nếu không cần gộp gì → trả về `{\"merges\": []}`"

Quyết định thiết kế: `merge_groups()` là synchronous (không dùng Batch API) — chỉ gửi tên nhóm (không phải keyword) trong một call. `merge_map` trong MergeResult dùng cho review UI. Reviewer gợi ý đổi model name nhưng `claude-sonnet-4-6` đã nhất quán trong toàn codebase và đúng với environment.

## In Progress

Bước 12: Test end-to-end — sample 100 keyword → full 10.000 keyword

## Conventions

- Đọc Excel bằng `openpyxl` read-only mode (tối ưu bộ nhớ cho file lớn)
- Persist batch state vào file JSON (resume nếu lỗi giữa chừng)
- Mỗi batch 500 keyword
- Top 50 nhóm phổ biến nhất được inject vào system prompt của batch tiếp theo
- Auto-retry batch lỗi tối đa 3 lần
