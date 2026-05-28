# Keyword Labeler

Khi user gọi lệnh `/keyword`, thực hiện đúng theo workflow dưới đây. Không bỏ qua bước nào.

---

## Bước 0 — Kiểm tra API key

Gọi MCP tool `check_api_key` ngay khi user gọi `/keyword`.

**Nếu `is_valid: true`**: tiếp tục sang Bước 1.

**Nếu `is_valid: false` hoặc `has_key: false`**: hiển thị thông báo:

```
⚠️  ANTHROPIC API KEY chưa được cấu hình hoặc không hợp lệ.

Keyword Labeler cần API key từ Anthropic để hoạt động.
Lấy key miễn phí tại: https://console.anthropic.com/settings/keys

Paste API key của bạn vào đây (bắt đầu bằng sk-ant-...):
```

Sau khi user cung cấp key, gọi `save_api_key` để xác minh và lưu lại.

- Nếu thành công (`saved: true`) → thông báo "✅ API key đã được lưu" rồi tiếp tục Bước 1.
- Nếu không hợp lệ → thông báo lỗi cụ thể và yêu cầu nhập lại.

---

## Bước 1 — Thu thập thông tin đầu vào

Hiển thị form sau và chờ user điền đầy đủ trước khi tiếp tục:

```
=== KEYWORD LABELER ===

Vui lòng cung cấp thông tin sau:

[1] Chủ đề SEO (bắt buộc)
    Mô tả ngắn gọn lĩnh vực SEO bạn đang triển khai.
    Ví dụ: "sữa bột cho trẻ em", "laptop gaming", "dịch vụ kế toán"
    → 

[2] File keyword (bắt buộc)
    Đường dẫn tới file Excel (.xlsx) hoặc CSV.
    File cần có cột "keyword" và "volume" (hoặc tương đương).
    → 

[3] File SpySERP (tuỳ chọn)
    Đường dẫn tới file export CSV/Excel từ SpySERP.
    File cần có cột keyword và các cột URL ranking (url_1 đến url_10).
    Nhập "bỏ qua" nếu không có.
    → 

[4] Xử lý branded keyword
    [1] Nhóm riêng theo từng thương hiệu  (vd: sữa bột - Enfamil, sữa bột - Nan)
    [2] Gộp vào nhóm "thương hiệu" chung  (vd: sữa bột - thương hiệu)
    [3] Loại bỏ toàn bộ branded keyword
    [4] Giữ nguyên, không xử lý đặc biệt
    → 

[5] Ngưỡng volume keyword không dấu (mặc định: 100)
    Keyword không dấu (vd: "sua bot") có volume ≥ ngưỡng sẽ được giữ lại.
    Nhập số hoặc nhấn Enter để dùng mặc định (100).
    → 

[6] Output format
    [1] Excel (.xlsx) — gồm 3 sheet: Labeled / Removed / Summary
    [2] Markdown (.md)
    → 

[7] Chạy sample trước? (y/n, mặc định: y)
    Xử lý thử 100 keyword đầu tiên để kiểm tra chất lượng
    trước khi chạy toàn bộ danh sách.
    → 
```

Sau khi user nhập xong, xác nhận lại toàn bộ thông tin một lần trước khi xử lý.

---

## Bước 2 — Load và validate dữ liệu

Gọi MCP tool `load_keyword_file` với đường dẫn file keyword.

Kết quả trả về:
- Tổng số dòng
- Tên các cột tìm thấy
- Cảnh báo nếu thiếu cột keyword hoặc volume

Nếu có file SpySERP, gọi thêm `load_spyserp_file`.

Nếu có lỗi (file không tồn tại, sai định dạng), báo lỗi rõ ràng và hỏi lại đường dẫn — không dừng toàn bộ workflow.

---

## Bước 3 — Ước tính chi phí và xác nhận

Gọi MCP tool `estimate_cost` để tính:
- Số keyword cần xử lý
- Token ước tính
- Chi phí Batch API ước tính (USD)

Hiển thị cho user:
```
=== ƯỚC TÍNH ===
Số keyword: X
Token ước tính: ~Yk token
Chi phí API ước tính: ~$Z.ZZ (Batch API, giảm 50%)
Thời gian xử lý: ~N phút

Tiếp tục? (y/n):
```

Nếu user chọn "n", dừng lại và hỏi xem có muốn điều chỉnh gì không.

---

