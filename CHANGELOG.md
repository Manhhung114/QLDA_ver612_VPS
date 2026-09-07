# CHANGELOG

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
