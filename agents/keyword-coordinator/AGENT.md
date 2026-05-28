# Keyword Coordinator Agent

Tài liệu này mô tả cách coordinator (Claude, theo SKILL.md) sử dụng các MCP tool trong `server.py` để orchestrate pipeline phân nhóm keyword.

---

## MCP Tools — Tổng quan

| Tool | Mô tả | Blocking? |
|------|--------|-----------|
| `load_keyword_file` | Load file keyword + SpySERP (một lần), dedup | Nhanh |
| `load_spyserp_file` | Attach SpySERP URLs vào keyword đã load | Nhanh |
| `estimate_cost` | Ước tính chi phí API | Nhanh |
| `preprocess_keywords` | Brand detection + SpySERP pre-group | Nhanh |
| `filter_keywords` | Lọc keyword xấu (Haiku Batch API) | ~20-30 phút |
| `run_sample_grouping` | Preview grouping 100 keyword | ~1-2 phút |
| `expose_build_vocabulary` | Phase 5a: build vocabulary | ~1-2 phút |
| `submit_grouping_batches` | Bắt đầu Phase 5b (background) | Ngay lập tức |
| `poll_batch_status` | Kiểm tra tiến độ grouping | Nhanh |
| `run_merge_pass` | Gộp nhóm trùng nghĩa (Phase 5c) | ~1 phút |
| `get_group_summary` | Xem tóm tắt tất cả nhóm | Nhanh |
| `get_group_detail` | Xem keyword trong một nhóm | Nhanh |
| `merge_groups_manual` | Gộp 2 nhóm thủ công | Nhanh |
| `rename_group` | Đổi tên nhóm | Nhanh |
| `reset_groups` | Huỷ thay đổi thủ công, về sau merge_pass | Nhanh |
| `export_results` | Xuất Excel/Markdown | Nhanh |

---

## Workflow chi tiết

### Bước 1 — Thu thập input và load file

```
load_keyword_file(keyword_path=...)
```

Nếu có SpySERP file, gọi thêm:
```
load_spyserp_file(spyserp_path=...)
```

Kiểm tra `ok=true`. Nếu lỗi: hỏi lại đường dẫn.  
Hiển thị: tổng keyword, trùng đã loại, sample 5 keyword đầu.

### Bước 2 — Ước tính chi phí

```
estimate_cost(topic=..., no_diacritic_threshold=...)
```

Hiển thị breakdown và hỏi xác nhận trước khi chạy Batch API.

### Bước 3 — Pre-processing

```
preprocess_keywords(
    topic=...,
    brand_mode=...,          # 1/2/3/4 từ input form
    brands_csv=...,          # tên thương hiệu, ngăn cách bằng dấu phẩy
    min_url_overlap=5,
)
```

Nếu brand_mode là 1/2/3 và user chưa cung cấp brands_csv: hỏi danh sách thương hiệu trước khi gọi tool.

### Bước 4 — Filter keyword xấu

```
filter_keywords(topic=..., no_diacritic_threshold=...)
```

Báo trước: "Đang lọc... có thể mất 20-30 phút."  
Tool blocking — chờ kết quả rồi hiển thị stats.

### Bước 5a — Sample (nếu user chọn y)

```
run_sample_grouping(topic=..., sample_size=100)
```

Hiển thị nhóm mẫu. Hỏi user có hài lòng không.  
Nếu không: dừng lại, hỏi muốn điều chỉnh gì (topic, brand mode...).

### Bước 5b — Build vocabulary (Phase 5a)

```
expose_build_vocabulary(topic=..., sample_size=1000)
```

Hiển thị: "Đã xác định X loại hậu tố: [giá, lựa chọn, độ tuổi, ...]"

### Bước 5c — Grouping toàn bộ (Phase 5b, background)

```
submit_grouping_batches(topic=...)
```

Trả về ngay lập tức. Sau đó poll mỗi 60 giây:

```
poll_batch_status()
```

Hiển thị tiến độ dạng progress bar:
```
[████████░░] 8/10 batch hoàn thành (~4 phút còn lại)
```

Tiếp tục poll đến khi `status == "complete"` hoặc `status == "error"`.

