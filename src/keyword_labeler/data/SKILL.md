---
name: keyword
description: >
  Use this skill when the user types /keyword or wants to group SEO keywords,
  "phân nhóm keyword", "gom nhóm keyword", "phân loại keyword SEO",
  "bắt đầu phân nhóm", "keyword labeling", "group keywords",
  "label keywords", "phân tích keyword", "xử lý file keyword".
  Triggers the full keyword clustering pipeline: input collection →
  filter → group → review → export Excel/Markdown.
metadata:
  version: "1.0.6"
---

# Keyword Labeler

Khi user gọi lệnh `/keyword`, thực hiện đúng theo workflow dưới đây. Không bỏ qua bước nào.

---

## Bước 0 — Kiểm tra API key

Gọi MCP tool `check_api_key` ngay khi user gọi `/keyword`.

**Nếu `is_valid: true`**: tiếp tục sang Bước 1.

**Nếu `is_valid: false` hoặc `has_key: false`**: hiển thị thông báo:

> ⚠️ **ANTHROPIC API KEY chưa được cấu hình hoặc không hợp lệ.**
>
> Keyword Labeler cần API key từ Anthropic để hoạt động.
> Lấy key miễn phí tại: https://console.anthropic.com/settings/keys
>
> Paste API key của bạn vào đây (bắt đầu bằng `sk-ant-...`):

Sau khi user cung cấp key, gọi `save_api_key` để xác minh và lưu lại.

- Nếu thành công (`saved: true`) → thông báo "✅ API key đã được lưu" rồi tiếp tục Bước 1.
- Nếu không hợp lệ → thông báo lỗi cụ thể và yêu cầu nhập lại.

---

## Bước 1 — Thu thập thông tin đầu vào

Hiển thị form sau và chờ user điền đầy đủ trước khi tiếp tục:

---
### 📋 KEYWORD LABELER — Thông tin đầu vào

**[1] Chủ đề SEO** *(bắt buộc)*
Mô tả ngắn gọn lĩnh vực SEO bạn đang triển khai.
*Ví dụ: "sữa bột cho trẻ em", "laptop gaming", "dịch vụ kế toán"*
→ ___

**[2] File keyword** *(bắt buộc)*
Đường dẫn tới file Excel (.xlsx) hoặc CSV. File cần có cột `keyword` và `volume`.
→ ___

**[3] File SpySERP** *(tuỳ chọn)*
Đường dẫn tới file export SpySERP (cột keyword + url_1 đến url_10).
Nhập `bỏ qua` nếu không có.
→ ___

**[4] Xử lý branded keyword**

| Lựa chọn | Mô tả |
|---|---|
| **[1]** | Nhóm riêng theo từng thương hiệu *(vd: sữa bột - Enfamil)* |
| **[2]** | Gộp tất cả vào nhóm "thương hiệu" chung |
| **[3]** | Loại bỏ hoàn toàn branded keyword |
| **[4]** | Giữ nguyên, không xử lý đặc biệt |

→ ___

**[5] Ngưỡng volume keyword không dấu** *(mặc định: 100)*
Keyword không dấu (vd: `sua bot`) có volume ≥ ngưỡng sẽ được giữ lại. Nhấn Enter để dùng mặc định.
→ ___

**[6] Output format**

| Lựa chọn | Mô tả |
|---|---|
| **[1]** | Excel (.xlsx) — 3 sheet: Labeled / Removed / Summary |
| **[2]** | Markdown (.md) |

→ ___

**[7] Chạy sample trước?** *(mặc định: y)*
Xử lý thử 100 keyword để kiểm tra chất lượng trước khi chạy toàn bộ.
→ ___

**[8] Thông tin lưu ý** *(tuỳ chọn)*
Yêu cầu bổ sung, ưu tiên đặc biệt cho lần chạy này.
*Ví dụ: "ưu tiên intent thương mại", "tên nhóm tối đa 5 từ", "không tạo nhóm < 10 keyword"*
Nhập `bỏ qua` nếu không có.
→ ___

---

Sau khi user nhập xong, phân tích mục [8] và xác định các điều chỉnh cần áp dụng:
- Nếu user không có lưu ý → tiếp tục bình thường.
- Nếu có lưu ý → tóm tắt ngắn cách sẽ điều chỉnh, ví dụ: *"Ghi nhận: sẽ bỏ qua nhóm < 10 keyword và ưu tiên intent thương mại."* Áp dụng xuyên suốt toàn bộ session.

Sau đó xác nhận lại toàn bộ thông tin một lần trước khi xử lý.

---

## Bước 2 — Load và validate dữ liệu

Gọi MCP tool `load_keyword_file` với đường dẫn file keyword.

Hiển thị kết quả dạng bảng:

