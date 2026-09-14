# CHANGELOG

## V6.24.4 — VO Background Worker
- Tải file VO gốc thẳng xuống SSD VPS, tính SHA theo chunk và mở đồng thời bản giá trị/công thức bằng `read_only=True`; không nạp toàn bộ workbook vào RAM Streamlit.
- Parser chi tiết VO chuyển sang quét tuần tự từng sheet nhưng giữ nguyên quy tắc nhận diện tăng/giảm, vật tư/nhân công, thành tiền, sheet nguồn và đối chiếu tổng hợp của V6.22.
- Mỗi VO tiếp tục là hồ sơ độc lập; cùng SHA không tạo revision thừa, file mới cùng mã VO tạo revision và giữ nguyên giá trị duyệt, nguồn vốn, trạng thái.
- Dòng chi tiết được ghi PostgreSQL theo batch, đọc lại bằng `vo_id` và chỉ trả `HOÀN TẤT` khi `expected/scanned/prepared/inserted/written` khớp tuyệt đối, `failed=0`.
- Sai lệch số dòng hoặc hủy giữa chừng rollback toàn transaction, giữ nguyên revision VO trước và không cập nhật dở dang sang bảng `cost_variations`.
- Giao diện VO có upload nền, tiến độ, hủy an toàn, VO/revision, giá trị đề xuất và bằng chứng số dòng PostgreSQL.
- CI kiểm tra file gốc không đổi SHA, tính độc lập/idempotency/revision, rollback, batch 2.003 dòng và PostgreSQL 16 thật.

## V6.24.3 — IPC Background Worker
- Tải file IPC gốc thẳng xuống SSD VPS và mở workbook từ đường dẫn bằng `read_only=True`; worker không nạp toàn bộ file thành `bytes` trong tiến trình Streamlit.
- Giữ nguyên parser nghiệp vụ IPC hiện có, gồm nhận diện form thích nghi, kỳ thanh toán, số Claim và ngữ nghĩa vật tư/nhân công.
- Mỗi Claim/IPC tiếp tục là một hồ sơ độc lập; cùng Claim với file khác tạo revision mới, cùng SHA là idempotent và không tạo revision thừa.
- Chi tiết GTHT được ghi PostgreSQL theo batch và đọc lại bằng `claim_id`; kết quả bắt buộc có `expected/scanned/prepared/inserted/written/failed`.
- Chỉ trả `HOÀN TẤT` khi toàn bộ bộ đếm khớp. Sai lệch hoặc hủy giữa chừng rollback toàn transaction và giữ nguyên revision trước.
- Giao diện IPC hiển thị hàng đợi, tiến độ, Claim/revision và bằng chứng số dòng PostgreSQL; luồng nhập nhỏ hiện hữu vẫn được giữ để tương thích.
- GitHub Actions kiểm tra parser đường dẫn, SHA file gốc, tính độc lập Claim, idempotency/revision, rollback SQLite và ghi thực trên PostgreSQL 16.

## V6.24.2 — Xác minh số dòng BOQ PostgreSQL
- Sau khi ghi BOQ theo batch, worker đọc lại đúng lô bằng `project_id + batch_id`; không dùng bộ đếm vòng lặp làm kết quả xác nhận.
- Kết quả job lưu và hiển thị đủ `expected_rows`, `scanned_rows`, `prepared_rows`, `written_rows`, `failed_rows` và trạng thái `HOÀN TẤT/CHƯA ĐỦ`.
- Nếu số dòng PostgreSQL không khớp, toàn bộ transaction bị rollback và job không được chuyển sang `DONE`.
- GitHub Actions chạy kiểm thử tích hợp trên PostgreSQL 16 trước khi cho phép chuyển sang V6.24.3 IPC Background.
- File Excel gốc vẫn được giữ nguyên trên SSD VPS và chỉ được mở `read_only=True`.

## V6.22 PostgreSQL Cloud — Streamlit Community Cloud
- Chạy trực tiếp bằng `streamlit_app.py` trên **Streamlit Community Cloud**.
- Source WebOpt được giải nén, finalize và compile trực tiếp trong bộ nhớ; không cần bước build container khi chạy Community Cloud.
- PostgreSQL được kích hoạt khi `DATABASE_URL` được cấu hình; lớp tương thích SQLite vẫn được giữ cho dữ liệu legacy.
- Giữ nguyên workflow phê duyệt, Google Drive Gateway, đăng nhập persistent qua F5/Refresh và AI streaming/typewriter.
- Dọn toàn bộ nhánh runtime và tài liệu của nền tảng triển khai cũ để tránh ảnh hưởng đến Community Cloud.
- `requirements.txt`, `packages.txt` và `.streamlit/config.toml` là bộ cấu hình deploy chính.

## V6.21 WebOpt
- Khôi phục **cách trả lời AI như ban đầu**: dùng lại toàn bộ snapshot dự án từ `ProjectContextBuilder.build()` và tối đa 8 message lịch sử gần nhất để câu trả lời đầy đủ, có đủ tiến độ, hồ sơ, bản vẽ, chi phí, vật tư và pháp lý theo logic gốc.
- Bỏ cơ chế `Fast Context`, bỏ rút gọn lịch sử 4 message và bỏ đường chọn model nhanh cưỡng bức. Gemini quay lại cơ chế model/fallback/retry ban đầu.
- Giữ **streaming**: OpenAI dùng Responses streaming delta, Gemini dùng `generate_content_stream`; Streamlit hiển thị dần bằng `st.write_stream()` thay vì chờ xong toàn bộ câu trả lời.
- Nếu SDK OpenAI cũ không có streaming API, giao diện vẫn phát dần theo từng cụm từ thay vì đổ cả khối một lần.
- Bỏ các chú thích/hướng dẫn tĩnh trong các sheet để giao diện gọn hơn; vẫn giữ các thông báo nghiệp vụ cần thiết.
- Giữ nguyên toàn bộ luồng phê duyệt online RFA/RFI/Shopdrawing/Hoàn công.
- Chỉ render module/sheet đang mở; `st.fragment` cho vùng file và phê duyệt online.
- Plotly và module Văn bản chỉ import khi thực sự dùng; Gantt tắt mặc định.
- Workflow được đọc theo batch để tránh N+1 query; giữ tương thích hồ sơ legacy qua V6.15 deterministic resubmit.
- SQLite: WAL, statement cache, memory temp/cache, mmap và index cho các truy vấn chính.
- Google Apps Script/Drive dùng HTTP connection pool; `/me` cache 15 giây; file list/file-count cache 5 giây; nút Làm mới file xóa cache ngay.
- Bảng lớn phân trang: hồ sơ/bản vẽ 50 dòng/trang; tiến độ 100 dòng/trang.
- Excel chỉ tạo khi người dùng bấm `Tạo Excel`, không chạy openpyxl ở mọi rerun.
- Cookie đăng nhập chỉ ghi khi token thay đổi, giảm component HTML thừa.

## V6.21 — Partial Rerun / Lazy Load
- Chỉ render module và sheet đang chọn.
- Dùng `st.fragment` cho vùng file và phê duyệt online.

## V6.20 — Runtime Fast Path
- HTTP pool/cache Drive và SQLite index ban đầu.

## V6.19 — Lean Runtime
- Dọn build context và tài liệu cũ.