## Bước 4 — Xử lý (chạy theo thứ tự)

Thông báo tiến độ sau mỗi bước hoàn thành.

### 4a. Pre-processing
Gọi `preprocess_keywords` với:
- Cấu hình brand handling (lựa chọn [4])
- Ngưỡng volume không dấu (lựa chọn [5])
- Dữ liệu SpySERP (nếu có)

### 4b. Filter keyword xấu
Gọi `filter_keywords`:
- Lọc sai chính tả, off-topic
- Phân loại keyword không dấu theo ngưỡng volume

Hiển thị tóm tắt: "Đã lọc X keyword (Y sai chính tả, Z off-topic, W không dấu volume thấp)"

### 4c. Nếu chạy sample trước
Nếu user chọn sample (y), gọi `run_sample_grouping` với 100 keyword đại diện.

Hiển thị kết quả sample:
```
=== KẾT QUẢ SAMPLE (100 keyword) ===
Nhóm tạo ra: N nhóm
Ví dụ:
  - sữa bột - lựa chọn - 1 tuổi (X kw)
  - sữa bột - thương hiệu - Enfamil (Y kw)
  ...

Chất lượng nhóm có đạt yêu cầu không?
[y] Tiếp tục xử lý toàn bộ
[n] Dừng lại, tôi muốn điều chỉnh prompt/cấu hình
```

Nếu user không hài lòng, dừng lại và hỏi muốn thay đổi gì (chủ đề, cách xử lý brand...).

### 4d. Build vocabulary
Gọi `build_vocabulary` — phân tích 1.000 keyword đại diện để tạo danh sách hậu tố cố định.

Hiển thị: "Đã xác định X loại hậu tố: [giá, lựa chọn, độ tuổi, thương hiệu, ...]"

### 4e. Phân nhóm toàn bộ (Batch API)
Gọi `submit_grouping_batches` — chia thành các batch 500 keyword và submit lên Batch API.

Hiển thị: "Đã submit N batch (tổng X keyword). Đang chờ kết quả..."

Gọi `poll_batch_status` định kỳ — hiển thị tiến độ:
```
[████████░░] 8/10 batch hoàn thành (~4 phút còn lại)
```

### 4f. Merge pass
Sau khi tất cả batch xong, gọi `run_merge_pass` để gộp tên nhóm trùng nghĩa.

---

## Bước 5 — Review kết quả

Hiển thị summary và cho phép user chỉnh sửa trước khi export:

```
=== KẾT QUẢ PHÂN NHÓM ===
Tổng: X keyword → Y nhóm (Z keyword bị lọc)

 #   Nhóm                              Keywords   Volume
 1   sữa bột - lựa chọn - 1 tuổi          234    45,000
 2   sữa bột - thương hiệu - Enfamil        89    12,000
 3   sữa bột - giá - bình dân               67     8,500
...
⚠️  Nhóm #5 có 620 keyword — cân nhắc chia nhỏ
⚠️  Nhóm #12 chỉ có 3 keyword — cân nhắc gộp vào nhóm khác

Lệnh có thể dùng:
  xem <số>          — xem chi tiết keyword của nhóm
  merge <số> <số>   — gộp 2 nhóm lại
  rename <số> <tên> — đổi tên nhóm
  export            — xuất file kết quả
  reset             — huỷ thay đổi, về kết quả gốc
```

Lặp lại vòng review cho đến khi user gõ "export".

---

## Bước 6 — Export

Gọi MCP tool `export_results` với format đã chọn.

Thông báo đường dẫn file output:
```
✓ Đã xuất: /Users/.../keywords_labeled_2024-01-15.xlsx
  - Sheet "Labeled":  8,234 keyword đã phân nhóm
  - Sheet "Removed":  1,766 keyword bị lọc
  - Sheet "Summary":  47 nhóm, tổng volume 2,450,000
```

---

## Lưu ý xử lý lỗi

- **File không tìm thấy**: hỏi lại đường dẫn, gợi ý kiểm tra khoảng trắng trong tên file
- **Batch API lỗi**: thông báo rõ batch nào lỗi, tự retry, nếu vẫn lỗi hỏi user có muốn tiếp tục với kết quả partial không
- **Thiếu ANTHROPIC_API_KEY**: hướng dẫn set env var hoặc tạo file `~/.anthropic_key`
- **File quá lớn (>10.000 dòng)**: cảnh báo và hỏi user có muốn giới hạn số dòng không
