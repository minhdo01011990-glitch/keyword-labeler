# Releases

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