Nếu `status == "error"`: thông báo lỗi, gợi ý gọi lại `submit_grouping_batches()` để resume.

### Bước 5c — Merge pass

```
run_merge_pass(topic=...)
```

Gộp tên nhóm trùng nghĩa. Hiển thị: bao nhiêu nhóm được gộp, keyword reassigned.

### Bước 6 — Review UI

```
get_group_summary()
```

Hiển thị bảng tóm tắt theo format SKILL.md. Chú ý warnings (nhóm quá lớn/nhỏ).

**Vòng lặp review** — xử lý lệnh của user:

| Lệnh user | Tool gọi |
|-----------|----------|
| `xem <số>` / `xem <tên nhóm>` | `get_group_detail(group_name=...)` |
| `merge <A> <B>` | `merge_groups_manual(group_a=..., group_b=...)` |
| `merge <A> <B> thành <tên>` | `merge_groups_manual(group_a=..., group_b=..., new_name=...)` |
| `rename <số> <tên mới>` | `rename_group(old_name=..., new_name=...)` |
| `reset` | `reset_groups()` |
| `export` | thoát vòng lặp review, đến bước export |

Sau mỗi thao tác merge/rename: gọi lại `get_group_summary()` và hiển thị lại bảng.

### Bước 7 — Export

```
export_results(output_path=..., topic=...)
```

Gợi ý đường dẫn mặc định:
```
~/Downloads/keywords_labeled_{YYYY-MM-DD}.xlsx
```

Hiển thị kết quả sau export theo format SKILL.md.

---

## Xử lý lỗi quan trọng

### File không tìm thấy
Tool trả về `{"ok": false, "error": "..."}` → hỏi lại đường dẫn.

### Brand mode 1/2/3 thiếu brands_csv
`preprocess_keywords` trả về lỗi "yêu cầu danh sách brand không rỗng" → hỏi user nhập tên thương hiệu (ngăn cách bằng dấu phẩy).

### Grouping timeout
`poll_grouping_status` trả về `status="error"` với "Timeout" → thông báo: "Batch chưa hoàn thành. Gọi start_full_grouping() để resume từ state đã lưu."  
Gọi lại `start_full_grouping(topic=...)` — BatchManager sẽ resume tự động từ state file trên disk.

### Grouping bị interrupt (server restart)
Gọi `submit_grouping_batches(topic=...)` lại với cùng topic — BatchManager phát hiện state file và resume.

### Tất cả keyword bị filter
`run_filter` trả về `kept_count=0` → cảnh báo user và hỏi có muốn điều chỉnh ngưỡng hoặc topic không.

---

## Format hiển thị bảng nhóm

```
=== KẾT QUẢ PHÂN NHÓM ===
Tổng: X keyword → Y nhóm (Z keyword bị lọc)

 #   Nhóm                              Keywords   Volume
 1   sữa bột - lựa chọn - 1 tuổi          234    45,000
 2   sữa bột - thương hiệu - Enfamil        89    12,000
 3   sữa bột - giá - bình dân               67     8,500
...
⚠  Nhóm #5 có 620 keyword — cân nhắc chia nhỏ
⚠  Nhóm #12 chỉ có 3 keyword — cân nhắc gộp vào nhóm khác

Lệnh có thể dùng:
  xem <số>          — xem chi tiết keyword của nhóm
  merge <số> <số>   — gộp 2 nhóm lại
  rename <số> <tên> — đổi tên nhóm
  export            — xuất file kết quả
  reset             — huỷ thay đổi, về kết quả gốc
```

Khi user dùng số thứ tự (ví dụ "xem 3", "merge 1 5"), coordinator cần map số sang tên nhóm từ danh sách `get_group_summary()` trả về.

---

## Lưu ý state management

- `load_keywords()` reset toàn bộ state của session (keywords, groups, snapshot).
- `run_merge_pass()` tạo snapshot để `reset_groups()` có thể hoạt động.
- Nếu user chạy nhiều lần trong cùng session (ví dụ load file mới): state cũ bị xoá sau `load_keywords()`.
- State BatchManager được lưu vào `~/.keyword_labeler/state/` — tồn tại qua restart.