| Thống kê | Giá trị |
|---|---|
| Tổng dòng trong file | X |
| Trùng lặp đã xoá | Y |
| Keyword hợp lệ | **Z** |
| Keyword volume = 0 | W |

Nếu có file SpySERP, gọi thêm `load_spyserp_file` và hiển thị: *"SpySERP: matched W keyword."*

Nếu có lỗi (file không tồn tại, sai định dạng), báo lỗi rõ ràng và hỏi lại đường dẫn — không dừng toàn bộ workflow.

---

## Bước 3 — Ước tính chi phí và xác nhận

Gọi MCP tool `estimate_cost`. Hiển thị kết quả:

### 💰 Ước tính chi phí

| Hạng mục | Chi tiết |
|---|---|
| Keyword đầu vào | X |
| Keyword qua filter | ~Y |
| Keyword qua grouping | ~Z |
| Chi phí filter | ~$A (Haiku Batch API) |
| Chi phí grouping | ~$B (Sonnet Batch API) |
| **Tổng chi phí** | **~$C** |
| Thời gian ước tính | ~N phút |

> ℹ️ Giá Batch API giảm 50% so với standard. Ước tính có thể lệch ±30%.

**Tiếp tục xử lý? (y/n)**

Nếu user chọn "n", dừng lại và hỏi xem có muốn điều chỉnh gì không.

---

## Bước 4 — Xử lý (chạy theo thứ tự)

### 4a. Pre-processing

Gọi `preprocess_keywords`. Hiển thị kết quả ngắn:

> ✅ Pre-processing xong: **X** branded keyword *(mode: Y)*, **Z** keyword SpySERP tagged.

### 4b. Filter keyword xấu

Gọi `filter_keywords` để lọc sai chính tả, off-topic, không dấu volume thấp.

Sau khi xong, hiển thị bảng tóm tắt:

| Kết quả filter | Số lượng |
|---|---|
| ✅ Giữ lại | X |
| ❌ Sai chính tả / off-topic | Y |
| ❌ Không dấu, volume thấp | Z |
| **Tổng bị lọc** | **W** |

**Checkpoint:** Viết đúng 1 dòng rồi tiếp tục ngay:
> ✅ Filter xong: **X** giữ lại, **Y** bị lọc. Tiếp tục bước sample/grouping.

### 4c. Nếu chạy sample trước

Nếu user chọn sample (y), gọi `run_sample_grouping` với 100 keyword đại diện.

Hiển thị kết quả dạng bảng:

### 🔍 Kết quả Sample (100 keyword)

| # | Nhóm | Keywords |
|---|---|---|
| 1 | sữa bột - lựa chọn - 1 tuổi | 12 |
| 2 | sữa bột - thương hiệu - Enfamil | 8 |
| 3 | sữa bột - giá - bình dân | 7 |
| ... | ... | ... |

**Vocabulary phát hiện:** `giá`, `lựa chọn`, `độ tuổi`, `thương hiệu`, `review`, `mua ở đâu`

> **Chất lượng nhóm có đạt yêu cầu không?**
> - **[y]** Tiếp tục xử lý toàn bộ
> - **[n]** Dừng lại, tôi muốn điều chỉnh

Nếu user không hài lòng, dừng lại và hỏi muốn thay đổi gì.

### 4d. Build vocabulary

Gọi `expose_build_vocabulary` — phân tích 1.000 keyword đại diện để tạo danh sách hậu tố cố định.

Hiển thị: *"Đã xác định **X** hậu tố: `giá`, `lựa chọn`, `độ tuổi`, `thương hiệu`, ..."*

### 4e. Phân nhóm toàn bộ (Batch API)

Gọi `submit_grouping_batches` — chia thành các batch **200 keyword** và submit lên Batch API.

Hiển thị:
> 🚀 Đã submit **N** request trong 1 batch job (tổng **X** keyword). Batch API đang xử lý...

**Ngay sau khi submit**, hỏi user chọn chế độ poll:

---
### ⏱ Chọn cách theo dõi kết quả

Batch API thường mất **30–60 phút**. Mỗi lần poll = 1 lượt hội thoại → poll quá dày tốn nhiều token.

| Chế độ | Mô tả |
|---|---|
| **[1] Tự động** | Claude kiểm tra định kỳ, bạn không cần làm gì |
| **[2] Thủ công** | Bạn tự kiểm tra trong Anthropic Console, báo Claude khi xong |

**Nếu chọn [1]** — chọn khoảng cách giữa các lần kiểm tra:
`[5]` `[10]` `[15]` `[20]` `[25]` `[30]` phút

---

