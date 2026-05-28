# Keyword Labeler

**Plugin Claude Desktop / Claude Code** để tự động phân nhóm keyword SEO từ file Excel/CSV (lên đến 10.000 keyword).

> **English summary:** An MCP plugin for Claude Desktop and Claude Code that automatically clusters SEO keywords using Claude Batch API. Input: Excel/CSV with keyword + volume columns. Output: Excel (3 sheets: Labeled / Removed / Summary) or Markdown. Supports SpySERP URL-overlap pre-grouping, brand keyword handling, and Vietnamese no-diacritic filtering.

---

## Tính năng

- **Phân nhóm tự động** theo format `chủ đề - hậu tố 1 - hậu tố 2` (ví dụ: `viêm phụ khoa - dấu hiệu - khi mang thai`)
- **Claude Batch API** — tiết kiệm 50% chi phí so với standard API
- **Vocabulary nhất quán** — Phase 5a build vocabulary cố định, inject vào tất cả batch
- **Merge pass** — gộp tên nhóm trùng nghĩa sau khi phân nhóm
- **SpySERP integration** — pre-group theo URL overlap (≥5 URL chung)
- **Xử lý brand keyword** — 4 chế độ: nhóm riêng / gộp / loại bỏ / giữ nguyên
- **Lọc keyword** — loại sai chính tả, không dấu volume thấp, off-topic
- **Review UI** — xem, merge, rename nhóm ngay trong Claude trước khi export
- **Export** — Excel 3 sheet hoặc Markdown

---

## Yêu cầu

- Python 3.9+
- [Claude Desktop](https://claude.ai/download) hoặc Claude Code
- `ANTHROPIC_API_KEY` trong environment (hoặc file `~/.anthropic_key`)

---

## Cài đặt

### Cách 1 — PyPI (khuyến nghị)

```bash
pip install keyword-labeler
keyword-labeler-install
```

Sau đó **restart Claude Desktop** và gõ `/keyword`.

### Cách 2 — GitHub (clone & install)

```bash
git clone https://github.com/minhdo01011990-glitch/keyword-labeler.git
cd keyword-labeler
bash install.sh
```

Sau đó **restart Claude Desktop** và gõ `/keyword`.

---

## Sử dụng

1. Mở Claude Desktop, gõ `/keyword`
2. Điền form:
   - **Chủ đề SEO** (ví dụ: `sữa bột cho trẻ em`)
   - **Đường dẫn file keyword** (Excel/CSV: cột `keyword` + `volume`)
   - **File SpySERP** (tuỳ chọn)
   - **Xử lý brand**: [1] nhóm riêng / [2] gộp / [3] loại bỏ / [4] giữ nguyên
   - **Ngưỡng volume không dấu** (default: 100)
   - **Output**: Excel hoặc Markdown
3. Xác nhận chi phí ước tính → plugin chạy tự động
4. Review nhóm, merge/rename nếu cần → export

### Format file keyword

| keyword | volume |
|---------|--------|
| sữa bột enfamil | 2400 |
| cách chọn sữa cho bé | 880 |
| sữa bột loại nào tốt | 590 |

### Format output Excel

**Sheet Labeled**: Keyword, Volume, Nhóm, Chủ đề, Hậu tố 1, Hậu tố 2, Intent, Thương hiệu  
**Sheet Removed**: Keyword, Volume, Lý do lọc  
**Sheet Summary**: Nhóm, Số keyword, Tổng volume, Informational, Commercial, Transactional, Navigational

---

## Kiến trúc

```
/keyword (Claude Desktop prompt)
  → data_loader      — đọc Excel/CSV, dedup volume
  → brand_detector   — tag branded keyword
  → filter           — Batch API: lọc sai chính tả, off-topic
  → spyserp_grouper  — URL overlap pre-group (≥5 URL)
  → grouper          — Phase 5a vocab + Phase 5b Batch API
  → merge_pass       — gộp tên nhóm trùng nghĩa
  → review UI        — xem / merge / rename trong Claude
  → exporter         — Excel 3 sheet hoặc Markdown
```

**Stack:** Python + [FastMCP](https://github.com/modelcontextprotocol/python-sdk) + [Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python) + openpyxl + pandas

---

## Chi phí ước tính

Với 1.000 keyword (chủ đề tiếng Việt):
- Filter (Haiku Batch): ~$0.01
- Grouper (Sonnet Batch): ~$0.14
- **Tổng: ~$0.15**

Batch API tự động tiết kiệm 50% so với standard API. Thời gian xử lý: 15–40 phút tùy số lượng keyword.

---

## License

MIT — xem [LICENSE](LICENSE).
