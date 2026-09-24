# Contractor Data Hub V1–V5

## Mục tiêu

Mỗi nhà thầu trong một dự án QLDA có một **Data Space logic riêng**. Nhà thầu vẫn sở hữu dữ liệu Google; họ chỉ chia sẻ quyền Viewer cho Gmail QLDA. QLDA đọc dữ liệu bằng Google OAuth read-only, chuẩn hóa và lưu snapshot trong PostgreSQL. AI chỉ truy xuất dữ liệu mà người dùng QLDA được phép xem.

## V1 — Contractor Data Space

Ranh giới dữ liệu bắt buộc:

- `master_project_id`
- `contractor_id`
- `workspace_project_id`
- `data_space_id = cds:<master_project_id>:<contractor_id>`

Các bảng chính:

- `contractor_data_spaces`
- `contractor_data_sources`
- `contractor_data_records`
- `contractor_data_snapshots`
- `contractor_data_sync_runs`

Dữ liệu các nhà thầu không được gộp thành một record set không có khóa phạm vi.

## V2 — Google Sheets / Drive Connector

OAuth scope:

- `https://www.googleapis.com/auth/spreadsheets.readonly`
- `https://www.googleapis.com/auth/drive.readonly`

Nguồn hỗ trợ:

- Google Sheet riêng tư đã chia sẻ cho Gmail QLDA
- Google Sheet công khai bằng link (tương thích cũ)
- Google Drive Folder — tự duyệt cây thư mục tối đa 6 cấp / 1.000 file mỗi lần sync
- Một file Google Drive

Nội dung được trích xuất:

- Google Sheets
- Google Docs
- Google Slides (export PDF)
- PDF có text
- XLSX/XLSM
- TXT/CSV/TSV/JSON/Markdown
- file khác: lưu metadata để AI biết file tồn tại

QLDA không xin quyền sửa/xóa Google Drive hoặc Google Sheets.

## V3 — Persistent Sync / History

Google refresh token được mã hóa bằng Fernet và lưu ngoài Git checkout tại:

`/opt/qlda/shared/config/google_connections.json`

Khóa mã hóa lấy từ `QLDA_SETTINGS_MASTER_KEY`, fallback sang `QLDA_LOCAL_UPLOAD_SECRET`.

Mỗi kho có chu kỳ sync riêng từ 15–1.440 phút. Worker kiểm tra mặc định mỗi 300 giây:

`qlda-contractor-data-worker.service`

Worker command:

```bash
/opt/qlda/venv/bin/python -m qlda.modules.contractor_data.worker --poll-seconds 300
```

Snapshot chỉ được tạo khi checksum nguồn thay đổi. Lịch sử sync giữ trạng thái, số file phát hiện, số record ghi và số điểm sản lượng.

## V4 — Contractor AI Retrieval

Khi tài khoản có classification `CONTRACTOR`, `authorized_contractor_rows()` chỉ trả đúng workspace nhà thầu đã được Admin gán. AI Data Hub luôn nhận `workspace_ids` từ phạm vi này, vì vậy nhà thầu A không thể hỏi AI để lấy record của nhà thầu B.

AI retrieval chạy trong QLDA trước, sau đó chỉ context nhỏ có liên quan mới được gửi tới OpenAI/Gemini. Context giữ tag bằng chứng dạng:

`[DATA:<contractor>/<source>/<worksheet>/<record-ref>]`

## V5 — Project-wide AI Retrieval

Ban điều hành / Admin có thể chọn:

- một nhà thầu;
- hoặc `Toàn dự án`.

AI nhận tổng quan từng kho, cảnh báo dữ liệu và các record liên quan. Prompt yêu cầu tách kết quả từng nhà thầu trước khi tổng hợp và không tự suy đoán khi thiếu dữ liệu.

## Giao diện

Trong QLDA:

`🏗️ Thi công → 📊 Sản lượng`

có các tab:

1. `📈 Tổng quan`
2. `🏢 Kho nhà thầu`
3. `🔗 Nguồn Google`
4. `🕘 Lịch sử`
5. `🤖 AI phân tích`

## Quy trình triển khai

1. Admin cấu hình OAuth app tại `Công cụ → Hệ thống → Google OAuth`.
2. Google Cloud phải bật Google Sheets API và Google Drive API.
3. Nhà thầu chia sẻ Sheet/Folder cho Gmail QLDA với quyền Viewer.
4. Trong `Nguồn Google`, Admin/BĐH bấm `Đăng nhập Google` và cấp quyền read-only.
5. Chọn đúng nhà thầu rồi thêm Sheet/Folder/File vào Data Space của nhà thầu đó.
6. Sync lần đầu chạy ngay; sau đó worker tự đồng bộ theo chu kỳ.
7. AI phân tích dữ liệu đã đồng bộ, không đọc chéo ngoài quyền QLDA.

> Khi nâng từ kết nối Sheets-only cũ lên Data Hub V1–V5, cần **Kết nối lại Google một lần** để Google cấp thêm `drive.readonly`.

## Giới hạn an toàn

- File tải để trích nội dung: tối đa 30 MB/file.
- Folder discovery: tối đa 1.000 file/lần sync.
- Record mỗi nguồn: tối đa 20.000 record/lần sync.
- XLS legacy `.xls`: hiện chỉ lưu metadata; nên chuyển sang `.xlsx` để AI đọc nội dung.
- PDF scan không có text: hệ thống vẫn theo dõi nguồn/file nhưng cần pipeline OCR/vision riêng nếu muốn đọc nội dung ảnh.
