# Releases

## v1.0.8 — 2026-06-12

### Fix: keyword lẻ cuối không được gửi batch khi chạy lại cùng topic

**Root cause:** `BatchManager.resume_if_exists()` không kiểm tra `num_chunks` — khi eligible keyword count thay đổi giữa 2 lần chạy cùng topic (ví dụ: filter cho kết quả khác, hoặc dùng file keyword khác), state file cũ có 8 chunks nhưng run mới cần 9 chunks. `resume_if_exists()` load state cũ, `is_complete()` = True ngay lập tức → chunk cuối (50–100–150 keyword) không bao giờ được submit.

**Fixes:**
- `BatchManager.resume_if_exists()`: thêm tham số `num_chunks`; nếu state file có số chunk khác với run hiện tại → trả về `None` để force start fresh
- `grouper.py`: truyền `num_chunks=len(chunk_payloads)` khi gọi `resume_if_exists()`
- `server.py`: sửa `n_batches` display từ `(eligible + 499) // 500` → `(eligible + 199) // 200` (đồng bộ với batch size thực tế 200)

---

## v1.0.7 — 2026-06-11

### UI: chuyển toàn bộ form và bảng sang markdown visualization

Tất cả form, bảng kết quả và thông báo trong SKILL.md được chuyển từ plain-text code block sang markdown tables và formatted output — render đẹp trong Claude Desktop / Cowork plugin.

**Thay đổi:**
- **Bước 1 — Input form**: chuyển từ code block sang markdown bold labels + tables cho lựa chọn [4] và [6]
- **Bước 2 — Load data**: hiển thị stats dạng `| Thống kê | Giá trị |` table
- **Bước 3 — Ước tính chi phí**: table đầy đủ các hạng mục chi phí
- **Bước 4b — Filter**: table tóm tắt kết quả lọc
- **Bước 4c — Sample**: bảng nhóm mẫu thay cho plain-text list
- **Bước 4e — Poll mode**: table chế độ poll, blockquote hướng dẫn thủ công
- **Bước 5 — Review**: 2 tables (thống kê tổng + danh sách nhóm), table lệnh điều khiển
- **Bước 6 — Export**: table thông tin file output
- **Lưu ý lỗi**: table mapping lỗi → xử lý
- Fix metadata version trong SKILL.md từ `1.0.3` → `1.0.6`
- Fix step 4e: batch size `500` → `200` (đồng bộ với fix v1.0.6)

---

## v1.0.6 — 2026-06-05

### Fix grouper: batch size quá lớn làm mất 75% keyword

**Root cause:** Batch size 500 kw × ~30 token/entry ≈ 15,000 token output — vượt giới hạn thực tế của model (~8,192). JSON bị truncate → parse fail → 3 retry đều fail → toàn bộ chunk bị discard. Chạy thực tế: 665 kw → chỉ 171 được gán nhóm, 494 mất.

**Fixes:**
- `_BATCH_SIZE` 500 → **200**: output 200 kw ≈ 6,000 token — an toàn với mọi model
- `_PHASE5B_MAX_TOKENS` 16,000 → **8,192**: đúng với giới hạn thực tế
- Prompt thêm quy tắc: "phân nhóm **TẤT CẢ** keyword, không bỏ sót bất kỳ index nào"
- **Partial-JSON recovery**: nếu response vẫn bị truncate, extract các entry hoàn chỉnh bằng regex thay vì discard toàn bộ chunk

---

## v1.0.5 — 2026-06-05

### Tối ưu token — giảm nguy cơ hit limit Claude Pro

**Poll mode selection (thay đổi lớn nhất):**
- Sau khi submit batch, Claude hỏi user chọn chế độ poll:
  - **Tự động**: chọn interval 5 / 10 / 15 / 20 / 25 / 30 phút — Claude dùng `sleep` giữa các lần poll, giảm số lượt hội thoại từ ~120 xuống còn ~4–6 lần
  - **Thủ công**: Claude cung cấp link Anthropic Console và hướng dẫn, chờ user báo "batch xong" rồi mới poll 1 lần duy nhất
- Mỗi lần poll chỉ hiển thị 1 dòng ngắn, không generate phân tích dài

**Response discipline:**
- Checkpoint sau filter (bước 4b): Claude chỉ viết 2–3 dòng tóm tắt rồi tiếp tục ngay
- Checkpoint sau merge pass (bước 4f): Claude chỉ viết 1–2 dòng tóm tắt rồi chuyển sang review
- Review phase: `get_group_detail` hiển thị tối đa 20 keyword đầu cho nhóm lớn; `get_group_summary` chỉ gọi khi user yêu cầu

**Server:**
- `poll_grouping_status` docstring ghi rõ: chỉ trả lightweight metadata, không bao giờ embed keyword data, không gọi trong tight loop

**Version sync:** đồng bộ `pyproject.toml` và `plugin.json` cùng về 1.0.5 (trước đó lệch nhau 1.0.3 vs 1.0.4)

---

## v1.0.4 — 2026-05-xx

- **Fix install**: thử `uv` trước khi dùng pip; bỏ qua Python candidates có pip bị lỗi
- Bump `plugin.json` lên v1.0.4

---

## v1.0.3 — 2026-05-xx

- **Feat**: thêm field **[8] Thông tin lưu ý** vào input form — cho phép user cung cấp yêu cầu bổ sung (tên nhóm ngắn, ưu tiên intent, giới hạn keyword/nhóm...)
- Claude đọc field này và áp dụng xuyên suốt toàn bộ session

---

## v1.0.2 — 2026-05-xx

- **Fix plugin**: thêm YAML frontmatter vào `SKILL.md` để qua validation của Claude Desktop plugin system

---

## v1.0.1 — 2026-05-xx

- **Feat**: kiểm tra và lưu API key tự động khi khởi động (`check_api_key` + `save_api_key`)
- **Feat**: đóng gói Cowork plugin (`.claude-plugin/`, `plugin.json`)
- **Refactor**: đơn giản hóa install flow theo pattern `seo-audit-plugin`

---

## v1.0.0 — 2026-05-xx

Initial release:
- Load keyword từ Excel/CSV, dedup, lọc volume 0
- Brand detection — 4 chế độ xử lý
- Filter qua Claude Batch API (lọc sai chính tả, không dấu, off-topic)
- SpySERP URL-overlap pre-grouping
- Phase 5a vocabulary building + Phase 5b Batch API grouping (500 kw/batch)
- Merge pass gộp tên nhóm trùng nghĩa
- Review UI trong Claude: xem / merge / rename / reset nhóm
- Export Excel 3 sheet (Labeled / Removed / Summary) hoặc Markdown