**Nếu chọn [1] Tự động (interval = N phút):**
- Dùng Bash tool để chờ: chia thành các lệnh `sleep` tuần tự, mỗi lệnh ≤ 540 giây, cho đến đủ N phút tổng cộng. Ví dụ: 15 phút = `sleep 540` rồi `sleep 360`.
- Khi đủ thời gian, gọi `poll_batch_status`
- **Chỉ hiển thị 1 dòng ngắn** sau mỗi lần poll: `⏳ Đang xử lý — chờ tiếp N phút...` hoặc `✅ Batch hoàn thành!`
- Nếu `status == "complete"`: tiếp tục bước 4f
- Nếu `status == "running"`: sleep tiếp và poll lại
- Nếu `status == "error"` kéo dài 2 lần liên tiếp: thông báo lỗi chi tiết cho user

**Nếu chọn [2] Thủ công:**

> 📋 **Hướng dẫn kiểm tra batch thủ công:**
>
> 1. Mở trình duyệt → vào: https://console.anthropic.com/settings/workspaces/default/batches
> 2. Tìm batch có nhiều request nhất (batch mới nhất)
> 3. Chờ cột **Status** chuyển thành `ended` (thường 30–60 phút)
> 4. Khi thấy `ended` → quay lại Claude và nhắn: **"batch xong"** hoặc **"check kết quả"**

- Khi user báo hiệu, gọi `poll_batch_status` 1 lần duy nhất
- Nếu `status != "complete"`: hỏi user có muốn chờ thêm không

### 4f. Merge pass

Sau khi tất cả batch xong, gọi `run_merge_pass` để gộp tên nhóm trùng nghĩa.

**Checkpoint:** Viết đúng 1 dòng rồi chuyển sang Bước 5:
> ✅ Merge pass xong: **X** nhóm → **Y** nhóm. Chuyển sang review.

---

**Lưu ý về token khi review (Bước 5):**
- Khi user gõ "xem `<số>`": gọi `get_group_detail`, hiển thị tối đa 20 keyword đầu nếu nhóm lớn
- Khi user gõ "tóm tắt": gọi `get_group_summary` — chỉ gọi 1 lần, không gọi lại nếu không cần thiết
- Không tự động gọi thêm tool nào giữa các lệnh của user

---

## Bước 5 — Review kết quả

Gọi `get_group_summary` và hiển thị:

### 📊 Kết quả phân nhóm

| Thống kê | Giá trị |
|---|---|
| Tổng keyword đầu vào | X |
| Đã phân nhóm | Y |
| Chưa phân nhóm | Z |
| Bị lọc | W |
| Số nhóm | **N** |

| # | Nhóm | Keywords | Volume |
|---|---|---|---|
| 1 | sữa bột - lựa chọn - 1 tuổi | 234 | 45,000 |
| 2 | sữa bột - thương hiệu - Enfamil | 89 | 12,000 |
| 3 | sữa bột - giá - bình dân | 67 | 8,500 |
| ... | ... | ... | ... |

> ⚠️ Nhóm #5 có 620 keyword — cân nhắc chia nhỏ
> ⚠️ Nhóm #12 chỉ có 3 keyword — cân nhắc gộp vào nhóm khác

---
**Lệnh có thể dùng:**

| Lệnh | Tác dụng |
|---|---|
| `xem <số>` | Xem chi tiết keyword của nhóm |
| `merge <số> <số>` | Gộp 2 nhóm lại |
| `rename <số> <tên>` | Đổi tên nhóm |
| `export` | Xuất file kết quả |
| `reset` | Huỷ thay đổi, về kết quả gốc |

Lặp lại vòng review cho đến khi user gõ "export".

---

## Bước 6 — Export

Gọi MCP tool `export_results` với format đã chọn. Hiển thị kết quả:

### ✅ Xuất file thành công

| Thông tin | Chi tiết |
|---|---|
| 📁 File | `/Users/.../keywords_labeled_2024-01-15.xlsx` |
| 📋 Sheet Labeled | X keyword đã phân nhóm |
| 🗑️ Sheet Removed | Y keyword bị lọc |
| 📊 Sheet Summary | Z nhóm, tổng volume W |

---

## Lưu ý xử lý lỗi

| Lỗi | Xử lý |
|---|---|
| File không tìm thấy | Hỏi lại đường dẫn, gợi ý kiểm tra khoảng trắng trong tên file |
| Batch API lỗi | Thông báo rõ batch nào lỗi, tự retry; nếu vẫn lỗi hỏi user có muốn tiếp tục partial không |
| Thiếu ANTHROPIC_API_KEY | Hướng dẫn set env var hoặc tạo file `~/.anthropic_key` |
| File quá lớn (>10.000 dòng) | Cảnh báo và hỏi user có muốn giới hạn số dòng không |
